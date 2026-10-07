"""Owner-process bridge from pinned evidence to redacted historical check records.

This module has no HTTP import route, credentials, request client or financial writes.
The retained proof stays in owner storage; Mission Control receives a small projection.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from trade_graph.api import progress_runs
from trade_graph.application.venue_conformance import STAGES, PinnedVenueObservation, VenueObservationScope

_ACCOUNT_NAMESPACE = uuid.UUID("779a9934-88b9-4d85-a057-5b6e5822ea61")
STAGE_LABELS = {
    "instruments": "Instrument rules",
    "account_fees": "Account fee schedule",
    "balances": "Account balances",
    "open_orders": "Open orders",
    "order_lookups": "Requested order lookups",
    "native_history": "Bounded native history",
}
PENDING_LABELS = {
    "owner_eligibility_unverified": "Owner eligibility remains unverified.",
    "native_account_owner_identity_unverified": "Native account ownership remains unverified.",
    "key_permission_inventory_unverified": "Full API key permissions remain unverified.",
    "withdrawals_absent_unverified": "Absence of withdrawal permission remains unverified.",
    "write_cancel_uncertainty_conformance_unverified": "Order and cancellation uncertainty checks remain unverified.",
    "native_stop_protection_unverified": "Native stop protection remains unverified.",
    "protected_account_ledger_reconciliation_unverified": "Financial account reconciliation remains unverified.",
    "intended_host_dependency_identity_unverified": "Intended host and dependency identity remains unverified.",
    "native_partial_fill_reserve_bound_unverified": "Native partial fill reserve bounds remain unverified.",
    "injected_transport_is_not_authenticated_venue_evidence": "Injected transport is synthetic evidence.",
    "authenticated_private_observation_missing": "Actual authenticated private connectivity remains unverified.",
    "historical_account_scope_limited": "Earlier account history was outside the observation scope.",
    "native_observation_incomplete": "Native observation stages remain incomplete.",
    "native_stage_transport_evidence_incomplete": "A native stage has incomplete retained transport evidence.",
    "observation_deadline_exceeded": "The bounded observation deadline was reached.",
    "order_lookup_unresolved": "A requested order lookup remains unresolved.",
    "order_lookups_not_requested": "Order lookups were not requested.",
    "additional_checks_pending": "Additional observation checks remain pending.",
}


def import_observation(
    runtime: Any, capture: PinnedVenueObservation, expected_scope: VenueObservationScope,
    *, maximum_age_seconds: int = 60, now: datetime | None = None,
) -> dict[str, Any]:
    """Verify the protected source now, then retain only an allowlisted projection.

    The exact scope is supplied by protected owner provisioning, never an HTTP caller.
    No claimed verification flag or precomputed proof is accepted.
    """
    current = datetime.now(UTC) if now is None else now
    if type(capture) is not PinnedVenueObservation or type(expected_scope) is not VenueObservationScope:
        raise ValueError("A pinned observation and exact protected scope are required.")
    try:
        # Invoke the trusted verifier directly rather than any caller-overridden method.
        proof = PinnedVenueObservation.verify(capture, now=current, maximum_age_seconds=maximum_age_seconds)
    except (ValueError, TypeError, OSError, KeyError, AttributeError):
        raise ValueError("The read-only observation could not be verified.") from None
    observation = proof.observation
    scope = progress_runs._scope(runtime)
    if (observation.scope != expected_scope or scope != (expected_scope.portfolio_id, expected_scope.deployment_id)
            or expected_scope.symbol != "BTC/USD"):
        raise ValueError("The read-only observation does not match the protected runtime scope.")
    if not proof.source_current:
        raise ValueError("The observation requires current collector and adapter sources.")
    stages = list(observation.completed_stages)
    pending = [code if code in PENDING_LABELS else "additional_checks_pending" for code in proof.pending]
    lookup_requested = any(receipt.method == "QueryOrders" for receipt in observation.receipts)
    if "order_lookups" in stages and not lookup_requested:
        stages.remove("order_lookups")
        pending.append("order_lookups_not_requested")
    metadata = {
        "symbol": "BTC/USD",
        "observation_started_at": observation.started_at.isoformat(),
        "observation_finished_at": observation.finished_at.isoformat(),
        "verified_at": current.astimezone(UTC).isoformat(),
        "maximum_age_seconds": maximum_age_seconds,
        "transport_basis": observation.transport_basis,
        "verified_completed_stages": stages,
        "authenticated_private_read_count": len(proof.authenticated_reads),
        "source_current": True,
        "pending_codes": list(dict.fromkeys(pending)),
    }
    # Derive an opaque scoped UUID from the verified pin. No source hash is stored
    # or exposed. Distinct captures with identical public projections stay distinct.
    run_id = uuid.uuid5(_ACCOUNT_NAMESPACE, json.dumps((*scope, proof.source_sha256))).hex
    try:
        path = progress_runs._path(runtime)
        with progress_runs._connection(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT r.*, a.metadata_json AS account_json FROM progress_runs r
                LEFT JOIN progress_account_observations a ON a.run_id=r.run_id WHERE r.run_id=?
                AND r.portfolio_id=? AND r.deployment_id=?""", (run_id, *scope),
            ).fetchone()
            if existing is not None:
                result = progress_runs._record(existing, now=current)
            else:
                progress_runs._prune(connection, path, scope)
                connection.execute(
                    "INSERT INTO progress_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (run_id, *scope, "kraken-account", "incomplete", metadata["observation_started_at"],
                     metadata["observation_finished_at"],
                     "Historical read-only observation; readiness checks remain pending.",
                     "[]", 0, 0, progress_runs._PROCESS, os.getpid()),
                )
                connection.execute("INSERT INTO progress_account_observations VALUES (?, ?)",
                                   (run_id, json.dumps(metadata, separators=(",", ":"))))
                row = connection.execute(
                    """SELECT r.*, a.metadata_json AS account_json FROM progress_runs r
                    JOIN progress_account_observations a ON a.run_id=r.run_id WHERE r.run_id=?""", (run_id,),
                ).fetchone()
                result = progress_runs._record(row, now=current)
            connection.execute("COMMIT")
            return result
    except (OSError, sqlite3.Error):
        raise RuntimeError("The private check registry is unavailable.") from None


