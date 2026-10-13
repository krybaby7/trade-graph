"""The import-kraken-archive command: one JSON report, local files only, never the financial database."""

from __future__ import annotations

import json
import os
import shlex
import socket
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.archive_support import FIRST_OPEN, csv_text, synthetic_rows, write_zip

from trade_graph import cli, market_data_cli
from trade_graph.adapters.market.kraken_archive import ARCHIVE_SOURCE
from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.application.candle_store import CandleStore
from trade_graph.domain.clock import FrozenClock
from trade_graph.market_data_cli import archive as archive_cli

IMPORT_AT = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network access attempted by the archive command")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(archive_cli, "SystemClock", lambda: FrozenClock(IMPORT_AT))


def _members() -> dict[str, str]:
    rows = lambda interval, seed=1: csv_text(synthetic_rows(FIRST_OPEN, 8, interval, seed=seed))  # noqa: E731
    return {"MANIFEST.json": "{}", "XBTUSD_15.csv": rows(15), "XBTUSD_60.csv": rows(60), "XBTUSD_1.csv": rows(1),
            "ETHUSD_15.csv": rows(15, 2), "ADAEUR_15.csv": rows(15, 3)}


@pytest.fixture
def archive(tmp_path) -> Path:
    return write_zip(tmp_path / "Kraken_OHLCVT_2026Q3.zip", _members())


def _database(tmp_path) -> Path:
    return tmp_path / "private" / "market_data.sqlite"


def _run(capsys, *arguments: str) -> tuple[int, dict]:
    code = cli.main(["import-kraken-archive", *arguments])
    out = capsys.readouterr().out
    return code, json.loads(out)


def test_the_command_is_registered_by_discovery() -> None:
    assert "trade_graph.market_data_cli.archive" in [module.__name__ for module in market_data_cli._modules()]
    args = cli.build_parser().parse_args(["import-kraken-archive", "--archive", "x.zip"])
    assert args.command == "import-kraken-archive" and args.intervals == "15,60" and args.max_uncompressed_gb == "16"
    assert args.market_database == "runtime/market_data.sqlite"


def test_it_prints_one_json_report_and_imports_the_default_scope(tmp_path, capsys, archive) -> None:
    database = _database(tmp_path)
    code, report = _run(capsys, "--archive", str(archive), "--market-database", str(database))
    assert code == 0 and report["status"] == "ok" and report["kind"] == "archive_import"
    assert report["archive"]["quarter"] == "2026Q3" and report["archive"]["file_name"] == archive.name
    assert report["scope"] == {"quote": "USD", "intervals": [15, 60], "pairs": None}
    assert report["skipped"]["non_usd_files"] == 1 and report["skipped"]["interval_not_selected_files"] == {"1": 1}
    assert "survivorship_notice" in report and "optimistic" in report["survivorship_notice"]
    assert report["imported_at_utc"] == "2026-10-14T09:00:00.000000Z"
    opened = MarketDataDatabase(database)
    stored = CandleStore(opened)
    assert {(s.symbol, s.interval_minutes, s.source) for s in stored.series()} == {
        ("BTC/USD", 15, ARCHIVE_SOURCE), ("BTC/USD", 60, ARCHIVE_SOURCE), ("ETH/USD", 15, ARCHIVE_SOURCE)}
    assert stored.runs()[0].evidence_label == "local_archive_file" and stored.runs()[0].run_id == report["run_id"]
    opened.close()


def test_intervals_pairs_and_quarter_options_reach_the_importer(tmp_path, capsys, archive) -> None:
    code, report = _run(capsys, "--archive", str(archive), "--market-database", str(_database(tmp_path)),
                        "--intervals", "1,15", "--pairs", "XBTUSD,ETH/USD", "--quarter", "2026Q2")
    assert code == 0
    assert report["scope"] == {"quote": "USD", "intervals": [1, 15], "pairs": ["BTC/USD", "ETH/USD"]}
    assert report["archive"]["quarter"] == "2026Q2" and report["archive"]["quarter_source"] == "argument"
    assert {(s["symbol"], s["interval_minutes"]) for s in report["series"]} == {
        ("BTC/USD", 1), ("BTC/USD", 15), ("ETH/USD", 15)}


