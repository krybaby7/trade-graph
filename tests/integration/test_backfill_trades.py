"""The public Trades backfill, driven end to end by a scripted exchange. No network, no real sleeping."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

import pytest
from tests.trades_support import (
    FakeSleeper,
    SyntheticTrade,
    TradeScript,
    steady_trades,
    unix,
    wire_page,
    wire_row,
)

from trade_graph.adapters.market.kraken_trades import KrakenTrades
from trade_graph.adapters.market.trades_rate_limit import LimiterSettings, RequestLimiter
from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.application.backfill_trades import (
    BACKFILL_VERSION,
    STOP_REASONS,
    TRADES_SOURCE,
    BackfillScope,
    backfill_trades,
    read_resume_points,
)
from trade_graph.application.candle_store import CandleStore
from trade_graph.contracts.candles import Candle, from_unix
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import ValidationFailure

NOW = datetime(2026, 10, 13, 20, 0, tzinfo=UTC)
T0 = 1_791_892_800  # 2026-10-13T12:00:00Z: the archive ends here
COLLECTOR_START = T0 + 7200  # the collector's first 15 minute candle opens at 14:00
ARCHIVE = "kraken_ohlcvt_archive"
COLLECTOR = "kraken_public_ohlc"


def at(offset: str) -> str:
    """A trade time ``offset`` seconds after 12:00:00Z, written with Kraken's seven fraction digits."""
    return f"{Decimal(T0) + Decimal(offset):.7f}"


def trade(trade_id: int, offset: str, price: str, volume: str) -> SyntheticTrade:
    return SyntheticTrade(trade_id, price, volume, at(offset))


@pytest.fixture
def store(tmp_path):
    database = MarketDataDatabase(tmp_path / "market" / "market_data.sqlite")
    yield CandleStore(database)
    database.close()


def seed(store: CandleStore, *, symbol: str = "BTC/USD", pair: str = "XBTUSD", interval: int = 15,
         archive_last_close: int | None = T0, collector_first_open: int | None = COLLECTOR_START) -> None:
    step = interval * 60

    def candle(open_unix: int, source: str) -> Candle:
        return Candle(
            symbol=symbol, source_pair=pair, interval_minutes=interval, open_time_utc=from_unix(open_unix),
            close_time_utc=from_unix(open_unix + step), available_at_utc=NOW, open=Decimal("100"),
            high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"), vwap=None, volume=Decimal("5"),
            trade_count=3, source=source)

    if archive_last_close is not None:
        run = store.begin_run(kind="archive_import", source=ARCHIVE, evidence_label="local_archive_file",
                              started_at=NOW, parameters={})
        store.save([candle(archive_last_close - 2 * step, ARCHIVE), candle(archive_last_close - step, ARCHIVE)],
                   run_id=run)
    if collector_first_open is not None:
        run = store.begin_run(kind="public_collect", source=COLLECTOR, evidence_label="synthetic_fixture",
                              started_at=NOW, parameters={})
        store.save([candle(collector_first_open, COLLECTOR), candle(collector_first_open + step, COLLECTOR)],
                   run_id=run)


class Rig:
    """One clock, one scripted exchange, one limiter; ``drive`` runs a backfill and returns its result."""

    def __init__(self, store: CandleStore, trades: list[SyntheticTrade], *, delay: float = 2, hook=None,
                 now: datetime = NOW, key: str = "XXBTZUSD") -> None:
        self.store = store
        self.clock = FrozenClock(now)
        self.script = TradeScript(trades, key=key, clock=self.clock, delay_seconds=delay, hook=hook)
        self.sleeper = FakeSleeper(self.clock)

    def drive(self, *, symbols=("BTC/USD",), intervals=(15,), max_requests=50, start=None, end=None,
              page_size=1000, label="synthetic_fixture", ignore_cursor=False, settings=None):
        limiter = RequestLimiter(self.clock, self.sleeper, settings or LimiterSettings())
        scope = BackfillScope(symbols=tuple(symbols), intervals=tuple(intervals), start=start, end=end)
        return backfill_trades(self.store, self.clock, KrakenTrades(self.script, self.clock), limiter, scope,
                               max_requests=max_requests, evidence_label=label, page_size=page_size,
                               ignore_cursor=ignore_cursor)


def stored(store: CandleStore, interval: int = 15, symbol: str = "BTC/USD", *, first: int = T0,
           last: int = COLLECTOR_START) -> dict[int, Candle]:
    step = interval * 60
    window = store.window(symbol, interval, TRADES_SOURCE, end_close_time=from_unix(last), count=(last - first) // step,
                          mode="replay")
    return {candle.open_unix: candle for candle in window.candles}


