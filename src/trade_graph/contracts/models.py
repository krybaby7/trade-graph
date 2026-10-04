"""Business contracts. Decimal strings, extra fields rejected, mode is not model-controlled."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from trade_graph.domain.money import Money, Quantity, parse_decimal

Mode = Literal["paper", "replay", "live"]
DecisionAction = Literal["enter", "exit", "hold", "adjust_order", "resize", "no_action"]
TaskStatus = Literal[
    "QUEUED",
    "LEASED",
    "RUNNING",
    "WAITING_EXTERNAL",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "BLOCKED_BUDGET",
    "DEAD_LETTER",
    "PROPOSED",
]
PauseProfile = Literal[
    "RUNNING",
    "PAUSE_DECISIONS",
    "NO_NEW_EXPOSURE",
    "MANAGE_ONLY",
    "CANCEL_ALL",
    "FLATTEN",
    "STOPPED",
]
LessonStatus = Literal["tentative", "testing", "supported", "contradicted", "retired"]
CandidateState = Literal[
    "AUTHORIZED",
    "DEVELOPING",
    "VALIDATING",
    "FAILED",
    "READY",
    "APPROVED",
    "ACTIVATING",
    "ACTIVE",
    "OBSERVING",
    "REJECTED",
    "ROLLED_BACK",
    "SUPERSEDED",
]
ModelFailureKind = Literal[
    "credentials",
    "unsupported",
    "rate_limit",
    "temporary",
    "timeout_uncertain",
    "refusal",
    "truncation",
    "validation",
]
BrokerErrorKind = Literal[
    "lookup_not_supported",
    "timeout_uncertain",
    "rejected",
    "unavailable",
    "rate_limited",
]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _dec(value: Any) -> Decimal:
    return parse_decimal(value)


class Envelope(ContractModel):
    schema_version: int = 1
    record_id: str
    created_at_utc: datetime
    run_id: str
    task_id: str | None = None
    root_task_id: str | None = None
    portfolio_id: str | None = None
    mode: Mode
    system_version_id: str
    evidence_refs: list[str] = Field(default_factory=list)
    trace_id: str


class OwnerPolicy(ContractModel):
    schema_version: int = 1
    revision_id: str
    live_enabled: bool = False
    withdrawals_allowed: bool = False
    leverage_allowed: bool = False
    paid_calls_enabled: bool = False
    reporting_currency: str = "EUR"
    virtual_capital: Money
    monthly_operating: Money
    priority_reserve: Money
    daily_paid_limit: Money
    root_paid_limit: Money
    maximum_gross_exposure_fraction: Decimal
    maximum_single_asset_exposure_fraction: Decimal
    maximum_delegation_depth: int = 3
    maximum_descendants_per_root: int = 12
    ordinary_max_paid_attempts: int = 3
    engineer_max_paid_attempts: int = 10
    maximum_schema_repairs: int = 1
    allowed_change_classes: list[str]
    allowed_venues: list[str]
    allowed_symbols: list[str]
    budget_exhaustion_profile: PauseProfile = "MANAGE_ONLY"
    fx_reserve_buffer: Decimal = Decimal("1.02")
    maximum_quote_age_seconds: int = 15

    @field_validator(
        "maximum_gross_exposure_fraction",
        "maximum_single_asset_exposure_fraction",
        "fx_reserve_buffer",
        mode="before",
    )
    @classmethod
    def _fractions(cls, value: Any) -> Decimal:
        return _dec(value)

    @field_serializer(
        "maximum_gross_exposure_fraction",
        "maximum_single_asset_exposure_fraction",
        "fx_reserve_buffer",
    )
    def _ser_fractions(self, value: Decimal) -> str:
        from trade_graph.domain.money import canonical_decimal

        return canonical_decimal(value)


class Mandate(ContractModel):
    schema_version: int = 1
    mandate_id: str
    portfolio_id: str
    revision: int
    strategy_ids: list[str]
    experiment_id: str | None = None
    symbols: list[str]
    allowed_order_types: list[Literal["market", "limit"]]
    max_gross_exposure_fraction: Decimal
    max_single_asset_exposure_fraction: Decimal
    decision_horizon_seconds: int
    order_types_long_only: bool = True
    discretionary_experiment: bool = True
    max_quote_age_seconds: int
    expires_at_utc: datetime
    resource_note: str

    @field_validator(
        "max_gross_exposure_fraction",
        "max_single_asset_exposure_fraction",
        mode="before",
    )
    @classmethod
    def _fractions(cls, value: Any) -> Decimal:
        return _dec(value)

    @field_serializer("max_gross_exposure_fraction", "max_single_asset_exposure_fraction")
    def _ser(self, value: Decimal) -> str:
        from trade_graph.domain.money import canonical_decimal

        return canonical_decimal(value)


class Decision(Envelope):
    action: DecisionAction
    symbol: str | None = None
    position_ref: str | None = None
    order_ref: str | None = None
    quantity: Quantity | None = None
    limit_price: Money | None = None
    stop_price: Money | None = None
    time_in_force: Literal["gtc", "ioc"] = "gtc"
    rationale: str
    invalidation: str
    horizon_seconds: int
    strategy_id: str
    experiment_id: str | None = None
    snapshot_id: str
    mandate_revision: str
    policy_revision: str
    no_action_reason: str | None = None
    uncertainty_note: str | None = None


class ResearchFinding(Envelope):
    question: str
    claim: str
    source_url: str
    publisher: str
    published_at_utc: datetime | None
    event_at_utc: datetime | None
    retrieved_at_utc: datetime
    available_at_utc: datetime
    source_hash: str
    instruments: list[str]
    relevance: str
    counterevidence: str
    expires_at_utc: datetime
    invalidation: str


class StrategyProposal(Envelope):
    hypothesis: str
    mechanism: str
    features: list[str]
    entry_rule: str
    exit_rule: str
    invalidation: str
    sizing_rule: str
    required_capabilities: list[str]
    estimated_friction_note: str
    regimes: list[str]
    baseline: str
    validation_protocol: str
    retire_when: str
    provenance: str
    verification_status: Literal["unverified", "fixture", "observed"]


class LessonRevision(Envelope):
    lesson_id: str
    revision: int
    observation: str
    supporting_cases: list[str]
    counterexamples: list[str]
    explanation: str
    proposed_improvement: str
    validation_method: str
    subsequent_result: str | None = None
    scope: str
    sample_note: str
    confidence_category: Literal["insufficient", "tentative", "testing", "supported"]
    linked_decisions: list[str]
    status: LessonStatus
    supersedes: str | None = None
    process_assessment: Literal["valid_thesis", "invalid_process", "unclassified"] = "unclassified"
    outcome_sign: Literal["gain", "loss", "flat", "unknown"] = "unknown"


class OptimisationProposal(Envelope):
    issue: str
    resources: str
    interval: str
    modification: str
    expected_benefit: str
    quality_risk: str
    validation_metrics: list[str]
    disables_reconciliation: bool = False


class LeaderDecision(Envelope):
    actions: list[dict[str, Any]] = Field(default_factory=list)
    snapshot_id: str | None = None
    rationale: str
    intended_outcome: str
    mandate_id: str | None = None
    pause_profile: PauseProfile | None = None
    activate_candidate_id: str | None = None
    reject_candidate_id: str | None = None
    task_objectives: list[str] = Field(default_factory=list)
    resources_committed: str
    authority_ok: bool
    review_criteria: str


class ChangeTask(Envelope):
    objective: str
    baseline_version: str
    baseline_hash: str
    allowed_classes: list[str]
    allowed_paths: list[str]
    invariants: list[str]
    max_spend: Money
    max_steps: int
    test_plan: str
    success_criteria: str
    rollback_criteria: str
    expires_at_utc: datetime


class ChangeResult(Envelope):
    change_id: str
    candidate_id: str
    state: CandidateState
    content_hash: str | None
    changed_files: list[str]
    attestation_id: str | None
    known_limits: str


class UsageReceipt(ContractModel):
    schema_version: int = 1
    receipt_id: str
    reservation_id: str
    provider: str
    model: str
    requested_model: str
    role: str
    task_id: str | None
    root_task_id: str | None
    run_id: str
    system_version_id: str
    price_card_id: str
    uncached_input_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    billed_output_tokens: int
    reasoning_tokens: int
    tool_units: int
    native_cost: Money
    status: Literal["committed", "uncertain", "reconciled", "conservative_charge"]
    synthetic: bool
    created_at_utc: datetime


class InstrumentRules(ContractModel):
    venue: str
    symbol: str
    base_asset: str
    quote_asset: str
    price_increment: Decimal
    quantity_increment: Decimal
    min_quantity: Decimal
    min_notional: Decimal
    synthetic: bool = False

    @field_validator(
        "price_increment",
        "quantity_increment",
        "min_quantity",
        "min_notional",
        mode="before",
    )
    @classmethod
    def _decs(cls, value: Any) -> Decimal:
        return _dec(value)

    @field_serializer(
        "price_increment",
        "quantity_increment",
        "min_quantity",
        "min_notional",
    )
    def _ser(self, value: Decimal) -> str:
        from trade_graph.domain.money import canonical_decimal

        return canonical_decimal(value)


class BrokerCapabilities(ContractModel):
    venue: str
    mode: Mode
    client_id_lookup: bool
    native_amend: bool
    native_stop: bool
    native_stop_tested: bool
    time_in_force: list[str]
    reduce_only_flag: bool
    fills_pagination: bool
    cancel_behaviour: str
    withdrawals: bool = False


class AuthorizedOrderIntent(ContractModel):
    intent_id: str
    portfolio_id: str
    account_id: str
    venue: str
    mode: Mode
    client_order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit", "stop"]
    quantity: Decimal
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: Literal["gtc", "ioc"] = "gtc"
    decision_id: str | None = None
    snapshot_id: str
    eligible_after_utc: datetime
    excluded_observation_id: str | None = None
    reduce_only: bool = False

    @field_validator("quantity", "limit_price", "stop_price", mode="before")
    @classmethod
    def _decs(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        return _dec(value)

    @field_serializer("quantity", "limit_price", "stop_price")
    def _ser(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        from trade_graph.domain.money import canonical_decimal

        return canonical_decimal(value)


class SubmitResult(ContractModel):
    status: Literal["acknowledged", "rejected", "uncertain"]
    venue_order_id: str | None = None
    error: BrokerErrorKind | None = None
    message: str = ""


class CancelRequest(ContractModel):
    intent_id: str
    client_order_id: str
    venue_order_id: str | None = None
    symbol: str


class CancelResult(ContractModel):
    status: Literal["cancel_pending", "cancelled", "filled", "uncertain", "rejected"]
    error: BrokerErrorKind | None = None
    message: str = ""


class OrderLookup(ContractModel):
    client_order_id: str | None = None
    venue_order_id: str | None = None
    symbol: str


class OrderLookupResult(ContractModel):
    status: Literal["open", "partially_filled", "filled", "cancelled", "unknown", "not_found"]
    error: BrokerErrorKind | None = None
    filled_quantity: Decimal = Decimal("0")
    venue_order_id: str | None = None

    @field_validator("filled_quantity", mode="before")
    @classmethod
    def _qty(cls, value: Any) -> Decimal:
        return _dec(value)

    @field_serializer("filled_quantity")
    def _ser(self, value: Decimal) -> str:
        from trade_graph.domain.money import canonical_decimal

        return canonical_decimal(value)


class BalanceSnapshot(ContractModel):
    venue: str
    account_id: str
    as_of_utc: datetime
    amounts: dict[str, str]


class OrderSnapshot(ContractModel):
    client_order_id: str
    venue_order_id: str | None
    symbol: str
    side: Literal["buy", "sell"]
    status: str
    remaining_quantity: Decimal

    @field_validator("remaining_quantity", mode="before")
    @classmethod
    def _qty(cls, value: Any) -> Decimal:
        return _dec(value)

    @field_serializer("remaining_quantity")
    def _ser(self, value: Decimal) -> str:
        from trade_graph.domain.money import canonical_decimal

        return canonical_decimal(value)


class FillFeeRecord(ContractModel):
    """One native fee debit (positive) or rebate credit (negative), never netted."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    asset: str
    amount: Decimal
    source_ref: str = Field(min_length=1, max_length=128)
    provenance_kind: Literal["native_source", "legacy_compatibility"] = "native_source"
    effective_at_utc: datetime
    identified_rate: Decimal | None = None
    rate_source_ref: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("amount", "identified_rate", mode="before")
    @classmethod
    def _numbers(cls, value: Any) -> Decimal | None:
        return None if value is None else _dec(value)

    @field_validator("asset")
    @classmethod
    def _asset(cls, value: str) -> str:
        if not value.isupper() or not value.isalnum() or not 2 <= len(value) <= 16:
            raise ValueError("native fee asset must be uppercase alphanumeric")
        return value

    @model_validator(mode="after")
    def _provenance(self) -> FillFeeRecord:
        if self.amount == 0:
            raise ValueError("fee vectors retain nonzero native legs")
        if self.effective_at_utc.tzinfo is None:
            raise ValueError("native fee effective time requires a timezone")
        if ((self.identified_rate is None) != (self.rate_source_ref is None)
                or (self.identified_rate is not None and self.identified_rate <= 0)):
            raise ValueError("identified fee valuation requires a positive rate and its source")
        return self

    @field_serializer("amount", "identified_rate")
    def _serialize(self, value: Decimal | None) -> str | None:
        from trade_graph.domain.money import canonical_decimal

        return None if value is None else canonical_decimal(value)


