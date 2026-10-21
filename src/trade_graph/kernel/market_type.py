"""Market types: one label per coin and moment, the same live and in replays (W11-A, C10; D280 item 3, D285-D289).

Software, not a model, says whether a coin is ``rising``, ``sideways``, ``falling`` or ``unclear``, from completed
candles only. The rules are a versioned document (``market-type-rules@1``, D285) naming the candle size they read and
four TA-Lib menu indicators on it (D118): a long simple moving average of the close, and ADX, +DI and -DI.

- **rising**: the trend is strong enough (ADX at or above ``trending_strength``), buyers are ahead (+DI above -DI),
  the close is above the average, and the average is higher than ``slope_bars`` candles earlier;
- **falling**: the mirror: ADX at or above the threshold, sellers ahead, the close below a falling average;
- **sideways**: everything else, a weak trend or signs that disagree;
- **unclear**: no basis for a label: no candle within ``max_lag_bars`` of the moment (``no_recent_candle``), fewer
  complete candles in a row than the indicators read (``not_enough_candles``), a missing candle among them (``gap``;
  nothing is carried forward, D136), an indicator without a value (``no_value``), or a read that failed
  (``evaluation_failed``).

Version 2 (``market-type-rules@2``, D230) keeps version 1's readings and adds one rule: a hole of at most
``max_bridged_gap_bars`` missing slots with a candle right before and right after it is skipped, the indicators reading
the candles on both sides as if they followed each other, as long as a label's look-back (the history plus
``max_bridged_total_bars``) holds at most ``max_bridged_total_bars`` skipped slots. Nothing is invented for the hole:
no candle is carried forward or filled in. A longer outage is still ``gap``. Version 1 (no skipping) is unchanged.

Each comparison is exact, with both sides rounded half-even to 12 significant digits first, as alarms compare (D136).

The application computes the indicator values per candle slot (``application/market_types/labeller.py``, with the
engine's own ``rolling_latest``) and hands them here as plain decimals; this module decides. The hourly scan's live
labels and the practice range's replays call ``slot_verdicts`` and ``MarketTypeTimeline.at`` with the same rules,
never a second copy (like ``day_plan_coverage.py``, D277), so a replay sees the labels the live function gives on the
same candles. A label at a moment uses only candles that closed at or before it, and a slot's verdict reads only the
slots that end at it, so later candles never change an earlier label.

Protected execution-adjacent rules (``kernel/``): agents cannot edit them. No indicator library is imported here.
"""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import MAX_EMAX, MIN_EMIN, ROUND_HALF_EVEN, Context, Decimal
from types import MappingProxyType
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from trade_graph.contracts.indicators import get_indicator
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.market_types import MARKET_TYPES, MarketType
from trade_graph.domain.money import canonical_decimal, parse_decimal
from trade_graph.kernel.trade_metrics import GroupResult, RoundTrip, split_results

RULES_ID: Final = "market-type-rules"
LABEL_INTERVALS: Final = (15, 60)
UnclearReason = Literal["no_recent_candle", "not_enough_candles", "gap", "no_value", "evaluation_failed"]
UNCLEAR_REASONS: Final[tuple[UnclearReason, ...]] = (
    "no_recent_candle", "not_enough_candles", "gap", "no_value", "evaluation_failed")
ROLES: Final = ("average", "strength", "plus", "minus")
COMPARISON_DIGITS: Final = 12
_TWELVE = Context(prec=COMPARISON_DIGITS, rounding=ROUND_HALF_EVEN, Emin=MIN_EMIN, Emax=MAX_EMAX)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_DECIMAL_TEXT_LIMIT = 40
_EMPTY: Mapping[str, str] = MappingProxyType({})


