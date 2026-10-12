"""HQ tells the owner, in plain words, that the software refused an order and that no money moved."""

import json
from types import SimpleNamespace

import pytest
from tests.integration import test_hq_projection as t
from tests.integration.test_dashboard import _client

from trade_graph.api import evidence, resources
from trade_graph.application import service_controller
from trade_graph.domain.clock import utc_iso

RAW = "below venue minimum; size was not increased"


@pytest.fixture
def env(tmp_path, monkeypatch):
    client, database, portfolio, token, csrf, leader = _client(tmp_path)
    runtime = client.app.state.runtime
    runtime.clock.advance(2 * 86400 + 3600)
    monkeypatch.setattr(service_controller, "process_identity", lambda pid: "birth" if pid == t.LIVE_PID else None)
    monkeypatch.setattr(resources, "observation",
                        lambda runtime: {"observed_at": utc_iso(runtime.clock.now()), "warnings": []})
    yield SimpleNamespace(client=client, runtime=runtime, db=database, pid=portfolio, token=token, csrf=csrf,
                          leader=leader)
    database.close()


def _refused(env):
    refusal = {"decision_id": "d-refused", "task_id": "t-refused", "action": "enter", "side": "buy",
               "symbol": "BTC/USD", "quantity": "0.00001", "order_type": "market", "limit_price": None,
               "code": "validation", "error": "ValidationFailure", "reason": RAW, "refused_at": t.at(env, -300)}
    out = {"decision_id": "d-refused", "intent_id": None, "action": "enter", "refusal": refusal}
    t.task(env, "t-refused", "trader", "SUCCEEDED", created=-400, output=out, result=out)
    payload = {"action": "enter", "symbol": "BTC/USD", "quantity": {"amount": "0.00001", "asset": "BTC"},
               "rationale": "Synthetic small entry."}
    t.insert(env, "decisions", decision_id="d-refused", portfolio_id=env.pid, action="enter",
             payload_json=json.dumps(payload), mandate_revision="1", policy_revision="1", snapshot_id="snap",
             system_version_id="v1", created_at=t.at(env, -300), task_id="t-refused")


def _item(data, item_id):
    return next(item for item in data["story"]["items"] if item["id"] == item_id)


def test_refused_practice_order_reads_as_not_placed_and_no_stop(env):
    t.run(env)
    _refused(env)
    data = t.snap(env)
    item = _item(data, "decision:d-refused")
    assert t.text(item["headline"]) == "Trader decided to buy BTC."
    assert t.text(item["consequence"]) == ("The software didn't place this practice order: the order was smaller "
                                           "than the exchange's minimum. No money moved.")
    assert [segment["code"] for segment in item["consequence"] if "code" in segment] == [RAW]
    assert item["problem"] is False
    assert data["now"]["code"] != "safety_stop"
    assert "task:t-refused" not in [item["id"] for item in data["story"]["items"]]
    assert "safety_stop" not in t.codes(data)
    detail = evidence.decision(env.runtime, "d-refused")
    assert detail["task"]["completion"]["refusal"]["reason"] == RAW and detail["orders"] == []


def test_refused_live_order_never_says_practice(env):
    t.quiet(env, mode="live")
    _refused(env)
    item = _item(t.snap(env), "decision:d-refused")
    words = t.text(item["consequence"])
    assert words == ("The software didn't place this order: the order was smaller than the exchange's minimum. "
                     "No money moved.")
    assert "practice" not in words.lower()
