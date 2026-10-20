"""The scorecard's split by strategy version, market type at entry and stance (W13-A, C13), checked by hand.

``split_scorecard`` takes the scorecard's kernel sums (``kernel/trade_metrics.summarize``) and, for every trade and
every position still held, the choice behind its first buy (``EntryChoice``); it counts every trade and open position
once per dimension with the kernel's ``split_results``, so each dimension's rows sum exactly to the totals. Every
expected number is worked out in the comments with plain arithmetic. All fills are synthetic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_graph.application.scorecard import (
    CARRIED_IN_GROUP,
    MARKET_TYPES_AT_ENTRY,
    NO_DAY_PLAN,
    STANCES_AT_ENTRY,
    EntryChoice,
    split_scorecard,
)
from trade_graph.domain.errors import ValidationFailure
from trade_graph.kernel.trade_metrics import TradeFill, summarize

T0 = datetime(2026, 11, 2, tzinfo=UTC)
D = Decimal
DIP, TREND = "dip-rebound@1", "slow-trend-pullback@1"


def _fill(ref: str, side: str, quantity: str, price: str, fee: str, minutes: int, symbol: str = "SOL/USD"):
    return TradeFill(fill_ref=ref, symbol=symbol, side=side, quantity=D(quantity),
                     quote_amount=D(quantity) * D(price), fee=D(fee), at=T0 + timedelta(minutes=minutes))


def _metrics(extra: str = "0"):
    fills = [
        # Trade 1: buy 10 at 100 (1000, fee 4), sell 10 at 110 (1100, fee 8.8): 1100 - 1000 - 12.8 = 87.2, a win.
        _fill("f1", "buy", "10", "100", "4", 0), _fill("f2", "sell", "10", "110", "8.8", 60),
        # Trade 2: buy 10 at 100 (1000, fee 4), sell 10 at 97 (970, fee 7.76): 970 - 1000 - 11.76 = -41.76, a loss.
        _fill("f3", "buy", "10", "100", "4", 120), _fill("f4", "sell", "10", "97", "7.76", 180),
        # Trade 3 on ETH: buy 1 at 3000 (fee 12), sell 1 at 2990 (fee 23.92): 2990 - 3000 - 35.92 = -45.92, a loss.
        _fill("f5", "buy", "1", "3000", "12", 200, "ETH/USD"), _fill("f6", "sell", "1", "2990", "23.92", 230,
                                                                     "ETH/USD"),
        # Still held: buy 5 SOL at 100 (500, fee 2). Valued at the end at 102 less the 0.8% selling fee:
        # 510 - 4.08 = 505.92, so it adds 505.92 - 500 - 2 = 3.92.
        _fill("f7", "buy", "5", "100", "2", 300),
    ]
    # Cash after the fills: 10000 + 87.2 - 41.76 - 45.92 - 502 = 9497.52; plus the held SOL's 505.92 = 10003.44.
    ending = D("10003.44") + D(extra)
    return summarize(currency="USD", starting_equity=D("10000"), ending_equity=ending, fills=fills, equity_points=[])


CHOICES = (
    EntryChoice(strategy=DIP, market_type="sideways", stance="buy_dips", plan_id="dp-1"),
    EntryChoice(strategy=TREND, market_type="rising", stance="follow_trend", plan_id="dp-2"),
    EntryChoice(strategy=DIP, market_type="falling", stance="buy_dips", plan_id="dp-3"),
)
HELD = EntryChoice(strategy=NO_DAY_PLAN, market_type="missing", stance="not_recorded")


def test_each_dimension_sums_to_the_scorecards_totals() -> None:
    metrics = _metrics()
    assert metrics.result == D("3.44")  # 87.2 - 41.76 - 45.92 + 3.92
    split = split_scorecard(metrics, trade_choices=CHOICES, open_choices=(HELD,), open_values=(D("505.92"),))
    # By strategy version: the versions found in name order, then the trades no day plan placed.
    assert [(row.group, row.trades, row.wins, row.losses, row.open_positions, row.result)
            for row in split.by_strategy] == [
        (DIP, 2, 1, 1, 0, D("41.28")),  # 87.2 - 45.92
        (TREND, 1, 0, 1, 0, D("-41.76")),
        (NO_DAY_PLAN, 0, 0, 0, 1, D("3.92")),
    ]
    # By market type at entry: every word in its fixed order, empty ones included.
    assert [row.group for row in split.by_market_type] == list(MARKET_TYPES_AT_ENTRY)
    assert {row.group: row.result for row in split.by_market_type if row.trades or row.open_positions} == {
        "sideways": D("87.2"), "rising": D("-41.76"), "falling": D("-45.92"), "missing": D("3.92")}
    # By stance: the desk's stance recorded with the day plan, or not recorded.
    assert [row.group for row in split.by_stance] == list(STANCES_AT_ENTRY)
    assert {row.group: (row.trades, row.result) for row in split.by_stance if row.trades or row.open_positions} == {
        "buy_dips": (2, D("41.28")), "follow_trend": (1, D("-41.76")), "not_recorded": (0, D("3.92"))}
    for rows in (split.by_strategy, split.by_market_type, split.by_stance, [row for _, row in split.by_choice]):
        assert sum(row.trades for row in rows) == metrics.trades == 3
        assert sum(row.wins for row in rows) == metrics.wins == 1
        assert sum(row.losses for row in rows) == metrics.losses == 2
        assert sum(row.flat_trades for row in rows) == metrics.flat_trades == 0
        assert sum(row.open_positions for row in rows) == len(metrics.open_positions) == 1
        assert sum((row.result for row in rows), D("0")) == metrics.result
    assert split.outside_trades == D("0")


def test_the_combined_rows_say_which_choice_earned_or_lost_in_which_market() -> None:
    split = split_scorecard(_metrics(), trade_choices=CHOICES, open_choices=(HELD,), open_values=(D("505.92"),))
    assert [(key, row.trades, row.result) for key, row in split.by_choice] == [
        ((DIP, "sideways", "buy_dips"), 1, D("87.2")),
        ((DIP, "falling", "buy_dips"), 1, D("-45.92")),
        ((TREND, "rising", "follow_trend"), 1, D("-41.76")),
        ((NO_DAY_PLAN, "missing", "not_recorded"), 0, D("3.92")),
    ]


def test_money_outside_the_trades_is_shown_apart_and_the_rows_still_sum_to_the_trades() -> None:
    metrics = _metrics(extra="1.5")  # 1.5 USD more in the books than the trades explain
    split = split_scorecard(metrics, trade_choices=CHOICES, open_choices=(HELD,), open_values=(D("505.92"),))
    assert split.outside_trades == D("1.5")
    assert sum((row.result for row in split.by_market_type), D("0")) + split.outside_trades == metrics.result


def test_a_coin_already_held_at_the_start_counts_as_carried_in() -> None:
    choice = EntryChoice.carried_in()
    assert {choice.strategy, choice.market_type, choice.stance} == {CARRIED_IN_GROUP}
    split = split_scorecard(_metrics(), trade_choices=(choice, *CHOICES[1:]), open_choices=(HELD,),
                            open_values=(D("505.92"),))
    assert [row.group for row in split.by_strategy] == [DIP, TREND, NO_DAY_PLAN, CARRIED_IN_GROUP]
    assert {row.group: row.trades for row in split.by_market_type}["carried_in"] == 1


@pytest.mark.parametrize("changes, message", [
    ({"trade_choices": CHOICES[:2]}, "one choice per trade"),
    ({"open_choices": ()}, "one choice per position"),
    ({"open_values": ()}, "one choice per position"),
])
def test_every_trade_and_open_position_needs_exactly_one_choice(changes, message) -> None:
    options = {"trade_choices": CHOICES, "open_choices": (HELD,), "open_values": (D("505.92"),), **changes}
    with pytest.raises(ValidationFailure, match=message):
        split_scorecard(_metrics(), **options)


@pytest.mark.parametrize("field, value", [("market_type", "trending"), ("stance", "maybe"), ("strategy", "x")])
def test_a_choice_uses_the_fixed_words(field, value) -> None:
    document = {"strategy": DIP, "market_type": "sideways", "stance": "buy_dips", field: value}
    with pytest.raises(ValidationFailure):
        EntryChoice(**document)


def test_the_document_names_its_basis_and_every_row() -> None:
    split = split_scorecard(_metrics(), trade_choices=CHOICES, open_choices=(HELD,), open_values=(D("505.92"),))
    document = split.document()
    assert document["available"] is True and document["reason"] is None
    assert document["totals"] == {"trades": 3, "wins": 1, "losses": 2, "flat_trades": 0, "open_positions": 1,
                                  "result_after_fees": "3.44"}  # 87.2 - 41.76 - 45.92 + 3.92
    assert document["outside_trades"] == "0"
    assert document["by_strategy"][0] == {
        "strategy_ref": DIP, "trades": 2, "wins": 1, "losses": 1, "flat_trades": 0, "result_after_fees": "41.28",
        "closed_trades_result": "41.28", "open_positions": 0, "open_position_result": "0"}
    assert [row["market_type"] for row in document["by_market_type"]] == list(MARKET_TYPES_AT_ENTRY)
    assert [row["stance"] for row in document["by_stance"]] == list(STANCES_AT_ENTRY)
    assert document["by_choice"][0]["strategy_ref"] == DIP and document["by_choice"][0]["market_type"] == "sideways"
    assert [item["kind"] for item in document["entries"]] == ["trade", "trade", "trade", "open"]
    assert document["entries"][0]["plan_id"] == "dp-1" and document["entries"][3]["result_after_fees"] == "3.92"
    assert "first buy" in document["basis"]
