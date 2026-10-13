"""Compare the same candle slots across sources, and choose between them by a fixed order (T26 slice B).

Sources are never merged. ``reconcile_candles`` reports where two or more sources agree, differ (which fields, by how
much) or are the only holder of a slot. ``precedence_window`` hands out, for each slot, the stored candle of the
first source in the given order that has it, labelled with that source and with any other source that disagreed
there. Nothing is averaged, filled or blended: the chosen candle is the stored candle, unchanged.

Default order (see docs/KRAKEN-TRADES-BACKFILL.md): the collector's own candles (what Kraken's OHLC endpoint said when
this process asked, with a real receipt time), then Kraken's published archive file (Kraken's numbers, received in
bulk and in an unconfirmed format), then candles this program aggregated from raw trades (the most derived).
"""

from __future__ import annotations

import heapq
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal
from itertools import combinations
from typing import Any

from trade_graph.application.candle_store import CandleStore
from trade_graph.contracts.candles import (
    CANDLE_INTERVALS,
    CANDLE_SOURCES,
    AvailabilityBasis,
    Candle,
    ReadMode,
    StoredCandle,
    to_unix,
)
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.money import canonical_decimal

DEFAULT_SOURCE_PRECEDENCE = ("kraken_public_ohlc", "kraken_ohlcvt_archive", "kraken_public_trades")
COMPARED_FIELDS = ("open", "high", "low", "close", "volume", "vwap", "trade_count")
DEFAULT_WORST = 10
MAX_WORST = 1000
MAX_RECONCILE_SLOTS = 2_000_000
DEFAULT_CHUNK_SLOTS = 20_000
RELATIVE_PLACES = Decimal("1e-12")
STATUSES = ("identical", "differing", "single_source", "absent_in_all")
NOTES = (
    "Differences are reported, never merged: every source stays its own series and nothing is averaged or filled.",
    "A missing candle means no candle is stored for the slot: either there were no trades or it was not collected.",
    "vwap is compared only where both sources hold one; the archive has none, so it is counted as not comparable.",
    "Relative size is |a - b| / max(|a|, |b|), rounded to 12 decimals. trade_count may be defined differently by "
    "each source (the Trades-built count is rows of the Trades list).",
)
_MATH = Context(prec=50)


def _value(candle: Candle, name: str) -> Decimal | None:
    raw = getattr(candle, name)
    return None if raw is None else Decimal(raw)


def _text(value: Decimal) -> str:
    return canonical_decimal(value)


def _relative(left: Decimal, right: Decimal) -> Decimal:
    largest = max(abs(left), abs(right))
    return Decimal(0) if largest == 0 else _MATH.divide(abs(left - right), largest)


@dataclass(frozen=True, slots=True)
class FieldDifference:
    field: str
    left_source: str
    left: Decimal
    right_source: str
    right: Decimal
    absolute: Decimal
    relative: Decimal

    def to_document(self) -> dict[str, str]:
        return {"field": self.field, "left_source": self.left_source, "left": _text(self.left),
                "right_source": self.right_source, "right": _text(self.right), "absolute": _text(self.absolute),
                "relative": _text(self.relative.quantize(RELATIVE_PLACES, rounding=ROUND_HALF_EVEN, context=_MATH))}


def compare_candles(left: Candle, right: Candle, fields: Sequence[str] = COMPARED_FIELDS,
                    ) -> tuple[list[FieldDifference], list[str]]:
    """(differences, fields that could not be compared because one side holds no value)."""
    differences: list[FieldDifference] = []
    skipped: list[str] = []
    for name in fields:
        a, b = _value(left, name), _value(right, name)
        if a is None or b is None:
            skipped.append(name)
        elif a != b:
            differences.append(FieldDifference(name, left.source, a, right.source, b, abs(a - b), _relative(a, b)))
    return differences, skipped


@dataclass(frozen=True, slots=True)
class SlotReport:
    open_time_utc: datetime
    close_time_utc: datetime
    status: str
    sources_present: tuple[str, ...]
    differences: tuple[FieldDifference, ...]

    @property
    def relative_size(self) -> Decimal:
        return max((difference.relative for difference in self.differences), default=Decimal(0))

    def to_document(self) -> dict[str, Any]:
        ordered = sorted(self.differences, key=lambda d: (-d.relative, COMPARED_FIELDS.index(d.field)))
        return {
            "open_time_utc": utc_iso(self.open_time_utc), "close_time_utc": utc_iso(self.close_time_utc),
            "status": self.status, "sources_present": list(self.sources_present),
            "relative_size": _text(self.relative_size.quantize(RELATIVE_PLACES, rounding=ROUND_HALF_EVEN,
                                                               context=_MATH)),
            "differences": [difference.to_document() for difference in ordered],
        }


