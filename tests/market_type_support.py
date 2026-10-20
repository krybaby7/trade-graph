"""Synthetic hourly series for the market-type tests (W11-A, C10): a steady rise, a steady fall, a flat range, a gap.

Every price is an exact decimal built from whole numbers, so the series are the same on every platform. Candles are
saved under the evidence label ``synthetic_fixture``; nothing here touches the network.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from tests.practice_range_support import save
from trade_graph.application.candle_store import CandleStore
from trade_graph.contracts.candles import Candle
from trade_graph.contracts.practice_range import PracticeStrategy, ReplayWindow

HOUR = timedelta(hours=1)
SERIES_START = datetime(2026, 5, 1, tzinfo=UTC)  # the open of candle 0
# The default rules need 863 hourly candles before a coin can be labelled (ADX 14 reads that span, D136), so the
# first labelled candle of a series that starts at candle 0 is candle 862.
FIRST_LABELLED = 862


def candle(symbol: str, index: int, o: Decimal, h: Decimal, low: Decimal, c: Decimal, *, interval: int = 60,
           start: datetime = SERIES_START, source: str = "synthetic", received: datetime | None = None,
           volume: str = "1000") -> Candle:
    step = timedelta(minutes=interval)
    opened = start + step * index
    closed = opened + step
    return Candle(symbol=symbol, source_pair=symbol.replace("/", ""), interval_minutes=interval,
                  open_time_utc=opened, close_time_utc=closed, available_at_utc=received or closed,
                  open=o, high=h, low=low, close=c, volume=Decimal(volume), trade_count=10, source=source)


def closes_rising(count: int, *, base: str = "100", step: str = "0.2") -> list[Decimal]:
    return [Decimal(base) + Decimal(step) * index for index in range(count)]


def closes_falling(count: int, *, base: str = "400", step: str = "0.2") -> list[Decimal]:
    return [Decimal(base) - Decimal(step) * index for index in range(count)]


_ZIGZAG = ("0", "0.5", "1", "0.5", "0", "-0.5", "-1", "-0.5")


def closes_range(count: int, *, centre: str = "100") -> list[Decimal]:
    """A flat range: the close walks 100, 100.5, 101, 100.5, 100, 99.5, 99, 99.5 and again, every 8 hours."""
    return [Decimal(centre) + Decimal(_ZIGZAG[index % len(_ZIGZAG)]) for index in range(count)]


def closes_wandering(count: int, seed: int, *, drifts: tuple[int, ...] = (-25, 0, 25)) -> list[Decimal]:
    """A deterministic path in cents from 100.00: a drift per 100-hour block (falls, flats, rises) plus noise, from a
    64-bit linear congruential generator, so the same seed gives the same prices everywhere."""
    state = seed & ((1 << 64) - 1)
    price = 10_000
    closes = []
    for index in range(count):
        state = (state * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        noise = (state >> 33) % 241 - 120
        drift = drifts[(index // 100 + seed) % len(drifts)]
        price = max(1_000, price + drift + noise)
        closes.append(Decimal(price) / 100)
    return closes


def series(symbol: str, closes: list[Decimal], *, wick: str = "0.05", skip: Iterable[int] = (), first_index: int = 0,
           **kwargs) -> list[Candle]:
    """Candles whose open is the previous close and whose wicks reach ``wick`` beyond the body."""
    missing = set(skip)
    rows = []
    for offset, close in enumerate(closes):
        index = first_index + offset
        if index in missing:
            continue
        opened = closes[offset - 1] if offset else close
        rows.append(candle(symbol, index, opened, max(opened, close) + Decimal(wick),
                           min(opened, close) - Decimal(wick), close, **kwargs))
    return rows


def store_series(store: CandleStore, rows: list[Candle], *, at: datetime | None = None) -> int:
    """Save under ``synthetic_fixture``; the run's time defaults to just after the newest candle closed."""
    moment = at or max(row.available_at_utc for row in rows)
    return save(store, rows, at=moment)


def hourly_moment(index: int, *, minutes: int = 5, start: datetime = SERIES_START) -> datetime:
    """A moment ``minutes`` after candle ``index`` closed (the hourly scan runs a few minutes past the hour)."""
    return start + HOUR * (index + 1) + timedelta(minutes=minutes)


# --- the practice-range fixture: an hourly path whose window holds every market type -----------------------------

# Seed 2's hourly path is falling, then sideways, then rising inside the window; the missing hour 970 then makes
# the last 13 hours of the window unclear (a gap among the 863 hours the label reads).
MIXED_SEED = 2
MIXED_HOURS = 1000
MIXED_HOLE = 970
REPLAY_WINDOW = ReplayWindow(start=date(2026, 6, 7), end=date(2026, 6, 11))  # hourly candles 888 to 983
QUARTER = timedelta(minutes=15)


def mixed_hours(symbol: str = "SYN/USD", **kwargs) -> list[Candle]:
    return series(symbol, closes_wandering(MIXED_HOURS, MIXED_SEED), skip={MIXED_HOLE}, **kwargs)


def quarters(hours: list[Candle], *, first: datetime, last: datetime) -> list[Candle]:
    """15-minute candles inside each hourly candle that opens in [first, last): the price walks in four equal steps
    from the hour's open to its close, with 0.03 wicks. The missing hour has no quarters either."""
    rows = []
    for hour in hours:
        if not first <= hour.open_time_utc < last:
            continue
        step = (hour.close - hour.open) / 4
        for part in range(4):
            opened = hour.open + step * part
            closed = opened + step
            begin = hour.open_time_utc + QUARTER * part
            rows.append(hour.model_copy(update={
                "interval_minutes": 15, "open_time_utc": begin, "close_time_utc": begin + QUARTER,
                "available_at_utc": begin + QUARTER, "open": opened, "close": closed,
                "high": max(opened, closed) + Decimal("0.03"), "low": min(opened, closed) - Decimal("0.03")}))
    return rows


def busy_strategy(*market_types: tuple[str, str], version: int = 1, **changes) -> PracticeStrategy:
    """Buy at the next open whenever flat (the close is always above 1), sell at +0.6%, -1% or after 6 candles;
    ``market_types`` adds conditions such as ``("is", "rising")``. A test strategy, nothing more."""
    entry = [{"left": {"candle": "close"}, "relation": "above", "right": {"level": "1"}},
             *({"market_type": kind, "relation": relation} for relation, kind in market_types)]
    document = {
        "schema_version": 1, "strategy_id": "busy-test", "version": version, "title": "Always in, briefly",
        "hypothesis": "Trades often so every market type gets trades; a test strategy, nothing more.",
        "interval_minutes": 15, "indicators": {},
        "entry": {"all_of": entry, "order": {"kind": "market"}},
        "exit": {"stop_loss_fraction": "0.01", "take_profit_fraction": "0.006", "max_holding_bars": 6},
        "sizing": {"equity_fraction": "0.5"},
    }
    document.update(changes)
    return PracticeStrategy.model_validate(document)


def labels_by_index(label_at: Callable[[datetime], str], indices: Iterable[int]) -> dict[int, str]:
    return {index: label_at(hourly_moment(index)) for index in indices}


__all__ = [
    "FIRST_LABELLED", "HOUR", "SERIES_START", "candle", "closes_falling", "closes_range", "closes_rising",
    "closes_wandering", "MIXED_HOLE", "MIXED_HOURS", "MIXED_SEED", "REPLAY_WINDOW", "busy_strategy",
    "mixed_hours", "quarters",
    "hourly_moment", "labels_by_index", "series", "store_series",
]
