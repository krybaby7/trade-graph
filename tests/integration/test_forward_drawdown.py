"""Global sampled high-water marks survive block boundaries and external costs."""

from datetime import timedelta
from decimal import Decimal

import pytest
from tests.integration.test_forward_evaluation import SHA, expense, observation, populate, protocol, registry, seal

from trade_graph.evaluation_contracts import CostInventory, ExpenseAllocation, ExpenseEvidence, ForwardObservation


def test_continuous_equity_path_drawdown_can_exceed_every_within_block_drawdown(tmp_path):
    declared = protocol(count=40, block_excess_lower_eur="-15", block_excess_upper_eur="60")
    instance, clock, _ = registry(tmp_path, declared)
    closing = {arm: declared.capital_eur for arm in ("agent", "cash", "buy_and_hold", "deterministic")}
    for index, block in enumerate(declared.forward_blocks):
        gain = Decimal("-15") if index < 20 else Decimal("60")
        payload = observation(declared, index, agent_gain=gain).model_dump()
        for arm in payload["arms"]:
            arm["opening_equity_eur"] = closing[arm["arm"]]
            arm["closing_equity_eur"] = closing[arm["arm"]] + (gain if arm["arm"] == "agent" else 0)
            # No block loses more than 3% from its own opening mark.
            arm["maximum_drawdown_fraction"] = Decimal("0.03") if arm["arm"] == "agent" else 0
            closing[arm["arm"]] = arm["closing_equity_eur"]
        clock.advance((block.end - clock.now()).total_seconds())
        instance.observe(declared.trial_id, ForwardObservation.model_validate(payload))
    seal(instance, declared)
    report = instance.report(declared.trial_id)
    # The complete observed path goes 1000 -> 700 -> 1900 with no external
    # flows. Its 30% drawdown violates the preregistered 20% risk limit.
    assert report["verdict"] == "not_supported"
    assert Decimal(report["financial_metrics"]["agent"]["sampled_maximum_drawdown_fraction"]) >= Decimal("0.30")


@pytest.mark.parametrize("embedded", [False, True])
def test_actual_paid_costs_affect_the_same_all_in_drawdown_inside_or_outside_paper_equity(tmp_path, embedded):
    declared = protocol(count=4, block_excess_lower_eur="-300", block_excess_upper_eur="300")
    instance, clock, _ = registry(tmp_path, declared)
    closing = {arm: declared.capital_eur for arm in ("agent", "cash", "buy_and_hold", "deterministic")}
    for index, block in enumerate(declared.forward_blocks):
        clock.advance((block.end - clock.now()).total_seconds())
        if index == 0:
            instance.record_expense(ExpenseEvidence(
                receipt_id="cost", source_ref="fixture:actual-receipt", conversion_ref="fixture:EUR-identity",
                incurred_at=block.end, amount_eur="300", evidence_kind="actual",
                cost_class="recurring", outcome="failed", variant_sha256=SHA,
            ))
            instance.allocate_expense(declared.trial_id, ExpenseAllocation(
                receipt_id="cost", arm="agent", weight="1", source_ref="fixture:full-attribution",
            ))
        included = Decimal("300") if embedded and index == 0 else 0
        payload = observation(declared, index, embedded=Decimal(included)).model_dump()
        for arm in payload["arms"]:
            arm["opening_equity_eur"] = closing[arm["arm"]]
            arm["closing_equity_eur"] = closing[arm["arm"]] + (
                Decimal("2") - included if arm["arm"] == "agent" else 0
            )
            closing[arm["arm"]] = arm["closing_equity_eur"]
        instance.observe(declared.trial_id, ForwardObservation.model_validate(payload))
    seal(instance, declared)
    report = instance.report(declared.trial_id)
    agent = report["financial_metrics"]["agent"]
    assert agent["trading_after_friction_eur"] == "8"
    assert agent["all_in_net_economic_eur"] == "-292"
    assert agent["sampled_trading_boundary_drawdown_fraction"] == "0"
    assert agent["sampled_all_in_boundary_drawdown_fraction"] == "0.298"
    assert report["verdict"] == "not_supported"
    assert report["sampled_drawdown"]["agent"]["samples"][1]["all_in_flow_adjusted_equity_eur"] == "702"


