"""What a candle collection run covers, in what order, and what it costs (pure functions, no I/O).

Scope (docs/KRAKEN-CANDLE-COLLECTION.md): every eligible USD pair at 5, 15 and 60 minutes, and 1 minute only for
an explicit short list (default BTC/USD and ETH/USD), because a quarter of one-minute candles is about 130,000
rows per pair. Eligible means normal trading status and no stablecoin, yield-token or fiat mark (D131, D127).

Order: a response holds the 720 most recent candles, so data that is not collected falls out of reach after
12 hours at 1 minute, 60 hours at 5, 7.5 days at 15 and 30 days at 60. A run that stops early therefore does
the smallest interval first and, inside an interval, the series whose newest stored candle is oldest first.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from math import ceil

from trade_graph.adapters.market.kraken_candles import OHLC_COMMITTED_ROWS
from trade_graph.contracts.candles import COLLECTED_INTERVALS
from trade_graph.contracts.pair_catalog import PairRecord
from trade_graph.domain.errors import ValidationFailure

DEFAULT_ONE_MINUTE_SYMBOLS = ("BTC/USD", "ETH/USD")
BYTES_PER_ROW = 118  # measured 2026-10-13: 100,000 rows took 11.8 MB, indexes included (docs/CANDLE-STORE.md)
QUARTER_DAYS = 91
_USD_SYMBOL = re.compile(r"[A-Z0-9]{1,16}/USD")


def window_hours(interval_minutes: int) -> int:
    """How far back the 720 most recent candles of one response reach."""
    return OHLC_COMMITTED_ROWS * interval_minutes // 60


def rows_per_quarter(interval_minutes: int) -> int:
    return QUARTER_DAYS * 1440 // interval_minutes


def _symbols(name: str, value: object, *, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(value, tuple) or (not value and not allow_empty):
        raise ValidationFailure(f"{name} must be a non-empty tuple of USD pair symbols")
    if any(not isinstance(item, str) or _USD_SYMBOL.fullmatch(item) is None for item in value):
        raise ValidationFailure(f"{name} must hold upper-case BASE/USD symbols")
    if len(set(value)) != len(value):
        raise ValidationFailure(f"{name} must not repeat a symbol")
    return value


@dataclass(frozen=True, slots=True)
class CollectionScope:
    intervals: tuple[int, ...] = COLLECTED_INTERVALS
    symbols: tuple[str, ...] | None = None          # None means every eligible pair at the wide intervals
    one_minute_symbols: tuple[str, ...] = DEFAULT_ONE_MINUTE_SYMBOLS

    def __post_init__(self) -> None:
        intervals = self.intervals
        if (not isinstance(intervals, tuple) or not intervals
                or any(type(item) is not int or item not in COLLECTED_INTERVALS for item in intervals)
                or len(set(intervals)) != len(intervals)):
            raise ValidationFailure("intervals must be a non-empty tuple of distinct values from 1, 5, 15 and 60")
        object.__setattr__(self, "intervals", tuple(sorted(intervals)))
        if self.symbols is not None:
            _symbols("symbols", self.symbols, allow_empty=False)
        _symbols("one_minute_symbols", self.one_minute_symbols, allow_empty=True)

    def to_document(self) -> dict:
        return {
            "intervals": list(self.intervals),
            "symbols": None if self.symbols is None else list(self.symbols),
            "one_minute_symbols": list(self.one_minute_symbols),
        }


@dataclass(frozen=True, slots=True)
class PlannedSeries:
    record: PairRecord
    interval_minutes: int
    newest_stored_open: int | None   # unix seconds of the newest stored candle, None if the series is new

    @property
    def since(self) -> int | None:
        """One interval before the newest stored candle, so the last committed candle is fetched again."""
        if self.newest_stored_open is None:
            return None
        return max(0, self.newest_stored_open - self.interval_minutes * 60)


@dataclass(frozen=True, slots=True)
class SkippedPair:
    symbol: str
    reasons: tuple[str, ...]   # "status:cancel_only", "excluded:stablecoin", ...


@dataclass(frozen=True, slots=True)
class CollectionPlan:
    series: tuple[PlannedSeries, ...]
    skipped: tuple[SkippedPair, ...]
    unknown_symbols: tuple[str, ...]   # named in the scope but not in the catalog


def _skip_reasons(record: PairRecord) -> tuple[str, ...]:
    reasons = [f"excluded:{code}" for code in record.exclusion_reasons]
    if not record.in_normal_status:
        reasons.append(f"status:{record.status}")
    return tuple(sorted(reasons))


def plan_collection(records: Sequence[PairRecord], scope: CollectionScope,
                    newest_stored: Mapping[tuple[str, int], int]) -> CollectionPlan:
    """The ordered series to request. ``newest_stored`` maps (symbol, interval) to the newest stored open time."""
    by_symbol = {record.symbol: record for record in records if record.usd_quoted}
    wide_names = tuple(sorted(by_symbol)) if scope.symbols is None else scope.symbols
    unknown: set[str] = set()
    skipped: dict[str, tuple[str, ...]] = {}

    def usable(symbol: str) -> PairRecord | None:
        record = by_symbol.get(symbol)
        if record is None:
            unknown.add(symbol)
            return None
        if not record.eligible_for_collection:
            skipped[symbol] = _skip_reasons(record)
            return None
        return record

    series: list[PlannedSeries] = []
    for interval in scope.intervals:
        for symbol in scope.one_minute_symbols if interval == 1 else wide_names:
            record = usable(symbol)
            if record is not None:
                series.append(PlannedSeries(record, interval, newest_stored.get((symbol, interval))))
    series.sort(key=lambda item: (
        item.interval_minutes, item.newest_stored_open is None, item.newest_stored_open or 0, item.record.symbol))
    return CollectionPlan(
        series=tuple(series), skipped=tuple(SkippedPair(name, skipped[name]) for name in sorted(skipped)),
        unknown_symbols=tuple(sorted(unknown)))


def storage_projection(series_by_interval: Mapping[int, int]) -> dict:
    """Rows and bytes one quarter of the planned series would add, at the measured bytes per row."""
    per_quarter: dict[str, dict] = {}
    total_rows = 0
    for interval in sorted(series_by_interval):
        rows = series_by_interval[interval] * rows_per_quarter(interval)
        total_rows += rows
        per_quarter[str(interval)] = {
            "series": series_by_interval[interval], "rows": rows, "estimated_bytes": rows * BYTES_PER_ROW,
        }
    return {
        "bytes_per_row_estimate": BYTES_PER_ROW,
        "quarter_days": QUARTER_DAYS,
        "per_quarter": per_quarter,
        "per_quarter_total": {"rows": total_rows, "estimated_bytes": total_rows * BYTES_PER_ROW},
    }


def cadence_report(series_by_interval: Mapping[int, int], *, ceiling: int, decay_per_second: Fraction) -> dict:
    """How often each interval must be collected to leave no new gap, and how long a full pass takes."""
    intervals: dict[str, dict] = {}
    total_requests = 0
    for interval in sorted(series_by_interval):
        requests = series_by_interval[interval]
        total_requests += requests
        window = window_hours(interval)
        intervals[str(interval)] = {
            "window_hours": window,
            "longest_pause_without_a_gap_hours": window,
            "recommended_every_hours": window // 2,
            "requests_per_pass": requests,
            "minutes_per_pass_at_the_limiter_rate": _pass_minutes(requests, ceiling, decay_per_second),
        }
    return {
        "candles_per_response": OHLC_COMMITTED_ROWS,
        "intervals": intervals,
        "all_intervals": {
            "requests_per_pass": total_requests,
            "minutes_per_pass_at_the_limiter_rate": _pass_minutes(total_requests, ceiling, decay_per_second),
            "longest_pause_without_a_gap_hours": min((window_hours(interval) for interval in series_by_interval),
                                                     default=None),
        },
        "note": ("A response reaches back 720 candles. If the next collection starts later than the window, the "
                 "candles in between are not available from this endpoint and the series has a gap that the "
                 "Trades backfill or the archive import must fill. Recommended cadence is half the window."),
    }


def _pass_minutes(requests: int, ceiling: int, decay_per_second: Fraction) -> float:
    paced = max(0, requests - ceiling)
    return round(ceil(paced / decay_per_second) / 60, 1)
