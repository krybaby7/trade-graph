"""Owner bill evidence and retrospective period allocations.

Bills accrue once at deployment scope and never write API receipts, ledger expenses
or funding allowances. The full native bill remains visible. The graph share is
owner declared; its service-period overlap is prorated with Decimal microsecond
ratios and converted using the incurred-at FX observation. Department weights are
owner allocation evidence, not inference about per-call subscription fees.

``projection`` returns ``full_bills``, the two ``graph_*_allocation`` summaries,
``department_allocations``, and ``coverage``. Summary ``known_amount`` is the sum
of retained bill allocations, including zero when none were recorded. ``amount``
is null until an owner completeness declaration covers that category's entire
half-open query period and its FX/deduplication evidence is usable. Completeness
covers only this table's non-API bills; existing ledger/API costs remain separate.
A later bill invalidates an older intersecting declaration until redeclared.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Annotated, Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trade_graph.api.financial import _amount, _convert, _rows, _sum
from trade_graph.api.security import redact
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.money import canonical_decimal

ZERO = Decimal("0")
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]
Label = Annotated[str, Field(min_length=1, max_length=240)]
Kind = Literal["subscription", "other"]


class EvidenceConflict(ValueError):
    """A replay changed its evidence or a bill would be charged more than once."""


def _timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return utc_iso(parsed)


def _decimal(value: str) -> str:
    if type(value) is not str or len(value) > 64 or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value):
        raise ValueError("decimal amounts and weights require nonnegative decimal strings")
    return canonical_decimal(Decimal(value))


class _Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: Identifier
    period_start: str
    period_end: str
    evidence_ref: Identifier

    @field_validator("period_start", "period_end")
    @classmethod
    def timestamps(cls, value):
        return _timestamp(value)

    @field_validator("request_id", "evidence_ref")
    @classmethod
    def safe_reference(cls, value):
        if redact(value) != value:
            raise ValueError("use a safe opaque evidence reference")
        return value

    @model_validator(mode="after")
    def period(self):
        if self.period_end <= self.period_start:
            raise ValueError("the evidence period must have positive duration")
        return self


class ExpenseEvidence(_Evidence):
    record_type: Literal["expense"]
    bill_id: Identifier
    billing_scope: Identifier
    expense_kind: Kind
    amount_native: str
    native_currency: Annotated[str, Field(pattern=r"^[A-Z0-9]{2,16}$")]
    incurred_at: str
    graph_share: str
    allocation_policy: Label
    department_weights: dict[str, str]
    department_allocation_label: Label

    @field_validator("incurred_at")
    @classmethod
    def incurred_timestamp(cls, value):
        return _timestamp(value)

    @field_validator("amount_native", "graph_share", mode="before")
    @classmethod
    def decimal_strings(cls, value):
        return _decimal(value)

    @field_validator("bill_id", "billing_scope", "allocation_policy", "department_allocation_label")
    @classmethod
    def safe_labels(cls, value):
        if redact(value) != value or any(ord(character) < 32 for character in value):
            raise ValueError("use safe owner labels without paths, secrets or invoice contents")
        return value

    @field_validator("department_weights", mode="before")
    @classmethod
    def weights(cls, value):
        if type(value) is not dict or not 1 <= len(value) <= 32:
            raise ValueError("explicit department weights are required")
        result = {}
        for name, weight in sorted(value.items()):
            if type(name) is not str or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name):
                raise ValueError("department labels must be safe identifiers")
            result[name] = _decimal(weight)
        with localcontext() as context:
            context.prec = 128
            if sum((Decimal(weight) for weight in result.values()), ZERO) != 1:
                raise ValueError("department weights must sum to one")
        return result

    @model_validator(mode="after")
    def share(self):
        if Decimal(self.graph_share) > 1:
            raise ValueError("the graph share must be between zero and one")
        return self


class CompletenessEvidence(_Evidence):
    record_type: Literal["completeness"]
    expense_kinds: list[Kind]

    @field_validator("expense_kinds")
    @classmethod
    def kinds(cls, value):
        if not value or len(set(value)) != len(value):
            raise ValueError("declare each complete expense category exactly once")
        return sorted(value)


def _parse(body: dict) -> ExpenseEvidence | CompletenessEvidence:
    if type(body) is not dict:
        raise ValueError("evidence must be an object")
    model = {"expense": ExpenseEvidence, "completeness": CompletenessEvidence}.get(body.get("record_type"))
    if model is None:
        raise ValueError("unknown evidence type")
    return model.model_validate(body)


def _deployment(runtime) -> str:
    return getattr(runtime, "deployment_id", "deployment")


def _existing_identities(runtime) -> set[str]:
    """Identity matches are supported; arbitrary invoice-to-API mirroring is not."""
    identities = set()
    for row in _rows(runtime, """SELECT r.receipt_id,r.provider_request_id FROM usage_receipts r
            JOIN budget_reservations b USING (reservation_id) WHERE b.deployment_id=?""", (_deployment(runtime),)):
        identities.update(value for value in row.values() if value)
    for row in _rows(runtime, "SELECT invoice_id FROM invoice_reconciliations WHERE deployment_id=?",
                     (_deployment(runtime),)):
        identities.add(row["invoice_id"])
    # Ledger expenses have no deployment key; fail closed on every retained identity.
    for row in _rows(runtime, "SELECT payload_json FROM ledger_events WHERE kind='expense'"):
        expense = json.loads(row["payload_json"])
        identities.update(value for value in (expense.get("expense_id"), expense.get("source")) if value)
    return identities


def _duplicates(document: dict, existing: set[str]) -> bool:
    identities = {document["bill_id"], document["evidence_ref"]}
    for identity in tuple(identities):
        if identity.startswith(("receipt:", "owner-evidence:", "expense:")):
            identities.add(identity.split(":", 1)[1])
    return bool(identities & existing)


def _document(row: dict) -> dict:
    return {**json.loads(row["document_json"]), "record_id": row["record_id"], "sequence": row["sequence"],
            "deployment_id": row["deployment_id"], "created_at": row["created_at"]}


def record(runtime, body: dict) -> dict:
    """Append one validated owner statement, or return its exact idempotent replay."""
    evidence = _parse(body)
    now = utc_iso(runtime.clock.now())
    if isinstance(evidence, ExpenseEvidence) and evidence.incurred_at > now:
        raise ValueError("future expense accrual evidence cannot be recorded")
    if isinstance(evidence, CompletenessEvidence) and evidence.period_end > now:
        raise ValueError("future periods cannot be declared complete")
    document = evidence.model_dump(mode="json")
    serialized = json.dumps(document, sort_keys=True, separators=(",", ":"))
    deployment = _deployment(runtime)
    with runtime.database.immediate():
        prior = _rows(runtime, "SELECT * FROM owner_expense_evidence WHERE deployment_id=? AND request_id=?",
                      (deployment, evidence.request_id))
        if prior:
            if prior[0]["document_json"] != serialized:
                raise EvidenceConflict("request_id already records different owner evidence")
            return _document(prior[0])
        if isinstance(evidence, ExpenseEvidence) and _duplicates(document, _existing_identities(runtime)):
            raise EvidenceConflict("bill evidence already belongs to ledger or API expense accounting")
        record_id = str(uuid.uuid4())
        try:
            runtime.database.execute("""INSERT INTO owner_expense_evidence
                (record_id,deployment_id,request_id,record_type,bill_id,billing_scope,expense_kind,
                 period_start,period_end,incurred_at,evidence_ref,document_json,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (record_id, deployment, evidence.request_id, evidence.record_type, document.get("bill_id"),
                 document.get("billing_scope"), document.get("expense_kind"), evidence.period_start,
                 evidence.period_end, document.get("incurred_at"), evidence.evidence_ref, serialized, now))
        except sqlite3.IntegrityError:
            raise EvidenceConflict("bill identity, retained evidence, or billing period is already allocated") from None
        return _document(_rows(runtime, "SELECT * FROM owner_expense_evidence WHERE record_id=?", (record_id,))[0])


