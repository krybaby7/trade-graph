"""Which coins the owner's coin list covers, for the day's focus (T37; D120, D127, D138 item 3, D206).

Focus coins come only from the coin scanner's shortlist **and** the owner's symbol scope. The scope is the same one
entries are checked against: a listed mandate names its coins (inside the owner policy's list), and under the
owner's coin rule any spot pair in its quote currency counts (whether it may be bought at a given moment is still
the scanner's answer, checked per entry by the money limits). Riba products never count (D127). When the owner
policy or the portfolio's mandate cannot be read, nothing is covered.
"""

from __future__ import annotations

from collections.abc import Iterable

from trade_graph.application.authority import AuthorityRecord
from trade_graph.domain.errors import AuthorityDenied, TradeGraphError
from trade_graph.kernel.authority import owner_scope_covers
from trade_graph.kernel.riba import refuse_riba_symbol

OUTSIDE_LIST = "outside the owner's coin list"


def focus_scope_refusals(authority: AuthorityRecord, portfolio_id: str, symbols: Iterable[str]) -> dict[str, str]:
    """Each symbol the owner's scope does not cover, with the reason in plain words; ``{}`` when all are covered."""
    wanted = tuple(dict.fromkeys(symbols))
    if not wanted:
        return {}
    try:
        policy = authority.active_policy()
        mandate = authority.active_mandate(portfolio_id)
    except TradeGraphError as error:
        return {symbol: f"{OUTSIDE_LIST}: it can't be read ({error})" for symbol in wanted}
    refusals: dict[str, str] = {}
    for symbol in wanted:
        try:
            refuse_riba_symbol(symbol)
        except AuthorityDenied as error:
            refusals[symbol] = f"{OUTSIDE_LIST}: riba ({error})"
            continue
        if not owner_scope_covers(policy, symbol):
            refusals[symbol] = f"{OUTSIDE_LIST} (owner policy {policy.revision_id})"
            continue
        try:
            AuthorityRecord._require_listed(policy, mandate, symbol)
        except AuthorityDenied:
            refusals[symbol] = f"{OUTSIDE_LIST} (the portfolio's mandate, revision {mandate.revision})"
    return refusals


__all__ = ["OUTSIDE_LIST", "focus_scope_refusals"]
