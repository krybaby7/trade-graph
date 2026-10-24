"""Original provider budgets and retained costs survive mutable-table omissions."""

import json
import sqlite3
from decimal import Decimal

import pytest
from tests.integration.test_protected_department_graph import GRAPH, protected_flow
from tests.integration.test_runtime_models import RuntimeFlow

from trade_graph.application.protected_runtime import ProtectedPaperRuntime
from trade_graph.contracts.models import ModelUsage
from trade_graph.domain.errors import StaleState
from trade_graph.kernel.runtime_manifest import ProtectedRuntimeManifest, protected_package_sha256


def reserve(flow, *, task=None, synthetic=True):
    task = task or flow.add("research")
    row = flow.row(task)
    return flow.assembly.gateway.budget.reserve(deployment_id="deployment", role="research", task_id=task,
        root_task_id=row["root_task_id"], price_card_id="primary", max_input=1000, max_output=1000,
        max_tools=0, fx_rate=Decimal(1), fx_buffer=Decimal(1), priority=False, synthetic=synthetic,
        purpose="synthetic continuity preparation")


def test_original_reservation_is_sealed_before_actual_scripted_provider_effect(tmp_path):
    flow, runtime = protected_flow(tmp_path)
    original, observed = flow.assembly.gateway.scripted.complete, []
    def complete(request):
        reservations = flow.db.execute("SELECT reservation_id FROM budget_reservations").fetchall()
        assert len(reservations) == 1
        origin = runtime.financial.origins._load(reservations[0][0], "ORIGIN")
        assert origin["reservation"]["state"] == "RESERVED"
        assert origin["task_scope"]["portfolio_id"] == flow.pid
        observed.append(origin["reservation_id"])
        return original(request)
    flow.assembly.gateway.scripted.complete = complete
    flow.add("research")
    assert flow.run("research") == 1
    assert len(observed) == 1
    runtime.financial.origins.verify_all()
    assert flow.db.execute("SELECT count(*) FROM protected_financial_budget_origins").fetchone()[0] == 2


@pytest.mark.parametrize("attack", ["receipt-delete", "receipt-charge", "allocation-delete", "allocation-rewrite",
                                     "reservation-reset", "reservation-amount", "task-scope"])
def test_known_provider_cost_cannot_disappear_or_replenish_budget(tmp_path, attack):
    flow, runtime = protected_flow(tmp_path)
    task = flow.add("research")
    assert flow.run("research") == 1
    runtime.financial.origins.verify_all()
    if attack == "receipt-delete":
        flow.db.execute("DELETE FROM cost_allocations")
        flow.db.execute("DELETE FROM usage_receipts")
    elif attack == "receipt-charge":
        flow.db.execute("UPDATE usage_receipts SET reporting_cost='0.001'")
    elif attack == "allocation-delete":
        flow.db.execute("DELETE FROM cost_allocations")
    elif attack == "allocation-rewrite":
        flow.db.execute("UPDATE cost_allocations SET amount='123'")
    elif attack == "reservation-reset":
        flow.db.execute("UPDATE budget_reservations SET state='RESERVED'")
    elif attack == "reservation-amount":
        flow.db.execute("UPDATE budget_reservations SET amount='0'")
    else:
        flow.db.execute("UPDATE tasks SET root_task_id='different-original' WHERE task_id=?", (task,))
    with pytest.raises(StaleState, match="protected"):
        runtime.financial.origins.verify_all()
    with pytest.raises(StaleState, match="protected"):
        reserve(flow)


