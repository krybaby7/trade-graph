"""HQ decisions send the exact §B.8 bodies to the existing owner endpoints, through the full app (spec §E.1 WP4).

Every request uses an owner cookie session with its CSRF token, as the HQ page does. The service
controller uses a recording launcher, so no worker, provider or broker process is started.
"""

import json
import os
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_service_controller import runtime_stack

from trade_graph.api import resources
from trade_graph.api.app import create_app
from trade_graph.api.auth import issue_session
from trade_graph.application.service_controller import ServiceController
from trade_graph.domain.clock import utc_iso

PAUSE_REASON = "Owner HQ: pause AI, keep protection"
SPLIT_FIVE = {"leader": "0.2", "research": "0.2", "trader": "0.2", "learning": "0.2", "optimisation": "0.2"}


@pytest.fixture
def hq(tmp_path, monkeypatch):
    # The host's own resource counters never leak into these tests (review T14).
    monkeypatch.setattr(resources, "observation",
                        lambda runtime: {"observed_at": utc_iso(runtime.clock.now()), "warnings": []})
    runtime, broker = runtime_stack(tmp_path)
    launched = []
    runtime.service_controller = ServiceController(
        runtime, launcher=lambda command: launched.append(command) or SimpleNamespace(pid=os.getpid())
    )
    client = TestClient(create_app(runtime))
    token, csrf = issue_session(runtime.database, runtime.clock, "owner")
    client.cookies.set("tg_session", token)
    yield SimpleNamespace(client=client, runtime=runtime, broker=broker, launched=launched,
                          headers={"X-CSRF-Token": csrf})
    runtime.database.close()


def _post(hq, path: str, body: dict):
    return hq.client.post(path, headers=hq.headers, content=json.dumps(body))


def _revision(hq) -> int:
    response = hq.client.get("/api/v1/owner/config")
    assert response.status_code == 200
    return response.json()["revision"]


def _pause(request_id: str, revision: int) -> dict:
    return {"request_id": request_id, "expected_revision": revision, "profile": "MANAGE_ONLY",
            "reason": PAUSE_REASON}


def _snapshot(hq) -> dict:
    hq.runtime._hq_money = None
    response = hq.client.get("/api/v1/hq")
    assert response.status_code == 200
    return response.json()


def _card(snapshot: dict, decision_id: str) -> dict:
    return next(card for card in snapshot["decisions"] if card["id"] == decision_id)


def test_cookie_writes_need_the_csrf_header(hq):
    for path, body in (("/api/v1/owner/pause", _pause("no-csrf", 0)),
                       ("/api/v1/owner/start-trading", {"request_id": "no-csrf", "mode": "paper"}),
                       ("/api/v1/owner/checkins", {"request_id": "no-csrf", "checkin_id": "c-1", "action": "cancel"})):
        response = hq.client.post(path, content=json.dumps(body))
        assert response.status_code == 403 and response.json() == {"detail": "csrf"}
    assert hq.runtime.execution.profile(hq.runtime.portfolio_id) == "RUNNING"
    assert hq.launched == []


def test_pause_with_fresh_revision_then_stale_retry_and_resume(hq):
    assert _snapshot(hq)["viewer"]["can_act"] is True
    first = _post(hq, "/api/v1/owner/pause", _pause("hq-pause-1", _revision(hq)))
    assert first.status_code == 200
    assert first.json()["profile"] == "MANAGE_ONLY" and first.json()["management_continues"] is True
    assert hq.runtime.execution.profile(hq.runtime.portfolio_id) == "MANAGE_ONLY"
    resumed = _post(hq, "/api/v1/owner/resume", {"request_id": "hq-resume-1", "expected_revision": _revision(hq)})
    assert resumed.status_code == 200 and resumed.json()["profile"] == "RUNNING"

    stale = _post(hq, "/api/v1/owner/pause", _pause("hq-pause-2", 0))
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["reason"] == "stale revision" and detail["revision"] == _revision(hq) == 2
    assert hq.runtime.execution.profile(hq.runtime.portfolio_id) == "RUNNING"
    # HQ retries once with the reported revision and a new request id (D107).
    retried = _post(hq, "/api/v1/owner/pause", _pause("hq-pause-3", detail["revision"]))
    assert retried.status_code == 200 and retried.json()["profile"] == "MANAGE_ONLY"
    replay = _post(hq, "/api/v1/owner/pause", _pause("hq-pause-3", detail["revision"]))
    assert replay.status_code == 200 and replay.json() == retried.json()
    assert _revision(hq) == 3


def test_resume_blocked_by_unknown_order_replays_the_same_refusal(hq):
    runtime = hq.runtime
    assert _post(hq, "/api/v1/owner/pause", _pause("hq-pause", _revision(hq))).status_code == 200
    runtime.database.execute(
        """INSERT INTO order_intents
        (intent_id, portfolio_id, client_order_id, state, symbol, payload_json, created_at, updated_at)
        VALUES ('unknown', ?, 'unknown-client', 'UNKNOWN', 'BTC/USD',
        '{"client_order_id":"unknown-client","symbol":"BTC/USD"}', ?, ?)""",
        (runtime.portfolio_id, utc_iso(runtime.clock.now()), utc_iso(runtime.clock.now())),
    )
    resume = _card(_snapshot(hq), "resume")
    assert resume["shown"] is True and resume["available"] is True
    body = {"request_id": "hq-resume-blocked", "expected_revision": _revision(hq)}
    refused = _post(hq, "/api/v1/owner/resume", body)
    assert refused.status_code == 409
    assert "unresolved orders" in refused.json()["detail"]["barriers"]
    replay = _post(hq, "/api/v1/owner/resume", body)
    assert replay.status_code == 409 and replay.json() == refused.json()
    assert runtime.execution.profile(runtime.portfolio_id) == "MANAGE_ONLY"


