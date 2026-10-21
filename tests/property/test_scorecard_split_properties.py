"""Property tests for the scorecard's split (W13-A, C13; D300): whatever the trades and whatever choice is behind each
one's first buy, the split by strategy version, by market type at entry, by stance and by their combinations each
sums exactly to the scorecard's trades, wins, losses, even trades, positions still held and result after fees.

The first test feeds the kernel's sums (``summarize``) random fills and random choices. The second runs random
synthetic paper days through the real ledger with random market-type labels recorded at random moments (as the
hourly scan stores them), and checks the market type each trade is split by against the labels themselves: the
newest label at its first buy when it is at most 2 hours old, ``stale`` when older, ``missing`` when there is none.
All synthetic; no network, model, key or order.
"""

from __future__ import annotations

import tempfile
from datetime import timedelta
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.scorecard_support import PORTFOLIO, START, Paper
from tests.trading_desk_support import insert_label

from trade_graph.application.scorecard import (
    MARKET_TYPES_AT_ENTRY,
    NO_DAY_PLAN,
    STANCES_AT_ENTRY,
    EntryChoice,
    compute_scorecard,
    split_scorecard,
)
from trade_graph.domain.clock import parse_utc
from trade_graph.kernel.trade_metrics import TradeFill, summarize

D = Decimal
LOT = D("0.0001")
VERSIONS = ("dip-rebound@1", "dip-rebound@2", "slow-trend-pullback@1", NO_DAY_PLAN)
CHOICE = st.builds(EntryChoice, strategy=st.sampled_from(VERSIONS), market_type=st.sampled_from(MARKET_TYPES_AT_ENTRY),
                   stance=st.sampled_from(STANCES_AT_ENTRY))
FILL = st.tuples(st.sampled_from(["SOL/USD", "ETH/USD"]), st.booleans(), st.integers(1, 10), st.integers(90, 110),
                 st.integers(0, 12))


def _check(split, metrics) -> None:
    for rows in (split.by_strategy, split.by_market_type, split.by_stance, [row for _, row in split.by_choice]):
        assert sum(row.trades for row in rows) == metrics.trades
        assert sum(row.wins for row in rows) == metrics.wins
        assert sum(row.losses for row in rows) == metrics.losses
        assert sum(row.flat_trades for row in rows) == metrics.flat_trades
        assert sum(row.open_positions for row in rows) == len(metrics.open_positions)
        assert sum((row.result for row in rows), D("0")) + split.outside_trades == metrics.result
    document = split.document()
    assert document["totals"]["trades"] == metrics.trades
    assert len(document["entries"]) == metrics.trades + len(metrics.open_positions)


@settings(max_examples=300, deadline=None, derandomize=True)
@given(st.lists(FILL, min_size=1, max_size=30), st.data())
def test_every_dimension_of_the_split_sums_to_the_totals(steps, data) -> None:
    fills, held, at = [], {"SOL/USD": D("0"), "ETH/USD": D("0")}, START
    for index, (symbol, buy, tenths, price, fee_tenths) in enumerate(steps):
        at += timedelta(minutes=1)
        quantity = D(tenths) / 10 if buy else (held[symbol] * tenths / 10).quantize(LOT, rounding=ROUND_FLOOR)
        if quantity <= 0:
            continue
        held[symbol] += quantity if buy else -quantity
        fills.append(TradeFill(fill_ref=f"f{index}", symbol=symbol, side="buy" if buy else "sell", quantity=quantity,
                               quote_amount=quantity * price, fee=quantity * price * fee_tenths / 1000, at=at))
    cash = sum(((-fill.quote_amount if fill.side == "buy" else fill.quote_amount) - fill.fee for fill in fills),
               D("10000"))
    values = {symbol: held[symbol] * data.draw(st.integers(80, 120), label=f"end price {symbol}") * D("0.992")
              for symbol in held}
    metrics = summarize(currency="USD", starting_equity=D("10000"), fills=fills, equity_points=[],
                        ending_equity=cash + sum(values.values(), D("0")))
    choices = data.draw(st.lists(CHOICE, min_size=metrics.trades, max_size=metrics.trades), label="trade choices")
    open_choices = data.draw(st.lists(CHOICE, min_size=len(metrics.open_positions),
                                      max_size=len(metrics.open_positions)), label="open choices")
    split = split_scorecard(metrics, trade_choices=choices, open_choices=open_choices,
                            open_values=[values[position.symbol] for position in metrics.open_positions])
    _check(split, metrics)
    assert split.outside_trades == D("0")  # every dollar of the result comes from a trade or a position still held