@dataclass
class _FieldStats:
    compared: int = 0
    differing: int = 0
    not_comparable: int = 0
    max_absolute: Decimal | None = None
    max_relative: Decimal | None = None
    sum_relative: Decimal = Decimal(0)

    def document(self) -> dict[str, Any]:
        def rounded(value: Decimal | None) -> str | None:
            return None if value is None else _text(value.quantize(RELATIVE_PLACES, rounding=ROUND_HALF_EVEN,
                                                                    context=_MATH))

        mean = None if not self.differing else _MATH.divide(self.sum_relative, Decimal(self.differing))
        return {"compared": self.compared, "differing": self.differing, "not_comparable": self.not_comparable,
                "max_absolute": None if self.max_absolute is None else _text(self.max_absolute),
                "max_relative": rounded(self.max_relative), "mean_relative": rounded(mean)}


@dataclass
class _PairStats:
    both_present: int = 0
    identical: int = 0
    differing: int = 0
    only_first: int = 0
    only_second: int = 0
    absent_in_both: int = 0
    fields: dict[str, _FieldStats] = field(default_factory=dict)

    def document(self, fields: Sequence[str]) -> dict[str, Any]:
        return {"both_present": self.both_present, "identical": self.identical, "differing": self.differing,
                "only_first": self.only_first, "only_second": self.only_second, "absent_in_both": self.absent_in_both,
                "fields": {name: self.fields.get(name, _FieldStats()).document() for name in fields}}


@dataclass(frozen=True)
class ReconciliationReport:
    symbol: str
    interval_minutes: int
    sources: tuple[str, ...]
    mode: ReadMode
    as_of_utc: datetime | None
    first_open_utc: datetime
    last_close_utc: datetime
    slot_count: int
    fields: tuple[str, ...]
    counts: dict[str, Any]
    pairwise: dict[str, _PairStats]
    worst_slots: tuple[SlotReport, ...]
    slot_statuses: tuple[str, ...]
    series_present: dict[str, bool]
    source_pairs: dict[str, str | None]

    @property
    def availability_basis(self) -> AvailabilityBasis:
        return "receipt_time" if self.mode == "point_in_time" else "event_time_only"

    def to_document(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "interval_minutes": self.interval_minutes, "sources": list(self.sources),
            "mode": self.mode, "availability_basis": self.availability_basis,
            "as_of_utc": None if self.as_of_utc is None else utc_iso(self.as_of_utc),
            "window": {"first_open_utc": utc_iso(self.first_open_utc), "last_close_utc": utc_iso(self.last_close_utc),
                       "slots": self.slot_count},
            "compared_fields": list(self.fields), "series_present": dict(self.series_present),
            "source_pairs": dict(self.source_pairs), "counts": self.counts,
            "pairwise": {key: stats.document(self.fields) for key, stats in self.pairwise.items()},
            "worst_slots": [slot.to_document() for slot in self.worst_slots], "notes": list(NOTES),
        }


def _check_sources(sources: Any, *, minimum: int) -> tuple[str, ...]:
    if isinstance(sources, str) or not isinstance(sources, (tuple, list)):
        raise ValidationFailure("sources must be an ordered tuple of source names")
    chosen = tuple(sources)
    if len(chosen) < minimum or len(set(chosen)) != len(chosen) or any(s not in CANDLE_SOURCES for s in chosen):
        raise ValidationFailure(f"name {minimum} or more different, known sources")
    return chosen


def _check_window(interval_minutes: int, count: int, mode: str, as_of: datetime | None) -> None:
    if interval_minutes not in CANDLE_INTERVALS:
        raise ValidationFailure("unsupported candle interval")
    if type(count) is not int or not 1 <= count <= MAX_RECONCILE_SLOTS:
        raise ValidationFailure(f"count must be between 1 and {MAX_RECONCILE_SLOTS}")
    if mode not in ("point_in_time", "replay"):
        raise ValidationFailure("unsupported read mode")
    if mode == "point_in_time" and as_of is None:
        raise ValidationFailure("point-in-time reads require as_of")


def _chunks(end_close_time: datetime, count: int, step: timedelta, size: int) -> list[tuple[datetime, int]]:
    chunks, remaining, end = [], count, end_close_time
    while remaining > 0:
        take = min(size, remaining)
        chunks.append((end, take))
        end -= step * take
        remaining -= take
    return chunks[::-1]