def two_slot_trades() -> list[SyntheticTrade]:
    return [
        trade(1, "-0.5", "90.00", "1"),         # half a second before the range: returned, then ignored
        trade(2, "10.5", "100.00", "2"),
        trade(3, "300.25", "102.50", "1"),
        trade(4, "899.9999999", "99.00", "3"),
        trade(5, "900", "98.00", "1"),          # exactly on the close: belongs to the next candle
        trade(6, "1200", "101.00", "3"),
        trade(7, "1800", "105.00", "1"),        # at the range end: proves the second candle is complete
        trade(8, "1860", "106.00", "1"),        # after the range: ignored
    ]


def two_slot_store(store: CandleStore) -> None:
    seed(store, collector_first_open=T0 + 1800)


# --- the target range ----------------------------------------------------------------------------------------------


def test_the_gap_runs_from_the_archive_end_to_the_collector_start_with_hand_computed_candles(store) -> None:
    two_slot_store(store)
    result = Rig(store, two_slot_trades()).drive()
    assert result.status == "ok"
    candles = stored(store, last=T0 + 1800)
    assert sorted(candles) == [T0, T0 + 900]
    first, second = candles[T0], candles[T0 + 900]
    assert (first.open, first.high, first.low, first.close) == (
        Decimal("100.00"), Decimal("102.50"), Decimal("99.00"), Decimal("99.00"))
    assert (first.volume, first.trade_count, first.vwap) == (Decimal(6), 3, Decimal("99.916666666667"))
    assert (second.open, second.high, second.low, second.close) == (
        Decimal("98.00"), Decimal("101.00"), Decimal("98.00"), Decimal("101.00"))
    assert (second.volume, second.trade_count, second.vwap) == (Decimal(4), 2, Decimal("100.25"))
    assert all(c.source == TRADES_SOURCE and c.source_pair == "XBTUSD" and c.symbol == "BTC/USD"
               for c in candles.values())
    pair = result.report["pairs"]["BTC/USD"]
    assert (pair["ignored_before_range"], pair["ignored_after_end"], pair["stop_reason"]) == (1, 1, "end_reached")
    document = pair["intervals"]["15"]
    assert document["range_start_unix"] == T0 and document["range_end_unix"] == T0 + 1800
    assert document["range_source"] == {"start": "archive_end", "end": "collector_start"}
    assert document["target_slots"] == 2 and document["status"] == "complete" and document["unreached_slots"] == 0


def test_the_first_request_asks_from_one_second_before_the_range_start(store) -> None:
    two_slot_store(store)
    rig = Rig(store, two_slot_trades())
    rig.drive()
    assert rig.script.calls[0].endswith(f"/Trades?pair=XBTUSD&since={T0 - 1}&count=1000")


def test_a_candle_is_never_built_before_the_stream_has_passed_its_close(store) -> None:
    two_slot_store(store)
    short = two_slot_trades()[:-2]  # the exchange has nothing at or after the range end yet
    result = Rig(store, short).drive()
    candles = stored(store, last=T0 + 1800)
    assert sorted(candles) == [T0]  # the second candle may still gain trades, so it is not stored
    pair = result.report["pairs"]["BTC/USD"]
    assert pair["stop_reason"] == "stream_exhausted" and result.status == "partial"
    document = pair["intervals"]["15"]
    assert document["status"] == "partial" and document["unreached_slots"] == 1
    assert document["next_open_unix"] == T0 + 900 and document["unreached_from_utc"] == "2026-10-13T12:15:00.000000Z"


def test_the_archive_end_may_be_replaced_by_a_chosen_start_when_there_is_no_archive(store) -> None:
    seed(store, archive_last_close=None, collector_first_open=T0 + 1800)
    result = Rig(store, two_slot_trades()).drive(start=from_unix(T0))
    assert sorted(stored(store, last=T0 + 1800)) == [T0, T0 + 900]
    assert result.report["pairs"]["BTC/USD"]["intervals"]["15"]["range_source"] == {
        "start": "argument", "end": "collector_start"}


def test_without_an_archive_and_without_a_chosen_start_nothing_is_guessed(store) -> None:
    seed(store, archive_last_close=None)
    rig = Rig(store, two_slot_trades())
    result = rig.drive()
    document = result.report["pairs"]["BTC/USD"]["intervals"]["15"]
    assert document["status"] == "no_target" and "archive" in document["reason"] and "--start" in document["reason"]
    assert rig.script.calls == [] and result.status == "failed"


