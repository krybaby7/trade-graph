"""Owner check-ins stay owner-only, append-only, idempotent and free of side effects (spec §B.10)."""

import inspect
import json
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from trade_graph.adapters.persistence import migrate
from trade_graph.adapters.persistence.db import Database
from trade_graph.adapters.persistence.owner_checkin_schema import OWNER_CHECKIN_STATEMENTS, migration
from trade_graph.api import owner_checkins
from trade_graph.api.auth import csrf_for_token, issue_session, role_for_token
from trade_graph.api.hq_contract import Checkin, CheckinRecordResponse, CheckinsResponse, OwnerCheckins
from trade_graph.domain.clock import FrozenClock

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
PATH = "/api/v1/owner/checkins"
INVALID = {"detail": "invalid check-in request"}


def _ensure_checkin_table(database):
    """Isolated databases get the 0025 tables directly until migrate.py registers them."""
    with database.immediate() as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='owner_checkins'"
                                  ).fetchone():
            for statement in OWNER_CHECKIN_STATEMENTS:
                connection.execute(statement)


@pytest.fixture
def runtime(tmp_path):
    database = Database(tmp_path / "owner-checkins.sqlite")
    _ensure_checkin_table(database)
    yield SimpleNamespace(database=database, clock=FrozenClock(NOW), portfolio_id="portfolio",
                          deployment_id="deployment")
    database.close()


def _app(runtime):
    """The same identity and owner_write rules as ``api/app.py``."""
    app = FastAPI()

    def identity(request: Request) -> str:
        parts = request.headers.get("authorization", "").split()
        bearer = len(parts) == 2 and parts[0].lower() == "bearer"
        token = parts[1] if bearer else request.cookies.get("tg_session")
        role = role_for_token(runtime.database, token)
        if role is None:
            raise HTTPException(401, "authentication required")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not bearer:
            expected = csrf_for_token(runtime.database, token)
            if not expected or not secrets.compare_digest(request.headers.get("x-csrf-token", ""), expected):
                raise HTTPException(403, "csrf")
        return role

    def owner_write(request: Request) -> str:
        if identity(request) != "owner":
            raise HTTPException(403, "owner authority required")
        return "owner"

    owner_checkins.register(app, runtime, identity, owner_write)
    return app


def _owner(runtime):
    token, csrf = issue_session(runtime.database, runtime.clock, "owner")
    return TestClient(_app(runtime)), {"Authorization": f"Bearer {token}"}, token, csrf


def _plan(**changes):
    return {"request_id": "plan-1", "checkin_id": "c-1", "action": "plan", "planned_at": "2026-10-09T14:00:00Z",
            "prompt_kind": "interval", "title": "Evening review", **changes}


def _rows(runtime, where="1", params=()):
    return [dict(row) for row in runtime.database.execute(
        f"SELECT * FROM owner_checkins WHERE {where} ORDER BY sequence", params)]


def _post(client, headers, body):
    response = client.post(PATH, headers=headers, json=body)
    if response.status_code == 200:
        CheckinRecordResponse.model_validate(response.json())
    return response


def test_owner_only(runtime):
    client, owner, token, csrf = _owner(runtime)
    assert client.get(PATH).status_code == 401
    assert client.post(PATH, json=_plan()).status_code == 401
    for role in ("reader", "leader"):
        other, other_csrf = issue_session(runtime.database, runtime.clock, role)
        bearer = {"Authorization": f"Bearer {other}"}
        assert client.get(PATH, headers=bearer).status_code == 403
        assert client.post(PATH, headers=bearer, json=_plan()).status_code == 403
        cookie_client = TestClient(_app(runtime))
        cookie_client.cookies.set("tg_session", other)
        assert cookie_client.post(PATH, headers={"X-CSRF-Token": other_csrf}, json=_plan()).status_code == 403
    assert _rows(runtime) == []

    cookie = TestClient(_app(runtime))
    cookie.cookies.set("tg_session", token)
    refused = cookie.post(PATH, json=_plan())
    assert refused.status_code == 403
    assert cookie.post(PATH, headers={"X-CSRF-Token": "wrong"}, json=_plan()).status_code == 403
    assert _rows(runtime) == []
    assert cookie.get(PATH).status_code == 200  # reads need no CSRF token

    response = client.get(PATH, headers=owner)
    assert response.status_code == 200
    assert response.json() == {"as_of": "2026-10-09T12:00:00.000Z", "checkins": []}
    assert _post(client, owner, _plan()).status_code == 200
    assert _post(cookie, {"X-CSRF-Token": csrf}, _plan(request_id="plan-2", checkin_id="c-2")).status_code == 200
    assert [row["checkin_id"] for row in _rows(runtime)] == ["c-1", "c-2"]