class MarketTypeRules(BaseModel):
    """A versioned, bounded rules document. A change of any value is a new version with a new hash."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    rules_id: Literal["market-type-rules"] = RULES_ID
    version: int = Field(ge=1, le=10_000, strict=True)
    interval_minutes: Literal[15, 60]
    average_period: int = Field(ge=20, le=500, strict=True)
    slope_bars: int = Field(ge=1, le=168, strict=True)
    strength_period: int = Field(ge=7, le=50, strict=True)
    trending_strength: Decimal
    max_lag_bars: int = Field(ge=0, le=6, strict=True)
    max_bridged_gap_bars: int = Field(default=0, ge=0, le=12, strict=True)
    max_bridged_total_bars: int = Field(default=0, ge=0, le=48, strict=True)
    note: str = Field(default="", max_length=600)

    @field_validator("interval_minutes", mode="before")
    @classmethod
    def _interval(cls, value: Any) -> int:
        if type(value) is not int or value not in LABEL_INTERVALS:
            raise ValueError(f"the rules read candles of {' or '.join(map(str, LABEL_INTERVALS))} minutes")
        return value

    @field_validator("trending_strength", mode="before")
    @classmethod
    def _strength(cls, value: Any) -> Decimal:
        if isinstance(value, str) and len(value) > _DECIMAL_TEXT_LIMIT:
            raise ValueError("decimal text is too long")
        try:
            level = parse_decimal(value)
        except (ValueError, ArithmeticError) as error:
            raise ValueError(f"an exact decimal is required ({error})") from None
        if not Decimal("10") <= level <= Decimal("40"):
            raise ValueError("the trend-strength level lies from 10 to 40 on ADX's 0 to 100 scale")
        return level

    @field_serializer("trending_strength")
    def _serialize(self, value: Decimal) -> str:
        return canonical_decimal(value)

    @model_validator(mode="after")
    def _on_the_menu(self) -> MarketTypeRules:
        for name, parameters in self.indicator_settings().values():
            try:
                get_indicator(name).resolve_parameters(parameters)
            except ValidationFailure as error:
                raise ValueError(str(error)) from None
        gap, total = self.max_bridged_gap_bars, self.max_bridged_total_bars
        if (gap == 0) != (total == 0) or total < gap:
            raise ValueError("skipping holes needs both a longest hole and a total of at least that many slots, "
                             "or neither")
        return self

    @property
    def ref(self) -> str:
        return f"{self.rules_id}@{self.version}"

    def indicator_settings(self) -> dict[str, tuple[str, dict[str, int]]]:
        """The menu entries the rules read, by role: the average and the trend strength with its two sides."""
        return {"average": ("SMA", {"timeperiod": self.average_period}),
                "strength": ("ADX", {"timeperiod": self.strength_period}),
                "plus": ("PLUS_DI", {"timeperiod": self.strength_period}),
                "minus": ("MINUS_DI", {"timeperiod": self.strength_period})}

    def canonical_document(self) -> dict[str, Any]:
        document = self.model_dump(mode="json")
        if not self.max_bridged_gap_bars:  # rules without skipping keep version 1's exact document and hash (D230)
            del document["max_bridged_gap_bars"], document["max_bridged_total_bars"]
        document["indicators"] = {role: {"indicator": name, "parameters": dict(parameters)}
                                  for role, (name, parameters) in self.indicator_settings().items()}
        return document

    @property
    def content_sha256(self) -> str:
        text = json.dumps(self.canonical_document(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(text.encode("ascii")).hexdigest()

    @property
    def step(self) -> timedelta:
        return timedelta(minutes=self.interval_minutes)

    def reference(self) -> dict[str, Any]:
        return {"ref": self.ref, "sha256": self.content_sha256, "interval_minutes": self.interval_minutes}


MARKET_TYPE_RULES_V1: Final = MarketTypeRules(
    version=1, interval_minutes=60, average_period=168, slope_bars=24, strength_period=14,
    trending_strength=Decimal("20"), max_lag_bars=2,
    note=("Hourly candles: the one-week average of the close (168 hours) and its change over one day (24 hours) give "
          "the direction; ADX 14 at 20 or more says a trend is present and +DI against -DI says which side leads "
          "(D285)."))
MARKET_TYPE_RULES_V2: Final = MarketTypeRules(
    version=2, interval_minutes=60, average_period=168, slope_bars=24, strength_period=14,
    trending_strength=Decimal("20"), max_lag_bars=2, max_bridged_gap_bars=6, max_bridged_total_bars=24,
    note=("Version 1's readings, and a hole of up to 6 missing hours with a candle on both sides is skipped, at most "
          "24 skipped hours among the hours one label looks back over, so one missed hour no longer leaves a coin "
          "unclear for 36 days (D230)."))
CURRENT_MARKET_TYPE_RULES: Final = MARKET_TYPE_RULES_V2
KNOWN_MARKET_TYPE_RULES: Final[Mapping[str, MarketTypeRules]] = MappingProxyType(
    {rules.ref: rules for rules in (MARKET_TYPE_RULES_V1, MARKET_TYPE_RULES_V2)})


def resolve_market_type_rules(ref: str) -> MarketTypeRules:
    """A known rules version by its reference (``market-type-rules@1``); stored labels and reports name theirs."""
    rules = KNOWN_MARKET_TYPE_RULES.get(ref) if isinstance(ref, str) else None
    if rules is None:
        raise ValidationFailure(f"no market-type rules {ref!r} are known")
    return rules


def _rounded(label: str, value: object) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise ValidationFailure(f"{label} must be a finite Decimal")
    return _TWELVE.plus(value)


def classify(*, close: Decimal, average: Decimal, average_before: Decimal, strength: Decimal, plus: Decimal,
             minus: Decimal, rules: MarketTypeRules) -> tuple[MarketType, dict[str, str]]:
    """Rising, falling or sideways from one candle's readings, with the four checks behind it in words."""
    close, average, before, strength, plus, minus = (
        _rounded(name, value) for name, value in (("close", close), ("average", average),
                                                  ("average_before", average_before), ("strength", strength),
                                                  ("plus", plus), ("minus", minus)))
    trending = strength >= _rounded("trending_strength", rules.trending_strength)
    checks = {
        "strength": "trending" if trending else "weak",
        "pressure": "buyers" if plus > minus else "sellers" if minus > plus else "even",
        "price": "above_average" if close > average else "below_average" if close < average else "at_average",
        "average": "rising" if average > before else "falling" if average < before else "flat",
    }
    if trending and (checks["pressure"], checks["price"], checks["average"]) == ("buyers", "above_average", "rising"):
        return "rising", checks
    if trending and (checks["pressure"], checks["price"], checks["average"]) == ("sellers", "below_average",
                                                                                  "falling"):
        return "falling", checks
    return "sideways", checks