def test_without_a_collector_series_and_without_a_chosen_end_nothing_is_guessed(store) -> None:
    seed(store, collector_first_open=None)
    rig = Rig(store, two_slot_trades())
    result = rig.drive()
    document = result.report["pairs"]["BTC/USD"]["intervals"]["15"]
    assert document["status"] == "no_target" and "collector" in document["reason"] and "--end" in document["reason"]
    assert rig.script.calls == []
    chosen = Rig(store, two_slot_trades()).drive(end=from_unix(T0 + 1800))
    assert chosen.report["pairs"]["BTC/USD"]["intervals"]["15"]["range_source"]["end"] == "argument"
    assert sorted(stored(store, last=T0 + 1800)) == [T0, T0 + 900]


def test_overlapping_archive_and_collector_leave_no_gap_and_no_requests(store) -> None:
    seed(store, archive_last_close=T0 + 3600, collector_first_open=T0 + 1800)
    rig = Rig(store, two_slot_trades())
    result = rig.drive()
    assert rig.script.calls == [] and result.status == "ok"
    document = result.report["pairs"]["BTC/USD"]["intervals"]["15"]
    assert document["status"] == "already_covered" and "overlap" in document["reason"]


def test_a_chosen_start_and_end_are_aligned_inward_and_reported(store) -> None:
    seed(store, archive_last_close=None, collector_first_open=None)
    result = Rig(store, two_slot_trades()).drive(start=from_unix(T0 - 100), end=from_unix(T0 + 1801))
    document = result.report["pairs"]["BTC/USD"]["intervals"]["15"]
    assert (document["range_start_unix"], document["range_end_unix"]) == (T0, T0 + 1800)


# --- paging, boundary duplicates, receipt time ---------------------------------------------------------------------


def grouped_trades(groups: int = 70, group_size: int = 3, every: int = 100) -> list[SyntheticTrade]:
    """Trades in groups that share one timestamp, as one market order matching several resting orders does."""
    trades = []
    for group in range(groups):
        for member in range(group_size):
            number = group * group_size + member
            cents = 10_000 + (number * 53) % 197 - 90
            trades.append(trade(1000 + number, f"{-300 + group * every}.2500000", f"{cents // 100}.{cents % 100:02d}",
                                f"{number % 5 + 1}.{number % 3}0"))
    return trades


def naive_candles(trades: list[SyntheticTrade], interval: int, start: int, end: int) -> dict[int, tuple]:
    step = interval * 60
    slots: dict[int, list[SyntheticTrade]] = {}
    for item in trades:
        whole = int(Decimal(item.time))
        slot = whole - whole % step
        if start <= slot < end:
            slots.setdefault(slot, []).append(item)
    latest = max(Decimal(item.time) for item in trades)
    expected = {}
    for slot, members in slots.items():
        if latest < slot + step:
            continue
        prices = [Decimal(m.price) for m in members]
        volume = sum((Decimal(m.volume) for m in members), Decimal(0))
        notional = sum((Decimal(m.price) * Decimal(m.volume) for m in members), Decimal(0))
        vwap = (notional / volume).quantize(Decimal("1e-12"), rounding=ROUND_HALF_EVEN)
        expected[slot] = (prices[0], max(prices), min(prices), prices[-1], volume, len(members), vwap)
    return expected


def as_tuple(candle: Candle) -> tuple:
    return (candle.open, candle.high, candle.low, candle.close, candle.volume, candle.trade_count, candle.vwap)


@pytest.mark.parametrize("page_size", [1000, 40, 7])
def test_page_boundaries_inside_a_timestamp_group_change_nothing(store, page_size) -> None:
    """Kraken's cursor is inclusive, so a page that ends inside a group is followed by a page that repeats it."""
    seed(store, collector_first_open=T0 + 3600)
    trades = grouped_trades()
    rig = Rig(store, trades)
    result = rig.drive(page_size=page_size)
    assert result.status == "ok"
    expected = naive_candles(trades, 15, T0, T0 + 3600)
    assert len(expected) == 4
    found = stored(store, last=T0 + 3600)
    assert {slot: as_tuple(candle) for slot, candle in found.items()} == expected
    pair = result.report["pairs"]["BTC/USD"]
    assert pair["id_gaps"] == 0 and pair["unexpected_overlap"] == 0
    assert pair["trades_received"] == pair["trades_used"] + pair["duplicate_trades"]
    if page_size < 1000:
        assert pair["duplicate_trades"] > 0 and pair["pages"] > 1
    else:
        assert pair["duplicate_trades"] == 0 and pair["pages"] == 1


def test_pages_follow_the_cursor_of_the_page_before(store) -> None:
    seed(store, collector_first_open=T0 + 3600)
    rig = Rig(store, grouped_trades())
    rig.drive(page_size=40)
    queries = [call.split("?")[1] for call in rig.script.calls]
    assert queries[0] == f"pair=XBTUSD&since={T0 - 1}&count=40"
    cursors = [query.split("since=")[1].split("&")[0] for query in queries]
    assert len(set(cursors)) == len(cursors) and all(len(cursor) == 19 for cursor in cursors[1:])
    assert all(query.endswith("&count=40") for query in queries)


