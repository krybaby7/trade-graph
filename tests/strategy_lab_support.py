"""Helpers for the strategy lab tests (W11-B, C11): a synthetic candle file, lab requests and a worker.

Everything is synthetic and labelled so: 15-minute SOL/USD candles that swing around 100 (``synthetic_fixture``) for
2026-09-01 to 2026-09-15, received on 2026-10-01; the account is the day-plan account (USD10,000, synthetic money
limits, ``slow-trend-pullback@1`` with one synthetic confirmation report). No network, model, key, paid call or order.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from tests.practice_range_support import cross_strategy, open_store, save
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.practice_range.replay import replay
from trade_graph.application.strategy_lab.queue import LabQueue
from trade_graph.application.strategy_lab.worker import StrategyLabWorker
from trade_graph.contracts.candles import Candle
from trade_graph.contracts.strategy_lab import LabRequest

SERIES_START = datetime(2026, 9, 1, tzinfo=UTC)
SERIES_DAYS = 14
RECEIVED = datetime(2026, 10, 1, tzinfo=UTC)
TUNE = (date(2026, 9, 1), date(2026, 9, 8))
CONFIRM = (date(2026, 9, 8), date(2026, 9, 15))
STRATEGY = cross_strategy()  # cross-hundred@1: buy when the close crosses above 100; stop 2%, target 5%
STUDY_ID = "cross-sep"


def swinging_candles(symbol: str = "SOL/USD", *, start: datetime = SERIES_START, days: int = SERIES_DAYS,
                     interval: int = 15) -> list[Candle]:
    """A deterministic swing around 100 (about 3% each way, a period of 16 hours), so the test strategy trades."""
    step = timedelta(minutes=interval)
    candles = []
    for index in range(days * 24 * 60 // interval):
        opened = start + step * index
        middle = 100 + 3 * math.sin(2 * math.pi * index / 64) + 0.5 * math.sin(2 * math.pi * index / 7)
        previous = 100 + 3 * math.sin(2 * math.pi * (index - 1) / 64) + 0.5 * math.sin(2 * math.pi * (index - 1) / 7)
        o, c = Decimal(f"{previous:.2f}"), Decimal(f"{middle:.2f}")
        high, low = max(o, c) + Decimal("0.15"), min(o, c) - Decimal("0.15")
        candles.append(Candle(symbol=symbol, source_pair=symbol.replace("/", ""), interval_minutes=interval,
                              open_time_utc=opened, close_time_utc=opened + step, available_at_utc=RECEIVED,
                              open=o, high=high, low=low, close=c, volume=Decimal("1000"), trade_count=10,
                              source="synthetic"))
    return candles


def candle_file(path: Path, symbols: Sequence[str] = ("SOL/USD",)) -> tuple[Any, CandleStore]:
    database, store = open_store(path)
    for symbol in symbols:
        save(store, swinging_candles(symbol), label="synthetic_fixture")
    return database, store


def register(strategy=STRATEGY, **changes: Any) -> LabRequest:
    document = strategy.canonical_document()
    document.update(changes)
    return LabRequest(kind="register_strategy", strategy_json=json.dumps(document))


def study(study_id: str = STUDY_ID, *, family: str = "cross-hundred", tune=TUNE, confirm=CONFIRM) -> LabRequest:
    return LabRequest(kind="create_study", study_id=study_id, strategy_id=family, tune_start=tune[0],
                      tune_end=tune[1], confirm_start=confirm[0], confirm_end=confirm[1])


def tune(ref: str = "cross-hundred@1", *, study_id: str = STUDY_ID, window=None, profiles=("okx-eea-spot",),
         symbol: str = "SOL/USD") -> LabRequest:
    return LabRequest(kind="tune", study_id=study_id, strategy_ref=ref, symbol=symbol, source="synthetic",
                      window_start=window[0] if window else None, window_end=window[1] if window else None,
                      cost_profiles=profiles)


def confirm(ref: str = "cross-hundred@1", *, study_id: str = STUDY_ID, profiles=("okx-eea-spot",),
            symbol: str = "SOL/USD") -> LabRequest:
    return LabRequest(kind="confirm", study_id=study_id, strategy_ref=ref, symbol=symbol, source="synthetic",
                      cost_profiles=profiles)


def worker_for(account, candles: CandleStore | None, *, replay_fn: Callable = replay,
               records: list | None = None, **options: Any) -> StrategyLabWorker:
    return StrategyLabWorker(account.database, account.clock, candles=candles, replay_fn=replay_fn,
                             record=None if records is None else records.append, **options)


def submit(account, requests: Sequence[LabRequest], *, batch_id: str = "batch-1", wake_id: str | None = None):
    return LabQueue(account.database, account.clock).submit(account.portfolio, list(requests), batch_id=batch_id,
                                                            wake_id=wake_id)


def statuses(account, batch_id: str = "batch-1") -> list[tuple[str, str]]:
    return [(record.request.kind, record.status) for record in LabQueue(account.database, account.clock)
            .requests(batch_id=batch_id)]


__all__ = ["CONFIRM", "STRATEGY", "STUDY_ID", "TUNE", "candle_file", "confirm", "register", "statuses", "study",
           "submit", "swinging_candles", "tune", "worker_for"]
