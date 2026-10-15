"""The candle collection commands: JSON reports, exit codes, refusals and a read-only coverage view. No network."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.candle_support import (
    FIXTURES,
    AdvancingSleeper,
    FakeKraken,
    HttpRateLimited,
    QueueTransport,
    error_wire,
    listed_pairs,
    unix,
)
from tests.network_guard import block_network

from trade_graph.adapters.market.kraken_catalog import CATALOG_MAXIMUM_RESPONSE_BYTES
from trade_graph.adapters.market.public import HttpxTextTransport
from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.application.candle_store import CandleStore
from trade_graph.cli import build_parser, main
from trade_graph.contracts.candles import Candle
from trade_graph.domain.clock import FrozenClock, SystemClock
from trade_graph.market_data_cli import collect as collect_cli

NOW = datetime(2026, 10, 13, 15, 30, 20, tzinfo=UTC)
ASSET_PAIRS = (FIXTURES / "asset_pairs_2026-10-13.json").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


class Scripted:
    """Replaces the clock, sleeper and transports of the command with scripted ones."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, catalog: list | None = None, **exchange_options) -> None:
        self.clock = FrozenClock(NOW)
        self.sleeper = AdvancingSleeper(self.clock)
        self.catalog = QueueTransport(catalog if catalog is not None else [ASSET_PAIRS] * 3, self.clock,
                                      delay_seconds=1)
        self.exchange = FakeKraken(self.clock, list(listed_pairs().values()), **exchange_options)
        monkeypatch.setattr(collect_cli, "build_clock", lambda: self.clock)
        monkeypatch.setattr(collect_cli, "build_sleeper", lambda: self.sleeper)
        monkeypatch.setattr(collect_cli, "build_transports", lambda: (self.catalog, self.exchange))


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    out = capsys.readouterr().out
    return code, json.loads(out) if out.strip() else {}


def database_path(tmp_path: Path) -> str:
    return str(tmp_path / "market" / "market_data.sqlite")


# --- the command line -----------------------------------------------------------------------------------------------


def test_the_collect_command_has_bounded_defaults_and_no_credential_options() -> None:
    args = build_parser().parse_args(["collect-kraken-candles"])
    assert args.market_database == "runtime/market_data.sqlite"
    assert args.smoke is False and args.catalog_only is False
    for name in ("api_key", "api_secret", "paid_calls_enabled", "mode", "owner_directory", "database"):
        assert not hasattr(args, name)
    assert build_parser().parse_args(["candle-coverage"]).market_database == "runtime/market_data.sqlite"


def test_the_real_transports_clock_and_sleeper_are_the_bounded_ones() -> None:
    catalog, ohlc = collect_cli.build_transports()
    assert isinstance(catalog, HttpxTextTransport) and isinstance(ohlc, HttpxTextTransport)
    assert catalog.maximum_response_bytes == CATALOG_MAXIMUM_RESPONSE_BYTES
    assert ohlc.maximum_response_bytes == 1_048_576
    assert isinstance(collect_cli.build_clock(), SystemClock) and collect_cli.build_sleeper() is time.sleep


@pytest.mark.parametrize("argv", [
    ["--intervals", "30"], ["--intervals", ""], ["--intervals", "5,5"], ["--intervals", "five"],
    ["--max-requests", "0"], ["--max-requests", "1"], ["--max-requests", "10002"], ["--max-requests", "-3"],
    ["--max-requests", "x"],
    ["--pairs", "btc/usd"], ["--pairs", ""], ["--pairs", "BTC/EUR"], ["--pairs", "BTC/USD,BTC/USD"],
    ["--one-minute-pairs", "BTC"], ["--smoke", "--pairs", "SOL/USD"], ["--smoke", "--intervals", "5"],
    ["--smoke", "--max-requests", "3"], ["--smoke", "--one-minute-pairs", "SOL/USD"],
    ["--smoke", "--catalog-only"], ["--catalog-only", "--intervals", "5"], ["--catalog-only", "--pairs", "SOL/USD"],
])
def test_bad_options_exit_2_before_any_file_or_request(tmp_path, monkeypatch, capsys, argv: list[str]) -> None:
    scripted = Scripted(monkeypatch)
    path = database_path(tmp_path)
    with pytest.raises(SystemExit) as refused:
        main(["collect-kraken-candles", "--market-database", path, *argv])
    assert refused.value.code == 2
    assert not Path(path).exists() and scripted.catalog.calls == [] and scripted.exchange.calls == []
    assert capsys.readouterr().err


