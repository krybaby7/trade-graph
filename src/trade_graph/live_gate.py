"""Read-only live prerequisites; this module never grants execution authority.

Dashboard records are declarations, not verification. A protected caller may
inspect pinned private evidence against current state; even that cannot enable
the broker, change owner policy, or issue an order.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trade_graph.adapters.persistence.db import Database
from trade_graph.contracts.models import Mandate, OwnerPolicy
from trade_graph.domain.clock import Clock, parse_utc, utc_iso
from trade_graph.domain.money import Money, canonical_decimal, parse_decimal

Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_./:-]+$")]
EvidenceKind = Literal[
    "eligibility", "funding", "key_permissions", "venue_metadata_fees", "read_only_reconciliation",
    "broker_conformance", "pause_protection_recovery", "host_backup_alerts", "economic_evaluation",
]
_ISSUERS = {
    "eligibility": "eligibility", "funding": "venue", "key_permissions": "venue",
    "venue_metadata_fees": "venue", "read_only_reconciliation": "venue", "broker_conformance": "venue",
    "pause_protection_recovery": "operations", "host_backup_alerts": "operations",
    "economic_evaluation": "economics",
}
_ASSERTIONS = {
    "eligibility": ("current_legal_and_account_eligibility",),
    "funding": ("real_funded_account",),
    "key_permissions": ("read_trade_only", "withdrawals_absent"),
    "venue_metadata_fees": ("current_rules_precision_minimums_fees", "minimum_size_within_owner_allocation"),
    "read_only_reconciliation": ("authenticated_account_orders_fills_balances", "complete_reconciliation"),
    "broker_conformance": ("order_uncertainty_cancel_fill_restart", "current_adapter_wire_contract"),
    "pause_protection_recovery": ("pause_and_independent_recovery", "offline_protection_limitations_reviewed"),
    "host_backup_alerts": ("intended_host_verified", "backup_restore_verified", "alerts_verified"),
    "economic_evaluation": ("forward_evaluation_complete", "all_actual_costs_and_receipts_verified",
                            "paper_venue_differences_verified"),
}
_MAX_BYTES = 262144
_DOMAIN = b"trade-graph.live-readiness.v1\0"
_MAX_AGE = {kind: timedelta(days=31) for kind in _ISSUERS}
_MAX_AGE.update(funding=timedelta(seconds=60), read_only_reconciliation=timedelta(seconds=60),
                key_permissions=timedelta(days=1), venue_metadata_fees=timedelta(days=1))


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LivePilotScope(_Contract):
    """Identity pinned by protected deployment configuration, never request data."""

    deployment_id: Identifier
    portfolio_id: Identifier
    account_id: Identifier
    venue: Identifier
    symbol: Identifier
    policy_revision: Identifier
    policy_sha256: Fingerprint
    deployment_artifact_sha256: Fingerprint
    system_version_sha256: Fingerprint


class _Dated(_Contract):
    verified_at: datetime
    expires_at: datetime

    @field_validator("verified_at", "expires_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("verification timestamps must be timezone aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def bounded_window(self):
        if not timedelta(0) < self.expires_at - self.verified_at <= timedelta(days=31):
            raise ValueError("verification window must be positive and at most 31 days")
        return self


class OwnerPilotAuthorization(_Dated):
    """A separate explicit grant with native real capital and EUR expense caps."""

    authorization_id: Identifier
    action: Literal["authorize_bounded_live_pilot"]
    scope: LivePilotScope
    allocation_origin: Literal["owner_real_funds"]
    allocation: Money
    maximum_loss: Money
    operating_allowance: Money
    daily_expense_limit: Money
    budget_configuration_sha256: Fingerprint
    purpose: Literal["supported_economics", "diagnostic_execution_measurement"]
    stop_profile: Literal["MANAGE_ONLY", "FLATTEN"]
    leverage_allowed: Literal[False]
    withdrawals_allowed: Literal[False]

    @field_validator("leverage_allowed", "withdrawals_allowed", mode="before")
    @classmethod
    def exact_false(cls, value):
        if value is not False:
            raise ValueError("leverage and withdrawals must be explicitly false")
        return value

    @model_validator(mode="after")
    def limits(self):
        for amount in (self.allocation, self.maximum_loss, self.operating_allowance, self.daily_expense_limit):
            if amount.amount <= 0 or not -18 <= amount.amount.as_tuple().exponent <= 18:
                raise ValueError("pilot limits must be positive bounded fixed-point amounts")
            if len(amount.amount.as_tuple().digits) > 36:
                raise ValueError("pilot amount precision is excessive")
        if self.maximum_loss.currency != self.allocation.currency or self.maximum_loss.amount > self.allocation.amount:
            raise ValueError("loss cap must fit the native allocation")
        if self.operating_allowance.currency != "EUR" or self.daily_expense_limit.currency != "EUR":
            raise ValueError("operating expenses require EUR limits")
        if self.daily_expense_limit.amount > self.operating_allowance.amount:
            raise ValueError("daily limit exceeds the real allowance")
        return self


class ReadinessEvidence(_Dated):
    evidence_id: Identifier
    kind: EvidenceKind
    scope: LivePilotScope
    verification_basis: Literal["actual_external_or_deployment_verification", "unverified_imports", "synthetic"]
    source_sha256: tuple[Fingerprint, ...] = Field(min_length=1, max_length=64)
    assertions: tuple[Identifier, ...] = Field(min_length=1, max_length=16)
    funded_amount: Money | None = None
    economic_verdict: Literal["supported", "not_supported", "insufficient_evidence"] | None = None
    forward_protocol_sha256: Fingerprint | None = None
    forward_report_sha256: Fingerprint | None = None
    sealed_inventory_sha256: Fingerprint | None = None
    registry_snapshot_sha256: Fingerprint | None = None

    @field_validator("funded_amount")
    @classmethod
    def bounded_funding(cls, value: Money | None):
        if value is not None and (value.amount <= 0 or not -18 <= value.amount.as_tuple().exponent <= 18
                                  or len(value.amount.as_tuple().digits) > 36):
            raise ValueError("funding must use positive bounded fixed-point amounts")
        return value


class SignedAuthorization(_Contract):
    issuer: Literal["owner"]
    payload: OwnerPilotAuthorization
    signature: Fingerprint


class SignedEvidence(_Contract):
    issuer: Literal["eligibility", "venue", "operations", "economics"]
    payload: ReadinessEvidence
    signature: Fingerprint


class ReadinessBundle(_Contract):
    schema_version: Literal[1]
    owner_authorization: SignedAuthorization
    evidence: tuple[SignedEvidence, ...] = Field(min_length=1, max_length=16)


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def budget_configuration_digest(database: Database, deployment_id: str) -> str | None:
    """Hash protected budget and role limits without using portfolio equity."""
    row = database.execute("SELECT * FROM deployment_budget WHERE deployment_id = ?", (deployment_id,)).fetchone()
    if row is None:
        return None
    roles = database.execute(
        "SELECT role, amount FROM role_allocations WHERE deployment_id = ? ORDER BY role", (deployment_id,),
    ).fetchall()
    return hashlib.sha256(_canonical({"budget": dict(row), "roles": [dict(role) for role in roles]})).hexdigest()


@dataclass(frozen=True)
class PinnedReadinessSource:
    """Protected runtime pins, not request input or an Engineer artifact.

    Keep the full bundle pin and issuer keys outside the mutable process. MACs
    establish issuer provenance; they do not prove an external check happened.
    Each issuer must independently check its actual sources. No writer is offered.
    """

    path: Path
    bundle_sha256: str
    issuer_keys: Mapping[str, bytes] = dataclass_field(repr=False)

    def __post_init__(self):
        if not self.path.is_absolute():
            raise ValueError("protected evidence path must be absolute")
        object.__setattr__(self, "issuer_keys", MappingProxyType(dict(self.issuer_keys)))

    def load(self) -> ReadinessBundle:
        if len(self.bundle_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.bundle_sha256):
            raise ValueError("invalid protected evidence pin")
        keys = dict(self.issuer_keys)
        if set(keys) != {"owner", *_ISSUERS.values()} or any(
            type(key) is not bytes or len(key) < 32 for key in keys.values()
        ):
            raise ValueError("missing protected issuer keys")
        if len(set(keys.values())) != len(keys):
            raise ValueError("issuer authority must use separate keys")
        directory_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            directory = os.fstat(directory_fd)
            if directory.st_uid != os.getuid() or stat.S_IMODE(directory.st_mode) & 0o077:
                raise ValueError("evidence directory must be owner private")
            descriptor = os.open(self.path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
            try:
                metadata = os.fstat(descriptor)
                if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                        or stat.S_IMODE(metadata.st_mode) & 0o077 or metadata.st_nlink != 1
                        or metadata.st_size > _MAX_BYTES):
                    raise ValueError("evidence must be a bounded owner-private regular file")
                with os.fdopen(descriptor, "rb", closefd=False) as stream:
                    raw = stream.read(_MAX_BYTES + 1)
                if len(raw) > _MAX_BYTES or hashlib.sha256(raw).hexdigest() != self.bundle_sha256:
                    raise ValueError("evidence does not match protected deployment pin")
            finally:
                os.close(descriptor)
        finally:
            os.close(directory_fd)
        bundle = ReadinessBundle.model_validate_json(raw)
        signed = [("owner_authorization", bundle.owner_authorization)]
        signed.extend((entry.payload.kind, entry) for entry in bundle.evidence)
        for kind, entry in signed:
            expected = "owner" if kind == "owner_authorization" else _ISSUERS[kind]
            if entry.issuer != expected:
                raise ValueError("incorrect evidence issuer")
            message = _DOMAIN + kind.encode() + b"\0" + _canonical(entry.payload.model_dump(mode="json"))
            signature = hmac.new(keys[expected], message, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, entry.signature):
                raise ValueError("invalid protected issuer signature")
        return bundle


def evaluate_live_enablement(record: dict) -> dict:
    """Legacy projection: arbitrary records and truthy flags never enable live."""
    reasons = ["unverified live record; protected scoped evidence and explicit authority required"]
    allocation = "0"
    try:
        amount = parse_decimal(record.get("live_allocation", "0"))
        if amount < 0 or not -18 <= amount.as_tuple().exponent <= 18 or len(amount.as_tuple().digits) > 36:
            raise ValueError("invalid live allocation")
        allocation = canonical_decimal(amount)
    except (ValueError, TypeError, ArithmeticError):
        reasons.append("invalid live allocation")
    if record.get("uses_paper_capital_as_live_allocation") or record.get("allocation_copied_from_paper"):
        reasons.append("paper capital is not a live allocation")
    return {"enabled": False, "diagnostic": False, "ready": False, "status": "unverified_record",
            "reasons": reasons, "live_allocation": allocation}


def evaluate_live_readiness(
    database: Database, clock: Clock, *, scope: LivePilotScope, source: PinnedReadinessSource,
) -> dict:
    """Compare verified issuer documents with one current protected DB snapshot.

    Scope and source must be runtime pins. This has no request route, policy
    writer, broker call, or enablement. Results omit account IDs and raw evidence.
    """
    with localcontext(Context(prec=100)):
        # Exact comparisons for bounded 36-digit native limits, their exponent
        # range, and realistically bounded SQLite reservation counts.
        return _evaluate_live_readiness(database, clock, scope=scope, source=source)


def _state_amount(value) -> Decimal:
    amount = parse_decimal(value)
    if amount < 0 or not -18 <= amount.as_tuple().exponent <= 18 or len(amount.as_tuple().digits) > 36:
        raise ValueError("protected limits require bounded nonnegative fixed-point values")
    return amount


def _evaluate_live_readiness(
    database: Database, clock: Clock, *, scope: LivePilotScope, source: PinnedReadinessSource,
) -> dict:
    reasons: list[str] = []
    checks: dict[str, bool] = {}

    def check(name: str, ok: bool) -> None:
        checks[name] = ok
        if not ok:
            reasons.append(name)

    try:
        bundle = source.load()
    except (OSError, ValueError, TypeError, ArithmeticError, RecursionError):
        return {"enabled": False, "ready": False, "diagnostic": False, "status": "blocked",
                "reasons": ["protected evidence unavailable or invalid"], "checks": {"protected_evidence": False}}
    now = clock.now()
    if now.tzinfo is None:
        return {"enabled": False, "ready": False, "diagnostic": False, "status": "blocked",
                "reasons": ["protected clock requires a timezone"], "checks": {"protected_clock": False}}
    grant = bundle.owner_authorization.payload
    check("owner_scope", grant.scope == scope)
    check("owner_freshness", grant.verified_at <= now < grant.expires_at)
    kinds = [entry.payload.kind for entry in bundle.evidence]
    identifiers = [entry.payload.evidence_id for entry in bundle.evidence]
    check("complete_unique_evidence", set(kinds) == set(_ISSUERS) and len(set(kinds)) == len(kinds)
          and len(set(identifiers)) == len(identifiers))
    evidence = {entry.payload.kind: entry.payload for entry in bundle.evidence}
    for kind, item in evidence.items():
        check(f"{kind}_scope", item.scope == scope)
        check(f"{kind}_freshness", item.verified_at <= now < item.expires_at
              and now - item.verified_at <= _MAX_AGE[kind])
        check(f"{kind}_actual_verification", item.verification_basis == "actual_external_or_deployment_verification")
        check(f"{kind}_assertions", set(item.assertions) == set(_ASSERTIONS[kind])
              and len(set(item.assertions)) == len(item.assertions))
    funding = evidence.get("funding")
    check("real_allocation_funded", bool(
        funding and funding.funded_amount and funding.funded_amount.currency == grant.allocation.currency
        and funding.funded_amount.amount.is_finite() and funding.funded_amount.amount >= grant.allocation.amount
    ))
    economics = evidence.get("economic_evaluation")
    check("economic_source_bindings", bool(economics and economics.forward_protocol_sha256
          and economics.forward_report_sha256 and economics.sealed_inventory_sha256
          and economics.registry_snapshot_sha256))
    diagnostic = grant.purpose == "diagnostic_execution_measurement"
    check("economic_or_explicit_diagnostic", bool(
        economics and economics.economic_verdict is not None
        and (economics.economic_verdict == "supported" or diagnostic)
    ))
    try:
        with database.snapshot():
            row = database.execute(
                "SELECT * FROM owner_policy_revisions ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
            policy = OwnerPolicy.model_validate_json(row["document_json"]) if row else None
            check("active_owner_policy", bool(row and policy and row["revision_id"] == scope.policy_revision
                  and hashlib.sha256(row["document_json"].encode()).hexdigest() == scope.policy_sha256
                  and row["content_hash"] == scope.policy_sha256))
            check("explicit_live_owner_policy", bool(policy and policy.live_enabled and not policy.withdrawals_allowed
                  and not policy.leverage_allowed and scope.venue in policy.allowed_venues
                  and scope.symbol in policy.allowed_symbols))
            portfolio = database.execute(
                "SELECT mode, status FROM portfolios WHERE portfolio_id = ?", (scope.portfolio_id,),
            ).fetchone()
            check("separate_live_portfolio", bool(portfolio and portfolio["mode"] == "live"
                  and portfolio["status"] == "open"))
            mandate_row = database.execute(
                "SELECT document_json FROM mandates WHERE portfolio_id = ? AND active = 1", (scope.portfolio_id,),
            ).fetchone()
            mandate = Mandate.model_validate_json(mandate_row["document_json"]) if mandate_row else None
            check("one_instrument_current_mandate", bool(mandate and mandate.portfolio_id == scope.portfolio_id
                  and mandate.symbols == [scope.symbol] and mandate.order_types_long_only
                  and mandate.expires_at_utc > now))
            version = database.execute(
                "SELECT artifact_hash FROM active_versions WHERE portfolio_id = ?", (scope.portfolio_id,),
            ).fetchone()
            check("current_system_version", bool(version and version["artifact_hash"] == scope.system_version_sha256))
            budget = database.execute(
                "SELECT * FROM deployment_budget WHERE deployment_id = ?", (scope.deployment_id,),
            ).fetchone()
            check("owner_budget_identity", budget_configuration_digest(database, scope.deployment_id)
                  == grant.budget_configuration_sha256)
            check("real_operating_limits", bool(budget and budget["currency"] == "EUR"
                  and _state_amount(budget["total_allowance"]) == grant.operating_allowance.amount
                  and _state_amount(budget["daily_limit"]) == grant.daily_expense_limit.amount
                  and _state_amount(budget["period_allowance"]) > 0 and _state_amount(budget["root_limit"]) > 0))
            check("policy_operating_limits", bool(policy and policy.monthly_operating.currency == "EUR"
                  and policy.daily_paid_limit.currency == "EUR"
                  and grant.operating_allowance.amount <= policy.monthly_operating.amount
                  and grant.daily_expense_limit.amount <= policy.daily_paid_limit.amount))
            holds = database.execute(
                "SELECT amount, state, created_at FROM budget_reservations WHERE deployment_id = ? AND synthetic = 0 "
                "AND state IN ('RESERVED', 'UNCERTAIN', 'COMMITTED', 'CONSERVATIVE', 'RECONCILED')",
                (scope.deployment_id,),
            ).fetchall()
            used = sum((_state_amount(hold["amount"]) for hold in holds), Decimal(0))
            now_text = utc_iso(now)
            month_used = sum((_state_amount(hold["amount"]) for hold in holds
                              if hold["created_at"].startswith(now_text[:7])), Decimal(0))
            day_used = sum((_state_amount(hold["amount"]) for hold in holds
                            if hold["created_at"].startswith(now_text[:10])), Decimal(0))
            check("remaining_real_allowance", bool(budget and used < _state_amount(budget["total_allowance"])
                  - _state_amount(budget["priority_reserve"])))
            check("remaining_current_period", bool(budget and month_used < _state_amount(budget["period_allowance"])))
            check("remaining_current_day", bool(budget and day_used < _state_amount(budget["daily_limit"])))
            check("no_unresolved_real_usage", all(hold["state"] not in {"RESERVED", "UNCERTAIN", "CONSERVATIVE"}
                                                  for hold in holds))
            differences = database.execute(
                "SELECT unexplained FROM invoice_reconciliations WHERE deployment_id = ?", (scope.deployment_id,),
            ).fetchall()
            check("no_invoice_differences", all(parse_decimal(item["unexplained"]) == 0 for item in differences))
            pause = database.execute(
                "SELECT profile FROM pause_states WHERE portfolio_id = ?", (scope.portfolio_id,),
            ).fetchone()
            check("persisted_running_profile", bool(pause and pause["profile"] == "RUNNING"))
            unknown = database.execute(
                "SELECT COUNT(*) FROM order_intents WHERE state IN ('UNKNOWN', 'SUBMITTING', 'CANCEL_PENDING') "
                "AND (portfolio_id = ? OR (json_extract(payload_json, '$.venue') = ? "
                "AND json_extract(payload_json, '$.account_id') = ? AND json_extract(payload_json, '$.mode') = 'live'))",
                (scope.portfolio_id, scope.venue, scope.account_id),
            ).fetchone()[0]
            check("no_unknown_orders", unknown == 0)
            health = database.execute(
                "SELECT payload_json, created_at FROM activity_events WHERE kind = 'execution_reconciliation_health' "
                "AND json_extract(payload_json, '$.venue') = ? AND json_extract(payload_json, '$.account_id') = ? "
                "AND json_extract(payload_json, '$.mode') = 'live' ORDER BY rowid DESC LIMIT 1",
                (scope.venue, scope.account_id),
            ).fetchone()
            check("current_complete_account_history", bool(health
                  and json.loads(health["payload_json"])["state"] == "complete"
                  and timedelta(0) <= now - parse_utc(health["created_at"]) <= timedelta(seconds=60)))
            pending = database.execute(
                "SELECT COUNT(*) FROM dashboard_commands WHERE scope = ? AND status = 'PROCESSING'",
                (f"owner:{scope.deployment_id}",),
            ).fetchone()[0]
            check("no_pending_owner_commands", pending == 0)
            rollouts = database.execute(
                "SELECT state FROM version_rollouts WHERE portfolio_id = ? ORDER BY generation DESC LIMIT 1",
                (scope.portfolio_id,),
            ).fetchone()
            check("no_artifact_recovery_pending", not rollouts or rollouts["state"] not in {
                "BLOCKED", "ROLLBACK_PENDING", "RESTART_PENDING", "RESTORE_PENDING",
            })
    except (ValueError, TypeError, ArithmeticError, KeyError, sqlite3.DatabaseError):
        check("protected_state_valid", False)
    recorded_checks_passed = not reasons
    # The current forward registry retains unverified imports. Issuer signatures
    # and digest labels cannot substitute for re-reading authenticated upstream
    # receipts/provenance and invalidating evidence when those sources change.
    check("authoritative_upstream_economic_verification", False)
    return {"enabled": False, "ready": False, "diagnostic": False, "recorded_checks_passed": recorded_checks_passed,
            "diagnostic_authorization_recorded": diagnostic and recorded_checks_passed,
            "status": "blocked", "reasons": reasons, "checks": checks,
            "live_allocation": grant.allocation.model_dump(mode="json"),
            "maximum_loss": grant.maximum_loss.model_dump(mode="json"),
            "operating_allowance": grant.operating_allowance.model_dump(mode="json"),
            "economic_evidence": economics.economic_verdict if economics else "insufficient_evidence",
            "execution_authority": "disabled; advisory evidence cannot enable the broker or owner policy"}
