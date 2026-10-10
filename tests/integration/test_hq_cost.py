"""HQ reads stay cheap: no expensive projections, and money comes from a fingerprinted cache (spec §B.9)."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from tests.integration import test_hq_projection as t
from tests.integration.test_dashboard import _client

from trade_graph.adapters.persistence.owner_checkin_schema import OWNER_CHECKIN_STATEMENTS
from trade_graph.api import (
    activity,
    controls,
    financial,
    health,
    hq,
    hq_facts,
    hq_money,
    live,
    owner_checkins,
    resources,
    review_export,
)
from trade_graph.api.hq_contract import validate_snapshot
from trade_graph.api.owner_expenses import record
from trade_graph.application import service_controller
from trade_graph.application.service_controller import ServiceController
from trade_graph.domain.clock import utc_iso

POISONED = (
    (activity, "overview"), (activity, "_attempts"), (live, "overview"), (live, "health"), (health, "health"),
    (controls, "configuration"), (review_export, "export"), (ServiceController, "status"),
    (ServiceController, "_readiness"),
    # The ownership probe takes the database lock for a moment; HQ never probes (D110).
    (service_controller, "database_owned"), (hq_facts, "database_owned"),
)
FIXTURE_SCENARIOS = sorted(path.stem for path in t.FIXTURES.glob("snapshot_*.json"))
EXTRA_SCENARIOS = ("owner_pause_with_blocker", "working_with_attempts", "optimisation_cycle", "checkin_planned")


def _quiet_host(monkeypatch) -> None:
    """The host's own resource counters never leak into these tests (review T14)."""
    monkeypatch.setattr(resources, "observation",
                        lambda runtime: {"observed_at": utc_iso(runtime.clock.now()), "warnings": []})


def _poison(monkeypatch) -> None:
    for owner, name in POISONED:
        monkeypatch.setattr(owner, name, lambda *args, _name=name, **kwargs: pytest.fail(f"HQ called {_name}"),
                            raising=False)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    client, database, *_ = _client(tmp_path)
    runtime = client.app.state.runtime
    runtime.clock.advance(3600)
    _quiet_host(monkeypatch)
    _poison(monkeypatch)
    yield runtime
    database.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    client, database, portfolio, token, csrf, leader = _client(tmp_path)
    runtime = client.app.state.runtime
    runtime.clock.advance(2 * 86400 + 3600)
    monkeypatch.setattr(service_controller, "process_identity", lambda pid: "birth" if pid == t.LIVE_PID else None)
    _quiet_host(monkeypatch)
    yield SimpleNamespace(client=client, runtime=runtime, db=database, pid=portfolio, token=token, csrf=csrf,
                          leader=leader)
    database.close()


def _extra_scenario(env, name: str) -> hq.Viewer:
    if name == "owner_pause_with_blocker":  # reaches the Resume barrier prediction in hq.snapshot
        t.run(env)
        t.pause(env, "MANAGE_ONLY", "owner")
        t.insert(env, "order_intents", intent_id="i1", portfolio_id=env.pid, client_order_id="c1", state="UNKNOWN",
                 symbol="BTC/USD", payload_json='{"client_order_id":"c1","symbol":"BTC/USD"}',
                 created_at=t.at(env, -30), updated_at=t.at(env, -30))
    elif name == "working_with_attempts":
        t.quiet(env)
        t.task(env, "t-work", "trader", "RUNNING", created=-120, lease=60, attempts=2)
        t.task(env, "t-stuck", "research", "BLOCKED_BUDGET", created=-900, attempts=1,
               output={"reason": "budget exhausted"})
    elif name == "optimisation_cycle":
        t.quiet(env)
        t.task(env, "t-cycle", "optimisation", "RUNNING", created=-900, objective="owner-optimisation-cycle",
               lease=60, attempts=1)
        t.task(env, "t-cycle-a", "leader", "QUEUED", created=-600, root="t-cycle")
    elif name == "checkin_planned":
        t.quiet(env)
        if not owner_checkins.available(env.runtime):
            with env.db.immediate() as connection:
                for statement in OWNER_CHECKIN_STATEMENTS:
                    connection.execute(statement)
        owner_checkins.record(env.runtime, {
            "request_id": "poison-plan", "checkin_id": "c-poison", "action": "plan", "prompt_kind": "baseline",
            "planned_at": utc_iso(env.runtime.clock.now() + timedelta(minutes=10)), "title": "Morning review"})
    return t.OWNER