# --- running --------------------------------------------------------------------------------------------------------


def test_smoke_collects_bitcoin_and_ether_at_15_minutes_in_at_most_four_requests(tmp_path, monkeypatch, capsys) -> None:
    scripted = Scripted(monkeypatch)
    path = database_path(tmp_path)
    code, report = run(capsys, "collect-kraken-candles", "--market-database", path, "--smoke")
    assert code == 0 and report["status"] == "ok" and report["command"] == "collect-kraken-candles"
    assert report["evidence_label"] == "live_public_smoke" and report["mode"] == "smoke"
    assert report["scope"] == {"intervals": [15], "symbols": ["BTC/USD", "ETH/USD"], "one_minute_symbols": []}
    assert [(item["symbol"], item["interval_minutes"]) for item in report["series"]] == [
        ("BTC/USD", 15), ("ETH/USD", 15)]
    assert report["requests"]["made"] == 2 and report["requests_total"] == 3 <= 4
    assert len(scripted.catalog.calls) == 1 and len(scripted.exchange.calls) == 2
    assert report["paid_calls_enabled"] is False and report["live_trading_enabled"] is False
    assert report["catalog_refresh"]["status"] == "ok" and report["market_database"] == path
    database = MarketDataDatabase(path)
    try:
        store = CandleStore(database)
        assert {(item.symbol, item.interval_minutes, item.source, item.candle_count) for item in store.series()} == {
            ("BTC/USD", 15, "kraken_public_ohlc", 720), ("ETH/USD", 15, "kraken_public_ohlc", 720)}
        assert [(run_.kind, run_.evidence_label) for run_ in store.runs()] == [
            ("catalog", "live_public_smoke"), ("public_collect", "live_public_smoke")]
    finally:
        database.close()


def test_a_throttled_smoke_run_stays_within_four_requests(tmp_path, monkeypatch, capsys) -> None:
    soon = unix(NOW) + 5
    once = Scripted(monkeypatch)
    once.exchange.queue("XBTUSD", 15, error_wire(f"EService: Throttled: {soon}"))
    code, report = run(capsys, "collect-kraken-candles", "--market-database", database_path(tmp_path), "--smoke")
    assert code == 0 and report["requests_total"] == 4 and report["requests"]["throttle_retries"] == 1
    twice = Scripted(monkeypatch)
    twice.exchange.queue("XBTUSD", 15, *[error_wire(f"EService: Throttled: {soon}")] * 2)
    code, report = run(capsys, "collect-kraken-candles", "--market-database", str(tmp_path / "second.sqlite"),
                       "--smoke")
    assert code == 1 and report["stop_reason"] == "throttled" and report["requests_total"] == 3 <= 4


def test_a_normal_run_uses_the_live_label_the_given_scope_and_prints_one_json_report(
        tmp_path, monkeypatch, capsys) -> None:
    Scripted(monkeypatch)
    code, report = run(capsys, "collect-kraken-candles", "--market-database", database_path(tmp_path),
                       "--pairs", "SOL/USD,LINK/USD", "--intervals", "60,5", "--one-minute-pairs", "",
                       "--max-requests", "50")
    assert code == 0 and report["status"] == "ok" and report["mode"] == "normal"
    assert report["evidence_label"] == "live_public"
    assert report["scope"] == {"intervals": [5, 60], "symbols": ["SOL/USD", "LINK/USD"], "one_minute_symbols": []}
    assert report["requests"]["max"] == 49 and report["counts"]["planned_series"] == 4
    assert report["series"][0]["interval_minutes"] == 5 and report["series"][-1]["interval_minutes"] == 60
    assert set(report) >= {"series", "not_reached", "skipped_pairs", "storage", "cadence", "totals", "by_interval"}


