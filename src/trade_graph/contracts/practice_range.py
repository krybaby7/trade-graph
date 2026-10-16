"""The practice range's strategy grammar and study windows (T29, D119, D165).

A ``PracticeStrategy`` is a registered, versioned data artifact: it names indicators from the T27 menu with bounded
settings, compares them with the latest completed candle or a fixed level, and states a long-only entry order, a
protective exit and a size. It holds no code, formula or expression, and nothing in it can choose a side, borrow or
size above the account (D127). The replay engine (``application/practice_range``) is the only thing that reads it.

A ``StudyPlan`` fixes, before any replay, an earlier tuning window and a later confirmation window of whole UTC
days. The confirmation window must start no earlier than the tuning window ends. The store refuses to tune a
strategy family on any day that one of its studies set aside for confirmation, and binds each confirmation window to
the first strategy version confirmed on it (D166).

Prices, fractions and money are exact decimals given as text; a binary float is refused.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    field_serializer,
    field_validator,
    model_validator,
)

from trade_graph.contracts.indicators import get_indicator
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.money import canonical_decimal, parse_decimal

SCHEMA_VERSION = 1
REPLAY_INTERVALS = (1, 5, 15)  # D119: 15-minute bars or finer
MAX_INDICATORS = 8
MAX_CONDITIONS = 8
MAX_EXPIRY_BARS = 10_000
MAX_HOLDING_BARS = 100_000
THREE_MONTHS_DAYS = 90
Relation = Literal["above", "below", "crosses_above", "crosses_below"]
CandleField = Literal["open", "high", "low", "close", "volume"]
Phase = Literal["tune", "confirm"]
_KEY = r"^[a-z][a-z0-9_]{0,31}$"
_STRATEGY_ID = r"^[a-z0-9][a-z0-9-]{2,63}$"
_STUDY_ID = r"^[a-z0-9][a-z0-9._-]{2,63}$"
_DECIMAL_TEXT_LIMIT = 40

REPORT_LABEL: dict[str, Any] = {
    "kind": "evidence_for_trader_discretion",
    "statement": ("A practice-range report is evidence the Trader reads and weighs. It is not a pass mark, "
                  "not a claim that the strategy is profitable, and not permission to trade real money: working "
                  "software, economic evidence and permission for real money are separate gates (D138 item 5)."),
    "pass_mark": None,
    "live_authorization": False,
}


def _decimal(value: Any) -> Decimal:
    if isinstance(value, str) and len(value) > _DECIMAL_TEXT_LIMIT:
        raise ValueError("decimal text is too long")
    try:
        parsed = parse_decimal(value)
    except (ValueError, ArithmeticError) as error:
        raise ValueError(f"an exact decimal is required ({error})") from None
    return parsed


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CandleOperand(_Record):
    """A field of the latest completed candle at the decision."""

    candle: CandleField


class IndicatorOperand(_Record):
    """The newest value of a declared indicator at the decision; multi-output entries name their output."""

    indicator: str = Field(pattern=_KEY)
    output: str | None = Field(default=None, pattern=_KEY)


class LevelOperand(_Record):
    level: Decimal

    @field_validator("level", mode="before")
    @classmethod
    def _level(cls, value: Any) -> Decimal:
        level = _decimal(value)
        if level <= 0:
            raise ValueError("a price or indicator level here is positive")
        return level

    @field_serializer("level")
    def _serialize(self, value: Decimal) -> str:
        return canonical_decimal(value)


Operand = CandleOperand | IndicatorOperand | LevelOperand


class Condition(_Record):
    """``left relation right``, compared as the alarm engine compares: both sides rounded to 12 significant digits.

    ``crosses_above`` is previous left <= previous right and left > right; ``crosses_below`` mirrors it.
    """

    left: Operand
    relation: Relation
    right: Operand

    @model_validator(mode="after")
    def _not_constant(self) -> Condition:
        if isinstance(self.left, LevelOperand) and isinstance(self.right, LevelOperand):
            raise ValueError("a condition compares at least one market value")
        return self


class IndicatorUse(_Record):
    indicator: str
    parameters: dict[str, StrictInt | StrictFloat] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _on_the_menu(self) -> IndicatorUse:
        try:
            get_indicator(self.indicator).resolve_parameters(self.parameters)
        except ValidationFailure as error:
            raise ValueError(str(error)) from None
        return self

    @property
    def resolved_parameters(self) -> dict[str, int | float]:
        return get_indicator(self.indicator).resolve_parameters(self.parameters)


class EntryOrder(_Record):
    """A buy. Market: the next candle's open. Limit: ``price`` less ``offset_fraction``, resting until it expires."""

    kind: Literal["market", "limit"]
    price: Operand | None = None
    offset_fraction: Decimal = Decimal("0")
    expires_after_bars: int = Field(default=1, ge=1, le=MAX_EXPIRY_BARS, strict=True)

    @field_validator("offset_fraction", mode="before")
    @classmethod
    def _offset(cls, value: Any) -> Decimal:
        offset = _decimal(value)
        if not Decimal("0") <= offset < Decimal("0.5"):
            raise ValueError("a limit offset lies from 0 up to 50%")
        return offset

    @field_serializer("offset_fraction")
    def _serialize(self, value: Decimal) -> str:
        return canonical_decimal(value)

    @model_validator(mode="after")
    def _priced(self) -> EntryOrder:
        if self.kind == "limit" and self.price is None:
            raise ValueError("a limit entry names the price it rests at")
        if self.kind == "market" and (self.price is not None or self.offset_fraction != 0):
            raise ValueError("a market entry has no price or offset")
        if isinstance(self.price, CandleOperand) and self.price.candle == "volume":
            raise ValueError("a volume is not a price")
        return self


