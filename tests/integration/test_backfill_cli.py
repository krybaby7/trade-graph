"""backfill-kraken-trades and reconcile-candles: one JSON report each, never the financial database, no network."""

from __future__ import annotations

import json
import signal
import socket
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from tests.trades_support import FakeSleeper, SyntheticTrade, TradeScript, steady_trades

from trade_graph import cli, market_data_cli
from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.application.candle_store import CandleStore
from trade_graph.contracts.candles import Candle, from_unix
from trade_graph.domain.clock import FrozenClock
from trade_graph.market_data_cli import backfill as backfill_cli
from trade_graph.market_data_cli import reconcile as reconcile_cli

NOW = datetime(2026, 10, 13, 20, 0, tzinfo=UTC)
T0 = 1_791_892_800  # 12:00Z, where the archive ends
ARCHIVE, COLLECTOR = "kraken_ohlcvt_archive", "kraken_public_ohlc"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network access attempted by a candle-store command")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture
def clock(monkeypatch) -> FrozenClock:
    frozen = FrozenClock(NOW)
    monkeypatch.setattr(backfill_cli, "SystemClock", lambda: frozen)
    monkeypatch.setattr(reconcile_cli, "SystemClock", lambda: frozen)
    monkeypatch.setattr(backfill_cli, "make_sleeper", lambda: FakeSleeper(frozen))
    return frozen


def exchange(monkeypatch, clock: FrozenClock, trades: list[SyntheticTrade], hook=None) -> TradeScript:
    script = TradeScript(trades, clock=clock, delay_seconds=1, hook=hook)
    monkeypatch.setattr(backfill_cli, "make_transport", lambda: script)
    return script


def database_path(tmp_path: Path) -> Path:
    return tmp_path / "private" / "market_data.sqlite"


def candle(source: str, open_unix: int, *, interval: int = 15, close: str = "100.5") -> Candle:
    return Candle(
        symbol="BTC/USD", source_pair="XBTUSD", interval_minutes=interval, open_time_utc=from_unix(open_unix),
        close_time_utc=from_unix(open_unix + interval * 60), available_at_utc=NOW, open=Decimal("100"),
        high=Decimal("101"), low=Decimal("99"), close=Decimal(close), vwap=None, volume=Decimal("5"),
        trade_count=3, source=source)


def seed(path: Path, *, collector_start: int | None = T0 + 3600, archive_end: int | None = T0) -> None:
    database = MarketDataDatabase(path)
    store = CandleStore(database)
    if archive_end is not None:
        run = store.begin_run(kind="archive_import", source=ARCHIVE, evidence_label="local_archive_file",
                              started_at=NOW, parameters={})
        store.save([candle(ARCHIVE, archive_end - 1800), candle(ARCHIVE, archive_end - 900)], run_id=run)
    if collector_start is not None:
        run = store.begin_run(kind="public_collect", source=COLLECTOR, evidence_label="synthetic_fixture",
                              started_at=NOW, parameters={})
        store.save([candle(COLLECTOR, collector_start), candle(COLLECTOR, collector_start + 900)], run_id=run)
    database.close()


def run(capsys, *arguments: str) -> tuple[int, dict]:
    code = cli.main([*arguments])
    return code, json.loads(capsys.readouterr().out)


def trades() -> list[SyntheticTrade]:
    return steady_trades(1000, T0 - 600, 100, every_seconds=60)


# --- backfill-kraken-trades ----------------------------------------------------------------------------------------


def test_both_commands_are_registered_by_discovery() -> None:
    names = [module.__name__ for module in market_data_cli._modules()]
    assert "trade_graph.market_data_cli.backfill" in names and "trade_graph.market_data_cli.reconcile" in names
    args = cli.build_parser().parse_args(["backfill-kraken-trades", "--pairs", "BTC/USD"])
    assert (args.intervals, args.max_requests, args.market_database, args.smoke) == (
        "15", None, "runtime/market_data.sqlite", False)  # None: 100 normally, 3 under --smoke
    args = cli.build_parser().parse_args(["reconcile-candles", "--pair", "BTC/USD"])
    assert (args.interval, args.count, args.mode, args.worst) == (15, 2000, "replay", 10)