@dataclass(frozen=True, slots=True)
class SlotFacts:
    """One candle slot of the rules' interval: is its candle there, and the readings the engine gives at it."""

    close_time: datetime
    present: bool
    close: Decimal | None = None
    average: Decimal | None = None
    strength: Decimal | None = None
    plus: Decimal | None = None
    minus: Decimal | None = None


@dataclass(frozen=True, slots=True)
class SlotVerdict:
    """The label a slot's candle supports. A slot without a candle has none (``market_type`` None)."""

    close_time: datetime
    present: bool
    market_type: MarketType | None
    reason: UnclearReason | None = None
    checks: Mapping[str, str] = field(default=_EMPTY)
    values: Mapping[str, str] = field(default=_EMPTY)


def _consecutive(close_times: Sequence[datetime], rules: MarketTypeRules) -> None:
    step = rules.step
    for at in close_times[:1]:
        if at.tzinfo is None or (at - _EPOCH) % step:
            raise ValidationFailure("slots are consecutive candles of the rules' interval, on its time grid")
    for earlier, later in zip(close_times, close_times[1:]):
        if later - earlier != step:
            raise ValidationFailure("slots are consecutive candles of the rules' interval, on its time grid")


def look_back_slots(rules: MarketTypeRules, *, history: int) -> int:
    """How many slots ending at a slot its label depends on: the history, and the skipped slots the rules allow."""
    return history + rules.max_bridged_total_bars


def bridged_slots(present: Sequence[bool], rules: MarketTypeRules) -> tuple[bool, ...]:
    """For each slot, whether it is a missing slot the rules skip: a run of at most ``max_bridged_gap_bars`` missing
    slots with a candle right before and right after it. A run at the start (the series, or the read, began later) or
    at the end (no candle after it yet) is never skipped. Version 1 skips nothing."""
    flags = [bool(item) for item in present]
    skipped = [False] * len(flags)
    limit = rules.max_bridged_gap_bars
    index = 0
    while limit and index < len(flags):
        if flags[index]:
            index += 1
            continue
        end = index
        while end < len(flags) and not flags[end]:
            end += 1
        if index > 0 and end < len(flags) and end - index <= limit:
            skipped[index:end] = [True] * (end - index)
        index = end
    return tuple(skipped)


