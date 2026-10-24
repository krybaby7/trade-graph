"""Fixed synthetic OHLCV series for the indicator tests, built with integer arithmetic only.

No random module and no libm calls are involved, so every platform and Python version produces the
same candles. Prices are exact Decimals; the indicator tests convert them to float64 only at the
library boundary (the same path the production wrapper uses).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trade_graph.contracts.candles import Candle

_MASK = (1 << 64) - 1
_EPOCH_OPEN = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)


class _Lcg:
    """64-bit linear congruential generator (Knuth MMIX constants); exact on every platform."""

    def __init__(self, seed: int) -> None:
        self.state = seed & _MASK

    def step(self, bound: int) -> int:
        """A value in [0, bound)."""
        self.state = (self.state * 6364136223846793005 + 1442695040888963407) & _MASK
        return (self.state >> 33) % bound

    def centered(self, half_width: int) -> int:
        """Roughly triangular noise in [-half_width, half_width]."""
        return (self.step(half_width + 1) + self.step(half_width + 1)) - half_width


@dataclass(frozen=True, slots=True)
class SeriesSpec:
    name: str
    count: int
    seed: int
    start_price: int  # in price units (see scale)
    scale: int  # decimal places of the price
    volume_scale: int  # decimal places of the volume
    base_volume: int  # in volume units


def _trend_pullback(spec: SeriesSpec, count: int) -> list[tuple[int, int, int, int, int]]:
    """BTC-like: long regimes of drift up and down with pullbacks; steady volume that rises on big moves."""
    rng = _Lcg(spec.seed)
    price = spec.start_price
    rows: list[tuple[int, int, int, int, int]] = []
    regime_drift = [8, -6, 12, -9, 5, -4]  # parts per 10,000 per bar
    for index in range(count):
        drift = regime_drift[(index // 100) % len(regime_drift)]
        move = price * (drift + rng.centered(40)) // 10_000
        opened = price
        closed = max(1, price + move)
        wick_up = price * rng.step(25) // 10_000
        wick_down = price * rng.step(25) // 10_000
        high = max(opened, closed) + wick_up
        low = max(1, min(opened, closed) - wick_down)
        volume = spec.base_volume + abs(move) * spec.base_volume // max(1, price // 300) + rng.step(spec.base_volume)
        rows.append((opened, high, low, closed, volume))
        price = closed
    return rows


def _range_burst(spec: SeriesSpec, count: int) -> list[tuple[int, int, int, int, int]]:
    """Alt-coin-like: mean-reverting range, a volatility burst, and one single-bar jump."""
    rng = _Lcg(spec.seed)
    price = spec.start_price
    anchor = spec.start_price
    rows: list[tuple[int, int, int, int, int]] = []
    for index in range(count):
        wide = 3 if 300 <= index < 360 else 1
        pull = (anchor - price) * 6 // 100
        move = pull + price * rng.centered(60 * wide) // 10_000
        if index == 420:
            move += price * 8 // 100
        opened = price
        closed = max(1, price + move)
        wick_up = price * rng.step(40 * wide) // 10_000
        wick_down = price * rng.step(40 * wide) // 10_000
        high = max(opened, closed) + wick_up
        low = max(1, min(opened, closed) - wick_down)
        volume = spec.base_volume * (1 + wide) + abs(move) * spec.base_volume // max(1, price // 100) \
            + rng.step(spec.base_volume * 2)
        rows.append((opened, high, low, closed, volume))
        price = closed
    return rows


SERIES_SPECS = {
    "trend_pullback": SeriesSpec("trend_pullback", 600, 20261013, 3_000_000, 2, 4, 5_000),
    "range_burst": SeriesSpec("range_burst", 600, 7_654_321, 12_000, 4, 0, 40_000),
}
_BUILDERS = {"trend_pullback": _trend_pullback, "range_burst": _range_burst}


def build_candles(name: str, *, interval_minutes: int = 15, count: int | None = None) -> list[Candle]:
    """Completed candles for a named series, each received a few seconds after its close."""
    spec = SERIES_SPECS[name]
    rows = _BUILDERS[name](spec, spec.count if count is None else count)
    step = timedelta(minutes=interval_minutes)
    candles: list[Candle] = []
    for index, (opened, high, low, closed, volume) in enumerate(rows):
        open_time = _EPOCH_OPEN + step * index
        candles.append(Candle(
            symbol="SYN/USD", source_pair="SYNUSD", interval_minutes=interval_minutes, open_time_utc=open_time,
            close_time_utc=open_time + step, available_at_utc=open_time + step + timedelta(seconds=3),
            open=Decimal(opened).scaleb(-spec.scale), high=Decimal(high).scaleb(-spec.scale),
            low=Decimal(low).scaleb(-spec.scale), close=Decimal(closed).scaleb(-spec.scale),
            volume=Decimal(volume).scaleb(-spec.volume_scale), trade_count=1 + volume % 97, source="synthetic",
        ))
    return candles