def test_backfill_prints_one_json_report_and_stores_the_candles(tmp_path, capsys, monkeypatch, clock) -> None:
    path = database_path(tmp_path)
    seed(path)
    script = exchange(monkeypatch, clock, trades())
    code, report = run(capsys, "backfill-kraken-trades", "--pairs", "BTC/USD", "--market-database", str(path))
    assert code == 0 and report["status"] == "ok" and report["kind"] == "trades_backfill"
    assert report["evidence_label"] == "live_public" and report["requests"]["max"] == 100
    assert report["pairs"]["BTC/USD"]["intervals"]["15"]["status"] == "complete"
    assert script.calls and "pair=XBTUSD" in script.calls[0]
    database = MarketDataDatabase(path)
    store = CandleStore(database)
    found = {s.source: s.candle_count for s in store.series() if s.symbol == "BTC/USD"}
    assert found["kraken_public_trades"] == 4 and report["run_id"] == store.runs()[-1].run_id
    database.close()


def test_pairs_may_be_named_in_the_archive_style(tmp_path, capsys, monkeypatch, clock) -> None:
    path = database_path(tmp_path)
    seed(path)
    exchange(monkeypatch, clock, trades())
    code, report = run(capsys, "backfill-kraken-trades", "--pairs", "XBTUSD", "--market-database", str(path),
                       "--intervals", "15")
    assert code == 0 and list(report["pairs"]) == ["BTC/USD"]


def test_smoke_means_one_pair_three_requests_and_its_own_label(tmp_path, capsys, monkeypatch, clock) -> None:
    path = database_path(tmp_path)
    seed(path)
    script = exchange(monkeypatch, clock, steady_trades(1000, T0 - 600, 400, every_seconds=60))
    code, report = run(capsys, "backfill-kraken-trades", "--pairs", "BTC/USD", "--smoke",
                       "--market-database", str(path))
    assert code == 0 and report["evidence_label"] == "live_public_smoke" and report["requests"]["max"] == 3
    database = MarketDataDatabase(path)
    assert CandleStore(database).runs()[-1].evidence_label == "live_public_smoke"
    database.close()
    assert len(script.calls) <= 3


def test_smoke_refuses_several_pairs_and_a_larger_budget(tmp_path, monkeypatch, clock) -> None:
    path = database_path(tmp_path)
    exchange(monkeypatch, clock, trades())
    for extra in (["--pairs", "BTC/USD,ETH/USD"], ["--pairs", "BTC/USD", "--max-requests", "4"], []):
        with pytest.raises(SystemExit) as stopped:
            cli.main(["backfill-kraken-trades", "--smoke", "--market-database", str(path), *extra])
        assert stopped.value.code == 2
    assert not path.exists()


@pytest.mark.parametrize("arguments", [
    ["--pairs", "BTC/EUR"], ["--pairs", "USDT/USD"], ["--pairs", ""], ["--pairs", "BTC/USD", "--intervals", "7"],
    ["--pairs", "BTC/USD", "--intervals", "240"], ["--pairs", "BTC/USD", "--intervals", "15,15"],
    ["--pairs", "BTC/USD", "--max-requests", "0"], ["--pairs", "BTC/USD", "--max-requests", "100001"],
    ["--pairs", "BTC/USD", "--max-requests", "many"], ["--pairs", "BTC/USD", "--start", "2026-10-13T12:00:00"],
    ["--pairs", "BTC/USD", "--end", "yesterday"], [],
    ["--pairs", "BTC/USD", "--start", "1969-12-31T23:00:00Z"], ["--pairs", "BTC/USD", "--end", "2100-01-01T00:00:00Z"],
    ["--pairs", "BTC/USD", "--smoke", "--max-requests", "0"], ["--pairs", "BTC/USD", "--smoke", "--max-requests", "-5"],
])
def test_bad_arguments_are_usage_errors_and_touch_nothing(tmp_path, arguments) -> None:
    path = database_path(tmp_path)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["backfill-kraken-trades", "--market-database", str(path), *arguments])
    assert stopped.value.code == 2 and not path.exists()


