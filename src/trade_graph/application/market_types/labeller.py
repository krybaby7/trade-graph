"""Market-type labels from stored candles: the readings the kernel decides on (W11-A, C10; D285, D286).

``kernel/market_type.py`` decides; this module supplies its inputs. For every candle slot of the rules' interval it
gives the candle's close and the four menu readings the rules name (the average, ADX, +DI, -DI), each exactly what the
indicator engine gives for that candle (``rolling_latest``, the engine's own span, so float for float the live
engine's value, D136), rounded half-even to the 12 significant digits comparisons use. No indicator formula is
written here (D118).

- ``label_coin`` is the live label: the window that ends at the moment's candle, read point-in-time (only candles
  received by the moment, D132), just long enough for the indicators and the lag.
- ``read_timeline`` reads a longer stretch once and answers any moment in it; the practice-range replay uses it for
  the labels at its decisions. Both end in ``slot_verdicts`` and ``MarketTypeTimeline.at`` with the same rules, so a
  replay sees the label the live function gives on the same candles.

Indicator values are floats from the library; prices stay ``Decimal``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any

from trade_graph.application.candle_store import CandleStore
from trade_graph.application.indicators.engine import plan_history, spec_version
from trade_graph.application.indicators.library import library_versions, rounded_decimal, rounded_significant
from trade_graph.application.indicators.rolling import rolling_latest
from trade_graph.contracts.candles import CandleWindow, ReadMode, align_close_time, to_micros
from trade_graph.contracts.indicators import get_indicator
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import ValidationFailure
from trade_graph.kernel.market_type import (
    CURRENT_MARKET_TYPE_RULES,
    MarketTypeLabel,
    MarketTypeRules,
    MarketTypeTimeline,
    SlotFacts,
    resolve_market_type_rules,
    slot_verdicts,
)

# The collector's hourly series (D133, D170): the live labels read the same candles as the hourly scan.
LIVE_SOURCE = "kraken_public_ohlc"
# Weakest first, as the coin scanner orders them: a label is only as real as its least real candle run.
EVIDENCE_ORDER = ("synthetic_fixture", "recorded_public_fixture", "local_archive_file", "live_public_smoke",
                  "live_public")
NO_CANDLES = "no_candles"


def required_history(rules: MarketTypeRules) -> int:
    """How many complete candles in a row a slot's label reads: the longest indicator span, and the average's span
    plus its slope (the average a slope ago is read too)."""
    spans = {role: plan_history(name, parameters, 1).span
             for role, (name, parameters) in rules.indicator_settings().items()}
    return max(spans["strength"], spans["plus"], spans["minus"], spans["average"] + rules.slope_bars)


def indicator_document(rules: MarketTypeRules) -> dict[str, Any]:
    """The menu entries behind a label, with their menu versions and spans (a label names what it read)."""
    document = {}
    for role, (name, parameters) in rules.indicator_settings().items():
        spec = get_indicator(name)
        document[role] = {"indicator": name, "parameters": dict(spec.resolve_parameters(parameters)),
                          "spec_version": spec_version(spec), "span": plan_history(name, parameters, 1).span}
    return document


def slot_facts(window: CandleWindow, rules: MarketTypeRules) -> tuple[SlotFacts, ...]:
    """Each slot's candle close and readings; ``None`` where the engine gives no value (warming up or gapped)."""
    if window.interval_minutes != rules.interval_minutes:
        raise ValidationFailure(f"the rules read {rules.interval_minutes}-minute candles, not "
                                f"{window.interval_minutes}-minute ones")
    series = {role: rolling_latest(name, parameters, window)
              for role, (name, parameters) in rules.indicator_settings().items()}

    def reading(role: str, index: int):
        value = series[role].at(index)
        return None if value is None else rounded_decimal(value)

    facts = []
    for index, slot in enumerate(window.slots):
        if slot.stored is None:
            facts.append(SlotFacts(close_time=slot.close_time_utc, present=False))
            continue
        facts.append(SlotFacts(close_time=slot.close_time_utc, present=True,
                               close=rounded_significant(slot.stored.candle.close),
                               average=reading("average", index), strength=reading("strength", index),
                               plus=reading("plus", index), minus=reading("minus", index)))
    return tuple(facts)


def _digest(window: CandleWindow, first: int, last: int) -> str:
    """The indicator engine's input hash scheme over the slots a label read: revision, run and content per candle."""
    document = [[to_micros(slot.close_time_utc), slot.stored.revision, slot.stored.run_id,
                 slot.stored.candle.content_hash] for slot in window.slots[first:last + 1] if slot.stored is not None]
    return hashlib.sha256(json.dumps(document, separators=(",", ":")).encode("ascii")).hexdigest()


