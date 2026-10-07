"""Offline fixture summary; actual forward verdicts require TrialRegistry evidence."""

from __future__ import annotations

from decimal import Decimal

from trade_graph.domain.money import canonical_decimal
from trade_graph.evaluation_contracts import fixed_decimal


def evaluate_forward(
    *,
    independent_decisions: int,
    minimum_decisions: int,
    net_economic: Decimal,
    predeclared_hurdle: Decimal,
    costs_included: bool,
) -> dict:
    for value in (independent_decisions, minimum_decisions):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("decision counts must be integers")
    if independent_decisions < 0 or minimum_decisions <= 0:
        raise ValueError("counts require a nonnegative sample and positive minimum")
    net_economic = fixed_decimal(net_economic)
    predeclared_hurdle = fixed_decimal(predeclared_hurdle)
    if not costs_included:
        raise ValueError("failed and external costs must be included")
    return {
        "verdict": "insufficient_evidence",
        "net_economic": canonical_decimal(net_economic),
        "decisions": independent_decisions,
        "minimum_decisions": minimum_decisions,
        "mechanical_hurdle_met": net_economic >= predeclared_hurdle,
        "reason": "offline summary has no preregistration, actual forward provenance or uncertainty evidence",
    }