def test_the_collector_catalog_supplies_the_expected_pairs(tmp_path, capsys, archive) -> None:
    database = _database(tmp_path)
    opened = MarketDataDatabase(database)
    store = CandleStore(opened)
    run_id = store.begin_run(kind="catalog", source="kraken_public_ohlc", evidence_label="synthetic_fixture",
                             started_at=IMPORT_AT - timedelta(hours=1), parameters={})
    store.save_catalog(
        [{"symbol": "BTC/USD", "source_pair": "XBTUSD", "status": "online"},
         {"symbol": "SOL/USD", "source_pair": "SOLUSD", "status": "online"},
         {"symbol": "ETH/EUR", "source_pair": "ETHEUR", "status": "online"}, {"odd": True}],
        observed_at=IMPORT_AT - timedelta(minutes=30), run_id=run_id, rules_version="r1")
    opened.close()
    code, report = _run(capsys, "--archive", str(archive), "--market-database", str(database))
    assert code == 0
    assert report["expected_pairs"]["supplied"] is True and report["expected_pairs"]["missing"] == ["SOL/USD"]


def test_an_explicit_expected_list_wins_over_the_catalog(tmp_path, capsys, archive) -> None:
    code, report = _run(capsys, "--archive", str(archive), "--market-database", str(_database(tmp_path)),
                        "--expected-pairs", "BTC/USD,LUNA/USD,XDGUSD")
    assert code == 0 and report["expected_pairs"]["missing"] == ["DOGE/USD", "LUNA/USD"]


def test_without_a_catalog_or_list_missing_pairs_are_not_named(tmp_path, capsys, archive) -> None:
    code, report = _run(capsys, "--archive", str(archive), "--market-database", str(_database(tmp_path)))
    assert report["expected_pairs"]["supplied"] is False and report["expected_pairs"]["missing"] is None


def test_a_refused_file_prints_the_failed_report_and_exits_1(tmp_path, capsys, archive) -> None:
    code, report = _run(capsys, "--archive", str(archive), "--market-database", str(_database(tmp_path)),
                        "--max-uncompressed-gb", "0.0000001")
    assert code == 1 and report["status"] == "failed"
    assert report["reason"] in {"member_size_limit", "total_size_limit"} and report["run_id"] == 1


def test_a_missing_file_exits_1_with_a_reason_and_no_run(tmp_path, capsys) -> None:
    code, report = _run(capsys, "--archive", str(tmp_path / "absent.zip"),
                        "--market-database", str(_database(tmp_path)))
    assert code == 1 and report["status"] == "failed" and report["reason"] == "not_a_file"
    assert report["run_id"] is None


@pytest.mark.parametrize("location", [
    "https://assets.kraken.com/marketing/institutions/Kraken_OHLCVT_2026Q3.zip", "http://example.com/a.zip",
    "ftp://example.com/a.zip", "file://localhost/tmp/a.zip",
])
def test_a_url_is_refused_because_the_importer_never_downloads(tmp_path, capsys, location) -> None:
    with pytest.raises(SystemExit) as stopped:
        cli.main(["import-kraken-archive", "--archive", location, "--market-database", str(_database(tmp_path))])
    assert stopped.value.code == 2
    assert "never downloads" in capsys.readouterr().err
    assert not _database(tmp_path).exists()


def test_the_default_financial_database_path_is_refused(tmp_path, monkeypatch, capsys, archive) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["import-kraken-archive", "--archive", str(archive), "--market-database",
                  "runtime/trade_graph.sqlite"])
    assert stopped.value.code == 2 and "financial database" in capsys.readouterr().err
    assert not (tmp_path / "runtime").exists()


