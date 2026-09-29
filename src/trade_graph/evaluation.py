"""Forward-paper verdicts. Insufficient evidence is a valid result."""

from __future__ import annotations

from decimal import Decimal


def evaluate_forward(
    *,
    independent_decisions: int,
    minimum_decisions: int,
    net_economic: Decimal,
    predeclared_hurdle: Decimal,
    costs_included: bool,
) -> dict:
    if not costs_included:
        raise ValueError("failed and external costs must be included")
    if independent_decisions < minimum_decisions:
        return {
            "verdict": "insufficient_evidence",
            "net_economic": str(net_economic),
            "decisions": independent_decisions,
        }
    verdict = "supported" if net_economic >= predeclared_hurdle else "not_supported"
    return {
        "verdict": verdict,
        "net_economic": str(net_economic),
        "decisions": independent_decisions,
    }
