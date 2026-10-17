"""The one cautious fill and protective-exit engine shared by the paper broker and the practice range (T28).

Pure functions over exact decimals: an order or exit and one market event (a completed candle, or a quote
observation) in, at most one ``SimFill`` out. The caller keeps ``remaining`` between events. Every fill names
the cost profile and fill policy revision it used (D146).

Entries (D145, D148):
- An event that began before the order existed never fills it, so the bar or quote a decision used is excluded.
- Bars: a resting limit fills only when trades print beyond it (``low < limit`` for a buy); a touch is not a
  fill. It fills at its limit, never at a better gap price, as a maker, for at most the policy's share of the
  bar's volume. A limit at or through the placement price is marketable: a taker at the next open plus
  slippage, never beyond its limit. Market orders fill at the next open plus slippage as takers.
- Quotes: a resting limit fills when the opposite best price reaches it, because a real resting order there
  would have been matched (T05). Market orders take the displayed size at the touch.

Protective exits (D147), a stop-loss and optional take-profit on one long position, one cancelling the other,
both filled as triggered taker sells of the whole quantity with slippage:
- The stop triggers when the price reaches it (``low <= stop``); a gap through it fills from the worse open.
- The take-profit needs a trade beyond it (``high > target``) and fills from its level, never a better gap.
- A bar that reaches both is assumed to have hit the stop first.
- In the bar the position was entered, the order of the entry and the bar's high and low is unknown: a later
  low is assumed and a later high is not, so only the stop can trigger.

Bar fills inside the bar are stamped at its close, when the completed bar shows they happened; fills at the
open are stamped at its open. Slipped prices round to the price increment against the trader.

Lots (W4-A): given the instrument's ``quantity_increment`` (and ``min_quantity``), a partial fill is rounded down to
the lot. A share that covers what remains fills it exactly. When the rounded share would be below the minimum, an
order filled on the engine's own assumption (no volume figure: half of what remains) finishes, because what remains
is then at most about two minimum lots; an order limited by a reported volume or displayed size fills nothing yet,
because that much did not trade. Without an increment the earlier behaviour is kept.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Context, Decimal, localcontext

from trade_graph.contracts.candles import Candle
from trade_graph.contracts.fills import (
    CostProfile,
    FillPolicy,
    FillReason,
    Liquidity,
    ProtectiveExit,
    Side,
    SimFill,
    SimOrder,
)
from trade_graph.contracts.models import InstrumentRules, Observation
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.precision import floor_to_increment

# The paper broker reads real quotes: the touch already includes the spread, and buy reservations are sized at
# the quote, so entries add no slippage there. Protective exits still slip (D147).
PAPER_QUOTE_POLICY = FillPolicy(
    policy_id="paper-quote", revision=1, participation=Decimal("1"), unknown_volume_fill_share=Decimal("0.5"),
    entry_slippage_rate=Decimal("0"), exit_slippage_rate=Decimal("0.001"),
)
# Practice-range replays on candles: bars carry no bid or ask, so taker fills slip from the open or trigger level.
BAR_REPLAY_POLICY = FillPolicy(
    policy_id="bar-replay", revision=1, participation=Decimal("0.1"), unknown_volume_fill_share=Decimal("0.5"),
    entry_slippage_rate=Decimal("0.001"), exit_slippage_rate=Decimal("0.001"),
)
_EXACT = Context(prec=160)


def observation_ref(observation: Observation) -> str:
    return f"observation:{observation.observation_id}"


def _round_against(price: Decimal, increment: Decimal | None, side: Side) -> Decimal:
    if increment is None:
        return price
    if increment <= 0:
        raise ValidationFailure("price increment must be positive")
    with localcontext(_EXACT):
        steps = (price / increment).to_integral_value(rounding=ROUND_CEILING if side == "buy" else ROUND_FLOOR)
        return steps * increment


def _slipped(reference: Decimal, rate: Decimal, side: Side, increment: Decimal | None) -> Decimal:
    with localcontext(_EXACT):
        moved = reference * (1 + rate) if side == "buy" else reference * (1 - rate)
    return _round_against(moved, increment, side)


def _fill(*, order_id: str, symbol: str, side: Side, quantity: Decimal, price: Decimal, reference: Decimal,
          liquidity: Liquidity, reason: FillReason, at, event_ref: str, heuristic: bool, profile: CostProfile,
          policy: FillPolicy) -> SimFill:
    if price <= 0:
        raise ValidationFailure("slippage left no positive fill price")
    with localcontext(_EXACT):
        fee = price * quantity * profile.fee_rate(liquidity)
    return SimFill(
        order_id=order_id, symbol=symbol, side=side, quantity=quantity, price=price, fee=fee,
        fee_asset=symbol.split("/")[1], liquidity=liquidity, reason=reason, reference_price=reference,
        filled_at_utc=at, event_ref=event_ref, heuristic=heuristic, cost_profile_ref=profile.ref,
        cost_profile_sha256=profile.content_sha256, fill_policy_ref=policy.ref,
        fill_policy_sha256=policy.content_sha256,
    )


def _on_the_lot(remaining: Decimal, share: Decimal, *, quantity_increment: Decimal | None,
                min_quantity: Decimal | None, assumed: bool) -> Decimal:
    """The quantity to fill from ``share`` of ``remaining``: on the lot, never a remainder of growing digits.

    ``assumed`` says the share is the engine's own assumption (no volume figure), not traded evidence.
    """
    with localcontext(_EXACT):
        available = min(remaining, share)
        if quantity_increment is None or available >= remaining:
            return available
        if quantity_increment <= 0:
            raise ValidationFailure("quantity increment must be positive")
        quantity = floor_to_increment(available, quantity_increment)
        minimum = max(quantity_increment, min_quantity or Decimal("0"))
        if quantity >= minimum:
            return quantity
        return remaining if assumed else Decimal("0")


def _order_eligible(order: SimOrder | ProtectiveExit, symbol: str, began, event_ref: str) -> bool:
    return symbol == order.symbol and began >= order.eligible_after_utc and order.excluded_event_ref != event_ref


def fill_order_on_candle(order: SimOrder, candle: Candle, *, profile: CostProfile, policy: FillPolicy,
                         price_increment: Decimal | None = None, quantity_increment: Decimal | None = None,
                         min_quantity: Decimal | None = None) -> SimFill | None:
    """Evaluate one entry or sell order against one completed candle."""
    if order.kind == "limit" and order.placement_price is None:
        raise ValidationFailure("a limit order on candles needs its placement price to tell resting from marketable")
    event_ref = candle.source_ref
    if not _order_eligible(order, candle.symbol, candle.open_time_utc, event_ref) or order.remaining <= 0:
        return None
    with localcontext(_EXACT):
        share = candle.volume * policy.participation
    quantity = _on_the_lot(order.remaining, share, quantity_increment=quantity_increment, min_quantity=min_quantity,
                           assumed=False)
    if quantity <= 0:
        return None  # no trades in the bar (or not one lot), so no evidence anything could be bought or sold
    buy = order.side == "buy"
    common = dict(order_id=order.order_id, symbol=order.symbol, side=order.side, quantity=quantity,
                  event_ref=event_ref, heuristic=True, profile=profile, policy=policy)
    if order.kind == "market":
        price = _slipped(candle.open, policy.entry_slippage_rate, order.side, price_increment)
        return _fill(price=price, reference=candle.open, liquidity="taker", reason="market",
                     at=candle.open_time_utc, **common)
    limit = order.limit_price
    assert limit is not None
    traded_through = candle.low < limit if buy else candle.high > limit
    if order.marketable:
        opened_inside = candle.open <= limit if buy else candle.open >= limit
        if opened_inside:
            slipped = _slipped(candle.open, policy.entry_slippage_rate, order.side, price_increment)
            price = min(slipped, limit) if buy else max(slipped, limit)
            return _fill(price=price, reference=candle.open, liquidity="taker", reason="marketable_limit",
                         at=candle.open_time_utc, **common)
        if traded_through:
            return _fill(price=limit, reference=limit, liquidity="taker", reason="marketable_limit",
                         at=candle.close_time_utc, **common)
        return None
    if not traded_through:
        return None
    return _fill(price=limit, reference=limit, liquidity="maker", reason="limit_trade_through",
                 at=candle.close_time_utc, **common)


def trigger_exit_on_candle(exit: ProtectiveExit, candle: Candle, *, armed_in_this_candle: bool,
                           profile: CostProfile, policy: FillPolicy,
                           price_increment: Decimal | None = None) -> SimFill | None:
    """Evaluate a protective exit against one completed candle.

    ``armed_in_this_candle`` says the position was entered inside this candle, so only a later stop can count.
    """
    event_ref = candle.source_ref
    if armed_in_this_candle:
        if not candle.open_time_utc <= exit.eligible_after_utc <= candle.close_time_utc:
            raise ValidationFailure("an exit armed in this candle has its arming time inside it")
        if candle.symbol != exit.symbol or exit.excluded_event_ref == event_ref:
            return None
    elif not _order_eligible(exit, candle.symbol, candle.open_time_utc, event_ref):
        return None
    stop, target = exit.stop_price, exit.take_profit_price
    target_hit = target is not None and candle.high > target and not armed_in_this_candle
    if candle.low <= stop:
        reference = min(candle.open, stop)
        if armed_in_this_candle:
            reason: FillReason = "stop_entry_bar"
        elif target_hit:
            reason = "stop_both_hit"
        else:
            reason = "stop_gap" if candle.open < stop else "stop"
    elif target_hit:
        assert target is not None
        reference, reason = target, "take_profit"
    else:
        return None
    price = _slipped(reference, policy.exit_slippage_rate, "sell", price_increment)
    return _fill(order_id=exit.exit_id, symbol=exit.symbol, side="sell", quantity=exit.quantity, price=price,
                 reference=reference, liquidity="taker", reason=reason, at=candle.close_time_utc,
                 event_ref=event_ref, heuristic=True, profile=profile, policy=policy)


def fill_order_on_observation(order: SimOrder, observation: Observation, *, profile: CostProfile,
                              policy: FillPolicy, price_increment: Decimal | None = None,
                              quantity_increment: Decimal | None = None,
                              min_quantity: Decimal | None = None) -> SimFill | None:
    """Evaluate one order against one quote observation (the paper broker's path)."""
    event_ref = observation_ref(observation)
    if not _order_eligible(order, observation.symbol, observation.available_at_utc, event_ref):
        return None
    remaining = order.remaining
    if remaining <= 0:
        return None
    buy = order.side == "buy"
    common = dict(order_id=order.order_id, symbol=order.symbol, side=order.side, at=observation.available_at_utc,
                  event_ref=event_ref, profile=profile, policy=policy)
    if order.kind == "market":
        touch = observation.ask if buy else observation.bid
        depth = observation.ask_size if buy else observation.bid_size
        if touch is None:
            return None
        with localcontext(_EXACT):
            share = remaining if depth is None else depth * policy.participation
        quantity = _on_the_lot(remaining, share, quantity_increment=quantity_increment, min_quantity=min_quantity,
                               assumed=False)
        if quantity <= 0:
            return None
        price = _slipped(touch, policy.entry_slippage_rate, order.side, price_increment)
        return _fill(quantity=quantity, price=price, reference=touch, liquidity="taker", reason="market",
                     heuristic=False, **common)
    limit = order.limit_price
    assert limit is not None
    opposite = observation.ask if buy else observation.bid
    if opposite is None or (opposite > limit if buy else opposite < limit):
        return None
    heuristic = observation.volume is None
    with localcontext(_EXACT):
        if observation.volume is not None:
            share = observation.volume * policy.participation
        else:
            share = remaining * policy.unknown_volume_fill_share
    quantity = _on_the_lot(remaining, share, quantity_increment=quantity_increment, min_quantity=min_quantity,
                           assumed=heuristic)
    if quantity <= 0:
        return None
    return _fill(quantity=quantity, price=limit, reference=limit, liquidity="maker", reason="limit_book_cross",
                 heuristic=heuristic, **common)


def trigger_exit_on_observation(exit: ProtectiveExit, observation: Observation, *, profile: CostProfile,
                                policy: FillPolicy, price_increment: Decimal | None = None) -> SimFill | None:
    """Evaluate a protective exit against one quote; a sell exit needs a bid, it never invents a price."""
    event_ref = observation_ref(observation)
    if not _order_eligible(exit, observation.symbol, observation.available_at_utc, event_ref):
        return None
    bid = observation.bid
    if bid is None:
        return None
    stop, target = exit.stop_price, exit.take_profit_price
    if bid <= stop:
        reference, reason = bid, ("stop_gap" if bid < stop else "stop")
    elif target is not None and bid > target:
        reference, reason = target, "take_profit"
    else:
        return None
    price = _slipped(reference, policy.exit_slippage_rate, "sell", price_increment)
    return _fill(order_id=exit.exit_id, symbol=exit.symbol, side="sell", quantity=exit.quantity, price=price,
                 reference=reference, liquidity="taker", reason=reason, at=observation.available_at_utc,
                 event_ref=event_ref, heuristic=False, profile=profile, policy=policy)


def check_order_rules(order: SimOrder, rules: InstrumentRules, *, reference_price: Decimal | None = None) -> None:
    """Refuse an order a venue would refuse: increments and minimums are exact, nothing is resized."""
    if order.symbol != rules.symbol:
        raise ValidationFailure("order symbol differs from its instrument rules")
    if order.quantity != floor_to_increment(order.quantity, rules.quantity_increment):
        raise ValidationFailure("order quantity is off the quantity increment")
    if order.quantity < rules.min_quantity:
        raise ValidationFailure("order quantity is below the minimum quantity")
    price = order.limit_price or order.placement_price or reference_price
    if price is None:
        raise ValidationFailure("a market order needs its placement price to check the minimum notional")
    with localcontext(_EXACT):
        notional = order.quantity * price
    if notional < rules.min_notional:
        raise ValidationFailure("order value is below the minimum notional")
    if order.limit_price is not None and order.limit_price != floor_to_increment(order.limit_price,
                                                                                 rules.price_increment):
        raise ValidationFailure("limit price is off the price increment")


def check_exit_rules(exit: ProtectiveExit, rules: InstrumentRules) -> None:
    """Refuse an exit a venue would refuse: trigger levels on the tick, quantity on the lot size."""
    if exit.symbol != rules.symbol:
        raise ValidationFailure("exit symbol differs from its instrument rules")
    if exit.quantity != floor_to_increment(exit.quantity, rules.quantity_increment):
        raise ValidationFailure("exit quantity is off the quantity increment")
    for level in (exit.stop_price, exit.take_profit_price):
        if level is not None and level != floor_to_increment(level, rules.price_increment):
            raise ValidationFailure("exit level is off the price increment")
