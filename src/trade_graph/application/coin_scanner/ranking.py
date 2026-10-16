"""The ranking step (D173): places per factor, summed. Pure, deterministic, exact decimals, no model.

Each pair gets a place in each factor's list: 1 for the best value, and pairs with equal values share a place
(one more than the number of pairs strictly better, so the next place is skipped). Higher is better for traded
volume, swing relative to cost and trend; lower is better for spread. The score is the sum of the places, so the
four factors count equally. A lower score ranks higher; equal scores go to the pair with more traded volume, then
to the symbol in alphabetical order, so the result never depends on the order of the input list.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from trade_graph.contracts.coin_scanner import FACTORS
from trade_graph.domain.errors import ValidationFailure

_LOWER_IS_BETTER = frozenset({"spread"})


@dataclass(frozen=True, slots=True)
class Candidate:
    """A pair that passed every check, with the four values it is ranked on. ``spread`` may be None only when the
    scan does not rank spread (a replay has no historical bid and ask)."""

    symbol: str
    volume_usd_24h: Decimal
    swing_to_cost: Decimal
    spread: Decimal | None
    trend: Decimal


@dataclass(frozen=True, slots=True)
class Ranked:
    symbol: str
    position: int
    score: int
    ranks: Mapping[str, int]
    candidate: Candidate

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Ranked):
            return NotImplemented
        return (self.symbol, self.position, self.score, dict(self.ranks), self.candidate) == (
            other.symbol, other.position, other.score, dict(other.ranks), other.candidate)

    def __hash__(self) -> int:
        return hash((self.symbol, self.position, self.score))


def _value(candidate: Candidate, factor: str) -> Decimal:
    value = getattr(candidate, factor)
    if value is None:
        raise ValidationFailure(f"{candidate.symbol} has no {factor} value to rank")
    if type(value) is not Decimal or not value.is_finite():
        raise ValidationFailure(f"{candidate.symbol} {factor} must be a finite Decimal")
    return value


def _places(values: Sequence[Decimal], *, lower_is_better: bool) -> list[int]:
    """1 + the number of values strictly better than each one."""
    if lower_is_better:
        return [1 + sum(1 for other in values if other < value) for value in values]
    return [1 + sum(1 for other in values if other > value) for value in values]


def rank(candidates: Sequence[Candidate], *, factors: Sequence[str] = FACTORS) -> tuple[Ranked, ...]:
    """Every candidate, best first. Only the named factors are ranked; each must be one of ``FACTORS``."""
    chosen = tuple(factors)
    if not chosen or any(factor not in FACTORS for factor in chosen) or len(set(chosen)) != len(chosen):
        raise ValidationFailure(f"each ranked factor must be one of {', '.join(FACTORS)}, once")
    items = list(candidates)
    if any(not isinstance(item, Candidate) for item in items):
        raise ValidationFailure("only Candidate values can be ranked")
    symbols = [item.symbol for item in items]
    if len(set(symbols)) != len(symbols):
        raise ValidationFailure("a pair appears twice in one ranking")
    places: dict[str, list[int]] = {}
    for factor in chosen:
        values = [_value(item, factor) for item in items]
        places[factor] = _places(values, lower_is_better=factor in _LOWER_IS_BETTER)
    if "volume_usd_24h" not in chosen:
        for item in items:
            _value(item, "volume_usd_24h")  # the tie-break needs it either way
    scored = []
    for index, item in enumerate(items):
        ranks = {factor: places[factor][index] for factor in chosen}
        scored.append((sum(ranks.values()), -item.volume_usd_24h, item.symbol, ranks, item))
    scored.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
    return tuple(Ranked(symbol=item.symbol, position=position, score=score, ranks=MappingProxyType(ranks),
                        candidate=item)
                 for position, (score, _volume, _symbol, ranks, item) in enumerate(scored, start=1))


def shortlist(ranked: Sequence[Ranked], size: int) -> tuple[Ranked, ...]:
    """The first ``size`` places. Fewer when fewer pairs passed; never padded with an excluded pair."""
    if type(size) is not int or size < 1:
        raise ValidationFailure("a shortlist holds at least one place")
    return tuple(ranked[:size])


__all__ = ["Candidate", "Ranked", "rank", "shortlist"]