def test_a_candle_is_available_when_the_page_that_completed_it_was_received(store) -> None:
    two_slot_store(store)
    rig = Rig(store, two_slot_trades(), delay=10)
    rig.drive(page_size=5)
    candles = stored(store, last=T0 + 1800)
    # Page 1 holds trade ids 1 to 5; trade 5, at exactly 12:15:00, proves the first candle complete. Page 2 repeats
    # trade 5, then 6, 7 and 8; trade 7, at 12:30:00, proves the second candle complete. Each response takes ten
    # seconds, which already exceeds the limiter spacing, so the limiter adds no wait here.
    assert candles[T0].available_at_utc == NOW + timedelta(seconds=10)
    assert candles[T0 + 900].available_at_utc == NOW + timedelta(seconds=20)
    for candle in candles.values():
        assert candle.available_at_utc >= candle.close_time_utc


def test_availability_follows_the_completing_page_not_the_last_page(store) -> None:
    seed(store, collector_first_open=T0 + 3600)
    trades = [trade(1, "10", "10.00", "1"), trade(2, "950", "11.00", "1"), trade(3, "1850", "12.00", "1"),
              trade(4, "2750", "13.00", "1"), trade(5, "3650", "14.00", "1")]
    rig = Rig(store, trades, delay=10)
    rig.drive(page_size=2)
    candles = stored(store, last=T0 + 3600)
    # page 1: trades 1, 2 (completes slot 0 at trade 2). page 2: trades 2, 3 (completes slot 1). page 3: 3, 4 (slot 2).
    # page 4: trades 4, 5 (completes slot 3 and ends the range). Each page is received 10 seconds after the last.
    received = [candles[T0 + 900 * index].available_at_utc for index in range(4)]
    assert received == [NOW + timedelta(seconds=10 * (index + 1)) for index in range(4)]


def test_a_candle_whose_close_is_after_the_receipt_is_withheld_not_backdated(store) -> None:
    two_slot_store(store)
    early = datetime.fromtimestamp(T0 + 1200, UTC)  # the clock says 12:20, so the 12:15 to 12:30 candle is not complete
    result = Rig(store, two_slot_trades(), now=early).drive()
    assert sorted(stored(store, last=T0 + 1800)) == [T0]
    document = result.report["pairs"]["BTC/USD"]["intervals"]["15"]
    assert document["candles"]["withheld_receipt_before_close"] == 1 and result.status == "partial"


# --- budget, cursor and resume -------------------------------------------------------------------------------------


def long_gap(store: CandleStore, hours: int = 6) -> list[SyntheticTrade]:
    seed(store, collector_first_open=T0 + hours * 3600)
    trades = steady_trades(1000, T0 - 600, hours * 60 + 30, every_seconds=60)
    return trades


def test_max_requests_bounds_the_run_and_the_unreached_part_is_reported(store) -> None:
    trades = long_gap(store)
    rig = Rig(store, trades)
    result = rig.drive(max_requests=2, page_size=25)
    assert len(rig.script.calls) == 2
    pair = result.report["pairs"]["BTC/USD"]
    assert pair["stop_reason"] == "max_requests" and result.report["requests"]["made"] == 2
    document = pair["intervals"]["15"]
    assert document["status"] == "partial" and document["unreached_slots"] > 0
    assert document["decided_slots"] + document["unreached_slots"] == document["target_slots"] == 24
    assert result.status == "partial"
    assert result.report["not_reached"][0]["symbol"] == "BTC/USD"
    assert result.report["not_reached"][0]["unreached_slots"] == document["unreached_slots"]


def test_the_next_run_resumes_from_the_cursor_in_the_last_run_report(store) -> None:
    trades = long_gap(store)
    expected = naive_candles(trades, 15, T0, T0 + 6 * 3600)
    first = Rig(store, trades)
    one = first.drive(max_requests=3, page_size=25)
    cursor = one.report["pairs"]["BTC/USD"]["intervals"]["15"]["next_open_unix"]
    assert cursor > T0
    assert read_resume_points(store, "BTC/USD", "XBTUSD", 15, range_start=T0) == cursor
    stored_run = [run for run in store.runs() if run.kind == "trades_backfill"][0]
    assert stored_run.report["pairs"]["BTC/USD"]["intervals"]["15"]["next_open_unix"] == cursor
    assert stored_run.evidence_label == "synthetic_fixture" and stored_run.source == TRADES_SOURCE

    second = Rig(store, trades)
    result = second.drive(max_requests=50, page_size=25)
    assert result.status == "ok"
    assert second.script.calls[0].endswith(f"since={cursor - 1}&count=25")
    assert result.report["pairs"]["BTC/USD"]["intervals"]["15"]["resumed_from_unix"] == cursor
    found = stored(store, last=T0 + 6 * 3600)
    assert {slot: as_tuple(candle) for slot, candle in found.items()} == expected


