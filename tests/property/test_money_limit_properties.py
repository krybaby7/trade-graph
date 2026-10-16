"""Random resting entries and random fill sequences never breach the per-coin or total share (T32 acceptance).

The model: an account of cash and coins at fixed marks (price moves are not fills; D138 orders no sell-off when a
coin grows past its share). Entries are proposed one by one and checked with ``check_entry``, counting every
resting entry as if filled; accepted ones rest. Fills then take any part of any resting entry at any price at or
inside its limit and pay any fee up to the reserve rate, in any order, interleaved with new proposals. After every
step each coin and all coins together stay within their caps of the account's value. Synthetic numbers only.

``tests/integration/test_money_limits.py`` runs the same claim through ``Execution`` and the paper broker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from hypothesis import example, given, settings
from hypothesis import strategies as st

from trade_graph.domain.errors import AuthorityDenied
from trade_graph.kernel.money_limits import check_entry, entry_value

D = Decimal
COINS = ("SOL", "BTC", "ETH")


@dataclass
class Resting:
    coin: str
    limit: Decimal
    remaining: Decimal


@dataclass
class Account:
    cash: Decimal
    marks: dict[str, Decimal]
    asks: dict[str, Decimal]
    held: dict[str, Decimal] = field(default_factory=dict)
    resting: list[Resting] = field(default_factory=list)

    def value(self) -> Decimal:
        return self.cash + sum((quantity * self.marks[coin] for coin, quantity in self.held.items()), D("0"))

    def coin_value(self, coin: str) -> Decimal:
        return self.held.get(coin, D("0")) * self.marks[coin]

    def entry(self, coin: str, quantity: Decimal, limit: Decimal):
        return entry_value(coin, quantity, order_price=limit, reference=self.asks[coin], mark=self.marks[coin])


# A limit as a percentage of the mark: resting below the market, at it, or marketable above it.
limits = st.one_of(st.sampled_from([50, 90, 99, 100, 101, 110, 130]), st.integers(50, 130))
steps = st.lists(st.one_of(
    st.tuples(st.just("enter"), st.sampled_from(COINS), limits, st.integers(1, 4000)),
    # The largest entry the check accepts: a Trader sizing up to the cap.
    st.tuples(st.just("enter"), st.sampled_from(COINS), limits, st.just(None)),
    st.tuples(st.just("fill"), st.integers(0, 50), st.sampled_from([100, 50, 1]) | st.integers(1, 100),
              st.sampled_from([0]) | st.integers(0, 40), st.sampled_from([100, 0]) | st.integers(0, 100)),
), min_size=1, max_size=40)
SAME = (100, 100, 100)
FLAT = (0, 0, 0)


@settings(max_examples=400, derandomize=True, deadline=None)
# A full-size entry at the market whose fill pays the fee: the cap must be measured after the fee.
@example(steps=[("enter", "SOL", 100, None)], marks=SAME, spreads=FLAT, caps=(20, 80), fee_basis_points=100,
         cash=1000)
# A resting buy below the market counts at what it is worth once filled, not at its cheaper limit.
@example(steps=[("enter", "SOL", 50, None), ("fill", 0, 100, 0, 100)], marks=SAME, spreads=FLAT, caps=(20, 80),
         fee_basis_points=40, cash=1000)
# A marketable entry on one coin lowers the equity left for another coin already at its share.
@example(steps=[("enter", "SOL", 90, None), ("enter", "BTC", 115, None)], marks=SAME, spreads=FLAT, caps=(5, 9),
         fee_basis_points=0, cash=1000)
@given(steps=steps,
       marks=st.tuples(*(st.sampled_from([100, 5000]) | st.integers(100, 100000) for _ in COINS)),
       spreads=st.tuples(*(st.sampled_from([0]) | st.integers(0, 100) for _ in COINS)),
       caps=st.tuples(st.integers(5, 100), st.integers(5, 100)),
       fee_basis_points=st.sampled_from([0, 40, 80]) | st.integers(0, 100),
       cash=st.integers(1000, 100000))
def test_no_sequence_of_resting_entry_fills_breaches_a_cap(steps, marks, spreads, caps, fee_basis_points,
                                                           cash) -> None:
    coin_cap, gross_cap = D(min(caps)) / 100, D(max(caps)) / 100
    fee_rate = D(fee_basis_points) / 10000
    account = Account(cash=D(cash), marks={coin: D(mark) / 100 for coin, mark in zip(COINS, marks, strict=True)},
                      asks={})
    account.asks = {coin: account.marks[coin] * (1 + D(spread) / 10000)
                    for coin, spread in zip(COINS, spreads, strict=True)}

    def accepts(coin: str, quantity: Decimal, limit: Decimal) -> bool:
        pending = [account.entry(item.coin, item.remaining, item.limit) for item in account.resting]
        try:
            held = {other: account.coin_value(other) for other in account.held}
            held[coin] = account.held.get(coin, D("0")) * max(account.marks[coin], account.asks[coin], limit)
            check_entry(equity=account.value(), inventory=sum((account.coin_value(c) for c in COINS), D("0")),
                        held=held, entries=pending, proposed=account.entry(coin, quantity, limit), fee_rate=fee_rate,
                        coin_cap=coin_cap, gross_cap=gross_cap)
        except AuthorityDenied:
            return False
        return True

    for step in steps:
        if step[0] == "enter":
            _, coin, limit_percent, hundredths = step
            limit = (account.marks[coin] * limit_percent / 100).quantize(D("0.01"))
            if hundredths is None:  # bisect for the largest accepted quantity, in steps of 0.000001
                low, high = 0, 10**12
                while low < high:
                    middle = (low + high + 1) // 2
                    low, high = (middle, high) if accepts(coin, D(middle) / 10**6, limit) else (low, middle - 1)
                if low == 0:
                    continue
                quantity = D(low) / 10**6
            else:
                quantity = D(hundredths) / 100
                if not accepts(coin, quantity, limit):
                    continue
            account.resting.append(Resting(coin, limit, quantity))
        else:
            _, index, percent, discount, fee_share = step
            if not account.resting:
                continue
            order = account.resting[index % len(account.resting)]
            quantity = max(D("0.000001"), (order.remaining * percent / 100).quantize(D("0.000001")))
            quantity = min(quantity, order.remaining)
            price = order.limit * (100 - discount) / 100  # at or inside the limit, never worse
            fee = price * quantity * fee_rate * fee_share / 100
            account.cash -= price * quantity + fee
            account.held[order.coin] = account.held.get(order.coin, D("0")) + quantity
            order.remaining -= quantity
            if order.remaining == 0:
                account.resting.remove(order)
        value = account.value()
        assert account.cash >= 0
        for coin in COINS:
            assert account.coin_value(coin) <= coin_cap * value
        assert sum((account.coin_value(coin) for coin in COINS), D("0")) <= gross_cap * value
    # Then every remaining resting entry fills in full at its limit and the worst fee: still within the caps.
    for order in list(account.resting):
        account.cash -= order.limit * order.remaining * (1 + fee_rate)
        account.held[order.coin] = account.held.get(order.coin, D("0")) + order.remaining
    value = account.value()
    for coin in COINS:
        assert account.coin_value(coin) <= coin_cap * value
    assert sum((account.coin_value(coin) for coin in COINS), D("0")) <= gross_cap * value
