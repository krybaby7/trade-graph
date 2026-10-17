"""No riba (D127): only spot pairs are traded, and interest-like postings are flagged (T32).

D127 excludes futures and perpetuals (and their funding payments), conventional short selling, margin and other
interest-bearing borrowing, and lending, earn or interest on idle balances. In this engine:

- an order names a spot pair written ``BASE/QUOTE`` in plain upper-case letters and digits. Venue derivative
  names (Kraken Futures ``PF_``/``PI_``/``FI_``, ``-PERP``, ``-SWAP``, dated contracts, ``BASE/QUOTE:SETTLE``) and
  Kraken's staking or earn variants of an asset (``ETH.S``, ``DOT.F``, ``USD.M``, ``ETH2.S``) do not fit and are
  refused by :func:`refuse_riba_symbol`;
- margin and leverage are refused with the owner policy (``leverage_allowed`` must stay false) and short sales
  by the spot balance check (an exit never sells more than is held, and nothing is borrowed);
- nothing in the engine lends, stakes or earns. A ledger posting that looks like interest, a staking or earn
  reward, a margin rollover or a funding payment is flagged for the owner by
  :func:`interest_posting_reasons`; it is evidence to review, not income the engine counts on.

The owner may revise D127 with a scholar's opinion; that is an owner change to these protected rules.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

from trade_graph.domain.errors import AuthorityDenied

_SPOT: Final = re.compile(r"[A-Z0-9]{1,20}/[A-Z0-9]{2,10}")
_DERIVATIVE_WORDS: Final = ("PERP", "SWAP", "FUTURE")
# Word in a posting reference -> the reason recorded with the flag.
_INTEREST_WORDS: Final = {
    "interest": "interest", "apr": "interest", "apy": "interest",
    "staking": "staking", "stake": "staking", "staked": "staking", "unstake": "staking",
    "earn": "earn", "earning": "earn", "earnings": "earn",
    "reward": "reward", "rewards": "reward",
    "yield": "yield",
    "lending": "lending", "lend": "lending", "loan": "lending", "borrow": "margin", "borrowing": "margin",
    "margin": "margin", "leverage": "margin",
    "rollover": "rollover",
    "funding": "funding",
}
_WORD: Final = re.compile(r"[a-z0-9]+")
_STAKED_ASSET: Final = re.compile(r"[A-Z0-9]+\.[A-Z]{1,4}")


def refuse_riba_symbol(symbol: str) -> None:
    """Refuse anything that is not a plain spot pair (D127)."""
    if not isinstance(symbol, str) or _SPOT.fullmatch(symbol) is None:
        raise AuthorityDenied(f"{symbol!r} is not a spot pair: futures, perpetuals, staking and earn products "
                              "are refused (D127)")
    if any(word in symbol for word in _DERIVATIVE_WORDS):
        raise AuthorityDenied(f"{symbol!r} names a derivative: futures and perpetuals are refused (D127)")


def interest_posting_reasons(kind: str, external_ref: str | None, payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Why a deposit, withdrawal or internal posting looks like riba, or ``()``. Sorted, without repeats.

    Every ``kind`` of cash or coin movement is read the same way (its reference words and its asset); fills and
    expenses are not passed here.
    """
    words = _WORD.findall(f"{kind} {external_ref or ''}".lower())
    reasons = {_INTEREST_WORDS[word] for word in words if word in _INTEREST_WORDS}
    asset = payload.get("asset")
    if isinstance(asset, str) and _STAKED_ASSET.fullmatch(asset):
        reasons.add("staked_asset")
    return tuple(sorted(reasons))


__all__ = ["interest_posting_reasons", "refuse_riba_symbol"]
