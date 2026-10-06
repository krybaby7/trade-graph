"""Read-only financial projections from durable business records.

Reporting amounts retain their event-time FX basis. Receipt accruals and their
ledger mirrors count once; reservations classify funds rather than create equity.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from trade_graph.api.security import redact
from trade_graph.application.budget import BudgetGateway
from trade_graph.contracts.models import FillRecord
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.money import canonical_decimal
from trade_graph.kernel.books import FxRate, Mark, mark_equity

ZERO = Decimal("0")
UNCERTAIN_ORDERS = {"UNKNOWN", "SUBMITTING", "CANCEL_PENDING"}


def _amount(value: Decimal | None) -> str | None:
    return None if value is None else canonical_decimal(value)


def _json(value: str | None) -> dict:
    return {} if value is None else json.loads(value)


def _rows(runtime, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(row) for row in runtime.database.execute(sql, params).fetchall()]


def _page(items: list, limit: int, offset: int) -> tuple[list, dict]:
    if not 1 <= limit <= 200 or offset < 0:
        raise ValueError("pagination requires 1 <= limit <= 200 and offset >= 0")
    return items[offset : offset + limit], {
        "limit": limit,
        "offset": offset,
        "total": len(items),
        "has_more": offset + limit < len(items),
        "next_offset": offset + limit if offset + limit < len(items) else None,
    }


def _fx(runtime, currency: str, reporting: str, at: str) -> dict | None:
    if currency == reporting:
        return {
            "base": currency,
            "quote": reporting,
            "rate": "1",
            "kind": "identity",
            "source": "native currency",
            "observed_at": at,
            "stale": False,
        }
    rows = _rows(
        runtime,
        """SELECT * FROM fx_rates WHERE base = ? AND quote = ?
        AND observed_at <= ? AND valid_as_of <= ? AND retrieved_at <= ?
        ORDER BY observed_at DESC, retrieved_at DESC, rowid DESC LIMIT 1""",
        (currency, reporting, at, at, at),
    )
    if not rows:
        return None
    _rate_freshness(runtime, rows[0], at)
    return rows[0]


def _age(at: str, observed_at: str) -> int:
    current = datetime.fromisoformat(at.replace("Z", "+00:00"))
    observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    return max(0, int((current - observed).total_seconds()))


def _expired(at: str, observed_at: str, maximum_age: int) -> bool:
    current = datetime.fromisoformat(at.replace("Z", "+00:00"))
    observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    return current > observed + timedelta(seconds=maximum_age)


def _rate_freshness(runtime, row: dict, at: str) -> None:
    reference = "reference" in row["kind"].lower() or "daily" in row["kind"].lower()
    maximum_age = int(getattr(runtime, "reporting_fx_max_age_seconds", 7 * 86400 if reference else 86400))
    row["age_seconds"] = _age(at, row["observed_at"])
    row["freshness_age_seconds"] = _age(at, row["valid_as_of"])
    row["maximum_age_seconds"] = maximum_age
    row["recorded_stale"] = bool(row["stale"])
    row["stale"] = row["recorded_stale"] or _expired(at, row["valid_as_of"], maximum_age)


def _mark_maximum_age(runtime, at: str) -> int:
    rows = _rows(
        runtime,
        """SELECT document_json FROM owner_policy_revisions WHERE created_at <= ?
        ORDER BY created_at DESC, rowid DESC LIMIT 1""",
        (at,),
    )
    maximum_age = 30 if not rows else int(_json(rows[0]["document_json"])["maximum_quote_age_seconds"])
    mandates = _rows(
        runtime,
        """SELECT document_json FROM mandates WHERE portfolio_id = ?
        AND active = 1 AND created_at <= ?""",
        (runtime.portfolio_id, at),
    )
    if mandates:
        maximum_age = min(maximum_age, int(_json(mandates[0]["document_json"])["max_quote_age_seconds"]))
    return maximum_age


def _convert(
    runtime, amount: Decimal, currency: str, reporting: str, at: str
) -> tuple[Decimal | None, bool, dict | None]:
    basis = _fx(runtime, currency, reporting, at)
    if basis is None:
        return None, True, None
    return amount * Decimal(basis["rate"]), bool(basis["stale"]), basis


def _sum(values: list[Decimal | None]) -> Decimal | None:
    return None if any(value is None for value in values) else sum(values, ZERO)


def _native(values: dict[str, Decimal], key: str = "asset") -> list[dict]:
    return [{key: asset, "amount": _amount(amount)} for asset, amount in sorted(values.items())]


def _original_basis(native: str, native_currency: str, reporting: str | None, reporting_currency: str | None) -> dict:
    """Retain stored accruals even where the old schema has no linked rate ID."""
    rate = None
    if reporting is not None and Decimal(native) != 0:
        rate = Decimal(reporting) / Decimal(native)
    return {
        "base": native_currency,
        "quote": reporting_currency,
        "rate": _amount(rate),
        "source": "stored native and reporting accrual amounts",
        "rate_id": None,
        "provenance_complete": native_currency == reporting_currency,
    }


def _context(runtime) -> tuple[str, dict, str]:
    at = utc_iso(runtime.clock.now())
    portfolio = _rows(runtime, "SELECT * FROM portfolios WHERE portfolio_id = ?", (runtime.portfolio_id,))[0]
    return at, portfolio, portfolio["reporting_currency"]


def _valuation(runtime, at: str) -> dict:
    rates = _rows(
        runtime,
        """SELECT * FROM fx_rates WHERE observed_at <= ?
        AND valid_as_of <= ? AND retrieved_at <= ? ORDER BY observed_at, retrieved_at, rowid""",
        (at, at, at),
    )
    latest_fx = {(row["base"], row["quote"]): row for row in rates}
    marks = _rows(
        runtime,
        """SELECT * FROM valuation_marks WHERE portfolio_id = ? AND observed_at <= ?
        ORDER BY observed_at, rowid""",
        (runtime.portfolio_id, at),
    )
    latest_marks = {row["asset"]: row for row in marks}
    for row in latest_fx.values():
        _rate_freshness(runtime, row, at)
    mark_maximum_age = _mark_maximum_age(runtime, at)
    for row in latest_marks.values():
        row["age_seconds"] = _age(at, row["observed_at"])
        row["maximum_age_seconds"] = mark_maximum_age
        row["recorded_stale"] = bool(row["stale"])
        row["stale"] = row["recorded_stale"] or _expired(at, row["observed_at"], mark_maximum_age)
    return {"as_of": at, "fx": list(latest_fx.values()), "marks": list(latest_marks.values())}


def _equity(books, reporting: str, at: str, valuation: dict):
    """Use the same available-at observations as the displayed valuation basis."""
    rates = [
        FxRate(
            row["base"],
            row["quote"],
            Decimal(row["rate"]),
            max(row["observed_at"], row["valid_as_of"]),
            row["stale"],
            row["source"],
            row["rate_id"],
            row["retrieved_at"],
        )
        for row in valuation["fx"]
    ]
    marks = [
        Mark(row["asset"], Decimal(row["mark"]), row["quote_currency"], row["observed_at"], row["stale"], row["source"])
        for row in valuation["marks"]
    ]
    return mark_equity(books, reporting=reporting, marks=marks, rates=rates, at=at)


def _phase(usage: dict, task_input: dict, purpose: str) -> str:
    # An explicit accounting classification is evidence. A role name is not.
    value = usage.get("cost_phase", task_input.get("cost_phase"))
    if value in {"setup", "recurring"}:
        return value
    prefix = purpose.lower().split(":", 1)[0].strip()
    return prefix if prefix in {"setup", "recurring"} else "unclassified"


def _expense_records(runtime) -> list[dict]:
    rows = _rows(
        runtime,
        """SELECT portfolio_id, payload_json, effective_at, sequence FROM ledger_events
        WHERE kind = 'expense' AND effective_at <= ? ORDER BY effective_at, sequence, event_id""",
        (utc_iso(runtime.clock.now()),),
    )
    return [
        {**_json(row["payload_json"]), "portfolio_id": row["portfolio_id"], "at": row["effective_at"]} for row in rows
    ]


def _cost_state(runtime, currency: str = "EUR") -> dict:
    """Accruals globally and allocations per portfolio, with historical conversion."""
    at = utc_iso(runtime.clock.now())
    receipts = _rows(
        runtime,
        """SELECT r.*, b.deployment_id, b.role, b.task_id, b.root_task_id,
        b.system_version_id, b.attempt_kind, b.purpose, b.price_card_id, b.state AS reservation_state
        FROM usage_receipts r JOIN budget_reservations b USING (reservation_id)
        WHERE r.created_at <= ? ORDER BY r.created_at DESC, r.receipt_id""",
        (at,),
    )
    reservations = _rows(
        runtime,
        """SELECT * FROM budget_reservations WHERE created_at <= ?
        ORDER BY created_at DESC, reservation_id""",
        (at,),
    )
    allocations = _rows(runtime, "SELECT * FROM cost_allocations ORDER BY receipt_id, portfolio_id")
    by_receipt = defaultdict(list)
    for allocation in allocations:
        by_receipt[allocation["receipt_id"]].append(allocation)
    tasks = {row["task_id"]: row for row in _rows(runtime, "SELECT task_id, portfolio_id, input_json FROM tasks")}
    invocations = {
        row["reservation_id"]: row
        for row in _rows(runtime, "SELECT reservation_id, portfolio_id, run_id, state FROM model_invocations")
    }
    expenses = [record for record in _expense_records(runtime) if record["at"] <= at]
    settlements = {record["settles"] for record in expenses if record.get("settles")}
    mirrors = defaultdict(list)
    receipt_ids = {row["receipt_id"] for row in receipts}
    reservation_ids = {row["reservation_id"]: row["receipt_id"] for row in receipts}
    independent = []
    seen_expenses = set()
    for expense in expenses:
        if expense.get("settles"):
            continue
        expense_id = expense["expense_id"]
        source_ref = expense["source"].removeprefix("receipt:")
        linked = expense_id if expense_id in receipt_ids else reservation_ids.get(expense_id)
        linked = linked or (source_ref if source_ref in receipt_ids else None)
        if linked:
            mirrors[linked].append(expense)
        elif expense_id not in seen_expenses:
            independent.append(expense)
            seen_expenses.add(expense_id)

    actual = []
    synthetic = []
    allocated = []
    allocated_phases = {name: [] for name in ("setup", "recurring", "unclassified")}
    settled = []
    phases = {name: [] for name in ("setup", "recurring", "unclassified")}
    attribution = {name: defaultdict(list) for name in ("role", "task", "root", "run", "provider", "model", "version")}
    provisional = False
    allocation_issues = []
    synthetic_allocation_issues = []
    output_receipts = []
    for receipt in receipts:
        usage = _json(receipt.pop("usage_json"))
        task = tasks.get(receipt["task_id"], {})
        invocation = invocations.get(receipt["reservation_id"], {})
        receipt["usage"] = usage
        receipt["synthetic"] = bool(receipt["synthetic"])
        receipt["run_id"] = invocation.get("run_id")
        receipt["attempt_state"] = invocation.get("state", receipt["status"])
        receipt["phase"] = _phase(usage, _json(task.get("input_json")), receipt["purpose"])
        receipt["settled"] = receipt["status"] == "settled" or receipt["receipt_id"] in settlements
        receipt["unresolved"] = receipt["status"] in {"uncertain", "conservative_charge"}
        amount, bad, basis = (None, True, None)
        if receipt["reporting_cost"] is not None:
            amount, bad, basis = _convert(
                runtime,
                Decimal(receipt["reporting_cost"]),
                receipt["reporting_currency"],
                currency,
                receipt["created_at"],
            )
        receipt["valuation"] = {
            "amount": _amount(amount),
            "currency": currency,
            "at": receipt["created_at"],
            "fx": basis,
            "provisional": bad,
        }
        receipt["original_fx_basis"] = _original_basis(
            receipt["native_cost"],
            receipt["native_currency"],
            receipt["reporting_cost"],
            receipt["reporting_currency"],
        )
        expense_mirrors = mirrors[receipt["receipt_id"]]
        receipt["embedded"] = any(record["embedded"] for record in expense_mirrors)
        if any(record["expense_id"] in settlements for record in expense_mirrors):
            receipt["settled"] = True
        receipt["allocations"] = by_receipt[receipt["receipt_id"]]
        portions = receipt["allocations"]
        if portions:
            weight = sum((Decimal(portion["weight"]) for portion in portions), ZERO)
            total = sum((Decimal(portion["amount"]) for portion in portions), ZERO)
            if weight != 1 or receipt["reporting_cost"] is None or total != Decimal(receipt["reporting_cost"]):
                issues = synthetic_allocation_issues if receipt["synthetic"] else allocation_issues
                issues.append(receipt["receipt_id"])
        owner = invocation.get("portfolio_id") or task.get("portfolio_id")
        if owner is None and len({record["portfolio_id"] for record in expense_mirrors}) == 1:
            owner = expense_mirrors[0]["portfolio_id"]
        native_allocated = sum(
            (Decimal(portion["amount"]) for portion in portions if portion["portfolio_id"] == runtime.portfolio_id),
            ZERO,
        )
        allocated_amount = ZERO
        allocated_bad = False
        if portions:
            allocated_amount, allocated_bad, _ = _convert(
                runtime, native_allocated, receipt["reporting_currency"], currency, receipt["created_at"]
            )
        elif owner == runtime.portfolio_id:
            allocated_amount, allocated_bad = amount, bad
        receipt["allocated_amount"] = _amount(allocated_amount)
        receipt["allocation_basis"] = (
            "explicit weights" if portions else ("portfolio attribution" if owner else "unallocated deployment expense")
        )
        # An uncertain usage record is evidence of exposure, not a known accrual.
        if receipt["status"] != "uncertain":
            (synthetic if receipt["synthetic"] else actual).append(amount)
            if not receipt["synthetic"]:
                allocated.append(allocated_amount)
                allocated_phases[receipt["phase"]].append(allocated_amount)
                phases[receipt["phase"]].append(amount)
                if receipt["settled"]:
                    settled.append(amount)
                dimensions = {
                    "role": receipt["role"],
                    "task": receipt["task_id"],
                    "root": receipt["root_task_id"],
                    "run": receipt["run_id"],
                    "provider": receipt["provider"],
                    "model": receipt["model"],
                    "version": receipt["system_version_id"],
                }
                for dimension, key in dimensions.items():
                    attribution[dimension][key or "unattributed"].append(amount)
                provisional = provisional or bad or allocated_bad or receipt["unresolved"]
        output_receipts.append(receipt)

    ledger_expenses = []
    for expense in independent:
        amount, bad, basis = _convert(
            runtime, Decimal(expense["reporting_amount"]), expense["reporting_currency"], currency, expense["at"]
        )
        expense["valuation"] = {"amount": _amount(amount), "currency": currency, "fx": basis, "provisional": bad}
        expense["original_fx_basis"] = _original_basis(
            expense["native_amount"],
            expense["native_currency"],
            expense["reporting_amount"],
            expense["reporting_currency"],
        )
        expense["settled"] = expense["expense_id"] in settlements
        expense["phase"] = _phase(expense, {}, expense["source"])
        actual.append(amount)
        phases[expense["phase"]].append(amount)
        if expense["portfolio_id"] == runtime.portfolio_id:
            allocated.append(amount)
            allocated_phases[expense["phase"]].append(amount)
        if expense["settled"]:
            settled.append(amount)
        for dimension in attribution:
            attribution[dimension]["other operating" if dimension == "role" else "unattributed"].append(amount)
        provisional = provisional or bad
        ledger_expenses.append(expense)
    holds = {"reserved": [], "uncertain": []}
    for reservation in reservations:
        reservation["synthetic"] = bool(reservation["synthetic"])
        if not reservation["synthetic"] and reservation["state"] in {"RESERVED", "UNCERTAIN"}:
            value, bad, _ = _convert(
                runtime, Decimal(reservation["amount"]), reservation["currency"], currency, reservation["created_at"]
            )
            holds["uncertain" if reservation["state"] == "UNCERTAIN" else "reserved"].append(value)
            provisional = provisional or bad or reservation["state"] == "UNCERTAIN"
    return {
        "actual": _sum(actual),
        "synthetic": _sum(synthetic),
        "allocated": _sum(allocated),
        "settled": _sum(settled),
        "phases": {key: _sum(values) for key, values in phases.items()},
        "allocated_phases": {key: _sum(values) for key, values in allocated_phases.items()},
        "holds": {key: _sum(values) for key, values in holds.items()},
        "receipts": output_receipts,
        "reservations": reservations,
        "ledger_expenses": ledger_expenses,
        "allocations": allocations,
        "allocation_issues": allocation_issues,
        "synthetic_allocation_issues": synthetic_allocation_issues,
        "attribution": {
            key: {identity: _amount(_sum(values)) for identity, values in groups.items()}
            for key, groups in attribution.items()
        },
        "provisional": provisional
        or bool(allocation_issues)
        or any(
            Decimal(row["unexplained"]) != 0
            for row in _rows(runtime, "SELECT unexplained FROM invoice_reconciliations WHERE created_at <= ?", (at,))
        ),
    }


def costs(runtime, limit: int = 50, offset: int = 0) -> dict:
    with runtime.database.snapshot():
        at, portfolio, _ = _context(runtime)
        state = _cost_state(runtime)
        deployment_id = getattr(runtime, "deployment_id", "deployment")
        budgets = _rows(runtime, "SELECT * FROM deployment_budget WHERE deployment_id = ?", (deployment_id,))
        remaining = None if not budgets else BudgetGateway(runtime.database, runtime.clock).remaining(deployment_id)
        receipts, pagination = _page(state["receipts"], limit, offset)
        reservations, reservation_pagination = _page(state["reservations"], limit, offset)
        invoices = _rows(
            runtime,
            """SELECT * FROM invoice_reconciliations WHERE created_at <= ?
            ORDER BY created_at DESC, invoice_id""",
            (at,),
        )
        embedded = []
        for expense in runtime.ledger.books(runtime.portfolio_id).expenses:
            if expense.embedded and not expense.settles:
                value, _, _ = _convert(runtime, expense.reporting_amount, expense.reporting_currency, "EUR", expense.at)
                embedded.append(value)
        return redact(
            {
                "actual_spend": _amount(state["actual"]),
                "actual_spend_basis": "actual resource accruals; settlements count once",
                "currency": "EUR",
                "as_of": at,
                "synthetic_spend": _amount(state["synthetic"]),
                "allocated_actual_spend": _amount(state["allocated"]),
                "allocated_cost_phases": {key: _amount(value) for key, value in state["allocated_phases"].items()},
                "simulated": portfolio["mode"] == "paper",
                "simulated_trading_separate": True,
                "remaining_allowance": _amount(remaining),
                "uncertain_reservations": sum(
                    not row["synthetic"] and row["state"] == "UNCERTAIN" for row in state["reservations"]
                ),
                "paper_equity_does_not_refill_allowance": True,
                "provisional": state["provisional"] or any(Decimal(row["unexplained"]) != 0 for row in invoices),
                "summary": {
                    "actual_accrued": _amount(state["actual"]),
                    "actual_settled": _amount(state["settled"]),
                    "synthetic_accrued": _amount(state["synthetic"]),
                    "reserved": _amount(state["holds"]["reserved"]),
                    "uncertain": _amount(state["holds"]["uncertain"]),
                    "embedded_operating": _amount(_sum(embedded)),
                    **{key: _amount(value) for key, value in state["phases"].items()},
                },
                "budget": None if not budgets else budgets[0],
                "role_allocations": _rows(
                    runtime, "SELECT * FROM role_allocations WHERE deployment_id = ?", (deployment_id,)
                ),
                "receipts": receipts,
                "pagination": pagination,
                "reservations": reservations,
                "reservation_pagination": reservation_pagination,
                "ledger_expenses": state["ledger_expenses"],
                "allocations": state["allocations"],
                "allocation_issues": state["allocation_issues"],
                "synthetic_allocation_issues": state["synthetic_allocation_issues"],
                "invoice_adjustments": invoices,
                "attribution_basis": "actual accruals counted once globally; synthetic receipts excluded",
                **{"by_" + key: value for key, value in state["attribution"].items()},
            }
        )


def _fill_events(runtime) -> list[dict]:
    events = _rows(
        runtime,
        """SELECT payload_json, effective_at FROM ledger_events
        WHERE portfolio_id = ? AND kind = 'fill' AND effective_at <= ? ORDER BY sequence""",
        (runtime.portfolio_id, utc_iso(runtime.clock.now())),
    )
    result = []
    for row in events:
        payload = _json(row["payload_json"])
        event_time = datetime.fromisoformat(payload["fill"]["filled_at_utc"].replace("Z", "+00:00"))
        result.append({**payload, "at": row["effective_at"], "filled_at": utc_iso(event_time)})
    return result


def _drawdown(runtime, reporting: str, at: str, books, flows: list[dict]) -> dict:
    """Absolute trading drawdown on retained, cash-flow-adjusted observations.

    This is a discrete additive currency series, not a percentage return or a
    continuous price history. Real operating expenses are added back to match
    the trading-P&L boundary; historical cost-allocation revisions are not stored.
    """
    result = {
        "value": None,
        "metric": "maximum absolute drawdown across retained observations",
        "current": None,
        "currency": reporting,
        "benchmark_value": None,
        "benchmark_current": None,
        "basis": "equity minus subsequent event-valued external flows plus embedded operating expenses",
        "expense_basis": "trading after exchange fees; before real operating expenses",
        "sampling": "opening capital and retained financial/mark/FX timestamps, plus current time",
        "sample_count": 0,
        "valid_sample_count": 0,
        "provisional": False,
        "period_start": None,
        "period_end": at,
        "samples": [],
    }
    opening = next((flow for flow in flows if flow["opening"]), None)
    if opening is None:
        return {**result, "reason": "No opening capital evidence"}
    start = opening["at"]
    result["period_start"] = start
    sources = defaultdict(set)
    for row in _rows(
        runtime,
        """SELECT effective_at FROM ledger_events WHERE portfolio_id = ?
        AND effective_at >= ? AND effective_at <= ? ORDER BY effective_at""",
        (runtime.portfolio_id, start, at),
    ):
        sources[row["effective_at"]].add("ledger")
    held_assets = {lot.asset for lot in books.lots}
    for row in _rows(
        runtime,
        """SELECT asset, observed_at FROM valuation_marks WHERE portfolio_id = ?
        AND observed_at >= ? AND observed_at <= ?""",
        (runtime.portfolio_id, start, at),
    ):
        if row["asset"] in held_assets:
            sources[row["observed_at"]].add("mark")
    currencies = set(books.cash) | {flow["asset"] for flow in flows} | {lot.cost_currency for lot in books.lots}
    currencies.update(
        row["quote_currency"]
        for row in _rows(
            runtime,
            "SELECT DISTINCT asset, quote_currency FROM valuation_marks WHERE portfolio_id = ? AND observed_at <= ?",
            (runtime.portfolio_id, at),
        )
        if row["asset"] in held_assets
    )
    for row in _rows(
        runtime,
        """SELECT base, quote, observed_at, valid_as_of, retrieved_at FROM fx_rates
        WHERE observed_at <= ? AND valid_as_of <= ? AND retrieved_at <= ?""",
        (at, at, at),
    ):
        if row["base"] in currencies and row["base"] != reporting and row["quote"] == reporting:
            available_at = max(row["observed_at"], row["valid_as_of"], row["retrieved_at"])
            if available_at >= start:
                sources[available_at].add("fx")
    sources[at].add("current")
    addbacks = []
    for expense in books.expenses:
        if expense.embedded and not expense.settles:
            value, bad, _ = _convert(
                runtime, expense.reporting_amount, expense.reporting_currency, reporting, expense.at
            )
            addbacks.append({"at": expense.at, "value": value, "provisional": bad})
    opening_value = None if opening["reporting_amount"] is None else Decimal(opening["reporting_amount"])
    samples = [
        {
            "at": start,
            "sources": ["opening capital"],
            "adjusted_equity": _amount(opening_value),
            "benchmark_adjusted_equity": _amount(opening_value),
            "provisional": opening["provisional"],
            "reason": "Opening capital FX unavailable or stale" if opening["provisional"] else None,
        }
    ]
    adjusted_values = []
    benchmark_values = []
    if opening_value is not None and not opening["provisional"]:
        adjusted_values.append(opening_value)
        benchmark_values.append(opening_value)
    for observed_at in sorted(sources):
        sample_books = runtime.ledger.books(runtime.portfolio_id, observed_at)
        sample_equity = _equity(sample_books, reporting, observed_at, _valuation(runtime, observed_at))
        included_flows = [flow for flow in flows if not flow["opening"] and flow["at"] <= observed_at]
        external = _sum(
            [
                None
                if flow["reporting_amount"] is None
                else Decimal(flow["reporting_amount"]) * (1 if flow["kind"] == "deposit" else -1)
                for flow in included_flows
            ]
        )
        included_addbacks = [expense for expense in addbacks if expense["at"] <= observed_at]
        embedded = _sum([expense["value"] for expense in included_addbacks])
        bad = (
            sample_equity.provisional
            or opening["provisional"]
            or any(flow["provisional"] for flow in included_flows)
            or any(expense["provisional"] for expense in included_addbacks)
        )
        adjusted = (
            None
            if sample_equity.equity is None or external is None or embedded is None
            else sample_equity.equity - external + embedded
        )
        benchmark = None if sample_equity.baseline is None or external is None else sample_equity.baseline - external
        samples.append(
            {
                "at": observed_at,
                "sources": sorted(sources[observed_at]),
                "equity": _amount(sample_equity.equity),
                "external_flow_since_opening": _amount(external),
                "embedded_operating": _amount(embedded),
                "adjusted_equity": _amount(adjusted),
                "benchmark_adjusted_equity": _amount(benchmark),
                "provisional": bad,
                "reason": "Valuation, flow or operating FX unavailable or stale" if bad else None,
            }
        )
        if not bad and adjusted is not None and benchmark is not None:
            adjusted_values.append(adjusted)
            benchmark_values.append(benchmark)
    result.update({"samples": samples, "sample_count": len(samples), "valid_sample_count": len(adjusted_values)})
    if len(adjusted_values) != len(samples):
        return {**result, "provisional": True, "reason": "Retained series contains missing or stale valuations"}
    if len(samples) < 2 or (
        len(sources) == 1
        and not any(
            row["kind"] == "fill"
            for row in _rows(
                runtime,
                """SELECT kind FROM ledger_events
        WHERE portfolio_id = ? AND effective_at <= ?""",
                (runtime.portfolio_id, at),
            )
        )
    ):
        return {**result, "reason": "At least two retained valuation observations are required"}

    def losses(values: list[Decimal]) -> tuple[Decimal, Decimal]:
        peak, maximum, current = values[0], ZERO, ZERO
        for value in values[1:]:
            peak = max(peak, value)
            current = peak - value
            maximum = max(maximum, current)
        return maximum, current

    maximum, current = losses(adjusted_values)
    benchmark_maximum, benchmark_current = losses(benchmark_values)
    return {
        **result,
        "value": _amount(maximum),
        "current": _amount(current),
        "benchmark_value": _amount(benchmark_maximum),
        "benchmark_current": _amount(benchmark_current),
        "reason": None,
    }


def overview(runtime) -> dict:
    with runtime.database.snapshot():
        at, portfolio, reporting = _context(runtime)
        books = runtime.ledger.books(runtime.portfolio_id, at)
        valuation = _valuation(runtime, at)
        equity = _equity(books, reporting, at, valuation)
        cost = _cost_state(runtime, reporting)
        global_cost = cost if reporting == "EUR" else _cost_state(runtime)
        flows = []
        opening = None
        flow_amounts = []
        subsequent = []
        baseline_native = defaultdict(lambda: ZERO)
        for flow in books.flows:
            if flow.kind == "internal":
                continue
            value, bad, fx = _convert(runtime, flow.amount, flow.asset, reporting, flow.at)
            sign = Decimal("1") if flow.kind == "deposit" else Decimal("-1")
            is_opening = opening is None and flow.kind == "deposit"
            item = {
                "kind": flow.kind,
                "asset": flow.asset,
                "amount": _amount(flow.amount),
                "reporting_amount": _amount(value),
                "at": flow.at,
                "ref": flow.ref,
                "fx": fx,
                "provisional": bad,
                "opening": is_opening,
            }
            if is_opening:
                opening = item
            else:
                subsequent.append(None if value is None else sign * value)
            flows.append(item)
            flow_amounts.append(None if value is None else sign * value)
            baseline_native[flow.asset] += sign * flow.amount
        all_flows = _sum(flow_amounts)
        embedded = []
        embedded_provisional = False
        for expense in books.expenses:
            if expense.embedded and not expense.settles:
                value, bad, _ = _convert(
                    runtime, expense.reporting_amount, expense.reporting_currency, reporting, expense.at
                )
                embedded.append(value)
                embedded_provisional = embedded_provisional or bad
        embedded_total = _sum(embedded)
        provisional = (
            equity.provisional
            or cost["provisional"]
            or embedded_provisional
            or any(flow["provisional"] for flow in flows)
        )
        trading = None
        economic = None
        valuation_provisional = equity.provisional or embedded_provisional or any(flow["provisional"] for flow in flows)
        if (
            not valuation_provisional
            and equity.equity is not None
            and all_flows is not None
            and embedded_total is not None
        ):
            trading = equity.equity - all_flows + embedded_total
            if cost["allocated"] is not None and not cost["provisional"]:
                economic = trading - cost["allocated"]
        realized = []
        fill_events = _fill_events(runtime)
        fill_times = {event["fill"]["trade_id"]: event["filled_at"] for event in fill_events}
        for lot in books.lots:
            for disposal in lot.disposals:
                disposal_at = fill_times.get(disposal.source_ref.removesuffix(":fee"), disposal.at)
                value, bad, _ = _convert(runtime, disposal.realized, disposal.proceeds_currency, reporting, disposal_at)
                realized.append(value)
                provisional = provisional or bad
        fee_native = defaultdict(lambda: ZERO)
        fee_reporting = []
        fee_items = []
        for event in fill_events:
            fill = event["fill"]
            for component in FillRecord.model_validate(fill).fee_legs():
                fee = component.amount
                fee_native[component.asset] += fee
                quote = event["quote_asset"]
                if component.asset == quote:
                    quote_value = fee
                elif component.asset == event["base_asset"]:
                    quote_value = fee * Decimal(fill["price"])
                else:
                    quote_value = None if component.identified_rate is None else fee * component.identified_rate
                value, bad, fx = (
                    (None, True, None) if quote_value is None
                    else _convert(runtime, quote_value, quote, reporting, event["filled_at"])
                )
                fee_reporting.append(value)
                provisional = provisional or bad
                item = {
                    "trade_id": fill["trade_id"], "asset": component.asset, "amount": _amount(fee),
                    "reporting_amount": _amount(value), "at": event["filled_at"],
                    "recorded_at": event["at"], "fx": fx,
                }
                if fill.get("fee_components") is not None:
                    item.update(source_ref=component.source_ref, rate_source_ref=component.rate_source_ref)
                fee_items.append(item)
        benchmark_values = [
            _convert(runtime, amount, asset, reporting, at)[0] for asset, amount in baseline_native.items()
        ]
        benchmark = _sum(benchmark_values)
        valuation["provisional"] = equity.provisional
        return redact(
            {
                "portfolio_id": runtime.portfolio_id,
                "experiment_id": portfolio["experiment_id"],
                "reporting_currency": reporting,
                "equity": _amount(equity.equity),
                "provisional": provisional,
                "simulated": portfolio["mode"] == "paper",
                "actual_spend": _amount(global_cost["actual"]),
                "actual_spend_currency": "EUR",
                "as_of": at,
                "allocated_capital": {
                    "native": [] if opening is None else [{"asset": opening["asset"], "amount": opening["amount"]}],
                    "reporting_amount": None if opening is None else opening["reporting_amount"],
                    "at": None if opening is None else opening["at"],
                },
                "current_value": {
                    "cash": _amount(equity.cash_reporting),
                    "inventory": _amount(equity.inventory_reporting),
                    "total": _amount(equity.equity),
                    "currency": reporting,
                    "as_of": at,
                },
                "external_flows": {"net_reporting": _amount(_sum(subsequent)), "items": flows, "currency": reporting},
                "performance": {
                    "realized": _amount(_sum(realized)),
                    "unrealized": _amount(equity.unrealized),
                    "trading_fees": {
                        "native": _native(fee_native),
                        "reporting_amount": _amount(_sum(fee_reporting)),
                        "items": fee_items,
                        "currency": reporting,
                    },
                    "trading_pnl": _amount(trading),
                    "net_economic_pnl": _amount(economic),
                    "allocated_actual_operating": _amount(cost["allocated"]),
                    "embedded_operating": _amount(embedded_total),
                    "currency": reporting,
                    "provisional": provisional,
                    "basis": "inception; external flows valued at event time; trading fees already incurred",
                },
                "benchmark": {
                    "kind": "native cash",
                    "native": _native(baseline_native),
                    "value": _amount(benchmark),
                    "currency": reporting,
                    "fx_pnl": _amount(None if benchmark is None or all_flows is None else benchmark - all_flows),
                    "strategy_alpha": _amount(
                        None
                        if trading is None or benchmark is None or all_flows is None
                        else trading - (benchmark - all_flows)
                    ),
                },
                "drawdown": _drawdown(runtime, reporting, at, books, flows),
                "valuation": valuation,
                "cost_views": {
                    "all_in": _amount(cost["allocated"]),
                    "setup_global": _amount(cost["phases"]["setup"]),
                    "recurring_global": _amount(cost["phases"]["recurring"]),
                    "unclassified_global": _amount(cost["phases"]["unclassified"]),
                    "allocated_setup": _amount(cost["allocated_phases"]["setup"]),
                    "allocated_recurring": _amount(cost["allocated_phases"]["recurring"]),
                    "allocated_unclassified": _amount(cost["allocated_phases"]["unclassified"]),
                },
            }
        )


def _decision_maps(runtime) -> tuple[dict, dict]:
    at = utc_iso(runtime.clock.now())
    decisions = {
        row["decision_id"]: {**_json(row["payload_json"]), "system_version_id": row["system_version_id"]}
        for row in _rows(
            runtime, "SELECT * FROM decisions WHERE portfolio_id = ? AND created_at <= ?", (runtime.portfolio_id, at)
        )
    }
    intents = {
        row["intent_id"]: _json(row["payload_json"])
        for row in _rows(
            runtime,
            "SELECT intent_id, payload_json FROM order_intents WHERE portfolio_id = ? AND created_at <= ?",
            (runtime.portfolio_id, at),
        )
    }
    return decisions, intents


def positions(runtime, limit: int = 50, offset: int = 0) -> dict:
    with runtime.database.snapshot():
        at, portfolio, reporting = _context(runtime)
        books = runtime.ledger.books(runtime.portfolio_id, at)
        valuation = _valuation(runtime, at)
        equity = _equity(books, reporting, at, valuation)
        marks = {mark["asset"]: mark for mark in valuation["marks"]}
        reservations = _rows(
            runtime,
            """SELECT * FROM position_reservations
            WHERE portfolio_id = ? AND state = 'held' AND created_at <= ? ORDER BY reservation_id""",
            (runtime.portfolio_id, at),
        )
        reservations.extend(_rows(
            runtime, """SELECT reservation_id,asset,current_amount AS amount FROM native_fee_reservations
            WHERE portfolio_id=? AND state='held' AND created_at<=? ORDER BY reservation_id""",
            (runtime.portfolio_id, at),
        ))
        reserved = defaultdict(lambda: ZERO)
        for reservation in reservations:
            reserved[reservation["asset"]] += Decimal(reservation["amount"])
        decisions, intents = _decision_maps(runtime)
        trades = {}
        for event in _fill_events(runtime):
            fill = event["fill"]
            trade_id = fill["trade_id"]
            trades[trade_id] = fill
            trades[f"{trade_id}:fee"] = fill
            for index, _component in enumerate(fill.get("fee_components") or ()):
                trades[f"{trade_id}:fee:{index}"] = fill

        def provenance(ref: str) -> dict:
            fill = trades.get(ref, {})
            intent = intents.get(fill.get("intent_id"), {})
            decision_id = intent.get("decision_id")
            decision = decisions.get(decision_id, {})
            return {
                "decision_id": decision_id,
                "version_id": decision.get("system_version_id"),
                "strategy_id": decision.get("strategy_id"),
                "rationale": decision.get("rationale"),
                "invalidation": decision.get("invalidation"),
                "snapshot_id": decision.get("snapshot_id"),
            }

        balances = []
        provisional = False
        for asset, owned in sorted(books.cash.items()):
            value, bad, fx = _convert(runtime, owned, asset, reporting, at)
            provisional = provisional or bad
            balances.append(
                {
                    "asset": asset,
                    "owned": _amount(owned),
                    "reserved": _amount(reserved[asset]),
                    "available": _amount(owned - reserved[asset]),
                    "reporting_amount": _amount(value),
                    "currency": reporting,
                    "fx": fx,
                    "provisional": bad,
                    "as_of": at,
                }
            )
        projected = []
        assets = sorted({lot.asset for lot in books.lots if lot.open_quantity() > 0})
        for asset in assets:
            lots = [lot for lot in books.lots if lot.asset == asset and lot.open_quantity() > 0]
            quantity = sum((lot.open_quantity() for lot in lots), ZERO)
            native_cost = defaultdict(lambda: ZERO)
            costs_reporting = []
            lot_views = []
            theses = {}
            for lot in lots:
                native_cost[lot.cost_currency] += lot.open_cost()
                cost_value, bad, _ = _convert(runtime, lot.open_cost(), lot.cost_currency, reporting, at)
                provisional = provisional or bad
                costs_reporting.append(cost_value)
                origin = provenance(lot.source_ref)
                if origin["decision_id"]:
                    theses[origin["decision_id"]] = origin
                disposals = []
                for disposal in lot.disposals:
                    closing = provenance(disposal.source_ref)
                    disposals.append(
                        {
                            "quantity": _amount(disposal.quantity),
                            "cost_released": _amount(disposal.cost_released),
                            "proceeds": _amount(disposal.proceeds),
                            "currency": disposal.proceeds_currency,
                            "realized": _amount(disposal.realized),
                            "at": disposal.at,
                            "source_ref": disposal.source_ref,
                            "closing_decision_id": closing["decision_id"],
                            "closing_version_id": closing["version_id"],
                        }
                    )
                lot_views.append(
                    {
                        "lot_id": lot.lot_id,
                        "asset": asset,
                        "quantity_original": _amount(lot.quantity_original),
                        "cost_original": _amount(lot.cost_original),
                        "open_quantity": _amount(lot.open_quantity()),
                        "open_cost": _amount(lot.open_cost()),
                        "cost_currency": lot.cost_currency,
                        "opened_at": lot.opened_at,
                        "source_ref": lot.source_ref,
                        "opening_decision_id": origin["decision_id"],
                        "opening_version_id": origin["version_id"],
                        "disposals": disposals,
                    }
                )
            mark = marks.get(asset)
            value, bad, fx = (
                (None, True, None)
                if mark is None
                else _convert(runtime, quantity * Decimal(mark["mark"]), mark["quote_currency"], reporting, at)
            )
            bad = bad or mark is None or mark["stale"]
            provisional = provisional or bad
            cost_value = _sum(costs_reporting)
            projected.append(
                {
                    "asset": asset,
                    "quantity": _amount(quantity),
                    "reserved": _amount(reserved[asset]),
                    "available": _amount(quantity - reserved[asset]),
                    "cost_native": _native(native_cost, "currency"),
                    "reporting_amount": _amount(value),
                    "cost_reporting": _amount(cost_value),
                    "unrealized": _amount(None if value is None or cost_value is None else value - cost_value),
                    "currency": reporting,
                    "exposure_fraction": _amount(
                        None if value is None or equity.equity is None or equity.equity <= 0 else value / equity.equity
                    ),
                    "mark": mark,
                    "fx": fx,
                    "provisional": bad,
                    "lots": lot_views,
                    "theses": list(theses.values()),
                    "as_of": at,
                }
            )
        items, pagination = _page(projected, limit, offset)
        mandate_rows = _rows(
            runtime, "SELECT * FROM mandates WHERE portfolio_id = ? AND active = 1", (runtime.portfolio_id,)
        )
        mandate = None if not mandate_rows else _json(mandate_rows[0]["document_json"])
        return redact(
            {
                "portfolio_id": runtime.portfolio_id,
                "experiment_id": portfolio["experiment_id"],
                "simulated": portfolio["mode"] == "paper",
                "as_of": at,
                "reporting_currency": reporting,
                "balances": balances,
                "positions": items,
                "reservations": reservations,
                "provisional": provisional,
                "pagination": pagination,
                "mandate": mandate,
                "gross_exposure_fraction": _amount(
                    None
                    if equity.inventory_reporting is None or equity.equity is None or equity.equity <= 0
                    else equity.inventory_reporting / equity.equity
                ),
            }
        )


def orders(runtime, limit: int = 50, offset: int = 0) -> dict:
    with runtime.database.snapshot():
        at, portfolio, reporting = _context(runtime)
        decisions, _ = _decision_maps(runtime)
        rows = _rows(
            runtime,
            """SELECT * FROM order_intents WHERE portfolio_id = ? AND created_at <= ?
            ORDER BY created_at DESC, intent_id""",
            (runtime.portfolio_id, at),
        )
        items = []
        for row in rows:
            payload = _json(row.pop("payload_json"))
            decision = decisions.get(payload.get("decision_id"), {})
            requested_quantity = (decision.get("quantity") or {}).get("amount")
            submitted_quantity = payload.get("quantity")
            requested_price = (decision.get("limit_price") or {}).get("amount")
            fills = _rows(
                runtime,
                """SELECT fill_id, document_json, created_at FROM fills
                WHERE intent_id = ? AND portfolio_id = ? AND created_at <= ?
                ORDER BY created_at, fill_id""",
                (row["intent_id"], runtime.portfolio_id, at),
            )
            fill_views = []
            for fill in fills:
                source = _json(fill["document_json"])
                public_fields = {
                    "venue",
                    "trade_id",
                    "intent_id",
                    "symbol",
                    "side",
                    "quantity",
                    "price",
                    "fee_amount",
                    "fee_asset",
                    "liquidity",
                    "filled_at_utc",
                    "heuristic",
                    "reference_mid",
                    "fee_identified_rate",
                    "quote_cost",
                    "fee_components",
                }
                document = {key: value for key, value in source.items() if key in public_fields}
                reference = document.get("reference_mid")
                price = Decimal(document["price"])
                deviation = None if reference is None else price - Decimal(reference)
                document["execution_deviation"] = _amount(deviation)
                fill_views.append({**document, "fill_id": fill["fill_id"], "recorded_at": fill["created_at"]})
            filled = sum((Decimal(fill["quantity"]) for fill in fill_views), ZERO)
            attempts = _rows(
                runtime,
                """SELECT * FROM order_attempts WHERE intent_id = ? AND created_at <= ?
                ORDER BY created_at, attempt_id""",
                (row["intent_id"], at),
            )
            for attempt in attempts:
                attempt["result"] = _json(attempt.pop("result_json"))
            requested = None if requested_quantity is None else Decimal(requested_quantity)
            submitted = None if submitted_quantity is None else Decimal(submitted_quantity)
            submitted_price = payload.get("limit_price")
            price_rounding = (
                None
                if requested_price is None or submitted_price is None
                else Decimal(submitted_price) - Decimal(requested_price)
            )
            items.append(
                {
                    **row,
                    "side": payload.get("side"),
                    "order_type": payload.get("order_type"),
                    "venue": payload.get("venue"),
                    "venue_order_id": payload.get("venue_order_id"),
                    "decision_id": payload.get("decision_id"),
                    "system_version_id": decision.get("system_version_id"),
                    "requested_quantity": _amount(requested),
                    "submitted_quantity": _amount(submitted),
                    "rounding_delta": _amount(
                        None if requested is None or submitted is None else submitted - requested
                    ),
                    "quantity_asset": row["symbol"].split("/")[0],
                    "base_asset": row["symbol"].split("/")[0],
                    "requested_price": requested_price,
                    "submitted_price": payload.get("limit_price"),
                    "price_rounding_delta": _amount(price_rounding),
                    "price_currency": row["symbol"].split("/")[-1],
                    "quote_asset": row["symbol"].split("/")[-1],
                    "filled_quantity": _amount(filled),
                    "remaining_quantity": _amount(None if submitted is None else max(ZERO, submitted - filled)),
                    "reserve_asset": payload.get("reserve_asset"),
                    "reserve_amount": payload.get("reserve_amount"),
                    "degraded": row["state"] in UNCERTAIN_ORDERS,
                    "uncertainty": payload.get("error"),
                    "fills": fill_views,
                    "attempts": attempts,
                    "rationale": decision.get("rationale"),
                    "invalidation": decision.get("invalidation"),
                }
            )
        page, pagination = _page(items, limit, offset)
        return redact(
            {
                "orders": page,
                "degraded": any(item["degraded"] for item in items),
                "simulated": portfolio["mode"] == "paper",
                "portfolio_id": runtime.portfolio_id,
                "reporting_currency": reporting,
                "as_of": at,
                "pagination": pagination,
            }
        )
