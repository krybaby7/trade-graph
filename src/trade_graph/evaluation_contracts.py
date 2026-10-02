"""Immutable, provider-neutral contracts for predeclared forward-paper trials."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer, field_validator, model_validator

from trade_graph.domain.money import canonical_decimal, parse_decimal


def fixed_decimal(value):
    try:
        parsed = parse_decimal(value)
    except ArithmeticError as exc:
        raise ValueError("invalid fixed-point decimal") from exc
    if not -18 <= parsed.as_tuple().exponent <= 36:
        raise ValueError("evaluation amounts require bounded fixed-point precision")
    return parsed

Amount = Annotated[
    Decimal, BeforeValidator(fixed_decimal), Field(max_digits=36, decimal_places=18),
    PlainSerializer(canonical_decimal, return_type=str),
]
Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Reference = Annotated[str, Field(min_length=1, max_length=512)]
Arm = Literal["agent", "cash", "buy_and_hold", "deterministic"]
ARMS = ("agent", "cash", "buy_and_hold", "deterministic")


class EvaluationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("*", mode="before")
    @classmethod
    def reject_floats(cls, value):
        def check(item):
            if isinstance(item, float):
                raise ValueError("binary floats are not evaluation inputs")
            if isinstance(item, dict):
                for nested in item.values():
                    check(nested)
            elif isinstance(item, (tuple, list)):
                for nested in item:
                    check(nested)
        check(value)
        return value


class Window(EvaluationContract):
    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("evaluation timestamps require a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("evaluation windows must have positive duration")
        return self


class Baseline(EvaluationContract):
    arm: Literal["cash", "buy_and_hold", "deterministic"]
    artifact_sha256: Fingerprint
    policy: Reference


class Sensitivity(EvaluationContract):
    name: Reference
    fee_multiplier: Amount = Field(ge=1)
    additional_slippage_bps: Amount = Field(ge=0)
    operating_cost_multiplier: Amount = Field(ge=1)
    additional_agent_model_cost_eur: Amount = Field(ge=0)
    missed_profitable_pnl_fraction: Amount = Field(ge=0, le=1)


class ForwardProtocol(EvaluationContract):
    schema_version: Literal[1] = 1
    trial_id: Reference
    family_id: Reference
    hypothesis: Reference
    portfolio_id: Reference
    market_stream_id: Reference
    selected_version_sha256: Fingerprint
    allowed_versions_sha256: tuple[Fingerprint, ...]
    data_policy_sha256: Fingerprint
    friction_policy_sha256: Fingerprint
    regime_classifier_sha256: Fingerprint
    capital_eur: Amount = Field(gt=0)
    development: Window
    validation: Window
    forward_blocks: tuple[Window, ...]
    purge_seconds: int = Field(ge=0, strict=True)
    minimum_decisions: int = Field(gt=0, strict=True)
    required_regimes: tuple[Reference, ...]
    maximum_drawdown_fraction: Amount = Field(ge=0, le=1)
    maximum_turnover_eur: Amount = Field(ge=0)
    net_economic_hurdle_eur: Amount
    baseline_mean_excess_hurdle_eur: Amount
    # Bounds apply to every block's paired net-economic excess, including costs.
    block_excess_lower_eur: Amount
    block_excess_upper_eur: Amount
    uncertainty_alpha: Amount = Field(gt=0, lt=1)
    maximum_family_trials: int = Field(gt=0, strict=True)
    sensitivities: tuple[Sensitivity, ...]
    stop_review_conditions: tuple[Reference, ...]
    cost_allocation_policy: Reference
    independence_policy: Reference

    @model_validator(mode="after")
    def predeclared(self):
        from datetime import timedelta

        if len(self.forward_blocks) < 2:
            raise ValueError("at least two fixed forward blocks are required")
        if len(self.allowed_versions_sha256) != len(set(self.allowed_versions_sha256)):
            raise ValueError("allowed versions must be unique")
        if self.selected_version_sha256 not in self.allowed_versions_sha256:
            raise ValueError("selected version must be predeclared")
        if not self.required_regimes or len(set(self.required_regimes)) != len(self.required_regimes):
            raise ValueError("declare unique required regimes")
        if not self.stop_review_conditions or not self.cost_allocation_policy.strip():
            raise ValueError("stop/review and expense-allocation rules are required")
        if self.block_excess_upper_eur <= self.block_excess_lower_eur:
            raise ValueError("predeclared uncertainty bounds must be ordered")
        gap = timedelta(seconds=self.purge_seconds)
        if self.validation.start < self.development.end + gap:
            raise ValueError("development/validation horizons must be purged")
        if self.forward_blocks[0].start < self.validation.end + gap:
            raise ValueError("validation/forward horizons must be purged")
        duration = self.forward_blocks[0].end - self.forward_blocks[0].start
        for previous, current in zip(self.forward_blocks, self.forward_blocks[1:]):
            if current.start != previous.end or current.end - current.start != duration:
                raise ValueError("fixed forward blocks must be contiguous and equally sized")
        names = [case.name for case in self.sensitivities]
        if not names or len(set(names)) != len(names):
            raise ValueError("declare distinct friction/model/missed-fill sensitivity cases")
        if not any(case.fee_multiplier > 1 for case in self.sensitivities):
            raise ValueError("fee sensitivity is required")
        if not any(case.additional_slippage_bps > 0 for case in self.sensitivities):
            raise ValueError("slippage sensitivity is required")
        if not any(case.additional_agent_model_cost_eur > 0 for case in self.sensitivities):
            raise ValueError("additional model-call cost sensitivity is required")
        if not any(case.missed_profitable_pnl_fraction > 0 for case in self.sensitivities):
            raise ValueError("missed-fill sensitivity is required")
        return self

    baselines: tuple[Baseline, ...]

    @model_validator(mode="after")
    def baseline_set(self):
        if sorted(item.arm for item in self.baselines) != ["buy_and_hold", "cash", "deterministic"]:
            raise ValueError("cash, buy-and-hold and deterministic baselines are required once each")
        return self


class DecisionEvidence(EvaluationContract):
    decision_id: Reference
    window: Window  # decision time through its outcome horizon
    latest_input_available_at: datetime
    version_sha256: Fingerprint
    source_ref: Reference
    useful: bool

    @field_validator("latest_input_available_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("input availability requires a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def point_in_time(self):
        if self.latest_input_available_at > self.window.start:
            raise ValueError("decision inputs cannot arrive after the decision")
        return self


class ArmPerformance(EvaluationContract):
    arm: Arm
    opening_equity_eur: Amount = Field(gt=0)
    closing_equity_eur: Amount = Field(ge=0)
    external_flows_eur: Amount
    embedded_operating_expenses_eur: Amount = Field(ge=0)
    # Fees/slippage are already in execution-based equity; these are diagnostics.
    trading_fees_eur: Amount = Field(ge=0)
    measured_slippage_eur: Amount = Field(ge=0)
    turnover_eur: Amount = Field(ge=0)
    maximum_drawdown_fraction: Amount = Field(ge=0, le=1)
    mean_exposure_fraction: Amount = Field(ge=0, le=1)
    operational_errors: int = Field(ge=0, strict=True)
    source_ref: Reference

    @property
    def trading_pnl(self) -> Decimal:
        return (self.closing_equity_eur - self.opening_equity_eur - self.external_flows_eur
                + self.embedded_operating_expenses_eur)


class ForwardObservation(EvaluationContract):
    block_index: int = Field(ge=0, strict=True)
    window: Window
    evidence_kind: Literal["forward_paper", "synthetic"]
    available_at: datetime
    data_policy_sha256: Fingerprint
    friction_policy_sha256: Fingerprint
    regime_classifier_sha256: Fingerprint
    regime: Reference
    source_ref: Reference
    credentialed_soak_report_ref: Reference | None = None
    observed_venue_conditions_ref: Reference | None = None
    paper_venue_differences: tuple[Reference, ...] = ()
    independence_status: Literal["assessed_independent", "dependent", "unassessed"] = "unassessed"
    independence_assessment_ref: Reference | None = None
    opportunities: int = Field(ge=0, strict=True)
    decisions: tuple[DecisionEvidence, ...]
    arms: tuple[ArmPerformance, ...]

    @field_validator("available_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("observation availability requires a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def observation(self):
        if self.available_at < self.window.end:
            raise ValueError("observations require a completed outcome horizon")
        if sorted(item.arm for item in self.arms) != sorted(ARMS):
            raise ValueError("each observation requires all four arms once")
        if len(self.decisions) > self.opportunities:
            raise ValueError("decisions cannot exceed recorded opportunities")
        ids = [item.decision_id for item in self.decisions]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate decision evidence")
        for decision in self.decisions:
            if decision.window.start < self.window.start or decision.window.end > self.window.end:
                raise ValueError("decision/outcome horizons must stay inside their fixed block")
        flows = {item.external_flows_eur for item in self.arms}
        if len(flows) != 1:
            raise ValueError("comparisons require the same capital-flow path")
        return self


class ExpenseEvidence(EvaluationContract):
    receipt_id: Reference
    source_ref: Reference  # authoritative receipt/invoice, not an operating-budget write
    conversion_ref: Reference  # dated valuation/FX evidence, including EUR identity conversion
    incurred_at: datetime
    amount_eur: Amount | None
    evidence_kind: Literal["actual", "synthetic"]
    cost_class: Literal["recurring", "setup_engineering"]
    outcome: Literal["succeeded", "failed", "unresolved"]
    variant_sha256: Fingerprint | None = None

    @field_validator("incurred_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("expense evidence requires a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def expense(self):
        if self.amount_eur is not None and self.amount_eur < 0:
            raise ValueError("expense amounts cannot be negative")
        if self.amount_eur is None and self.outcome != "unresolved":
            raise ValueError("unknown usage must remain unresolved")
        return self


class ExpenseAllocation(EvaluationContract):
    receipt_id: Reference
    arm: Arm
    weight: Amount = Field(gt=0, le=1)
    source_ref: Reference


class ExpenseResolution(EvaluationContract):
    receipt_id: Reference
    amount_eur: Amount = Field(ge=0)
    outcome: Literal["succeeded", "failed"]
    source_ref: Reference
    conversion_ref: Reference


class Attempt(EvaluationContract):
    attempt_id: Reference
    variant_sha256: Fingerprint
    objective: Reference


class AttemptResult(EvaluationContract):
    attempt_id: Reference
    outcome: Literal["completed", "failed", "rejected"]
    source_ref: Reference
    receipt_ids: tuple[Reference, ...]


class CostInventory(EvaluationContract):
    source_ref: Reference
    ledger_export_sha256: Fingerprint
    source_cutoff: datetime
    receipt_ids: tuple[Reference, ...]

    @field_validator("source_cutoff")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("ledger cutoff requires a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.receipt_ids)) != len(self.receipt_ids):
            raise ValueError("ledger inventory contains duplicate receipts")
        return self