@pytest.mark.parametrize("attack", ["reduce", "release", "deployment", "delete"])
def test_unresolved_pre_dispatch_hold_cannot_reset_original_authority(tmp_path, attack):
    flow, runtime = protected_flow(tmp_path)
    reservation = reserve(flow, synthetic=False)
    flow.assembly.gateway.budget.mark_uncertain(reservation)
    runtime.financial.origins.verify_all()
    assert flow.office.budget.remaining("deployment") < 5
    if attack == "reduce":
        flow.db.execute("UPDATE budget_reservations SET amount='0'")
    elif attack == "release":
        flow.db.execute("UPDATE budget_reservations SET state='CANCELLED'")
    elif attack == "deployment":
        flow.db.execute("UPDATE budget_reservations SET deployment_id='other-deployment'")
    else:
        # Immutable origin FK and trigger prevent the ordinary deletion path.
        flow.db.connection.execute("PRAGMA foreign_keys=OFF")
        flow.db.execute("DELETE FROM budget_reservations")
        flow.db.connection.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(StaleState, match="protected"):
        runtime.financial.origins.verify_all()


@pytest.mark.parametrize("settlement", ["committed", "conservative"])
def test_retained_receipt_allows_exact_real_settlement_of_original_hold(tmp_path, settlement):
    flow, runtime = protected_flow(tmp_path)
    budget = flow.assembly.gateway.budget
    reservation = reserve(flow, synthetic=False)
    before = flow.office.budget.remaining("deployment")
    budget.mark_uncertain(reservation)
    if settlement == "committed":
        with flow.db.immediate():
            receipt = budget.commit(reservation, ModelUsage(uncached_input_tokens=10, billed_output_tokens=5),
                provider="scripted", model="scripted", fx_rate=Decimal(1))
            budget.allocate(receipt, {flow.pid: Decimal(1)})
        assert flow.office.budget.remaining("deployment") > before
    else:
        receipt = budget.conservative_charge(reservation)
        assert flow.office.budget.remaining("deployment") == before
    runtime.financial.origins.verify_all()
    sealed = runtime.financial.origins._load(reservation, "RECEIPT")
    assert sealed["receipt"]["receipt_id"] == receipt
    assert sealed["allocations"] == runtime.financial.origins.allocations(receipt)
    assert sealed["reservation"]["state"] in {"COMMITTED", "CONSERVATIVE"}


def test_budget_continuity_includes_other_portfolios_sharing_same_deployment(tmp_path):
    flow, runtime = protected_flow(tmp_path)
    other = flow.engineer.ledger.create_portfolio(reporting_currency="EUR", mode="paper")
    flow.office.execution.authority.install_mandate(
        flow.office.execution.authority.active_mandate(flow.pid).model_copy(
            update={"portfolio_id": other, "mandate_id": "other-portfolio-mandate"}), role="owner")
    runtime.financial.prepare_history(other, instance_id=runtime.controller.instance_id)
    task = flow.office.scheduler.add_task(role="research", objective="Other exact portfolio scope.",
        portfolio_id=other, max_attempts=1, allocated_spend=Decimal(1))
    reservation = reserve(flow, task=task, synthetic=False)
    assert runtime.financial.origins._load(reservation, "ORIGIN")["portfolio_id"] == other
    flow.db.execute("UPDATE budget_reservations SET amount='0' WHERE reservation_id=?", (reservation,))
    with pytest.raises(StaleState, match="original budget"):
        runtime.financial.origins.verify_all()


def test_first_owner_preparation_cannot_rebaseline_existing_uncertain_outbound_hold(tmp_path):
    import hashlib
    flow = RuntimeFlow(tmp_path, provider="scripted", paid=False, owner_paid=False, keys={})
    reservation = reserve(flow)
    flow.office.budget.mark_uncertain(reservation)
    manifest = ProtectedRuntimeManifest(schema_version=1, protected_package_sha256=protected_package_sha256(),
        deployment_id="deployment", approved_source_sha256=(hashlib.sha256(GRAPH.encode()).hexdigest(),),
        operations=("submit_decision", "invoke_model", "apply_role_result"))
    runtime = ProtectedPaperRuntime(database=flow.db, clock=flow.clock, execution=flow.office.execution,
        manifest=manifest, capability_key=b"synthetic-original-budget-key-only", instance_id="originals")
    with pytest.raises(StaleState, match="uncertain"):
        runtime.controller.admit_release(release_id="graph-v1", source_text=GRAPH)
    assert flow.db.execute("SELECT count(*) FROM protected_financial_budget_origins").fetchone()[0] == 0
    assert not runtime.financial.history.path.exists()


