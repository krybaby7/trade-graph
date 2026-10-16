"""Candle-database migration 0002 (T29): OKX public candles become their own source, old files keep every row.

SQLite cannot widen a CHECK constraint in place, so 0002 rebuilds ``candle_series`` and ``candle_runs`` the way
SQLite documents (foreign keys off for the copy, then a full foreign-key check before commit). These tests upgrade a
real 0001 file holding rows in every table and check that nothing was lost, renumbered or loosened.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.adapters.persistence.market_data_schema import MARKET_DATA_MIGRATIONS, VERSION_0001
from trade_graph.application.candle_store import CandleStore
from trade_graph.contracts.candles import CANDLE_SOURCES, Candle

T0 = datetime(2026, 7, 1, tzinfo=UTC)
REGISTERED = [version for version, _ in MARKET_DATA_MIGRATIONS]
TABLES = ("candle_series", "candle_runs", "candle_run_results", "market_candles", "pair_catalog_observations")


def _version_0001_file(path) -> None:
    path.parent.mkdir(mode=0o700)
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("CREATE TABLE market_data_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    for statement in VERSION_0001:
        connection.execute(statement)
    connection.execute("INSERT INTO market_data_migrations VALUES ('0001', '2026-10-13T00:00:00.000000Z')")
    connection.execute("COMMIT")
    connection.close()
    path.chmod(0o600)
    raw = sqlite3.connect(path, isolation_level=None)  # rows written the 0001 way, before any upgrade
    raw.execute("PRAGMA foreign_keys=ON")
    for run in range(1, 4):
        raw.execute("INSERT INTO candle_runs (kind, source, evidence_label, started_at, parameters_json) VALUES "
                    "('public_collect', 'kraken_public_ohlc', 'live_public', ?, '{}')", (run,))
    raw.execute("INSERT INTO candle_run_results VALUES (1, 10, 'ok', '{}')")
    raw.execute("INSERT INTO candle_series (symbol, source, interval_minutes, source_pair) VALUES "
                "('BTC/USD', 'kraken_public_ohlc', 15, 'XBTUSD')")
    raw.execute("INSERT INTO candle_series (symbol, source, interval_minutes, source_pair) VALUES "
                "('ETH/USD', 'kraken_ohlcvt_archive', 60, 'ETHUSD')")
    raw.execute("INSERT INTO market_candles VALUES (1, 1782864000, 1, 1782864900000000, 2, '1','2','0.5','1.5', NULL, "
                "'3', 4, zeroblob(32))")
    raw.execute("INSERT INTO pair_catalog_observations (observed_at, run_id, rules_version, content_hash, "
                "document_json) VALUES (5, 3, 'pair-rules-2026-10-13.1', zeroblob(32), '[]')")
    raw.close()


def _rows(path) -> dict[str, list]:
    connection = sqlite3.connect(path)
    try:
        return {table: connection.execute(f"SELECT * FROM {table} ORDER BY 1, 2, 3").fetchall() for table in TABLES}
    finally:
        connection.close()


def test_0002_is_registered_after_0001_and_okx_is_a_source() -> None:
    versions = [version for version, _ in MARKET_DATA_MIGRATIONS]
    assert versions[:2] == ["0001", "0002"] and versions == sorted(versions)
    assert "okx_public_candles" in CANDLE_SOURCES


def test_an_old_file_upgrades_with_every_row_kept_and_rules_intact(tmp_path) -> None:
    path = tmp_path / "market" / "market_data.sqlite"
    _version_0001_file(path)
    before = _rows(path)
    database = MarketDataDatabase(path)
    try:
        versions = [row[0] for row in database.query("SELECT version FROM market_data_migrations ORDER BY version")]
        assert versions == REGISTERED
        assert database.query("PRAGMA foreign_keys")[0][0] == 1
        assert database.query("PRAGMA foreign_key_check") == []
        triggers = {row[0] for row in database.query("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        assert {f"{table}_{verb}" for table in TABLES for verb in ("no_update", "no_delete")} <= triggers
        for table in ("candle_series", "candle_runs"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                database.execute(f"UPDATE {table} SET source = source")
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                database.execute(f"DELETE FROM {table}")
        with pytest.raises(sqlite3.IntegrityError):
            database.execute("INSERT INTO candle_series (symbol, source, interval_minutes, source_pair) VALUES "
                             "('X/USD', 'made_up_source', 15, 'XUSD')")
        with pytest.raises(sqlite3.IntegrityError):  # foreign keys are enforced again
            database.execute("INSERT INTO market_candles VALUES (99, 1, 1, 1, 1, '1','1','1','1', NULL, '1', 1, "
                             "zeroblob(32))")
        store = CandleStore(database)
        run = store.begin_run(kind="public_collect", source="okx_public_candles", evidence_label="synthetic_fixture",
                              started_at=T0, parameters={})
        assert run == 4  # numbering continues after the old runs
        store.save([Candle(symbol="SOL/USDT", source_pair="SOL-USDT", interval_minutes=15, open_time_utc=T0,
                           close_time_utc=T0 + timedelta(minutes=15), available_at_utc=T0 + timedelta(hours=1),
                           open=Decimal("150"), high=Decimal("151"), low=Decimal("149"), close=Decimal("150.5"),
                           volume=Decimal("12"), trade_count=0, source="okx_public_candles")], run_id=run)
        window = store.window("BTC/USD", 15, "kraken_public_ohlc", end_close_time=datetime.fromtimestamp(
            1782864900, UTC), count=1, mode="replay")
        assert window.candles[0].close == Decimal("1.5")
    finally:
        database.close()
    after = _rows(path)
    for table in TABLES:
        assert after[table][:len(before[table])] == before[table], table
    reopened = MarketDataDatabase(path)
    assert sorted(row[0] for row in reopened.query("SELECT version FROM market_data_migrations")) == REGISTERED
    reopened.close()


def test_a_new_file_gets_both_versions(tmp_path) -> None:
    database = MarketDataDatabase(tmp_path / "fresh" / "market_data.sqlite")
    try:
        assert [row[0] for row in database.query("SELECT version FROM market_data_migrations ORDER BY version")] == (
            REGISTERED)
        sql = database.query("SELECT sql FROM sqlite_master WHERE name = 'candle_series'")[0][0]
        assert "okx_public_candles" in sql
    finally:
        database.close()
