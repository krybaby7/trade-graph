"""Bounded public candle collection into the candle store, against a scripted exchange and recorded responses.

No test here uses the network. ``FakeKraken`` behaves as the recorded responses show Kraken behaves.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.candle_support import (
    FIXTURES,
    AdvancingSleeper,
    FakeKraken,
    QueueTransport,
    asset_pairs_with_new_coins,
    error_wire,
    fixture_text,
    listed_pairs,
    manifest,
    pair_records,
    row,
    unix,
    wire,
)
from tests.network_guard import block_network

from trade_graph.adapters.market.rate_limit import KrakenRestLimiter
from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.application.candle_collection_plan import CollectionScope, plan_collection
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.collect_candles import collect_candles, refresh_catalog
from trade_graph.contracts.candles import Candle, from_unix
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import ValidationFailure

NOW = datetime(2026, 10, 13, 15, 30, 20, tzinfo=UTC)
ASSET_PAIRS = (FIXTURES / "asset_pairs_2026-10-13.json").read_text(encoding="utf-8")
PAIRS = listed_pairs()
CODE_TO_SYMBOL = {pair.source_pair: pair.symbol for pair in PAIRS.values()}
SYMBOL_TO_CODE = {symbol: code for code, symbol in CODE_TO_SYMBOL.items()}
ORDER = [(item.record.symbol, item.interval_minutes)
         for item in plan_collection(list(pair_records().values()), CollectionScope(), {}).series]


class Crash(BaseException):
    pass


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


@pytest.fixture
def new_store(tmp_path):
    databases: list[MarketDataDatabase] = []

    def make(name: str = "market_data") -> CandleStore:
        database = MarketDataDatabase(tmp_path / "market" / f"{name}.sqlite")
        databases.append(database)
        return CandleStore(database)

    yield make
    for database in databases:
        database.close()


@pytest.fixture
def store(new_store):
    return new_store()


class Env:
    """A catalog already in the store, a scripted exchange, and a limiter that moves the same scripted clock."""

    def __init__(self, store: CandleStore, *, seed_catalog: bool = True, start: datetime = NOW,
                 asset_pairs: str = ASSET_PAIRS, **exchange_options) -> None:
        self.store = store
        self.clock = FrozenClock(start)
        if seed_catalog:
            refresh_catalog(store, self.clock, QueueTransport([asset_pairs], self.clock, delay_seconds=1),
                            "recorded_public_fixture")
        self.sleeper = AdvancingSleeper(self.clock)
        self.limiter = KrakenRestLimiter(self.clock, self.sleeper)
        self.exchange = FakeKraken(self.clock, list(PAIRS.values()), **exchange_options)

    def collect(self, scope: CollectionScope | None = None, max_requests: int = 1000,
                label: str = "synthetic_fixture", transport=None) -> dict:
        return collect_candles(self.store, self.clock, transport or self.exchange, self.limiter,
                               scope or CollectionScope(), max_requests, label)

    def requested(self) -> list[tuple[str, int]]:
        return [(CODE_TO_SYMBOL[code], interval) for code, interval, _since in self.exchange.requests]


def entry(report: dict, symbol: str, interval: int) -> dict:
    return next(item for item in report["series"] if item["symbol"] == symbol and item["interval_minutes"] == interval)


def small_scope(*, intervals=(60,), symbols=("BTC/USD", "SOL/USD"), one_minute=()) -> CollectionScope:
    return CollectionScope(intervals=intervals, symbols=symbols, one_minute_symbols=one_minute)


def row_count(store: CandleStore) -> int:
    return store.database.query("SELECT COUNT(*) AS n FROM market_candles")[0]["n"]


def sources(store: CandleStore) -> set[str]:
    return {item.source for item in store.series()}


# --- scope, order, one request per series ---------------------------------------------------------------------------


def test_each_eligible_pair_and_interval_is_requested_once_most_perishable_data_first(store: CandleStore) -> None:
    env = Env(store)
    report = env.collect()
    assert env.requested() == ORDER and len(ORDER) == 41
    assert all(call.startswith("https://api.kraken.com/0/public/OHLC?pair=") for call in env.exchange.calls)
    assert report["status"] == "ok" and report["stop_reason"] is None and report["error"] is None
    assert report["counts"] == {"planned_series": 41, "attempted": 41, "ok": 41, "failed": 0, "not_reached": 0}
    assert report["requests"]["made"] == 41 and report["requests"]["max"] == 1000
    assert report["not_reached"] == [] and report["unknown_symbols"] == []
    assert [(item["symbol"], item["interval_minutes"]) for item in report["series"]] == ORDER
    assert [interval for _symbol, interval in ORDER] == sorted(interval for _symbol, interval in ORDER)


def test_one_minute_candles_are_collected_only_for_the_explicit_list(store: CandleStore, new_store) -> None:
    env = Env(store)
    env.collect()
    one_minute = sorted(symbol for symbol, interval in env.requested() if interval == 1)
    assert one_minute == ["BTC/USD", "ETH/USD"]
    custom = Env(new_store("second"))
    custom.collect(CollectionScope(intervals=(1,), one_minute_symbols=("SOL/USD", "LINK/USD")))
    assert sorted(custom.requested()) == [("LINK/USD", 1), ("SOL/USD", 1)]
    none = Env(new_store("third"))
    report = none.collect(CollectionScope(intervals=(1, 15), symbols=("SOL/USD",), one_minute_symbols=()))
    assert none.requested() == [("SOL/USD", 15)] and report["status"] == "ok"


def test_pairs_that_may_not_be_collected_are_skipped_and_reported(store: CandleStore) -> None:
    env = Env(store)
    report = env.collect()
    skipped = {item["symbol"]: item["reasons"] for item in report["skipped_pairs"]}
    assert len(skipped) == 31
    assert skipped["USDT/USD"] == ["excluded:stablecoin"] and skipped["MSOL/USD"] == ["excluded:yield_token"]
    assert skipped["ACA/USD"] == ["status:cancel_only"] and skipped["EURR/USD"] == [
        "excluded:stablecoin", "status:post_only"]
    requested_codes = {code for code, _interval, _since in env.exchange.requests}
    assert not requested_codes & {SYMBOL_TO_CODE[symbol] for symbol in skipped}
    assert not {symbol for symbol, _interval in env.requested()} & set(skipped)


def test_a_named_pair_that_is_excluded_or_unknown_is_reported_and_never_requested(store: CandleStore) -> None:
    env = Env(store)
    scope = CollectionScope(intervals=(15,), symbols=("SOL/USD", "USDT/USD", "NOPE/USD"), one_minute_symbols=())
    report = env.collect(scope)
    assert env.requested() == [("SOL/USD", 15)]
    assert report["unknown_symbols"] == ["NOPE/USD"]
    assert [item["symbol"] for item in report["skipped_pairs"]] == ["USDT/USD"]
    assert report["status"] == "partial"  # a name that matches nothing is a mistake the run cannot hide


def test_without_a_stored_catalog_nothing_is_requested_and_the_failed_run_is_recorded(store: CandleStore) -> None:
    env = Env(store, seed_catalog=False)
    report = env.collect()
    assert report["status"] == "failed" and report["error"] == "no_catalog"
    assert env.exchange.calls == [] and report["requests"]["made"] == 0
    run = store.runs()[-1]
    assert (run.kind, run.status) == ("public_collect", "failed") and run.report == json.loads(json.dumps(report))


def test_a_scope_with_nothing_usable_fails_without_a_request(store: CandleStore) -> None:
    env = Env(store)
    report = env.collect(CollectionScope(symbols=("USDT/USD", "ACA/USD"), one_minute_symbols=()))
    assert report["status"] == "failed" and report["error"] == "nothing_to_collect"
    assert env.exchange.calls == [] and len(report["skipped_pairs"]) == 2


# --- the first and later collections --------------------------------------------------------------------------------


def test_the_first_collection_asks_for_the_full_window_and_reports_each_series(store: CandleStore) -> None:
    env = Env(store)
    report = env.collect()
    assert all(since is None for _code, _interval, since in env.exchange.requests)
    assert not any("since" in call for call in env.exchange.calls)
    for item in report["series"]:
        assert item["status"] == "ok" and item["error"] is None and item["since"] is None
        assert (item["rows_received"], item["candles_returned"]) == (721, 720)
        assert (item["inserted"], item["duplicates"], item["revised"]) == (720, 0, 0)
        assert item["continuity"]["status"] == "first_collection"
        assert item["coverage"]["expected_slots"] == item["coverage"]["present_slots"] == 720
        assert item["coverage"]["missing_slots"] == 0 and item["coverage"]["missing_ranges"] == []
    totals = report["totals"]
    assert totals["rows_received"] == 41 * 721 and totals["inserted"] == 41 * 720
    assert totals["duplicates"] == totals["revised"] == totals["series_with_gap"] == 0
    assert report["by_interval"]["1"]["planned_series"] == 2 and report["by_interval"]["5"]["inserted"] == 13 * 720


def test_a_later_collection_asks_from_one_interval_before_the_newest_stored_candle(store: CandleStore) -> None:
    env = Env(store)
    first = env.collect()
    newest = {key: from_unix(0) for key in ORDER}
    for item in first["series"]:
        newest[(item["symbol"], item["interval_minutes"])] = datetime.fromisoformat(item["last_open_utc"])
    made = len(env.exchange.requests)
    env.clock.advance(3600)
    second = env.collect()
    assert second["status"] == "ok" and len(env.exchange.requests) - made == 41
    for code, interval, since in env.exchange.requests[made:]:
        assert since == unix(newest[(CODE_TO_SYMBOL[code], interval)]) - interval * 60
    for item in second["series"]:
        step = item["interval_minutes"] * 60
        before = newest[(item["symbol"], item["interval_minutes"])]
        assert item["continuity"]["status"] == "connected"
        assert item["duplicates"] == 2 and item["revised"] == 0
        advanced = (unix(datetime.fromisoformat(item["last_open_utc"])) - unix(before)) // step
        assert item["inserted"] == advanced
        assert item["since"] == unix(before) - step
    assert second["totals"]["inserted"] > 0 and second["totals"]["series_with_gap"] == 0


def other_source_candle(open_unix: int, source: str) -> Candle:
    """A stored 60-minute BTC/USD candle of another source, for tests of the incremental cursor."""
    return Candle(
        symbol="BTC/USD", source_pair="XBTUSD", interval_minutes=60, open_time_utc=from_unix(open_unix),
        close_time_utc=from_unix(open_unix + 3600), available_at_utc=from_unix(open_unix + 3603), open=Decimal("100"),
        high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"), vwap=Decimal("100.2"),
        volume=Decimal("2"), trade_count=3, source=source)


def test_the_since_cursor_comes_only_from_the_series_of_the_runs_own_source(store: CandleStore) -> None:
    env = Env(store)
    scope = small_scope(symbols=("BTC/USD",))
    first = env.collect(scope, label="recorded_public_fixture")
    newest = unix(datetime.fromisoformat(first["series"][0]["last_open_utc"]))
    trades_run = store.begin_run(kind="trades_backfill", source="kraken_public_trades",
                                 evidence_label="synthetic_fixture", started_at=env.clock.now(), parameters={})
    store.save([other_source_candle(newest + 3600 * hours, "kraken_public_trades") for hours in range(1, 6)],
               run_id=trades_run)  # a newer series of another source, same symbol and interval
    store.save([other_source_candle(newest - 3600 * hours, "synthetic") for hours in range(10, 12)],
               run_id=trades_run)  # and a synthetic one that sorts after the collector's source
    assert {item.source for item in store.series()} == {"kraken_public_ohlc", "kraken_public_trades", "synthetic"}
    env.clock.advance(3600)
    made = len(env.exchange.requests)
    env.collect(scope, label="recorded_public_fixture")
    assert env.exchange.requests[made:] == [("XBTUSD", 60, newest - 3600)]
    synthetic = env.collect(scope, label="synthetic_fixture")
    assert env.exchange.requests[-1] == ("XBTUSD", 60, newest - 3600 * 11)  # the synthetic series' own newest candle
    assert synthetic["series"][0]["continuity"]["status"] == "connected"


def test_a_response_holding_only_the_forming_candle_cannot_confirm_a_stored_series(store: CandleStore) -> None:
    env = Env(store)
    scope = small_scope(symbols=("BTC/USD",))
    env.collect(scope)
    env.clock.advance(3600)
    forming = unix(env.clock.now()) - unix(env.clock.now()) % 3600
    env.exchange.queue("XBTUSD", 60, wire("XXBTZUSD", [row(forming)], last=forming - 3600))
    report = env.collect(scope)
    item = report["series"][0]
    assert item["status"] == "ok" and item["candles_returned"] == 0 and item["inserted"] == 0
    assert item["continuity"]["status"] == "unverified" and item["continuity"]["action"] is None
    assert item["coverage"] is None
    assert report["totals"]["series_unverified"] == 1 and report["totals"]["series_with_gap"] == 0
    assert report["status"] == "ok"


def test_rerunning_with_the_whole_window_again_inserts_nothing_and_keeps_the_original_receipts(
        store: CandleStore) -> None:
    env = Env(store, ignore_since=True)
    scope = small_scope()
    first = env.collect(scope)
    rows_after_first = row_count(store)
    slot = datetime.fromisoformat(first["series"][0]["last_open_utc"])
    receipt = store.revisions("BTC/USD", 60, "synthetic", slot)[0].candle.available_at_utc
    env.clock.advance(5)
    second = env.collect(scope)
    assert second["status"] == "ok" and second["totals"]["inserted"] == 0 and second["totals"]["revised"] == 0
    assert all(item["duplicates"] == 720 for item in second["series"])
    assert row_count(store) == rows_after_first
    revisions = store.revisions("BTC/USD", 60, "synthetic", slot)
    assert len(revisions) == 1 and revisions[0].candle.available_at_utc == receipt
    assert [run.status for run in store.runs()[-2:]] == ["ok", "ok"]
    assert store.runs()[-1].run_id != store.runs()[-2].run_id


def test_a_changed_committed_candle_becomes_a_new_revision_and_is_reported(store: CandleStore) -> None:
    env = Env(store)
    scope = small_scope(symbols=("BTC/USD",))
    first = env.collect(scope)
    newest = unix(datetime.fromisoformat(first["series"][0]["last_open_utc"]))
    late_trade = [newest, "100.0", "103.0", "99.0", "101.0", "100.5", "3.00000000", 11]
    env.exchange.revised[("XBTUSD", 60, newest)] = late_trade
    env.clock.advance(5)
    second = env.collect(scope)
    assert second["totals"]["revised"] == 1 and second["series"][0]["revised"] == 1
    revisions = store.revisions("BTC/USD", 60, "synthetic", from_unix(newest))
    assert [item.revision for item in revisions] == [1, 2]
    assert revisions[1].candle.available_at_utc > revisions[0].candle.available_at_utc
    assert revisions[1].candle.close == 101 and revisions[0].candle.close != revisions[1].candle.close


def test_a_stored_candle_has_the_receipt_time_of_its_own_request(store: CandleStore) -> None:
    env = Env(store)
    report = env.collect(small_scope(symbols=("BTC/USD",)))
    item = report["series"][0]
    stored = store.revisions("BTC/USD", 60, "synthetic", datetime.fromisoformat(item["last_open_utc"]))[0].candle
    assert stored.available_at_utc == datetime.fromisoformat(item["received_at_utc"])
    assert stored.available_at_utc >= stored.close_time_utc
    assert stored.available_at_utc - env.clock.now() <= timedelta(0)


def test_quiet_intervals_are_stored_as_kraken_sent_them_and_counted(store: CandleStore) -> None:
    env = Env(store, quiet_every={"SOLUSD": 4})
    report = env.collect(small_scope())
    sol, btc = entry(report, "SOL/USD", 60), entry(report, "BTC/USD", 60)
    assert sol["no_trade_candles"] == 180 and btc["no_trade_candles"] == 0
    assert sol["inserted"] == 720 and sol["coverage"]["missing_slots"] == 0
    assert report["totals"]["no_trade_candles"] == 180
    opened = datetime.fromisoformat(sol["first_open_utc"])
    quiet = [stored for stored in (store.revisions("SOL/USD", 60, "synthetic", opened + timedelta(hours=hour))
                                   for hour in range(8)) if stored and stored[0].candle.trade_count == 0]
    assert quiet and all(stored[0].candle.volume == 0 for stored in quiet)


def test_the_report_says_how_many_eligible_pairs_were_never_individually_reviewed(store: CandleStore) -> None:
    env = Env(store, asset_pairs=asset_pairs_with_new_coins("NEWA", "NEWB"))
    catalog = env.collect(small_scope(symbols=("BTC/USD",)))["catalog"]
    assert catalog["eligible_pairs"] == 15 and catalog["eligible_unreviewed_pairs"] == 2
    assert catalog["unreviewed_asset_count"] == 5  # the three unreviewed coins of the recording and the two new ones
    note = catalog["verification_note"]
    assert "not individually reviewed" in note and "verified clean" in note


def test_a_pair_with_no_data_returns_only_the_time_zero_row_and_is_reported_as_such(store: CandleStore) -> None:
    env = Env(store, no_data=["SOLUSD"])
    report = env.collect(small_scope())
    sol = entry(report, "SOL/USD", 60)
    assert sol["status"] == "ok" and sol["rows_received"] == 1 and sol["candles_returned"] == 0
    assert sol["continuity"]["status"] == "no_data" and sol["coverage"] is None
    assert report["totals"]["series_without_data"] == 1 and report["status"] == "ok"
    assert ("SOL/USD", "synthetic", 60) not in {(s.symbol, s.source, s.interval_minutes) for s in store.series()}


# --- gaps -----------------------------------------------------------------------------------------------------------


def test_a_pause_longer_than_the_window_is_flagged_as_a_gap_with_its_size(store: CandleStore) -> None:
    env = Env(store)
    scope = CollectionScope(intervals=(1, 5), symbols=("BTC/USD",), one_minute_symbols=("BTC/USD",))
    first = env.collect(scope)
    newest = unix(datetime.fromisoformat(entry(first, "BTC/USD", 1)["last_open_utc"]))
    env.clock.advance(20 * 3600)
    second = env.collect(scope)
    one = entry(second, "BTC/USD", 1)
    window_start = one["continuity"]["first_returned_open_utc"]
    missing = (unix(datetime.fromisoformat(window_start)) - newest) // 60 - 1
    assert one["continuity"]["status"] == "gap" and one["continuity"]["missing_slots"] == missing > 0
    assert "Trades backfill" in one["continuity"]["action"] and "archive" in one["continuity"]["action"]
    assert one["coverage"]["missing_slots"] == missing
    assert one["coverage"]["missing_ranges"] == [{
        "first_open_utc": datetime.fromtimestamp(newest + 60, UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "last_open_utc": datetime.fromtimestamp(unix(datetime.fromisoformat(window_start)) - 60, UTC).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"),
        "slots": missing,
    }]
    assert entry(second, "BTC/USD", 5)["continuity"]["status"] == "connected"
    assert second["totals"]["series_with_gap"] == 1 and second["status"] == "ok"
    stored = store.coverage("BTC/USD", 1, "synthetic", start_open=from_unix(newest),
                            end_open=from_unix(newest + 60 * (missing + 2)), as_of=env.clock.now())
    assert stored.missing == missing


def test_a_hole_inside_one_response_is_a_missing_slot_in_the_covered_window(store: CandleStore) -> None:
    env = Env(store)
    step = 3600
    forming = unix(NOW) - unix(NOW) % step
    opens = [forming + offset * step for offset in range(-10, 1) if offset != -5]  # the last one is still forming
    rows = [row(open_time) for open_time in opens]
    env.exchange.queue("XBTUSD", 60, json.dumps({"error": [], "result": {"XXBTZUSD": rows, "last": forming - step}}))
    report = env.collect(small_scope(symbols=("BTC/USD",)))
    item = report["series"][0]
    assert item["candles_returned"] == 9 and item["coverage"]["expected_slots"] == 10
    assert item["coverage"]["missing_slots"] == 1
    assert item["coverage"]["missing_ranges"][0]["slots"] == 1


# --- stopping early -------------------------------------------------------------------------------------------------


def test_a_run_that_reaches_its_request_limit_lists_exactly_what_it_did_not_reach(store: CandleStore) -> None:
    env = Env(store)
    report = env.collect(max_requests=5)
    assert report["status"] == "partial" and report["stop_reason"] == "max_requests"
    assert report["stopped"] == {"reason": "max_requests", "requests_made": 5}
    assert report["requests"]["made"] == len(env.exchange.calls) == 5
    assert [(item["symbol"], item["interval_minutes"]) for item in report["series"]] == ORDER[:5]
    assert [tuple(item) for item in report["not_reached"]] == ORDER[5:]
    assert report["counts"] == {"planned_series": 41, "attempted": 5, "ok": 5, "failed": 0, "not_reached": 36}
    assert ORDER[:5] == [("BTC/USD", 1), ("ETH/USD", 1), ("ADA/USD", 5), ("AVAX/USD", 5), ("BTC/USD", 5)]
    run = store.runs()[-1]
    assert run.status == "partial" and run.report["not_reached"] == report["not_reached"]


def test_a_limit_equal_to_the_plan_is_a_complete_run_and_one_less_is_not(store: CandleStore, new_store) -> None:
    env = Env(store)
    assert env.collect(max_requests=41)["status"] == "ok"
    other = Env(new_store("other"))
    report = other.collect(max_requests=40)
    assert report["status"] == "partial" and report["not_reached"] == [list(ORDER[-1])]


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "5", None, 10_001])
def test_the_request_limit_must_be_a_bounded_whole_number(store: CandleStore, bad) -> None:
    env = Env(store)
    with pytest.raises(ValidationFailure):
        env.collect(max_requests=bad)
    assert env.exchange.calls == [] and [run.kind for run in store.runs()] == ["catalog"]


def test_a_rate_limit_error_stops_the_run_and_lists_the_rest(store: CandleStore) -> None:
    env = Env(store)
    index = ORDER.index(("LINK/USD", 5))
    env.exchange.queue("LINKUSD", 5, error_wire("EAPI:Rate limit exceeded"))
    report = env.collect()
    assert report["status"] == "partial" and report["stop_reason"] == "rate_limited"
    assert report["requests"]["made"] == index + 1
    failed = entry(report, "LINK/USD", 5)
    assert failed["status"] == "failed" and failed["error"] == "rate_limited"
    assert [tuple(item) for item in report["not_reached"]] == ORDER[index + 1:]
    assert report["counts"]["ok"] == index and report["counts"]["failed"] == 1
    assert report["stopped"]["reason"] == "rate_limited"


@pytest.mark.parametrize("message", [
    "EGeneral:Too many requests", "EAuth:Too many requests", "EGeneral:Temporary lockout",
    "EOrder:Rate limit exceeded",
])
def test_the_other_wordings_of_a_rate_limit_also_stop_the_run_at_once(store: CandleStore, message: str) -> None:
    env = Env(store)
    env.exchange.queue("ETHUSD", 1, error_wire(message))
    report = env.collect()
    assert report["stop_reason"] == "rate_limited" and report["requests"]["made"] == 2
    assert entry(report, "ETH/USD", 1)["error"] == "rate_limited"


def test_an_http_429_also_stops_the_run(store: CandleStore) -> None:
    class Rejected(Exception):
        response = type("Response", (), {"status_code": 429})()

    env = Env(store)
    env.exchange.queue("ETHUSD", 1, Rejected("SECRET 429"))
    report = env.collect()
    assert report["stop_reason"] == "rate_limited" and report["requests"]["made"] == 2
    assert "SECRET" not in json.dumps(report)


def test_a_rate_limit_on_the_first_request_is_a_failed_run(store: CandleStore) -> None:
    env = Env(store)
    env.exchange.queue("XBTUSD", 1, error_wire("EAPI:Rate limit exceeded"))
    report = env.collect()
    assert report["status"] == "failed" and report["stop_reason"] == "rate_limited"
    assert len(report["not_reached"]) == 40 and row_count(store) == 0


def test_a_short_throttle_is_waited_out_once_and_the_request_is_repeated(store: CandleStore) -> None:
    env = Env(store)
    retry_at = unix(NOW) + 12
    env.exchange.queue("XBTUSD", 1, error_wire(f"EService: Throttled: {retry_at}"))
    report = env.collect()
    assert report["status"] == "ok" and report["requests"]["throttle_retries"] == 1
    assert report["requests"]["made"] == 42 and len(env.exchange.calls) == 42
    assert env.exchange.requests[0] == env.exchange.requests[1]  # the same request, repeated once
    # The error arrived 2 s into the run (1 s catalog, 1 s request): 12 - 2 = 10 s remain, plus a 2 s margin.
    assert 12.0 in env.sleeper.requests
    assert entry(report, "BTC/USD", 1)["status"] == "ok"


def test_a_second_throttle_in_the_same_run_stops_the_run(store: CandleStore) -> None:
    env = Env(store)
    stamp = unix(NOW) + 12
    env.exchange.queue("XBTUSD", 1, error_wire(f"EService: Throttled: {stamp}"))
    env.exchange.queue("ETHUSD", 1, error_wire(f"EService: Throttled: {stamp + 13}"))
    report = env.collect()
    assert report["stop_reason"] == "throttled" and report["requests"]["throttle_retries"] == 1
    assert entry(report, "ETH/USD", 1)["error"] == "throttled" and report["requests"]["made"] == 3
    assert [tuple(item) for item in report["not_reached"]] == ORDER[2:]


def test_a_long_or_unreadable_throttle_is_not_waited_out(new_store) -> None:
    for number, message in enumerate((f"EService: Throttled: {unix(NOW) + 600}", "EService:Throttled")):
        other = Env(new_store(f"throttle{number}"))
        other.exchange.queue("XBTUSD", 1, error_wire(message))
        report = other.collect()
        assert report["stop_reason"] == "throttled" and report["requests"]["throttle_retries"] == 0
        assert report["requests"]["made"] == 1 and len(other.sleeper.requests) == 0
        assert report["stopped"]["reason"] == "throttled"


def test_a_throttle_with_no_request_left_in_the_budget_stops_the_run(store: CandleStore) -> None:
    env = Env(store)
    env.exchange.queue("XBTUSD", 1, error_wire(f"EService: Throttled: {unix(NOW) + 12}"))
    report = env.collect(max_requests=1)
    assert report["stop_reason"] == "throttled" and report["requests"]["made"] == 1


def test_one_failed_series_does_not_discard_the_others(store: CandleStore) -> None:
    env = Env(store)
    env.exchange.queue("SOLUSD", 15, "SECRET-BODY not json")
    report = env.collect()
    assert report["status"] == "partial" and report["stop_reason"] is None
    assert report["counts"] == {"planned_series": 41, "attempted": 41, "ok": 40, "failed": 1, "not_reached": 0}
    failed = entry(report, "SOL/USD", 15)
    assert (failed["status"], failed["error"], failed["inserted"]) == ("failed", "response_invalid", 0)
    assert failed["continuity"] is None and failed["coverage"] is None
    assert "SECRET" not in json.dumps(report) and "SECRET" not in json.dumps(store.runs()[-1].report)
    present = {(item.symbol, item.interval_minutes) for item in store.series()}
    assert ("SOL/USD", 15) not in present and len(present) == 40


def test_an_absurd_trade_count_fails_that_series_as_an_invalid_response_not_as_a_storage_error(
        store: CandleStore) -> None:
    env = Env(store)
    step = 3600
    forming = unix(NOW) - unix(NOW) % step
    rows = [row(forming + offset * step) for offset in range(-5, 1)]
    rows[2] = row(rows[2][0], count=10**30)
    env.exchange.queue("SOLUSD", 60, wire("SOLUSD", rows, last=forming - step))
    report = env.collect(small_scope())
    failed = entry(report, "SOL/USD", 60)
    assert (failed["status"], failed["error"], failed["inserted"]) == ("failed", "response_invalid", 0)
    assert entry(report, "BTC/USD", 60)["status"] == "ok" and report["counts"]["failed"] == 1


def test_ten_failures_in_a_row_stop_the_run_but_a_success_resets_the_count(store: CandleStore, new_store) -> None:
    env = Env(store)
    for symbol, interval in ORDER[:10]:
        env.exchange.queue(SYMBOL_TO_CODE[symbol], interval, "not json")
    report = env.collect()
    assert report["status"] == "failed" and report["stop_reason"] == "consecutive_failures"
    assert report["requests"]["made"] == 10 and [tuple(item) for item in report["not_reached"]] == ORDER[10:]
    assert report["stopped"]["consecutive_failures"] == 10
    other = Env(new_store("other"))
    for position, (symbol, interval) in enumerate(ORDER[:19]):
        if position != 9:
            other.exchange.queue(SYMBOL_TO_CODE[symbol], interval, "not json")
    resumed = other.collect()
    assert resumed["stop_reason"] is None and resumed["counts"]["failed"] == 18 and resumed["status"] == "partial"


def test_a_storage_failure_fails_that_series_only_and_never_shows_its_detail(store: CandleStore, monkeypatch) -> None:
    env = Env(store)
    original = store.save

    def failing(candles, *, run_id):
        if candles and candles[0].symbol == "SOL/USD" and candles[0].interval_minutes == 60:
            raise sqlite3.OperationalError("SECRET disk full")
        return original(candles, run_id=run_id)

    monkeypatch.setattr(store, "save", failing)
    report = env.collect()
    failed = entry(report, "SOL/USD", 60)
    assert failed["status"] == "failed" and failed["error"] == "persistence_failed"
    assert report["counts"]["ok"] == 40 and "SECRET" not in json.dumps(report)


def test_an_exception_from_the_transport_fails_that_series_with_a_fixed_category(store: CandleStore) -> None:
    class Odd(Exception):
        pass

    def explode(code: str, interval: int) -> None:
        if (code, interval) == ("SOLUSD", 5):
            raise Odd("SECRET")

    env = Env(store)
    env.exchange.before = explode
    report = env.collect()
    assert entry(report, "SOL/USD", 5)["error"] == "request_failed"
    assert report["counts"]["failed"] == 1 and "SECRET" not in json.dumps(report)


# --- restarts and the run record ------------------------------------------------------------------------------------


def test_the_run_and_its_parameters_are_recorded_before_the_first_request(store: CandleStore) -> None:
    env = Env(store)
    seen: list = []
    env.exchange.before = lambda _code, _interval: seen.extend(store.runs()[-1:]) or None
    env.collect(max_requests=2)
    run = seen[0]
    assert (run.kind, run.status, run.report, run.source) == ("public_collect", "interrupted", None, "synthetic")
    assert run.evidence_label == "synthetic_fixture"
    assert run.parameters["max_requests"] == 2 and run.parameters["paid_calls_enabled"] is False
    assert run.parameters["live_trading_enabled"] is False
    assert run.parameters["scope"] == CollectionScope().to_document()
    assert run.parameters["planned_series"] == 41 and run.parameters["limiter"]["ceiling"] == 10
    assert run.parameters["endpoint"] == "https://api.kraken.com/0/public/OHLC"


def test_a_crash_mid_run_leaves_the_finished_series_stored_and_an_interrupted_run(store: CandleStore) -> None:
    env = Env(store)
    calls = {"count": 0}

    def crash(_code, _interval) -> None:
        calls["count"] += 1
        if calls["count"] == 4:
            raise Crash

    env.exchange.before = crash
    with pytest.raises(Crash):
        env.collect()
    assert store.runs()[-1].status == "interrupted" and store.runs()[-1].report is None
    done = {(item.symbol, item.interval_minutes): item.candle_count for item in store.series()}
    assert done == {key: 720 for key in ORDER[:3]}
    env.exchange.before = None
    again = env.collect()
    assert again["status"] == "ok" and again["counts"]["ok"] == 41
    for symbol, interval in ORDER[:3]:
        item = entry(again, symbol, interval)
        assert item["continuity"]["status"] == "connected" and item["since"] is not None
    for symbol, interval in ORDER[3:]:
        assert entry(again, symbol, interval)["continuity"]["status"] == "first_collection"
    assert [run.status for run in store.runs() if run.kind == "public_collect"] == ["interrupted", "ok"]


# --- cost, rate limiting, labels ------------------------------------------------------------------------------------


def test_every_request_takes_a_limiter_slot_and_the_run_is_slowed_to_the_assumed_rate(store: CandleStore) -> None:
    env = Env(store)
    started = env.clock.now()
    report = env.collect()
    assert env.limiter.calls == 41 == report["requests"]["made"]
    assert env.sleeper.requests and report["requests"]["limiter"]["ceiling"] == 10
    assert report["requests"]["limiter"]["calls"] == 41
    assert (env.clock.now() - started).total_seconds() >= (41 - 10) / 0.3 - 1


def test_the_report_estimates_storage_and_the_collection_cadence(store: CandleStore) -> None:
    env = Env(store)
    report = env.collect()
    storage = report["storage"]
    assert storage["bytes_per_row_estimate"] == 118
    assert storage["this_run"] == {"rows_inserted": 29_520, "estimated_bytes_added": 29_520 * 118}
    assert storage["store_total"] == {"rows": 29_520, "estimated_bytes": 29_520 * 118}
    quarter = storage["per_quarter"]["per_quarter_total"]
    assert quarter["rows"] == 2 * 131_040 + 13 * (26_208 + 8_736 + 2_184)
    cadence = report["cadence"]
    assert cadence["intervals"]["1"]["window_hours"] == 12 and cadence["intervals"]["60"]["window_hours"] == 720
    assert cadence["intervals"]["5"]["recommended_every_hours"] == 30
    assert cadence["all_intervals"]["requests_per_pass"] == 41
    assert cadence["all_intervals"]["longest_pause_without_a_gap_hours"] == 12


def test_the_report_is_plain_json_and_never_enables_paid_calls_or_live_trading(store: CandleStore) -> None:
    env = Env(store)
    report = env.collect()
    assert json.loads(json.dumps(report)) == report and len(json.dumps(report)) < 80_000
    assert report["paid_calls_enabled"] is False and report["live_trading_enabled"] is False
    assert report["evidence_label"] == "synthetic_fixture" and report["source"] == "synthetic"
    assert report["catalog"]["rules_version"] == "pair-rules-2026-10-13.1" and report["catalog"]["eligible_pairs"] == 13
    run = store.runs()[-1]
    assert run.report == report and run.finished_at_utc is not None and run.status == "ok"


def test_synthetic_runs_write_synthetic_series_and_recorded_or_live_runs_write_kraken_series(
        store: CandleStore) -> None:
    env = Env(store)
    env.collect(small_scope(), label="synthetic_fixture")
    assert sources(store) == {"synthetic"}
    env.collect(small_scope(), label="recorded_public_fixture")
    assert sources(store) == {"synthetic", "kraken_public_ohlc"}
    env.collect(small_scope(), label="live_public_smoke")
    env.collect(small_scope(), label="live_public")
    assert [run.evidence_label for run in store.runs() if run.kind == "public_collect"] == [
        "synthetic_fixture", "recorded_public_fixture", "live_public_smoke", "live_public"]


@pytest.mark.parametrize("label", ["local_archive_file", "invented", "", None])
def test_an_unsupported_evidence_label_is_refused_before_anything_is_recorded(store: CandleStore, label) -> None:
    env = Env(store)
    with pytest.raises(ValidationFailure):
        env.collect(label=label)
    assert [run.kind for run in store.runs()] == ["catalog"] and env.exchange.calls == []


def test_a_clock_that_reads_earlier_than_a_recorded_run_is_refused_before_any_request(store: CandleStore) -> None:
    env = Env(store)
    scope = small_scope(symbols=("BTC/USD",))
    env.clock.advance(600)  # well after the pair list was observed, so a step back of 153 s stays after it
    first = env.collect(scope, label="recorded_public_fixture")
    recorded = datetime.fromisoformat(first["finished_at_utc"])
    stored = row_count(store)
    made = len(env.exchange.calls)
    env.clock.advance(-153)  # the host clock was corrected backwards by the time service
    report = env.collect(scope, label="recorded_public_fixture")
    assert report["status"] == "failed" and report["error"] == "clock_regression"
    assert report["latest_recorded_utc"] == first["finished_at_utc"] and report["requests"]["made"] == 0
    assert len(env.exchange.calls) == made and row_count(store) == stored
    assert report["not_reached"] == [] and report["series"] == []
    assert store.runs()[-1].status == "failed" and store.runs()[-1].started_at_utc < recorded
    # Another source's runs are not this source's clock history: a synthetic run is not held back by it.
    assert env.collect(scope, label="synthetic_fixture")["status"] == "ok"
    env.clock.advance(153 + 1)
    again = env.collect(scope, label="recorded_public_fixture")
    assert again["status"] == "ok" and again["error"] is None
    assert entry(again, "BTC/USD", 60)["continuity"]["status"] == "connected"


def test_a_clock_reading_equal_to_the_last_recorded_time_is_allowed(store: CandleStore) -> None:
    env = Env(store)
    scope = small_scope(symbols=("BTC/USD",))
    first = env.collect(scope, label="recorded_public_fixture")
    second = env.collect(scope, label="recorded_public_fixture")  # the scripted clock did not move in between
    assert second["started_at_utc"] == first["finished_at_utc"] and second["status"] == "ok"


def test_a_naive_clock_is_refused_before_anything_is_recorded(store: CandleStore) -> None:
    env = Env(store)

    class Naive:
        def now(self) -> datetime:
            return datetime(2026, 10, 13, 15, 30)

    with pytest.raises(ValidationFailure):
        collect_candles(store, Naive(), env.exchange, env.limiter, CollectionScope(), 10, "synthetic_fixture")
    assert [run.kind for run in store.runs()] == ["catalog"]


# --- recorded responses end to end ----------------------------------------------------------------------------------


class RecordedKraken:
    """Replays the recorded responses where they exist and falls back to the scripted exchange elsewhere."""

    def __init__(self, fallback: FakeKraken) -> None:
        self.fallback = fallback
        self.recorded = {(entry_["pair"], entry_["interval_minutes"]): name for name, entry_ in manifest().items()
                         if entry_["stored_as"] and entry_["since"] is None and entry_["pair"] != "ACAUSD"}
        self.served: list[tuple[str, int]] = []

    def get_text(self, url: str) -> str:
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(url).query)
        key = (query["pair"][0], int(query["interval"][0]))
        name = self.recorded.get(key) if "since" not in query else None
        if name is None:
            return self.fallback.get_text(url)
        self.served.append(key)
        self.fallback.clock.advance(self.fallback.delay_seconds)
        return fixture_text(name)


def test_recorded_public_responses_for_many_pairs_and_all_four_intervals_are_collected(store: CandleStore) -> None:
    # The recorded files were made between 15:17 and 15:23 on the host clock; collect just after the last of them.
    env = Env(store, start=datetime(2026, 10, 13, 15, 24, tzinfo=UTC))
    recorded = RecordedKraken(env.exchange)
    symbols = ("BTC/USD", "SOL/USD", "LINK/USD", "USDUC/USD", "PAXG/USD")
    scope = CollectionScope(symbols=symbols, one_minute_symbols=symbols)
    report = env.collect(scope, label="recorded_public_fixture", transport=recorded)
    assert report["status"] == "ok" and report["evidence_label"] == "recorded_public_fixture"
    # 13 of the 20 series come from stored recordings; the other 7 were never recorded and are answered by the scripted
    # exchange (LINK and USDUC at 5 and 15 minutes, PAXG at 5, 15 and 60 minutes).
    assert len(recorded.served) == 13 and report["counts"]["planned_series"] == 20
    assert len(env.exchange.requests) == 7
    assert {interval for _pair, interval in recorded.served} == {1, 5, 15, 60}
    assert {pair for pair, _interval in recorded.served} == {"XBTUSD", "SOLUSD", "LINKUSD", "USDUCUSD", "PAXGUSD"}
    assert sources(store) == {"kraken_public_ohlc"}
    btc = entry(report, "BTC/USD", 15)
    assert btc["candles_returned"] == 120 and btc["inserted"] == 120 and btc["no_trade_candles"] == 0
    window = store.window("BTC/USD", 15, "kraken_public_ohlc", end_close_time=from_unix(1791904500), count=1,
                          as_of=env.clock.now())
    assert str(window.slots[0].stored.candle.close) == "83568.1"
    usduc = entry(report, "USDUC/USD", 1)
    assert usduc["candles_returned"] == 300 and usduc["no_trade_candles"] >= 200
    assert usduc["coverage"]["missing_slots"] == 0
