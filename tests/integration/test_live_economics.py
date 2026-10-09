"""Live economics use selected allocations and retain uncertain expense evidence."""

from decimal import Decimal

import pytest
from tests.integration.test_dashboard_financial import _receipt, _reservation, _runtime

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.application.execution import Execution
from trade_graph.domain.clock import utc_iso


def _live_runtime(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.execution = Execution(runtime.database, runtime.ledger, runtime.clock,
                                  PaperBroker(runtime.database, runtime.clock))
    runtime.clock.advance(1)
    return runtime


def _complete_owner_coverage(runtime):
    from trade_graph.api.owner_expenses import record

    start = runtime.database.execute("SELECT created_at FROM portfolios WHERE portfolio_id=?",
                                     (runtime.portfolio_id,)).fetchone()[0]
    record(runtime, {"record_type": "completeness", "request_id": "economic-coverage",
                     "period_start": start, "period_end": utc_iso(runtime.clock.now()),
                     "expense_kinds": ["subscription", "other"],
                     "evidence_ref": "owner-evidence:synthetic-complete"})


def test_live_ai_cost_uses_selected_portfolio_fraction_of_shared_receipt(tmp_path):
    from trade_graph.api import live

    runtime = _live_runtime(tmp_path)
    other = runtime.ledger.create_portfolio(reporting_currency="EUR")
    runtime.ledger.deposit(other, "EUR", Decimal("100"), "other-opening")
    receipt = _receipt(runtime)
    runtime.budget.allocate(receipt, {runtime.portfolio_id: Decimal("0.25"), other: Decimal("0.75")})
    _complete_owner_coverage(runtime)

    result = live.overview(runtime)["economic_result"]

    assert result["known_api_ai_expense"] == "0.225"
    assert result["allocated_ai_expense"] == "0.225"
    assert result["other_operating_expense"] == "0"
    assert result["known_other_expense"] == "0"
    assert result["net_result"] == "-0.225"
    assert result["status"] == "not_covered"


def test_unresolved_conservative_receipt_does_not_establish_cost_coverage(tmp_path):
    from trade_graph.api import live

    runtime = _live_runtime(tmp_path)
    reservation = _reservation(runtime)
    receipt = runtime.budget.conservative_charge(reservation)
    runtime.budget.allocate(receipt, {runtime.portfolio_id: Decimal("1")})
    _complete_owner_coverage(runtime)

    result = live.overview(runtime)["economic_result"]

    assert result["allocated_ai_expense"] is None
    assert result["known_api_ai_expense"] == "0"
    assert result["known_other_expense"] == "0"
    assert result["net_result"] is None
    assert result["status"] == "unknown"
    assert "API receipt or allocation coverage remains incomplete." in result["unknown_reasons"]


@pytest.mark.parametrize("fx_state", ["missing", "stale"])
def test_receipt_fx_uncertainty_remains_unknown_in_live_economics(tmp_path, fx_state):
    from trade_graph.api import live

    runtime = _live_runtime(tmp_path)
    receipt = _receipt(runtime)
    runtime.budget.allocate(receipt, {runtime.portfolio_id: Decimal("1")})
    # Retained legacy receipts can lack a current trusted reporting conversion.
    runtime.database.execute("UPDATE usage_receipts SET reporting_currency='GBP' WHERE receipt_id=?", (receipt,))
    if fx_state == "stale":
        runtime.ledger.observe_fx(base="GBP", quote="EUR", rate=Decimal("1"),
                                  source="synthetic stale reference", kind="reference", stale=True)
    _complete_owner_coverage(runtime)

    result = live.overview(runtime)["economic_result"]

    assert result["allocated_ai_expense"] is None
    assert result["known_api_ai_expense"] == "0"
    assert result["known_other_expense"] == "0"
    assert result["net_result"] is None
    assert result["status"] == "unknown"
    assert "API receipt or allocation coverage remains incomplete." in result["unknown_reasons"]


def test_deployment_owner_bill_is_not_assigned_to_one_of_multiple_portfolios(tmp_path):
    from trade_graph.api import live
    from trade_graph.api.owner_expenses import record

    runtime = _live_runtime(tmp_path)
    other = runtime.ledger.create_portfolio(reporting_currency="EUR")
    runtime.ledger.deposit(other, "EUR", Decimal("100"), "other-opening")
    start = runtime.database.execute("SELECT created_at FROM portfolios WHERE portfolio_id=?",
                                     (runtime.portfolio_id,)).fetchone()[0]
    end = utc_iso(runtime.clock.now())
    record(runtime, {"record_type": "expense", "request_id": "shared-owner-bill", "bill_id": "shared-plan",
                     "billing_scope": "owner-plan", "expense_kind": "subscription", "amount_native": "10",
                     "native_currency": "EUR", "incurred_at": start, "period_start": start, "period_end": end,
                     "graph_share": "1", "allocation_policy": "owner graph share",
                     "department_weights": {"trader": "1"}, "department_allocation_label": "owner weights",
                     "evidence_ref": "owner-evidence:synthetic-plan"})
    _complete_owner_coverage(runtime)

    overview = live.overview(runtime)
    result = overview["economic_result"]

    assert overview["owner_expenses"]["graph_subscription_allocation"]["amount"] == "10"
    assert result["allocated_ai_expense"] is None
    assert result["other_operating_expense"] is None
    assert result["net_result"] is None
    assert result["status"] == "unknown"
    assert "Owner bills allocate to the deployment; this portfolio's share is unknown." in result["unknown_reasons"]


def test_historical_period_uses_current_owner_evidence_and_incurred_fx_but_cuts_off_api_accruals(tmp_path):
    from trade_graph.api import live
    from trade_graph.api.owner_expenses import record

    runtime = _live_runtime(tmp_path)
    start = runtime.database.execute("SELECT created_at FROM portfolios WHERE portfolio_id=?",
                                     (runtime.portfolio_id,)).fetchone()[0]
    cutoff = utc_iso(runtime.clock.now())
    runtime.ledger.observe_fx(base="USD", quote="EUR", rate=Decimal("0.9"),
                              source="synthetic bill-date reference", kind="reference", stale=False)
    earlier = _receipt(runtime)
    runtime.budget.allocate(earlier, {runtime.portfolio_id: Decimal("1")})
    runtime.clock.advance(10)
    runtime.ledger.observe_fx(base="USD", quote="EUR", rate=Decimal("0.8"),
                              source="synthetic later reference", kind="reference", stale=False)
    later = _receipt(runtime)
    runtime.budget.allocate(later, {runtime.portfolio_id: Decimal("1")})
    record(runtime, {"record_type": "expense", "request_id": "late-owner-bill", "bill_id": "closed-plan",
                     "billing_scope": "closed-owner-plan", "expense_kind": "subscription", "amount_native": "20",
                     "native_currency": "USD", "incurred_at": cutoff, "period_start": start, "period_end": cutoff,
                     "graph_share": "0.5", "allocation_policy": "owner graph share",
                     "department_weights": {"trader": "1"}, "department_allocation_label": "owner weights",
                     "evidence_ref": "owner-evidence:late-plan"})
    record(runtime, {"record_type": "completeness", "request_id": "late-owner-completeness",
                     "period_start": start, "period_end": cutoff, "expense_kinds": ["subscription", "other"],
                     "evidence_ref": "owner-evidence:closed-period"})

    overview = live.overview(runtime, reporting_end=cutoff)
    owner = overview["owner_expenses"]
    result = overview["economic_result"]

    assert owner["coverage"]["status"] == "complete"
    assert len(owner["completeness_declarations"]) == 1
    assert owner["evidence_as_of"] == utc_iso(runtime.clock.now())
    assert owner["period"] == {"start_at": start, "end_at": cutoff}
    assert owner["graph_subscription_allocation"]["amount"] == "9"
    assert owner["full_bills"][0]["graph_allocation_valuation"]["fx"]["rate"] == "0.9"
    assert overview["as_of"] == owner["evidence_as_of"]
    assert overview["valuation"]["as_of"] == cutoff
    assert result["known_api_ai_expense"] == "0.9"
    assert result["allocated_ai_expense"] == "9.9"
    assert result["net_result"] == "-9.9"
    assert result["status"] == "not_covered"


def test_nonzero_unallocated_api_receipt_does_not_establish_cost_coverage(tmp_path):
    from trade_graph.api import financial, live

    runtime = _live_runtime(tmp_path)
    _receipt(runtime)
    _complete_owner_coverage(runtime)
    costs = financial._cost_state(runtime)
    assert costs["actual"] == Decimal("0.9")
    assert costs["receipts"][0]["allocation_basis"] == "unallocated deployment expense"

    result = live.overview(runtime)["economic_result"]

    assert result["known_api_ai_expense"] == "0"
    assert result["allocated_ai_expense"] is None
    assert result["net_result"] is None
    assert result["status"] == "unknown"
    assert "API receipt or allocation coverage remains incomplete." in result["unknown_reasons"]


def test_deterministically_zero_api_receipt_does_not_require_account_allocation(tmp_path):
    from trade_graph.api import financial, live

    runtime = _live_runtime(tmp_path)
    _receipt(runtime, tokens=0)
    _complete_owner_coverage(runtime)
    costs = financial._cost_state(runtime)
    assert costs["actual"] == Decimal("0")
    assert costs["receipts"][0]["allocation_basis"] == "unallocated deployment expense"

    result = live.overview(runtime)["economic_result"]

    assert result["known_api_ai_expense"] == "0"
    assert result["allocated_ai_expense"] == "0"
    assert result["net_result"] == "0"
    assert result["status"] == "covered"
