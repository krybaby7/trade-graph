"""The coin scanner commands (T31): ``scan-coins`` records one scan, ``coin-shortlist`` only reads.

Synthetic candles and a synthetic ticker fixture; the network is blocked in every test.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

import pytest
from tests.coin_scanner_support import AS_OF, hourly, new_store, record, save_candles, save_catalog, tight_quote
from tests.integration.test_coin_scanner import ELIGIBLE
from tests.network_guard import block_network

from trade_graph.cli import main
from trade_graph.domain.clock import FrozenClock

BASES = ("SOL", "BTC", "ETH", "ADA", "DOT", "AVAX", "XRP", "DOGE", "LINK", "WILDX", "ATOM")


@pytest.fixture
def installation(tmp_path, monkeypatch, capsys):
    from trade_graph.market_data_cli import coin_scanner

    block_network(monkeypatch)
    monkeypatch.setattr(coin_scanner, "build_clock", lambda: FrozenClock(AS_OF))
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    database = private / "trade_graph.sqlite"
    assert main(["init", "--database", str(database)]) == 0
    store = new_store(private)
    save_catalog(store, [record(base) for base in (*BASES, "USDT")], observed_at=AS_OF - timedelta(hours=1))
    for base in BASES:
        save_candles(store, hourly(f"{base}/USD", ELIGIBLE[base][0], source="kraken_public_ohlc"))
    store.database.close()
    response = {"error": [], "result": {
        f"{base}USD": {"a": [tight_quote(ELIGIBLE[base][0].level, ELIGIBLE[base][1])[1], "1", "1.000"],
                       "b": [tight_quote(ELIGIBLE[base][0].level, ELIGIBLE[base][1])[0], "1", "1.000"],
                       "c": [ELIGIBLE[base][0].level, "1.0"]} for base in BASES}}
    fixture = tmp_path / "ticker.json"
    fixture.write_text(json.dumps({"evidence_label": "synthetic_fixture", "received_at_utc":
                                   "2026-10-16T12:04:40.000000Z", "response": response}), encoding="utf-8")
    capsys.readouterr()
    return {"database": database, "market": private / "market_data.sqlite", "ticker": fixture}


def _args(installation, *extra) -> list[str]:
    return ["--database", str(installation["database"]), "--market-database", str(installation["market"]), *extra]


def _json(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_a_scan_with_a_fixture_ticker_records_the_shortlist_and_says_what_it_used(installation, capsys) -> None:
    assert main(["scan-coins", *_args(installation, "--fixture-ticker", str(installation["ticker"]))]) == 0
    summary = _json(capsys)
    assert summary["status"] == "ok" and summary["mode"] == "live" and summary["optimistic"] is False
    assert summary["evidence"] == "synthetic_fixture" and summary["public_requests"] == 0
    assert summary["model_calls"] == 0 and summary["paid_calls"] is False
    assert [item["symbol"] for item in summary["shortlist"]][0] == "SOL/USD" and len(summary["shortlist"]) == 10
    assert summary["counts"]["excluded"] == 1 and summary["excluded"] == {"stablecoin": ["USDT/USD"]}
    connection = sqlite3.connect(installation["database"])
    assert connection.execute("SELECT scan_id, status FROM coin_scans").fetchall() == [(summary["scan_id"], "ok")]
    connection.close()


def test_the_shortlist_report_reads_the_latest_live_scan_in_plain_words(installation, capsys) -> None:
    assert main(["coin-shortlist", "--database", str(installation["database"])]) == 0
    assert "No live coin scan has been recorded yet" in capsys.readouterr().out
    main(["scan-coins", *_args(installation, "--fixture-ticker", str(installation["ticker"]))])
    capsys.readouterr()
    before = installation["database"].stat().st_mtime_ns
    assert main(["coin-shortlist", "--database", str(installation["database"]), "--format", "text"]) == 0
    text = capsys.readouterr().out
    assert "Shortlist from the coin scan at 2026-10-16 12:05 UTC" in text
    assert " 1. SOL/USD" in text and "round-trip fee" in text and "synthetic" in text
    assert installation["database"].stat().st_mtime_ns == before
    assert main(["coin-shortlist", "--database", str(installation["database"]), "--format", "json"]) == 0
    report = _json(capsys)
    assert report["shortlist"][0]["symbol"] == "SOL/USD" and report["optimistic"] is False


def test_a_live_scan_asks_kraken_once_and_a_failed_answer_ranks_nothing(installation, capsys, monkeypatch) -> None:
    from trade_graph.market_data_cli import coin_scanner

    calls = []

    class Refused:
        def get_text(self, url: str) -> str:
            calls.append(url)
            raise OSError("refused")

    monkeypatch.setattr(coin_scanner, "build_ticker_transport", lambda: Refused())
    assert main(["scan-coins", *_args(installation)]) == 0
    summary = _json(capsys)
    assert calls == ["https://api.kraken.com/0/public/Ticker"] and summary["public_requests"] == 1
    assert summary["status"] == "no_candidates" and summary["ticker"]["reason"] == "ticker_request_failed"
    assert summary["excluded"]["missing_spread"][0] == "ADA/USD"


def test_a_replay_is_labelled_optimistic_and_takes_no_ticker(installation, capsys, tmp_path) -> None:
    replay = ["--replay", "--as-of", "2026-10-16T12:05:00Z", "--source", "kraken_public_ohlc"]
    assert main(["scan-coins", *_args(installation, *replay)]) == 0
    summary = _json(capsys)
    assert summary["mode"] == "replay" and summary["optimistic"] is True
    assert "delisted_pairs_missing" in [reason["code"] for reason in summary["optimism_reasons"]]
    with pytest.raises(SystemExit):
        main(["scan-coins", *_args(installation, *replay, "--fixture-ticker", str(installation["ticker"]))])
    assert "replay" in capsys.readouterr().err
    assert main(["coin-shortlist", "--database", str(installation["database"])]) == 0
    assert "No live coin scan has been recorded yet" in capsys.readouterr().out


def test_missing_files_are_refused_and_never_created(installation, tmp_path, capsys) -> None:
    absent = tmp_path / "private" / "absent.sqlite"
    with pytest.raises(SystemExit):
        main(["scan-coins", "--database", str(absent), "--market-database", str(installation["market"]),
              "--fixture-ticker", str(installation["ticker"])])
    with pytest.raises(SystemExit):
        main(["scan-coins", "--database", str(installation["database"]), "--market-database", str(absent),
              "--fixture-ticker", str(installation["ticker"])])
    with pytest.raises(SystemExit):
        main(["coin-shortlist", "--database", str(absent)])
    assert not absent.exists()
    capsys.readouterr()
    with pytest.raises(SystemExit):  # the financial database is never read as a candle file
        main(["scan-coins", "--database", str(installation["database"]), "--market-database",
              str(installation["database"]), "--fixture-ticker", str(installation["ticker"])])
    assert "financial database" in capsys.readouterr().err


def test_a_replay_time_must_be_utc(installation, capsys) -> None:
    with pytest.raises(SystemExit):
        main(["scan-coins", *_args(installation, "--replay", "--as-of", "2026-10-16T12:05:00")])
    with pytest.raises(SystemExit):
        main(["scan-coins", *_args(installation, "--replay")])
    assert "--as-of" in capsys.readouterr().err


def test_repeated_live_scans_run_once_an_hour_after_the_hours_candle(installation, capsys, monkeypatch) -> None:
    from trade_graph.market_data_cli import coin_scanner

    clock = FrozenClock(AS_OF + timedelta(minutes=10))  # 12:15, after this hour's scan minute
    waits = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        clock.advance(seconds)

    monkeypatch.setattr(coin_scanner, "build_clock", lambda: clock)
    monkeypatch.setattr(coin_scanner, "build_sleep", lambda: sleep)
    assert main(["scan-coins", *_args(installation, "--fixture-ticker", str(installation["ticker"]),
                                      "--scans", "3", "--at-minute", "5")]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert [line["as_of_utc"] for line in lines] == [
        "2026-10-16T12:15:00.000000Z", "2026-10-16T13:05:00.000000Z", "2026-10-16T14:05:00.000000Z"]
    assert waits == [50 * 60, 60 * 60]
    # The fixture's bid and ask are from 12:04:40, so the later scans honestly find them too old to use.
    assert [line["status"] for line in lines] == ["no_candidates", "no_candidates", "no_candidates"]
    assert lines[1]["ticker"]["reason"] == "ticker_older_than_allowed"
    connection = sqlite3.connect(installation["database"])
    assert connection.execute("SELECT COUNT(*) FROM coin_scans WHERE mode = 'live'").fetchone()[0] == 3
    connection.close()


def test_a_replay_range_scans_one_moment_per_hour_without_waiting(installation, capsys, monkeypatch) -> None:
    from trade_graph.market_data_cli import coin_scanner

    monkeypatch.setattr(coin_scanner, "build_sleep", lambda: pytest.fail)
    assert main(["scan-coins", *_args(installation, "--replay", "--as-of", "2026-10-16T10:05:00Z", "--source",
                                      "kraken_public_ohlc", "--scans", "3")]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert [line["as_of_utc"][11:16] for line in lines] == ["10:05", "11:05", "12:05"]
    assert all(line["optimistic"] and line["status"] == "ok" for line in lines)
    with pytest.raises(SystemExit):
        main(["scan-coins", *_args(installation, "--scans", "0")])