def test_the_default_run_covers_every_eligible_pair_and_the_one_minute_default(tmp_path, monkeypatch, capsys) -> None:
    scripted = Scripted(monkeypatch)
    code, report = run(capsys, "collect-kraken-candles", "--market-database", database_path(tmp_path))
    assert code == 0 and report["counts"]["planned_series"] == 41 and report["requests"]["max"] == 1999
    assert report["scope"]["one_minute_symbols"] == ["BTC/USD", "ETH/USD"]
    assert len(scripted.exchange.calls) == 41 and report["requests_total"] == 42


def test_a_run_that_is_not_complete_exits_1_and_says_what_was_not_reached(tmp_path, monkeypatch, capsys) -> None:
    Scripted(monkeypatch)
    code, report = run(capsys, "collect-kraken-candles", "--market-database", database_path(tmp_path),
                       "--max-requests", "4")
    # The limit counts every request of the run, the pair-list refresh included: 1 + 3.
    assert code == 1 and report["status"] == "partial" and report["stop_reason"] == "max_requests"
    assert report["requests"]["made"] == 3 and report["requests_total"] == 4 and len(report["not_reached"]) == 38


def test_a_failed_series_exits_1(tmp_path, monkeypatch, capsys) -> None:
    scripted = Scripted(monkeypatch)
    scripted.exchange.queue("SOLUSD", 60, "not json")
    code, report = run(capsys, "collect-kraken-candles", "--market-database", database_path(tmp_path),
                       "--pairs", "SOL/USD,LINK/USD", "--intervals", "60", "--one-minute-pairs", "")
    assert code == 1 and report["status"] == "partial" and report["counts"]["failed"] == 1


def test_catalog_only_refreshes_the_pair_list_and_collects_nothing(tmp_path, monkeypatch, capsys) -> None:
    scripted = Scripted(monkeypatch)
    path = database_path(tmp_path)
    code, report = run(capsys, "collect-kraken-candles", "--market-database", path, "--catalog-only")
    assert code == 0 and report["status"] == "ok" and report["mode"] == "catalog_only"
    assert report["catalog"]["usd_pairs"] == 44 and report["catalog"]["eligible_for_collection"] == 13
    assert report["requests_total"] == 1 and scripted.exchange.calls == []
    database = MarketDataDatabase(path)
    try:
        store = CandleStore(database)
        assert store.series() == [] and store.latest_catalog(as_of=NOW + timedelta(hours=1)) is not None
        assert [run_.kind for run_ in store.runs()] == ["catalog"]
    finally:
        database.close()


def test_a_failed_catalog_with_an_earlier_one_stored_still_collects_but_the_run_is_not_ok(
        tmp_path, monkeypatch, capsys) -> None:
    Scripted(monkeypatch)
    path = database_path(tmp_path)
    assert run(capsys, "collect-kraken-candles", "--market-database", path, "--catalog-only")[0] == 0
    scripted = Scripted(monkeypatch, catalog=[RuntimeError("SECRET down")])
    scripted.clock.advance(3600)
    code, report = run(capsys, "collect-kraken-candles", "--market-database", path,
                       "--pairs", "SOL/USD", "--intervals", "60", "--one-minute-pairs", "")
    assert code == 1 and report["status"] == "partial"
    assert report["catalog_refresh"]["status"] == "failed" and report["counts"]["ok"] == 1
    assert "SECRET" not in json.dumps(report)


@pytest.mark.parametrize(("answer", "error", "stop"), [
    (error_wire("EAPI:Rate limit exceeded"), "public_catalog_rate_limited", "rate_limited"),
    (HttpRateLimited(), "public_catalog_rate_limited", "rate_limited"),
    (error_wire(f"EService: Throttled: {unix(NOW) + 3600 + 5}"), "public_catalog_throttled", "throttled"),
    (error_wire("EService:Throttled"), "public_catalog_throttled", "throttled"),
])
def test_a_rate_limited_pair_list_stops_the_run_before_any_candle_is_requested(
        tmp_path, monkeypatch, capsys, answer, error: str, stop: str) -> None:
    Scripted(monkeypatch)
    path = database_path(tmp_path)
    assert run(capsys, "collect-kraken-candles", "--market-database", path, "--catalog-only")[0] == 0
    scripted = Scripted(monkeypatch, catalog=[answer])
    scripted.clock.advance(3600)  # an older list is stored: before this fix the run went on with it
    code, report = run(capsys, "collect-kraken-candles", "--market-database", path,
                       "--pairs", "SOL/USD", "--intervals", "60", "--one-minute-pairs", "")
    assert code == 1 and report["status"] == "failed" and report["error"] == error
    assert report["stop_reason"] == stop and report["requests_total"] == 1
    assert report["catalog_refresh"]["error"] == error and report["catalog_refresh"]["status"] == "failed"
    assert scripted.exchange.calls == [] and len(scripted.catalog.calls) == 1
    assert report["paid_calls_enabled"] is False and "SECRET" not in json.dumps(report)


