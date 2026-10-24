"""Property tests for the paper scorecard (T42) over random synthetic paper days.

Whatever the Trader does (buys and sells of two coins in partial amounts, fees in the quote currency or in the coin,
prices moving, a period starting while coins are held), the scorecard's running books agree with the ledger at both
ends, the result is exactly the trades plus what the coins still held are worth if sold, fees paid are exactly the
fills' fees, the worst fall is a fraction between 0 and 1, and a rerun gives the same document.
"""

from __future__ import annotations

import tempfile
from datetime import timedelta
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.scorecard_support import PORTFOLIO, START, Paper

from trade_graph.application.scorecard import compute_scorecard

D = Decimal
TAKER = D("0.008")
FEE = D("0.004")
LOT = D("0.0001")
BASE = {"BTC": D("60000"), "ETH": D("3000")}
STEP = st.tuples(
    st.sampled_from(["mark", "buy", "sell"]),
    st.sampled_from(["BTC", "ETH"]),
    st.integers(min_value=-5, max_value=5),  # price move in percent from the base price
    st.integers(min_value=1, max_value=10),  # tenths: of half the cash to spend, or of the coins held to sell
    st.booleans(),  # a buy's fee is charged in the coin
)


@settings(max_examples=30, deadline=None, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(STEP, min_size=1, max_size=18), st.integers(min_value=0, max_value=20))
def test_the_result_is_the_trades_plus_what_is_still_held_and_the_books_agree(steps, start_after) -> None:
    with tempfile.TemporaryDirectory() as folder:
        paper = Paper(Path(folder) / "state.sqlite", at=START)
        try:
            paper.deposit("10000")
            for coin, price in BASE.items():
                paper.mark(coin, str(price))
            cash, held = D("10000"), {"BTC": D("0"), "ETH": D("0")}
            start, fees_after_start = None, D("0")
            for index, (kind, coin, move, tenths, coin_fee) in enumerate(steps):
                paper.at(START + timedelta(minutes=index + 1))
                if index == start_after:
                    start = paper.clock.now()  # the period starts here, with whatever is held
                price = BASE[coin] * (100 + move) / 100
                symbol = f"{coin}/USD"
                if kind == "mark":
                    paper.mark(coin, str(price))
                    continue
                if kind == "buy":
                    quantity = (cash / 2 * tenths / 10 / price).quantize(LOT, rounding=ROUND_FLOOR)
                    if quantity <= 0:
                        continue
                    if coin_fee:
                        fee, fee_value = quantity * FEE, quantity * FEE * price
                        paper.fill(symbol, "buy", str(quantity), str(price), fee=str(fee), fee_asset=coin)
                        cash, held[coin] = cash - quantity * price, held[coin] + quantity - fee
                    else:
                        fee_value = quantity * price * FEE
                        paper.fill(symbol, "buy", str(quantity), str(price), fee=str(fee_value))
                        cash, held[coin] = cash - quantity * price - fee_value, held[coin] + quantity
                else:
                    quantity = held[coin] if tenths == 10 else (held[coin] * tenths / 10).quantize(
                        LOT, rounding=ROUND_FLOOR)
                    if quantity <= 0:
                        continue
                    fee_value = quantity * price * TAKER
                    paper.fill(symbol, "sell", str(quantity), str(price), fee=str(fee_value), liquidity="taker")
                    cash, held[coin] = cash + quantity * price - fee_value, held[coin] - quantity
                if start is not None and paper.clock.now() > start:  # a fill at the start instant is carried in
                    fees_after_start += fee_value
            end = paper.clock.now() + timedelta(minutes=1)
            card = compute_scorecard(paper.database, portfolio_id=PORTFOLIO, end=end, start=start)
            metrics = card.metrics
            # The ledger's own equity at the end, and the cash and coins this test kept itself.
            assert card.ending_equity_marked == paper.ledger.native_equity(PORTFOLIO, "USD").equity
            prices = {coin: card_mark for coin, card_mark in _last_marks(paper).items()}
            assert card.ending_equity_marked == cash + sum((held[coin] * prices[coin] for coin in held), D("0"))
            # Coins still held are valued as sold at their mark with the taker fee.
            assert card.exit_fees == sum((held[coin] * prices[coin] * TAKER for coin in held), D("0"))
            assert metrics.ending_equity == card.ending_equity_marked - card.exit_fees
            # The result is exactly the trades plus the open positions as if sold.
            open_value = sum((position.sold + position.quantity * prices[position.symbol.split("/")[0]] * (1 - TAKER)
                              - position.bought - position.fees for position in metrics.open_positions), D("0"))
            assert metrics.result == sum((trip.result for trip in metrics.round_trips), D("0")) + open_value
            assert {p.symbol.split("/")[0]: p.quantity for p in metrics.open_positions} == {
                coin: quantity for coin, quantity in held.items() if quantity}
            if start is not None:
                assert metrics.fees_paid == fees_after_start
            assert D("0") <= metrics.worst_drawdown.fraction < 1
            assert card.document()["scorecard_sha256"] == compute_scorecard(
                paper.database, portfolio_id=PORTFOLIO, end=end, start=start).document()["scorecard_sha256"]
        finally:
            paper.close()


def _last_marks(paper: Paper) -> dict[str, Decimal]:
    rows = paper.database.execute(
        "SELECT asset, mark FROM valuation_marks WHERE portfolio_id = ? ORDER BY observed_at, rowid",
        (PORTFOLIO,)).fetchall()
    return {row["asset"]: Decimal(row["mark"]) for row in rows}
