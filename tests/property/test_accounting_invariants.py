"""Generated independent conservation checks for native accounting and cash flows."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from trade_graph.contracts.models import FillRecord
from trade_graph.kernel.books import Books, apply_fill, deposit, mark_equity, performance, withdraw

START = datetime(2026, 1, 1, tzinfo=UTC)


def _at(seconds):
    return (START + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _fill(side, quantity, price, fee, index):
    return FillRecord(
        venue="paper", account_id="synthetic", trade_id=f"fill-{index}", symbol="TEST/USD", intent_id=None,
        side=side, quantity=quantity, price=price, fee_amount=fee, fee_asset="USD", liquidity="taker",
        filled_at_utc=START + timedelta(seconds=index), heuristic=False,
    )


@settings(max_examples=100, derandomize=True, deadline=None)
@given(
    lots=st.lists(st.tuples(st.integers(1, 12), st.integers(100, 10000)), min_size=1, max_size=8),
    exit_cents=st.integers(100, 10000),
    fee_bps=st.integers(-10, 50),
)
def test_fifo_split_roundtrip_conserves_cash_inventory_and_native_postings(lots, exit_cents, fee_bps):
    books = Books()
    initial = Decimal("100000")
    deposit(books, "USD", initial, _at(0), "initial")
    rate = Decimal(fee_bps) / Decimal("10000")
    cost = Decimal("0")
    total_quantity = sum(q for q, _ in lots)
    for index, (quantity, cents) in enumerate(lots, 1):
        price = Decimal(cents) / 100
        fee = quantity * price * rate
        cost += quantity * price + fee
        apply_fill(books, _fill("buy", quantity, price, fee, index), base_asset="TEST", quote_asset="USD",
                   lot_id=f"lot-{index}", at=_at(index))
    # Reverse chunk sizes to dispose across lot boundaries rather than undoing
    # each original fill. Expected cash uses total notionals, independently of FIFO.
    exit_price = Decimal(exit_cents) / 100
    for index, (quantity, _) in enumerate(reversed(lots), len(lots) + 1):
        fee = quantity * exit_price * rate
        apply_fill(books, _fill("sell", quantity, exit_price, fee, index), base_asset="TEST", quote_asset="USD",
                   lot_id="unused", at=_at(index))
    expected_profit = total_quantity * exit_price * (1 - rate) - cost
    assert books.cash_amount("USD") == initial + expected_profit
    assert all(lot.open_quantity() == 0 and lot.open_cost() == 0 for lot in books.lots)
    realized = sum((disposal.realized for lot in books.lots for disposal in lot.disposals), Decimal("0"))
    assert realized == expected_profit
    for group in books.groups:
        for asset in {posting.asset for posting in group}:
            assert sum((posting.amount for posting in group if posting.asset == asset), Decimal("0")) == 0
    assert mark_equity(books, reporting="USD", marks=[], rates=[], at=_at(100)).equity == initial + expected_profit


@settings(max_examples=100, derandomize=True, deadline=None)
@given(flows=st.lists(st.integers(-100000, 100000), min_size=1, max_size=30))
def test_arbitrary_external_cash_flows_never_become_profit(flows):
    books = Books()
    deposit(books, "USD", Decimal("100000"), _at(0), "initial")
    start = mark_equity(books, reporting="USD", marks=[], rates=[], at=_at(0))
    net_flows = Decimal("0")
    for index, cents in enumerate(flows, 1):
        amount = Decimal(cents) / 100
        if amount > 0:
            deposit(books, "USD", amount, _at(index), f"flow-{index}")
        elif amount < 0:
            withdraw(books, "USD", -amount, _at(index), f"flow-{index}")
        net_flows += amount
    end = mark_equity(books, reporting="USD", marks=[], rates=[], at=_at(100))
    result = performance(books, reporting="USD", marks=[], rates=[], start=_at(0), end=_at(100),
                         start_view=start, end_view=end)
    assert end.equity == Decimal("100000") + net_flows
    assert result.trading_pnl == result.economic_pnl == result.alpha == 0