def test_deposit_and_withdrawal_neither_invent_profit_nor_prove_unknown_flow_timing_drawdown(tmp_path):
    declared = protocol(count=2, block_excess_lower_eur="-1", block_excess_upper_eur="3")
    instance, clock, _ = registry(tmp_path, declared)
    closing = {arm: declared.capital_eur for arm in ("agent", "cash", "buy_and_hold", "deterministic")}
    for index, block in enumerate(declared.forward_blocks):
        flow = Decimal("300") if index == 0 else Decimal("-300")
        payload = observation(declared, index, agent_gain=Decimal("0"), flows=flow).model_dump()
        for arm in payload["arms"]:
            arm["opening_equity_eur"] = closing[arm["arm"]]
            arm["closing_equity_eur"] = closing[arm["arm"]] + flow
            arm["maximum_drawdown_fraction"] = "0"
            closing[arm["arm"]] = arm["closing_equity_eur"]
        clock.advance((block.end - clock.now()).total_seconds())
        instance.observe(declared.trial_id, ForwardObservation.model_validate(payload))
    seal(instance, declared)
    report = instance.report(declared.trial_id)
    agent = report["financial_metrics"]["agent"]
    assert agent["trading_after_friction_eur"] == "0" and agent["sampled_maximum_drawdown_fraction"] == "0"
    assert report["verdict"] == "insufficient_evidence"
    assert "drawdown_external_flow_timing_unavailable" in report["reasons"]
    assert report["sampled_drawdown"]["agent"]["external_flow_timing_available"] is False


def test_pre_horizon_failed_setup_costs_retain_initial_capital_high_water(tmp_path):
    declared = protocol(maximum_drawdown_fraction="0.015")
    instance, clock, _ = registry(tmp_path, declared)
    expense(instance, declared, "historical-failed-setup", "20", cost_class="setup_engineering", outcome="failed")
    populate(instance, clock, declared)
    seal(instance, declared)
    report = instance.report(declared.trial_id)
    assert report["financial_metrics"]["agent"]["all_in_net_economic_eur"] == "60"
    assert Decimal(report["baseline_comparisons"]["cash"]["lower_eur"]) > 0
    assert report["financial_metrics"]["agent"]["sampled_maximum_drawdown_fraction"] == "0.02"
    assert report["verdict"] == "not_supported"


def test_late_attributed_actual_cost_uses_an_explicit_frozen_final_market_mark(tmp_path):
    declared = protocol(maximum_drawdown_fraction="0.04")
    instance, clock, _ = registry(tmp_path, declared)
    populate(instance, clock, declared)
    clock.advance(timedelta(days=1).total_seconds())
    instance.record_expense(ExpenseEvidence(
        receipt_id="late-review", source_ref="fixture:attributed-late-review", conversion_ref="fixture:EUR-identity",
        incurred_at=clock.now(), amount_eur="50", evidence_kind="actual", cost_class="setup_engineering",
        outcome="failed", variant_sha256=SHA,
    ))
    instance.allocate_expense(declared.trial_id, ExpenseAllocation(
        receipt_id="late-review", arm="agent", weight="1", source_ref="fixture:full-attribution",
    ))
    instance.seal_cost_inventory(declared.trial_id, CostInventory(
        source_ref="fixture:complete-post-review-inventory", ledger_export_sha256=SHA,
        source_cutoff=clock.now(), receipt_ids=("late-review",),
    ))
    report = instance.report(declared.trial_id)
    assert report["financial_metrics"]["agent"]["all_in_net_economic_eur"] == "30"
    assert report["verdict"] == "insufficient_evidence"
    assert "allocated_cost_timing_unestablished" in report["reasons"]
    last = report["sampled_drawdown"]["agent"]["samples"][-1]
    assert last["kind"] == "final_all_in_cost_attribution_at_last_market_mark"
    assert last["trading_flow_adjusted_equity_eur"] == "1080"
    assert last["all_in_flow_adjusted_equity_eur"] == "1030"
