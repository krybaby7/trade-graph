"""Trader day plans (T34; D115, D117, D119, D127, D138 item 5).

A ``DayPlan`` is what the Trader writes for one focus coin: the practice-range strategy version it follows, the
menu indicators and settings it watches, the alarms that should wake it, and resting limit entries, each with a
protective exit (a stop-loss and an optional take-profit, one cancelling the other), plus a concise rationale.

Software validates and installs it (``application/day_plans.py``). The contract deliberately has no confidence
figure, no approval, no committee and no vote: the Trader's discretion is real and nobody approves a plan (AGENTS.md,
D121). Software checks only what is deterministic: the strategy version has a practice-range report, the coin is
in the owner's scope, the entries fit the money limits, and everything is spot, long only and unleveraged (D127).

The shapes stay plain JSON objects with named fields (parameters are name and value pairs, not a free mapping), so
the same contract can be a provider's structured reply when the event engine (T35) asks the Trader for a plan.
Prices and quantities are exact decimals; a binary float is refused. This module imports no adapter, library or
model code.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

from trade_graph.contracts.alarms import (
    MAX_NOTE_LENGTH,
    SYMBOL_PATTERN,
    AlarmRelation,
    CandlePriceCondition,
    IndicatorCondition,
    QuotePriceCondition,
    parse_condition,
)
from trade_graph.contracts.candles import COLLECTED_INTERVALS
from trade_graph.contracts.indicators import get_indicator
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.money import canonical_decimal, parse_decimal

DAY_PLAN_CONTRACT_VERSION = "day-plan-v1"
# Size bounds of one document (like a request envelope), not limits on activity: the Trader may write and revise
# plans as often as it likes (D116).
MAX_ENTRIES = 16
MAX_ALARMS = 16
MAX_INDICATORS = 8
MAX_PARAMETERS = 8
MAX_RATIONALE_LENGTH = 1000
MAX_INVALIDATION_LENGTH = 500
STRATEGY_REF_PATTERN = r"^[a-z0-9][a-z0-9-]{2,63}@[1-9][0-9]{0,6}$"
_PARAMETER_NAME = r"^[a-z][a-z0-9_]{0,31}$"
_DECIMAL_TEXT_LIMIT = 40
_DECIMAL_DIGIT_LIMIT = 30
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")
AlarmKind = Literal["quote_price", "candle_price", "indicator"]
AlarmPrice = Literal["mid", "bid", "ask", "last", "close", "high", "low"]


def _price(value: Any) -> Decimal:
    if isinstance(value, str) and len(value) > _DECIMAL_TEXT_LIMIT:
        raise ValueError("decimal text is too long")
    try:
        parsed = parse_decimal(value)
    except (ValueError, ArithmeticError) as error:
        raise ValueError(f"an exact decimal is required, as text or an integer, never a float ({error})") from None
    if len(parsed.as_tuple().digits) > _DECIMAL_DIGIT_LIMIT:
        raise ValueError(f"at most {_DECIMAL_DIGIT_LIMIT} digits")
    if parsed <= 0:
        raise ValueError("prices and quantities are positive")
    return parsed


def _plain(value: str | None) -> str | None:
    if value is not None and _CONTROL.search(value):
        raise ValueError("control or text-direction characters are not allowed")
    return value


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlanParameter(_Frozen):
    """One setting of a menu indicator, by name."""

    name: str = Field(pattern=_PARAMETER_NAME)
    value: StrictInt | StrictFloat


def _settings(parameters: tuple[PlanParameter, ...]) -> dict[str, int | float]:
    names = [item.name for item in parameters]
    if len(names) != len(set(names)):
        raise ValueError("each indicator setting is named once")
    return {item.name: item.value for item in parameters}


class PlanIndicator(_Frozen):
    """A menu indicator (docs/INDICATOR-MENU.md) the plan watches, on one collected candle interval."""

    indicator: str = Field(min_length=1, max_length=32)
    interval_minutes: int = Field(strict=True)
    parameters: tuple[PlanParameter, ...] = Field(default=(), max_length=MAX_PARAMETERS)

    @field_validator("interval_minutes")
    @classmethod
    def _collected(cls, value: int) -> int:
        if value not in COLLECTED_INTERVALS:
            raise ValueError(f"indicators read the collected intervals {COLLECTED_INTERVALS} only")
        return value

    @model_validator(mode="after")
    def _on_the_menu(self) -> PlanIndicator:
        try:
            get_indicator(self.indicator).resolve_parameters(_settings(self.parameters))
        except ValidationFailure as error:
            raise ValueError(str(error)) from None
        return self

    def resolved_parameters(self) -> dict[str, int | float]:
        """Every setting with the menu defaults filled in, so the record says exactly what is computed."""
        return get_indicator(self.indicator).resolve_parameters(_settings(self.parameters))


class PlanAlarm(_Frozen):
    """An alarm that wakes the Trader (T33): a flat shape that becomes the alarm contract's condition.

    ``price`` is ``mid``, ``bid``, ``ask`` or ``last`` for a quote alarm and ``close``, ``high`` or ``low`` for a
    candle alarm; candle and indicator alarms name the ``interval_minutes``; indicator alarms name the
    ``indicator``, its settings and, for an indicator with several outputs, the ``output``.
    """

    kind: AlarmKind
    symbol: str = Field(pattern=SYMBOL_PATTERN)
    relation: AlarmRelation
    level: Decimal
    price: AlarmPrice | None = None
    interval_minutes: int | None = Field(default=None, strict=True)
    indicator: str | None = Field(default=None, min_length=1, max_length=32)
    parameters: tuple[PlanParameter, ...] = Field(default=(), max_length=MAX_PARAMETERS)
    output: str | None = Field(default=None, min_length=1, max_length=32)
    note: str | None = Field(default=None, min_length=1, max_length=MAX_NOTE_LENGTH)

    @field_validator("level", mode="before")
    @classmethod
    def _level(cls, value: Any) -> Decimal:
        if isinstance(value, str) and len(value) > _DECIMAL_TEXT_LIMIT:
            raise ValueError("decimal text is too long")
        try:
            return parse_decimal(value)
        except (ValueError, ArithmeticError) as error:
            raise ValueError(f"an exact decimal level is required, never a float ({error})") from None

    @field_serializer("level")
    def _serialize_level(self, value: Decimal) -> str:
        return canonical_decimal(value)

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        return _plain(value)

    @model_validator(mode="after")
    def _a_valid_alarm(self) -> PlanAlarm:
        try:
            parse_condition(self.condition_document())
        except ValidationError as error:
            raise ValueError(f"the alarm is not valid: {error.errors()[0]['msg']}") from None
        return self

    def condition_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {"kind": self.kind, "symbol": self.symbol, "relation": self.relation,
                                    "level": canonical_decimal(self.level)}
        if self.kind == "quote_price":
            if self.interval_minutes is not None or self.indicator is not None or self.parameters or self.output:
                raise ValueError("a quote alarm watches a price update, not a candle series or an indicator")
            if self.price is not None:
                document["price"] = self.price
            return document
        if self.interval_minutes is None:
            raise ValueError("a candle or indicator alarm names its candle interval")
        document["interval_minutes"] = self.interval_minutes
        if self.kind == "candle_price":
            if self.indicator is not None or self.parameters or self.output:
                raise ValueError("a candle alarm watches a candle price, not an indicator")
            if self.price is not None:
                document["price"] = self.price
            return document
        if self.price is not None:
            raise ValueError("an indicator alarm compares the indicator, not a price")
        if self.indicator is None:
            raise ValueError("an indicator alarm names the menu indicator")
        document["indicator"] = self.indicator
        document["parameters"] = _settings(self.parameters)
        if self.output is not None:
            document["output"] = self.output
        return document

    def condition(self) -> QuotePriceCondition | CandlePriceCondition | IndicatorCondition:
        return parse_condition(self.condition_document())


class PlanEntry(_Frozen):
    """A resting limit buy with its protective exit (D117).

    The stop sits below the limit and the optional take-profit above it. The exit is attached by software to every
    part of the entry that fills, whatever the Trader is doing. ``expires_at_utc`` defaults to the end of the
    trading day; an entry still resting then is cancelled (a filled part keeps its exit).
    """

    quantity: Decimal
    limit_price: Decimal
    stop_price: Decimal
    take_profit_price: Decimal | None = None
    expires_at_utc: AwareDatetime | None = None

    @field_validator("quantity", "limit_price", "stop_price", mode="before")
    @classmethod
    def _positive(cls, value: Any) -> Decimal:
        return _price(value)

    @field_validator("take_profit_price", mode="before")
    @classmethod
    def _target(cls, value: Any) -> Decimal | None:
        return None if value is None else _price(value)

    @field_serializer("quantity", "limit_price", "stop_price", "take_profit_price")
    def _serialize(self, value: Decimal | None) -> str | None:
        return None if value is None else canonical_decimal(value)

    @model_validator(mode="after")
    def _a_long_protected_entry(self) -> PlanEntry:
        if self.stop_price >= self.limit_price:
            raise ValueError("the protective stop sits below the entry's limit price (spot, long only, D127)")
        if self.take_profit_price is not None and self.take_profit_price <= self.limit_price:
            raise ValueError("the take-profit sits above the entry's limit price")
        return self


class DayPlan(_Frozen):
    """The Trader's plan for one focus coin and trading day. It holds no confidence figure or approval."""

    schema_version: Literal[1] = 1
    symbol: str = Field(pattern=SYMBOL_PATTERN)
    strategy_ref: str = Field(pattern=STRATEGY_REF_PATTERN)
    indicators: tuple[PlanIndicator, ...] = Field(default=(), max_length=MAX_INDICATORS)
    alarms: tuple[PlanAlarm, ...] = Field(default=(), max_length=MAX_ALARMS)
    entries: tuple[PlanEntry, ...] = Field(default=(), max_length=MAX_ENTRIES)
    rationale: str = Field(min_length=1, max_length=MAX_RATIONALE_LENGTH)
    invalidation: str = Field(min_length=1, max_length=MAX_INVALIDATION_LENGTH)

    @field_validator("rationale", "invalidation")
    @classmethod
    def _text(cls, value: str) -> str:
        return _plain(value) or value

    @model_validator(mode="after")
    def _does_something(self) -> DayPlan:
        if not self.entries and not self.alarms:
            raise ValueError("a day plan rests at least one entry or sets at least one alarm; to do nothing, the "
                             "Trader records no_action")
        return self

    @property
    def strategy_id(self) -> str:
        return self.strategy_ref.split("@", 1)[0]

    def canonical_document(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @property
    def content_sha256(self) -> str:
        text = json.dumps(self.canonical_document(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(text.encode("ascii")).hexdigest()


def expiry_of(entry: PlanEntry, day_end: datetime) -> datetime:
    """When an entry stops resting: its own expiry, or the end of the trading day."""
    return entry.expires_at_utc or day_end


__all__ = [
    "DAY_PLAN_CONTRACT_VERSION", "MAX_ALARMS", "MAX_ENTRIES", "MAX_INDICATORS", "MAX_INVALIDATION_LENGTH",
    "MAX_RATIONALE_LENGTH", "STRATEGY_REF_PATTERN", "DayPlan", "PlanAlarm", "PlanEntry", "PlanIndicator",
    "PlanParameter", "expiry_of",
]
