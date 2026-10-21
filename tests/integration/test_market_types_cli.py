"""``trade-graph market-types`` prints the newest labels and only reads; ``scan-coins`` labels its coins (W11-A; D287).

Synthetic candles and a synthetic ticker fixture; the network is blocked in every test.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

import pytest
from tests.integration.test_coin_scanner_cli import _args, _json, installation  # noqa: F401
from tests.market_type_support import (
    FIRST_LABELLED,
    HOUR,
    closes_falling,
    closes_rising,
    hourly_moment,
    series,
    store_series,
)
from tests.network_guard import block_network
from tests.practice_range_support import open_store

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.market_types.hourly import label_after_scan
from trade_graph.application.market_types.store import MarketTypeStore
from trade_graph.cli import main
from trade_graph.domain.clock import FrozenClock

SOURCE = "kraken_public_ohlc"
MOMENT = hourly_moment(FIRST_LABELLED + 10)


@pytest.fixture
def labelled(tmp_path, monkeypatch, capsys):
    block_network(monkeypatch)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    path = private / "trade_graph.sqlite"
    assert main(["init", "--database", str(path)]) == 0
    capsys.readouterr()
    market, candles = open_store(tmp_path / "market" / "market_data.sqlite")
    store_series(candles, series("SOL/USD", closes_rising(900), source=SOURCE))
    store_series(candles, series("ETH/USD", closes_falling(900), source=SOURCE))
    database = Database(path)
    clock = FrozenClock(MOMENT)
    yield path, database, candles, clock
    database.close()
    market.close()


def _label(database, candles, clock, moment, symbols) -> None:
    label_after_scan(candles=candles, labels=MarketTypeStore(database, clock), scan=None, day_symbols=symbols,
                     as_of=moment)


def test_the_newest_labels_in_plain_words_and_as_json_without_writing(labelled, capsys) -> None:
    path, database, candles, clock = labelled
    assert main(["market-types", "--database", str(path)]) == 0
    assert "No market types have been recorded yet" in capsys.readouterr().out
    _label(database, candles, clock, MOMENT - HOUR, {"SOL/USD"})
    _label(database, candles, clock, MOMENT, {"SOL/USD", "ETH/USD"})
    before = path.stat().st_mtime_ns
    assert main(["market-types", "--database", str(path)]) == 0
    text = capsys.readouterr().out
    assert "Market types (software's labels from 60-minute candles, market-type-rules@2; no AI" in text
    assert "ETH/USD is falling" in text and "SOL/USD is rising" in text
    assert text.index("ETH/USD") < text.index("SOL/USD")
    assert "1 rising, 0 sideways, 1 falling, 0 unclear" in text
    assert main(["market-types", "--database", str(path), "--format", "json", "--symbol", "SOL/USD"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["command"] == "market-types" and report["found"] is True and report["model_calls"] == 0
    assert [item["symbol"] for item in report["labels"]] == ["SOL/USD"]
    assert report["labels"][0]["market_type"] == "rising" and report["labels"][0]["as_of_utc"].startswith(
        "2026-06-06T")
    as_of = (MOMENT - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert main(["market-types", "--database", str(path), "--format", "json", "--as-of", as_of]) == 0
    earlier = json.loads(capsys.readouterr().out)
    assert [item["symbol"] for item in earlier["labels"]] == ["SOL/USD"]
    assert path.stat().st_mtime_ns == before


def test_an_old_label_is_marked_and_a_missing_database_is_refused(labelled, tmp_path, capsys) -> None:
    path, database, candles, clock = labelled
    _label(database, candles, clock, MOMENT - 3 * HOUR, {"SOL/USD"})
    assert main(["market-types", "--database", str(path), "--as-of",
                 MOMENT.strftime("%Y-%m-%dT%H:%M:%SZ")]) == 0
    assert "older than 2 hours" in capsys.readouterr().out
    absent = tmp_path / "private" / "absent.sqlite"
    with pytest.raises(SystemExit):
        main(["market-types", "--database", str(absent)])
    assert not absent.exists()
    older = tmp_path / "older.sqlite"
    sqlite3.connect(older).execute("CREATE TABLE something (x)").connection.close()
    assert main(["market-types", "--database", str(older)]) == 0
    assert "No market types have been recorded yet" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["market-types", "--database", str(path), "--as-of", "yesterday"])


def test_a_live_scan_labels_its_shortlist_and_says_so(installation, capsys) -> None:  # noqa: F811
    assert main(["scan-coins", *_args(installation, "--fixture-ticker", str(installation["ticker"]))]) == 0
    summary = _json(capsys)
    shortlist = [item["symbol"] for item in summary["shortlist"]]
    assert summary["market_types"]["status"] == "ok" and summary["market_types"]["labelled"] == len(shortlist)
    assert summary["market_types"]["scan_id"] == summary["scan_id"]
    connection = sqlite3.connect(installation["database"])
    rows = connection.execute("SELECT symbol, scan_id FROM market_type_labels ORDER BY symbol").fetchall()
    connection.close()
    assert rows == sorted((symbol, summary["scan_id"]) for symbol in shortlist)
    replay = ["--replay", "--as-of", "2026-10-16T12:05:00Z", "--source", "kraken_public_ohlc"]
    assert main(["scan-coins", *_args(installation, *replay)]) == 0
    assert "market_types" not in _json(capsys)  # a replay labels nothing live