def test_the_financial_database_is_refused(tmp_path, monkeypatch, clock) -> None:
    exchange(monkeypatch, clock, trades())
    financial = tmp_path / "trade_graph.sqlite"
    with pytest.raises(SystemExit) as stopped:
        cli.main(["backfill-kraken-trades", "--pairs", "BTC/USD", "--market-database", str(financial)])
    assert stopped.value.code == 2 and not financial.exists()
    other = tmp_path / "other.sqlite"
    import sqlite3

    connection = sqlite3.connect(other)
    connection.execute("CREATE TABLE ledger (id INTEGER)")
    connection.commit()
    connection.close()
    with pytest.raises(SystemExit) as stopped:
        cli.main(["backfill-kraken-trades", "--pairs", "BTC/USD", "--market-database", str(other)])
    assert stopped.value.code == 2


def test_a_store_with_no_gap_to_fill_exits_one_and_says_why(tmp_path, capsys, monkeypatch, clock) -> None:
    path = database_path(tmp_path)
    seed(path, collector_start=None)
    script = exchange(monkeypatch, clock, trades())
    code, report = run(capsys, "backfill-kraken-trades", "--pairs", "BTC/USD", "--market-database", str(path))
    assert code == 1 and report["status"] == "failed" and script.calls == []
    assert "--end" in report["pairs"]["BTC/USD"]["intervals"]["15"]["reason"]


def test_a_smoke_budget_smaller_than_one_candle_warns_on_the_error_stream(tmp_path, capsys, monkeypatch,
                                                                         clock) -> None:
    path = database_path(tmp_path)
    seed(path)
    dense = [SyntheticTrade(1000 + i, "100.00", "1", f"{Decimal(T0) + 10 + Decimal(i) / 5:.7f}") for i in range(3500)]
    exchange(monkeypatch, clock, dense)  # 1000 trades per page, 0.2 s apart: three pages cover 600 s of a 900 s candle
    code = cli.main(["backfill-kraken-trades", "--pairs", "BTC/USD", "--smoke", "--market-database", str(path)])
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert code == 0 and report["pairs"]["BTC/USD"]["stop_reason"] == "max_requests"
    assert report["pairs"]["BTC/USD"]["intervals"]["15"]["decided_this_run"] == 0
    assert "BTC/USD" in captured.err and "before one whole candle" in captured.err and "--max-requests" in captured.err


def test_a_run_in_which_every_request_failed_exits_one_so_a_scheduler_can_tell(tmp_path, capsys, monkeypatch,
                                                                              clock) -> None:
    path = database_path(tmp_path)
    seed(path)
    script = exchange(monkeypatch, clock, trades(), hook=lambda call, url: ConnectionError("network is down"))
    code, report = run(capsys, "backfill-kraken-trades", "--pairs", "BTC/USD", "--market-database", str(path))
    assert code == 1 and report["status"] == "failed" and len(script.calls) == 3
    assert report["pairs"]["BTC/USD"]["stop_reason"] == "transport_error"
    database = MarketDataDatabase(path)
    assert CandleStore(database).runs()[-1].status == "failed"
    database.close()


def test_a_chosen_start_and_end_make_a_fresh_store_usable(tmp_path, capsys, monkeypatch, clock) -> None:
    path = database_path(tmp_path)
    script = exchange(monkeypatch, clock, trades())
    code, report = run(capsys, "backfill-kraken-trades", "--pairs", "BTC/USD", "--market-database", str(path),
                       "--start", "2026-10-13T12:00:00Z", "--end", "2026-10-13T13:00:00+00:00")
    assert code == 0 and script.calls
    interval = report["pairs"]["BTC/USD"]["intervals"]["15"]
    assert interval["range_source"] == {"start": "argument", "end": "argument"} and interval["target_slots"] == 4