def test_resuming_matches_a_single_uninterrupted_run_candle_for_candle(store, tmp_path) -> None:
    trades = long_gap(store)
    Rig(store, trades).drive(max_requests=2, page_size=17)
    Rig(store, trades).drive(max_requests=3, page_size=17)
    Rig(store, trades).drive(max_requests=100, page_size=17)
    other_db = MarketDataDatabase(tmp_path / "other" / "market_data.sqlite")
    other = CandleStore(other_db)
    seed(other, collector_first_open=T0 + 6 * 3600)
    Rig(other, trades).drive(max_requests=100, page_size=1000)
    split = {slot: candle.content_hash for slot, candle in stored(store, last=T0 + 6 * 3600).items()}
    whole = {slot: candle.content_hash for slot, candle in stored(other, last=T0 + 6 * 3600).items()}
    other_db.close()
    assert split == whole and len(whole) == 24


def test_a_finished_range_costs_no_requests_on_the_next_run(store) -> None:
    trades = long_gap(store, hours=2)
    Rig(store, trades).drive()
    rig = Rig(store, trades)
    result = rig.drive()
    assert rig.script.calls == [] and result.status == "ok"
    assert result.report["pairs"]["BTC/USD"]["intervals"]["15"]["status"] == "complete"


def test_the_resume_point_rewinds_to_the_oldest_open_bucket_of_every_interval(store) -> None:
    seed(store, collector_first_open=T0 + 6 * 3600)
    seed(store, interval=60, archive_last_close=T0, collector_first_open=T0 + 6 * 3600)
    trades = steady_trades(1000, T0 - 600, 6 * 60 + 30, every_seconds=60)
    first = Rig(store, trades)
    one = first.drive(intervals=(15, 60), max_requests=2, page_size=40)
    pair = one.report["pairs"]["BTC/USD"]
    next_15, next_60 = (pair["intervals"][key]["next_open_unix"] for key in ("15", "60"))
    assert next_60 <= next_15 and (next_60 - T0) % 3600 == 0
    second = Rig(store, trades)
    result = second.drive(intervals=(15, 60), max_requests=100, page_size=40)
    assert second.script.calls[0].endswith(f"since={min(next_15, next_60) - 1}&count=40")
    assert result.status == "ok"
    assert len(stored(store, 60, last=T0 + 6 * 3600)) == 6 and len(stored(store, 15, last=T0 + 6 * 3600)) == 24
    # the rewound hour is fetched again, but candles already decided for the quarter-hour series are not rebuilt
    quarter = result.report["pairs"]["BTC/USD"]["intervals"]["15"]["candles"]
    assert quarter["duplicates"] == 0


def test_a_cursor_from_a_run_with_a_later_start_is_not_trusted_for_an_earlier_start(store) -> None:
    trades = long_gap(store, hours=4)
    Rig(store, trades).drive(start=from_unix(T0 + 3600), max_requests=100)
    assert read_resume_points(store, "BTC/USD", "XBTUSD", 15, range_start=T0 + 3600) == T0 + 4 * 3600
    assert read_resume_points(store, "BTC/USD", "XBTUSD", 15, range_start=T0) is None
    rig = Rig(store, trades)
    rig.drive(max_requests=100)
    assert rig.script.calls and rig.script.calls[0].endswith(f"since={T0 - 1}&count=1000")


def test_the_cursor_can_be_ignored_on_request(store) -> None:
    trades = long_gap(store, hours=2)
    Rig(store, trades).drive()
    rig = Rig(store, trades)
    result = rig.drive(ignore_cursor=True)
    assert rig.script.calls and result.report["pairs"]["BTC/USD"]["intervals"]["15"]["resumed_from_unix"] is None
    assert result.report["pairs"]["BTC/USD"]["intervals"]["15"]["candles"]["duplicates"] == 8


def test_a_cursor_for_another_venue_pair_code_is_ignored(store) -> None:
    trades = long_gap(store, hours=2)
    Rig(store, trades).drive()
    assert read_resume_points(store, "BTC/USD", "XXBTZUSD", 15, range_start=T0) is None


