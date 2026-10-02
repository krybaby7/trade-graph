"""Actual dated work cannot be averaged away to create bounded independent gains."""

from decimal import Decimal

from tests.integration.test_forward_evaluation import expense, observation, registry, seal

from trade_graph.evaluation_contracts import ExpenseAllocation, ExpenseEvidence


def test_first_block_expense_violates_bounds_even_with_profitable_total_and_small_drawdown(tmp_path):
    instance, clock, declared = registry(tmp_path)
    for index, block in enumerate(declared.forward_blocks):
        clock.advance((block.end - clock.now()).total_seconds())
        if index == 0:
            instance.record_expense(ExpenseEvidence(
                receipt_id="first-block-work", source_ref="fixture:actual-work", conversion_ref="fixture:EUR-identity",
                incurred_at=block.end, amount_eur="40", evidence_kind="actual", cost_class="recurring",
                outcome="succeeded",
            ))
            instance.allocate_expense(declared.trial_id, ExpenseAllocation(
                receipt_id="first-block-work", arm="agent", weight="1", source_ref="fixture:full-attribution",
            ))
        instance.observe(declared.trial_id, observation(declared, index))
    seal(instance, declared)
    report = instance.report(declared.trial_id)
    assert report["financial_metrics"]["agent"]["all_in_net_economic_eur"] == "40"
    assert Decimal(report["financial_metrics"]["agent"]["sampled_maximum_drawdown_fraction"]) < Decimal("0.20")
    assert report["verdict"] == "insufficient_evidence"
    comparison = report["baseline_comparisons"]["cash"]
    assert comparison["net_economic_excess_by_block_eur"][0] == "-38"
    assert comparison["mean_block_net_economic_excess_eur"] == "1"
    assert comparison["predeclared_bounds_respected"] is False and comparison["lower_eur"] is None
    assert "cash_predeclared_uncertainty_bounds_violated" in report["reasons"]


def test_historical_fixed_setup_shift_preserves_all_in_mean_and_valid_bounds(tmp_path):
    instance, clock, declared = registry(tmp_path)
    expense(instance, declared, "historical-fixed", "40", cost_class="setup_engineering", outcome="failed")
    for index, block in enumerate(declared.forward_blocks):
        clock.advance((block.end - clock.now()).total_seconds())
        instance.observe(declared.trial_id, observation(declared, index))
    seal(instance, declared)
    report = instance.report(declared.trial_id)
    comparison = report["baseline_comparisons"]["cash"]
    assert comparison["historical_fixed_setup_shift_per_block_eur"] == "1"
    assert set(comparison["net_economic_excess_by_block_eur"]) == {"1"}
    assert Decimal(comparison["lower_eur"]) > 0
    assert report["financial_metrics"]["agent"]["all_in_net_economic_eur"] == "40"
    assert report["verdict"] == "supported"


def test_historical_recurring_costs_remain_counted_without_invented_block_allocation(tmp_path):
    instance, clock, declared = registry(tmp_path)
    expense(instance, declared, "historical-recurring", "40")
    for index, block in enumerate(declared.forward_blocks):
        clock.advance((block.end - clock.now()).total_seconds())
        instance.observe(declared.trial_id, observation(declared, index))
    seal(instance, declared)
    report = instance.report(declared.trial_id)
    assert report["financial_metrics"]["agent"]["all_in_net_economic_eur"] == "40"
    assert report["verdict"] == "insufficient_evidence"
    assert "allocated_cost_timing_unestablished" in report["reasons"]
    comparison = report["baseline_comparisons"]["cash"]
    assert comparison["mean_block_net_economic_excess_eur"] == "1"
    assert comparison["cost_timing_established"] is False
    assert comparison["lower_eur"] is None and comparison["predeclared_bounds_respected"] is None