def test_any_existing_database_that_is_not_a_candle_file_is_refused(tmp_path, capsys, archive) -> None:
    financial = tmp_path / "money.sqlite"
    connection = sqlite3.connect(financial)
    connection.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT)")
    connection.commit()
    connection.close()
    before = financial.read_bytes()
    with pytest.raises(SystemExit) as stopped:
        cli.main(["import-kraken-archive", "--archive", str(archive), "--market-database", str(financial)])
    assert stopped.value.code == 2 and "financial database" in capsys.readouterr().err
    assert financial.read_bytes() == before

    garbage = tmp_path / "garbage.sqlite"
    garbage.write_bytes(b"not a database at all, just text")
    with pytest.raises(SystemExit) as junk:
        cli.main(["import-kraken-archive", "--archive", str(archive), "--market-database", str(garbage)])
    assert junk.value.code == 2


def test_an_existing_candle_file_is_accepted_again(tmp_path, capsys, archive) -> None:
    database = _database(tmp_path)
    assert _run(capsys, "--archive", str(archive), "--market-database", str(database))[0] == 0
    code, second = _run(capsys, "--archive", str(archive), "--market-database", str(database))
    assert code == 0 and second["totals"]["inserted"] == 0 and second["totals"]["duplicates"] == 24
    assert second["same_file_seen_in_runs"] == [1]


def test_a_candle_folder_that_is_not_private_is_a_usage_error_that_names_the_fix(tmp_path, capsys, archive) -> None:
    """The default runtime/ folder often exists with open permissions; the owner must not meet a traceback."""
    folder = tmp_path / "open folder"
    folder.mkdir()
    folder.chmod(0o755)
    database = folder / "market_data.sqlite"
    with pytest.raises(SystemExit) as stopped:
        cli.main(["import-kraken-archive", "--archive", str(archive), "--market-database", str(database)])
    message = capsys.readouterr().err
    assert stopped.value.code == 2
    assert "must be private" in message and f"chmod 700 {shlex.quote(str(folder))}" in message
    assert not database.exists() and folder.stat().st_mode & 0o777 == 0o755


def test_a_candle_file_that_is_not_private_is_a_usage_error_that_names_the_fix(tmp_path, capsys, archive) -> None:
    database = _database(tmp_path)
    MarketDataDatabase(database).close()
    database.chmod(0o644)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["import-kraken-archive", "--archive", str(archive), "--market-database", str(database)])
    message = capsys.readouterr().err
    assert stopped.value.code == 2
    assert "must be private" in message and f"chmod 600 {shlex.quote(str(database))}" in message
    assert database.stat().st_mode & 0o777 == 0o644  # the command only explains; it never changes permissions


def test_a_symlinked_candle_file_is_a_usage_error_without_a_permission_hint(tmp_path, capsys, archive) -> None:
    real = _database(tmp_path)
    MarketDataDatabase(real).close()
    link = real.parent / "linked.sqlite"
    link.symlink_to(real)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["import-kraken-archive", "--archive", str(archive), "--market-database", str(link)])
    message = capsys.readouterr().err
    assert stopped.value.code == 2 and "symlinks" in message and "chmod" not in message


@pytest.mark.parametrize("arguments", [
    ["--intervals", "7"], ["--intervals", "abc"], ["--intervals", ""], ["--pairs", "BTC/EUR"], ["--quarter", "Q3"],
    ["--max-uncompressed-gb", "0"], ["--max-uncompressed-gb", "-1"], ["--max-uncompressed-gb", "lots"],
    ["--max-uncompressed-gb", "100000"], ["--expected-pairs", "ETHXBT"],
    ["--max-uncompressed-gb", "NaN"], ["--max-uncompressed-gb", "-NaN"], ["--max-uncompressed-gb", "sNaN"],
    ["--max-uncompressed-gb", "Infinity"], ["--max-uncompressed-gb", "-Infinity"],
])
def test_bad_options_are_usage_errors_and_nothing_is_created(tmp_path, archive, arguments) -> None:
    with pytest.raises(SystemExit) as stopped:
        cli.main(["import-kraken-archive", "--archive", str(archive), "--market-database",
                  str(_database(tmp_path)), *arguments])
    assert stopped.value.code == 2
    assert not _database(tmp_path).exists()
    assert sorted(os.listdir(tmp_path)) == [archive.name]
