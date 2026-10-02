"""Synthetic fixtures verify reporting math; they do not constitute a paid soak."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier

import pytest
from pydantic import ValidationError

from trade_graph.domain.clock import FrozenClock
from trade_graph.evaluation_contracts import (
    ARMS,
    ArmPerformance,
    Attempt,
    AttemptResult,
    Baseline,
    CostInventory,
    DecisionEvidence,
    ExpenseAllocation,
    ExpenseEvidence,
    ExpenseResolution,
    ForwardObservation,
    ForwardProtocol,
    Sensitivity,
    Window,
)
from trade_graph.evaluation_registry import TrialRegistry

SHA = "a" * 64
OTHER_SHA = "b" * 64
START = datetime(2026, 1, 2, tzinfo=UTC)
REGISTERED = datetime(2026, 1, 1, 12, tzinfo=UTC)


def protocol(*, count=40, trial_id="trial", family_id="family", maximum_family_trials=1, **changes):
    data = dict(
        trial_id=trial_id, family_id=family_id, hypothesis="fixed agent beats declared baselines after all expenses",
        portfolio_id="paper-portfolio", market_stream_id="public-market-stream",
        selected_version_sha256=SHA, allowed_versions_sha256=(SHA,),
        data_policy_sha256=SHA, friction_policy_sha256=SHA, regime_classifier_sha256=SHA,
        capital_eur="1000", development=Window(start=REGISTERED - timedelta(days=10),
                                                end=REGISTERED - timedelta(days=3)),
        validation=Window(start=REGISTERED - timedelta(days=2), end=REGISTERED - timedelta(hours=2)),
        forward_blocks=tuple(Window(start=START + timedelta(hours=i), end=START + timedelta(hours=i + 1))
                             for i in range(count)),
        purge_seconds=3600, minimum_decisions=count, required_regimes=("calm", "stress"),
        maximum_drawdown_fraction="0.2", maximum_turnover_eur="100000",
        net_economic_hurdle_eur="0", baseline_mean_excess_hurdle_eur="0",
        block_excess_lower_eur="-1", block_excess_upper_eur="3", uncertainty_alpha="0.05",
        maximum_family_trials=maximum_family_trials,
        sensitivities=(Sensitivity(name="friction/model/missed-fill stress", fee_multiplier="2",
                                  additional_slippage_bps="10", operating_cost_multiplier="2",
                                  additional_agent_model_cost_eur="1", missed_profitable_pnl_fraction="0.1"),),
        stop_review_conditions=("review if declared drawdown or turnover is exceeded",),
        cost_allocation_policy="all recurring and setup, including failed work; shared receipt weights fixed",
        independence_policy="predeclared block construction and dependence review with retained evidence",
        baselines=tuple(Baseline(arm=arm, artifact_sha256=SHA, policy="same data/friction/constraints/flows")
                        for arm in ARMS[1:]),
    )
    data.update(changes)
    return ForwardProtocol(**data)


def registry(tmp_path, declared=None):
    declared = declared or protocol()
    clock = FrozenClock(REGISTERED)
    instance = TrialRegistry(tmp_path / "forward.sqlite", clock)
    instance.register(declared)
    return instance, clock, declared


def observation(declared, index, *, evidence_kind="forward_paper", agent_gain=Decimal("2"),
                embedded=Decimal("0"), flows=Decimal("0"), decisions=None):
    block = declared.forward_blocks[index]
    # EUR amounts below represent an imported collector fixture, not live/provider evidence.
    arms = []
    for arm in ARMS:
        raw_gain = agent_gain - embedded if arm == "agent" else Decimal("0")
        opening = declared.capital_eur + index * (raw_gain + flows)
        arms.append(ArmPerformance(
            arm=arm, opening_equity_eur=opening, closing_equity_eur=opening + raw_gain + flows,
            external_flows_eur=flows, embedded_operating_expenses_eur=embedded if arm == "agent" else "0",
            trading_fees_eur="0.2" if arm == "agent" else "0",
            measured_slippage_eur="0.1" if arm == "agent" else "0",
            turnover_eur="100" if arm == "agent" else "0", maximum_drawdown_fraction="0.01",
            mean_exposure_fraction="0.1" if arm == "agent" else "0", operational_errors=0,
            source_ref=f"fixture-ledger:{arm}:{index}",
        ))
    if decisions is None:
        decisions = (DecisionEvidence(
            decision_id=f"decision-{index}", window=block, latest_input_available_at=block.start,
            version_sha256=SHA, source_ref=f"fixture-decision:{index}", useful=True,
        ),)
    return ForwardObservation(
        block_index=index, window=block, evidence_kind=evidence_kind, available_at=block.end,
        data_policy_sha256=SHA, friction_policy_sha256=SHA, regime_classifier_sha256=SHA,
        regime="calm" if index % 2 == 0 else "stress", opportunities=max(2, len(decisions)),
        decisions=decisions, arms=tuple(arms), source_ref=f"fixture-observation:{index}",
        credentialed_soak_report_ref="fixture-only:collector-import-schema",
        observed_venue_conditions_ref="fixture-only:public-spread-observation",
        paper_venue_differences=("fixture: conservative paper fee exceeds observed public tier",),
        independence_status="assessed_independent", independence_assessment_ref="fixture-only:dependence-review",
    )


def populate(instance, clock, declared, **options):
    for i, block in enumerate(declared.forward_blocks):
        clock.advance((block.end - clock.now()).total_seconds())
        instance.observe(declared.trial_id, observation(declared, i, **options))


def expense(instance, declared, receipt_id, amount="2", *, cost_class="recurring", outcome="succeeded",
            evidence_kind="actual", arm="agent", weight="1"):
    instance.record_expense(ExpenseEvidence(
        receipt_id=receipt_id, source_ref=f"fixture-only:receipt:{receipt_id}",
        conversion_ref="fixture-only:EUR-identity", incurred_at=REGISTERED,
        amount_eur=amount, evidence_kind=evidence_kind, cost_class=cost_class, outcome=outcome,
        variant_sha256=OTHER_SHA if outcome == "failed" else SHA,
    ))
    instance.allocate_expense(declared.trial_id, ExpenseAllocation(
        receipt_id=receipt_id, arm=arm, weight=weight, source_ref="fixture-only:owner-defined-allocation",
    ))


def seal(instance, declared):
    instance.seal_cost_inventory(declared.trial_id, CostInventory(
        source_ref="fixture-only:complete-deployment-ledger-export", ledger_export_sha256=SHA,
        source_cutoff=declared.forward_blocks[-1].end,
        receipt_ids=tuple(sorted({item.receipt_id for item in instance.allocations(declared.trial_id)})),
    ))


def test_positive_synthetic_fixture_never_supports_forward_economics(tmp_path):
    instance, clock, declared = registry(tmp_path)
    populate(instance, clock, declared, evidence_kind="synthetic")
    seal(instance, declared)
    result = instance.report(declared.trial_id)
    assert result["financial_metrics"]["agent"]["trading_after_friction_eur"] == "80"
    assert result["verdict"] == "insufficient_evidence"
    assert "synthetic_observations_are_not_forward_evidence" in result["reasons"]
    assert result["evidence_status"] == "pending_or_synthetic"
    assert result["live_authorization"] is False
    assert result["broader_engineer_authorization"] is False


def test_imported_fixture_math_includes_failed_and_external_costs_without_double_deduction(tmp_path):
    instance, clock, declared = registry(tmp_path)
    instance.start_attempt(declared.trial_id, Attempt(attempt_id="bad-variant", variant_sha256=OTHER_SHA,
                                                    objective="attempt before selecting the fixed version"))
    expense(instance, declared, "recurring", "2")
    expense(instance, declared, "failed", "3", cost_class="setup_engineering", outcome="failed")
    instance.finish_attempt(declared.trial_id, AttemptResult(attempt_id="bad-variant", outcome="failed",
                                                           source_ref="fixture:failed-tests", receipt_ids=("failed",)))
    populate(instance, clock, declared)
    seal(instance, declared)
    result = instance.report(declared.trial_id)
    agent = result["financial_metrics"]["agent"]
    assert agent["trading_after_friction_eur"] == "80"  # fees and measured slippage already in equity
    assert agent["recurring_net_economic_eur"] == "78"
    assert agent["all_in_net_economic_eur"] == "75"
    assert agent["actual_failed_work_expenses_eur"] == "3"
    assert agent["fees_already_in_equity_eur"] == "8"
    assert agent["slippage_already_in_equity_eur"] == "4"
    assert result["sample"]["actual_agent_cost_per_useful_decision_eur"] == "0.125"
    assert result["sample"]["opportunity_coverage"] == "0.5"
    assert result["trial_registry"]["attempt_results"][0]["outcome"] == "failed"
    assert Decimal(result["baseline_comparisons"]["cash"]["lower_eur"]) > 0
    # Mathematical branch only: fixture references above do not certify real paid-provider evidence.
    assert result["verdict"] == "supported"
    stressed = result["sensitivity"][0]["arms"]["agent"]
    assert stressed["all_in_net_economic_eur"] == "49"  # 75 - 8 fees - 4 slippage - 8 missed - 5 cost - 1 model
    assert stressed["additional_model_cost_eur"] == "1"
    assert all(isinstance(item["lower_eur"], str) for item in result["baseline_comparisons"].values())


def test_embedded_expenses_and_equal_external_flows_are_counted_once(tmp_path):
    instance, clock, declared = registry(tmp_path, protocol(count=4))
    expense(instance, declared, "inside-portfolio", "4")
    populate(instance, clock, declared, embedded=Decimal("1"), flows=Decimal("100"))
    seal(instance, declared)
    report = instance.report(declared.trial_id)
    assert report["financial_metrics"]["agent"]["trading_after_friction_eur"] == "8"
    assert report["financial_metrics"]["agent"]["all_in_net_economic_eur"] == "4"
    assert report["financial_metrics"]["cash"]["trading_after_friction_eur"] == "0"


@pytest.mark.parametrize(
    "gain, expected", [(Decimal("-0.5"), "not_supported"), (Decimal("0.5"), "insufficient_evidence")],
)
def test_negative_or_uncertain_imported_fixture_results(tmp_path, gain, expected):
    instance, clock, declared = registry(tmp_path, protocol(count=4))
    populate(instance, clock, declared, agent_gain=gain)
    seal(instance, declared)
    report = instance.report(declared.trial_id)
    assert report["verdict"] == expected
    if expected == "insufficient_evidence":
        assert "uncertainty_interval_crosses_predeclared_baseline_hurdle" in report["reasons"]


def test_pending_horizon_inventory_and_regimes_prevent_support(tmp_path):
    instance, _, declared = registry(tmp_path)
    report = instance.report(declared.trial_id)
    assert report["verdict"] == "insufficient_evidence"
    assert set(report["reasons"]) >= {"forward_horizon_incomplete", "fixed_forward_blocks_missing",
                                      "complete_authoritative_cost_inventory_pending",
                                      "predeclared_regime_coverage_missing"}
    assert report["sample"]["blocks_predeclared"] == 40


def test_synthetic_receipt_is_separate_from_actual_spend_and_prevents_support(tmp_path):
    instance, clock, declared = registry(tmp_path)
    expense(instance, declared, "simulated", "500", evidence_kind="synthetic")
    populate(instance, clock, declared)
    seal(instance, declared)
    report = instance.report(declared.trial_id)
    assert report["verdict"] == "insufficient_evidence"
    assert report["financial_metrics"]["agent"]["actual_recurring_expenses_eur"] == "0"
    assert report["financial_metrics"]["agent"]["all_in_net_economic_eur"] == "80"
    assert report["financial_metrics"]["agent"]["hypothetical_after_modeled_expenses_eur"] == "-420"


def test_unknown_usage_is_not_free_and_reconciliation_retains_original(tmp_path):
    instance, clock, declared = registry(tmp_path)
    expense(instance, declared, "unknown", None, outcome="unresolved")
    populate(instance, clock, declared)
    pending = instance.report(declared.trial_id)
    assert "usage_or_invoice_cost_unresolved" in pending["reasons"]
    assert pending["cost_inventory"]["unresolved_receipt_ids"] == ["unknown"]
    instance.resolve_expense(ExpenseResolution(receipt_id="unknown", amount_eur="7", outcome="failed",
                                              source_ref="fixture:invoice-reconciliation", conversion_ref="fixture:FX"))
    seal(instance, declared)
    report = instance.report(declared.trial_id)
    assert report["financial_metrics"]["agent"]["all_in_net_economic_eur"] == "73"
    assert report["cost_inventory"]["original_receipts"][0]["amount_eur"] is None
    assert report["cost_inventory"]["reconciliations"][0]["amount_eur"] == "7"
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        instance.resolve_expense(ExpenseResolution(receipt_id="unknown", amount_eur="6", outcome="failed",
                                                  source_ref="replacement", conversion_ref="FX"))


def test_failed_attempt_cost_omission_cannot_be_sealed(tmp_path):
    instance, clock, declared = registry(tmp_path)
    instance.start_attempt(declared.trial_id, Attempt(attempt_id="failed", variant_sha256=OTHER_SHA, objective="test"))
    instance.record_expense(ExpenseEvidence(receipt_id="omitted", source_ref="fixture:failure", conversion_ref="FX",
                                           incurred_at=REGISTERED, amount_eur="4", evidence_kind="actual",
                                           cost_class="setup_engineering", outcome="failed"))
    instance.finish_attempt(declared.trial_id, AttemptResult(attempt_id="failed", outcome="rejected",
                                                           source_ref="test-failure", receipt_ids=("omitted",)))
    populate(instance, clock, declared)
    with pytest.raises(ValueError, match="cannot be omitted"):
        seal(instance, declared)


def test_receipt_allocations_are_exact_once_across_trials_and_reopen(tmp_path):
    first = protocol(maximum_family_trials=2)
    instance, clock, _ = registry(tmp_path, first)
    second = protocol(trial_id="second", maximum_family_trials=2)
    instance.register(second)
    expense(instance, first, "shared", "10", weight="0.999999999999999999")
    instance.allocate_expense(second.trial_id, ExpenseAllocation(receipt_id="shared", arm="agent",
                                                                weight="0.000000000000000001", source_ref="shared"))
    with pytest.raises(ValueError, match="more than once"):
        instance.allocate_expense(second.trial_id, ExpenseAllocation(receipt_id="shared", arm="cash",
                                                                    weight="0.000000000000000001", source_ref="bad"))
    instance.close()
    reopened = TrialRegistry(tmp_path / "forward.sqlite", clock)
    assert len(reopened.allocations(second.trial_id)) == 1
    assert reopened.protocol(first.trial_id) == first


def test_concurrent_shared_allocations_cannot_overspend_receipt(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    first = protocol(maximum_family_trials=2)
    instance, _, _ = registry(tmp_path, first)
    second = protocol(trial_id="second", maximum_family_trials=2)
    instance.register(second)
    instance.record_expense(ExpenseEvidence(receipt_id="shared", source_ref="receipt", conversion_ref="FX",
                                           incurred_at=REGISTERED, amount_eur="10", evidence_kind="actual",
                                           cost_class="recurring", outcome="succeeded"))
    barrier = Barrier(2)

    def allocate(trial_id):
        local = TrialRegistry(tmp_path / "forward.sqlite", FrozenClock(REGISTERED))
        barrier.wait(timeout=10)
        try:
            local.allocate_expense(trial_id, ExpenseAllocation(receipt_id="shared", arm="agent",
                                                               weight="0.7", source_ref="allocation"))
            return "accepted"
        except ValueError:
            return "rejected"
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(allocate, (first.trial_id, second.trial_id)))
    assert sorted(results) == ["accepted", "rejected"]


def test_protocol_is_immutable_and_cannot_be_registered_after_forward_start(tmp_path):
    declared = protocol()
    with pytest.raises(ValidationError):
        declared.net_economic_hurdle_eur = Decimal("-100")
    instance = TrialRegistry(tmp_path / "forward.sqlite", FrozenClock(START))
    with pytest.raises(ValueError, match="strictly before"):
        instance.register(declared)
    with pytest.raises(ValidationError, match="purged"):
        protocol(validation=Window(start=REGISTERED - timedelta(days=4), end=REGISTERED - timedelta(hours=2)))


def test_family_multiplicity_cannot_be_revised_or_hidden(tmp_path):
    declared = protocol(maximum_family_trials=2)
    instance, _, _ = registry(tmp_path, declared)
    with pytest.raises(ValueError, match="cannot change"):
        instance.register(protocol(trial_id="second", maximum_family_trials=3))
    instance.register(protocol(trial_id="second", maximum_family_trials=2))
    with pytest.raises(ValueError, match="exhausted"):
        instance.register(protocol(trial_id="third", maximum_family_trials=2))
    assert sorted(instance.report(declared.trial_id)["trial_registry"]["family_trials"]) == ["second", "trial"]


def test_future_inputs_changed_versions_and_unmatched_baselines_fail_locally(tmp_path):
    instance, clock, declared = registry(tmp_path)
    value = observation(declared, 0)
    with pytest.raises(ValueError, match="future"):
        instance.observe(declared.trial_id, value)
    bad_decision = value.decisions[0].model_dump()
    bad_decision["latest_input_available_at"] = value.window.start + timedelta(seconds=1)
    with pytest.raises(ValidationError, match="after the decision"):
        DecisionEvidence.model_validate(bad_decision)
    clock.advance((value.window.end - clock.now()).total_seconds())
    bad = value.model_dump()
    bad["data_policy_sha256"] = OTHER_SHA
    with pytest.raises(ValueError, match="assumptions changed"):
        instance.observe(declared.trial_id, ForwardObservation.model_validate(bad))
    bad = value.model_dump()
    bad["decisions"][0]["version_sha256"] = OTHER_SHA
    with pytest.raises(ValueError, match="unregistered"):
        instance.observe(declared.trial_id, ForwardObservation.model_validate(bad))
    bad = value.model_dump()
    bad["arms"][1]["external_flows_eur"] = "1"
    with pytest.raises(ValidationError, match="same capital-flow path"):
        ForwardObservation.model_validate(bad)


def test_missing_duplicate_and_discontinuous_blocks_fail(tmp_path):
    instance, clock, declared = registry(tmp_path)
    value = observation(declared, 0)
    clock.advance((declared.forward_blocks[1].end - clock.now()).total_seconds())
    with pytest.raises(ValueError, match="chronological"):
        instance.observe(declared.trial_id, observation(declared, 1))
    instance.observe(declared.trial_id, value)
    second = observation(declared, 1).model_dump()
    second["decisions"][0]["decision_id"] = "decision-0"
    with pytest.raises(ValueError, match="reused"):
        instance.observe(declared.trial_id, ForwardObservation.model_validate(second))
    second = observation(declared, 1).model_dump()
    second["arms"][0]["opening_equity_eur"] = "1000"
    with pytest.raises(ValueError, match="preceding closing"):
        instance.observe(declared.trial_id, ForwardObservation.model_validate(second))


def test_overlap_does_not_inflate_nonoverlapping_outcome_sample(tmp_path):
    declared = protocol(count=2, minimum_decisions=4)
    instance, clock, _ = registry(tmp_path, declared)
    for index, block in enumerate(declared.forward_blocks):
        decisions = tuple(DecisionEvidence(decision_id=f"{index}:{i}", window=block,
                                          latest_input_available_at=block.start, version_sha256=SHA,
                                          source_ref="overlapping-outcome", useful=True) for i in range(4))
        clock.advance((block.end - clock.now()).total_seconds())
        instance.observe(declared.trial_id, observation(declared, index, decisions=decisions))
    seal(instance, declared)
    result = instance.report(declared.trial_id)
    assert result["sample"]["decisions"] == 8
    assert result["sample"]["nonoverlapping_outcome_horizons"] == 2
    assert result["verdict"] == "insufficient_evidence"


def test_out_of_bounds_population_does_not_receive_valid_uncertainty_interval(tmp_path):
    instance, clock, declared = registry(tmp_path, protocol(count=2))
    populate(instance, clock, declared, agent_gain=Decimal("10"))
    seal(instance, declared)
    result = instance.report(declared.trial_id)
    assert result["verdict"] == "insufficient_evidence"
    assert result["baseline_comparisons"]["cash"]["lower_eur"] is None
    assert "cash_predeclared_uncertainty_bounds_violated" in result["reasons"]


def test_nonoverlap_does_not_override_dependence_or_missing_assessment(tmp_path):
    instance, clock, declared = registry(tmp_path)
    for index, block in enumerate(declared.forward_blocks):
        clock.advance((block.end - clock.now()).total_seconds())
        data = observation(declared, index).model_dump()
        data["independence_status"] = "dependent" if index % 2 == 0 else "unassessed"
        instance.observe(declared.trial_id, ForwardObservation.model_validate(data))
    seal(instance, declared)
    report = instance.report(declared.trial_id)
    assert report["sample"]["nonoverlapping_outcome_horizons"] == 40
    assert report["verdict"] == "insufficient_evidence"
    assert "independent_block_assessment_missing_or_dependent" in report["reasons"]


def test_append_only_storage_and_sealing_preserve_evidence(tmp_path):
    import sqlite3

    instance, clock, declared = registry(tmp_path, protocol(count=2))
    populate(instance, clock, declared, evidence_kind="synthetic")
    seal(instance, declared)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        instance.connection.execute("DELETE FROM evaluation_records")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        instance.connection.execute("UPDATE evaluation_records SET document_json = '{}' WHERE kind = 'protocol'")
    with pytest.raises(ValueError, match="sealed"):
        instance.start_attempt(
            declared.trial_id, Attempt(attempt_id="late", variant_sha256=OTHER_SHA, objective="test"),
        )
    assert instance.report(declared.trial_id)["sample"]["blocks_observed"] == 2


@pytest.mark.parametrize("amount", [0.1, "NaN", "Infinity", True, "0.0000000000000000001", "0e-999999999"])
def test_financial_inputs_reject_floats_nonfinite_and_excess_precision(amount):
    with pytest.raises(ValidationError):
        protocol(capital_eur=amount)


def test_legacy_offline_summary_never_asserts_forward_support():
    from trade_graph.evaluation import evaluate_forward

    result = evaluate_forward(independent_decisions=10000, minimum_decisions=30,
                              net_economic=Decimal("100000"), predeclared_hurdle=Decimal("0"), costs_included=True)
    assert result["verdict"] == "insufficient_evidence"
    assert result["mechanical_hurdle_met"] is True