def weakest_evidence(labels: set[str] | frozenset[str] | tuple[str, ...]) -> str:
    known = [label for label in EVIDENCE_ORDER if label in labels]
    if known:
        return known[0]
    return sorted(labels)[0] if labels else NO_CANDLES


@dataclass(frozen=True, slots=True)
class LabelledWindow:
    """A window of the rules' candles with every slot's verdict; ``at`` answers any moment it covers."""

    window: CandleWindow
    rules: MarketTypeRules
    history: int
    timeline: MarketTypeTimeline
    _index: Mapping[datetime, int] = field(repr=False)

    def at(self, moment: datetime) -> MarketTypeLabel:
        return self.timeline.at(moment)

    def candles_behind(self, label: MarketTypeLabel, run_labels: Mapping[int, str]) -> dict[str, Any]:
        """The candles a label rests on: the ``history`` slots ending at its candle (none when it has none)."""
        if label.candle_close is None or label.candle_close not in self._index:
            first = last = -1
            used = []
        else:
            last = self._index[label.candle_close]
            first = max(0, last - self.history + 1)
            used = [slot.stored for slot in self.window.slots[first:last + 1] if slot.stored is not None]
        runs = sorted({stored.run_id for stored in used})
        return {"symbol": self.window.symbol, "source": self.window.source,
                "interval_minutes": self.window.interval_minutes, "read_mode": self.window.mode,
                "as_of_utc": utc_iso(self.window.as_of_utc), "history_candles": self.history,
                "candles_read": len(used), "run_ids": runs,
                "evidence_labels": sorted({run_labels.get(run, "unknown") for run in runs}),
                "input_sha256": _digest(self.window, first, last) if used else None}

    def provenance(self, run_labels: Mapping[int, str]) -> dict[str, Any]:
        """Every candle the window read (a replay's whole stretch of label candles)."""
        present = [slot.stored for slot in self.window.slots if slot.stored is not None]
        runs = sorted({stored.run_id for stored in present})
        return {"symbol": self.window.symbol, "source": self.window.source,
                "interval_minutes": self.window.interval_minutes, "read_mode": self.window.mode,
                "slots_read": len(self.window.slots), "candles_read": len(present), "run_ids": runs,
                "evidence_labels": sorted({run_labels.get(run, "unknown") for run in runs}),
                "input_sha256": _digest(self.window, 0, len(self.window.slots) - 1)}


def labelled_window(window: CandleWindow, rules: MarketTypeRules) -> LabelledWindow:
    history = required_history(rules)
    verdicts = slot_verdicts(slot_facts(window, rules), rules, history=history)
    return LabelledWindow(window=window, rules=rules, history=history,
                          timeline=MarketTypeTimeline(verdicts, rules),
                          _index=MappingProxyType({slot.close_time_utc: index
                                                   for index, slot in enumerate(window.slots)}))