def test_a_run_that_never_finished_leaves_no_cursor_and_the_next_run_starts_from_the_range_start(store) -> None:
    trades = long_gap(store, hours=2)
    store.begin_run(kind="trades_backfill", source=TRADES_SOURCE, evidence_label="synthetic_fixture",
                    started_at=NOW, parameters={})
    assert read_resume_points(store, "BTC/USD", "XBTUSD", 15, range_start=T0) is None
    assert Rig(store, trades).drive().status == "ok"


# --- rate limits and failures --------------------------------------------------------------------------------------


def rate_limit_once(call: int, url: str) -> str | None:
    return '{"error":["EAPI:Rate limit exceeded"],"result":{}}' if call == 2 else None


def test_a_rate_limit_answer_is_waited_out_and_the_same_page_is_asked_again(store) -> None:
    trades = long_gap(store, hours=2)
    rig = Rig(store, trades, hook=rate_limit_once)
    result = rig.drive(page_size=25)
    assert result.status == "ok"
    assert Decimal("9.091") in rig.sleeper.waits
    assert rig.script.calls[1] == rig.script.calls[2]  # the refused page was requested again
    requests = result.report["requests"]
    assert requests["rate_limited_responses"] == 1 and requests["made"] == len(rig.script.calls)
    assert Decimal(requests["waited_seconds"]) >= Decimal("9.091")


def test_persistent_rate_limiting_stops_the_run_with_progress_saved(store) -> None:
    trades = long_gap(store, hours=2)
    rig = Rig(store, trades, hook=lambda call, url: '{"error":["EAPI:Rate limit exceeded"],"result":{}}'
              if call >= 3 else None)
    result = rig.drive(page_size=25)
    pair = result.report["pairs"]["BTC/USD"]
    assert pair["stop_reason"] == "rate_limited" and result.status == "partial"
    assert len(rig.script.calls) == 2 + 3  # two good pages, then the first ask and two retries
    assert pair["intervals"]["15"]["next_open_unix"] > T0
    assert result.report["requests"]["rate_limited_responses"] == 3


def test_a_throttle_timestamp_is_honoured(store) -> None:
    trades = long_gap(store, hours=2)
    retry_at = unix(NOW) + 30

    def hook(call: int, url: str):
        return f'{{"error":["EService: Throttled: {retry_at}"],"result":{{}}}}' if call == 2 else None

    rig = Rig(store, trades, hook=hook, delay=0)
    result = rig.drive(page_size=25)
    assert result.status == "ok" and result.report["requests"]["throttled_responses"] == 1
    assert max(rig.sleeper.waits) >= 20


def test_a_throttle_far_in_the_future_stops_the_run_without_sleeping_for_it(store) -> None:
    trades = long_gap(store, hours=2)
    far = unix(NOW) + 100_000
    rig = Rig(store, trades, hook=lambda call, url: f'{{"error":["EService: Throttled: {far}"],"result":{{}}}}'
              if call == 2 else None)
    result = rig.drive(page_size=25)
    assert result.report["pairs"]["BTC/USD"]["stop_reason"] == "rate_limit_wait_too_long"
    assert max(rig.sleeper.waits, default=Decimal(0)) < 100


def test_a_transient_transport_failure_is_retried_after_a_back_off(store) -> None:
    trades = long_gap(store, hours=2)
    rig = Rig(store, trades, hook=lambda call, url: TimeoutError("read timed out") if call == 3 else None)
    result = rig.drive(page_size=25)
    assert result.status == "ok" and result.report["requests"]["transport_errors"] == 1
    assert Decimal("9.091") in rig.sleeper.waits and rig.script.calls[2] == rig.script.calls[3]


def test_a_persistent_transport_failure_stops_the_pair_and_keeps_what_was_saved(store) -> None:
    trades = long_gap(store, hours=2)
    rig = Rig(store, trades, hook=lambda call, url: TimeoutError("read timed out") if call >= 3 else None)
    result = rig.drive(page_size=25)
    pair = result.report["pairs"]["BTC/USD"]
    assert pair["stop_reason"] == "transport_error" and "TimeoutError" in pair["message"]
    assert len(rig.script.calls) == 2 + 3 and result.report["requests"]["transport_errors"] == 3
    assert stored(store, last=T0 + 7200) and result.status == "partial"
    assert pair["intervals"]["15"]["next_open_unix"] > T0


def test_an_unusable_page_stops_the_pair_with_the_refusal_reason(store) -> None:
    trades = long_gap(store, hours=2)
    rig = Rig(store, trades, hook=lambda call, url: "not json" if call == 2 else None)
    result = rig.drive(page_size=25)
    pair = result.report["pairs"]["BTC/USD"]
    assert pair["stop_reason"] == "page_refused" and pair["refusal_reason"] == "invalid_json"