@pytest.mark.parametrize("name", FIXTURE_SCENARIOS + list(EXTRA_SCENARIOS))
def test_no_scenario_reaches_an_expensive_projection(env, monkeypatch, name):
    """Every scenario shape, through hq.snapshot, a story page and both HTTP routes (review T11)."""
    viewer = t._scenario(env, name) if name.startswith("snapshot_") else _extra_scenario(env, name)
    _poison(monkeypatch)  # seeded first: only the HQ reads below are watched
    data = hq.snapshot(env.runtime, viewer, calendar_days=14)
    validate_snapshot(data)
    if name == "owner_pause_with_blocker":
        resume = next(card for card in data["decisions"] if card["id"] == "resume")
        assert resume["facts"]["predicted_blockers"], "the Resume barrier prediction was reached"
    if name == "checkin_planned":
        assert data["checkins"]["next_id"] == "c-poison"
    page = hq.story_page(env.runtime, viewer, before=data["story"]["window_start"])
    validate_snapshot({**data, "story": page["story"]})
    env.client.cookies.set("tg_session", env.token)
    for path in ("/", "/api/v1/hq?calendar_days=14"):
        response = env.client.get(path)
        assert response.status_code == 200, (path, response.text[:200])


def test_snapshot_uses_cache_and_never_runs_expensive_projections(runtime, monkeypatch):
    calls = []
    original = financial.overview

    def counted(reader):
        calls.append(reader)
        return original(reader)

    monkeypatch.setattr(financial, "overview", counted)
    viewer = hq.Viewer("owner", "cookie", True)
    first = hq.snapshot(runtime, viewer)
    validate_snapshot(first)
    assert len(calls) == 1
    runtime.clock.advance(20)
    second = hq.snapshot(runtime, viewer)
    assert len(calls) == 1
    assert second["money"]["as_of"] == first["money"]["as_of"]
    assert second["as_of"] != first["as_of"]
    start = runtime.database.execute("SELECT created_at FROM portfolios WHERE portfolio_id=?",
                                     (runtime.portfolio_id,)).fetchone()[0]
    record(runtime, {"record_type": "completeness", "request_id": "hq-cost-fingerprint", "period_start": start,
                     "period_end": utc_iso(runtime.clock.now()), "expense_kinds": ["other"],
                     "evidence_ref": "owner-evidence:synthetic-complete"})
    third = hq.snapshot(runtime, viewer)
    assert len(calls) == 2
    assert third["money"]["as_of"] != first["money"]["as_of"]
    assert "other_costs_unconfirmed" not in [item["code"] for item in third["money"]["pays_for_itself"]["missing"]]


def _text(segments) -> str:
    return "".join(segment.get("v") or segment.get("display") or "" for segment in segments or [])


def test_cache_hit_sees_a_new_open_order(runtime):
    viewer = hq.Viewer("owner", "cookie", True)
    first = hq.snapshot(runtime, viewer)
    assert first["money"]["holdings"]["flat"] is True
    now = utc_iso(runtime.clock.now())
    runtime.database.execute(
        """INSERT INTO order_intents (intent_id, portfolio_id, client_order_id, state, symbol, payload_json,
        created_at, updated_at) VALUES ('i-open', ?, 'c-open', 'OPEN', 'BTC/USD',
        '{"client_order_id":"c-open","symbol":"BTC/USD","side":"buy"}', ?, ?)""",
        (runtime.portfolio_id, now, now))
    second = hq.snapshot(runtime, viewer)  # the money cache is not reset by hand
    validate_snapshot(second)
    assert second["money"]["holdings"]["flat"] is False
    assert second["money"]["holdings"]["open_orders"] == 1
    assert second["money"]["counts"]["orders"] == 1
    stop = next(card for card in second["decisions"] if card["id"] == "stop_safely")
    assert stop["facts"]["flat"] is False
    assert "an order is open" in _text(stop["preview"]["what_happens"][0])


def test_cache_expires_after_60_seconds(runtime, monkeypatch):
    calls = []
    original = financial.overview
    monkeypatch.setattr(financial, "overview", lambda reader: calls.append(reader) or original(reader))
    clock = [1000.0]
    monkeypatch.setattr(hq_money, "_monotonic", lambda: clock[0])
    viewer = hq.Viewer("owner", "cookie", True)
    hq.snapshot(runtime, viewer)
    clock[0] += 59
    hq.snapshot(runtime, viewer)
    assert len(calls) == 1
    clock[0] += 2  # 61 s after the first computation
    hq.snapshot(runtime, viewer)
    assert len(calls) == 2


def test_story_page_and_reader_views_are_also_cheap(runtime):
    reader = hq.Viewer("reader", "bearer", False)
    data = hq.snapshot(runtime, reader, calendar_days=14)
    validate_snapshot(data)
    page = hq.story_page(runtime, reader, before=data["story"]["window_start"])
    assert set(page) == {"story"}


def test_money_failure_is_unknown_not_zero(runtime, monkeypatch):
    def broken(*args, **kwargs):
        raise ArithmeticError("synthetic valuation failure")

    monkeypatch.setattr(live, "economics", broken)
    data = hq.snapshot(runtime, hq.Viewer("owner", "cookie", True))
    validate_snapshot(data)
    money = data["money"]
    assert money["available"] is False and money["unavailable_reason"]
    assert money["practice_value"] is None and money["change"]["total"] is None
    assert money["pays_for_itself"] == {"status": "unknown", "net": None, "missing": [], "honest_note": None}
    assert "not known yet" in "".join(segment.get("v", "") for segment in data["now"]["detail"])