def _microseconds(start: str, end: str) -> int:
    delta = datetime.fromisoformat(end.replace("Z", "+00:00")) - datetime.fromisoformat(start.replace("Z", "+00:00"))
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def _coverage(kind: str, records: list[dict], start: str, end: str) -> dict:
    declarations = [row for row in records if row["record_type"] == "completeness"
                    and kind in row["expense_kinds"]]
    intervals = []
    accepted, invalidated = [], []
    for declaration in declarations:
        later_bill = any(row["record_type"] == "expense" and row["expense_kind"] == kind
                         and row["sequence"] > declaration["sequence"]
                         and row["period_start"] < declaration["period_end"]
                         and row["period_end"] > declaration["period_start"] for row in records)
        if later_bill:
            invalidated.append(declaration["record_id"])
        else:
            intervals.append((max(start, declaration["period_start"]), min(end, declaration["period_end"])))
            accepted.append(declaration["record_id"])
    cursor = start
    for left, right in sorted(intervals):
        if left > cursor:
            break
        cursor = max(cursor, right)
    return {"status": "complete" if accepted and cursor >= end else "unknown", "declaration_ids": accepted,
            "invalidated_declaration_ids": invalidated,
            "scope": "owner bills only; ledger and API receipts are separate"}


def _portions(amount: Decimal | None, weights: dict[str, str]) -> dict[str, Decimal | None]:
    if amount is None:
        return {name: None for name in weights}
    result = {}
    remaining = amount
    for index, (name, weight) in enumerate(weights.items()):
        value = remaining if index == len(weights) - 1 else amount * Decimal(weight)
        result[name] = value
        remaining -= value
    return result