def test_a_rate_limited_catalog_only_run_reports_its_category_and_exits_1(tmp_path, monkeypatch, capsys) -> None:
    Scripted(monkeypatch, catalog=[error_wire("EAPI:Rate limit exceeded")])
    code, report = run(capsys, "collect-kraken-candles", "--market-database", database_path(tmp_path),
                       "--catalog-only")
    assert code == 1 and report["status"] == "failed" and report["catalog"]["error"] == "public_catalog_rate_limited"


def test_a_failed_catalog_with_nothing_stored_is_a_failed_run_with_no_candle_request(
        tmp_path, monkeypatch, capsys) -> None:
    scripted = Scripted(monkeypatch, catalog=[RuntimeError("SECRET down")])
    code, report = run(capsys, "collect-kraken-candles", "--market-database", database_path(tmp_path))
    assert code == 1 and report["status"] == "failed" and report["error"] == "no_catalog"
    assert scripted.exchange.calls == [] and "SECRET" not in json.dumps(report)


def test_an_interrupted_run_prints_a_report_and_exits_130_leaving_the_run_visible(
        tmp_path, monkeypatch, capsys) -> None:
    scripted = Scripted(monkeypatch)
    scripted.exchange.before = lambda _code, _interval: (_ for _ in ()).throw(KeyboardInterrupt())
    path = database_path(tmp_path)
    code, report = run(capsys, "collect-kraken-candles", "--market-database", path, "--smoke")
    assert code == 130 and report["status"] == "interrupted"
    database = MarketDataDatabase(path)
    try:
        assert [run_.status for run_ in CandleStore(database).runs()] == ["ok", "interrupted"]
    finally:
        database.close()


# --- the financial database is never touched ------------------------------------------------------------------------