def _bridged_history(present: Sequence[bool], index: int, rules: MarketTypeRules, history: int,
                     ) -> tuple[UnclearReason | None, int | None]:
    """Version 2, for a present slot: why it is unclear, or None and the slot whose average is read ``slope_bars``
    candles earlier. Only the look-back slots ending at it are read (slots before the series count as missing), so a
    short live read and a long replay give the same answer."""
    first = index - look_back_slots(rules, history=history) + 1
    local = [False] * max(0, -first) + [bool(flag) for flag in present[max(0, first):index + 1]]
    skipped = bridged_slots(local, rules)
    kept = [position for position, flag in enumerate(skipped) if not flag]
    if len(kept) < history:
        return "gap", None  # more skipped slots in the look-back than the rules allow
    used = kept[-history:]
    missing = [position for position in used if not local[position]]
    if missing:
        first_present = next(position for position in used if local[position])
        return ("gap" if missing[-1] > first_present else "not_enough_candles"), None
    return None, first + used[-1 - rules.slope_bars]


def slot_verdicts(slots: Sequence[SlotFacts], rules: MarketTypeRules, *, history: int) -> tuple[SlotVerdict, ...]:
    """Each slot's verdict from the ``history`` slots ending at it (the candles its indicators read).

    A slot reads nothing outside those slots, so its verdict is the same in a short live read and a long replay, and
    later candles never change it. ``not_enough_candles``: the missing slots are all at the start of the history (the
    series began inside it, or a hole reaches back past its start); ``gap``: a slot is missing after a present one.
    Version 2 first skips the short holes ``bridged_slots`` names, within the slot's ``look_back_slots`` (D230).
    """
    if type(history) is not int or history <= rules.slope_bars:
        raise ValidationFailure("the history a label reads covers at least the average's slope and one more candle")
    facts = tuple(slots)
    _consecutive([item.close_time for item in facts], rules)
    present = [item.present for item in facts]
    reach = look_back_slots(rules, history=history)
    missing_before = [0]
    for item in facts:
        missing_before.append(missing_before[-1] + (not item.present))
    next_present = [len(facts)] * (len(facts) + 1)
    for index in range(len(facts) - 1, -1, -1):
        next_present[index] = index if facts[index].present else next_present[index + 1]
    verdicts: list[SlotVerdict] = []
    for index, item in enumerate(facts):
        if not item.present:
            verdicts.append(SlotVerdict(close_time=item.close_time, present=False, market_type=None))
            continue
        reason: UnclearReason | None = None
        before_index: int | None = index - rules.slope_bars
        if rules.max_bridged_gap_bars:
            look_back = index - reach + 1
            if look_back < 0 or missing_before[index + 1] - missing_before[look_back]:
                reason, before_index = _bridged_history(present, index, rules, history)
        else:
            start = index - history + 1
            if start < 0:
                reason = "not_enough_candles"
            elif missing_before[index + 1] - missing_before[start]:
                first = next_present[start]
                reason = "gap" if missing_before[index + 1] - missing_before[first] else "not_enough_candles"
        if reason is not None or before_index is None:
            verdicts.append(SlotVerdict(close_time=item.close_time, present=True, market_type="unclear",
                                        reason=reason))
            continue
        before = facts[before_index].average
        readings = {"close": item.close, "average": item.average, "average_before": before,
                    "strength": item.strength, "plus": item.plus, "minus": item.minus}
        if any(value is None for value in readings.values()):
            verdicts.append(SlotVerdict(close_time=item.close_time, present=True, market_type="unclear",
                                        reason="no_value"))
            continue
        market_type, checks = classify(rules=rules, **readings)  # type: ignore[arg-type]
        values = {name: canonical_decimal(_rounded(name, value)) for name, value in readings.items()}
        verdicts.append(SlotVerdict(close_time=item.close_time, present=True, market_type=market_type,
                                    checks=MappingProxyType(checks), values=MappingProxyType(values)))
    return tuple(verdicts)