def test_plan_reschedule_done_states(runtime):
    client, owner, *_ = _owner(runtime)
    planned = _post(client, owner, _plan()).json()
    assert planned["replayed"] is False
    assert planned["checkin"] == {
        "checkin_id": "c-1", "title": "Evening review", "prompt_kind": "interval",
        "planned_at": "2026-10-09T14:00:00.000Z", "state": "planned", "done_at": None, "note": None,
        "history": [{"action": "plan", "at": "2026-10-09T12:00:00.000Z", "planned_at": "2026-10-09T14:00:00.000Z"}],
    }

    def at(when):
        runtime.clock = FrozenClock(when)

    def state(when):
        at(when)
        item = owner_checkins.window(runtime, "2026-10-09T00:00:00Z", "2026-10-10T00:00:00Z")[0]
        Checkin.model_validate(item)
        return item["state"]

    due_from = datetime(2026, 10, 9, 13, 45, tzinfo=UTC)
    assert state(due_from - timedelta(microseconds=1)) == "planned"
    assert state(due_from) == "due"
    at(datetime(2026, 10, 9, 13, 50, tzinfo=UTC))
    moved = _post(client, owner, {"request_id": "move-1", "checkin_id": "c-1", "action": "reschedule",
                                  "planned_at": "2026-10-09T17:00:00+02:00"}).json()["checkin"]
    assert moved["planned_at"] == "2026-10-09T15:00:00.000Z"
    assert moved["state"] == "planned"
    assert [entry["action"] for entry in moved["history"]] == ["plan", "reschedule"]
    assert moved["history"][1] == {"action": "reschedule", "at": "2026-10-09T13:50:00.000Z",
                                   "planned_at": "2026-10-09T15:00:00.000Z"}
    assert moved["title"] == "Evening review" and moved["prompt_kind"] == "interval"

    planned_at = datetime(2026, 10, 9, 15, tzinfo=UTC)
    assert state(planned_at - timedelta(minutes=15)) == "due"
    assert state(planned_at + timedelta(minutes=60) - timedelta(microseconds=1)) == "due"
    assert state(planned_at + timedelta(minutes=60)) == "overdue"
    assert state(planned_at + timedelta(hours=6) - timedelta(microseconds=1)) == "overdue"
    assert state(planned_at + timedelta(hours=6)) == "missed"
    assert state(planned_at + timedelta(days=3)) == "missed"

    at(planned_at + timedelta(hours=7, milliseconds=250, microseconds=999))
    done = _post(client, owner, {"request_id": "done-1", "checkin_id": "c-1", "action": "done",
                                 "note": "Leader still failing; asked chat"}).json()["checkin"]
    assert done["state"] == "done"
    assert done["done_at"] == "2026-10-09T22:00:00.250Z"
    assert done["note"] == "Leader still failing; asked chat"
    assert done["history"][-1] == {"action": "done", "at": "2026-10-09T22:00:00.250Z", "planned_at": None}
    runtime.clock.advance(30 * 86400)
    assert state(runtime.clock.now()) == "done"

    at(NOW)
    _post(client, owner, _plan(request_id="plan-skip", checkin_id="c-skip", prompt_kind="bedtime", title="Bedtime"))
    skipped = _post(client, owner, {"request_id": "skip-1", "checkin_id": "c-skip", "action": "skip",
                                    "note": "Busy"}).json()["checkin"]
    assert (skipped["state"], skipped["note"], skipped["done_at"]) == ("skipped", "Busy", None)
    _post(client, owner, _plan(request_id="plan-cancel", checkin_id="c-cancel", prompt_kind="custom", title="Extra"))
    cancelled = _post(client, owner, {"request_id": "cancel-1", "checkin_id": "c-cancel",
                                      "action": "cancel"}).json()["checkin"]
    assert (cancelled["state"], cancelled["note"], cancelled["done_at"]) == ("cancelled", None, None)
    blank = _post(client, owner, _plan(request_id="plan-blank", checkin_id="c-blank", prompt_kind="next_day"))
    assert blank.status_code == 200
    blank_done = _post(client, owner, {"request_id": "done-blank", "checkin_id": "c-blank", "action": "done",
                                       "note": ""}).json()["checkin"]
    assert blank_done["note"] is None  # an empty note is no note


