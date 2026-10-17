"""HQ opens the candle file read-only (T41, D199): never created, upgraded or written, and private like the store."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime

import pytest
from tests.chart_support import candles, market_file, write_candles

from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.adapters.persistence.market_data_readonly import MarketDataUnavailable, ReadOnlyMarketData
from trade_graph.api.hq_chart import ChartCandleFile
from trade_graph.application.candle_store import CandleStore
from trade_graph.domain.errors import ValidationFailure

END = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)


def _reason(path) -> str:
    with pytest.raises(MarketDataUnavailable) as caught:
        ReadOnlyMarketData(path)
    return caught.value.reason


def test_a_missing_file_is_reported_and_never_created(tmp_path) -> None:
    path = market_file(tmp_path)
    assert _reason(path) == "missing"
    assert not path.exists()
    handle = ChartCandleFile(path)
    assert handle.open() == (None, "missing")
    write_candles(path, candles("SOL/USD", 15, END, 5))
    store, status = handle.open()  # the collector made the file later: the next chart picks it up
    assert status == "connected" and isinstance(store, CandleStore)
    assert len(store.window("SOL/USD", 15, "synthetic", end_close_time=END, count=5, as_of=END.replace(
        hour=9)).candles) == 5
    handle.close()
    assert ChartCandleFile(None).open() == (None, "not_configured")


def test_privacy_rules_match_the_store(tmp_path) -> None:
    path = market_file(tmp_path)
    write_candles(path, candles("SOL/USD", 15, END, 3))
    os.chmod(path, 0o644)
    assert _reason(path) == "unreadable"
    os.chmod(path, 0o600)
    os.chmod(path.parent, 0o755)
    assert _reason(path) == "unreadable"
    os.chmod(path.parent, 0o700)
    link = path.parent / "link.sqlite"
    link.symlink_to(path)
    assert _reason(link) == "unreadable"
    ReadOnlyMarketData(path).close()


def test_a_file_that_is_not_a_candle_file_or_still_needs_an_upgrade_is_refused_unchanged(tmp_path) -> None:
    path = market_file(tmp_path)
    other = sqlite3.connect(path)
    other.execute("CREATE TABLE ledger_events (event_id TEXT)")
    other.commit()
    other.close()
    os.chmod(path, 0o600)
    assert _reason(path) == "unreadable"
    path.unlink()
    MarketDataDatabase(path).close()
    writer = sqlite3.connect(path)
    writer.execute("DELETE FROM market_data_migrations WHERE version = '0002'")
    writer.commit()
    writer.close()
    assert _reason(path) == "outdated"
    reader = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    assert [row[0] for row in reader.execute("SELECT version FROM market_data_migrations")] == ["0001"]
    reader.close()


def test_every_write_is_refused(tmp_path) -> None:
    path = market_file(tmp_path)
    write_candles(path, candles("SOL/USD", 15, END, 3))
    database = ReadOnlyMarketData(path)
    store = CandleStore(database)
    with pytest.raises(sqlite3.OperationalError, match="readonly|read-only|query_only"):
        database.execute("INSERT INTO market_data_migrations VALUES ('9999', 'x')")
    with pytest.raises(ValidationFailure, match="never writes"):
        store.begin_run(kind="public_collect", source="synthetic", evidence_label="synthetic_fixture",
                        started_at=END, parameters={})
    with database.snapshot():
        with database.snapshot():  # nested snapshots share one read
            assert database.query("SELECT COUNT(*) FROM market_candles")[0][0] == 3
    database.close()
