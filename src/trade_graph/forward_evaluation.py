"""Decimal forward evidence reports, conditional uncertainty and friction stresses.

This collector assesses imported evidence. Its verdict never grants paid work,
live trading, executable Engineer artifacts, or changes to a protected gate.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, localcontext

from trade_graph.domain.clock import utc_iso
from trade_graph.domain.money import canonical_decimal
from trade_graph.evaluation_contracts import (
    ARMS,
    Attempt,
    AttemptResult,
    ExpenseAllocation,
    ExpenseEvidence,
    ForwardObservation,
    ForwardProtocol,
)

ZERO = Decimal("0")


def _sum(values) -> Decimal:
    return sum(values, start=ZERO)


def _independent_horizons(observations: list[ForwardObservation]) -> int:
    """Count nonoverlapping outcomes; this does not prove statistical independence."""
    windows = sorted((decision.window for block in observations for decision in block.decisions),
                     key=lambda item: (item.end, item.start))
    last_end = None
    count = 0
    for window in windows:
        if last_end is None or window.start >= last_end:
            count += 1
            last_end = window.end
    return count


def _stringify(value):
    if isinstance(value, Decimal):
        return canonical_decimal(value)
    if isinstance(value, dict):
        return {key: _stringify(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_stringify(item) for item in value]
    return value


def build_forward_report(
    *, protocol: ForwardProtocol, as_of: datetime, registration: dict,
    observations: list[ForwardObservation], expenses: list[ExpenseEvidence],
    allocations: list[ExpenseAllocation], attempts: list[dict], attempt_results: list[dict],
    inventories: list[dict], trials: list[dict],
    expense_history: list[dict], expense_resolutions: list[dict],
) -> dict:
    """Require completed, fixed, actual forward evidence before assessing support.

    The probability interval is a Bonferroni-adjusted Hoeffding bound. It is
    conditional on independent blocks and the predeclared bounded population,
    assumptions which market observations cannot prove. Correlated/regime-shifted
    data can invalidate the interpretation; report this assumption explicitly.
    """
    with localcontext() as context:
        context.prec = 100
        return _stringify(_build(
            protocol, as_of, registration, observations, expenses, allocations,
            attempts, attempt_results, inventories, trials, expense_history, expense_resolutions,
        ))


def _build(protocol, as_of, registration, observations, expenses, allocations,
           attempts, attempt_results, inventories, trials, expense_history, expense_resolutions):
    reasons = []
    if as_of < protocol.forward_blocks[-1].end:
        reasons.append("forward_horizon_incomplete")
    if len(observations) != len(protocol.forward_blocks):
        reasons.append("fixed_forward_blocks_missing")
    if any(block.evidence_kind != "forward_paper" for block in observations):
        reasons.append("synthetic_observations_are_not_forward_evidence")
    if any(not block.credentialed_soak_report_ref for block in observations):
        reasons.append("credentialed_paper_soak_provenance_missing")
    if any(not block.observed_venue_conditions_ref or not block.paper_venue_differences for block in observations):
        reasons.append("observed_venue_comparison_missing")
    if any(block.independence_status != "assessed_independent" or not block.independence_assessment_ref
           for block in observations):
        reasons.append("independent_block_assessment_missing_or_dependent")
    if not inventories:
        reasons.append("complete_authoritative_cost_inventory_pending")

    declared_attempts = [Attempt.model_validate_json(row["document_json"]) for row in attempts]
    outcomes = [AttemptResult.model_validate_json(row["document_json"]) for row in attempt_results]
    if {item.attempt_id for item in declared_attempts} != {item.attempt_id for item in outcomes}:
        reasons.append("unfinished_variant_attempts")

    independent = _independent_horizons(observations)
    decisions = sum(len(block.decisions) for block in observations)
    opportunities = sum(block.opportunities for block in observations)
    useful = sum(decision.useful for block in observations for decision in block.decisions)
    if independent < protocol.minimum_decisions:
        reasons.append("too_few_nonoverlapping_decision_outcomes")
    regimes = sorted({block.regime for block in observations})
    if not set(protocol.required_regimes) <= set(regimes):
        reasons.append("predeclared_regime_coverage_missing")

    receipts = {item.receipt_id: item for item in expenses}
    costs = {
        arm: {kind: {view: ZERO for view in ("recurring", "setup_engineering", "failed_work")}
              for kind in ("actual", "synthetic")} for arm in ARMS
    }
    unresolved = []
    receipt_details = []
    for allocation in allocations:
        receipt = receipts[allocation.receipt_id]
        amount = None if receipt.amount_eur is None else receipt.amount_eur * allocation.weight
        receipt_details.append({
            "receipt": receipt.model_dump(mode="json"), "allocation": allocation.model_dump(mode="json"),
            "allocated_eur": amount,
        })
        if receipt.outcome == "unresolved":
            unresolved.append(receipt.receipt_id)
        if amount is not None:
            costs[allocation.arm][receipt.evidence_kind][receipt.cost_class] += amount
            if receipt.outcome == "failed":
                costs[allocation.arm][receipt.evidence_kind]["failed_work"] += amount
        if receipt.evidence_kind == "synthetic":
            reasons.append("synthetic_cost_receipts_are_not_actual_expense_evidence")
    if unresolved:
        reasons.append("usage_or_invoice_cost_unresolved")

    metrics = {}
    series = {}
    for arm in ARMS:
        slices = [next(item for item in block.arms if item.arm == arm) for block in observations]
        pnl = [item.trading_pnl for item in slices]
        actual_recurring = costs[arm]["actual"]["recurring"]
        actual_setup = costs[arm]["actual"]["setup_engineering"]
        modeled = costs[arm]["synthetic"]["recurring"] + costs[arm]["synthetic"]["setup_engineering"]
        total = actual_recurring + actual_setup + modeled
        embedded = _sum(item.embedded_operating_expenses_eur for item in slices)
        if embedded > total:
            reasons.append(f"{arm}_embedded_expense_missing_from_cost_inventory")
        series[arm] = pnl
        metrics[arm] = {
            "trading_after_friction_eur": _sum(pnl),
            "recurring_net_economic_eur": _sum(pnl) - actual_recurring,
            "all_in_net_economic_eur": _sum(pnl) - actual_recurring - actual_setup,
            "hypothetical_after_modeled_expenses_eur": _sum(pnl) - total,
            "actual_recurring_expenses_eur": actual_recurring,
            "actual_setup_engineering_expenses_eur": actual_setup,
            "actual_failed_work_expenses_eur": costs[arm]["actual"]["failed_work"],
            "synthetic_modeled_expenses_eur": modeled,
            "embedded_operating_expenses_eur": embedded,
            "fees_already_in_equity_eur": _sum(item.trading_fees_eur for item in slices),
            "slippage_already_in_equity_eur": _sum(item.measured_slippage_eur for item in slices),
            "turnover_eur": _sum(item.turnover_eur for item in slices),
            "sampled_maximum_drawdown_fraction": max((item.maximum_drawdown_fraction for item in slices),
                                                     default=ZERO),
            "mean_exposure_fraction": _sum(item.mean_exposure_fraction for item in slices) / len(slices)
                if slices else None,
            "operational_errors": sum(item.operational_errors for item in slices),
        }

    comparisons = {}
    count = len(observations)
    if count:
        correction = Decimal(2 * protocol.maximum_family_trials * (len(ARMS) - 1))
        width = protocol.block_excess_upper_eur - protocol.block_excess_lower_eur
        radius = width * ((correction / protocol.uncertainty_alpha).ln() / Decimal(2 * count)).sqrt()
        for arm in ARMS[1:]:
            agent_expenses = metrics["agent"]["actual_recurring_expenses_eur"] + \
                metrics["agent"]["actual_setup_engineering_expenses_eur"]
            baseline_expenses = metrics[arm]["actual_recurring_expenses_eur"] + \
                metrics[arm]["actual_setup_engineering_expenses_eur"]
            expense_shift = (agent_expenses - baseline_expenses) / count
            excess = [left - right - expense_shift for left, right in zip(series["agent"], series[arm])]
            valid = all(protocol.block_excess_lower_eur <= value <= protocol.block_excess_upper_eur
                        for value in excess)
            if not valid:
                reasons.append(f"{arm}_predeclared_uncertainty_bounds_violated")
            mean = _sum(excess) / count
            comparisons[arm] = {
                "mean_block_net_economic_excess_eur": mean,
                "lower_eur": mean - radius if valid else None,
                "upper_eur": mean + radius if valid else None,
                "radius_eur": radius if valid else None,
                "predeclared_bounds_respected": valid,
            }

    scenarios = []
    for case in protocol.sensitivities:
        scenario_arms = {}
        for arm in ARMS:
            base = metrics[arm]
            expense = base["actual_recurring_expenses_eur"] + base["actual_setup_engineering_expenses_eur"]
            additional_fees = base["fees_already_in_equity_eur"] * (case.fee_multiplier - 1)
            additional_slippage = base["turnover_eur"] * case.additional_slippage_bps / Decimal("10000")
            missed_profit = _sum(max(value, ZERO) for value in series[arm]) * case.missed_profitable_pnl_fraction
            additional_model = case.additional_agent_model_cost_eur if arm == "agent" else ZERO
            economic = (base["all_in_net_economic_eur"] - additional_fees - additional_slippage
                        - missed_profit - expense * (case.operating_cost_multiplier - 1) - additional_model)
            scenario_arms[arm] = {
                "all_in_net_economic_eur": economic,
                "additional_fees_eur": additional_fees,
                "additional_slippage_eur": additional_slippage,
                "missed_profitable_pnl_penalty_eur": missed_profit,
                "additional_model_cost_eur": additional_model,
            }
        scenarios.append({
            "definition": case.model_dump(mode="json"), "arms": scenario_arms,
            "agent_excess_eur": {arm: scenario_arms["agent"]["all_in_net_economic_eur"] -
                                  scenario_arms[arm]["all_in_net_economic_eur"] for arm in ARMS[1:]},
            "interpretation": "penalty envelope; missed fills are not replayed or observed executions",
        })

    verdict = "insufficient_evidence"
    if not reasons:
        agent = metrics["agent"]
        if (agent["all_in_net_economic_eur"] < protocol.net_economic_hurdle_eur
                or agent["sampled_maximum_drawdown_fraction"] > protocol.maximum_drawdown_fraction
                or agent["turnover_eur"] > protocol.maximum_turnover_eur
                or any(item["upper_eur"] < protocol.baseline_mean_excess_hurdle_eur
                       for item in comparisons.values())):
            verdict = "not_supported"
        elif all(item["lower_eur"] >= protocol.baseline_mean_excess_hurdle_eur for item in comparisons.values()):
            verdict = "supported"
        else:
            reasons.append("uncertainty_interval_crosses_predeclared_baseline_hurdle")

    trial_documents = [json.loads(row["document_json"]) for row in trials]
    return {
        "schema_version": 1, "trial_id": protocol.trial_id, "verdict": verdict,
        "evidence_status": "imported_forward_paper" if observations and not any(
            block.evidence_kind == "synthetic" for block in observations) else "pending_or_synthetic",
        "reasons": sorted(set(reasons)), "as_of_utc": utc_iso(as_of),
        "live_authorization": False, "broader_engineer_authorization": False,
        "pre_registration": {"collected_at": registration["collected_at"],
                             "document_sha256": registration["document_sha256"],
                             "protocol": protocol.model_dump(mode="json")},
        "sample": {
            "blocks_observed": count, "blocks_predeclared": len(protocol.forward_blocks),
            "decisions": decisions, "nonoverlapping_outcome_horizons": independent,
            "regimes": regimes, "opportunities": opportunities,
            "opportunity_coverage": Decimal(decisions) / opportunities if opportunities else None,
            "useful_decisions": useful,
            "actual_agent_cost_per_useful_decision_eur": (
                metrics["agent"]["actual_recurring_expenses_eur"] +
                metrics["agent"]["actual_setup_engineering_expenses_eur"]
            ) / useful if useful else None,
            "versions_observed": sorted({decision.version_sha256 for block in observations
                                         for decision in block.decisions}),
        },
        "financial_metrics": metrics, "baseline_comparisons": comparisons,
        "uncertainty": {
            "method": "two-sided Hoeffding with family/baseline Bonferroni correction",
            "alpha": protocol.uncertainty_alpha, "maximum_family_trials": protocol.maximum_family_trials,
            "assumptions": ["independent equal-duration blocks; nonoverlap alone does not establish independence",
                            "population paired net-economic excess remains inside predeclared bounds",
                            "fixed all-in expenses are spread equally over observed blocks for comparisons",
                            "market-regime change can invalidate the population interpretation"],
        },
        "sensitivity": scenarios,
        "cost_inventory": {
            "sealed": bool(inventories), "unresolved_receipt_ids": sorted(set(unresolved)),
            "imports": [json.loads(row["document_json"]) for row in inventories],
            "receipts": receipt_details,
            "original_receipts": [json.loads(row["document_json"]) for row in expense_history
                                  if json.loads(row["document_json"])["receipt_id"] in
                                  {item.receipt_id for item in allocations}],
            "reconciliations": [json.loads(row["document_json"]) for row in expense_resolutions
                                if json.loads(row["document_json"])["receipt_id"] in
                                {item.receipt_id for item in allocations}],
            "scope": "imported deployment-ledger evidence; upstream completeness requires collector verification",
        },
        "trial_registry": {
            "registered_trials": len(trials),
            "family_trials": [item["trial_id"] for item in trial_documents if item["family_id"] == protocol.family_id],
            "all_trials": [{"trial_id": item["trial_id"], "family_id": item["family_id"],
                            "selected_version_sha256": item["selected_version_sha256"]}
                           for item in trial_documents],
            "attempts": [item.model_dump(mode="json") for item in declared_attempts],
            "attempt_results": [item.model_dump(mode="json") for item in outcomes],
        },
        "venue_assumption_comparison": [{
            "block_index": block.block_index,
            "observed_venue_conditions_ref": block.observed_venue_conditions_ref,
            "paper_venue_differences": block.paper_venue_differences,
            "credentialed_soak_report_ref": block.credentialed_soak_report_ref,
            "independence_status": block.independence_status,
            "independence_assessment_ref": block.independence_assessment_ref,
            "source_ref": block.source_ref,
        } for block in observations],
    }
