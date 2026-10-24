"""The engine around the indicator library: completed candles in, labelled numbers out (T27 slice B, D118).

No indicator formula is written here and nothing here imports the library. The engine

- asks the candle store (``CandleStore.window``, the only way candles are read) for exactly the slots one
  indicator needs, and asserts that every candle it receives had closed and been received by ``as_of``;
- sizes that span from the menu: the library's own lookback, plus the menu's measured settling history for
  entries whose value depends on where the history began (``Settling`` in ``contracts/indicators.py``), plus
  the requested number of output points. For those start-sensitive entries the span is padded to the same start
  for every number of points, so one candle always gets one value;
- withholds every value when any slot of the span is missing, naming the slots (no carry-forward, no zero
  fill, no partial window, and the span is never lengthened to step around a hole);
- labels the answer (status, mode, as-of, candle revisions and runs, library and menu versions);
- compares an answer with an alarm level at the twelve significant digits the library can vouch for.

Indicator values are floats. Prices, quantities, fees and balances stay ``Decimal`` everywhere else.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Literal

from trade_graph.application.candle_store import CandleStore
from trade_graph.application.indicators.library import (
    IndicatorComputationError,
    columns_from_candles,
    compute_raw,
    library_versions,
    lookback,
    rounded_decimal,
    rounded_significant,
)
from trade_graph.contracts.candles import AvailabilityBasis, CandleWindow, ReadMode, to_micros
from trade_graph.contracts.indicators import MENU_VERSION, IndicatorSpec, get_indicator
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import TradeGraphError, ValidationFailure

# One request returns at most this many output points; the span is bounded as well (the largest menu request,
# ADX at 500 with 500 points, needs 13,499 candles, well under the store's own 100,000-slot limit).
MAX_OUTPUT_POINTS = 500
MAX_HISTORY_CANDLES = 20_000
# Entries that are start-sensitive but have no Settling guidance (Parabolic SAR, On-Balance Volume) are given this
# many candles beyond the lookback. For SAR it is measured: with 200, two readings of a candle that start 1, 13 or
# 61 candles apart agree on every sampled candle of both test series, and with 50 they do not
# (tests/unit/test_indicator_settling.py). OBV is a running total, so no amount of history makes its level agree
# between starts (only its changes do); 200 is just the same depth. The result says ``start_dependent`` so nobody
# mistakes either level for one that settles.
START_DEPENDENT_HISTORY = 200
# Only this many missing close times are written into a document; the count is always complete.
DOCUMENT_MISSING_LIMIT = 100
# A decimal string longer than this many digits is not a level anyone set on purpose.
MAX_LEVEL_DIGITS = 64

IndicatorStatus = Literal["ready", "warming_up", "gapped"]
Relation = Literal["above", "below", "crosses_above", "crosses_below"]
RELATIONS: tuple[Relation, ...] = ("above", "below", "crosses_above", "crosses_below")
_STATUS_WORDS = {"ready": "ready", "warming_up": "warming up", "gapped": "gapped"}


class CandleSelectionError(TradeGraphError):
    """The store handed over something other than completed, already-received candles for the slots asked."""

    code = "candle_selection_invalid"


@dataclass(frozen=True, slots=True)
class HistoryPlan:
    """How many candles one request reads, and why."""

    lookback: int  # leading candles with no value, asked from the library for these settings
    # Extra candles before the first point's window for start-sensitive entries: the measured guidance, plus the
    # points not asked for, so that the span starts at the same candle whatever the number of points.
    seeding: int
    points: int  # output points requested

    @property
    def span(self) -> int:
        return self.lookback + self.seeding + self.points


@dataclass(frozen=True, slots=True)
class CandleUse:
    """One candle behind a result: which revision of which slot, from which collection run."""

    close_time_utc: datetime
    revision: int
    run_id: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class IndicatorResult:
    """Numbers and the facts that make them attributable. ``values`` is empty unless ``status`` is ready."""

    indicator: str
    parameters: Mapping[str, int | float]
    spec_version: str
    library: Mapping[str, str]
    symbol: str
    interval_minutes: int
    source: str
    mode: ReadMode
    availability_basis: AvailabilityBasis
    as_of_utc: datetime
    end_close_time_utc: datetime
    status: IndicatorStatus
    requested_points: int
    lookback_candles: int
    seeding_candles: int
    required_candles: int
    available_candles: int
    start_dependent: bool
    close_times_utc: tuple[datetime, ...]
    values: Mapping[str, tuple[float, ...]]
    missing_close_times_utc: tuple[datetime, ...]
    candles_used: tuple[CandleUse, ...]
    input_sha256: str

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"

    def latest(self, output: str | None = None) -> float | None:
        """The newest value of one output, or None unless ready. A multi-output entry needs the output named."""
        name = self.output_name(output)
        series = self.values.get(name)
        return None if not series else series[-1]

    def output_name(self, output: str | None) -> str:
        if output is not None:
            if output not in get_indicator(self.indicator).output_names:
                raise ValidationFailure(f"{self.indicator} has no output {output!r}")
            return output
        names = get_indicator(self.indicator).output_names
        if len(names) != 1:
            raise ValidationFailure(f"{self.indicator} has several outputs ({', '.join(names)}); name one")
        return names[0]

    def to_document(self) -> dict[str, Any]:
        """Plain JSON for the Trader and for receipts: numbers and ISO times, never NaN, never objects."""
        revised = [{"close_time_utc": utc_iso(use.close_time_utc), "revision": use.revision, "run_id": use.run_id}
                   for use in self.candles_used if use.revision > 1]
        return {
            "indicator": self.indicator, "parameters": dict(self.parameters), "status": self.status,
            "spec_version": self.spec_version, "library": dict(self.library),
            "symbol": self.symbol, "interval_minutes": self.interval_minutes, "source": self.source,
            "mode": self.mode, "availability_basis": self.availability_basis,
            "as_of_utc": utc_iso(self.as_of_utc), "end_close_time_utc": utc_iso(self.end_close_time_utc),
            "requested_points": self.requested_points, "lookback_candles": self.lookback_candles,
            "seeding_candles": self.seeding_candles, "required_candles": self.required_candles,
            "available_candles": self.available_candles, "start_dependent": self.start_dependent,
            "close_times_utc": [utc_iso(at) for at in self.close_times_utc],
            "values": {name: list(series) for name, series in self.values.items()},
            "missing_count": len(self.missing_close_times_utc),
            "missing_close_times_utc": [utc_iso(at) for at in self.missing_close_times_utc[:DOCUMENT_MISSING_LIMIT]],
            "candles": {
                "count": len(self.candles_used), "run_ids": sorted({use.run_id for use in self.candles_used}),
                "revised": revised, "input_sha256": self.input_sha256,
            },
        }


@dataclass(frozen=True, slots=True)
class LevelComparison:
    """A yes or no with the numbers behind it. Truthiness follows ``satisfied``."""

    satisfied: bool
    evaluated: bool
    relation: str
    indicator: str
    output: str | None
    level: Decimal
    value: Decimal | None
    previous_value: Decimal | None
    close_time_utc: datetime | None
    reason: str

    def __bool__(self) -> bool:
        return self.satisfied


def spec_version(spec: IndicatorSpec) -> str:
    """The menu version plus a short digest of this entry's whole definition, so any edit changes it."""
    document = json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{MENU_VERSION}/{hashlib.sha256(document.encode('ascii')).hexdigest()[:12]}"