def reconcile_candles(store: CandleStore, symbol: str, interval_minutes: int, *, sources: Sequence[str],
                      end_close_time: datetime, count: int, as_of: datetime | None = None,
                      mode: ReadMode = "replay", worst: int = DEFAULT_WORST,
                      fields: Sequence[str] = COMPARED_FIELDS, chunk_slots: int = DEFAULT_CHUNK_SLOTS,
                      ) -> ReconciliationReport:
    """Compare ``count`` slots ending at ``end_close_time`` across two or more sources. Read-only."""
    chosen = _check_sources(sources, minimum=2)
    _check_window(interval_minutes, count, mode, as_of)
    if type(worst) is not int or not 1 <= worst <= MAX_WORST:
        raise ValidationFailure(f"worst must be between 1 and {MAX_WORST}")
    if type(chunk_slots) is not int or chunk_slots < 1:
        raise ValidationFailure("chunk_slots must be a positive whole number")
    names = tuple(fields)
    if not names or any(name not in COMPARED_FIELDS for name in names):
        raise ValidationFailure(f"fields must be chosen from {COMPARED_FIELDS}")
    step = timedelta(minutes=interval_minutes)
    pairs = list(combinations(range(len(chosen)), 2))
    stats = {(a, b): _PairStats() for a, b in pairs}
    totals = {status: 0 for status in STATUSES}
    only = {source: 0 for source in chosen}
    statuses: list[str] = []
    kept: list[tuple[Decimal, int, SlotReport]] = []  # (relative size, -open time, report): a bounded top list
    for chunk_end, take in _chunks(end_close_time, count, step, chunk_slots):
        windows = [store.window(symbol, interval_minutes, source, end_close_time=chunk_end, count=take,
                                as_of=as_of, mode=mode) for source in chosen]
        for index in range(take):
            held = [window.slots[index].stored for window in windows]
            close = windows[0].slots[index].close_time_utc
            present = tuple(i for i, stored in enumerate(held) if stored is not None)
            for a, b in pairs:
                pair = stats[(a, b)]
                if held[a] is None and held[b] is None:
                    pair.absent_in_both += 1
                elif held[b] is None:
                    pair.only_first += 1
                elif held[a] is None:
                    pair.only_second += 1
                else:
                    pair.both_present += 1
            differences: list[FieldDifference] = []
            for a, b in combinations(present, 2):
                found, skipped = compare_candles(held[a].candle, held[b].candle, names)
                pair = stats[(a, b)]
                pair.differing += 1 if found else 0
                pair.identical += 0 if found else 1
                for name in names:
                    cell = pair.fields.setdefault(name, _FieldStats())
                    if name in skipped:
                        cell.not_comparable += 1
                    else:
                        cell.compared += 1
                for difference in found:
                    cell = pair.fields[difference.field]
                    cell.differing += 1
                    cell.sum_relative += difference.relative
                    cell.max_absolute = (difference.absolute if cell.max_absolute is None
                                         else max(cell.max_absolute, difference.absolute))
                    cell.max_relative = (difference.relative if cell.max_relative is None
                                         else max(cell.max_relative, difference.relative))
                differences.extend(found)
            if not present:
                status = "absent_in_all"
            elif len(present) == 1:
                status = "single_source"
                only[chosen[present[0]]] += 1
            else:
                status = "differing" if differences else "identical"
            totals[status] += 1
            statuses.append(status)
            if status == "differing":
                opened = close - step
                report = SlotReport(opened, close, status, tuple(chosen[i] for i in present), tuple(differences))
                kept.append((report.relative_size, -to_unix(opened), report))
                if len(kept) > worst * 4:
                    kept = heapq.nlargest(worst, kept, key=lambda item: (item[0], item[1]))
    top = heapq.nlargest(worst, kept, key=lambda item: (item[0], item[1]))
    summaries = {s.source: s for s in store.series() if s.symbol == symbol and s.interval_minutes == interval_minutes}
    first_open = end_close_time - step * count
    return ReconciliationReport(
        symbol=symbol, interval_minutes=interval_minutes, sources=chosen, mode=mode, as_of_utc=as_of,
        first_open_utc=first_open, last_close_utc=end_close_time, slot_count=count, fields=names,
        counts={"slots": count, "identical": totals["identical"], "differing": totals["differing"],
                "single_source": totals["single_source"], "single_source_by_source": only,
                "absent_in_all": totals["absent_in_all"]},
        pairwise={f"{chosen[a]}|{chosen[b]}": stats[(a, b)] for a, b in pairs},
        worst_slots=tuple(item[2] for item in top), slot_statuses=tuple(statuses),
        series_present={source: source in summaries for source in chosen},
        source_pairs={source: summaries[source].source_pair if source in summaries else None for source in chosen})


