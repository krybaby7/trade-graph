"""The HQ snapshot stays within its 80 KB budget on a busy synthetic week (review T2, fix B18, D110).

The seeding follows the review benchmark: five schedules, a trader turn with a decision
and a long rationale every 4 hours for 7 days, 300 mixed tasks (1 in 7 failed), 40
research notes and 72 hours of AI-allowance deferrals every 30 seconds.
"""

import json
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from tests.integration import test_hq_projection as t
from tests.integration.test_dashboard import _client

from trade_graph.api import hq, hq_facts, resources
from trade_graph.api.hq_contract import ESTIMATE_ITEMS_HORIZON_HOURS, iso_ms, validate_snapshot
from trade_graph.api.security import redact
from trade_graph.application import service_controller
from trade_graph.domain.clock import utc_iso

BUDGET = 80 * 1024
OWNER = hq.Viewer("owner", "cookie", True)


@pytest.fixture
def busy(tmp_path, monkeypatch):
    client, database, portfolio, token, csrf, leader = _client(tmp_path)
    runtime = client.app.state.runtime
    runtime.clock.advance(10 * 86400)
    monkeypatch.setattr(service_controller, "process_identity", lambda pid: "birth" if pid == t.LIVE_PID else None)
    monkeypatch.setattr(resources, "observation",
                        lambda runtime: {"observed_at": utc_iso(runtime.clock.now()), "warnings": []})
    env = SimpleNamespace(client=client, runtime=runtime, db=database, pid=portfolio, token=token, csrf=csrf,
                          leader=leader)
    _seed(env)
    yield env
    database.close()


def _seed(env) -> None:
    t.run(env)
    t.reading(env, remaining=55)
    t.schedule(env, "trader", 3600, interval=14400)
    t.schedule(env, "research", 7200, interval=86400)
    t.schedule(env, "learning", 9000, interval=302400)
    t.schedule(env, "leader", 20000, interval=604800)
    t.schedule(env, "optimisation", 30000, interval=604800)
    rationale = "Neither asset establishes the required uptrend; waiting for confirmation. " * 3
    for number, hour in enumerate(sorted(range(0, 7 * 24, 4), reverse=True), start=1):
        created = -hour * 3600 - 60
        t.task(env, f"t-tr-{number}", "trader", "SUCCEEDED", created=created, attempts=1,
               dedup_key=f"schedule:s-trader:{t.at(env, created)}", output={"decision_id": f"d-{number}"},
               result={"decision_id": f"d-{number}", "rationale": rationale})
        t.decision(env, f"d-{number}", created=created + 30, task_id=f"t-tr-{number}", rationale=rationale)
    for index in sorted(range(300), reverse=True):
        role = ("research", "learning", "leader", "trader")[index % 4]
        status = "FAILED" if index % 7 == 0 else "SUCCEEDED"
        t.task(env, f"t-x-{index}", role, status, created=-index * 1800 - 30, attempts=1,
               output={"reason": "model timeout_uncertain: no answer"} if status == "FAILED" else {"ok": True})
    for index in sorted(range(40), reverse=True):
        t.finding(env, f"f-{index}", created=-index * 3600 - 10, expires=86400 - index * 600)
    t.events(env, "subscription_quota_deferred",
             [(-72 * 3600 + 30 * index, {"task_id": f"t-q-{index // 40}", "role": "trader", "inference_attempts": 0})
              for index in range(8640)])


def _wire_bytes(data: dict) -> int:
    return len(json.dumps(redact(data), separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


@pytest.mark.parametrize("days", [7, 14])
def test_busy_week_payload_fits_budget(busy, days):
    began = time.perf_counter()
    data = hq.snapshot(busy.runtime, OWNER, calendar_days=days)
    elapsed = (time.perf_counter() - began) * 1000
    validate_snapshot(data)
    size = _wire_bytes(data)
    sections = {key: len(json.dumps(value, separators=(",", ":"), ensure_ascii=False)) for key, value in data.items()}
    print(f"{days} days: {size} B in {elapsed:.0f} ms; largest sections",
          sorted(sections.items(), key=lambda pair: -pair[1])[:5])  # logged, not asserted
    assert size <= BUDGET
    assert len(json.dumps(data)) < BUDGET  # the readable (non-compact) form fits too

    calendar = data["calendar"]
    horizon = iso_ms(busy.runtime.clock.now() + timedelta(hours=ESTIMATE_ITEMS_HORIZON_HOURS))
    assert not [item["id"] for item in calendar["items"] if item["status"] == "estimated" and item["at"] > horizon]
    trader = next(series for series in calendar["estimates"] if series["schedule_id"] == "s-trader")
    assert trader["dept"] == "trader" and trader["every_seconds"] == 14400 and trader["first_at"] > horizon
    listed = sorted(item["at"] for item in calendar["items"]
                    if item["dept"] == "trader" and item["status"] == "estimated")
    # The summary continues exactly where the listed estimates stop: same base, same interval.
    assert hq_facts.parse(trader["first_at"]) - hq_facts.parse(listed[-1]) == timedelta(seconds=14400)
    assert len(data["quests"]) <= 40


def test_busy_week_route_payload_fits_budget(busy):
    busy.client.cookies.set("tg_session", busy.token)
    response = busy.client.get("/api/v1/hq", params={"calendar_days": 14})
    assert response.status_code == 200
    body = response.json()
    assert body["viewer"]["can_act"] is True and body["decisions"]
    print(f"GET /api/v1/hq?calendar_days=14: {len(response.content)} B")
    assert len(response.content) <= BUDGET
