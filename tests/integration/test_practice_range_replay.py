"""The practice-range replay (T29) on hand-built candles: every price, fee and sum checked by hand.

The golden day (``tests/practice_range_support.py``): SYN/USD on 2026-07-01 at 15 minutes, cost profile
``kraken-spot-starting-tier@1`` (maker 0.40%, taker 0.80%), fill policy ``bar-replay@1`` (10% of a bar's volume,
0.10% slippage), USD10,000, tick 0.01 and whole-unit lots. The strategy buys at the next open when the close crosses
above 100, with half the equity, a 2% stop and a 5% take-profit.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from tests.practice_range_support import (
    WINDOW_START,
    bar,
    cross_strategy,
    flat,
    golden_btc,
    golden_candles,
    open_store,
    save,
)

from trade_graph.application.practice_range.replay import ReplayRequest, replay
from trade_graph.contracts.models import InstrumentRules
from trade_graph.contracts.practice_range import PracticeStrategy, ReplayWindow
from trade_graph.domain.errors import ValidationFailure
from trade_graph.kernel.cost_profiles import KRAKEN_STARTING, OKX_EEA, load_cost_profiles
from trade_graph.kernel.fills import BAR_REPLAY_POLICY

D = Decimal
DAY_WINDOW = ReplayWindow(start=date(2026, 7, 1), end=date(2026, 7, 2))
AS_OF = datetime(2026, 10, 2, tzinfo=UTC)
RULES = InstrumentRules(venue="kraken", symbol="SYN/USD", base_asset="SYN", quote_asset="USD",
                        price_increment=D("0.01"), quantity_increment=D("1"), min_quantity=D("1"),
                        min_notional=D("10"), synthetic=True)


def _request(strategy: PracticeStrategy | None = None, *, profile_id: str = KRAKEN_STARTING, **changes
             ) -> ReplayRequest:
    values = dict(strategy=strategy or cross_strategy(), symbol="SYN/USD", source="synthetic", window=DAY_WINDOW,
                  data_as_of=AS_OF, profile=load_cost_profiles().get(profile_id), policy=BAR_REPLAY_POLICY,
                  capital=D("10000"), rules=RULES)
    values.update(changes)
    return ReplayRequest(**values)


@pytest.fixture
def golden(tmp_path):
    database, store = open_store(tmp_path / "market" / "market_data.sqlite")
    save(store, golden_candles())
    save(store, golden_btc())
    yield store
    database.close()


def test_the_golden_day_matches_the_hand_calculation(golden) -> None:
    result = replay(golden, _request())
    fills = [(item.purpose, item.fill.reason, item.fill.side, item.fill.quantity, item.fill.price, item.fill.fee)
             for item in result.fills]
    assert fills == [
        # Signal at bar 10's close (101 crosses above 100). Size: 5000 / (101 x 1.001 x 1.008) = 49.06..., 49 units.
        # Bar 11 opens at 101.2: plus 0.10% slippage 101.3012, up to the tick 101.31. Fee 101.31 x 49 x 0.8%.
        ("entry", "market", "buy", D("49"), D("101.31"), D("39.71352")),
        # Stop 101.31 x 0.98 = 99.2838, down to 99.28; target 101.31 x 1.05 = 106.3755, up to 106.38. Bar 15's high
        # of 106.5 trades beyond 106.38: 106.38 less 0.10% = 106.27362, down to 106.27. Fee 41.65784.
        ("protective_exit", "take_profit", "sell", D("49"), D("106.27"), D("41.65784")),
        # Signal at bar 20 (100.4). Cash 10161.66864: half is 5080.83432 / (100.4 x 1.001 x 1.008) = 50.15, 50 units.
        # Bar 21 opens at 100.5: 100.6005, up to 100.61. Fee 40.244. Stop 98.5978 -> 98.59.
        ("entry", "market", "buy", D("50"), D("100.61"), D("40.244")),
        # Bar 22 opens at 97, below the stop: the worse open, less 0.10% = 96.903, down to 96.90. Fee 38.76.
        ("protective_exit", "stop_gap", "sell", D("50"), D("96.90"), D("38.76")),
        # Signal at bar 60 (100.2). Cash 9897.16464: half / (100.2 x 1.001 x 1.008) = 48.9, 48 units at 100.31.
        ("entry", "market", "buy", D("48"), D("100.31"), D("38.51904")),
    ]
    metrics = result.metrics
    # Trade 1: 5207.23 - 4964.19 - 39.71352 - 41.65784 = 161.66864. Trade 2: 4845 - 5030.5 - 40.244 - 38.76 = -264.504.
    assert [trip.result for trip in metrics.round_trips] == [D("161.66864"), D("-264.504")]
    assert (metrics.trades, metrics.wins, metrics.losses, metrics.win_rate) == (2, 1, 1, D("0.5"))
    assert (metrics.average_win, metrics.average_loss) == (D("161.66864"), D("-264.504"))
    assert metrics.worst_losing_streak == 1
    assert metrics.fees_paid == D("198.8944")
    # Still held at the end: 48 units valued at the last close 100.3 less 0.10% = 100.1997 -> 100.19, less the
    # taker fee 38.47296: 4770.64704. Cash 5043.7656. Ending equity 9814.41264, result -185.58736.
    assert result.ending.cash == D("5043.7656") and result.ending.held == D("48")
    assert (result.ending.price, result.ending.fee, result.ending.value) == (D("100.19"), D("38.47296"),
                                                                           D("4770.64704"))
    assert metrics.ending_equity == D("9814.41264") and metrics.result == D("-185.58736")
    # Peak at bar 14's close: 4996.09648 + 49 x 105.5 = 10165.59648. Trough at the end: 9814.41264.
    # 351.18384 / 10165.59648 = 0.0345463093.
    assert (metrics.worst_drawdown.amount, metrics.worst_drawdown.fraction) == (D("351.18384"), D("0.0345463093"))
    assert metrics.worst_drawdown.peak_at == WINDOW_START + timedelta(minutes=15 * 15)
    # Holding SYN: 10000 / (99 x 1.008) = 100.2 -> 100 units at the first open 99: 9900, fee 79.2, 20.8 left.
    # Sold at the last close 100.3: 10030, fee 80.24. Ending 9970.56, result -29.44; the strategy is 156.14736 worse.
    # Holding BTC: 0.19841269 at 50000 (9920.6345, fee 79.365076, 0.000424 left), sold at 55000 (10912.69795, fee
    # 87.3015836): result 825.3967904; the strategy is 1010.9841504 worse.
    assert metrics.benchmarks["hold_coin"].result == D("-29.44")
    assert metrics.benchmarks["hold_btc"].result == D("825.3967904")
    assert metrics.versus_benchmarks == {"hold_btc": D("-1010.9841504"), "hold_coin": D("-156.14736")}
    assert metrics.versus_cash == D("-185.58736")


def test_the_golden_day_reads_its_data_point_in_time_and_says_so(golden) -> None:
    result = replay(golden, _request())
    data = result.data
    assert (data.symbol, data.interval_minutes, data.source, data.read_mode) == ("SYN/USD", 15, "synthetic",
                                                                               "point_in_time")
    assert (data.window_bars, data.window_present, data.warmup_slots) == (96, 96, 1)
    assert data.missing_ranges == () and data.evidence_labels == ("synthetic_fixture",)
    assert len(data.input_sha256) == 64
    assert result.benchmark_data["hold_btc"]["first_open_utc"] == "2026-07-01T00:00:00.000000Z"
    assert result.events["entry_signals"] == 3 and result.events["entries_placed"] == 3


def test_a_rerun_gives_the_identical_result_document(golden) -> None:
    first = replay(golden, _request()).to_document()
    second = replay(golden, _request()).to_document()
    assert first == second


def test_each_cost_profile_gives_its_own_fees(golden) -> None:
    kraken = replay(golden, _request(profile_id=KRAKEN_STARTING))
    okx = replay(golden, _request(profile_id=OKX_EEA))
    assert okx.fills[0].fill.fee == D("101.31") * 49 * D("0.002")  # OKX Europe taker 0.20%
    assert okx.fills[0].fill.cost_profile_ref == "okx-eea-spot@1"
    assert kraken.metrics.fees_paid > okx.metrics.fees_paid


def test_candles_received_after_the_data_cut_off_are_invisible(golden, tmp_path) -> None:
    late = [bar("SYN/USD", 30, "97.2", "120", "97.2", "110", received=AS_OF + timedelta(days=1))]
    save(golden, late, at=AS_OF + timedelta(days=1))
    before = replay(golden, _request()).to_document()
    database, fresh = open_store(tmp_path / "fresh" / "market_data.sqlite")
    save(fresh, golden_candles())
    save(fresh, golden_btc())
    assert replay(fresh, _request()).to_document()["fills"] == before["fills"]
    database.close()


def test_nothing_after_the_window_can_change_it(golden) -> None:
    before = replay(golden, _request()).to_document()
    save(golden, [bar("SYN/USD", 96, "1", "500", "1", "300")])  # the day after
    assert replay(golden, _request()).to_document() == before


def test_a_touch_does_not_fill_a_resting_limit_but_a_trade_through_does(tmp_path) -> None:
    database, store = open_store(tmp_path / "m" / "market_data.sqlite")
    s = "SYN/USD"
    save(store, [
        *flat(s, range(-1, 5), "99"),
        bar(s, 5, "99", "101", "99", "100.5"),  # signal: limit at the close 100.5 less 0.5% = 99.9975 -> 99.99
        bar(s, 6, "100.4", "100.6", "99.99", "100.2"),  # low touches 99.99: no fill
        bar(s, 7, "100.2", "100.3", "99.95", "100.1"),  # trades through 99.99: fills at the limit as a maker
        *flat(s, range(8, 96), "100.1"),
    ])
    strategy = cross_strategy(entry={
        "all_of": [{"left": {"candle": "close"}, "relation": "crosses_above", "right": {"level": "100"}}],
        "order": {"kind": "limit", "price": {"candle": "close"}, "offset_fraction": "0.005",
                  "expires_after_bars": 4}})
    result = replay(store, _request(strategy))
    entry = result.fills[0].fill
    assert (entry.reason, entry.liquidity, entry.price) == ("limit_trade_through", "maker", D("99.99"))
    assert entry.filled_at_utc == WINDOW_START + timedelta(minutes=15 * 8)  # bar 7's close
    # 5000 / (99.99 x 1.001 x 1.008) = 49.56 -> 49 units, all inside 10% of bar 7's volume of 1000.
    assert entry.quantity == D("49") and entry.fee == D("99.99") * 49 * D("0.004")
    database.close()


def test_an_unfilled_limit_expires_and_a_thin_bar_fills_only_part(tmp_path) -> None:
    database, store = open_store(tmp_path / "m" / "market_data.sqlite")
    s = "SYN/USD"
    save(store, [
        *flat(s, range(-1, 5), "99"),
        bar(s, 5, "99", "101", "99", "100.5"),  # signal: limit 100.5 x 0.9999 = 100.48995 -> 100.48
        *flat(s, range(6, 20), "100.6"),  # never trades through 100.48: the entry expires at bar 9's close
        bar(s, 20, "100.6", "100.6", "99", "99.5"),
        bar(s, 21, "99.5", "101", "99.4", "100.6"),  # second signal: limit 100.58994 -> 100.58, 49 units
        bar(s, 22, "100.6", "100.7", "100.5", "100.6", volume="200"),  # 10% of 200 = 20 units fill
        bar(s, 23, "100.6", "100.7", "100.5", "100.6", volume="250"),  # 25 more
        *flat(s, range(24, 96), "100.65"),  # above the limit: the last 4 expire at bar 25's close
    ])
    strategy = cross_strategy(entry={
        "all_of": [{"left": {"candle": "close"}, "relation": "crosses_above", "right": {"level": "100"}}],
        "order": {"kind": "limit", "price": {"candle": "close"}, "offset_fraction": "0.0001",
                  "expires_after_bars": 4}})
    result = replay(store, _request(strategy))
    assert result.events["entries_expired_unfilled"] == 1
    entries = [item.fill for item in result.fills if item.purpose == "entry"]
    assert [fill.quantity for fill in entries] == [D("20"), D("25")]
    assert result.ending.held == D("45")
    assert result.events["entries_expired_partly_filled"] == 1
    database.close()


def test_a_stop_in_the_entry_bar_counts_and_a_target_there_does_not(tmp_path) -> None:
    database, store = open_store(tmp_path / "m" / "market_data.sqlite")
    s = "SYN/USD"
    save(store, [
        *flat(s, range(-1, 5), "99"),
        bar(s, 5, "99", "101", "99", "100.5"),
        bar(s, 6, "100.5", "120", "90", "100"),  # entry at the open; the bar reaches both exits: only the stop counts
        *flat(s, range(7, 96), "100"),
    ])
    result = replay(store, _request())
    assert [(item.purpose, item.fill.reason) for item in result.fills] == [("entry", "market"),
                                                                          ("protective_exit", "stop_entry_bar")]
    database.close()


def test_rule_exits_and_the_holding_limit_sell_at_the_next_open(tmp_path) -> None:
    database, store = open_store(tmp_path / "m" / "market_data.sqlite")
    s = "SYN/USD"
    save(store, [
        *flat(s, range(-1, 5), "99"),
        bar(s, 5, "99", "101", "99", "100.5"),
        bar(s, 6, "100.5", "100.8", "100.2", "100.6"),  # entry at 100.5 x 1.001 = 100.6005 -> 100.61
        bar(s, 7, "100.6", "100.7", "99.4", "99.5"),  # closes back below 100: the rule exit is placed
        bar(s, 8, "99.6", "99.8", "99.4", "99.7"),  # sold at this open: 99.6 less 0.10% = 99.5004 -> 99.50
        *flat(s, range(9, 96), "99.7"),
    ])
    strategy = cross_strategy(exit={
        "stop_loss_fraction": "0.05",
        "any_of": [{"left": {"candle": "close"}, "relation": "crosses_below", "right": {"level": "100"}}]})
    result = replay(store, _request(strategy))
    exits = [(item.purpose, item.fill.reason, item.fill.price) for item in result.fills if item.fill.side == "sell"]
    assert exits == [("rule_exit", "market", D("99.50"))]
    holding = cross_strategy(exit={"stop_loss_fraction": "0.05", "max_holding_bars": 3})
    result = replay(store, _request(holding))
    sells = [(item.purpose, item.fill.filled_at_utc) for item in result.fills if item.fill.side == "sell"]
    # Entered in bar 6; the third close after that is bar 9's: sold at bar 10's open.
    assert sells == [("holding_limit_exit", WINDOW_START + timedelta(minutes=15 * 10))]
    database.close()


def test_a_missing_candle_fills_nothing_and_is_reported(tmp_path) -> None:
    database, store = open_store(tmp_path / "m" / "market_data.sqlite")
    candles = [candle for candle in golden_candles() if candle.open_time_utc != WINDOW_START + timedelta(minutes=165)]
    save(store, candles)  # bar 11, where the first entry would have filled, is missing
    result = replay(store, _request())
    assert result.data.window_present == 95
    assert [(r["first_open_utc"], r["slots"]) for r in result.data.missing_ranges] == [
        ("2026-07-01T02:45:00.000000Z", 1)]
    # No candle, no fill: the market entry's one-bar life ends in the missing slot. The first fill is the second
    # signal's, at bar 21's open.
    assert result.events["entries_expired_unfilled"] == 1
    assert result.fills[0].fill.filled_at_utc == WINDOW_START + timedelta(minutes=15 * 21)
    assert "hold_btc" not in result.metrics.benchmarks
    assert result.benchmark_data["hold_btc"]["status"] == "unavailable"
    database.close()


def test_requests_outside_the_rules_are_refused(golden) -> None:
    with pytest.raises(ValidationFailure, match="cut-off"):
        replay(golden, _request(data_as_of=datetime(2026, 7, 1, 12, tzinfo=UTC)))
    with pytest.raises(ValidationFailure, match="capital"):
        replay(golden, _request(capital=D("0")))
    with pytest.raises(ValidationFailure, match="symbol"):
        replay(golden, _request(rules=RULES.model_copy(update={"symbol": "ABC/USD"})))


def test_indicator_conditions_use_the_engines_values(tmp_path) -> None:
    from tests.indicator_series import build_candles

    database, store = open_store(tmp_path / "m" / "market_data.sqlite")
    candles = build_candles("trend_pullback", count=1_200)
    save(store, candles)
    first_day = (candles[900].open_time_utc + timedelta(days=1)).date()
    window = ReplayWindow(start=first_day, end=first_day + timedelta(days=2))
    strategy = PracticeStrategy.model_validate({
        "schema_version": 1, "strategy_id": "sma-cross", "version": 1, "title": "Average cross",
        "hypothesis": "The close crossing its average starts a short move; a test strategy.",
        "interval_minutes": 15,
        "indicators": {"average": {"indicator": "SMA", "parameters": {"timeperiod": 20}},
                       "strength": {"indicator": "RSI", "parameters": {"timeperiod": 14}}},
        "entry": {"all_of": [
            {"left": {"candle": "close"}, "relation": "crosses_above", "right": {"indicator": "average"}},
            {"left": {"indicator": "strength"}, "relation": "below", "right": {"level": "70"}}],
            "order": {"kind": "market", "expires_after_bars": 1}},
        "exit": {"stop_loss_fraction": "0.01", "take_profit_fraction": "0.01"},
        "sizing": {"equity_fraction": "0.2"}})
    result = replay(store, _request(strategy, symbol="SYN/USD", window=window, rules=None))
    assert result.events["entry_signals"] >= 1
    assert result.indicator_versions["average"].startswith("indicator-menu-v1/")
    assert result.data.warmup_slots == max(result.indicator_spans.values())
    assert result.to_document() == replay(store, _request(strategy, window=window, rules=None)).to_document()
    database.close()