def plan_history(name: str, params: Mapping[str, object] | None, count: int) -> HistoryPlan:
    """The span a request reads: library lookback + the menu's settling history + the points asked for."""
    if type(count) is not int or not 1 <= count <= MAX_OUTPUT_POINTS:
        raise ValidationFailure(f"count must be a whole number from 1 to {MAX_OUTPUT_POINTS}")
    spec = get_indicator(name)
    resolved = spec.resolve_parameters({} if params is None else params)
    if spec.settle is not None:
        seeding = spec.settle.periods * int(resolved[spec.settle.parameter])
    else:
        seeding = START_DEPENDENT_HISTORY if spec.recursive_seed else 0
    if spec.recursive_seed:
        # A start-sensitive value depends on where the span began, so the span must begin at the same candle for
        # every ``count``: pad it with the points not asked for. The value at a candle then never depends on how
        # many neighbours were requested with it (the largest request already reads this much).
        seeding += MAX_OUTPUT_POINTS - count
    plan = HistoryPlan(lookback=lookback(name, params), seeding=seeding, points=count)
    if plan.span > MAX_HISTORY_CANDLES:
        raise ValidationFailure(
            f"{name} with these settings needs {plan.span} candles; the limit is {MAX_HISTORY_CANDLES}")
    return plan


def compute_indicator(store: CandleStore, name: str, params: Mapping[str, object] | None, *, symbol: str,
                      interval_minutes: int, source: str, end_close_time: datetime, as_of: datetime | None = None,
                      count: int = 1, mode: ReadMode = "point_in_time") -> IndicatorResult:
    """Read the span ending at ``end_close_time`` from the store and compute ``count`` output points.

    ``point_in_time`` (the default, for live use) sees only candles received by ``as_of``. ``replay`` uses close
    times only, is labelled so on the result, and is for research, never evidence that data had arrived.
    """
    _require_aware("end_close_time", end_close_time)
    if as_of is not None:
        _require_aware("as_of", as_of)
    plan = plan_history(name, params, count)
    try:
        window = store.window(symbol, interval_minutes, source, end_close_time=end_close_time, count=plan.span,
                              as_of=as_of, mode=mode)
        older = _only_the_oldest_slots_are_missing(window) and _candles_exist_before(store, window, plan)
    except OverflowError as error:  # a date whose span runs off the calendar is a bad request, not a crash
        raise ValidationFailure("the date is too early or too late for the candle span this indicator needs") from error
    return _evaluate(name, params, window, plan, older_history=older)


