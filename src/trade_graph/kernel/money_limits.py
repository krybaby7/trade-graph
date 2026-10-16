"""The owner's money limits as pure Decimal rules (T32; D138 items 1, 2 and 6).

No database, clock, network or model here; callers pass every value in. Two rules:

*The daily loss limit.* The trading day runs from the owner's day start (default 00:00 UTC) to the next one.
The limit is reached when the account's value has fallen from the day-start value by at least the owner's
fraction of it. Values are equity, so fees already paid and open positions at their marks are included.

*Entries counted as if filled.* An entry (and every resting entry already authorised) is valued at the highest
of its own price, the current buy quote and the coin's mark, so a resting buy below the market counts at what it
would be worth once it fills, not at its cheaper limit. The caps are compared with the equity left after every
counted entry paid the worst fee rate and any loss it could take against the mark when it fills
(``equity_floor``), for the coin bought and for every other coin, since a new entry's costs lower that floor
for all of them. With marks unchanged, no sequence of fills of those entries, whole or partial, at or inside
their limits, can then put more than the per-coin or total share of the account into coins (property tests in
``tests/property/test_money_limit_properties.py``). Price moves are not fills: a coin that rises can grow past
its share, and D138 orders no sell-off for that.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Context, Decimal, localcontext
from typing import Final

from trade_graph.domain.errors import AuthorityDenied

# A mark older than this at the moment of a valuation does not give a day-start value or a current value for the
# loss limit; the paper feed marks every poll (D129), so this only bites after an outage.
MARK_MAX_AGE: Final = timedelta(minutes=15)
_EXACT = Context(prec=80)


@dataclass(frozen=True, slots=True)
class TradingDay:
    start: datetime
    end: datetime  # the next day start; buying stopped by the loss limit resumes here


def day_start_time(text: str) -> time:
    hours, minutes = text.split(":")
    if len(hours) != 2 or len(minutes) != 2:
        raise ValueError("the trading-day start is HH:MM")
    return time(int(hours), int(minutes), tzinfo=UTC)


def trading_day(at: datetime, start: str) -> TradingDay:
    """The trading day holding ``at``: ``start <= at < end``, in UTC."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("a trading day needs an aware time")
    moment = at.astimezone(UTC)
    clock = day_start_time(start)
    candidate = datetime.combine(moment.date(), clock.replace(tzinfo=None), tzinfo=UTC)
    if candidate > moment:
        candidate -= timedelta(days=1)
    return TradingDay(candidate, candidate + timedelta(days=1))


def loss_limit_amount(day_start_value: Decimal, fraction: Decimal) -> Decimal:
    if day_start_value <= 0:
        raise ValueError("a loss limit needs a positive day-start value")
    with localcontext(_EXACT):
        return day_start_value * fraction


def loss_limit_reached(*, day_start_value: Decimal, current_value: Decimal, fraction: Decimal) -> bool:
    """At the limit is the limit: a loss equal to the fraction of the day-start value stops new entries."""
    with localcontext(_EXACT):
        return day_start_value - current_value >= loss_limit_amount(day_start_value, fraction)


@dataclass(frozen=True, slots=True)
class EntryValue:
    coin: str            # the base asset bought
    value: Decimal       # quantity × the valuation price, in the account's native currency
    shortfall: Decimal   # what a fill could lose against the mark beyond its fee: quantity × (valuation − mark)


def entry_value(coin: str, quantity: Decimal, *, order_price: Decimal | None, reference: Decimal | None,
                mark: Decimal | None) -> EntryValue:
    """Value an entry as if filled: at the highest of its price, the buy quote and the mark."""
    prices = [price for price in (order_price, reference, mark) if price is not None and price > 0]
    if not prices or quantity <= 0:
        raise ValueError("an entry needs a positive quantity and at least one positive price")
    with localcontext(_EXACT):
        price = max(prices)
        shortfall = quantity * (price - mark) if mark is not None and mark > 0 else Decimal("0")
        return EntryValue(coin, quantity * price, max(shortfall, Decimal("0")))


@dataclass(frozen=True, slots=True)
class ExposureCheck:
    equity: Decimal
    equity_floor: Decimal
    coin_value: Decimal
    gross_value: Decimal


def check_entry(*, equity: Decimal, inventory: Decimal, held: Mapping[str, Decimal], entries: Sequence[EntryValue],
                proposed: EntryValue, fee_rate: Decimal, coin_cap: Decimal, gross_cap: Decimal) -> ExposureCheck:
    """Refuse ``proposed`` when it and every counted entry, all filled, would exceed a cap.

    ``inventory`` is every held coin at its mark; ``held`` maps a coin to its holding's value (the proposed coin's
    at the highest of its mark, quote and order price, other coins' at their marks); ``entries`` are the resting
    entries already authorised, on any coin. The proposed coin must stay within its share of the equity floor,
    and so must every other coin that was within it before: the new entry's fees and shortfall lower the floor
    for all of them. A coin already above its share because its price rose is not refused here (no sell-off).
    """
    if equity <= 0:
        raise AuthorityDenied("no equity")
    with localcontext(_EXACT):
        before = equity - sum((fee_rate * entry.value + entry.shortfall for entry in entries), Decimal("0"))
        floor = before - (fee_rate * proposed.value + proposed.shortfall)
        if floor <= 0:
            raise AuthorityDenied("gross exposure")
        totals: dict[str, Decimal] = {coin: value for coin, value in held.items()}
        for entry in entries:
            totals[entry.coin] = totals.get(entry.coin, Decimal("0")) + entry.value
        coin_value = totals.get(proposed.coin, Decimal("0")) + proposed.value
        if coin_value > coin_cap * floor:
            raise AuthorityDenied("single asset exposure")
        for coin, total in totals.items():
            if coin != proposed.coin and coin_cap * floor < total <= coin_cap * before:
                raise AuthorityDenied("single asset exposure")
        gross_value = inventory + sum((entry.value for entry in entries), Decimal("0")) + proposed.value
        if gross_value > gross_cap * floor:
            raise AuthorityDenied("gross exposure")
    return ExposureCheck(equity, floor, coin_value, gross_value)


__all__ = [
    "MARK_MAX_AGE",
    "EntryValue",
    "ExposureCheck",
    "TradingDay",
    "check_entry",
    "day_start_time",
    "entry_value",
    "loss_limit_amount",
    "loss_limit_reached",
    "trading_day",
]