def test_from_start_ignores_a_stored_cursor(tmp_path, capsys, monkeypatch, clock) -> None:
    path = database_path(tmp_path)
    seed(path)
    exchange(monkeypatch, clock, trades())
    run(capsys, "backfill-kraken-trades", "--pairs", "BTC/USD", "--market-database", str(path))
    script = exchange(monkeypatch, clock, trades())
    code, again = run(capsys, "backfill-kraken-trades", "--pairs", "BTC/USD", "--market-database", str(path))
    assert code == 0 and script.calls == []
    code, forced = run(capsys, "backfill-kraken-trades", "--pairs", "BTC/USD", "--market-database", str(path),
                       "--from-start")
    assert code == 0 and script.calls and forced["pairs"]["BTC/USD"]["intervals"]["15"]["candles"]["duplicates"] == 4


def test_ctrl_c_still_records_the_run_and_exits_130(tmp_path, capsys, monkeypatch, clock) -> None:
    path = database_path(tmp_path)
    seed(path)
    exchange(monkeypatch, clock, steady_trades(1000, T0 - 600, 400, every_seconds=60),
             hook=lambda call, url: KeyboardInterrupt() if call == 1 else None)
    code, report = run(capsys, "backfill-kraken-trades", "--pairs", "BTC/USD", "--market-database", str(path))
    assert code == 130 and report["status"] == "failed" and report["reason"] == "interrupted"
    database = MarketDataDatabase(path)
    assert CandleStore(database).runs()[-1].report["reason"] == "interrupted"
    database.close()


def test_a_termination_request_is_recorded_like_ctrl_c_and_exits_143(tmp_path, capsys, monkeypatch, clock) -> None:
    path = database_path(tmp_path)
    seed(path)
    before = signal.getsignal(signal.SIGTERM)
    seen = []

    def hook(call: int, url: str) -> None:
        if call == 3:  # the operating system delivers SIGTERM to the handler the command installed
            handler = signal.getsignal(signal.SIGTERM)
            seen.append(handler)
            if callable(handler):
                handler(signal.SIGTERM, None)

    exchange(monkeypatch, clock, steady_trades(1000, T0 - 600, 5000, every_seconds=1), hook=hook)  # five pages
    code, report = run(capsys, "backfill-kraken-trades", "--pairs", "BTC/USD", "--market-database", str(path),
                       "--max-requests", "50")
    assert len(seen) == 1 and callable(seen[0]), "the command installs a SIGTERM handler for the length of the run"
    assert code == 143 and report["status"] == "failed" and report["reason"] == "interrupted"
    assert signal.getsignal(signal.SIGTERM) is before  # and puts the old one back
    database = MarketDataDatabase(path)
    recorded = CandleStore(database).runs()[-1]
    database.close()
    assert recorded.kind == "trades_backfill" and recorded.report["reason"] == "interrupted"
    assert recorded.report["pairs"]["BTC/USD"]["stop_reason"] == "interrupted"
    assert recorded.report["pairs"]["BTC/USD"]["intervals"]["15"]["next_open_unix"] > T0  # the cursor was saved


def test_the_only_module_that_builds_a_real_http_transport_is_the_backfill_command() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "trade_graph"
    users = sorted(str(path.relative_to(root)) for path in root.rglob("*.py")
                   if "HttpxTextTransport" in path.read_text(encoding="utf-8"))
    assert "market_data_cli/backfill.py" in users
    for forbidden in ("application/backfill_trades.py", "application/backfill_model.py",
                      "application/backfill_report.py", "application/reconcile_candles.py",
                      "application/trade_aggregation.py", "adapters/market/kraken_trades.py",
                      "adapters/market/trades_rate_limit.py", "market_data_cli/reconcile.py"):
        assert forbidden not in users


# --- reconcile-candles ---------------------------------------------------------------------------------------------


def seed_overlap(path: Path) -> None:
    database = MarketDataDatabase(path)
    store = CandleStore(database)
    run_a = store.begin_run(kind="archive_import", source=ARCHIVE, evidence_label="local_archive_file",
                            started_at=NOW, parameters={})
    run_c = store.begin_run(kind="public_collect", source=COLLECTOR, evidence_label="synthetic_fixture",
                            started_at=NOW, parameters={})
    store.save([candle(ARCHIVE, T0 + n * 900) for n in range(4)], run_id=run_a)
    store.save([candle(COLLECTOR, T0 + n * 900, close="100.6" if n == 1 else "100.5") for n in range(2, 6)] +
               [candle(COLLECTOR, T0 + 900, close="100.6")], run_id=run_c)
    database.close()