def test_original_reservation_and_receipt_mac_fields_are_immutable(tmp_path):
    flow, runtime = protected_flow(tmp_path)
    reservation = reserve(flow)
    original = runtime.financial.origins._load(reservation, "ORIGIN")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        flow.db.execute("UPDATE protected_financial_budget_origins SET origin_json=?", (json.dumps(original),))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        flow.db.execute("DELETE FROM protected_financial_budget_origins")


@pytest.mark.parametrize("restore", ["before-dispatch", "before-settlement"])
def test_database_rollback_before_next_financial_issue_cannot_erase_provider_budget_or_cost(tmp_path, restore):
    flow, runtime = protected_flow(tmp_path)
    flow.add("research")
    backup = sqlite3.connect(tmp_path / "old-cost.sqlite")
    if restore == "before-dispatch":
        flow.db.connection.backup(backup)
    else:
        original = flow.assembly.gateway.scripted.complete
        def complete(request):
            # This backup includes the durably witnessed dispatch hold, but no
            # receipt yet. Restoring it must not erase the later paid-cost proof.
            flow.db.connection.backup(backup)
            return original(request)
        flow.assembly.gateway.scripted.complete = complete
    assert flow.run("research") == 1
    assert flow.db.execute("SELECT count(*) FROM usage_receipts").fetchone()[0] == 1
    backup.backup(flow.db.connection)
    backup.close()
    assert not runtime.financial.history.ready(flow.pid)
    with pytest.raises(StaleState, match="independent financial witness"):
        reserve(flow)


def test_dispatch_witness_publication_failure_retains_hold_and_prevents_provider_effect(tmp_path, monkeypatch):
    flow, runtime = protected_flow(tmp_path)
    calls = []
    original = flow.assembly.gateway.scripted.complete
    def complete(request):
        calls.append(request.role)
        return original(request)
    flow.assembly.gateway.scripted.complete = complete
    def failed(*args):
        raise OSError("synthetic cost witness publication failure")
    monkeypatch.setattr(runtime.financial.history, "publish", failed)
    flow.add("research")
    with pytest.raises(OSError, match="publication failure"):
        flow.run("research")
    assert not calls
    assert flow.db.execute("SELECT count(*) FROM budget_reservations WHERE state='RESERVED'").fetchone()[0] == 1
    assert flow.db.execute("SELECT count(*) FROM protected_financial_budget_origins").fetchone()[0] == 1
    assert flow.db.execute("SELECT count(*) FROM usage_receipts").fetchone()[0] == 0
    assert not runtime.financial.history.ready(flow.pid)


def test_settlement_witness_failure_preserves_known_cost_before_role_acknowledgment(tmp_path, monkeypatch):
    flow, runtime = protected_flow(tmp_path)
    original = flow.assembly.gateway.scripted.complete
    def complete(request):
        def failed(*args):
            raise OSError("synthetic settlement witness failure")
        monkeypatch.setattr(runtime.financial.history, "publish", failed)
        return original(request)
    flow.assembly.gateway.scripted.complete = complete
    flow.add("research")
    with pytest.raises(OSError, match="settlement witness failure"):
        flow.run("research")
    assert flow.db.execute("SELECT count(*) FROM usage_receipts").fetchone()[0] == 1
    assert flow.db.execute("SELECT count(*) FROM cost_allocations").fetchone()[0] == 1
    assert flow.db.execute("SELECT count(*) FROM protected_financial_budget_origins").fetchone()[0] == 2
    assert flow.db.execute("SELECT count(*) FROM role_results").fetchone()[0] == 0
    assert not runtime.financial.history.ready(flow.pid)