def test_a_page_for_the_wrong_pair_is_refused(store) -> None:
    trades = long_gap(store, hours=2)
    result = Rig(store, trades, key="XETHZUSD").drive()
    pair = result.report["pairs"]["BTC/USD"]
    assert pair["stop_reason"] == "page_refused" and pair["refusal_reason"] == "pair_mismatch"
    assert stored(store, last=T0 + 7200) == {}


def test_an_interrupt_still_records_the_run_and_the_cursor_then_propagates(store) -> None:
    trades = long_gap(store, hours=2)
    rig = Rig(store, trades, hook=lambda call, url: KeyboardInterrupt() if call == 3 else None)
    with pytest.raises(KeyboardInterrupt):
        rig.drive(page_size=25)
    run = [run for run in store.runs() if run.kind == "trades_backfill"][0]
    assert run.status == "failed" and run.report["reason"] == "interrupted"
    assert run.report["pairs"]["BTC/USD"]["stop_reason"] == "interrupted"
    assert read_resume_points(store, "BTC/USD", "XBTUSD", 15, range_start=T0) > T0


def test_an_id_gap_withholds_the_candles_it_could_have_changed(store) -> None:
    seed(store, collector_first_open=T0 + 3600)
    trades = [trade(1, "10", "10.00", "1"), trade(2, "20", "11.00", "1"),
              trade(5, "1000", "12.00", "1"),                       # ids 3 and 4 are missing, between 20 s and 1000 s
              trade(6, "1100", "13.00", "1"), trade(7, "1900", "14.00", "1"), trade(8, "2800", "15.00", "1"),
              trade(9, "3700", "16.00", "1")]
    result = Rig(store, trades).drive()
    candles = stored(store, last=T0 + 3600)
    # the first two candles may lack trades 3 and 4 and are withheld; the other two are clean
    assert sorted(candles) == [T0 + 1800, T0 + 2700]
    pair = result.report["pairs"]["BTC/USD"]
    assert pair["id_gaps"] == 1 and pair["id_gap_examples"][0]["missing_trades"] == 2
    assert pair["intervals"]["15"]["candles"]["withheld_trade_id_gap"] == 2 and result.status == "partial"
    assert pair["intervals"]["15"]["status"] == "complete"


def test_a_repeated_boundary_trade_that_changed_content_is_refused(store) -> None:
    two_slot_store(store)
    trades = two_slot_trades()

    def hook(call: int, url: str) -> str | None:
        if call == 2:  # the repeated boundary trade (id 5) differs from what page 1 delivered for the same id
            rows = [wire_row(5, "98.50", "1", at("900")), wire_row(6, "101.00", "3", at("1200"))]
            return wire_page(rows, last=int(Decimal(at("1200")) * 10**9))
        return None

    result = Rig(store, trades, hook=hook).drive(page_size=5)
    pair = result.report["pairs"]["BTC/USD"]
    assert pair["stop_reason"] == "boundary_mismatch" and result.status == "partial"
    assert sorted(stored(store, last=T0 + 1800)) == [T0]  # page 1 completed the first candle; page 2 applied nothing
    assert pair["intervals"]["15"]["next_open_unix"] == T0 + 900


def test_a_full_page_of_only_repeats_stalls_instead_of_looping(store) -> None:
    two_slot_store(store)
    same_time = [trade(i, "10", "10.00", "1") for i in range(1, 8)]  # more trades at one instant than a page holds
    rig = Rig(store, same_time)
    result = rig.drive(page_size=4)
    pair = result.report["pairs"]["BTC/USD"]
    assert pair["stop_reason"] == "cursor_stalled" and len(rig.script.calls) == 2
    assert pair["intervals"]["15"]["status"] == "partial" and result.status == "partial"


def test_time_going_backwards_between_pages_is_refused(store) -> None:
    two_slot_store(store)

    def hook(call: int, url: str) -> str | None:
        if call == 2:  # a new trade id, but earlier than the trades already used
            return wire_page([wire_row(6, "98.00", "1", at("100"))], last=int(Decimal(at("100")) * 10**9))
        return None

    result = Rig(store, two_slot_trades(), hook=hook).drive(page_size=5)
    pair = result.report["pairs"]["BTC/USD"]
    assert pair["stop_reason"] == "time_regressed" and result.status == "partial"


# --- several intervals, several pairs ------------------------------------------------------------------------------