class Entry(_Record):
    all_of: tuple[Condition, ...] = Field(min_length=1, max_length=MAX_CONDITIONS)
    order: EntryOrder


class Exit(_Record):
    """Every position carries a protective exit while the Trader sleeps (D117): a stop, optionally a target."""

    stop_loss_fraction: Decimal
    take_profit_fraction: Decimal | None = None
    any_of: tuple[Condition, ...] = Field(default=(), max_length=MAX_CONDITIONS)
    max_holding_bars: int | None = Field(default=None, ge=1, le=MAX_HOLDING_BARS, strict=True)

    @field_validator("stop_loss_fraction", mode="before")
    @classmethod
    def _stop(cls, value: Any) -> Decimal:
        stop = _decimal(value)
        if not Decimal("0") < stop < Decimal("0.5"):
            raise ValueError("a stop sits between 0 and 50% below the entry")
        return stop

    @field_validator("take_profit_fraction", mode="before")
    @classmethod
    def _target(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        target = _decimal(value)
        if not Decimal("0") < target <= Decimal("10"):
            raise ValueError("a take-profit sits above the entry, at most 1000% higher")
        return target

    @field_serializer("stop_loss_fraction", "take_profit_fraction")
    def _serialize(self, value: Decimal | None) -> str | None:
        return None if value is None else canonical_decimal(value)


class Sizing(_Record):
    """Each entry spends at most this share of the account's equity at the decision. No borrowing (D127)."""

    equity_fraction: Decimal

    @field_validator("equity_fraction", mode="before")
    @classmethod
    def _fraction(cls, value: Any) -> Decimal:
        fraction = _decimal(value)
        if not Decimal("0") < fraction <= Decimal("1"):
            raise ValueError("an entry spends more than 0 and at most 100% of equity; there is no leverage")
        return fraction

    @field_serializer("equity_fraction")
    def _serialize(self, value: Decimal) -> str:
        return canonical_decimal(value)


class PracticeStrategy(_Record):
    schema_version: Literal[1]
    strategy_id: str = Field(pattern=_STRATEGY_ID)
    version: int = Field(ge=1, le=1_000_000, strict=True)
    title: str = Field(min_length=3, max_length=120)
    hypothesis: str = Field(min_length=10, max_length=600)
    interval_minutes: Literal[1, 5, 15]
    indicators: dict[str, IndicatorUse] = Field(default_factory=dict, max_length=MAX_INDICATORS)
    entry: Entry
    exit: Exit
    sizing: Sizing
    note: str = Field(default="", max_length=1200)

    @field_validator("indicators")
    @classmethod
    def _keys(cls, value: dict[str, IndicatorUse]) -> dict[str, IndicatorUse]:
        if any(not re.match(_KEY, key) for key in value):
            raise ValueError("indicator names are lowercase words")
        return value

    @model_validator(mode="after")
    def _references(self) -> PracticeStrategy:
        used: set[str] = set()
        operands: list[Operand] = []
        for condition in (*self.entry.all_of, *self.exit.any_of):
            operands.extend((condition.left, condition.right))
        if self.entry.order.price is not None:
            operands.append(self.entry.order.price)
        for operand in operands:
            if not isinstance(operand, IndicatorOperand):
                continue
            use = self.indicators.get(operand.indicator)
            if use is None:
                raise ValueError(f"no indicator named {operand.indicator!r} is declared")
            outputs = get_indicator(use.indicator).output_names
            if operand.output is None and len(outputs) != 1:
                raise ValueError(f"{use.indicator} has several outputs ({', '.join(outputs)}); name one")
            if operand.output is not None and operand.output not in outputs:
                raise ValueError(f"{use.indicator} has no output {operand.output!r}")
            used.add(operand.indicator)
        unused = sorted(set(self.indicators) - used)
        if unused:
            raise ValueError(f"declared indicators are never read: {', '.join(unused)}")
        return self

    @property
    def ref(self) -> str:
        return f"{self.strategy_id}@{self.version}"

    def canonical_document(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @property
    def content_sha256(self) -> str:
        text = json.dumps(self.canonical_document(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(text.encode("ascii")).hexdigest()

    def indicator_uses(self) -> dict[str, IndicatorUse]:
        return dict(self.indicators)


class ReplayWindow(_Record):
    """Whole UTC days, ``start`` included, ``end`` excluded (the trading day starts at 00:00 UTC, D138 item 6)."""

    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> ReplayWindow:
        if self.end <= self.start:
            raise ValueError("a window ends after it starts")
        return self

    @property
    def days(self) -> int:
        return (self.end - self.start).days

    @property
    def start_utc(self) -> datetime:
        return datetime.combine(self.start, time(0), tzinfo=UTC)

    @property
    def end_utc(self) -> datetime:
        return datetime.combine(self.end, time(0), tzinfo=UTC)

    def overlaps(self, other: ReplayWindow) -> bool:
        return self.start < other.end and other.start < self.end

    def contains(self, other: ReplayWindow) -> bool:
        return self.start <= other.start and other.end <= self.end

    def bars(self, interval_minutes: int) -> int:
        return self.days * 24 * 60 // interval_minutes

    def to_document(self) -> dict[str, Any]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "days": self.days,
                "end_is_exclusive": True}


class StudyPlan(_Record):
    study_id: str = Field(pattern=_STUDY_ID)
    strategy_id: str = Field(pattern=_STRATEGY_ID)
    tune: ReplayWindow
    confirm: ReplayWindow
    note: str = Field(default="", max_length=600)

    @model_validator(mode="after")
    def _later(self) -> StudyPlan:
        if self.confirm.start < self.tune.end:
            raise ValueError("the confirmation window starts no earlier than the tuning window ends (later, untouched)")
        return self

    @property
    def replayed_days(self) -> int:
        return self.tune.days + self.confirm.days

    @property
    def meets_three_month_minimum(self) -> bool:
        """D119 asks for at least three months of history; read here as 90 replayed days, tuning plus confirming."""
        return self.replayed_days >= THREE_MONTHS_DAYS

    def window(self, phase: Phase) -> ReplayWindow:
        return self.tune if phase == "tune" else self.confirm

    def to_document(self) -> dict[str, Any]:
        return {"study_id": self.study_id, "strategy_id": self.strategy_id, "tune": self.tune.to_document(),
                "confirm": self.confirm.to_document(), "replayed_days": self.replayed_days,
                "meets_three_month_minimum": self.meets_three_month_minimum, "note": self.note}


__all__ = [
    "MAX_CONDITIONS", "MAX_INDICATORS", "REPLAY_INTERVALS", "REPORT_LABEL", "SCHEMA_VERSION", "THREE_MONTHS_DAYS",
    "CandleOperand", "Condition", "Entry", "EntryOrder", "Exit", "IndicatorOperand", "IndicatorUse", "LevelOperand",
    "Operand", "Phase", "PracticeStrategy", "Relation", "ReplayWindow", "Sizing", "StudyPlan",
]