# --- precedence ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PrecedenceSlot:
    close_time_utc: datetime
    stored: StoredCandle | None
    source: str | None
    sources_present: tuple[str, ...]
    disagreeing_sources: tuple[str, ...]
    disagreement_fields: dict[str, tuple[str, ...]]

    def to_document(self) -> dict[str, Any]:
        return {"close_time_utc": utc_iso(self.close_time_utc), "source": self.source,
                "sources_present": list(self.sources_present), "disagreeing_sources": list(self.disagreeing_sources),
                "disagreement_fields": {key: list(value) for key, value in self.disagreement_fields.items()}}


@dataclass(frozen=True, slots=True)
class PrecedenceWindow:
    """Exactly the requested slots, oldest first, each labelled with the source that supplied it."""

    symbol: str
    interval_minutes: int
    sources: tuple[str, ...]
    as_of_utc: datetime
    mode: ReadMode
    slots: tuple[PrecedenceSlot, ...]

    @property
    def availability_basis(self) -> AvailabilityBasis:
        return "receipt_time" if self.mode == "point_in_time" else "event_time_only"

    @property
    def candles(self) -> tuple[Candle, ...]:
        return tuple(slot.stored.candle for slot in self.slots if slot.stored is not None)

    @property
    def missing_close_times(self) -> tuple[datetime, ...]:
        return tuple(slot.close_time_utc for slot in self.slots if slot.stored is None)

    @property
    def complete(self) -> bool:
        return all(slot.stored is not None for slot in self.slots)

    @property
    def source_counts(self) -> dict[str, int]:
        return {source: sum(1 for slot in self.slots if slot.source == source) for source in self.sources}

    @property
    def disagreement_count(self) -> int:
        return sum(1 for slot in self.slots if slot.disagreeing_sources)

    def to_document(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "interval_minutes": self.interval_minutes, "sources": list(self.sources),
            "mode": self.mode, "availability_basis": self.availability_basis, "as_of_utc": utc_iso(self.as_of_utc),
            "source_counts": self.source_counts, "missing_slots": len(self.missing_close_times),
            "disagreement_count": self.disagreement_count, "slots": [slot.to_document() for slot in self.slots],
            "notes": list(NOTES[:2]),
        }


def precedence_window(store: CandleStore, symbol: str, interval_minutes: int,
                      sources: Sequence[str] = DEFAULT_SOURCE_PRECEDENCE, *, end_close_time: datetime, count: int,
                      as_of: datetime | None = None, mode: ReadMode = "point_in_time") -> PrecedenceWindow:
    """For each slot, the first source in ``sources`` that holds it; other sources that disagree there are named."""
    chosen = _check_sources(sources, minimum=1)
    _check_window(interval_minutes, count, mode, as_of)
    windows = [store.window(symbol, interval_minutes, source, end_close_time=end_close_time, count=count,
                            as_of=as_of, mode=mode) for source in chosen]
    slots = []
    for index in range(count):
        held = [(chosen[i], window.slots[index].stored) for i, window in enumerate(windows)
                if window.slots[index].stored is not None]
        close = windows[0].slots[index].close_time_utc
        if not held:
            slots.append(PrecedenceSlot(close, None, None, (), (), {}))
            continue
        winner, stored = held[0]
        fields: dict[str, tuple[str, ...]] = {}
        for other, other_stored in held[1:]:
            differences, _ = compare_candles(stored.candle, other_stored.candle)
            if differences:
                fields[other] = tuple(difference.field for difference in differences)
        slots.append(PrecedenceSlot(close, stored, winner, tuple(name for name, _ in held), tuple(fields), fields))
    reference = as_of if as_of is not None else end_close_time
    return PrecedenceWindow(symbol=symbol, interval_minutes=interval_minutes, sources=chosen, as_of_utc=reference,
                            mode=mode, slots=tuple(slots))


__all__ = [
    "COMPARED_FIELDS", "DEFAULT_SOURCE_PRECEDENCE", "FieldDifference", "PrecedenceSlot", "PrecedenceWindow",
    "ReconciliationReport", "SlotReport", "compare_candles", "precedence_window", "reconcile_candles",
]