class FillRecord(ContractModel):
    venue: str
    account_id: str
    trade_id: str
    intent_id: str | None
    symbol: str
    side: Literal["buy", "sell"]
    quantity: Decimal
    price: Decimal
    # Native quote principal excludes fees. Older records omit this field and
    # retain quantity-times-price behavior and their exact serialized identity.
    quote_cost: Decimal | None = Field(default=None, exclude_if=lambda value: value is None)
    fee_amount: Decimal
    fee_asset: str
    liquidity: Literal["maker", "taker"]
    filled_at_utc: datetime
    heuristic: bool = False
    reference_mid: Decimal | None = None
    fee_identified_rate: Decimal | None = None
    fee_components: tuple[FillFeeRecord, ...] | None = Field(
        default=None, min_length=1, max_length=20, exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def _fee_vector(self) -> FillRecord:
        if self.fee_components is not None:
            if self.fee_amount != 0 or self.fee_identified_rate is not None:
                raise ValueError("fee vector cannot also charge the legacy fee")
            if len({item.source_ref for item in self.fee_components}) != len(self.fee_components):
                raise ValueError("native fee source references must be unique")
            if any(item.effective_at_utc != self.filled_at_utc for item in self.fee_components):
                raise ValueError("late fee adjustments require a separate correction contract")
        return self

    def fee_legs(self) -> tuple[FillFeeRecord, ...]:
        if self.fee_components is not None:
            return self.fee_components
        if self.fee_amount == 0:
            return ()
        return (FillFeeRecord(
            asset=self.fee_asset, amount=self.fee_amount, source_ref=f"{self.trade_id}:fee",
            effective_at_utc=self.filled_at_utc, identified_rate=self.fee_identified_rate,
            rate_source_ref=f"{self.trade_id}:legacy-fee-rate" if self.fee_identified_rate is not None else None,
            provenance_kind="legacy_compatibility",
        ),)

    @field_validator(
        "quantity",
        "price",
        "quote_cost",
        "fee_amount",
        "reference_mid",
        "fee_identified_rate",
        mode="before",
    )
    @classmethod
    def _decs(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        return _dec(value)

    @field_validator("quote_cost")
    @classmethod
    def _positive_quote_cost(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value <= 0:
            raise ValueError("native quote principal must be positive")
        return value

    @property
    def quote_principal(self) -> Decimal:
        return self.quote_cost if self.quote_cost is not None else self.quantity * self.price

    @field_serializer(
        "quantity",
        "price",
        "quote_cost",
        "fee_amount",
        "reference_mid",
        "fee_identified_rate",
    )
    def _ser(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        from trade_graph.domain.money import canonical_decimal

        return canonical_decimal(value)


class FillPage(ContractModel):
    fills: list[FillRecord]
    next_cursor: str | None = None


class Observation(ContractModel):
    observation_id: str
    venue: str
    symbol: str
    event_time_utc: datetime
    available_at_utc: datetime
    bid: Decimal | None = None
    ask: Decimal | None = None
    last: Decimal | None = None
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    volume: Decimal | None = None
    kind: Literal["quote", "trade", "bar"]
    source: str

    @field_validator("bid", "ask", "last", "bid_size", "ask_size", "volume", mode="before")
    @classmethod
    def _decs(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        return _dec(value)

    @field_serializer("bid", "ask", "last", "bid_size", "ask_size", "volume")
    def _ser(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        from trade_graph.domain.money import canonical_decimal

        return canonical_decimal(value)


class ModelCapabilities(ContractModel):
    provider: Literal["openai", "anthropic", "scripted"]
    model: str
    structured_output: bool
    forced_tool: bool
    sampling_temperature: bool
    tool_continuation: bool = False


class ToolRequest(ContractModel):
    call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ModelUsage(ContractModel):
    uncached_input_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    billed_output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    tool_units: int = Field(default=0, ge=0)
    provider_request_id: str | None = None


class ModelRequest(ContractModel):
    role: str
    task_id: str
    root_task_id: str
    run_id: str
    system_version_id: str
    provider: Literal["openai", "anthropic", "scripted"]
    model: str
    instructions: str
    context: dict[str, Any]
    output_schema: dict[str, Any]
    schema_name: str
    max_output_tokens: int
    max_tool_calls: int
    timeout_seconds: int
    priority: bool = False
    synthetic: bool = False


class ModelResult(ContractModel):
    ok: bool
    payload: dict[str, Any] | None = None
    failure: ModelFailureKind | None = None
    message: str = ""
    usage: ModelUsage | None = None
    tool_requests: list[ToolRequest] = Field(default_factory=list)
    provider_model: str | None = None
    elapsed_ms: int = 0
    raw_redacted: str = ""


class PriceCard(ContractModel):
    price_card_id: str
    provider: str
    model: str
    endpoint: str
    currency: str
    input_per_million: Decimal
    output_per_million: Decimal
    cache_read_per_million: Decimal | None = None
    cache_write_per_million: Decimal | None = None
    search_per_call: Decimal | None = None
    effective_at: str
    verified_at: str
    source_id: str
    tier: str
    context_band: str

    @field_validator(
        "input_per_million",
        "output_per_million",
        "cache_read_per_million",
        "cache_write_per_million",
        "search_per_call",
        mode="before",
    )
    @classmethod
    def _decs(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        result = _dec(value)
        if result < 0:
            raise ValueError("price rates must be nonnegative")
        return result

    @field_serializer(
        "input_per_million",
        "output_per_million",
        "cache_read_per_million",
        "cache_write_per_million",
        "search_per_call",
    )
    def _ser(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        from trade_graph.domain.money import canonical_decimal

        return canonical_decimal(value)


SCHEMA_MODELS: dict[str, type[ContractModel]] = {
    "OwnerPolicy": OwnerPolicy,
    "Mandate": Mandate,
    "Decision": Decision,
    "ResearchFinding": ResearchFinding,
    "StrategyProposal": StrategyProposal,
    "LessonRevision": LessonRevision,
    "OptimisationProposal": OptimisationProposal,
    "LeaderDecision": LeaderDecision,
    "ChangeTask": ChangeTask,
    "ChangeResult": ChangeResult,
    "UsageReceipt": UsageReceipt,
    "InstrumentRules": InstrumentRules,
    "BrokerCapabilities": BrokerCapabilities,
    "AuthorizedOrderIntent": AuthorizedOrderIntent,
    "FillRecord": FillRecord,
    "Observation": Observation,
    "ModelCapabilities": ModelCapabilities,
    "ModelRequest": ModelRequest,
    "ModelResult": ModelResult,
    "PriceCard": PriceCard,
}


def export_schemas() -> dict[str, dict[str, Any]]:
    return {name: model.model_json_schema() for name, model in SCHEMA_MODELS.items()}