def test_start_and_stop_use_the_existing_service_controls(hq):
    started = _post(hq, "/api/v1/owner/start-trading", {"request_id": "hq-start-1", "mode": "paper"})
    assert started.status_code == 202
    assert len(hq.launched) == 1 and hq.broker.submit_count == 0
    replay = _post(hq, "/api/v1/owner/start-trading", {"request_id": "hq-start-1", "mode": "paper"})
    assert replay.status_code == 202 and len(hq.launched) == 1
    # Stop answers 200 (the route sets no 202). The synthetic account holds nothing, so the existing
    # controller stops fully; with holdings, "manage-only" keeps protection running instead.
    stopped = _post(hq, "/api/v1/owner/stop-service", {"request_id": "hq-stop-1", "position_policy": "manage-only"})
    assert stopped.status_code == 200
    assert stopped.json()["profile"] == "STOPPED"
    assert hq.runtime.execution.profile(hq.runtime.portfolio_id) == "STOPPED"
    replay = _post(hq, "/api/v1/owner/stop-service", {"request_id": "hq-stop-1", "position_policy": "manage-only"})
    assert replay.status_code == 200 and replay.json() == {**stopped.json(), "replayed": True}


def _bill(request_id: str, weights: dict, *, scope: str, start: str, end: str) -> dict:
    bill_id = f"owner-bill-{request_id}"
    return {
        "request_id": request_id, "record_type": "expense", "bill_id": bill_id, "billing_scope": scope,
        "expense_kind": "subscription" if scope == "ai-subscription" else "other", "amount_native": "20",
        "native_currency": "USD", "incurred_at": start, "period_start": start, "period_end": end,
        "graph_share": "1", "allocation_policy": "Owner HQ: Trade Graph share of this bill",
        "department_weights": weights,
        "department_allocation_label": "Owner HQ: not split by department" if "shared" in weights
        else "Owner HQ: equal split across five scheduled departments",
        "evidence_ref": f"owner-evidence:{bill_id}",
    }


def test_bill_presets_are_exact_and_a_six_way_split_is_refused(hq):
    hq.runtime.clock.advance(40 * 86400)
    january, february = "2026-01-01T00:00:00.000Z", "2026-02-01T00:00:00.000Z"
    shared = _post(hq, "/api/v1/owner/expenses",
                   _bill("hq-bill-1", {"shared": "1"}, scope="ai-subscription", start=january, end=february))
    assert shared.status_code == 200 and shared.json()["department_weights"] == {"shared": "1"}
    five = _post(hq, "/api/v1/owner/expenses",
                 _bill("hq-bill-2", SPLIT_FIVE, scope="other-hosting", start=january, end=february))
    assert five.status_code == 200 and sorted(five.json()["department_weights"]) == sorted(SPLIT_FIVE)
    six = {name: "0.166666" for name in ("leader", "research", "trader", "learning", "optimisation", "engineer")}
    refused = _post(hq, "/api/v1/owner/expenses",
                    _bill("hq-bill-3", six, scope="other-internet", start=january, end=february))
    assert refused.status_code == 422
    complete = _post(hq, "/api/v1/owner/expenses", {
        "request_id": "hq-complete-1", "record_type": "completeness", "period_start": january,
        "period_end": february, "expense_kinds": ["other", "subscription"],
        "evidence_ref": "owner-evidence:complete-2026-01-a1b2c3d4"})
    assert complete.status_code == 200
    count = hq.runtime.database.execute("SELECT COUNT(*) FROM owner_expense_evidence").fetchone()[0]
    assert count == 3


def test_checkins_round_trip_into_the_hq_snapshot(hq):
    runtime = hq.runtime
    planned = utc_iso(runtime.clock.now() + timedelta(minutes=10))
    plan = {"request_id": "hq-plan-1", "checkin_id": "c-hq-1", "action": "plan", "planned_at": planned,
            "prompt_kind": "baseline", "title": "Morning review"}
    created = _post(hq, "/api/v1/owner/checkins", plan)
    assert created.status_code == 200 and created.json()["replayed"] is False
    assert created.json()["checkin"]["state"] == "due"
    assert _post(hq, "/api/v1/owner/checkins", plan).json()["replayed"] is True
    snapshot = _snapshot(hq)
    assert snapshot["checkins"]["available"] is True and snapshot["checkins"]["next_id"] == "c-hq-1"
    assert "checkin:c-hq-1" in [item["id"] for item in snapshot["calendar"]["items"]]
    assert "checkin_due" in [item["id"] for item in snapshot["attention"]]
    runtime.clock.advance(15 * 60)
    done = _post(hq, "/api/v1/owner/checkins", {"request_id": "hq-done-1", "checkin_id": "c-hq-1", "action": "done",
                                                "note": "Leader still failing / asked chat"})
    assert done.status_code == 200 and done.json()["checkin"]["state"] == "done"
    after = _snapshot(hq)
    assert after["checkins"]["last_done_at"] == done.json()["checkin"]["done_at"]
    assert after["prompt_facts"]["since"]["label"] == "my last check-in"
    assert "checkin_due" not in [item["id"] for item in after["attention"]]
    again = _post(hq, "/api/v1/owner/checkins", {"request_id": "hq-skip-1", "checkin_id": "c-hq-1", "action": "skip"})
    assert again.status_code == 409 and again.json() == {"detail": "check-in already closed"}
