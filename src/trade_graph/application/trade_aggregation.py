"""Aggregate an ordered stream of public trades into completed candle values (docs/KRAKEN-TRADES-BACKFILL.md).

Pure: no clock, store or network. One stream feeds every requested interval. A candle slot is ``[open, open +
interval)``; a trade at exactly the close belongs to the next slot. A bucket is handed back only when a later trade
proves the stream has passed its close, so a still-forming candle is never produced. Slots without trades produce
nothing (no carry-forward, no zero fill).

The volume-weighted average price is the exact sum of price x volume divided by the exact volume, rounded half-even
to four decimals finer than the finest price in the bucket. Because that grid contains every price, the rounded
value cannot leave the bucket's low/high range.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Context, Decimal

from trade_graph.adapters.market.kraken_trades import PublicTrade
from trade_graph.contracts.candles import CANDLE_INTERVALS
from trade_graph.domain.errors import ValidationFailure

VWAP_EXTRA_PLACES = 4
MIN_VWAP_PLACES = 8
_EXACT = Context(prec=200)  # large enough that sums of products of 20-digit decimals are never rounded


@dataclass(frozen=True, slots=True)
class CompletedBucket:
    """The values of one completed candle. ``tainted`` marks a bucket whose trade list is known to be incomplete."""

    interval: int
    open_unix: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    vwap: Decimal
    trade_count: int
    tainted: bool


class _Bucket:
    __slots__ = ("open_unix", "open", "high", "low", "close", "volume", "notional", "count", "places", "tainted")

    def __init__(self, open_unix: int, trade: PublicTrade, tainted: bool) -> None:
        self.open_unix = open_unix
        self.open = self.high = self.low = self.close = trade.price
        self.volume = trade.volume
        self.notional = _EXACT.multiply(trade.price, trade.volume)
        self.count = 1
        self.places = _places(trade.price)
        self.tainted = tainted

    def add(self, trade: PublicTrade) -> None:
        self.high = max(self.high, trade.price)
        self.low = min(self.low, trade.price)
        self.close = trade.price
        self.volume = _EXACT.add(self.volume, trade.volume)
        self.notional = _EXACT.add(self.notional, _EXACT.multiply(trade.price, trade.volume))
        self.count += 1
        self.places = max(self.places, _places(trade.price))

    def complete(self, interval: int) -> CompletedBucket:
        grid = Decimal(1).scaleb(-(max(self.places, MIN_VWAP_PLACES) + VWAP_EXTRA_PLACES))
        vwap = _EXACT.divide(self.notional, self.volume).quantize(grid, rounding=ROUND_HALF_EVEN, context=_EXACT)
        return CompletedBucket(interval=interval, open_unix=self.open_unix, open=self.open, high=self.high,
                               low=self.low, close=self.close, volume=self.volume, vwap=vwap,
                               trade_count=self.count, tainted=self.tainted)


def _places(price: Decimal) -> int:
    return max(0, -int(price.as_tuple().exponent))


class _Interval:
    __slots__ = ("interval", "step", "floor", "end", "decided_until", "bucket", "finished")

    def __init__(self, interval: int, floor: int, end: int) -> None:
        self.interval = interval
        self.step = interval * 60
        self.floor = floor
        self.end = end
        self.decided_until = floor
        self.bucket: _Bucket | None = None
        self.finished = floor >= end


class TradeAggregator:
    """Feed trades in time order. ``floors``/``ends`` map interval -> unix seconds on that interval's grid.

    Slots before a floor were decided earlier and are ignored; the first trade at or after an end completes every
    open bucket below it and finishes that interval.
    """

    def __init__(self, floors: Mapping[int, int], ends: Mapping[int, int]) -> None:
        if not floors or set(floors) != set(ends):
            raise ValidationFailure("floors and ends must name the same, non-empty set of intervals")
        self._states: list[_Interval] = []
        for interval in sorted(floors):
            if interval not in CANDLE_INTERVALS:
                raise ValidationFailure("unsupported candle interval")
            step = interval * 60
            if type(floors[interval]) is not int or type(ends[interval]) is not int:
                raise ValidationFailure("floors and ends must be whole Unix seconds")
            if floors[interval] % step or ends[interval] % step:
                raise ValidationFailure("floors and ends must sit on the interval grid")
            self._states.append(_Interval(interval, floors[interval], ends[interval]))
        self.ignored_before_range = 0
        self.ignored_after_end = 0

    @property
    def finished(self) -> bool:
        return all(state.finished for state in self._states)

    def next_open(self, interval: int) -> int:
        """The first slot of this interval that is not yet decided (the open bucket's slot, if there is one)."""
        state = self._state(interval)
        if state.bucket is not None:
            return state.bucket.open_unix
        return state.decided_until

    def _state(self, interval: int) -> _Interval:
        for state in self._states:
            if state.interval == interval:
                return state
        raise ValidationFailure("unknown interval")

    def feed(self, trade: PublicTrade, *, gap_before: bool) -> list[CompletedBucket]:
        """Take the next trade. ``gap_before`` says trades are known to be missing just before it."""
        if self.finished:
            self.ignored_after_end += 1
            return []
        whole = int(trade.time.to_integral_value(rounding=ROUND_FLOOR))
        completed: list[CompletedBucket] = []
        in_range = False
        for state in self._states:
            if state.finished:
                continue
            slot = whole - whole % state.step
            if gap_before and state.bucket is not None:
                state.bucket.tainted = True
            if slot < state.floor:
                continue
            in_range = True
            if state.bucket is not None and slot > state.bucket.open_unix:
                completed.append(state.bucket.complete(state.interval))
                state.decided_until = state.bucket.open_unix + state.step
                state.bucket = None
            if slot >= state.end:
                state.finished = True
                state.decided_until = state.end
                continue
            if slot < max(state.decided_until, state.bucket.open_unix if state.bucket else 0):
                raise ValidationFailure("trade times ran backwards")
            if state.bucket is None:
                state.bucket = _Bucket(slot, trade, tainted=gap_before)
            else:
                state.bucket.add(trade)
        if not in_range:
            self.ignored_before_range += 1
        return completed