def test_exact_replay_and_conflict(runtime):
    client, owner, *_ = _owner(runtime)
    first = _post(client, owner, _plan()).json()
    replay = _post(client, owner, _plan()).json()
    assert replay == {**first, "replayed": True}
    # The canonical document compares instants, not their spelling.
    same_instant = _post(client, owner, _plan(planned_at="2026-10-09T16:00:00.000000+02:00"))
    assert same_instant.json()["replayed"] is True
    assert len(_rows(runtime)) == 1

    for changes in ({"title": "Other review"}, {"planned_at": "2026-10-09T15:00:00Z"}, {"checkin_id": "c-2"},
                    {"prompt_kind": "bedtime"}):
        response = client.post(PATH, headers=owner, json=_plan(**changes))
        assert response.status_code == 409
        assert response.json() == {"detail": "request_id already used for a different check-in change"}
    assert client.post(PATH, headers=owner, json={"request_id": "plan-1", "checkin_id": "c-1",
                                                  "action": "cancel"}).status_code == 409
    assert len(_rows(runtime)) == 1

    done = {"request_id": "done-1", "checkin_id": "c-1", "action": "done", "note": "Fine"}
    assert _post(client, owner, done).json()["replayed"] is False
    runtime.clock.advance(3 * 86400)  # the plan's time has left the planning window
    late = _post(client, owner, _plan()).json()
    assert late["replayed"] is True
    assert late["checkin"]["state"] == "done"  # a replay answers with the check-in as it is now
    assert _post(client, owner, done).json()["replayed"] is True
    assert [row["action"] for row in _rows(runtime)] == ["plan", "done"]
    assert owner_checkins.record(runtime, done)["replayed"] is True
    with pytest.raises(owner_checkins.CheckinConflict):
        owner_checkins.record(runtime, {**done, "note": "Changed"})