def _validate_metadata(metadata: dict[str, Any], now: datetime) -> datetime:
    # The private sidecar is historical display storage, not a new source of proof.
    # Never publish arbitrary stored fields, transport names, stages or private text.
    expected = {"symbol", "observation_started_at", "observation_finished_at", "verified_at", "maximum_age_seconds",
                "transport_basis", "verified_completed_stages", "authenticated_private_read_count",
                "source_current", "pending_codes"}
    if not isinstance(metadata, dict) or set(metadata) != expected or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Invalid historical observation projection.")
    stages, codes = metadata["verified_completed_stages"], metadata["pending_codes"]
    maximum, count = metadata["maximum_age_seconds"], metadata["authenticated_private_read_count"]
    basis = metadata["transport_basis"]
    if (metadata["symbol"] != "BTC/USD" or basis not in ("owned_https", "injected_transport")
            or metadata["source_current"] is not True or type(maximum) is not int or not 1 <= maximum <= 300
            or type(count) is not int or not 0 <= count <= 256 or (basis == "injected_transport" and count != 0)
            or type(stages) is not list or any(type(stage) is not str or stage not in STAGES for stage in stages)
            or stages != [stage for stage in STAGES if stage in stages]
            or type(codes) is not list or len(codes) > 64
            or any(type(code) is not str or len(code) > 128 for code in codes)):
        raise ValueError("Invalid historical observation projection.")
    dates = []
    for field in ("observation_started_at", "observation_finished_at", "verified_at"):
        value = metadata[field]
        if type(value) is not str or len(value) > 64:
            raise ValueError("Invalid historical observation time.")
        date = datetime.fromisoformat(value)
        if date.tzinfo is None or date.utcoffset() is None:
            raise ValueError("Invalid historical observation time.")
        dates.append(date)
    if not dates[0] <= dates[1] <= dates[2]:
        raise ValueError("Invalid historical observation window.")
    return dates[1]


def project_history(run: dict[str, Any], metadata: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """Present a stored observation as historical, with age from its native finish."""
    finished = _validate_metadata(metadata, now)
    age = (now - finished).total_seconds()
    fresh = 0 <= age <= metadata["maximum_age_seconds"]
    synthetic = metadata["transport_basis"] == "injected_transport"
    # Permanent verifier gates cannot be removed by editing display storage.
    permanent = list(PENDING_LABELS)[:9]
    derived = []
    if metadata["authenticated_private_read_count"] == 0:
        derived.append("authenticated_private_observation_missing")
    if synthetic:
        derived.append("injected_transport_is_not_authenticated_venue_evidence")
    codes = list(dict.fromkeys([*permanent, *derived, *metadata["pending_codes"]]))
    pending = list(dict.fromkeys(PENDING_LABELS.get(code, PENDING_LABELS["additional_checks_pending"])
                                for code in codes))
    account = {key: value for key, value in metadata.items() if key != "pending_codes"}
    account.update(historical=True, freshness="fresh" if fresh else "stale", pending_checks=pending)
    steps = []
    for stage in STAGES:
        observed = stage in metadata["verified_completed_stages"]
        not_requested = stage == "order_lookups" and "order_lookups_not_requested" in metadata["pending_codes"]
        if observed:
            status = "observed" if fresh else "historical"
            detail = "Verified retained synthetic read." if synthetic else "Verified retained native read."
        else:
            status = "not_requested" if not_requested else "pending"
            detail = "No QueryOrders response was retained." if not_requested else "This stage remains pending."
        if observed and not fresh:
            detail = "Observed in the retained historical capture; freshness has expired."
        steps.append({"label": STAGE_LABELS[stage], "status": status, "detail": detail})
    run.update(account_observation=account, synthetic=synthetic, status="incomplete" if fresh else "stale", steps=steps)
    run["summary"] = (
        "Historical observation. Synthetic injected transport; actual authenticated connectivity remains unverified."
        if synthetic else "Historical observation from the collector-owned HTTPS transport.")
    run["summary"] += (" Freshness has expired." if not fresh
                       else " Freshness is measured from the original observation.")
    run["summary"] += " Read-only results leave reconciliation and live readiness pending."
    return run
