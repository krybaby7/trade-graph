"""T28 golden cases for the shared cautious fill and protective-exit engine (synthetic bars and quotes)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trade_graph.contracts.candles import Candle
from trade_graph.contracts.fills import FillPolicy, ProtectiveExit, SimOrder
from trade_graph.contracts.models import InstrumentRules, Observation
from trade_graph.domain.errors import ValidationFailure
from trade_graph.kernel.cost_profiles import KRAKEN_AFTER_10K, KRAKEN_STARTING, OKX_EEA, load_cost_profiles
from trade_graph.kernel.fills import (
    BAR_REPLAY_POLICY,
    PAPER_QUOTE_POLICY,
    check_order_rules,
    fill_order_on_candle,
    fill_order_on_observation,
    trigger_exit_on_candle,
    trigger_exit_on_observation,
)

PLACED = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
BOOK = load_cost_profiles()
KRAKEN = BOOK.get(KRAKEN_STARTING)
PROFILES = [BOOK.get(KRAKEN_STARTING), BOOK.get(KRAKEN_AFTER_10K), BOOK.get(OKX_EEA)]
NO_SLIP = BAR_REPLAY_POLICY.model_copy(update={
    "policy_id": "bar-replay-no-slippage", "entry_slippage_rate": Decimal("0"), "exit_slippage_rate": Decimal("0"),
})


def _bar(open_, high, low, close, *, volume="10", start=PLACED, minutes=15) -> Candle:
    return Candle(
        symbol="BTC/USD", source_pair="XBTUSD", interval_minutes=minutes,
        open_time_utc=start, close_time_utc=start + timedelta(minutes=minutes),
        available_at_utc=start + timedelta(minutes=minutes, seconds=5),
        open=Decimal(open_), high=Decimal(high), low=Decimal(low), close=Decimal(close),
        volume=Decimal(volume), trade_count=3, source="synthetic",
    )


def _limit(side, price, *, quantity="0.5", placement="105", eligible=PLACED) -> SimOrder:
    return SimOrder(
        order_id="entry-1", symbol="BTC/USD", side=side, kind="limit", quantity=Decimal(quantity),
        remaining=Decimal(quantity), limit_price=Decimal(price),
        placement_price=None if placement is None else Decimal(placement), eligible_after_utc=eligible,
    )


def _market(side, *, quantity="0.5", eligible=PLACED) -> SimOrder:
    return SimOrder(
        order_id="entry-m", symbol="BTC/USD", side=side, kind="market", quantity=Decimal(quantity),
        remaining=Decimal(quantity), eligible_after_utc=eligible,
    )


def _exit(stop="95", target="110", *, quantity="0.5", eligible=PLACED) -> ProtectiveExit:
    return ProtectiveExit(
        exit_id="exit-1", symbol="BTC/USD", quantity=Decimal(quantity), stop_price=Decimal(stop),
        take_profit_price=None if target is None else Decimal(target), eligible_after_utc=eligible,
    )


def _quote(bid, ask, *, size="1", volume=None, observation_id="q-1", at=PLACED) -> Observation:
    return Observation(
        observation_id=observation_id, venue="paper", symbol="BTC/USD", event_time_utc=at, available_at_utc=at,
        bid=bid, ask=ask, bid_size=size, ask_size=size, volume=volume, kind="quote", source="synthetic",
    )


# Golden case 1: a touch-only bar does not fill.

@pytest.mark.parametrize("profile", PROFILES, ids=lambda p: p.profile_id)
def test_touch_only_bar_does_not_fill_a_resting_buy_limit(profile) -> None:
    order = _limit("buy", "100")
    assert fill_order_on_candle(order, _bar("104", "106", "100", "103"), profile=profile,
                                policy=BAR_REPLAY_POLICY) is None


def test_touch_only_bar_does_not_fill_a_resting_sell_limit() -> None:
    order = _limit("sell", "110", placement="105")
    assert fill_order_on_candle(order, _bar("104", "110", "103", "108"), profile=KRAKEN,
                                policy=BAR_REPLAY_POLICY) is None


@pytest.mark.parametrize(("profile", "fee"), [
    (PROFILES[0], Decimal("0.2")),    # 100 * 0.5 * 0.0040
    (PROFILES[1], Decimal("0.11")),   # 100 * 0.5 * 0.0022
    (PROFILES[2], Decimal("0.05")),   # 100 * 0.5 * 0.0010
], ids=lambda value: getattr(value, "profile_id", str(value)))
def test_trade_through_fills_at_the_limit_with_the_profile_maker_fee(profile, fee) -> None:
    fill = fill_order_on_candle(_limit("buy", "100"), _bar("104", "106", "99.9", "101"), profile=profile,
                                policy=BAR_REPLAY_POLICY)
    assert fill is not None
    assert (fill.price, fill.quantity, fill.fee, fill.fee_asset) == (Decimal("100"), Decimal("0.5"), fee, "USD")
    assert (fill.liquidity, fill.reason, fill.side) == ("maker", "limit_trade_through", "buy")
    assert fill.filled_at_utc == PLACED + timedelta(minutes=15)  # known only once the bar is complete
    assert fill.heuristic is True
    assert fill.cost_profile_ref == profile.ref and fill.cost_profile_sha256 == profile.content_sha256


def test_limit_fill_takes_at_most_the_participation_share_of_bar_volume() -> None:
    fill = fill_order_on_candle(_limit("buy", "100", quantity="2"), _bar("104", "106", "99", "101", volume="3"),
                                profile=KRAKEN, policy=BAR_REPLAY_POLICY)
    assert fill is not None and fill.quantity == Decimal("0.3")  # 10% of 3
    quiet = _bar("99", "99", "99", "99", volume="0")
    assert fill_order_on_candle(_limit("buy", "100"), quiet, profile=KRAKEN, policy=BAR_REPLAY_POLICY) is None


def test_gap_below_a_buy_limit_fills_at_the_limit_never_the_better_open() -> None:
    fill = fill_order_on_candle(_limit("buy", "100"), _bar("97", "98", "96", "97"), profile=KRAKEN,
                                policy=BAR_REPLAY_POLICY)
    assert fill is not None and fill.price == Decimal("100") and fill.liquidity == "maker"


def test_bar_that_opened_before_the_order_cannot_fill_it() -> None:
    """No hindsight: the bar whose close the decision used is never a fill candidate."""
    decided_on = _bar("104", "106", "99", "101")
    order = _limit("buy", "100", eligible=decided_on.close_time_utc)
    assert fill_order_on_candle(order, decided_on, profile=KRAKEN, policy=BAR_REPLAY_POLICY) is None
    later = _bar("101", "102", "99.5", "100", start=decided_on.close_time_utc)
    assert fill_order_on_candle(order, later, profile=KRAKEN, policy=BAR_REPLAY_POLICY) is not None


def test_limit_order_on_candles_needs_its_placement_price() -> None:
    with pytest.raises(ValidationFailure, match="placement price"):
        fill_order_on_candle(_limit("buy", "100", placement=None), _bar("104", "106", "99", "101"),
                             profile=KRAKEN, policy=BAR_REPLAY_POLICY)


def test_marketable_buy_limit_pays_the_taker_fee_and_never_beats_the_open() -> None:
    order = _limit("buy", "101", placement="100")
    fill = fill_order_on_candle(order, _bar("100.5", "103", "100.2", "102"), profile=KRAKEN,
                                policy=BAR_REPLAY_POLICY, price_increment=Decimal("0.1"))
    assert fill is not None
    # 100.5 * 1.001 = 100.6005, rounded up to the 0.1 tick against the buyer.
    assert (fill.price, fill.liquidity, fill.reason) == (Decimal("100.7"), "taker", "marketable_limit")
    assert fill.fee == Decimal("100.7") * Decimal("0.5") * Decimal("0.008")
    assert fill.filled_at_utc == PLACED
    capped = fill_order_on_candle(order, _bar("100.95", "103", "100.9", "102"), profile=KRAKEN,
                                  policy=BAR_REPLAY_POLICY)
    assert capped is not None and capped.price == Decimal("101")  # never above its limit
    ran_away = fill_order_on_candle(order, _bar("102", "104", "101", "103"), profile=KRAKEN,
                                    policy=BAR_REPLAY_POLICY)
    assert ran_away is None  # opened above the limit, then only touched it: it rests


def test_market_order_fills_at_the_next_open_with_slippage_and_participation() -> None:
    fill = fill_order_on_candle(_market("buy", quantity="2"), _bar("100", "101", "99", "100.5", volume="5"),
                                profile=KRAKEN, policy=BAR_REPLAY_POLICY, price_increment=Decimal("0.01"))
    assert fill is not None
    assert (fill.price, fill.quantity, fill.liquidity, fill.reason) == (Decimal("100.1"), Decimal("0.5"),
                                                                        "taker", "market")
    assert fill.fee == Decimal("100.1") * Decimal("0.5") * Decimal("0.008")
    sell = fill_order_on_candle(_market("sell"), _bar("100", "101", "99", "100.5"), profile=KRAKEN,
                                policy=BAR_REPLAY_POLICY, price_increment=Decimal("0.01"))
    assert sell is not None and sell.price == Decimal("99.9")


# Golden case 2: a gap through a stop fills at the worse open.

@pytest.mark.parametrize("profile", PROFILES, ids=lambda p: p.profile_id)
def test_gap_through_a_stop_fills_at_the_worse_open(profile) -> None:
    exit_ = _exit("95", "110")
    gap = _bar("90", "92", "88", "91")
    exact = trigger_exit_on_candle(exit_, gap, armed_in_this_candle=False, profile=profile, policy=NO_SLIP)
    assert exact is not None
    assert (exact.price, exact.reason, exact.liquidity, exact.side) == (Decimal("90"), "stop_gap", "taker", "sell")
    assert exact.fee == Decimal("90") * Decimal("0.5") * profile.taker_rate
    slipped = trigger_exit_on_candle(exit_, gap, armed_in_this_candle=False, profile=profile,
                                     policy=BAR_REPLAY_POLICY)
    assert slipped is not None and slipped.price == Decimal("89.91") and slipped.reference_price == Decimal("90")
    assert slipped.quantity == Decimal("0.5")


def test_stop_touch_triggers_at_the_stop_with_slippage() -> None:
    fill = trigger_exit_on_candle(_exit("95", "110"), _bar("100", "101", "95", "96"), armed_in_this_candle=False,
                                  profile=KRAKEN, policy=BAR_REPLAY_POLICY, price_increment=Decimal("0.1"))
    assert fill is not None
    # 95 * 0.999 = 94.905, rounded down to the 0.1 tick against the seller.
    assert (fill.price, fill.reason, fill.reference_price) == (Decimal("94.9"), "stop", Decimal("95"))


# Golden case 3: a bar hitting both exits takes the stop.

@pytest.mark.parametrize("profile", PROFILES, ids=lambda p: p.profile_id)
def test_bar_hitting_both_exits_takes_the_stop(profile) -> None:
    fill = trigger_exit_on_candle(_exit("95", "110"), _bar("100", "111", "94", "105"), armed_in_this_candle=False,
                                  profile=profile, policy=NO_SLIP)
    assert fill is not None
    assert (fill.price, fill.reason) == (Decimal("95"), "stop_both_hit")
    gap_up = trigger_exit_on_candle(_exit("95", "110"), _bar("112", "113", "94", "100"),
                                    armed_in_this_candle=False, profile=profile, policy=NO_SLIP)
    assert gap_up is not None and gap_up.reason == "stop_both_hit" and gap_up.price == Decimal("95")


def test_take_profit_needs_a_trade_through_and_never_gains_from_a_gap() -> None:
    exit_ = _exit("95", "110")
    assert trigger_exit_on_candle(exit_, _bar("105", "110", "104", "109"), armed_in_this_candle=False,
                                  profile=KRAKEN, policy=BAR_REPLAY_POLICY) is None
    fill = trigger_exit_on_candle(exit_, _bar("115", "116", "112", "114"), armed_in_this_candle=False,
                                  profile=KRAKEN, policy=BAR_REPLAY_POLICY)
    assert fill is not None
    assert (fill.price, fill.reason, fill.liquidity) == (Decimal("109.89"), "take_profit", "taker")
    assert fill.fee == Decimal("109.89") * Decimal("0.5") * Decimal("0.008")


def test_stop_only_exit_and_quiet_bar() -> None:
    assert trigger_exit_on_candle(_exit("95", None), _bar("100", "130", "96", "120"), armed_in_this_candle=False,
                                  profile=KRAKEN, policy=BAR_REPLAY_POLICY) is None


def test_exit_armed_inside_the_bar_takes_only_the_stop() -> None:
    """The entry filled somewhere inside this bar: a later high is unknown, a later low is assumed."""
    exit_ = _exit("95", "110", eligible=PLACED + timedelta(minutes=15))
    high_only = _bar("100", "112", "96", "111")
    assert trigger_exit_on_candle(exit_, high_only, armed_in_this_candle=True, profile=KRAKEN,
                                  policy=NO_SLIP) is None
    fell = trigger_exit_on_candle(exit_, _bar("100", "112", "94", "99"), armed_in_this_candle=True,
                                  profile=KRAKEN, policy=NO_SLIP)
    assert fell is not None and (fell.price, fell.reason) == (Decimal("95"), "stop_entry_bar")
    # Not armed in this bar and armed later than its open: not a candidate at all.
    assert trigger_exit_on_candle(exit_, _bar("100", "112", "94", "99"), armed_in_this_candle=False,
                                  profile=KRAKEN, policy=NO_SLIP) is None


def test_exit_eligibility_follows_its_arming_time() -> None:
    exit_ = _exit("95", "110", eligible=PLACED + timedelta(minutes=15))
    next_bar = _bar("100", "101", "94", "99", start=PLACED + timedelta(minutes=15))
    fill = trigger_exit_on_candle(exit_, next_bar, armed_in_this_candle=False, profile=KRAKEN, policy=NO_SLIP)
    assert fill is not None and fill.reason == "stop"


# Quotes: the paper broker path.

def test_quote_limit_fills_when_the_book_would_cross_and_not_before() -> None:
    order = _limit("buy", "100", placement=None)
    assert fill_order_on_observation(order, _quote("99", "100.1"), profile=KRAKEN, policy=PAPER_QUOTE_POLICY) is None
    fill = fill_order_on_observation(order, _quote("99", "100", volume="5"), profile=KRAKEN,
                                     policy=PAPER_QUOTE_POLICY)
    assert fill is not None
    assert (fill.price, fill.quantity, fill.liquidity, fill.reason) == (Decimal("100"), Decimal("0.5"), "maker",
                                                                        "limit_book_cross")
    assert fill.fee == Decimal("0.2") and fill.heuristic is False
    unknown_volume = fill_order_on_observation(order, _quote("99", "99.5"), profile=KRAKEN,
                                               policy=PAPER_QUOTE_POLICY)
    assert unknown_volume is not None and unknown_volume.quantity == Decimal("0.25") and unknown_volume.heuristic


def test_quote_market_order_takes_displayed_size_at_the_touch() -> None:
    fill = fill_order_on_observation(_market("buy"), _quote("99", "100", size="0.2"), profile=KRAKEN,
                                     policy=PAPER_QUOTE_POLICY)
    assert fill is not None
    assert (fill.price, fill.quantity, fill.liquidity) == (Decimal("100"), Decimal("0.2"), "taker")
    assert fill.reference_price == Decimal("100") and fill.event_ref == "observation:q-1"


def test_quote_eligibility_and_exclusion() -> None:
    order = SimOrder(order_id="o", symbol="BTC/USD", side="buy", kind="market", quantity=Decimal("1"),
                     remaining=Decimal("1"), eligible_after_utc=PLACED + timedelta(seconds=1),
                     excluded_event_ref="observation:decided")
    assert fill_order_on_observation(order, _quote("99", "100"), profile=KRAKEN, policy=PAPER_QUOTE_POLICY) is None
    late = PLACED + timedelta(seconds=2)
    excluded = _quote("99", "100", observation_id="decided", at=late)
    assert fill_order_on_observation(order, excluded, profile=KRAKEN, policy=PAPER_QUOTE_POLICY) is None
    assert fill_order_on_observation(order, _quote("99", "100", at=late), profile=KRAKEN,
                                     policy=PAPER_QUOTE_POLICY) is not None


def test_quote_stop_gap_fills_below_the_gapped_bid_with_slippage() -> None:
    fill = trigger_exit_on_observation(_exit("95", None), _quote("90", "91"), profile=KRAKEN,
                                       policy=PAPER_QUOTE_POLICY, price_increment=Decimal("0.1"))
    assert fill is not None
    # Bid 90 * 0.999 = 89.91, rounded down to the tick: worse than both the stop and the gapped bid.
    assert (fill.price, fill.reason, fill.reference_price) == (Decimal("89.9"), "stop_gap", Decimal("90"))
    assert fill.fee == Decimal("89.9") * Decimal("0.5") * Decimal("0.008")
    assert trigger_exit_on_observation(_exit("95", None), _quote(None, "91"), profile=KRAKEN,
                                       policy=PAPER_QUOTE_POLICY) is None  # no bid, no invented price


def test_quote_take_profit_needs_the_bid_through_the_target() -> None:
    exit_ = _exit("95", "110")
    assert trigger_exit_on_observation(exit_, _quote("110", "110.2"), profile=KRAKEN,
                                       policy=PAPER_QUOTE_POLICY) is None
    fill = trigger_exit_on_observation(exit_, _quote("111", "111.2"), profile=KRAKEN, policy=PAPER_QUOTE_POLICY)
    assert fill is not None and (fill.price, fill.reason) == (Decimal("109.89"), "take_profit")


# Contracts: Decimal only, long-only exits, exact minimums.

def test_contracts_refuse_floats_and_inconsistent_orders() -> None:
    with pytest.raises(ValidationError):
        SimOrder(order_id="o", symbol="BTC/USD", side="buy", kind="limit", quantity=0.5, remaining=Decimal("0.5"),
                 limit_price=Decimal("1"), eligible_after_utc=PLACED)
    with pytest.raises(ValidationError):
        SimOrder(order_id="o", symbol="BTC/USD", side="buy", kind="limit", quantity=Decimal("1"),
                 remaining=Decimal("1"), eligible_after_utc=PLACED)
    with pytest.raises(ValidationError):
        SimOrder(order_id="o", symbol="BTC/USD", side="buy", kind="market", quantity=Decimal("1"),
                 remaining=Decimal("2"), eligible_after_utc=PLACED)
    with pytest.raises(ValidationError):
        _exit("95", "94")  # a take-profit must sit above the stop on a long position
    with pytest.raises(ValidationError):
        ProtectiveExit(exit_id="x", symbol="BTC/USD", side="buy", quantity=Decimal("1"), stop_price=Decimal("1"),
                       eligible_after_utc=PLACED)
    with pytest.raises(ValidationError):
        FillPolicy(policy_id="p", revision=1, participation=Decimal("0"), exit_slippage_rate=Decimal("0"),
                   entry_slippage_rate=Decimal("0"))


def test_order_rules_check_minimums_and_increments_exactly() -> None:
    rules = InstrumentRules(venue="kraken", symbol="BTC/USD", base_asset="BTC", quote_asset="USD",
                            price_increment="0.1", quantity_increment="0.00000001", min_quantity="0.0001",
                            min_notional="0.5")
    check_order_rules(_limit("buy", "100", quantity="0.005"), rules)
    for order, message in [
        (_limit("buy", "100", quantity="0.00009"), "minimum quantity"),
        (_limit("buy", "100", quantity="0.00499999"), "minimum notional"),
        (_limit("buy", "100", quantity="0.000100001"), "quantity increment"),
        (_limit("buy", "100.05", quantity="0.01"), "price increment"),
        (_limit("buy", "100", quantity="0.01").model_copy(update={"symbol": "ETH/USD"}), "symbol"),
    ]:
        with pytest.raises(ValidationFailure, match=message):
            check_order_rules(order, rules)
    with pytest.raises(ValidationFailure, match="placement price"):
        check_order_rules(_market("buy", quantity="0.01"), rules)
    check_order_rules(_market("buy", quantity="0.01"), rules, reference_price=Decimal("100"))


def test_fill_identity_records_profile_and_policy() -> None:
    fill = trigger_exit_on_candle(_exit(), _bar("100", "101", "94", "99"), armed_in_this_candle=False,
                                  profile=KRAKEN, policy=BAR_REPLAY_POLICY)
    assert fill is not None
    assert fill.cost_profile_ref == "kraken-spot-starting-tier@1"
    assert fill.fill_policy_ref == BAR_REPLAY_POLICY.ref and fill.fill_policy_sha256 == BAR_REPLAY_POLICY.content_sha256
    assert fill.event_ref == "synthetic:XBTUSD:15:" + str(int(PLACED.timestamp()))
    assert fill.notional == fill.price * fill.quantity
