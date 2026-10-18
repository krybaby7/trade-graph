"""How much of a filled day-plan buy its orders cover (T34 D188, W8-B D246, W10-C): one arithmetic for every reader.

``DayPlanProtection`` (which places the safety exits), HQ's day plans panel (``api/hq_plans.py``) and HQ's "no safety
exit" card (``api/hq_protection.py``) all use these functions, so they never disagree about what is covered.

The rule, over persisted records only: an order that covers part of a filled buy (a safety exit, or the buy's part of
the Trader's close sell) covers its whole quantity while it is outstanding (an unknown status included, so an
unknown status never leads to a second exit) or filled, and only what it sold once it ended otherwise (cancelled,
refused, or a state this module doesn't know). What is uncovered is what the buy filled less what its orders cover.
At coin level, what the Trader sees as unprotected never exceeds what is held and not already protected.

``uncovered_since`` puts the same arithmetic on a timeline, for HQ's "since when". Plain values in, exact Decimals
out: no database, clock, broker or provider. Protected execution control (``kernel/``): agents cannot edit it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final

ZERO: Final = Decimal("0")
# Order states in which an order may still sell (or whose outcome is not known yet): it covers its whole quantity.
OUTSTANDING: Final = frozenset({"SUBMISSION_PENDING", "SUBMITTING", "OPEN", "PARTIALLY_FILLED", "UNKNOWN",
                                "CANCEL_PENDING"})


@dataclass(frozen=True)
class Cover:
    """One order covering part of a filled buy: a safety exit, or the buy's part of a Trader's close sell.

    ``state`` is its order intent's state (``None`` when the intent can't be found), ``quantity`` what it covers while
    outstanding or filled, ``sold`` what it sold (for a close's part, its share of the sale: ``shared_part``).
    ``placed_at`` and ``ended_at`` (when its intent was created and when it ended, for one that ended unsold) are
    needed only by ``uncovered_since``.
    """

    state: str | None
    quantity: Decimal
    sold: Decimal = ZERO
    placed_at: datetime | None = None
    ended_at: datetime | None = None


def counts_whole(state: str | None) -> bool:
    """Does an order in this state cover its whole quantity? Yes while outstanding or once filled."""
    return state in OUTSTANDING or state == "FILLED"


def covers(order: Cover) -> Decimal:
    """What one order covers now."""
    return order.quantity if counts_whole(order.state) else order.sold


def covered(orders: Iterable[Cover]) -> Decimal:
    """What a buy's orders cover now, in the coin's own units."""
    return sum((covers(order) for order in orders), ZERO)


def uncovered(filled: Decimal, orders: Iterable[Cover]) -> Decimal:
    """What a buy filled and its orders don't cover now (never below zero)."""
    return max(ZERO, filled - covered(orders))


def shared_part(sold: Decimal, parts: Sequence[tuple[int, Decimal]], sequence: int) -> Decimal:
    """One part's share of what a close sell sold: the sale's fills shared out over its ``(sequence, quantity)`` parts
    in their order, each part filled before the next (W8-B, D246)."""
    left = sold
    for number, quantity in parts:
        share = min(left, quantity)
        if number == sequence:
            return share
        left -= share
    return ZERO


def coin_unprotected(exposure: Decimal, *, held: Decimal, protected: Decimal) -> Decimal:
    """A coin's unprotected quantity (W8-B's position view): its buys' uncovered parts, never more than is held and
    not already protected by an exit still out (coins sold another way, for example by a sell-everything setting,
    need no exit)."""
    return min(exposure, max(ZERO, held - protected))


def uncovered_since(fills: Iterable[tuple[datetime, Decimal]], orders: Iterable[Cover]) -> datetime | None:
    """When a buy's uncovered part began, or ``None`` when it is fully covered now.

    The records are replayed in time order: each fill adds what it bought; each order covers its whole quantity from
    ``placed_at``, and one that ended unsold covers only what it sold from ``ended_at`` (from ``placed_at`` when no end
    time is known). The answer is the start of the last stretch during which the buy was never fully covered: the
    fill whose safety exit was never placed, or the moment an exit (or close sell) that covered it ended unsold.
    Raises ``ValueError`` for an order without ``placed_at``: such an order can't be put on the timeline.
    """
    changes: dict[datetime, Decimal] = {}

    def change(moment: datetime, amount: Decimal) -> None:
        changes[moment] = changes.get(moment, ZERO) + amount

    for moment, quantity in fills:
        change(moment, quantity)
    for order in orders:
        if order.placed_at is None:
            raise ValueError("an order without a placed time can't be put on the timeline")
        change(order.placed_at, -order.quantity)
        if not counts_whole(order.state):
            end = max(order.ended_at or order.placed_at, order.placed_at)
            change(end, order.quantity - order.sold)
    gap, since = ZERO, None
    for moment in sorted(changes):
        gap += changes[moment]
        since = (since or moment) if gap > 0 else None
    return since


__all__ = ["OUTSTANDING", "ZERO", "Cover", "coin_unprotected", "counts_whole", "covered", "covers", "shared_part",
           "uncovered", "uncovered_since"]
