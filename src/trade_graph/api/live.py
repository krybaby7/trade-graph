"""Inception-period dashboard with separate operational and economic evidence."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from trade_graph.api import activity, financial, owner_expenses, resources
from trade_graph.api.health import health
from trade_graph.api.security import redact
from trade_graph.domain.clock import FrozenClock, parse_utc, utc_iso
from trade_graph.domain.money import canonical_decimal


def projection_runtime(runtime):
    # Read projections have no route to the controller's native metadata refresh,
    # process launch or provider adapter. Their database/ledger are read snapshots.
    values = dict(vars(runtime))
    values.pop("service_controller", None)
    return SimpleNamespace(**values)


# Stable codes for economic_result.unknown_reasons, in the order they are appended.
# Owner-expense reasons without a code of their own map to "other".
_OWNER_EXPENSE_REASON_CODES = {
    "owner_subscription_completeness_missing": "subscription_bills_unconfirmed",
    "owner_other_completeness_missing": "other_costs_unconfirmed",
    "duplicate_existing_accounting": "bill_matches_existing_records",
    "missing_fx": "bill_euro_rate_missing",
    "stale_fx": "bill_euro_rate_old",
}


def _invoice_uncertainty(runtime, now: str) -> bool:
    # Same query as the health projection's invoice check.
    invoices = runtime.database.execute(
        "SELECT unexplained FROM invoice_reconciliations WHERE deployment_id = ? AND created_at <= ?",
        (getattr(runtime, "deployment_id", "deployment"), now),
    ).fetchall()
    return any(Decimal(row["unexplained"]) != 0 for row in invoices)


def economics(runtime, *, reporting_end: str | None = None) -> dict:
    """Inception economics from one financial overview, without activity or health projections.

    Returns the financial overview, the owner-expense projection, recovery history and
    ``economic_result`` exactly as :func:`overview` publishes them, plus
    ``unknown_reason_codes`` parallel to ``economic_result["unknown_reasons"]``.
    """
    reader = projection_runtime(runtime)
    now = utc_iso(runtime.clock.now())
    portfolio = reader.database.execute(
        "SELECT * FROM portfolios WHERE portfolio_id=?", (reader.portfolio_id,),
    ).fetchone()
    if reporting_end is not None:
        cutoff = datetime.fromisoformat(reporting_end.replace("Z", "+00:00"))
        if cutoff.tzinfo is None:
            raise ValueError("timezone-aware reporting cutoff required")
        if cutoff < parse_utc(portfolio["created_at"]) or cutoff > runtime.clock.now():
            raise ValueError("reporting cutoff outside retained account period")
        reader.clock = FrozenClock(cutoff)
    data = financial.overview(reader)
    current_reader = projection_runtime(runtime)
    period = {"start_at": portfolio["created_at"], "end_at": data["as_of"],
              "basis": "account inception, retained history", "selected_cutoff": reporting_end is not None}
    # Owner evidence can arrive after the period it substantiates. Retain the
    # current evidence snapshot while proration and incurred-at FX stay historical.
    expense = owner_expenses.projection(current_reader, period["start_at"], period["end_at"],
                                       data["reporting_currency"])
    # API/ledger accruals remain bounded by the selected financial cutoff.
    existing = financial._cost_state(reader, data["reporting_currency"])
    # Receipt attribution totals are global accruals. The selected account owns
    # only its persisted allocation, including its fraction of a shared receipt.
    actual_receipts = [row for row in existing["receipts"] if not row["synthetic"]]
    known_api_ai = sum((Decimal(row["allocated_amount"]) for row in actual_receipts
                        if not row["unresolved"] and not row["valuation"]["provisional"]
                        and row["allocated_amount"] is not None), Decimal(0))
    api_unknown = any(row["unresolved"] or row["valuation"]["provisional"]
                      or row["allocated_amount"] is None
                      or (row["allocation_basis"] == "unallocated deployment expense"
                          and Decimal(row["native_cost"]) != 0)
                      for row in actual_receipts)
    # Independent ledger expenses already exclude receipt mirrors. Deriving
    # this category directly prevents uncertain API bounds becoming other costs.
    independent_expenses = [row for row in existing["ledger_expenses"] if row["portfolio_id"] == reader.portfolio_id]
    recorded_other = financial._sum([
        None if row["valuation"]["provisional"] or row["valuation"]["amount"] is None
        else Decimal(row["valuation"]["amount"]) for row in independent_expenses
    ])
    subscription = expense["graph_subscription_allocation"]
    other = expense["graph_other_allocation"]
    # Owner bill evidence allocates to this deployment, without account weights.
    # With any potentially overlapping account, retain graph totals separately
    # rather than inventing the selected account's share of a nonzero bill.
    other_portfolio = reader.database.execute(
        "SELECT 1 FROM portfolios WHERE portfolio_id != ? AND created_at <= ? LIMIT 1",
        (reader.portfolio_id, now),
    ).fetchone()
    owner_allocation_unknown = other_portfolio is not None and any(
        Decimal(row["graph_allocation_native"]) != 0 for row in expense["full_bills"]
    )
    ai_amount = (None if owner_allocation_unknown or subscription["amount"] is None or api_unknown
                 else Decimal(subscription["amount"]) + known_api_ai)
    other_amount = (None if owner_allocation_unknown or other["amount"] is None or recorded_other is None
                    else Decimal(other["amount"]) + recorded_other)
    performance = data["performance"]
    trading = performance["trading_pnl"]
    reason_labels = {"owner_subscription_completeness_missing": "Subscription expense evidence is incomplete.",
                     "owner_other_completeness_missing": "Other operating expense evidence is incomplete."}
    owner_reasons = expense["coverage"].get("unknown_reasons", [])
    reasons = [reason_labels.get(reason, reason.replace("_", " ")) for reason in owner_reasons]
    codes = [_OWNER_EXPENSE_REASON_CODES.get(reason, "other") for reason in owner_reasons]
    if owner_allocation_unknown:
        reasons.append("Owner bills allocate to the deployment; this portfolio's share is unknown.")
        codes.append("shared_between_accounts")
    if data["valuation"].get("provisional"):
        reasons.append("Current valuation is incomplete or stale.")
        codes.append("valuation_not_final")
    if existing["allocation_issues"] or existing["holds"]["uncertain"] != Decimal(0) or api_unknown:
        reasons.append("API receipt or allocation coverage remains incomplete.")
        codes.append("ai_usage_records_incomplete")
    if recorded_other is None:
        reasons.append("Recorded operating expense valuation remains incomplete.")
        codes.append("other_cost_value_unknown")
    invoice_uncertainty = _invoice_uncertainty(current_reader, now)
    if invoice_uncertainty:
        reasons.append("Recorded invoices have unexplained differences.")
        codes.append("invoice_differences")
    recovery = runtime.recovery_history() if hasattr(runtime, "recovery_history") else {"status": "NOT_CONFIGURED"}
    if recovery.get("status") in {"PARTIAL_HISTORY", "UNAVAILABLE"}:
        reasons.append("Historical recovery limitations prevent a complete economic conclusion.")
        codes.append("history_gap")
    net = None
    if trading is not None and ai_amount is not None and other_amount is not None and not reasons:
        net = Decimal(trading) - ai_amount - other_amount
    economic_result = {"status": "unknown" if net is None else "covered" if net >= 0 else "not_covered",
                       "trading_after_execution_costs": trading,
                       "allocated_ai_expense": financial._amount(ai_amount),
                       "other_operating_expense": financial._amount(other_amount),
                       "known_api_ai_expense": canonical_decimal(known_api_ai),
                       "known_other_expense": financial._amount(recorded_other),
                       "net_result": financial._amount(net), "unknown_reasons": reasons,
                       "currency": data["reporting_currency"], "period": period,
                       "basis": "Trading fees, spread and slippage already affect trading result; "
                                "do not deduct twice."}
    return {"financial": data, "now": now, "period": period, "owner_expenses": expense,
            "recovery_history": recovery, "invoice_uncertainty": invoice_uncertainty,
            "economic_result": economic_result, "unknown_reason_codes": codes}


def overview(runtime, *, reporting_end: str | None = None) -> dict:
    economic = economics(runtime, reporting_end=reporting_end)
    data, now, period = economic["financial"], economic["now"], economic["period"]
    current_reader = projection_runtime(runtime)
    operations = health(current_reader, authenticated=True)
    active = activity.overview(current_reader)
    service = active.get("service", {})
    op_reasons = []
    pause = operations["pause"]["profile"]
    if pause != "RUNNING":
        op_reasons.append(f"Owner control is {pause}; reconciliation and protection policy remains in force.")
    heartbeat = service.get("heartbeat_at")
    if not heartbeat or (parse_utc(now) - parse_utc(heartbeat)).total_seconds() > 90:
        op_reasons.append("A current worker heartbeat has not been observed.")
    if operations["uncertain_orders"]:
        op_reasons.append("Order status requires reconciliation.")
    if service.get("status") not in {"RUNNING", "STARTING", "ATTACHED_EXISTING"}:
        op_reasons.append("Normal worker operation is not currently observed.")
    resource = resources.observation(runtime)
    op_reasons.extend(resource["warnings"])
    op_status = "operating" if not op_reasons else "paused" if pause != "RUNNING" else "attention"
    data.update(as_of=now, health=operations, recovery_history=economic["recovery_history"], activity=active,
                owner_expenses=economic["owner_expenses"], reporting_period=period,
                external_observations={"resources": resource},
                operational_health={"status": op_status, "reasons": op_reasons, "service": service,
                                    "basis": "persisted heartbeat, control and reconciliation records"},
                economic_result=economic["economic_result"])
    return redact(data)
