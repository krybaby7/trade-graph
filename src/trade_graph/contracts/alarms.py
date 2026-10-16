"""Price alarms (T33, D115-D117, D123): saved conditions that software watches and that fire once.

An alarm belongs to one department and may name the day plan it serves. It watches one of three things:

- ``quote_price``: a public price update (mid, bid, ask or last) of a symbol;
- ``candle_price``: the close, high or low of each newly completed candle of one series in the candle store;
- ``indicator``: a menu indicator (``contracts/indicators.py``) computed on each newly completed candle.

The relations are the indicator engine's four: ``above`` and ``below`` are strict, ``crosses_above`` is
previous <= level < newest and ``crosses_below`` is previous >= level > newest.

Levels are exact Decimals (floats refused). There is deliberately no field that caps how many alarms exist or
how often they fire (D116): the one-shot rule, enforced by the store, is the only limit.

This module is a contract only: it imports no library, adapter or model code.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_serializer, field_validator, model_validator

from trade_graph.contracts.candles import COLLECTED_INTERVALS, CandleSource
from trade_graph.contracts.indicators import get_indicator
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.money import canonical_decimal, parse_decimal

ALARM_CONTRACT_VERSION = "price-alarm-v1"
AlarmDepartment = Literal["trader", "research", "leader", "learning", "optimisation"]
ALARM_DEPARTMENTS: tuple[AlarmDepartment, ...] = ("trader", "research", "leader", "learning", "optimisation")
AlarmRelation = Literal["above", "below", "crosses_above", "crosses_below"]
ALARM_RELATIONS: tuple[AlarmRelation, ...] = ("above", "below", "crosses_above", "crosses_below")
QuotePrice = Literal["mid", "bid", "ask", "last"]
CandlePrice = Literal["close", "high", "low"]
AlarmStatus = Literal["armed", "spent", "cancelled"]
DeliveryKind = Literal["alarm_fired", "feed_lost", "feed_restored"]
DELIVERY_KINDS: tuple[DeliveryKind, ...] = ("alarm_fired", "feed_lost", "feed_restored")
BatchStatus = Literal["claimed", "acknowledged", "released"]

# The indicator engine refuses longer levels; a price level of that length is not one anyone set on purpose.
MAX_LEVEL_DIGITS = 64
MAX_NOTE_LENGTH = 280
SYMBOL_PATTERN = r"^[A-Z0-9]{1,16}/[A-Z]{3,5}$"
REFERENCE_PATTERN = r"^[A-Za-z0-9._:/-]{1,128}$"
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
# On-Balance Volume is a running total whose level depends on where its history starts, so no amount of history
# makes its level agree between readings (docs/INDICATOR-MENU.md, D136). An alarm level on it means nothing.
_LEVEL_FREE_INDICATORS = {"OBV": "OBV's level depends on where its history starts (D136); an alarm level on it "
                                 "is not meaningful. Use a change indicator instead"}


def _level(value: Any) -> Decimal:
    if isinstance(value, str) and len(value) > MAX_LEVEL_DIGITS + 8:
        raise ValueError("the alarm level is too long")
    try:
        parsed = parse_decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValueError("the alarm level must be a finite decimal (a decimal string or an integer, never a float)") \
            from error
    if len(parsed.as_tuple().digits) > MAX_LEVEL_DIGITS:
        raise ValueError(f"the alarm level may have at most {MAX_LEVEL_DIGITS} digits")
    return parsed


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Condition(_Frozen):
    symbol: str = Field(pattern=SYMBOL_PATTERN)
    relation: AlarmRelation
    level: Decimal

    @field_validator("level", mode="before")
    @classmethod
    def _decimal_level(cls, value: Any) -> Decimal:
        return _level(value)

    @field_serializer("level")
    def _serialize_level(self, value: Decimal) -> str:
        return canonical_decimal(value)

    @property
    def needs_previous(self) -> bool:
        """A crossing compares two readings; above and below compare one."""
        return self.relation.startswith("crosses")


class QuotePriceCondition(_Condition):
    """A public price update of ``symbol`` compared with ``level``. ``mid`` is (bid + ask) / 2."""

    kind: Literal["quote_price"] = "quote_price"
    price: QuotePrice = "mid"

    @field_validator("level")
    @classmethod
    def _positive(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("a price level must be positive")
        return value

    @property
    def basis(self) -> Literal["quote"]:
        return "quote"


class _CandleCondition(_Condition):
    interval_minutes: int = Field(strict=True)
    source: CandleSource = "kraken_public_ohlc"

    @field_validator("interval_minutes")
    @classmethod
    def _collected(cls, value: int) -> int:
        if value not in COLLECTED_INTERVALS:
            raise ValueError(f"candle alarms watch the collected intervals {COLLECTED_INTERVALS} only")
        return value

    @property
    def basis(self) -> Literal["candle"]:
        return "candle"


class CandlePriceCondition(_CandleCondition):
    """The close, high or low of each newly completed candle compared with ``level``."""

    kind: Literal["candle_price"] = "candle_price"
    price: CandlePrice = "close"

    @field_validator("level")
    @classmethod
    def _positive(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("a price level must be positive")
        return value


class IndicatorCondition(_CandleCondition):
    """A menu indicator on each newly completed candle compared with ``level`` (the engine's 12-digit rule)."""

    kind: Literal["indicator"] = "indicator"
    indicator: str
    parameters: dict[str, int | float] = Field(default_factory=dict)
    output: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _resolve(cls, data: Any) -> Any:
        """Fill the menu defaults and the output name so the stored condition says exactly what is computed."""
        if not isinstance(data, dict):
            return data
        name = data.get("indicator")
        try:
            spec = get_indicator(name)
            resolved = spec.resolve_parameters(data.get("parameters") or {})
        except ValidationFailure as error:
            raise ValueError(str(error)) from error
        if spec.name in _LEVEL_FREE_INDICATORS:
            raise ValueError(_LEVEL_FREE_INDICATORS[spec.name])
        output = data.get("output")
        names = spec.output_names
        if output is None:
            if len(names) != 1:
                raise ValueError(f"{spec.name} has several outputs ({', '.join(names)}); name one")
            output = names[0]
        elif output not in names:
            raise ValueError(f"{spec.name} has no output {output!r}")
        return {**data, "parameters": resolved, "output": output}

    @model_validator(mode="after")
    def _inside_the_range(self) -> IndicatorCondition:
        spec = get_indicator(self.indicator)
        declared = next(item for item in spec.outputs if item.name == self.output)
        lowest = None if declared.lowest is None else Decimal(str(declared.lowest))
        highest = None if declared.highest is None else Decimal(str(declared.highest))
        if (lowest is not None and self.level < lowest) or (highest is not None and self.level > highest):
            raise ValueError(f"the level is outside the range {self.indicator} {self.output} can read "
                             f"({declared.lowest} to {declared.highest}); the alarm could never mean anything")
        return self


AlarmCondition = Annotated[QuotePriceCondition | CandlePriceCondition | IndicatorCondition,
                           Field(discriminator="kind")]
_CONDITION = TypeAdapter(AlarmCondition)


def parse_condition(data: Any) -> QuotePriceCondition | CandlePriceCondition | IndicatorCondition:
    """A condition from plain JSON (a dict); raises pydantic's ``ValidationError`` when it is not valid."""
    return _CONDITION.validate_python(data)


def _clean_text(value: str | None) -> str | None:
    if value is not None and _CONTROL.search(value):
        raise ValueError("control characters are not allowed")
    return value


class AlarmRequest(_Frozen):
    """What a department asks for. ``request_id`` makes a retried tool call return the same alarm."""

    department: AlarmDepartment
    condition: AlarmCondition
    portfolio_id: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    day_plan_ref: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    task_id: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    request_id: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    note: str | None = Field(default=None, min_length=1, max_length=MAX_NOTE_LENGTH)

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        return _clean_text(value)


class AlarmRecord(_Frozen):
    """An alarm as stored, with its current state. ``generation`` counts armings: 1, then +1 per re-arm."""

    alarm_id: str
    department: AlarmDepartment
    condition: AlarmCondition
    portfolio_id: str | None
    day_plan_ref: str | None
    task_id: str | None
    request_id: str | None
    note: str | None
    created_at: str
    status: AlarmStatus
    generation: int = Field(ge=1)
    armed_at: str
    fired_at: str | None
    cancelled_at: str | None
    evaluated_through: str | None
    last_reason: str | None


class AlarmDelivery(_Frozen):
    """One queued item for a department: an alarm that fired, or a feed that was lost or restored."""

    delivery_id: str
    sequence: int = Field(ge=1)
    department: AlarmDepartment
    kind: DeliveryKind
    alarm_id: str | None
    generation: int | None = Field(ge=1)
    portfolio_id: str | None
    created_at: str
    document: dict[str, Any]

    @model_validator(mode="after")
    def _alarm_reference(self) -> AlarmDelivery:
        if (self.kind == "alarm_fired") != (self.alarm_id is not None and self.generation is not None):
            raise ValueError("an alarm_fired item names its alarm and generation; feed notices name none")
        return self


class DeliveryBatch(_Frozen):
    """Everything one department had waiting when one wake claimed it, oldest first."""

    wake_id: str
    department: AlarmDepartment
    status: BatchStatus
    claimed_at: str
    items: tuple[AlarmDelivery, ...]


__all__ = [
    "ALARM_CONTRACT_VERSION", "ALARM_DEPARTMENTS", "ALARM_RELATIONS", "DELIVERY_KINDS", "MAX_LEVEL_DIGITS",
    "MAX_NOTE_LENGTH", "AlarmCondition", "AlarmDelivery", "AlarmDepartment", "AlarmRecord", "AlarmRelation",
    "AlarmRequest", "AlarmStatus", "BatchStatus", "CandlePrice", "CandlePriceCondition", "DeliveryBatch",
    "DeliveryKind", "IndicatorCondition", "QuotePrice", "QuotePriceCondition", "parse_condition",
]