def _require_aware(label: str, value: object) -> None:
    """The store would raise a bare TypeError on a naive time; say it as a refusal before anything is read."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationFailure(f"{label} must be a timezone-aware datetime")


def compute_from_window(name: str, params: Mapping[str, object] | None, window: CandleWindow, *,
                        count: int = 1, older_history: bool = False) -> IndicatorResult:
    """The same computation over a window already read (it must be exactly the span the request needs).

    ``older_history`` says a candle is visible before the window. It only matters when the oldest slots are
    missing: with older history that is a gap; without it the history has not begun (warming up).
    """
    plan = plan_history(name, params, count)
    if len(window.slots) != plan.span:
        raise ValidationFailure(f"{name} needs a window of {plan.span} slots, not {len(window.slots)}")
    return _evaluate(name, params, window, plan, older_history=older_history)


def _evaluate(name: str, params: Mapping[str, object] | None, window: CandleWindow, plan: HistoryPlan, *,
              older_history: bool) -> IndicatorResult:
    spec = get_indicator(name)
    resolved = spec.resolve_parameters({} if params is None else params)
    _assert_completed(window)
    missing = window.missing_close_times
    status = _status(window, older_history)
    uses = _uses(window)
    close_times: tuple[datetime, ...] = ()
    values: dict[str, tuple[float, ...]] = {}
    if status == "ready":
        raw = compute_raw(name, resolved, columns_from_candles(window.candles))
        close_times = tuple(slot.close_time_utc for slot in window.slots[-plan.points:])
        for output in spec.outputs:
            series = tuple(float(number) for number in raw[output.name][-plan.points:])
            if len(series) != plan.points or not all(math.isfinite(number) for number in series):
                raise IndicatorComputationError(
                    f"{name} {output.name} has no finite value where the library lookback says it should")
            values[output.name] = series
    return IndicatorResult(
        indicator=name, parameters=MappingProxyType(dict(resolved)), spec_version=spec_version(spec),
        library=MappingProxyType(library_versions()), symbol=window.symbol,
        interval_minutes=window.interval_minutes, source=window.source, mode=window.mode,
        availability_basis=window.availability_basis, as_of_utc=window.as_of_utc,
        end_close_time_utc=window.slots[-1].close_time_utc, status=status, requested_points=plan.points,
        lookback_candles=plan.lookback, seeding_candles=plan.seeding, required_candles=plan.span,
        available_candles=plan.span - len(missing), start_dependent=spec.recursive_seed,
        close_times_utc=close_times, values=MappingProxyType(values), missing_close_times_utc=missing,
        candles_used=uses, input_sha256=_digest(uses))


def _only_the_oldest_slots_are_missing(window: CandleWindow) -> bool:
    """Some slots are missing, they are all at the start of the span, and the newest slots are present.

    A span with no candle at all is not this: it shows nothing about whether the history has begun (it could be a
    series that does not exist, or a feed that stopped long ago), so it stays a hole, never a young coin.
    """
    present = [index for index, slot in enumerate(window.slots) if slot.stored is not None]
    if not present or len(present) == len(window.slots):
        return False
    return len(present) == len(window.slots) - present[0]


def _candles_exist_before(store: CandleStore, window: CandleWindow, plan: HistoryPlan) -> bool:
    """Is any candle visible in the span-sized stretch just before the window? Read through ``window`` too."""
    earlier = store.window(window.symbol, window.interval_minutes, window.source,
                          end_close_time=window.slots[0].close_time_utc - timedelta(minutes=window.interval_minutes),
                          count=plan.span, as_of=window.as_of_utc, mode=window.mode)
    return any(slot.stored is not None for slot in earlier.slots)


def _status(window: CandleWindow, older_history: bool) -> IndicatorStatus:
    """ready: nothing is missing. warming_up: only the oldest slots are missing and nothing exists before them,
    so the history has not begun. gapped: any other missing slot, including a hole at the oldest slot with
    history before it, and the newest slot."""
    if all(slot.stored is not None for slot in window.slots):
        return "ready"
    if _only_the_oldest_slots_are_missing(window) and not older_history:
        return "warming_up"
    return "gapped"


def _assert_completed(window: CandleWindow) -> None:
    """The store promises consecutive slots of one series with completed candles known at ``as_of``; check it
    instead of trusting it, because ``compute_from_window`` takes a window from any caller."""
    step = timedelta(minutes=window.interval_minutes)
    for earlier, later in zip(window.slots, window.slots[1:]):
        if later.close_time_utc - earlier.close_time_utc != step:
            raise CandleSelectionError("the slots of a window must be consecutive, one interval apart")
    for slot in window.slots:
        if slot.stored is None:
            continue
        candle = slot.stored.candle
        if candle.close_time_utc != slot.close_time_utc or candle.interval_minutes != window.interval_minutes:
            raise CandleSelectionError("a candle does not belong to the slot it was returned for")
        if candle.symbol != window.symbol:
            raise CandleSelectionError("a candle of another symbol than the window's was returned")
        if candle.source != window.source:
            raise CandleSelectionError("a candle of another source than the window's was returned")
        if candle.available_at_utc < candle.close_time_utc:
            raise CandleSelectionError("a candle was received before it closed")
        if candle.close_time_utc > window.as_of_utc:
            raise CandleSelectionError("a candle that closes after as_of is not complete")
        if window.mode == "point_in_time" and candle.available_at_utc > window.as_of_utc:
            raise CandleSelectionError("a candle received after as_of is not visible in a point-in-time read")


def _uses(window: CandleWindow) -> tuple[CandleUse, ...]:
    return tuple(CandleUse(close_time_utc=slot.close_time_utc, revision=slot.stored.revision,
                           run_id=slot.stored.run_id, content_hash=slot.stored.candle.content_hash)
                 for slot in window.slots if slot.stored is not None)


def _digest(uses: tuple[CandleUse, ...]) -> str:
    """One hash over exactly which candle revisions were read, so a receipt can show the inputs were the same."""
    document = json.dumps([[to_micros(use.close_time_utc), use.revision, use.run_id, use.content_hash]
                           for use in uses], separators=(",", ":"))
    return hashlib.sha256(document.encode("ascii")).hexdigest()


def compare_to_level(result: IndicatorResult, level: Decimal, relation: Relation, *,
                     output: str | None = None) -> LevelComparison:
    """Compare the newest value with an alarm level. An indicator that is not ready never satisfies anything.

    ``above``/``below`` are strict and use the newest point. ``crosses_above`` is previous <= level < newest and
    ``crosses_below`` is previous >= level > newest, so a path that touches the level and then leaves it fires
    once, and one that only touches it does not fire. Both sides are compared as Decimals rounded half-even to the
    twelve significant digits the library can vouch for (``SNAPSHOT_DIGITS``, the rule stored snapshots use): its
    running sums add noise in the last digits, so a flat average of 63000.1 comes out as 63000.09999999999 or
    63000.10000000006 and must neither be above nor below an alarm at 63000.1. The returned comparison carries the
    rounded numbers used and the level as given.
    """
    if relation not in RELATIONS:
        raise ValidationFailure(f"relation must be one of {', '.join(RELATIONS)}")
    if type(level) is not Decimal or not level.is_finite() or len(level.as_tuple().digits) > MAX_LEVEL_DIGITS:
        raise ValidationFailure("the alarm level must be a finite Decimal")
    name = result.output_name(output)

    def outcome(satisfied: bool, reason: str, value: Decimal | None = None, previous: Decimal | None = None,
                at: datetime | None = None, evaluated: bool = True) -> LevelComparison:
        return LevelComparison(satisfied=satisfied, evaluated=evaluated, relation=relation,
                               indicator=result.indicator, output=name, level=level, value=value,
                               previous_value=previous, close_time_utc=at, reason=reason)

    if result.status != "ready":
        return outcome(False, f"the indicator is {_STATUS_WORDS[result.status]}", evaluated=False)
    series = result.values.get(name, ())
    needed = 2 if relation.startswith("crosses") else 1
    usable = [isinstance(number, float) and math.isfinite(number) for number in series[-needed:]]
    if len(series) < needed or not all(usable):
        reason = "a crossing needs two points" if needed == 2 and len(series) < 2 else "no finite value to compare"
        return outcome(False, reason, evaluated=False)
    now = rounded_decimal(series[-1])
    previous = rounded_decimal(series[-2]) if needed == 2 else None
    limit = rounded_significant(level)
    at = result.close_times_utc[-1]
    if relation == "above":
        satisfied = now > limit
    elif relation == "below":
        satisfied = now < limit
    elif relation == "crosses_above":
        satisfied = previous <= limit < now
    else:
        satisfied = previous >= limit > now
    return outcome(satisfied, "compared", now, previous, at)


__all__ = [
    "DOCUMENT_MISSING_LIMIT", "MAX_HISTORY_CANDLES", "MAX_OUTPUT_POINTS", "RELATIONS", "START_DEPENDENT_HISTORY",
    "CandleSelectionError", "CandleUse", "HistoryPlan", "IndicatorResult", "IndicatorStatus", "LevelComparison",
    "Relation", "compare_to_level", "compute_from_window", "compute_indicator", "plan_history", "spec_version",
]
