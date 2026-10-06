"""Leader journals actual execution mode; paper Engineer acceptance never grants live changes."""

import json

import pytest
from tests.integration.test_engineer import _task
from tests.integration.test_runtime_models import RuntimeFlow
from tests.leadership_support import reply

from trade_graph.domain.money import Money


@pytest.mark.parametrize("mode", ["paper", "live"])
def test_leader_decision_retains_actual_execution_mode(tmp_path, mode):
    flow = RuntimeFlow(tmp_path, provider="scripted", paid=False, owner_paid=False, keys={})
    flow.office.execution.mode = mode
    flow.db.execute("UPDATE portfolios SET mode=? WHERE portfolio_id=?", (mode, flow.pid))
    flow.worker.reconcile = lambda: None
    flow.assembly.gateway.scripted.outputs["leader"] = reply([flow.ref])
    identity = flow.add("leader")
    assert flow.run("leader") == 1
    assert flow.row(identity)["status"] == "SUCCEEDED", flow.row(identity)["output_json"]
    decision = json.loads(flow.db.execute("SELECT document_json FROM leader_decisions").fetchone()[0])
    assert decision["mode"] == mode
    assert not flow.transport.calls


def test_live_leader_cannot_commission_paper_scoped_engineer_change(tmp_path):
    flow = RuntimeFlow(tmp_path, provider="scripted", paid=False, owner_paid=False, keys={})
    change = _task(flow.clock, flow.pid, flow.baseline, max_steps=1,
                   max_spend=Money(amount="0.5", currency="EUR"))
    flow.engineer.propose(flow.pid, change)
    flow.office.execution.mode = "live"
    flow.db.execute("UPDATE portfolios SET mode='live' WHERE portfolio_id=?", (flow.pid,))
    flow.worker.reconcile = lambda: None
    flow.transport.outputs["LeaderReply"] = lambda context: reply(context["evidence_refs"], [
        {"kind": "commission", "change_id": change.record_id}])
    # Scripted gateway does not consume HTTP outputs; pin the synthetic reply
    # in the real scripted adapter without giving the task provider authority.
    flow.assembly.gateway.scripted.outputs["leader"] = reply([flow.ref, change.record_id], [
        {"kind": "commission", "change_id": change.record_id}])
    identity = flow.add("leader")
    assert flow.run("leader") == 1
    assert flow.row(identity)["status"] == "FAILED", flow.row(identity)["output_json"]
    assert flow.db.execute("SELECT state FROM change_tasks WHERE change_id=?", (change.record_id,)).fetchone()[0] \
        == "PROPOSED"
    assert flow.db.execute("SELECT COUNT(*) FROM engineering_commissions").fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM tasks WHERE role='engineer'").fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM leader_decisions").fetchone()[0] == 0
    assert not flow.transport.calls