STEP = st.tuples(
    st.sampled_from(["label", "buy", "sell", "wait"]),
    st.sampled_from(["BTC", "ETH"]),
    st.integers(min_value=-5, max_value=5),  # price move in percent from the base price
    st.integers(min_value=1, max_value=10),  # tenths of half the cash to spend, or of the coins held to sell
    st.sampled_from(["rising", "sideways", "falling", "unclear"]),
)
BASE = {"BTC": D("60000"), "ETH": D("3000")}


@settings(max_examples=25, deadline=None, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(STEP, min_size=1, max_size=16))
def test_on_ledger_records_the_split_sums_to_the_scorecard_and_reads_the_label_at_each_first_buy(steps) -> None:
    with tempfile.TemporaryDirectory() as folder:
        paper = Paper(Path(folder) / "state.sqlite", at=START)
        try:
            paper.deposit("10000")
            for coin, price in BASE.items():
                paper.mark(coin, str(price))
            cash, held, labels = D("10000"), {"BTC": D("0"), "ETH": D("0")}, {"BTC": [], "ETH": []}
            moment = START
            for kind, coin, move, tenths, word in steps:
                moment += timedelta(minutes=70 if kind == "wait" else 10)  # a wait lets earlier labels grow stale
                paper.at(moment)
                price = BASE[coin] * (100 + move) / 100
                paper.mark(coin, str(price))
                if kind == "label":
                    reason = "no_value" if word == "unclear" else None
                    insert_label(paper.database, f"{coin}/USD", word, paper.clock.now(), reason=reason)
                    labels[coin].append((paper.clock.now(), word))
                elif kind == "buy":
                    quantity = (cash / 2 * tenths / 10 / price).quantize(LOT, rounding=ROUND_FLOOR)
                    if quantity > 0:
                        fee = quantity * price * D("0.004")
                        paper.fill(f"{coin}/USD", "buy", str(quantity), str(price), fee=str(fee))
                        cash, held[coin] = cash - quantity * price - fee, held[coin] + quantity
                elif kind == "sell":
                    quantity = held[coin] if tenths == 10 else (held[coin] * tenths / 10).quantize(
                        LOT, rounding=ROUND_FLOOR)
                    if quantity > 0:
                        fee = quantity * price * D("0.008")
                        paper.fill(f"{coin}/USD", "sell", str(quantity), str(price), fee=str(fee), liquidity="taker")
                        cash, held[coin] = cash + quantity * price - fee, held[coin] - quantity
            card = compute_scorecard(paper.database, portfolio_id=PORTFOLIO, end=paper.clock.now() + timedelta(
                minutes=1))
            split = card.split
            assert split.available
            _check(split, card.metrics)
            assert split.outside_trades == D("0")
            for entry in split.document()["entries"]:
                opened = parse_utc(entry["opened_at_utc"])
                before = [(moment, word) for moment, word in labels[entry["symbol"].split("/")[0]] if moment <= opened]
                if not before:
                    assert entry["market_type"] == "missing"
                else:
                    moment, word = before[-1]
                    expected = word if opened - moment <= timedelta(hours=2) else "stale"
                    assert entry["market_type"] == expected, (entry, before)
                assert (entry["strategy_ref"], entry["stance"]) == (NO_DAY_PLAN, "not_recorded")
        finally:
            paper.close()