def _aware(label: str, value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationFailure(f"{label} must be a timezone-aware datetime")
    return value


def read_timeline(store: CandleStore, symbol: str, source: str, *, end_close: datetime, slots: int,
                  as_of: datetime, rules: MarketTypeRules = CURRENT_MARKET_TYPE_RULES,
                  mode: ReadMode = "point_in_time") -> LabelledWindow:
    """Labels for the ``slots`` candles ending at ``end_close`` and any moment up to the lag after them: reads the
    history and lag before the first one too, so every one of them is labelled from its full history."""
    _aware("end_close", end_close)
    _aware("as_of", as_of)
    if end_close != align_close_time(end_close, rules.interval_minutes) or end_close.microsecond:
        raise ValidationFailure(f"the last slot closes on the {rules.interval_minutes}-minute grid")
    if type(slots) is not int or slots < 1:
        raise ValidationFailure("label at least one slot")
    count = slots + required_history(rules) - 1 + rules.max_lag_bars
    window = store.window(symbol, rules.interval_minutes, source, end_close_time=end_close, count=count, as_of=as_of,
                          mode=mode)
    return labelled_window(window, rules)


@dataclass(frozen=True, slots=True)
class CoinLabel:
    """One coin's label at a moment, with the candles and menu entries it rests on."""

    symbol: str
    label: MarketTypeLabel
    candles: Mapping[str, Any]
    indicators: Mapping[str, Any]
    library: Mapping[str, str]

    @property
    def market_type(self) -> str:
        return self.label.market_type

    @property
    def evidence(self) -> str:
        return weakest_evidence(tuple(self.candles.get("evidence_labels", ())))

    def to_document(self) -> dict[str, Any]:
        return {"symbol": self.symbol, **self.label.to_document(), "candles": dict(self.candles),
                "indicators": dict(self.indicators), "library": dict(self.library)}

    def sentence(self) -> str:
        return describe(self.to_document())


def label_coin(store: CandleStore, *, symbol: str, moment: datetime,
               rules: MarketTypeRules = CURRENT_MARKET_TYPE_RULES, source: str = LIVE_SOURCE,
               mode: ReadMode = "point_in_time", run_labels: Mapping[int, str] | None = None) -> CoinLabel:
    """The live label: only candles that closed, and (point-in-time) were received, by ``moment``."""
    moment = _aware("the moment", moment)
    end = align_close_time(moment, rules.interval_minutes)
    labelled = read_timeline(store, symbol, source, end_close=end, slots=1, as_of=moment, rules=rules, mode=mode)
    label = labelled.at(moment)
    known = run_labels if run_labels is not None else {run.run_id: run.evidence_label for run in store.runs()}
    return CoinLabel(symbol=symbol, label=label, candles=MappingProxyType(labelled.candles_behind(label, known)),
                     indicators=MappingProxyType(indicator_document(rules)),
                     library=MappingProxyType(library_versions()))


# --- plain words ----------------------------------------------------------------------------------------------------

_UNCLEAR_WORDS = {
    "no_recent_candle": "no recent candle",
    "not_enough_candles": "not enough candles in a row yet",
    "gap": "a candle is missing in the history it reads",
    "no_value": "an indicator gave no value",
    "evaluation_failed": "the candles could not be read",
}


def _hours(count: int, interval_minutes: int) -> str:
    hours = count * interval_minutes / 60
    return f"{hours:g}-hour"


def describe(document: Mapping[str, Any]) -> str:
    """One plain sentence for a stored or fresh label document (display only; the document stays exact)."""
    symbol = document.get("symbol", "the coin")
    market_type = document["market_type"]
    try:
        rules = resolve_market_type_rules(document["rules"]["ref"])
    except (KeyError, TypeError, ValidationFailure):
        rules = None
    closed = document.get("candle_close_utc")
    when = f" (candle closed {closed[:16].replace('T', ' ')} UTC)" if closed else ""
    if market_type == "unclear":
        reason = document.get("reason")
        words = _UNCLEAR_WORDS.get(reason, reason or "no reason recorded")
        if reason == "no_recent_candle" and rules is not None:
            words = f"no {rules.interval_minutes}-minute candle in the last {rules.max_lag_bars + 1} intervals"
        if reason in ("not_enough_candles", "gap") and document.get("candles", {}).get("history_candles"):
            history = document["candles"]["history_candles"]
            words = (f"fewer than {history} complete candles in a row" if reason == "not_enough_candles"
                     else f"a candle is missing among the last {history}")
        return f"{symbol} is unclear: {words}{when}."
    checks, values = document.get("checks", {}), document.get("values", {})
    if rules is None:
        return f"{symbol} is {market_type}{when}."
    side = {"buyers": "buyers ahead", "sellers": "sellers ahead", "even": "neither side ahead"}
    price = {"above_average": "above", "below_average": "below", "at_average": "at"}
    average = {"rising": "higher than", "falling": "lower than", "flat": "level with"}
    strength = "a trend" if checks.get("strength") == "trending" else "no clear trend"
    return (f"{symbol} is {market_type}: {strength} (ADX {values.get('strength')}; a trend counts from "
            f"{rules.trending_strength}), {side.get(checks.get('pressure'), '?')} (+DI {values.get('plus')} against "
            f"-DI {values.get('minus')}), price {price.get(checks.get('price'), '?')} its "
            f"{_hours(rules.average_period, rules.interval_minutes)} average, which is "
            f"{average.get(checks.get('average'), '?')} {rules.slope_bars * rules.interval_minutes / 60:g} hours "
            f"earlier{when}.")


__all__ = [
    "EVIDENCE_ORDER", "LIVE_SOURCE", "NO_CANDLES", "CoinLabel", "LabelledWindow", "describe", "indicator_document",
    "label_coin", "labelled_window", "read_timeline", "required_history", "slot_facts", "weakest_evidence",
]