def test_triggers(runtime):
    client, owner, *_ = _owner(runtime)

    def conflict(body):
        response = client.post(PATH, headers=owner, json=body)
        assert response.status_code == 409
        return response.json()["detail"]

    assert conflict({"request_id": "early", "checkin_id": "nobody", "action": "done"}) == "unknown check-in"
    assert conflict({"request_id": "early-move", "checkin_id": "nobody", "action": "reschedule",
                     "planned_at": "2026-10-09T15:00:00Z"}) == "unknown check-in"
    _post(client, owner, _plan())
    assert conflict(_plan(request_id="plan-again")) == "check-in already planned"
    _post(client, owner, {"request_id": "done-1", "checkin_id": "c-1", "action": "done"})
    for action, extra in (("done", {}), ("skip", {}), ("cancel", {}),
                          ("reschedule", {"planned_at": "2026-10-09T18:00:00Z"})):
        assert conflict({"request_id": f"after-{action}", "checkin_id": "c-1", "action": action,
                         **extra}) == "check-in already closed"
    assert conflict(_plan(request_id="replan")) == "check-in already planned"
    assert len(_rows(runtime)) == 2

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        runtime.database.execute("UPDATE owner_checkins SET note='rewritten'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        runtime.database.execute("DELETE FROM owner_checkins")
    insert = """INSERT INTO owner_checkins (record_id,deployment_id,request_id,checkin_id,action,planned_at,
        prompt_kind,title,note,document_json,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,'{}','2026-10-09T12:00:00.000000Z')"""
    with pytest.raises(sqlite3.IntegrityError, match="unknown check-in"):
        runtime.database.execute(insert, ("r1", "deployment", "direct-1", "ghost", "skip", None, None, None, None))
    with pytest.raises(sqlite3.IntegrityError, match="already closed"):
        runtime.database.execute(insert, ("r2", "deployment", "direct-2", "c-1", "cancel", None, None, None, None))
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        runtime.database.execute(insert, ("r3", "deployment", "direct-3", "c-3", "plan", "2026-10-09T15:00:00Z",
                                          "interval", None, None))
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        runtime.database.execute(insert, ("r4", "deployment", "plan-1", "c-4", "plan", "2026-10-09T15:00:00Z",
                                          "interval", "Direct", None))
    # Another deployment's history is separate.
    runtime.database.execute(insert, ("r5", "other", "plan-1", "c-1", "plan", "2026-10-09T15:00:00Z", "interval",
                                      "Elsewhere", None))
    assert len(_rows(runtime, "deployment_id='deployment'")) == 2


@pytest.mark.parametrize("changes", [
    {"title": "/home/x"}, {"title": "C:\\x"}, {"title": "password: x"}, {"title": "key sk-live-secret"},
    {"title": "Bell\x07"}, {"title": " Evening"}, {"title": "Evening "}, {"title": ""}, {"title": "x" * 81},
    {"title": 5}, {"title": None}, {"prompt_kind": None}, {"prompt_kind": "weekly"}, {"action": "delete"},
    {"planned_at": "2026-10-09T17:30:00"}, {"planned_at": "2026-10-09"}, {"planned_at": "tomorrow"},
    {"planned_at": "2026-10-08T11:59:59Z"}, {"planned_at": "2026-12-08T12:00:00.000001Z"}, {"planned_at": None},
    {"note": "Fine"}, {"unexpected": "field"}, {"request_id": "sk-live-secret"}, {"request_id": "../x"},
    {"checkin_id": ""}, {"checkin_id": "c" * 129}, {"request_id": 7},
])
def test_rejects_unsafe_text(runtime, changes):
    client, owner, *_ = _owner(runtime)
    response = client.post(PATH, headers=owner, json=_plan(**changes))
    assert response.status_code == 422
    assert response.json() == INVALID
    for fragment in ("home", "password", "sk-live", "Bell", "weekly", "delete", "unexpected", "tomorrow"):
        assert fragment not in response.text
    assert _rows(runtime) == []


@pytest.mark.parametrize("body", [
    {"request_id": "d", "checkin_id": "c-1", "action": "done", "note": "line\nbreak"},
    {"request_id": "d", "checkin_id": "c-1", "action": "done", "note": "see /home/owner/notes.txt"},
    {"request_id": "d", "checkin_id": "c-1", "action": "skip", "note": "x" * 501},
    {"request_id": "d", "checkin_id": "c-1", "action": "cancel", "note": "Not needed"},
    {"request_id": "d", "checkin_id": "c-1", "action": "done", "title": "Retitle"},
    {"request_id": "d", "checkin_id": "c-1", "action": "skip", "planned_at": "2026-10-09T15:00:00Z"},
    {"request_id": "d", "checkin_id": "c-1", "action": "reschedule"},
    {"request_id": "d", "checkin_id": "c-1", "action": "reschedule", "planned_at": "2026-10-09T15:00:00Z",
     "prompt_kind": "custom"},
])
def test_rejects_invalid_followups(runtime, body):
    client, owner, *_ = _owner(runtime)
    _post(client, owner, _plan())
    response = client.post(PATH, headers=owner, json=body)
    assert response.status_code == 422
    assert response.json() == INVALID
    assert "home" not in response.text and "Retitle" not in response.text
    assert len(_rows(runtime)) == 1


def test_planning_window_bounds_are_inclusive(runtime):
    earliest = owner_checkins.record(runtime, _plan(request_id="early", checkin_id="c-early",
                                                    planned_at="2026-10-08T12:00:00Z"))
    latest = owner_checkins.record(runtime, _plan(request_id="late", checkin_id="c-late",
                                                  planned_at="2026-12-08T13:00:00+01:00"))
    assert earliest["checkin"]["state"] == "missed"
    assert latest["checkin"]["planned_at"] == "2026-12-08T12:00:00.000Z"
    with pytest.raises(ValueError):
        owner_checkins.record(runtime, {"request_id": "move", "checkin_id": "c-late", "action": "reschedule",
                                        "planned_at": "2026-10-08T11:59:59.999999Z"})
    assert len(_rows(runtime)) == 2


def test_rejects_malformed_and_oversized_bodies(runtime):
    client, owner, *_ = _owner(runtime)
    padded = json.dumps(_plan()) + " " * owner_checkins.CHECKIN_BODY_MAX_BYTES
    for content in (padded, "not json", "[]", '"plan"', "", json.dumps(_plan())[:-1]):
        response = client.post(PATH, headers={**owner, "Content-Type": "application/json"}, content=content)
        assert response.status_code == 422
        assert response.json() == INVALID
    with pytest.raises(ValueError):
        owner_checkins.record(runtime, {**_plan(), "title": "x" * 80, "padding": "y" * 4096})
    with pytest.raises(ValueError):
        owner_checkins.record(runtime, [_plan()])
    assert _rows(runtime) == []
    # A request at the field limits fits; titles count characters, not bytes.
    largest ={"request_id": "r" * 128, "checkin_id": "c" * 128, "action": "plan",
               "planned_at": "2026-10-09T14:00:00Z", "prompt_kind": "interval", "title": "é" * 80}
    assert _post(client, owner, largest).status_code == 200


def test_no_side_effects(runtime):
    client, owner, *_ = _owner(runtime)
    database = runtime.database
    database.execute("INSERT INTO dashboard_control_state VALUES ('owner:deployment', 7)")
    database.execute("INSERT INTO dashboard_commands VALUES ('cmd','owner:deployment','hash',NULL,'DONE',"
                     "'2026-10-09T11:00:00.000000Z')")
    database.execute("INSERT INTO activity_events VALUES ('event','portfolio','owner_pause','{}',"
                     "'2026-10-09T11:00:00.000000Z','hash',NULL)")

    def tables():
        names = [row[0] for row in database.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('owner_checkins','sqlite_sequence')")]
        return {name: [tuple(row) for row in database.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
                for name in names if not name.startswith("sqlite_")}

    before = tables()
    _post(client, owner, _plan())
    _post(client, owner, _plan())
    _post(client, owner, {"request_id": "move", "checkin_id": "c-1", "action": "reschedule",
                          "planned_at": "2026-10-09T15:00:00Z"})
    _post(client, owner, {"request_id": "done", "checkin_id": "c-1", "action": "done", "note": "Fine"})
    assert client.post(PATH, headers=owner, json=_plan(title="Changed")).status_code == 409
    assert client.post(PATH, headers=owner, json=_plan(request_id="again")).status_code == 409
    assert client.post(PATH, headers=owner, json=_plan(request_id="bad", title="/home/x")).status_code == 422
    assert client.get(PATH, headers=owner).status_code == 200
    owner_checkins.window(runtime, "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z")
    owner_checkins.last_done_at(runtime)
    after = tables()
    assert after == before
    assert after["dashboard_control_state"] == [("owner:deployment", 7)]
    assert len(after["activity_events"]) == 1
    assert len(_rows(runtime)) == 3


def test_window_and_last_done(runtime):
    client, owner, *_ = _owner(runtime)
    assert owner_checkins.last_done_at(runtime) is None
    for index, hour in enumerate((20, 13, 16, 22)):
        _post(client, owner, _plan(request_id=f"plan-{index}", checkin_id=f"c-{index}",
                                   planned_at=f"2026-10-09T{hour}:00:00Z", title=f"Review {hour}"))
    _post(client, owner, _plan(request_id="plan-tomorrow", checkin_id="c-tomorrow",
                               planned_at="2026-10-10T09:00:00Z", prompt_kind="next_day"))
    window = owner_checkins.window(runtime, "2026-10-09T13:00:00Z", "2026-10-09T22:00:00Z")
    assert [item["checkin_id"] for item in window] == ["c-1", "c-2", "c-0"]  # [start, end), soonest first

    # A reschedule moves a check-in by its current time, not its first one.
    _post(client, owner, {"request_id": "move-0", "checkin_id": "c-0", "action": "reschedule",
                          "planned_at": "2026-10-10T08:00:00Z"})
    _post(client, owner, {"request_id": "move-3", "checkin_id": "c-3", "action": "reschedule",
                          "planned_at": "2026-10-09T14:00:00+00:00"})
    window = owner_checkins.window(runtime, "2026-10-09T13:00:00Z", "2026-10-09T22:00:00Z")
    assert [item["checkin_id"] for item in window] == ["c-1", "c-3", "c-2"]
    assert [item["planned_at"] for item in window] == ["2026-10-09T13:00:00.000Z", "2026-10-09T14:00:00.000Z",
                                                        "2026-10-09T16:00:00.000Z"]
    assert owner_checkins.window(runtime, "2026-10-09T15:00:00+02:00", "2026-10-09T15:00:00+02:00") == []
    tomorrow = owner_checkins.window(runtime, "2026-10-10T00:00:00Z", "2026-10-11T00:00:00Z")
    assert [item["checkin_id"] for item in tomorrow] == ["c-0", "c-tomorrow"]
    for start, end in (("2026-10-09T13:00:00", "2026-10-10T00:00:00Z"), ("2026-10-10T00:00:00Z", "2026-10-09Z"),
                       ("2026-10-10T00:00:00Z", "2026-10-09T00:00:00Z")):
        with pytest.raises(ValueError):
            owner_checkins.window(runtime, start, end)

    # Closed check-ins stay in the window with their final state.
    runtime.clock.advance(3600)
    _post(client, owner, {"request_id": "done-1", "checkin_id": "c-1", "action": "done"})
    assert owner_checkins.last_done_at(runtime) == "2026-10-09T13:00:00.000Z"
    _post(client, owner, {"request_id": "skip-2", "checkin_id": "c-2", "action": "skip"})
    runtime.clock.advance(1800)
    _post(client, owner, {"request_id": "done-3", "checkin_id": "c-3", "action": "done"})
    runtime.clock.advance(1800)
    _post(client, owner, {"request_id": "cancel-t", "checkin_id": "c-tomorrow", "action": "cancel"})
    assert owner_checkins.last_done_at(runtime) == "2026-10-09T13:30:00.000Z"
    states = {item["checkin_id"]: item["state"]
              for item in owner_checkins.window(runtime, "2026-10-09T00:00:00Z", "2026-10-11T00:00:00Z")}
    assert states == {"c-1": "done", "c-3": "done", "c-2": "skipped", "c-0": "planned", "c-tomorrow": "cancelled"}

    # Other deployments neither see nor change these check-ins.
    other = SimpleNamespace(**{**vars(runtime), "deployment_id": "other"})
    assert owner_checkins.window(other, "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z") == []
    assert owner_checkins.last_done_at(other) is None
    assert owner_checkins.record(other, _plan(request_id="done-1"))["replayed"] is False

    # GET: defaults to now - 7 d .. now + 30 d and validates the span.
    response = client.get(PATH, headers=owner)
    assert response.status_code == 200
    body = CheckinsResponse.model_validate(response.json())
    assert body.as_of == "2026-10-09T14:00:00.000Z"
    assert [item.checkin_id for item in body.checkins] == ["c-1", "c-3", "c-2", "c-0", "c-tomorrow"]
    assert client.get(PATH, headers=owner, params={"from": "2026-10-09T15:00:00Z"}).json()["checkins"][0][
        "checkin_id"] == "c-2"
    assert [item["checkin_id"] for item in client.get(PATH, headers=owner, params={
        "from": "2026-10-09T13:30:00Z", "to": "2026-10-09T16:00:00Z"}).json()["checkins"]] == ["c-3"]
    runtime.clock.advance(8 * 86400)
    assert client.get(PATH, headers=owner).json()["checkins"] == []
    for params in ({"from": "2026-01-01T00:00:00Z", "to": "2026-06-01T00:00:00Z"},
                   {"from": "2026-10-10T00:00:00Z", "to": "2026-10-09T00:00:00Z"},
                   {"from": "yesterday-secret"}, {"to": "2026-10-09T00:00:00"}, {"from": "2000-01-01T00:00:00Z"}):
        response = client.get(PATH, headers=owner, params=params)
        assert response.status_code == 422
        assert response.json() == {"detail": "invalid check-in query"}


def test_window_returns_at_most_200_soonest(runtime):
    for index in range(205):
        owner_checkins.record(runtime, _plan(request_id=f"r-{index}", checkin_id=f"c-{index:03d}",
                                             planned_at=f"2026-10-{10 + index // 24:02d}T{index % 24:02d}:00:00Z"))
    items = owner_checkins.window(runtime, "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z")
    assert len(items) == 200
    assert [item["checkin_id"] for item in items] == [f"c-{index:03d}" for index in range(200)]
    CheckinsResponse.model_validate({"as_of": "2026-10-09T12:00:00.000Z", "checkins": items})


def test_unavailable_without_the_0025_table(tmp_path):
    database = Database(tmp_path / "old.sqlite")
    database.execute("DROP TABLE IF EXISTS owner_checkins")  # a database from before migration 0025
    runtime = SimpleNamespace(database=database, clock=FrozenClock(NOW), deployment_id="deployment")
    client, owner, *_ = _owner(runtime)
    assert owner_checkins.available(runtime) is False
    assert client.get(PATH, headers=owner).status_code == 503
    assert client.post(PATH, headers=owner, json=_plan()).status_code == 503
    assert owner_checkins.available(runtime) is False
    _ensure_checkin_table(database)
    assert owner_checkins.available(runtime) is True
    database.close()


def test_module_implements_the_frozen_interface():
    for name, method in inspect.getmembers(OwnerCheckins, inspect.isfunction):
        if name.startswith("_"):
            continue
        expected = list(inspect.signature(method).parameters)[1:]
        assert list(inspect.signature(getattr(owner_checkins, name)).parameters) == expected, name


def test_schema_is_one_additive_migration_after_0024(tmp_path, monkeypatch):
    version, statements = migration()
    assert (version, statements) == ("0025", OWNER_CHECKIN_STATEMENTS)
    registered = dict(migrate.STATEMENTS)
    if version in registered:  # once migrate.py registers 0025, it must be exactly this schema, right after 0024
        assert registered[version] == statements
        versions = [name for name, _ in migrate.STATEMENTS]
        assert versions[versions.index(version) - 1] == "0024"
    earlier = [(name, ddl) for name, ddl in migrate.STATEMENTS if name < version]
    assert earlier[-1][0] == "0024"
    monkeypatch.setattr(migrate, "STATEMENTS", earlier)
    connection = sqlite3.connect(tmp_path / "upgrade.sqlite", isolation_level=None)
    assert migrate.apply_migrations(connection, "2026-10-09T00:00:00.000000Z")[-1] == "0024"
    connection.execute("INSERT INTO portfolios VALUES ('retained','paper','EUR','experiment','active',NULL,"
                       "'2026-01-01T00:00:00.000000Z')")
    monkeypatch.setattr(migrate, "STATEMENTS", [*earlier, (version, statements)])
    assert migrate.apply_migrations(connection, "2026-10-09T12:00:00.000000Z") == ["0025"]
    assert connection.execute("SELECT portfolio_id FROM portfolios").fetchall() == [("retained",)]
    assert connection.execute("SELECT COUNT(*) FROM owner_checkins").fetchone()[0] == 0
    assert migrate.apply_migrations(connection, "2026-10-09T12:00:00.000000Z") == []
    connection.close()
