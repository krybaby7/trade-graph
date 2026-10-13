"""A candle collection run never touches the financial database or the existing hourly history (T25 acceptance)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.candle_support import FIXTURES, AdvancingSleeper, FakeKraken, QueueTransport, listed_pairs
from tests.network_guard import block_network

from trade_graph.adapters.market.rate_limit import KrakenRestLimiter
from trade_graph.adapters.persistence.db import Database
from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.application.candle_collection_plan import CollectionScope
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.collect_candles import collect_candles, refresh_catalog
from trade_graph.application.collect_price_history import collect_public_hourly_history
from trade_graph.application.price_history import HistoryStore
from trade_graph.domain.clock import FrozenClock

NOW = datetime(2026, 10, 13, 15, 30, 20, tzinfo=UTC)
FEATURES = ["sma_20", "sma_50", "pullback_from_high", "range_high", "range_low", "midpoint", "hourly_return"]
ASSET_PAIRS = (FIXTURES / "asset_pairs_2026-10-13.json").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hourly_state(path: Path) -> tuple[list[tuple], dict]:
    database = Database(path)
    try:
        rows = [tuple(row) for row in database.execute("SELECT * FROM hourly_candles ORDER BY sequence").fetchall()]
        store = HistoryStore(database)
        as_of = NOW + timedelta(minutes=5)  # after the receipt of the last hourly collection
        snapshots = {symbol: store.snapshot(symbol, as_of, FEATURES) for symbol in ("BTC/USD", "ETH/USD")}
        return rows, json.loads(json.dumps(snapshots, default=str))
    finally:
        database.close()


def test_hourly_history_and_its_feature_snapshots_are_byte_for_byte_unchanged(tmp_path, monkeypatch) -> None:
    financial = tmp_path / "paper.sqlite"
    clock = FrozenClock(NOW)
    exchange = FakeKraken(clock, list(listed_pairs().values()))
    database = Database(financial)
    try:
        report = collect_public_hourly_history(database, clock, exchange, hours=60)
    finally:
        database.close()
    assert report["status"] == "ok" and report["symbols"]["BTC/USD"]["fetched"] == 60
    rows_before, snapshots_before = _hourly_state(financial)
    assert len(rows_before) == 120 and snapshots_before["BTC/USD"]["values"] and snapshots_before["ETH/USD"]["values"]
    digest_before = _digest(financial)

    opened: list[str] = []
    real_connect = sqlite3.connect

    def spy(database, *args, **kwargs):
        opened.append(str(database))
        return real_connect(database, *args, **kwargs)

    market_path = tmp_path / "market" / "market_data.sqlite"
    with monkeypatch.context() as patched:
        patched.setattr(sqlite3, "connect", spy)
        market = MarketDataDatabase(market_path)
        try:
            store = CandleStore(market)
            refresh_catalog(store, clock, QueueTransport([ASSET_PAIRS], clock, delay_seconds=1),
                            "recorded_public_fixture")
            limiter = KrakenRestLimiter(clock, AdvancingSleeper(clock))
            collected = collect_candles(store, clock, exchange, limiter, CollectionScope(), 100,
                                        "recorded_public_fixture")
        finally:
            market.close()
    assert collected["status"] == "ok" and collected["totals"]["inserted"] == 41 * 720
    hourly = [item for item in collected["series"] if (item["symbol"], item["interval_minutes"]) == ("BTC/USD", 60)]
    assert hourly[0]["inserted"] == 720

    assert {Path(item).resolve() for item in opened} == {market_path.resolve()}  # only the candle file was opened
    assert _digest(financial) == digest_before
    rows_after, snapshots_after = _hourly_state(financial)
    assert rows_after == rows_before and snapshots_after == snapshots_before


def test_the_two_stores_share_no_table(tmp_path) -> None:
    financial = Database(tmp_path / "paper.sqlite")
    market = MarketDataDatabase(tmp_path / "market" / "market_data.sqlite")
    try:
        paper = {row[0] for row in financial.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        candles = {row[0] for row in market.query("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "hourly_candles" in paper and "market_candles" not in paper
        assert "market_candles" in candles and "hourly_candles" not in candles
        assert paper & candles <= {"sqlite_sequence"}
    finally:
        financial.close()
        market.close()
