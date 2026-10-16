"""Generated bars: the fill engine never fills on a touch, never beats a limit and always assumes the stop."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from trade_graph.contracts.candles import Candle
from trade_graph.contracts.fills import ProtectiveExit, SimOrder
from trade_graph.kernel.cost_profiles import load_cost_profiles
from trade_graph.kernel.fills import BAR_REPLAY_POLICY, fill_order_on_candle, trigger_exit_on_candle

START = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
BOOK = load_cost_profiles()
PROFILE_IDS = st.sampled_from(sorted(BOOK.ids()))
CENTS = st.integers(5000, 15000)


@st.composite
def bars(draw):
    prices = sorted(draw(st.lists(CENTS, min_size=4, max_size=4)))
    low, high = prices[0], prices[3]
    open_, close = draw(st.permutations([prices[1], prices[2]]))
    volume = Decimal(draw(st.integers(0, 500))) / Decimal(10)
    return Candle(symbol="BTC/USD", source_pair="XBTUSD", interval_minutes=15, open_time_utc=START,
                  close_time_utc=START + timedelta(minutes=15), available_at_utc=START + timedelta(minutes=15),
                  open=Decimal(open_) / 100, high=Decimal(high) / 100, low=Decimal(low) / 100,
                  close=Decimal(close) / 100, volume=volume, trade_count=1, source="synthetic")


@settings(max_examples=300, derandomize=True, deadline=None)
@given(bar=bars(), limit=CENTS, side=st.sampled_from(["buy", "sell"]), profile_id=PROFILE_IDS,
       quantity=st.integers(1, 400))
def test_resting_limits_fill_only_on_a_trade_through_and_never_better_than_the_limit(
        bar, limit, side, profile_id, quantity) -> None:
    limit_price = Decimal(limit) / 100
    # Resting: a buy below and a sell above the placement price.
    placement = limit_price + 1 if side == "buy" else limit_price - 1
    order = SimOrder(order_id="o", symbol="BTC/USD", side=side, kind="limit", quantity=Decimal(quantity) / 100,
                     remaining=Decimal(quantity) / 100, limit_price=limit_price, placement_price=placement,
                     eligible_after_utc=START)
    profile = BOOK.get(profile_id)
    fill = fill_order_on_candle(order, bar, profile=profile, policy=BAR_REPLAY_POLICY)
    through = bar.low < limit_price if side == "buy" else bar.high > limit_price
    if not through or bar.volume == 0:
        assert fill is None
        return
    assert fill is not None
    assert fill.price == limit_price and fill.liquidity == "maker"
    assert 0 < fill.quantity <= order.remaining
    assert fill.quantity <= bar.volume * BAR_REPLAY_POLICY.participation
    assert fill.fee == fill.price * fill.quantity * profile.maker_rate


@settings(max_examples=300, derandomize=True, deadline=None)
@given(bar=bars(), stop=CENTS, gap=st.integers(1, 3000), profile_id=PROFILE_IDS, armed_here=st.booleans())
def test_protective_exits_assume_the_stop_and_never_gain_from_a_gap(bar, stop, gap, profile_id, armed_here) -> None:
    stop_price = Decimal(stop) / 100
    target = stop_price + Decimal(gap) / 100
    exit_ = ProtectiveExit(exit_id="x", symbol="BTC/USD", quantity=Decimal("0.25"), stop_price=stop_price,
                           take_profit_price=target, eligible_after_utc=START)
    profile = BOOK.get(profile_id)
    fill = trigger_exit_on_candle(exit_, bar, armed_in_this_candle=armed_here, profile=profile,
                                  policy=BAR_REPLAY_POLICY)
    slip = 1 - BAR_REPLAY_POLICY.exit_slippage_rate
    if bar.low <= stop_price:
        assert fill is not None and fill.reason.startswith("stop")
        assert fill.reference_price == min(bar.open, stop_price)
        assert fill.price == fill.reference_price * slip
    elif bar.high > target and not armed_here:
        assert fill is not None and fill.reason == "take_profit"
        assert fill.price == target * slip  # never the better gap open
    else:
        assert fill is None
        return
    assert fill.quantity == exit_.quantity and fill.liquidity == "taker" and fill.side == "sell"
    assert fill.fee == fill.price * fill.quantity * profile.taker_rate
