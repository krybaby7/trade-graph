"""Conservative paper fills. A touched price is not a guaranteed fill."""

from __future__ import annotations

from decimal import Decimal

from trade_graph.contracts.models import Observation
from trade_graph.domain.clock import utc_iso


def plan_fill(
    order: dict,
    observation: Observation,
    *,
    participation: Decimal,
    maker_rate: Decimal,
    taker_rate: Decimal,
) -> tuple[Decimal, Decimal, Decimal, str, bool] | None:
    """Return quantity, price, fee, liquidity, heuristic. None when the order does not fill."""
    if observation.symbol != order["symbol"]:
        return None
    if utc_iso(observation.available_at_utc) < order["eligible_after"]:
        return None
    if observation.observation_id == order.get("excluded_observation_id"):
        return None
    remaining = Decimal(order["remaining"])
    if remaining <= 0:
        return None
    side = order["side"]
    order_type = order["order_type"]
    if order_type == "market":
        price = observation.ask if side == "buy" else observation.bid
        depth = observation.ask_size if side == "buy" else observation.bid_size
        if price is None:
            return None
        quantity = remaining if depth is None else min(remaining, depth * participation)
        if quantity <= 0:
            return None
        fee = price * quantity * taker_rate
        return quantity, price, fee, "taker", False
    if order_type == "limit":
        limit_price = Decimal(order["limit_price"])
        if side == "buy" and (observation.ask is None or observation.ask > limit_price):
            return None
        if side == "sell" and (observation.bid is None or observation.bid < limit_price):
            return None
        price = limit_price
        heuristic = observation.volume is None
        if observation.volume is not None:
            quantity = min(remaining, observation.volume * participation)
        else:
            quantity = min(remaining, remaining * Decimal("0.5"))
        if quantity <= 0:
            return None
        fee = price * quantity * maker_rate
        return quantity, price, fee, "maker", heuristic
    if order_type == "stop" and side == "sell":
        stop_price = Decimal(order["stop_price"])
        if observation.bid is None or observation.bid > stop_price:
            return None
        price = observation.bid
        quantity = remaining
        fee = price * quantity * taker_rate
        return quantity, price, fee, "taker", False
    return None
