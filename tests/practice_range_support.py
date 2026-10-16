"""Shared helpers for the practice-range tests: hand-built candles, a store and the golden strategy.

Nothing here touches the network. Candles are written row by row so every price in the golden test can be checked
by hand.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.application.candle_store import CandleStore
from trade_graph.contracts.candles import Candle
from trade_graph.contracts.practice_range import PracticeStrategy

DAY = date(2026, 7, 1)
WINDOW_START = datetime(2026, 7, 1, tzinfo=UTC)
STEP = timedelta(minutes=15)
RECEIVED = datetime(2026, 10, 1, tzinfo=UTC)  # every synthetic candle is received at import, long after it closed


def bar(symbol: str, index: int, o: str, h: str, low: str, c: str, volume: str = "1000", *,
        source: str = "synthetic", source_pair: str | None = None, start: datetime = WINDOW_START,
        received: datetime = RECEIVED) -> Candle:
    """The candle ``index`` bars after ``start`` (negative indices are warm-up before it)."""
    opened = start + STEP * index
    return Candle(symbol=symbol, source_pair=source_pair or symbol.replace("/", ""), interval_minutes=15,
                  open_time_utc=opened, close_time_utc=opened + STEP, available_at_utc=received,
                  open=Decimal(o), high=Decimal(h), low=Decimal(low), close=Decimal(c), volume=Decimal(volume),
                  trade_count=10, source=source)


def flat(symbol: str, indices: Iterable[int], price: str, **kwargs) -> list[Candle]:
    return [bar(symbol, index, price, price, price, price, **kwargs) for index in indices]


def open_store(path: Path) -> tuple[MarketDataDatabase, CandleStore]:
    database = MarketDataDatabase(path)
    return database, CandleStore(database)


def save(store: CandleStore, candles: list[Candle], *, label: str = "synthetic_fixture",
         source: str = "synthetic", at: datetime = RECEIVED) -> int:
    run_id = store.begin_run(kind="public_collect", source=source, evidence_label=label, started_at=at,
                             parameters={"test": "practice-range"})
    store.save(candles, run_id=run_id)
    store.finish_run(run_id, finished_at=at, status="ok", report={"saved": len(candles)})
    return run_id


def golden_candles() -> list[Candle]:
    """SYN/USD on 2026-07-01, 15-minute bars 0..95, plus one warm-up bar before the day (see the golden test)."""
    s = "SYN/USD"
    rows = [
        *flat(s, range(-1, 10), "99"),
        bar(s, 10, "99", "101.5", "98.8", "101"),  # closes above 100: the first entry signal
        bar(s, 11, "101.2", "102", "100.8", "101.6"),  # market entry fills at this open
        bar(s, 12, "101.6", "102.5", "101.4", "102.4"),
        bar(s, 13, "102.4", "104", "102.2", "103.8"),
        bar(s, 14, "103.8", "105.9", "103.5", "105.5"),
        bar(s, 15, "105.5", "106.5", "105.2", "106.1"),  # trades beyond the take-profit at 106.38
        bar(s, 16, "106.1", "106.2", "99.5", "99.6"),
        *flat(s, range(17, 20), "99.6"),
        bar(s, 20, "99.6", "100.6", "99.5", "100.4"),  # crosses above 100 again: the second entry signal
        bar(s, 21, "100.5", "100.9", "100.1", "100.2"),  # second entry fills at this open
        bar(s, 22, "97", "97.5", "96.5", "97.2"),  # opens below the stop at 98.59: a gap
        *flat(s, range(23, 60), "97.2"),
        bar(s, 60, "97.2", "100.3", "97.1", "100.2"),  # third entry signal
        bar(s, 61, "100.2", "100.4", "100", "100.3"),  # third entry fills at this open and is still held at the end
        *flat(s, range(62, 96), "100.3"),
    ]
    return rows


def golden_btc() -> list[Candle]:
    """BTC/USD with only the first and last bars of the day: the benchmark buys the first open, sells the last close."""
    return [bar("BTC/USD", 0, "50000", "50100", "49900", "50050", source_pair="XBTUSD"),
            bar("BTC/USD", 95, "54900", "55100", "54800", "55000", source_pair="XBTUSD")]


def cross_strategy(**changes) -> PracticeStrategy:
    """Buy at the next open when the close crosses above 100; stop 2%, take-profit 5%; half the equity per entry."""
    document = {
        "schema_version": 1, "strategy_id": "cross-hundred", "version": 1, "title": "Cross above one hundred",
        "hypothesis": "A close back above a round level starts a move; a test strategy, nothing more.",
        "interval_minutes": 15, "indicators": {},
        "entry": {"all_of": [{"left": {"candle": "close"}, "relation": "crosses_above", "right": {"level": "100"}}],
                  "order": {"kind": "market", "expires_after_bars": 1}},
        "exit": {"stop_loss_fraction": "0.02", "take_profit_fraction": "0.05"},
        "sizing": {"equity_fraction": "0.5"},
    }
    for key, value in changes.items():
        document[key] = value
    return PracticeStrategy.model_validate(document)
