"""Subscription receipt uncertainty cannot become resolved economic performance."""

from datetime import timedelta
from decimal import Decimal

from tests.integration.test_dashboard_financial import _receipt, _runtime
from tests.test_dashboard_pages import render
from tests.unit.test_subscription_adapter import request

from trade_graph.adapters.models.subscription import SubscriptionJournal
from trade_graph.api import financial
from trade_graph.contracts.models import ModelResult, ModelUsage
from trade_graph.domain.clock import utc_iso


def subscription_record(runtime, *, blocked=False, synthetic=False):
    journal = SubscriptionJournal(runtime.database, runtime.clock)
    journal.begin("subscription-fixture", request(), "claude_subscription", {})
    result = ModelResult(ok=False, failure="credentials", message="synthetic unavailable login") if blocked else \
        ModelResult(ok=True, payload={"note": "synthetic public evidence"}, provider_model="claude-sonnet-5-5",
                    usage=ModelUsage(uncached_input_tokens=7, billed_output_tokens=9))
    journal.save("subscription-fixture", result, "BLOCKED" if blocked else "COMPLETED", dispatched=not blocked)
    if synthetic:
        runtime.database.execute("UPDATE subscription_invocations SET synthetic=1")


def test_unknown_subscription_dispatch_nulls_economics_and_preserves_known_cost_budget_and_capital(tmp_path):
    runtime = _runtime(tmp_path, capital="10000", currency="USD")
    receipt = _receipt(runtime)
    runtime.budget.allocate(receipt, {runtime.portfolio_id: Decimal("1")})
    before = financial.overview(runtime)
    allowance = runtime.budget.remaining("deployment")
    subscription_record(runtime)
    current = financial.overview(runtime)
    costs = financial.costs(runtime)
    assert current["provisional"] and current["performance"]["provisional"]
    assert current["performance"]["net_economic_pnl"] is None
    assert current["performance"]["trading_pnl"] == before["performance"]["trading_pnl"] == "0"
    assert current["equity"] == before["equity"] == "9000"
    assert current["actual_spend"] == costs["actual_spend"] == "0.9"
    assert costs["allocated_actual_spend"] == "0.9"
    assert runtime.budget.remaining("deployment") == allowance == Decimal("9.1")
    assert current["cost_views"]["all_in"] is None
    assert costs["subscription"]["unknown_inference_costs"] == 1
    assert costs["subscription"]["shared_fee_allocation_status"] == "unknown"
    assert len(costs["receipts"]) == len(costs["subscription"]["records"]) == 1
    assert costs["subscription"]["records"][0]["actual_cost_native"] is None
    assert runtime.database.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 1


def test_blocked_subscription_has_no_inference_charge_but_shared_fee_remains_unknown(tmp_path):
    runtime = _runtime(tmp_path)
    subscription_record(runtime, blocked=True)
    costs = financial.costs(runtime)
    assert costs["subscription"]["unknown_inference_costs"] == 0
    record = costs["subscription"]["records"][0]
    assert record["inference_dispatched"] is False and record["cost_status"] == "not_incurred"
    assert costs["actual_spend"] == "0" and costs["remaining_allowance"] == "10"
    assert costs["subscription"]["shared_fee_allocation_status"] == "unknown"
    assert costs["provisional"] and financial.overview(runtime)["performance"]["net_economic_pnl"] is None


def test_declared_subscription_shared_fee_is_not_reported_as_resolved_zero(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.subscription_provider = "codex_subscription"
    costs = financial.costs(runtime)
    assert costs["subscription"]["records"] == [] and costs["subscription"]["unknown_inference_costs"] == 0
    assert costs["subscription"]["shared_fee_allocation_status"] == "unknown"
    assert costs["provisional"] and financial.overview(runtime)["performance"]["net_economic_pnl"] is None
    assert runtime.budget.remaining("deployment") == Decimal("10")


def test_synthetic_subscription_receipt_does_not_create_real_expense_uncertainty(tmp_path):
    runtime = _runtime(tmp_path)
    subscription_record(runtime, synthetic=True)
    costs = financial.costs(runtime)
    assert not costs["provisional"]
    assert costs["subscription"]["unknown_inference_costs"] == 0
    assert costs["subscription"]["shared_fee_allocation_status"] == "not_recorded"
    assert financial.overview(runtime)["performance"]["net_economic_pnl"] == "0"
    assert costs["subscription"]["records"][0]["synthetic"] is True


def test_future_subscription_receipt_is_not_part_of_current_economic_evidence(tmp_path):
    runtime = _runtime(tmp_path)
    subscription_record(runtime)
    future = utc_iso(runtime.clock.now() + timedelta(seconds=60))
    runtime.database.execute("UPDATE subscription_invocations SET created_at=?,updated_at=?", (future, future))
    assert not financial.costs(runtime)["provisional"]
    runtime.clock.advance(60)
    assert financial.costs(runtime)["provisional"]


def test_costs_template_keeps_subscription_unknowns_separate_from_recorded_api_expenses(tmp_path):
    runtime = _runtime(tmp_path)
    subscription_record(runtime, blocked=True)
    page = render("costs.html", financial.costs(runtime), operating_mode="paper")
    assert "Subscription usage and shared fees" in page
    assert "not_incurred" in page and "unknown" in page
    assert "Recorded actual operating spend" in page
    assert "Shared subscription fees are not recorded as zero" in page
    assert "claude_subscription" in page
