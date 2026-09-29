"""Live enablement stays closed unless the owner records every prerequisite."""

from __future__ import annotations

from decimal import Decimal


def evaluate_live_enablement(record: dict) -> dict:
    paper_capital = Decimal(str(record.get("paper_capital", "0")))
    live_allocation = Decimal(str(record.get("live_allocation", "0")))
    reasons = []
    if record.get("uses_paper_capital_as_live_allocation"):
        reasons.append("paper capital is not a live allocation")
    if paper_capital == live_allocation and live_allocation != 0 and record.get("allocation_copied_from_paper"):
        reasons.append("paper USD10000 cannot set the live allocation")
    checks = {
        "eligibility": bool(record.get("eligibility_confirmed")),
        "owner_allocation": live_allocation > 0 and bool(record.get("allocation_is_owner_set")),
        "withdrawals_absent": record.get("withdrawals_allowed") is False,
        "read_only_reconciliation": bool(record.get("read_only_reconciliation_passed")),
        "operating_budget": bool(record.get("operating_budget_set")),
        "owner_confirmation": bool(record.get("owner_confirmed")),
    }
    if record.get("economic_verdict") == "supported":
        checks["economic_or_diagnostic"] = True
    elif record.get("diagnostic_pilot") is True:
        checks["economic_or_diagnostic"] = True
        checks["diagnostic_label"] = True
    else:
        checks["economic_or_diagnostic"] = False
        reasons.append("no supported evidence and no labeled diagnostic pilot")
    for name, ok in checks.items():
        if not ok:
            reasons.append(name)
    enabled = not reasons and all(checks.values())
    return {
        "enabled": enabled,
        "diagnostic": bool(record.get("diagnostic_pilot")) and enabled,
        "reasons": reasons,
        "live_allocation": str(live_allocation),
    }
