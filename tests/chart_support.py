"""Synthetic records for the HQ chart tests (T41). No network, model, key or real order.

A day-plan paper account (``tests/day_plan_support.py``: USD10,000, SOL near 100, synthetic money limits), a private
candle file written through the real ``CandleStore`` with deterministic synthetic candles (evidence label
``synthetic_fixture``), and the namespace HQ's chart projection reads (``api/hq_chart.py``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from tests.day_plan_support import day_plan_account, entry, install, plan
from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.adapters.persistence.market_data_readonly import ReadOnlyMarketData
from trade_graph.application.candle_store import CandleStore
from trade_graph.contracts.candles import Candle, align_close_time

D = Decimal
SOURCE = "synthetic"


def price_at(index: int) -> Decimal:
    """A deterministic zig-zag around 100: up 0.07 for 9 candles, down 0.05 for 7, so every indicator moves."""
    cycle, step = divmod(index, 16)
    level = D("100") + D("0.13") * cycle % D("3")
    return (level + (D("0.07") * step if step < 9 else D("0.63") - D("0.05") * (step - 9))).quantize(D("0.01"))


def candles(symbol: str, interval: int, end_close: datetime, count: int, *, source: str = SOURCE,
            skip: Iterable[datetime] = (), received_after_seconds: int = 20) -> list[Candle]:
    """``count`` completed candles ending at ``end_close``, received a little after each closed; ``skip`` leaves
    those close times out (a missing slot)."""
    step = timedelta(minutes=interval)
    missing = set(skip)
    result = []
    for offset in range(count):
        close = end_close - step * (count - 1 - offset)
        if close in missing:
            continue
        close_price = price_at(offset)
        open_price = price_at(offset - 1) if offset else close_price
        result.append(Candle(
            symbol=symbol, source_pair=symbol.replace("/", ""), interval_minutes=interval, open_time_utc=close - step,
            close_time_utc=close, available_at_utc=close + timedelta(seconds=received_after_seconds),
            open=open_price, high=max(open_price, close_price) + D("0.05"),
            low=min(open_price, close_price) - D("0.04"),
            close=close_price, volume=D("12.5"), trade_count=3, source=source))
    return result


def market_file(tmp_path: Path) -> Path:
    folder = tmp_path / "market"
    folder.mkdir(mode=0o700, exist_ok=True)
    return folder / "market_data.sqlite"


def write_candles(path: Path, items: list[Candle]) -> None:
    """Store candles as one synthetic run through the real store, then close the writer."""
    database = MarketDataDatabase(path)
    try:
        store = CandleStore(database)
        run = store.begin_run(kind="public_collect", source=items[0].source, evidence_label="synthetic_fixture",
                              started_at=min(item.available_at_utc for item in items),
                              parameters={"note": "synthetic HQ chart test candles"})
        for start in range(0, len(items), 40_000):
            store.save(items[start:start + 40_000], run_id=run)
    finally:
        database.close()


def read_only(path: Path) -> CandleStore:
    return CandleStore(ReadOnlyMarketData(path))


def runtime(account, store: CandleStore | None, *, status: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(database=account.database, clock=account.clock, ledger=account.ledger,
                           execution=account.execution, portfolio_id=account.portfolio, chart_candles=store,
                           chart_candles_status=status or ("connected" if store is not None else "not_configured"))


def chart_plan(**changes):
    document = {
        "indicators": [{"indicator": "RSI", "interval_minutes": 15},
                       {"indicator": "SMA", "interval_minutes": 15,
                        "parameters": [{"name": "timeperiod", "value": 20}]},
                       {"indicator": "BBANDS", "interval_minutes": 15},
                       {"indicator": "EMA", "interval_minutes": 60}],
        "alarms": [{"kind": "quote_price", "symbol": "SOL/USD", "relation": "crosses_below", "level": "95"},
                   {"kind": "indicator", "symbol": "SOL/USD", "relation": "below", "level": "30", "indicator": "RSI",
                    "interval_minutes": 15},
                   {"kind": "candle_price", "symbol": "SOL/USD", "relation": "above", "level": "110",
                    "price": "close", "interval_minutes": 60}],
        "entries": [entry("5", "99.5", "97", "104"), entry("3", "98", "96")],
    }
    document.update(changes)
    return plan(**document)


def planned_account(tmp_path, **changes):
    """The synthetic account with one SOL day plan installed and its resting entries sent to the paper broker."""
    account = day_plan_account(tmp_path)
    installed = install(account, chart_plan(**changes))
    account.dispatch()
    return account, installed


def chart_now(account, interval: int = 15) -> datetime:
    return align_close_time(account.clock.now(), interval)


def insert_shortlist(database, symbols: list[str], *, as_of: str, scan_id: str = "scan-synthetic-1") -> None:
    """A minimal live scan record (synthetic) that shortlisted ``symbols``, written straight into migration 0031."""
    document = json.dumps({"label": "synthetic HQ chart test scan"}, sort_keys=True)
    with database.transaction() as connection:
        sequence = connection.execute(
            """INSERT INTO coin_scans (scan_id, mode, as_of, computed_at, recorded_at, ranking_version, status,
               error, evidence, optimistic, usd_pairs, ranked_pairs, shortlisted, document_json, document_sha256)
               VALUES (?, 'live', ?, ?, ?, 'synthetic@1', 'ok', NULL, 'synthetic_fixture', 0, ?, ?, ?, ?, ?)""",
            (scan_id, as_of, as_of, as_of, len(symbols), len(symbols), len(symbols), document,
             hashlib.sha256(document.encode("ascii")).hexdigest())).lastrowid
        for position, symbol in enumerate(symbols, start=1):
            connection.execute("INSERT INTO coin_scan_pairs VALUES (?, ?, ?, ?, 1, '{}')",
                               (sequence, position, symbol, position))
            connection.execute("INSERT INTO coin_shortlist_entries VALUES (?, ?, ?, '{}')",
                               (sequence, position, symbol))


def table_counts(connection) -> dict[str, int]:
    names = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
    return {name: connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in names}


__all__ = ["D", "SOURCE", "candles", "chart_now", "chart_plan", "insert_shortlist", "market_file", "planned_account",
           "price_at", "read_only", "runtime", "table_counts", "write_candles"]