def projection(runtime, start_at: str, end_at: str, reporting_currency: str) -> dict:
    """Retrospective, read-only allocation for one half-open service period."""
    start, end = _timestamp(start_at), _timestamp(end_at)
    now = utc_iso(runtime.clock.now())
    if start > end or end > now or not re.fullmatch(r"[A-Z0-9]{2,16}", reporting_currency):
        raise ValueError("invalid owner expense query period or currency")
    with runtime.database.snapshot(), localcontext() as context:
        context.prec = 128
        records = [_document(row) for row in _rows(runtime, """SELECT * FROM owner_expense_evidence
                   WHERE deployment_id=? AND created_at<=? AND period_start<? AND period_end>?
                   ORDER BY sequence""", (_deployment(runtime), now, end, start))] if start < end else []
        existing = _existing_identities(runtime)
        coverage = {kind: _coverage(kind, records, start, end) for kind in ("subscription", "other")}
        values = {kind: [] for kind in coverage}
        issues = {kind: set() for kind in coverage}
        departments, labels = defaultdict(list), defaultdict(set)
        full_bills = []
        for row in records:
            if row["record_type"] != "expense":
                continue
            kind = row["expense_kind"]
            left, right = max(start, row["period_start"]), min(end, row["period_end"])
            numerator, denominator = _microseconds(left, right), _microseconds(row["period_start"], row["period_end"])
            fraction = Decimal(numerator) / Decimal(denominator)
            native = Decimal(row["amount_native"])
            allocated_native = native * Decimal(row["graph_share"]) * fraction
            full_value, full_bad, fx = _convert(runtime, native, row["native_currency"], reporting_currency,
                                               row["incurred_at"])
            allocated_value, bad, _ = _convert(runtime, allocated_native, row["native_currency"], reporting_currency,
                                               row["incurred_at"])
            duplicate = _duplicates(row, existing)
            if duplicate:
                issues[kind].add("duplicate_existing_accounting")
                allocated_value = ZERO  # It remains in the original ledger/API component once.
            elif fx is None:
                issues[kind].add("missing_fx")
            elif bad:
                issues[kind].add("stale_fx")
            values[kind].append(allocated_value)
            native_departments = _portions(allocated_native, row["department_weights"])
            reporting_departments = _portions(allocated_value, row["department_weights"])
            row["department_allocations"] = []
            for department in row["department_weights"]:
                departments[department].append(reporting_departments[department])
                labels[department].add(row["department_allocation_label"])
                row["department_allocations"].append({"department": department,
                    "weight": row["department_weights"][department],
                    "native_amount": _amount(native_departments[department]),
                    "amount": _amount(reporting_departments[department]), "currency": reporting_currency,
                    "allocation_label": row["department_allocation_label"]})
            row.update({"period_fraction": _amount(fraction), "period_fraction_numerator_microseconds": str(numerator),
                        "period_fraction_denominator_microseconds": str(denominator),
                        "allocation_period": {"start_at": left, "end_at": right},
                        "graph_allocation_native": _amount(allocated_native),
                        "full_bill_valuation": {"amount": _amount(full_value), "currency": reporting_currency,
                                                "at": row["incurred_at"], "fx": fx, "provisional": full_bad},
                        "graph_allocation_valuation": {"amount": _amount(allocated_value),
                                                       "currency": reporting_currency,
                                                       "at": row["incurred_at"], "fx": fx,
                                                       "provisional": bad or duplicate},
                        "excluded_existing_accounting": duplicate})
            full_bills.append(row)
        summaries = {}
        unknown_reasons = set()
        for kind in coverage:
            complete = coverage[kind]["status"] == "complete"
            if not complete:
                unknown_reasons.add(f"owner_{kind}_completeness_missing")
            unknown_reasons.update(issues[kind])
            known = _sum(values[kind])
            summaries[kind] = {"amount": _amount(known) if complete and not issues[kind] else None,
                               "known_amount": _amount(known), "currency": reporting_currency,
                               "coverage_status": coverage[kind]["status"],
                               "provisional": not complete or bool(issues[kind]),
                               "bill_count": len(values[kind])}
        return {"period": {"start_at": start, "end_at": end}, "reporting_currency": reporting_currency,
                "evidence_as_of": now, "deployment_id": _deployment(runtime), "full_bills": full_bills,
                "graph_subscription_allocation": summaries["subscription"],
                "graph_other_allocation": summaries["other"],
                "department_allocations": [{"department": name, "amount": _amount(_sum(amounts)),
                    "currency": reporting_currency, "allocation_labels": sorted(labels[name]),
                    "provisional": bool(unknown_reasons)} for name, amounts in sorted(departments.items())],
                "coverage": {"status": "complete" if all(row["status"] == "complete" for row in coverage.values())
                             else "unknown", **coverage, "unknown_reasons": sorted(unknown_reasons)},
                "completeness_declarations": [row for row in records if row["record_type"] == "completeness"],
                "provisional": bool(unknown_reasons),
                "basis": "owner bills only; whole bill retained; explicit graph share and service-period proration; "
                         "incurred-at FX; owner department weights; separate from ledger/API expenses and allowance"}


def register(app, runtime, identity, owner_write) -> None:
    @app.get("/api/v1/owner/expenses")
    def expenses(request: Request, start_at: str | None = None, end_at: str | None = None,
                 reporting_currency: str | None = None):
        identity(request)
        try:
            with runtime.database.snapshot():
                portfolio = _rows(runtime, "SELECT created_at,reporting_currency FROM portfolios WHERE portfolio_id=?",
                                  (runtime.portfolio_id,))[0]
                return redact(projection(runtime, start_at or portfolio["created_at"],
                                         end_at or utc_iso(runtime.clock.now()),
                                         reporting_currency or portfolio["reporting_currency"]))
        except (ValueError, TypeError, ArithmeticError):
            raise HTTPException(422, "invalid owner expense query") from None

    @app.post("/api/v1/owner/expenses")
    async def append_expense(request: Request):
        owner_write(request)
        try:
            raw = await request.body()
            if len(raw) > 65536:
                raise ValueError("owner evidence request too large")
            return redact(record(runtime, json.loads(raw)))
        except EvidenceConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except (ValueError, TypeError, ArithmeticError, RecursionError):
            # Validation exception text may contain submitted private material.
            raise HTTPException(422, "invalid owner expense evidence") from None
