"""Each coin's market type as the Trading desk is shown it (W12-A, C12; D287, D297).

The labels are W11-A's (``application/market_types/``, protected; the hourly scan writes them, the day start's scan does
not). This module only reads them, point in time: the newest label of each coin made at or before the moment, through
``read_latest_market_types``. A label is current for ``DEFAULT_LABEL_AGE`` (2 hours, D287). A stale label is shown as
stale, with its age and the word it said then, and a coin never labelled as missing: neither is shown as the coin's
type now, so the desk never reads a guess. Nothing here labels a coin, calls a model or touches an order.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta

from trade_graph.application.market_types.store import DEFAULT_LABEL_AGE, StoredMarketType, read_latest_market_types
from trade_graph.contracts.trading_desk import COIN_ROLES, MAX_SENTENCE, CoinMarketType, CoinRole
from trade_graph.domain.clock import utc_iso

_RANK = {role: index for index, role in enumerate(COIN_ROLES)}


def _age_words(minutes: int) -> str:
    if minutes < 120:
        return f"{minutes} minutes"
    hours, rest = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} hours" + (f" {rest} minutes" if rest else "")
    return f"{hours // 24} days"


def reading(symbol: str, label: StoredMarketType | None, at: datetime, roles: Iterable[CoinRole] = (), *,
            max_age: timedelta = DEFAULT_LABEL_AGE) -> CoinMarketType:
    """One coin's reading at ``at`` from its newest label made by then (or none)."""
    seen_as = tuple(sorted(set(roles), key=_RANK.__getitem__))
    if label is None:
        return CoinMarketType(
            symbol=symbol, status="missing", seen_as=seen_as,
            sentence=(f"No market-type label has been recorded for {symbol} yet (software labels the coins it watches "
                      "with the hourly scan); its type is not known."))
    minutes = max(0, int((at - label.as_of).total_seconds() // 60))
    common = {"symbol": symbol, "newest_label": label.market_type, "reason": label.reason,
              "labelled_at": label.as_of, "age_minutes": minutes, "rules_ref": label.rules_ref,
              "label_id": label.label_id, "seen_as": seen_as}
    if label.is_fresh(at, max_age):
        try:
            sentence = label.sentence()
        except (KeyError, TypeError, ValueError):
            sentence = f"{symbol} is {label.market_type}."
        sentence = f"{sentence} (Labelled {_age_words(minutes)} ago.)"
        return CoinMarketType(status="current", market_type=label.market_type, **common,
                              sentence=sentence[:MAX_SENTENCE])
    when = utc_iso(label.as_of)[:16].replace("T", " ")
    sentence = (f"{symbol}'s newest market-type label is {_age_words(minutes)} old (made {when} UTC, when it was "
                f"{label.market_type}); a label is current for {int(max_age.total_seconds() // 3600)} hours, so its "
                "type now is not known.")
    return CoinMarketType(status="stale", market_type=None, **common, sentence=sentence[:MAX_SENTENCE])


def desk_coins(*, held: Iterable[str] = (), planned: Iterable[str] = (), focus: Iterable[str] = (),
               triggers: Iterable[str] = (), shortlist: Iterable[str] = ()) -> dict[str, tuple[CoinRole, ...]]:
    """The coins a desk wake is about, each with every role it has, in the order held, planned, focus, trigger and
    shortlist coins (the first role decides the place)."""
    roles: dict[str, list[CoinRole]] = {}
    for role, symbols in (("held", held), ("planned", planned), ("focus", focus), ("trigger", triggers),
                          ("shortlist", shortlist)):
        for symbol in symbols:
            if isinstance(symbol, str) and "/" in symbol and role not in roles.setdefault(symbol, []):
                roles[symbol].append(role)  # type: ignore[arg-type]
    return {symbol: tuple(items) for symbol, items in roles.items()}


def unreadable(coins: Mapping[str, Iterable[CoinRole]], error: Exception) -> tuple[CoinMarketType, ...]:
    """Every coin shown as missing when the labels cannot be read at all (an edited record, a broken database):
    the wake still runs, and the desk is told why it sees no type."""
    return tuple(CoinMarketType(
        symbol=symbol, status="missing", seen_as=tuple(sorted(set(roles), key=_RANK.__getitem__)),
        sentence=(f"The market-type labels could not be read ({type(error).__name__}); {symbol}'s type is not "
                  "known.")) for symbol, roles in coins.items())


def coin_market_types(connection: sqlite3.Connection, coins: Mapping[str, Iterable[CoinRole]], at: datetime, *,
                      max_age: timedelta = DEFAULT_LABEL_AGE) -> tuple[CoinMarketType, ...]:
    """The readings of ``coins`` (symbol to the roles it has in the wake) at ``at``: held, planned, focus, trigger and
    shortlist coins in that order, each kind in the order given. Works on a read-only connection; a database without
    labels (before migration 0050) shows every coin as missing."""
    labels = read_latest_market_types(connection, symbols=list(coins), as_of=at) if coins else {}
    order = {symbol: index for index, symbol in enumerate(coins)}
    ranked = sorted(coins, key=lambda symbol: (min((_RANK[role] for role in coins[symbol]), default=len(_RANK)),
                                               order[symbol]))
    return tuple(reading(symbol, labels.get(symbol), at, coins[symbol], max_age=max_age) for symbol in ranked)


__all__ = ["coin_market_types", "desk_coins", "reading", "unreadable"]
