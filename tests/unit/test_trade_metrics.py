"""Result sums shared by the practice range (T29) and the paper scorecard (T42), checked by hand to the last digit.

Every expected number below is worked out in the comments with plain arithmetic, not by calling the module.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_graph.contracts.fills import SimFill
from trade_graph.contracts.models import FillFeeRecord, FillRecord
from trade_graph.domain.errors import ValidationFailure
from trade_graph.kernel.trade_metrics import (
    EquityPoint,
    TradeFill,
    hold_benchmark,
    round_trips,
    summarize,
    worst_drawdown,
    worst_losing_streak,
)

T0 = datetime(2026, 7, 1, tzinfo=UTC)
D = Decimal


def _fill(ref: str, side: str, quantity: str, price: str, fee: str, minutes: int) -> TradeFill:
    return TradeFill(fill_ref=ref, symbol="ABC/USD", side=side, quantity=D(quantity),
                     quote_amount=D(quantity) * D(price), fee=D(fee), at=T0 + timedelta(minutes=minutes))


def _golden_fills() -> list[TradeFill]:
    return [
        # Trade 1: buy 10 at 100 (1000, maker fee 0.4% = 4), sell 10 at 110 (1100, fee 0.8% = 8.8).
        # Result 1100 - 1000 - 4 - 8.8 = 87.2, a win.
        _fill("f1", "buy", "10", "100", "4", 0),
        _fill("f2", "sell", "10", "110", "8.8", 60),
        # Trade 2 in partial fills: buys 6 at 100 (600, fee 2.4) and 4 at 101 (404, fee 1.616); sells 5 at 99
        # (495, fee 3.96) and 5 at 98 (490, fee 3.92). Bought 1004, sold 985, fees 11.896.
        # Result 985 - 1004 - 11.896 = -30.896, a loss.
        _fill("f3", "buy", "6", "100", "2.4", 120),
        _fill("f4", "buy", "4", "101", "1.616", 135),
        _fill("f5", "sell", "5", "99", "3.96", 180),
        _fill("f6", "sell", "5", "98", "3.92", 195),
        # Trade 3: buy 10 at 100 (1000, taker fee 8), sell 10 at 99 (990, fee 7.92). Result -25.92, a loss.
        _fill("f7", "buy", "10", "100", "8", 240),
        _fill("f8", "sell", "10", "99", "7.92", 300),
        # Still open at the end: buy 5 at 100 (500, fee 2).
        _fill("f9", "buy", "5", "100", "2", 360),
    ]


def test_round_trips_run_from_flat_to_flat_including_partial_fills_and_every_fee() -> None:
    trips, open_positions = round_trips(_golden_fills())
    assert [trip.result for trip in trips] == [D("87.2"), D("-30.896"), D("-25.92")]
    assert [trip.outcome for trip in trips] == ["win", "loss", "loss"]
    second = trips[1]
    assert (second.quantity, second.bought, second.sold, second.fees) == (D("10"), D("1004"), D("985"), D("11.896"))
    assert second.fill_refs == ("f3", "f4", "f5", "f6")
    assert second.opened_at == T0 + timedelta(minutes=120) and second.closed_at == T0 + timedelta(minutes=195)
    assert len(open_positions) == 1
    held = open_positions[0]
    assert (held.quantity, held.bought, held.fees, held.fill_refs) == (D("5"), D("500"), D("2"), ("f9",))


def test_golden_summary_is_exact_to_the_last_digit() -> None:
    fills = _golden_fills()
    # Cash: 10000 + 87.2 - 30.896 - 25.92 = 10030.384, then the open buy costs 502: cash 9528.384. The caller values
    # the 5 held at 102: 510. Ending equity 10038.384, result 38.384, result fraction 38.384 / 10000 = 0.0038384.
    curve = [EquityPoint(T0 + timedelta(minutes=minutes), D(value)) for minutes, value in (
        (0, "10000"), (30, "10050"), (60, "10087.2"), (150, "9990"), (195, "10056.304"), (300, "10030.384"),
        (420, "10038.384"))]
    metrics = summarize(currency="USD", starting_equity=D("10000"), ending_equity=D("10038.384"), fills=fills,
                        equity_points=curve)
    assert metrics.trades == 3 and metrics.wins == 1 and metrics.losses == 2 and metrics.flat_trades == 0
    assert metrics.win_rate == D("0.3333333333")  # 1 / 3, half-even at ten places
    assert metrics.total_won == D("87.2") and metrics.total_lost == D("-56.816")  # -30.896 - 25.92
    assert metrics.average_win == D("87.2") and metrics.average_loss == D("-28.408")  # -56.816 / 2
    assert metrics.fees_paid == D("42.616")  # 12.8 + 11.896 + 15.92 + 2 (the open buy's fee too)
    assert metrics.result == D("38.384") and metrics.result_fraction == D("0.0038384")
    # Cash earns nothing (no interest, D127), so the result against cash is the result itself.
    assert metrics.versus_cash == D("38.384")
    # Worst drawdown: peak 10087.2 at minute 60, trough 9990 at minute 150: 97.2, and 97.2 / 10087.2 = 0.0096359743.
    drawdown = metrics.worst_drawdown
    assert (drawdown.amount, drawdown.fraction) == (D("97.2"), D("0.0096359743"))
    assert drawdown.peak_at == T0 + timedelta(minutes=60) and drawdown.trough_at == T0 + timedelta(minutes=150)
    assert metrics.worst_losing_streak == 2
    assert len(metrics.open_positions) == 1
    document = metrics.to_document()
    assert document["win_rate"] == "0.3333333333" and document["fees_paid"] == "42.616"
    assert document["worst_drawdown"]["fraction"] == "0.0096359743"
    assert document["open_positions"][0]["quantity"] == "5"


def test_holding_btc_spends_the_capital_at_the_taker_fee_and_sells_at_the_end() -> None:
    # Quantity: 10000 / (100 x 1.008) = 99.2063492063..., floored to the 0.00000001 lot: 99.20634920.
    # Principal 9920.63492, fee 79.36507936, cash left 0.00000064. Sale: 99.2063492 x 110 = 10912.698412, fee
    # 87.301587296. Ending 0.00000064 + 10912.698412 - 87.301587296 = 10825.396825344, result 825.396825344.
    hold = hold_benchmark(symbol="BTC/USD", capital=D("10000"), buy_price=D("100"), sell_price=D("110"),
                          fee_rate=D("0.008"), quantity_increment=D("0.00000001"), bought_at=T0,
                          sold_at=T0 + timedelta(days=30))
    assert hold.quantity == D("99.20634920")
    assert (hold.buy_fee, hold.cash_left) == (D("79.36507936"), D("0.00000064"))
    assert (hold.sell_fee, hold.ending_value, hold.result) == (D("87.301587296"), D("10825.396825344"),
                                                               D("825.396825344"))
    metrics = summarize(currency="USD", starting_equity=D("10000"), ending_equity=D("10038.384"),
                        fills=_golden_fills(), equity_points=[EquityPoint(T0, D("10000"))],
                        benchmarks={"hold_btc": hold})
    # The strategy's 38.384 against holding BTC's 825.396825344: -787.012825344.
    assert metrics.versus_benchmarks == {"hold_btc": D("-787.012825344")}
    assert metrics.to_document()["benchmarks"]["hold_btc"]["versus"] == "-787.012825344"


def test_no_trades_gives_counts_of_zero_and_no_invented_averages() -> None:
    metrics = summarize(currency="USD", starting_equity=D("10000"), ending_equity=D("10000"), fills=[],
                        equity_points=[EquityPoint(T0, D("10000")), EquityPoint(T0 + timedelta(hours=1), D("10000"))])
    assert metrics.trades == 0 and metrics.win_rate is None
    assert metrics.average_win is None and metrics.average_loss is None
    assert metrics.worst_drawdown.amount == 0 and metrics.worst_drawdown.peak_at is None
    assert metrics.worst_losing_streak == 0 and metrics.result == 0


def test_a_flat_trade_is_neither_a_win_nor_a_loss_and_ends_a_losing_streak() -> None:
    def trip(index: int, sell_price: str) -> list[TradeFill]:
        return [_fill(f"b{index}", "buy", "1", "100", "0", index * 10),
                _fill(f"s{index}", "sell", "1", sell_price, "0", index * 10 + 5)]

    fills = [*trip(0, "99"), *trip(1, "98"), *trip(2, "100"), *trip(3, "97"), *trip(4, "101")]
    trips, _ = round_trips(fills)
    assert [t.outcome for t in trips] == ["loss", "loss", "flat", "loss", "win"]
    assert worst_losing_streak(trips) == 2


def test_selling_more_than_is_held_is_refused_because_the_account_is_long_only() -> None:
    with pytest.raises(ValidationFailure, match="long only"):
        round_trips([_fill("b", "buy", "1", "100", "0", 0), _fill("s", "sell", "2", "100", "0", 1)])
    with pytest.raises(ValidationFailure, match="long only"):
        round_trips([_fill("s", "sell", "1", "100", "0", 0)])


def test_fills_out_of_time_order_are_refused() -> None:
    with pytest.raises(ValidationFailure, match="time order"):
        round_trips([_fill("b", "buy", "1", "100", "0", 10), _fill("s", "sell", "1", "100", "0", 5)])


def test_inputs_must_be_exact_decimals() -> None:
    with pytest.raises(ValidationFailure):
        TradeFill(fill_ref="x", symbol="ABC/USD", side="buy", quantity=1.5, quote_amount=D("1"), fee=D("0"),
                  at=T0)  # type: ignore[arg-type]
    with pytest.raises(ValidationFailure):
        EquityPoint(T0, 10000)  # type: ignore[arg-type]


def test_drawdown_measures_from_the_running_peak_not_the_start() -> None:
    points = [EquityPoint(T0 + timedelta(hours=i), D(v)) for i, v in enumerate(["100", "80", "200", "150", "190"])]
    drawdown = worst_drawdown(points)
    # 100 -> 80 is 20% (0.2); 200 -> 150 is 25% (0.25), the worse one.
    assert (drawdown.amount, drawdown.fraction) == (D("50"), D("0.25"))
    assert drawdown.peak_equity == D("200") and drawdown.trough_equity == D("150")


def test_a_sim_fill_becomes_a_trade_fill_with_its_principal_and_quote_fee() -> None:
    fill = SimFill(order_id="o1", symbol="ABC/USD", side="buy", quantity=D("2"), price=D("100.5"), fee=D("0.804"),
                   fee_asset="USD", liquidity="maker", reason="limit_trade_through", reference_price=D("100.5"),
                   filled_at_utc=T0, event_ref="synthetic:ABC/USD:15:0", heuristic=True,
                   cost_profile_ref="p@1", cost_profile_sha256="0" * 64, fill_policy_ref="bar-replay@1",
                   fill_policy_sha256="0" * 64)
    converted = TradeFill.from_sim_fill(fill, fill_ref="o1:1")
    assert (converted.quantity, converted.quote_amount, converted.fee) == (D("2"), D("201"), D("0.804"))
    with pytest.raises(ValidationFailure, match="quote"):
        TradeFill.from_sim_fill(fill.model_copy(update={"fee_asset": "ABC"}), fill_ref="o1:1")


# --- Ledger fills for the paper scorecard (T42, D201) -------------------------------------------------------


def _record(side: str, quantity: str, price: str, *, fee: str = "0", fee_asset: str = "USD", symbol: str = "BTC/USD",
            **updates) -> FillRecord:
    body = dict(venue="paper", account_id="paper", trade_id=f"t-{side}-{quantity}", intent_id=None, symbol=symbol,
                side=side, quantity=quantity, price=price, fee_amount=fee, fee_asset=fee_asset, liquidity="maker",
                filled_at_utc=T0)
    body.update(updates)
    return FillRecord(**body)


def _leg(asset: str, amount: str, ref: str, **updates) -> FillFeeRecord:
    return FillFeeRecord(asset=asset, amount=amount, source_ref=ref, effective_at_utc=T0, **updates)


def _convert(record: FillRecord) -> TradeFill:
    return TradeFill.from_fill_record(record, fill_ref="paper:paper:x", base_asset="BTC", quote_asset="USD")


def test_a_ledger_fill_with_a_quote_fee_keeps_its_principal_and_fee() -> None:
    # Buy 2 at 100.5: principal 201, fee 0.804 USD (0.4%).
    converted = _convert(_record("buy", "2", "100.5", fee="0.804"))
    assert (converted.side, converted.quantity, converted.quote_amount, converted.fee) == (
        "buy", D("2"), D("201"), D("0.804"))
    assert converted.fill_ref == "paper:paper:x" and converted.at == T0 and converted.symbol == "BTC/USD"
    # A venue principal that is not quantity x price (quote_cost) is the principal the books posted.
    assert _convert(_record("buy", "0.3", "100", quote_cost="30.01")).quote_amount == D("30.01")


def test_a_fee_in_the_coin_changes_what_is_held_and_is_valued_at_the_fill_price() -> None:
    # Buy 1 at 100 with a 0.01 coin fee: 0.99 is held; the fee is worth 0.01 x 100 = 1, so the principal of what
    # is held is 100 - 1 = 99 and bought + fee is still the 100 paid.
    bought = _convert(_record("buy", "1", "100", fee="0.01", fee_asset="BTC"))
    assert (bought.quantity, bought.quote_amount, bought.fee) == (D("0.99"), D("99"), D("1"))
    # Sell 0.98 at 110 with a 0.01 coin fee: 0.99 leaves the books; the fee is worth 1.1, so the principal is
    # 0.98 x 110 + 1.1 = 108.9 and sold - fee is the 107.8 received.
    sold = _convert(_record("sell", "0.98", "110", fee="0.01", fee_asset="BTC"))
    assert (sold.quantity, sold.quote_amount, sold.fee) == (D("0.99"), D("108.9"), D("1.1"))
    # One round trip from flat to flat: 108.9 - 99 - (1 + 1.1) = 7.8, exactly the cash made (107.8 - 100).
    trips, still_open = round_trips([bought, sold])
    assert [trip.result for trip in trips] == [D("7.8")] and still_open == ()


def test_fee_vectors_add_every_leg_and_value_a_third_asset_at_its_identified_rate() -> None:
    # Legs: 0.5 USD, 2 KFEE at the identified 0.01 USD each (0.02) and 0.001 BTC at the fill price 100 (0.1).
    record = _record("buy", "1", "100", quote_cost="100", fee_components=(
        _leg("USD", "0.5", "usd"), _leg("KFEE", "2", "kfee", identified_rate="0.01", rate_source_ref="kfee-rate"),
        _leg("BTC", "0.001", "btc")))
    converted = _convert(record)
    assert (converted.quantity, converted.quote_amount, converted.fee) == (D("0.999"), D("99.9"), D("0.62"))
    # A legacy fee in a third asset is valued at its identified rate too: 3 KFEE x 0.02 = 0.06.
    legacy = _convert(_record("sell", "1", "100", fee="3", fee_asset="KFEE", fee_identified_rate="0.02"))
    assert (legacy.quantity, legacy.quote_amount, legacy.fee) == (D("1"), D("100"), D("0.06"))


def test_a_net_rebate_moves_the_principal_because_fees_paid_are_never_negative() -> None:
    # A buy of 1 at 100 with a 0.2 USD rebate paid 99.8: bought 99.8, fee 0. A sell received 100.2: sold 100.2.
    buy = _convert(_record("buy", "1", "100", quote_cost="100", fee_components=(_leg("USD", "-0.2", "rebate"),)))
    sell = _convert(_record("sell", "1", "100", quote_cost="100", fee_components=(_leg("USD", "-0.2", "rebate"),)))
    assert (buy.quote_amount, buy.fee) == (D("99.8"), D("0"))
    assert (sell.quote_amount, sell.fee) == (D("100.2"), D("0"))


def test_a_ledger_fill_that_cannot_be_put_in_the_quote_currency_is_refused() -> None:
    with pytest.raises(ValidationFailure, match="identified rate"):
        _convert(_record("buy", "1", "100", fee="3", fee_asset="KFEE"))
    with pytest.raises(ValidationFailure, match="symbol"):
        _convert(_record("buy", "1", "100", fee="0.4", symbol="ETH/USD"))