def test_one_stream_builds_every_requested_interval(store) -> None:
    for interval in (5, 15, 60):
        seed(store, interval=interval, collector_first_open=T0 + 7200)
    trades = steady_trades(1000, T0 - 600, 2 * 60 + 30, every_seconds=60)
    result = Rig(store, trades).drive(intervals=(5, 15, 60), page_size=40)
    assert result.status == "ok"
    for interval in (5, 15, 60):
        expected = naive_candles(trades, interval, T0, T0 + 7200)
        found = stored(store, interval, last=T0 + 7200)
        assert {slot: as_tuple(candle) for slot, candle in found.items()} == expected, interval
        assert len(found) == 7200 // (interval * 60)
    pair = result.report["pairs"]["BTC/USD"]
    assert set(pair["intervals"]) == {"5", "15", "60"}


def test_pairs_run_in_order_and_a_spent_budget_leaves_the_rest_reported_as_not_started(store) -> None:
    seed(store, collector_first_open=T0 + 3600)
    seed(store, symbol="ETH/USD", pair="ETHUSD", collector_first_open=T0 + 3600)
    trades = steady_trades(1000, T0 - 600, 2 * 60 + 30, every_seconds=60)
    rig = Rig(store, trades)
    result = rig.drive(symbols=("ETH/USD", "BTC/USD"), max_requests=1, page_size=10)
    pairs = result.report["pairs"]
    assert list(pairs) == ["BTC/USD", "ETH/USD"]  # sorted, deterministic
    assert pairs["BTC/USD"]["stop_reason"] == "max_requests"
    assert pairs["ETH/USD"]["status"] == "not_started" and pairs["ETH/USD"]["stop_reason"] == "max_requests"
    assert rig.script.calls and all("pair=XBTUSD" in call for call in rig.script.calls)
    names = {entry["symbol"] for entry in result.report["not_reached"]}
    assert names == {"BTC/USD", "ETH/USD"}


# --- record keeping and refusals -----------------------------------------------------------------------------------


def test_the_run_record_names_the_kind_source_label_parameters_and_version(store) -> None:
    two_slot_store(store)
    result = Rig(store, two_slot_trades()).drive(label="live_public_smoke", max_requests=3)
    run = [r for r in store.runs() if r.kind == "trades_backfill"][0]
    assert run.run_id == result.run_id and run.status == "ok" and run.evidence_label == "live_public_smoke"
    assert run.source == TRADES_SOURCE
    assert run.parameters["backfill_version"] == BACKFILL_VERSION and run.parameters["max_requests"] == 3
    assert run.parameters["scope"]["symbols"] == ["BTC/USD"] and run.parameters["scope"]["intervals"] == [15]
    report = run.report
    assert report["kind"] == "trades_backfill" and report["evidence_label"] == "live_public_smoke"
    assert report["requests"]["max"] == 3 and report["totals"]["candles_saved"] == 2
    assert report["pairs"]["BTC/USD"]["stop_reason"] in STOP_REASONS
    assert {note.split(":")[0] for note in report["notes"]} >= {"time_unit", "sequence_ids"}


def test_a_second_pass_over_the_same_data_adds_no_revisions(store) -> None:
    two_slot_store(store)
    Rig(store, two_slot_trades()).drive()
    again = Rig(store, two_slot_trades()).drive(ignore_cursor=True)
    totals = again.report["totals"]
    assert totals["candles_saved"] == 0 and totals["candles_duplicate"] == 2 and totals["candles_revised"] == 0


@pytest.mark.parametrize("kwargs", [
    {"symbols": ()}, {"symbols": ("BTC/EUR",)}, {"symbols": ("btc/usd",)}, {"symbols": ("USDT/USD",)},
    {"symbols": ("USDC/USD",)}, {"symbols": ("BTC/USD", "BTC/USD")}, {"intervals": ()}, {"intervals": (7,)},
    {"intervals": (15, 15)}, {"intervals": (240,)},
])
def test_a_bad_scope_is_refused_before_any_run_exists(store, kwargs) -> None:
    arguments = {"symbols": ("BTC/USD",), "intervals": (15,), **kwargs}
    with pytest.raises(ValidationFailure):
        BackfillScope(**arguments)
    assert store.runs() == []


@pytest.mark.parametrize("kwargs", [
    {"max_requests": 0}, {"max_requests": -1}, {"max_requests": True}, {"max_requests": 100_001},
    {"page_size": 1}, {"page_size": 1001}, {"label": "paid"},
])
def test_bad_budgets_page_sizes_and_labels_are_refused(store, kwargs) -> None:
    two_slot_store(store)
    with pytest.raises(ValidationFailure):
        Rig(store, two_slot_trades()).drive(**kwargs)
    assert [run for run in store.runs() if run.kind == "trades_backfill"] == []


def test_naive_start_and_end_must_be_aware(store) -> None:
    with pytest.raises(ValidationFailure):
        BackfillScope(symbols=("BTC/USD",), start=datetime(2026, 10, 13, 12, 0))