def test_reconcile_prints_one_json_report_and_changes_nothing(tmp_path, capsys, clock) -> None:
    path = database_path(tmp_path)
    seed_overlap(path)
    code, report = run(capsys, "reconcile-candles", "--pair", "BTC/USD", "--market-database", str(path),
                       "--sources", f"{COLLECTOR},{ARCHIVE}", "--end", "2026-10-13T13:30:00Z", "--count", "6")
    assert code == 0 and report["symbol"] == "BTC/USD" and report["sources"] == [COLLECTOR, ARCHIVE]
    assert report["counts"]["slots"] == 6 and report["counts"]["differing"] == 1
    assert report["counts"]["identical"] == 2 and report["counts"]["single_source"] == 3
    assert report["worst_slots"][0]["open_time_utc"] == "2026-10-13T12:15:00.000000Z"
    after = MarketDataDatabase(path)
    reopened = CandleStore(after)
    assert len(reopened.runs()) == 2 and sorted(s.candle_count for s in reopened.series()) == [4, 5]
    after.close()


def test_reconcile_defaults_the_end_to_the_newest_candle_and_all_three_sources(tmp_path, capsys, clock) -> None:
    path = database_path(tmp_path)
    seed_overlap(path)
    code, report = run(capsys, "reconcile-candles", "--pair", "BTC/USD", "--market-database", str(path),
                       "--count", "6")
    assert code == 0 and report["window"]["first_open_utc"] == "2026-10-13T12:00:00.000000Z"
    assert report["sources"] == [COLLECTOR, ARCHIVE, "kraken_public_trades"]
    assert report["series_present"]["kraken_public_trades"] is False


def test_reconcile_point_in_time_uses_the_clock_unless_as_of_is_given(tmp_path, capsys, clock) -> None:
    path = database_path(tmp_path)
    seed_overlap(path)
    code, report = run(capsys, "reconcile-candles", "--pair", "BTC/USD", "--market-database", str(path),
                       "--mode", "point_in_time", "--count", "6")
    assert code == 0 and report["availability_basis"] == "receipt_time"
    assert report["as_of_utc"] == "2026-10-13T20:00:00.000000Z"


def test_reconcile_refuses_a_missing_file_without_creating_it_and_the_financial_database(tmp_path) -> None:
    missing = database_path(tmp_path)
    for target in (missing, tmp_path / "trade_graph.sqlite"):
        with pytest.raises(SystemExit) as stopped:
            cli.main(["reconcile-candles", "--pair", "BTC/USD", "--market-database", str(target)])
        assert stopped.value.code == 2 and not target.exists()
    assert not missing.parent.exists()


@pytest.mark.parametrize("arguments", [
    ["--pair", "BTC/EUR"], ["--pair", "BTC/USD", "--interval", "7"],
    ["--pair", "BTC/USD", "--sources", "kraken_public_ohlc"],
    ["--pair", "BTC/USD", "--sources", "a,b"], ["--pair", "BTC/USD", "--count", "0"],
    ["--pair", "BTC/USD", "--end", "2026-10-13T14:07:00Z"], ["--pair", "BTC/USD", "--worst", "0"],
    ["--pair", "BTC/USD", "--as-of", "soon"], [],
])
def test_reconcile_bad_arguments_are_usage_errors(tmp_path, arguments) -> None:
    path = database_path(tmp_path)
    seed_overlap(path)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["reconcile-candles", "--market-database", str(path), *arguments])
    assert stopped.value.code == 2


def test_reconcile_with_no_candles_at_all_says_so(tmp_path) -> None:
    path = database_path(tmp_path)
    database = MarketDataDatabase(path)
    database.close()
    with pytest.raises(SystemExit) as stopped:
        cli.main(["reconcile-candles", "--pair", "BTC/USD", "--market-database", str(path)])
    assert stopped.value.code == 2