def _financial_database(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    assert main(["init", "--database", str(path)]) == 0
    return path


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("command", [
    ["collect-kraken-candles", "--smoke"], ["collect-kraken-candles", "--catalog-only"], ["candle-coverage"],
])
def test_a_financial_database_is_refused_whatever_it_is_called(tmp_path, monkeypatch, capsys, command) -> None:
    scripted = Scripted(monkeypatch)
    capsys.readouterr()
    for name in ("trade_graph.sqlite", "ledger_copy.sqlite"):
        financial = _financial_database(tmp_path, name)
        capsys.readouterr()
        before, listing = _hash(financial), sorted(item.name for item in tmp_path.iterdir())
        code, report = run(capsys, *command[:1], "--market-database", str(financial), *command[1:])
        assert code == 1 and report["status"] == "refused" and report["error"] == "financial_database"
        assert _hash(financial) == before and sorted(item.name for item in tmp_path.iterdir()) == listing
        connection = sqlite3.connect(financial)
        try:
            names = {item[0] for item in connection.execute("SELECT name FROM sqlite_master")}
        finally:
            connection.close()
        assert "market_data_migrations" not in names and "market_candles" not in names
    assert scripted.catalog.calls == [] and scripted.exchange.calls == []


def test_the_default_financial_path_is_refused_even_when_it_does_not_exist(tmp_path, monkeypatch, capsys) -> None:
    scripted = Scripted(monkeypatch)
    monkeypatch.chdir(tmp_path)
    code, report = run(capsys, "collect-kraken-candles", "--market-database", "runtime/trade_graph.sqlite", "--smoke")
    assert code == 1 and report["status"] == "refused"
    assert not (tmp_path / "runtime").exists() and scripted.catalog.calls == []


def test_a_missing_file_with_the_financial_name_is_refused_without_creating_anything(
        tmp_path, monkeypatch, capsys) -> None:
    scripted = Scripted(monkeypatch)
    target = tmp_path / "elsewhere" / "trade_graph.sqlite"
    code, report = run(capsys, "collect-kraken-candles", "--market-database", str(target), "--smoke")
    assert code == 1 and report["status"] == "refused" and not target.parent.exists()
    assert scripted.catalog.calls == []


def test_a_dangling_link_to_the_default_financial_path_is_refused(tmp_path, monkeypatch, capsys) -> None:
    scripted = Scripted(monkeypatch)
    monkeypatch.chdir(tmp_path)
    link = tmp_path / "market_link.sqlite"
    link.symlink_to(tmp_path / "runtime" / "trade_graph.sqlite")
    code, report = run(capsys, "collect-kraken-candles", "--market-database", str(link), "--smoke")
    assert code == 1 and report["status"] == "refused" and not (tmp_path / "runtime").exists()
    assert scripted.catalog.calls == []


def test_a_symlink_to_the_financial_database_is_refused(tmp_path, monkeypatch, capsys) -> None:
    scripted = Scripted(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime").mkdir(mode=0o700)
    financial = _financial_database(tmp_path / "runtime", "trade_graph.sqlite")
    link = tmp_path / "market_link.sqlite"
    link.symlink_to(financial)
    capsys.readouterr()
    before = _hash(financial)
    code, report = run(capsys, "collect-kraken-candles", "--market-database", str(link), "--smoke")
    assert code == 1 and report["status"] == "refused" and _hash(financial) == before
    assert scripted.catalog.calls == []


def test_a_market_data_file_is_not_mistaken_for_a_financial_one(tmp_path, monkeypatch, capsys) -> None:
    Scripted(monkeypatch)
    path = database_path(tmp_path)
    assert run(capsys, "collect-kraken-candles", "--market-database", path, "--catalog-only")[0] == 0
    assert run(capsys, "collect-kraken-candles", "--market-database", path, "--catalog-only")[0] == 0


# --- candle-coverage ------------------------------------------------------------------------------------------------


def _candle(index: int, *, symbol: str = "BTC/USD", interval: int = 15, source: str = "synthetic",
            base: datetime = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)) -> Candle:
    opened = base + timedelta(minutes=interval * index)
    closed = opened + timedelta(minutes=interval)
    return Candle(
        symbol=symbol, source_pair="XBTUSD", interval_minutes=interval, open_time_utc=opened, close_time_utc=closed,
        available_at_utc=closed + timedelta(seconds=4), open=Decimal("100"), high=Decimal("101"), low=Decimal("99"),
        close=Decimal("100.5"), vwap=Decimal("100.2"), volume=Decimal("2"), trade_count=3, source=source)


def _seed(path: str, indexes: list[int], **options) -> None:
    database = MarketDataDatabase(path)
    try:
        store = CandleStore(database)
        run_id = store.begin_run(kind="public_collect", source="synthetic", evidence_label="synthetic_fixture",
                                 started_at=NOW, parameters={})
        store.save([_candle(index, **options) for index in indexes], run_id=run_id)
    finally:
        database.close()


def test_coverage_lists_each_series_with_counts_first_and_last_candle_and_missing_ranges(
        tmp_path, capsys) -> None:
    path = database_path(tmp_path)
    _seed(path, [0, 1, 2, 5, 6, 9], source="synthetic")
    _seed(path, [0, 1, 2], symbol="ETH/USD", interval=60, source="synthetic")
    code, report = run(capsys, "candle-coverage", "--market-database", path, "--source", "synthetic")
    assert code == 0 and report["status"] == "ok" and report["command"] == "candle-coverage"
    btc = next(item for item in report["series"] if item["symbol"] == "BTC/USD")
    assert btc["interval_minutes"] == 15 and btc["source"] == "synthetic" and btc["source_pair"] == "XBTUSD"
    assert btc["candles"] == 6 and btc["first_open_utc"] == "2026-10-06T12:00:00.000000Z"
    assert btc["last_close_utc"] == "2026-10-06T14:30:00.000000Z"
    assert (btc["expected_slots"], btc["present_slots"], btc["missing_slots"]) == (10, 6, 4)
    assert btc["longest_run_slots"] == 3
    assert btc["missing_ranges"] == [
        {"first_open_utc": "2026-10-06T12:45:00.000000Z", "last_open_utc": "2026-10-06T13:00:00.000000Z", "slots": 2},
        {"first_open_utc": "2026-10-06T13:45:00.000000Z", "last_open_utc": "2026-10-06T14:00:00.000000Z", "slots": 2},
    ]
    assert btc["missing_ranges_total"] == 2 and btc["missing_ranges_truncated"] is False
    eth = next(item for item in report["series"] if item["symbol"] == "ETH/USD")
    assert eth["missing_slots"] == 0 and eth["missing_ranges"] == []
    assert report["totals"] == {"series": 2, "candles": 9, "missing_slots": 4}
    assert report["source"] == "synthetic"


def test_coverage_defaults_to_the_collector_source_and_filters_by_symbol_and_interval(tmp_path, capsys) -> None:
    path = database_path(tmp_path)
    _seed(path, [0, 1], source="synthetic")
    _seed(path, [0, 1, 2], source="kraken_public_ohlc")
    _seed(path, [0, 1], symbol="ETH/USD", source="kraken_public_ohlc")
    _seed(path, [0, 1], interval=60, source="kraken_public_ohlc")
    _, everything = run(capsys, "candle-coverage", "--market-database", path)
    assert everything["source"] == "kraken_public_ohlc" and everything["totals"]["series"] == 3
    _, one = run(capsys, "candle-coverage", "--market-database", path, "--symbols", "BTC/USD", "--intervals", "15")
    assert [(item["symbol"], item["interval_minutes"], item["candles"]) for item in one["series"]] == [
        ("BTC/USD", 15, 3)]
    _, none = run(capsys, "candle-coverage", "--market-database", path, "--symbols", "SOL/USD")
    assert none["series"] == [] and none["totals"] == {"series": 0, "candles": 0, "missing_slots": 0}


def test_coverage_is_an_inventory_and_counts_candles_whatever_their_receipt_time(tmp_path, capsys) -> None:
    path = database_path(tmp_path)
    _seed(path, [0, 1, 2], base=datetime(2099, 1, 1, tzinfo=UTC))  # received long after any clock reading
    _, report = run(capsys, "candle-coverage", "--market-database", path, "--source", "synthetic")
    assert report["series"][0]["candles"] == 3 and report["series"][0]["present_slots"] == 3
    assert report["series"][0]["missing_slots"] == 0


def test_coverage_cuts_a_long_range_list_but_says_how_many_there_are(tmp_path, capsys) -> None:
    path = database_path(tmp_path)
    _seed(path, list(range(0, 80, 2)))
    _, report = run(capsys, "candle-coverage", "--market-database", path, "--source", "synthetic", "--max-ranges", "5")
    item = report["series"][0]
    assert len(item["missing_ranges"]) == 5 and item["missing_ranges_total"] == 39
    assert item["missing_ranges_truncated"] is True and item["missing_slots"] == 39


@pytest.mark.parametrize("argv", [["--max-ranges", "0"], ["--max-ranges", "1001"], ["--source", "other"],
                                  ["--intervals", "7"], ["--symbols", "btc"]])
def test_bad_coverage_options_exit_2(tmp_path, capsys, argv) -> None:
    with pytest.raises(SystemExit) as refused:
        main(["candle-coverage", "--market-database", database_path(tmp_path), *argv])
    assert refused.value.code == 2


def test_coverage_never_creates_or_changes_anything(tmp_path, capsys) -> None:
    missing = Path(database_path(tmp_path))
    code, report = run(capsys, "candle-coverage", "--market-database", str(missing))
    assert code == 1 and report["status"] == "failed" and report["error"] == "market_database_not_found"
    assert not missing.exists() and not missing.parent.exists()
    _seed(str(missing), [0, 1, 3])
    connection = sqlite3.connect(missing)
    try:
        counts = [connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("market_candles", "candle_runs", "candle_run_results", "candle_series")]
    finally:
        connection.close()
    run(capsys, "candle-coverage", "--market-database", str(missing), "--source", "synthetic")
    connection = sqlite3.connect(missing)
    try:
        assert counts == [connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                          for table in ("market_candles", "candle_runs", "candle_run_results", "candle_series")]
    finally:
        connection.close()