@dataclass(frozen=True, slots=True)
class MarketTypeLabel:
    """A coin's market type at a moment, with the candle it rests on, how far behind that candle was, and why."""

    market_type: MarketType
    moment: datetime
    rules: MarketTypeRules
    candle_close: datetime | None
    lag_bars: int | None
    reason: UnclearReason | None = None
    checks: Mapping[str, str] = field(default=_EMPTY)
    values: Mapping[str, str] = field(default=_EMPTY)

    def to_document(self) -> dict[str, Any]:
        return {"market_type": self.market_type, "reason": self.reason, "moment_utc": utc_iso(self.moment),
                "candle_close_utc": None if self.candle_close is None else utc_iso(self.candle_close),
                "lag_bars": self.lag_bars, "rules": self.rules.reference(), "checks": dict(self.checks),
                "values": dict(self.values)}


def _aware(moment: object) -> datetime:
    if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
        raise ValidationFailure("a label's moment must be a timezone-aware datetime")
    return moment


def unclear_label(moment: datetime, rules: MarketTypeRules, reason: UnclearReason = "evaluation_failed",
                  ) -> MarketTypeLabel:
    """The label when nothing could be read for the coin (a refused read): unclear, resting on no candle."""
    if reason not in UNCLEAR_REASONS:
        raise ValidationFailure("an unclear label names one of the fixed reasons")
    return MarketTypeLabel(market_type="unclear", moment=_aware(moment), rules=rules, candle_close=None,
                           lag_bars=None, reason=reason)


class MarketTypeTimeline:
    """Slot verdicts over a stretch of time, read at any moment: the newest candle closed by then, within the lag."""

    def __init__(self, verdicts: Sequence[SlotVerdict], rules: MarketTypeRules) -> None:
        self.verdicts = tuple(verdicts)
        self.rules = rules
        self._closes = [item.close_time for item in self.verdicts]
        _consecutive(self._closes, rules)
        newest = -1
        self._newest_present: list[int] = []
        for index, item in enumerate(self.verdicts):
            newest = index if item.present else newest
            self._newest_present.append(newest)

    def at(self, moment: datetime) -> MarketTypeLabel:
        moment = _aware(moment)
        rules = self.rules
        step = rules.step
        target = _EPOCH + ((moment - _EPOCH) // step) * step  # the newest close at or before the moment
        index = bisect_right(self._closes, moment) - 1
        chosen = self._newest_present[index] if index >= 0 else -1
        if chosen < 0:
            return MarketTypeLabel(market_type="unclear", moment=moment, rules=rules, candle_close=None,
                                   lag_bars=None, reason="no_recent_candle")
        verdict = self.verdicts[chosen]
        lag = (target - verdict.close_time) // step
        if lag > rules.max_lag_bars:
            return MarketTypeLabel(market_type="unclear", moment=moment, rules=rules,
                                   candle_close=verdict.close_time, lag_bars=lag, reason="no_recent_candle")
        assert verdict.market_type is not None
        return MarketTypeLabel(market_type=verdict.market_type, moment=moment, rules=rules,
                               candle_close=verdict.close_time, lag_bars=lag, reason=verdict.reason,
                               checks=verdict.checks, values=verdict.values)


def label_at(moment: datetime, verdicts: Sequence[SlotVerdict], rules: MarketTypeRules) -> MarketTypeLabel:
    """One label at one moment (``MarketTypeTimeline`` reads many moments from the same verdicts)."""
    return MarketTypeTimeline(verdicts, rules).at(moment)


def split_by_market_type(trips: Sequence[tuple[MarketType, RoundTrip]],
                         open_positions: Sequence[tuple[MarketType, Decimal]] = ()) -> tuple[GroupResult, ...]:
    """A report's trades and result after fees by the market type at entry, in the order rising, sideways, falling,
    unclear; the rows sum to the report's totals (``kernel/trade_metrics.split_results``)."""
    return split_results(MARKET_TYPES, trips, open_positions)


__all__ = [
    "COMPARISON_DIGITS", "CURRENT_MARKET_TYPE_RULES", "KNOWN_MARKET_TYPE_RULES", "LABEL_INTERVALS",
    "MARKET_TYPE_RULES_V1", "MARKET_TYPE_RULES_V2", "RULES_ID", "ROLES", "UNCLEAR_REASONS", "MarketTypeLabel",
    "MarketTypeRules", "MarketTypeTimeline", "SlotFacts", "SlotVerdict", "UnclearReason", "bridged_slots", "classify",
    "label_at", "look_back_slots", "resolve_market_type_rules", "slot_verdicts", "split_by_market_type",
    "unclear_label",
]
