"""Trades -> completed candles, with hand-computed expectations. No store, no network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from trade_graph.adapters.market.kraken_trades import PublicTrade
from trade_graph.application.trade_aggregation import CompletedBucket, TradeAggregator
from trade_graph.contracts.candles import Candle, from_unix
from trade_graph.domain.errors import ValidationFailure

T0 = 1_791_892_800  # 2026-10-13T12:00:00Z, on the 15 and 60 minute grids
_next_id = iter(range(1, 10**9))


def trade(offset: str, price: str, volume: str, *, trade_id: int | None = None) -> PublicTrade:
    when = Decimal(T0) + Decimal(offset)
    return PublicTrade(trade_id=next(_next_id) if trade_id is None else trade_id, price=Decimal(price),
                       volume=Decimal(volume), time=when, side="b", order_type="l", misc="")


def feed_all(aggregator: TradeAggregator, trades: list[PublicTrade]) -> list[CompletedBucket]:
    done: list[CompletedBucket] = []
    for item in trades:
        done.extend(aggregator.feed(item, gap_before=False))
    return done


def far_end(interval: int = 15) -> dict[int, int]:
    return {interval: T0 + 86_400}


def test_one_candle_has_ohlc_in_trade_order_volume_sum_vwap_and_count() -> None:
    aggregator = TradeAggregator({15: T0}, far_end())
    done = feed_all(aggregator, [
        trade("10", "100.00", "2"), trade("20", "102.50", "1"), trade("500", "99.00", "3"),
        trade("899.9999999", "101.00", "4"), trade("900", "105.00", "1"),
    ])
    assert len(done) == 1
    candle = done[0]
    assert (candle.interval, candle.open_unix) == (15, T0)
    assert (candle.open, candle.high, candle.low, candle.close) == (
        Decimal("100.00"), Decimal("102.50"), Decimal("99.00"), Decimal("101.00"))
    assert candle.volume == Decimal("10") and candle.trade_count == 4 and not candle.tainted
    assert candle.vwap == Decimal("100.35")  # (200 + 102.5 + 297 + 404) / 10


def test_a_trade_exactly_on_the_close_belongs_to_the_next_candle() -> None:
    aggregator = TradeAggregator({15: T0}, far_end())
    done = feed_all(aggregator, [trade("899.9999999", "10", "1"), trade("900", "20", "1"), trade("1800", "30", "1")])
    assert [(c.open_unix, c.close, c.trade_count) for c in done] == [
        (T0, Decimal("10"), 1), (T0 + 900, Decimal("20"), 1)]


def test_a_candle_is_emitted_only_after_a_later_trade_proves_the_close_has_passed() -> None:
    aggregator = TradeAggregator({15: T0}, far_end())
    assert feed_all(aggregator, [trade("10", "10", "1"), trade("899", "11", "1")]) == []
    assert aggregator.next_open(15) == T0  # nothing decided yet: the open candle may still gain trades
    assert len(feed_all(aggregator, [trade("901", "12", "1")])) == 1
    assert aggregator.next_open(15) == T0 + 900


def test_empty_slots_produce_no_candle_and_no_fill() -> None:
    aggregator = TradeAggregator({15: T0}, far_end())
    done = feed_all(aggregator, [trade("10", "10", "1"), trade("3000", "20", "1"), trade("6000", "30", "1")])
    assert [c.open_unix for c in done] == [T0, T0 + 2700]  # slots 900, 1800 and 3600, 4500 stay missing
    assert aggregator.next_open(15) == T0 + 5400


def test_several_intervals_are_built_from_one_stream() -> None:
    aggregator = TradeAggregator({5: T0, 15: T0, 60: T0}, {5: T0 + 7200, 15: T0 + 7200, 60: T0 + 7200})
    stream = [trade("10", "10", "1"), trade("310", "14", "2"), trade("620", "12", "1"), trade("1000", "9", "3"),
              trade("3700", "20", "1"), trade("7300", "21", "1")]
    done = feed_all(aggregator, stream)
    by_interval = {(c.interval, c.open_unix): c for c in done}
    assert set(by_interval) == {(5, T0), (5, T0 + 300), (5, T0 + 600), (5, T0 + 900), (5, T0 + 3600),
                                (15, T0), (15, T0 + 900), (15, T0 + 3600), (60, T0), (60, T0 + 3600)}
    hour = by_interval[(60, T0)]
    assert (hour.open, hour.high, hour.low, hour.close, hour.volume, hour.trade_count) == (
        Decimal("10"), Decimal("14"), Decimal("9"), Decimal("9"), Decimal("7"), 4)
    assert hour.vwap == Decimal(11)  # (10 + 28 + 12 + 27) / 7
    quarter = by_interval[(15, T0)]  # the trades at 10, 310 and 620 seconds; the one at 1000 s is in the next quarter
    assert (quarter.volume, quarter.trade_count) == (Decimal(4), 3)
    assert (quarter.open, quarter.close) == (Decimal(10), Decimal(12))
    assert by_interval[(15, T0 + 900)].trade_count == 1 and by_interval[(15, T0 + 900)].open == Decimal(9)
    assert by_interval[(5, T0 + 300)].close == Decimal("14") and by_interval[(5, T0 + 300)].volume == Decimal("2")


def test_trades_before_the_floor_are_ignored_for_that_interval_only() -> None:
    aggregator = TradeAggregator({15: T0 + 900, 60: T0}, {15: T0 + 86_400, 60: T0 + 86_400})
    done = feed_all(aggregator, [trade("10", "10", "1"), trade("1000", "11", "1"), trade("3700", "12", "1")])
    assert [(c.interval, c.open_unix, c.trade_count) for c in done] == [(15, T0 + 900, 1), (60, T0, 2)]


def test_the_end_closes_the_last_candle_and_later_trades_are_ignored() -> None:
    aggregator = TradeAggregator({15: T0}, {15: T0 + 1800})
    done = feed_all(aggregator, [trade("10", "10", "1"), trade("1000", "11", "1"), trade("1800", "12", "1")])
    assert [c.open_unix for c in done] == [T0, T0 + 900]
    assert aggregator.finished and aggregator.next_open(15) == T0 + 1800
    assert feed_all(aggregator, [trade("1900", "13", "1"), trade("9000", "14", "1")]) == []


def test_an_interval_whose_floor_is_at_or_past_its_end_is_finished_from_the_start() -> None:
    aggregator = TradeAggregator({15: T0 + 900}, {15: T0 + 900})
    assert aggregator.finished and feed_all(aggregator, [trade("1000", "10", "1")]) == []


def test_a_gap_taints_the_open_candle_and_the_one_the_next_trade_opens() -> None:
    aggregator = TradeAggregator({15: T0}, far_end())
    aggregator.feed(trade("10", "10", "1"), gap_before=False)
    aggregator.feed(trade("20", "11", "1"), gap_before=False)
    done = aggregator.feed(trade("1000", "12", "1"), gap_before=True)  # trades are missing between 20 s and 1000 s
    assert [c.tainted for c in done] == [True]
    done += aggregator.feed(trade("1100", "13", "1"), gap_before=False)
    done += aggregator.feed(trade("1900", "14", "1"), gap_before=False)
    assert [(c.open_unix, c.tainted) for c in done] == [(T0, True), (T0 + 900, True)]
    done = aggregator.feed(trade("2800", "15", "1"), gap_before=False)
    assert [(c.open_unix, c.tainted) for c in done] == [(T0 + 1800, False)]


def test_a_gap_inside_one_candle_taints_just_that_candle() -> None:
    aggregator = TradeAggregator({15: T0}, far_end())
    aggregator.feed(trade("10", "10", "1"), gap_before=False)
    aggregator.feed(trade("20", "11", "1"), gap_before=True)
    done = aggregator.feed(trade("1000", "12", "1"), gap_before=False)
    assert done[0].tainted and done[0].trade_count == 2
    assert not feed_all(aggregator, [trade("1900", "13", "1")])[0].tainted


def test_vwap_rounds_half_even_on_a_grid_finer_than_every_price() -> None:
    aggregator = TradeAggregator({15: T0}, far_end())
    done = feed_all(aggregator, [trade("1", "1.00", "1"), trade("2", "2.00", "2"), trade("901", "5", "1")])
    assert done[0].vwap == Decimal("1.666666666667")  # 5/3 at twelve decimals
    assert done[0].low <= done[0].vwap <= done[0].high


def test_sub_second_times_are_floored_not_rounded() -> None:
    aggregator = TradeAggregator({15: T0}, far_end())
    done = feed_all(aggregator, [trade("899.9999999", "10", "1"), trade("900.0000001", "11", "1"),
                                 trade("1800", "12", "1")])
    assert [c.open_unix for c in done] == [T0, T0 + 900]


def test_time_may_not_run_backwards_into_a_finished_candle() -> None:
    aggregator = TradeAggregator({15: T0}, far_end())
    feed_all(aggregator, [trade("10", "10", "1"), trade("1000", "11", "1")])
    with pytest.raises(ValidationFailure):
        aggregator.feed(trade("20", "12", "1"), gap_before=False)


def test_counts_of_ignored_trades_are_kept() -> None:
    aggregator = TradeAggregator({15: T0 + 900}, {15: T0 + 1800})
    feed_all(aggregator, [trade("10", "10", "1"), trade("20", "10", "1"), trade("1000", "10", "1"),
                          trade("2000", "10", "1"), trade("3000", "10", "1")])
    assert aggregator.ignored_before_range == 2 and aggregator.ignored_after_end == 1


def test_bad_inputs_are_refused() -> None:
    with pytest.raises(ValidationFailure):
        TradeAggregator({7: T0}, {7: T0 + 900})
    with pytest.raises(ValidationFailure):
        TradeAggregator({15: T0 + 1}, {15: T0 + 900})  # floors and ends sit on the grid
    with pytest.raises(ValidationFailure):
        TradeAggregator({15: T0}, {60: T0 + 3600})
    with pytest.raises(ValidationFailure):
        TradeAggregator({}, {})


prices = st.integers(min_value=1, max_value=10**9).map(lambda cents: Decimal(cents).scaleb(-4))
volumes = st.integers(min_value=1, max_value=10**10).map(lambda units: Decimal(units).scaleb(-8))


@settings(max_examples=150, deadline=None)
@given(st.lists(st.tuples(prices, volumes), min_size=1, max_size=40))
def test_property_the_vwap_stays_inside_the_range_and_the_candle_is_valid(rows) -> None:
    aggregator = TradeAggregator({15: T0}, far_end())
    trades = [trade(str(index), str(price), str(volume)) for index, (price, volume) in enumerate(rows)]
    trades.append(trade("900", "1", "1"))
    done = feed_all(aggregator, trades)
    assert len(done) == 1
    bucket = done[0]
    assert bucket.volume == sum((volume for _, volume in rows), Decimal(0))
    assert bucket.trade_count == len(rows) and bucket.high == max(p for p, _ in rows)
    assert bucket.low == min(p for p, _ in rows) and bucket.open == rows[0][0] and bucket.close == rows[-1][0]
    receipt = datetime.fromtimestamp(T0, UTC) + timedelta(minutes=16)
    candle = Candle(
        symbol="BTC/USD", source_pair="XBTUSD", interval_minutes=15, open_time_utc=from_unix(T0),
        close_time_utc=from_unix(T0 + 900), available_at_utc=receipt, open=bucket.open, high=bucket.high,
        low=bucket.low, close=bucket.close, vwap=bucket.vwap, volume=bucket.volume, trade_count=bucket.trade_count,
        source="kraken_public_trades")
    assert candle.low <= candle.vwap <= candle.high
