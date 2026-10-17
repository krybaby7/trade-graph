"""The price collector inside the service (W6-D, D222), against a scripted Kraken. No network, no real sleeping.

The exchange answers the recorded pair list of 2026-10-13 (13 eligible USD pairs), OHLC the way the recordings show
Kraken does (``tests/candle_support.FakeKraken``) and a synthetic Ticker answer in Kraken's format. All candles are
labelled synthetic; the scan is the real coin scanner on them.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from tests.alarm_support import candle_request
from tests.candle_support import FIXTURES, AdvancingSleeper, FakeKraken, error_wire, listed_pairs

from trade_graph.adapters.market.rate_limit import KrakenRestLimiter
from trade_graph.adapters.persistence.db import Database
from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.application.alarms.book import AlarmBook
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.coin_scanner.scanner import CoinScanner
from trade_graph.application.coin_scanner.store import CoinScanStore
from trade_graph.application.events.store import WakeStore
from trade_graph.application.price_collector import (
    COLLECTOR_VERSION,
    CollectorSettings,
    CollectorStopped,
    CollectorTransports,
    HotCoins,
    InterruptibleSleeper,
    PriceCollector,
)
from trade_graph.application.service_market_data import service_hot_coins
from trade_graph.contracts.wakes import FocusSelection
from trade_graph.domain.clock import FrozenClock

ASSET_PAIRS = (FIXTURES / "asset_pairs_2026-10-13.json").read_text(encoding="utf-8")
TICKER = json.dumps(json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "coin_scanner"
                                / "ticker_synthetic.json").read_text(encoding="utf-8"))["response"])
ELIGIBLE = ("ADA/USD", "AVAX/USD", "BTC/USD", "DOGE/USD", "DOT/USD", "ETH/USD", "LINK/USD", "LTC/USD", "PAXG/USD",
            "PEPE/USD", "SOL/USD", "USDUC/USD", "XRP/USD")
START = datetime(2026, 10, 17, 9, 5, 40, tzinfo=UTC)  # the 09:00 hour and the 09:05 candles have settled


class Exchange:
    """AssetPairs, OHLC and Ticker of a scripted Kraken; every request is remembered in order."""

    def __init__(self, clock: FrozenClock) -> None:
        self.clock = clock
        self.ohlc = FakeKraken(clock, list(listed_pairs().values()), delay_seconds=0.2)
        self.codes = {pair.source_pair: pair.symbol for pair in listed_pairs().values()}
        self.log: list[tuple[str, str | None, int | None]] = []
        self.ticker_answer = TICKER

    def get_text(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.path.endswith("/AssetPairs"):
            self.log.append(("catalog", None, None))
            self.clock.advance(0.5)
            return ASSET_PAIRS
        if parsed.path.endswith("/Ticker"):
            self.log.append(("ticker", None, None))
            self.clock.advance(0.5)
            return self.ticker_answer
        query = parse_qs(parsed.query)
        self.log.append(("ohlc", self.codes[query["pair"][0]], int(query["interval"][0])))
        return self.ohlc.get_text(url)

    def ohlc_requests(self) -> list[tuple[str, int]]:
        return [(symbol, interval) for kind, symbol, interval in self.log if kind == "ohlc"]

    def clear(self) -> None:
        self.log.clear()


class Rig:
    def __init__(self, tmp_path, *, coins: HotCoins | None = None, settings: CollectorSettings | None = None,
                 sleeper=None, start: datetime = START) -> None:
        self.clock = FrozenClock(start)
        self.market = MarketDataDatabase(tmp_path / "market" / "market_data.sqlite")
        self.store = CandleStore(self.market)
        self.financial = Database(tmp_path / "state" / "trade_graph.sqlite")
        self.exchange = Exchange(self.clock)
        self.limiter = KrakenRestLimiter(self.clock, sleeper or AdvancingSleeper(self.clock))
        self.events: list[dict] = []
        self.coins = coins or HotCoins(symbols=frozenset({"SOL/USD"}), one_minute=frozenset({"SOL/USD"}))
        self.scans = CoinScanStore(self.financial, self.clock)
        self.stop = threading.Event()
        self.collector = self.make(settings)

    def make(self, settings: CollectorSettings | None = None) -> PriceCollector:
        transports = CollectorTransports(catalog=self.exchange, ohlc=self.exchange, ticker=self.exchange)

        def scan(as_of, ticker, error):
            return self.scans.record(CoinScanner(self.store, clock=self.clock).scan(
                as_of=as_of, ticker=ticker, ticker_error=error))

        def last_scan():
            latest = self.scans.latest()
            return None if latest is None else latest.as_of

        return PriceCollector(self.store, self.clock, transports, self.limiter, coins=lambda: self.coins,
                              settings=settings or CollectorSettings(chunk_requests=30), scan=scan,
                              last_scan_at=last_scan, record=self.events.append, stop=self.stop,
                              evidence_label="synthetic_fixture")

    def until_idle(self, limit: int = 20) -> list:
        reports = []
        for _ in range(limit):
            report = self.collector.step()
            if report.kind == "idle":
                return reports
            reports.append(report)
        raise AssertionError(f"still working after {limit} steps: {[item.kind for item in reports]}")

    def close(self) -> None:
        self.market.close()
        self.financial.close()


@pytest.fixture
def rig(tmp_path):
    made = Rig(tmp_path)
    yield made
    made.close()


def test_the_pair_list_comes_first_then_the_hot_coins_then_the_ranking_and_wide_passes_then_one_scan(rig) -> None:
    reports = rig.until_idle()
    assert [report.kind for report in reports] == ["catalog", "candles", "candles", "scan"]
    requested = rig.exchange.ohlc_requests()
    hot = {(symbol, interval) for symbol in ("BTC/USD", "ETH/USD", "SOL/USD") for interval in (1, 5, 15, 60)}
    assert set(requested[:12]) == hot  # BTC and ETH at 1 minute by default, SOL as a focus coin
    others = [symbol for symbol in ELIGIBLE if symbol not in {"BTC/USD", "ETH/USD", "SOL/USD"}]
    assert set(requested[12:22]) == {(symbol, 60) for symbol in others}  # the ranking pass for the hourly scan
    assert set(requested[22:]) == {(symbol, interval) for symbol in others for interval in (5, 15)}
    assert len(requested) == len(set(requested)) == 42  # every series once
    assert [kind for kind, _symbol, _interval in rig.exchange.log].count("ticker") == 1
    assert rig.exchange.log[0][0] == "catalog" and rig.exchange.log[-1][0] == "ticker"
    scan = rig.scans.latest()
    assert scan is not None and scan.mode == "live" and scans_in(rig) == 1
    assert rig.collector.requests_total == 1 + 42 + 1 == rig.limiter.calls
    kinds = [event["job"] for event in rig.events]
    assert kinds[0] == "catalog" and kinds[-1] == "scan"
    assert rig.events[-1]["ranking_pass"]["attempted"] == 10


def scans_in(rig: Rig) -> int:
    return rig.financial.execute("SELECT COUNT(*) FROM coin_scans").fetchone()[0]


def test_each_chunk_is_one_candle_run_with_a_compact_report(rig) -> None:
    rig.until_idle()
    runs = [run for run in rig.store.runs() if run.kind == "public_collect"]
    assert len(runs) == 2 and all(run.status == "ok" for run in runs)
    first = runs[0]
    assert first.parameters["collector"] == COLLECTOR_VERSION and len(first.parameters["series"]) == 30
    assert first.parameters["paid_calls_enabled"] is False and first.parameters["live_trading_enabled"] is False
    assert {"failed", "gaps", "totals", "by_kind", "requests"} <= set(first.report)
    assert "series" not in first.report and first.report["failed"] == [] and first.report["gaps"] == []
    assert first.report["by_kind"]["hot"] == {"planned": 12, "attempted": 12, "ok": 12}
    assert len(json.dumps(first.report)) < 4000


def test_a_hot_series_is_asked_again_only_after_a_newer_candle_closed(rig) -> None:
    rig.until_idle()
    rig.exchange.clear()
    assert rig.collector.step().kind == "idle"  # nothing new has closed
    rig.clock.advance(15)  # past the idle check
    assert rig.collector.step().kind == "idle"
    rig.clock.advance(6 * 60)  # 09:10 has closed and settled for 1 and 5 minutes; no 15-minute or hourly close yet
    rig.until_idle()
    assert sorted(rig.exchange.ohlc_requests()) == sorted(
        (symbol, interval) for symbol in ("BTC/USD", "ETH/USD", "SOL/USD") for interval in (1, 5))


def test_the_ranking_pass_runs_once_an_hour_after_the_hour_settles_and_a_restart_does_not_scan_twice(rig) -> None:
    rig.until_idle()
    rig.clock.advance((datetime(2026, 10, 17, 10, 2, tzinfo=UTC) - rig.clock.now()).total_seconds())
    rig.exchange.clear()
    rig.until_idle()  # 10:02: the 10:00 hour has not settled yet (three minutes)
    assert ("ADA/USD", 60) not in rig.exchange.ohlc_requests()
    rig.clock.advance(70)
    rig.exchange.clear()
    rig.until_idle()
    hourly = [item for item in rig.exchange.ohlc_requests() if item[1] == 60]
    assert sorted(hourly) == sorted((symbol, 60) for symbol in ELIGIBLE)
    assert [kind for kind, _s, _i in rig.exchange.log].count("ticker") == 1 and scans_in(rig) == 2
    rig.collector = rig.make()  # a restart in the same hour
    rig.exchange.clear()
    rig.until_idle()
    assert rig.exchange.log == [] and scans_in(rig) == 2


def test_a_rate_limit_answer_stops_the_chunk_assumes_the_counter_full_and_pauses(rig) -> None:
    rig.collector.step()  # the pair list
    rig.exchange.ohlc.queue("XBTUSD", 5, error_wire("EAPI:Rate limit exceeded"))
    report = rig.collector.step()
    assert report.kind == "candles" and report.status == "partial"
    run = [run for run in rig.store.runs() if run.kind == "public_collect"][-1]
    assert run.report["stop_reason"] == "rate_limited" and run.report["not_reached"]
    assert rig.limiter.penalties == 1
    assert rig.collector.step().kind == "paused"
    rig.clock.advance(61)
    rest = rig.until_idle()
    assert rest and all(item.status == "ok" for item in rest if item.kind == "candles")
    assert ("BTC/USD", 5) in rig.exchange.ohlc_requests()[-40:]


def test_a_stop_request_ends_the_limiters_wait_and_the_chunk_is_still_recorded(tmp_path) -> None:
    stop = threading.Event()
    calls = {"n": 0}
    inner = InterruptibleSleeper(stop)

    def sleeper(seconds: float) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            stop.set()
        if stop.is_set():
            inner(seconds)
        made.clock.advance(seconds)

    made = Rig(tmp_path, sleeper=sleeper)
    made.stop = stop
    made.collector = made.make()
    try:
        made.collector.step()  # the pair list
        report = made.collector.step()
        assert report.kind == "stopped"
        run = [run for run in made.store.runs() if run.kind == "public_collect"][-1]
        assert run.status == "partial" and run.report["stop_reason"] == "service_stopping"
        assert run.report["requests"]["made"] >= 10
        assert made.collector.step().kind == "stopped"
        made.collector.resume()
        assert made.collector.step().kind == "candles"
    finally:
        made.close()
    stop.set()
    with pytest.raises(CollectorStopped):
        InterruptibleSleeper(stop)(5)


def test_a_clock_behind_a_recorded_run_pauses_collection_instead_of_dating_receipts_backwards(rig) -> None:
    rig.until_idle()
    rig.clock.advance(-3600)
    rig.coins = HotCoins(symbols=frozenset({"LTC/USD"}), one_minute=frozenset({"LTC/USD"}))  # due: never collected
    report = rig.collector.step()
    assert report.kind == "paused" and report.detail["error"] == "clock_regression"
    assert rig.events[-1]["error"] == "clock_regression"


def test_the_hot_coins_come_from_saved_records(tmp_path) -> None:
    from tests.chart_support import insert_shortlist
    from tests.day_plan_support import day_plan_account, install, plan

    account = day_plan_account(tmp_path)
    now = account.clock.now()
    insert_shortlist(account.database, ["LINK/USD", "ADA/USD"], as_of="2026-10-16T00:00:00.000000Z")
    install(account, plan())  # a SOL day plan for today
    store = WakeStore(account.database, account.clock)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    store.record_focus(portfolio_id=account.portfolio, day_start=day, day_end=day + timedelta(days=1), scan_id=None,
                       selection=FocusSelection.model_validate({"coins": [{"symbol": "DOT/USD", "note": "n"}],
                                                                "summary": "s"}), wake_id=None, task_id=None)
    AlarmBook(account.database, account.clock).create(candle_request("5", symbol="XRP/USD", interval=1))
    coins = service_hot_coins(account.database, account.clock, [account.portfolio], ledger=account.ledger,
                              paper_symbols=["BTC/USD", "LTC/USD"])
    assert coins.symbols >= {"BTC/USD", "ETH/USD", "LTC/USD", "LINK/USD", "ADA/USD", "SOL/USD", "DOT/USD", "XRP/USD"}
    assert coins.one_minute == {"DOT/USD", "XRP/USD"}  # today's focus coin and the coin with a 1-minute alarm


@pytest.mark.parametrize("bad", [{"chunk_requests": 0}, {"one_minute_every_seconds": 10}, {"hourly_scan": 1},
                                 {"one_minute_symbols": ("BTC/EUR",)}, {"ranking_every_hours": 0}])
def test_bad_collector_settings_are_refused(bad) -> None:
    from trade_graph.domain.errors import ValidationFailure

    with pytest.raises(ValidationFailure):
        CollectorSettings(**bad)


def test_an_answer_without_the_newer_candle_is_asked_again_only_after_the_retry_time(rig) -> None:
    from tests.candle_support import unix, wire

    rig.until_idle()
    rig.clock.advance(6 * 60)  # 09:13 or so: the five-minute candle that closed at 09:10 is due
    newest = unix(datetime(2026, 10, 17, 9, 0, tzinfo=UTC))
    known = rig.exchange.ohlc._row  # the same content Kraken sent before, then the forming row
    stale = wire("XXBTZUSD", [known("XBTUSD", 5, newest), known("XBTUSD", 5, newest + 300)])  # 09:05 not committed
    rig.exchange.ohlc.queue("XBTUSD", 5, stale)
    rig.exchange.clear()
    rig.until_idle()
    assert rig.exchange.ohlc_requests().count(("BTC/USD", 5)) == 1
    rig.exchange.clear()
    rig.clock.advance(20)
    rig.until_idle()
    assert ("BTC/USD", 5) not in rig.exchange.ohlc_requests()  # within the 60-second retry time
    rig.clock.advance(45)
    rig.until_idle()
    assert rig.exchange.ohlc_requests().count(("BTC/USD", 5)) == 1
