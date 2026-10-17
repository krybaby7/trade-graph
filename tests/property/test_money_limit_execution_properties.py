"""Through ``Execution`` and the paper broker: random resting entries, some sized right up to the cap, then random
fills, never put more than the per-coin or total share of the account into coins (T32 acceptance).

Marks stay where they were when the entries were placed (price moves are not fills). Fills come from quotes whose
ask reaches the resting limits, in any order and with any traded volume, so the paper broker fills them at their
limits as a maker, whole or in part; then everything still resting fills. Synthetic account: USD10,000, 30% per
coin, 90% in total. No network, model, key or order.
"""

from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from tests.money_limit_support import open_account

from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure

D = Decimal
COINS = {"SOL": ("SOL/USD", D("100"), D("0.0001")), "BTC": ("BTC/USD", D("50000"), D("0.0001")),
         "ADA": ("ADA/USD", D("0.5"), D("0.0001"))}
COIN_CAP, GROSS_CAP = D("0.30"), D("0.90")

entries = st.lists(st.tuples(st.sampled_from(sorted(COINS)), st.sampled_from([60, 90, 99, 100]) | st.integers(50, 100),
                             st.one_of(st.none(), st.integers(1, 400))), min_size=1, max_size=6)
# A fill: the coin, the ask as a percentage of its highest resting limit, and the traded volume (its tenth fills).
fills = st.lists(st.tuples(st.sampled_from(sorted(COINS)), st.sampled_from([100, 95]) | st.integers(50, 100),
                           st.sampled_from(["100000000", "1"]) | st.sampled_from(["0.05", "50", "5000"])),
                 max_size=8)


def _largest(account, symbol: str, base: str, limit: Decimal) -> Decimal | None:
    """Bisect for the largest quantity the exposure check accepts (in quantity increments)."""
    policy = account.execution.authority.active_policy()
    mandate = account.execution.authority.active_mandate(account.portfolio)
    gross_cap, asset_cap, _ = account.execution.authority.limits(policy, mandate)

    def fits(units: int) -> bool:
        try:
            account.execution._assert_increase_exposure(account.portfolio, symbol, D(units) * D("0.0001"), limit,
                                                        "USD", base, gross_cap, asset_cap)
        except AuthorityDenied:
            return False
        return True

    low, high = 0, 10**9
    while low < high:
        middle = (low + high + 1) // 2
        low, high = (middle, high) if fits(middle) else (low, middle - 1)
    return None if low == 0 else D(low) * D("0.0001")


def _assert_within_caps(account) -> None:
    view = account.ledger.native_equity(account.portfolio, "USD")
    assert view.equity is not None and view.equity > 0
    total = D("0")
    for base, (_symbol, mark, _step) in COINS.items():
        value = account.held(base) * mark
        assert value <= COIN_CAP * view.equity, (base, value, view.equity)
        total += value
    assert total <= GROSS_CAP * view.equity
    assert view.cash is not None and view.cash >= 0


@settings(max_examples=25, derandomize=True, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@example(entries=[("SOL", 60, None)], fills=[("SOL", 100, "100000000")])
@example(entries=[("SOL", 90, None), ("BTC", 100, None), ("ADA", 99, None)],
         fills=[("BTC", 100, "100000000"), ("ADA", 99, "100000000"), ("SOL", 90, "100000000")])
@given(entries=entries, fills=fills)
def test_no_sequence_of_resting_entry_fills_breaches_a_cap_through_execution(entries, fills) -> None:
    with tempfile.TemporaryDirectory() as folder:
        account = open_account(Path(folder), limits=None)
        try:
            limits: dict[str, list[Decimal]] = {base: [] for base in COINS}
            for base, percent, hundredths in entries:
                symbol, mark, _step = COINS[base]
                limit = (mark * percent / 100).quantize(D("0.0001") if base == "ADA" else D("0.01"))
                quantity = _largest(account, symbol, base, limit) if hundredths is None else D(hundredths) / 100
                if quantity is None:
                    continue
                try:
                    account.authorize("enter", symbol, str(quantity), limit=str(limit))
                except (AuthorityDenied, ValidationFailure, StaleState):
                    continue
                limits[base].append(limit)
            account.dispatch()
            for base, percent, size in fills:
                symbol, _mark, _step = COINS[base]
                if not limits[base]:
                    continue
                ask = (max(limits[base]) * percent / 100).quantize(D("0.0001"))
                account.clock.advance(1)
                # A quote that reaches the limits fills them; the marks stay put (mark=False).
                account.quote(symbol, str(ask * D("0.999")), str(ask), mark=False, volume=size)
                _assert_within_caps(account)
            # Then every resting entry fills completely, at its limit: still within the caps.
            for _round in range(40):
                if not account.execution._has_outstanding(account.portfolio, sides={"buy"}):
                    break
                for base, prices in limits.items():
                    if prices:
                        account.clock.advance(1)
                        low = min(prices)
                        account.quote(COINS[base][0], str(low * D("0.999")), str(low), mark=False,
                                      volume="100000000")
                        _assert_within_caps(account)
            assert not account.execution._has_outstanding(account.portfolio, sides={"buy"})
            _assert_within_caps(account)
        finally:
            account.database.close()
