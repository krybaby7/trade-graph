"""Read-only operational facts and explicit live-prerequisite state."""

from __future__ import annotations

import json
from decimal import Decimal

from trade_graph.api import financial
from trade_graph.api.security import redact
from trade_graph.domain.clock import utc_iso
from trade_graph.live_gate import evaluate_live_enablement


def health(runtime, *, authenticated: bool = False) -> dict:
    result = {
        "mode": "paper", "paid_calls_enabled": False, "live_enabled": False,
        "live_prerequisites": {"enabled": False, "status": "implementation_pending"},
    }
    if not authenticated:
        return result
    database, pid = runtime.database, runtime.portfolio_id
    now = utc_iso(runtime.clock.now())
    policy_row = database.execute(
        "SELECT document_json FROM owner_policy_revisions ORDER BY created_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    policy = json.loads(policy_row["document_json"]) if policy_row else {}
    gate_row = database.execute(
        "SELECT document_json FROM live_gate WHERE deployment_id = ?",
        (getattr(runtime, "deployment_id", "deployment"),),
    ).fetchone()
    gate_record = json.loads(gate_row["document_json"]) if gate_row else {}
    declared_verdict = gate_record.get("economic_verdict")
    if not isinstance(declared_verdict, str) or declared_verdict not in {
        "supported", "not_supported", "insufficient_evidence",
    }:
        declared_verdict = "insufficient_evidence"
    gate_record["paper_capital"] = policy.get("virtual_capital", {}).get("amount", "0")
    gate_record["withdrawals_allowed"] = False
    gate = evaluate_live_enablement(gate_record)
    gate["recorded_eligibility_passed"] = gate["enabled"]
    gate["enabled"] = False
    gate["implementation_ready"] = False
    gate["reasons"] = [*gate["reasons"], "paper-only service; live adapter and owner authorization pending"]
    gate["unverified_recorded_checks"] = {
        name: gate_record.get(field) is True for name, field in {
            "eligibility": "eligibility_confirmed", "explicit_owner_confirmation": "owner_confirmed",
            "venue_metadata": "venue_metadata_verified", "broker_conformance": "broker_conformance_passed",
            "read_only_reconciliation": "read_only_reconciliation_passed",
            "pause_and_protection": "pause_protection_passed", "host_and_backup": "host_backup_passed",
            "operating_budget": "operating_budget_set",
        }.items()
    }
    gate["checks"] = dict.fromkeys(gate["unverified_recorded_checks"], False)
    gate["verification_basis"] = "unverified_dashboard_record"
    uncertain_orders = database.execute(
        "SELECT COUNT(*) FROM order_intents WHERE portfolio_id = ? "
        "AND state IN ('UNKNOWN', 'SUBMITTING', 'CANCEL_PENDING')", (pid,)
    ).fetchone()[0]
    uncertain_usage = database.execute(
        "SELECT COUNT(*) FROM budget_reservations WHERE deployment_id = ? AND state = 'UNCERTAIN' AND synthetic = 0",
        (getattr(runtime, "deployment_id", "deployment"),),
    ).fetchone()[0]
    pause_row = database.execute("SELECT * FROM pause_states WHERE portfolio_id = ?", (pid,)).fetchone()
    active = database.execute("SELECT * FROM active_versions WHERE portfolio_id = ?", (pid,)).fetchone()
    rollout = database.execute(
        "SELECT rollout_id, state, generation, target_hash FROM version_rollouts WHERE portfolio_id = ? "
        "ORDER BY generation DESC LIMIT 1", (pid,),
    ).fetchone()
    loads = database.execute(
        "SELECT consumer_id, artifact_hash, generation, reconciled_at FROM consumer_loads WHERE portfolio_id = ?",
        (pid,),
    ).fetchall()
    leases = database.execute("SELECT lease_name, owner, expires_at FROM process_leases").fetchall()
    overview = financial.overview(runtime) if hasattr(runtime, "ledger") else None
    provisional = overview["provisional"] if overview is not None else True
    invoices = database.execute(
        "SELECT unexplained FROM invoice_reconciliations WHERE deployment_id = ? AND created_at <= ?",
        (getattr(runtime, "deployment_id", "deployment"), now),
    ).fetchall()
    invoice_uncertainty = any(Decimal(row["unexplained"]) != 0 for row in invoices)
    reasons = []
    if uncertain_orders:
        reasons.append("unknown orders")
    if uncertain_usage:
        reasons.append("uncertain billing")
    if provisional:
        reasons.append("provisional financial result")
    if invoice_uncertainty:
        reasons.append("unexplained invoice differences")
    if rollout and rollout["state"] in {"BLOCKED", "ROLLBACK_PENDING", "RESTART_PENDING", "RESTORE_PENDING"}:
        reasons.append("artifact reload or recovery pending")
    result.update({
        "as_of": now, "portfolio_id": pid, "live_prerequisites": gate,
        "paid_calls_enabled": bool(getattr(runtime, "paid_calls_enabled", False)),
        "paid_calls_authorized": bool(policy.get("paid_calls_enabled", False)),
        "pause": dict(pause_row) if pause_row else {"profile": "RUNNING", "originator": None},
        "degraded": bool(reasons), "degraded_reasons": reasons,
        "provisional": provisional, "uncertain_orders": uncertain_orders,
        "uncertain_usage": uncertain_usage,
        "invoice_uncertainty": invoice_uncertainty,
        "active_version": dict(active) if active else None,
        "rollout": dict(rollout) if rollout else None,
        "consumers": [dict(row) for row in loads],
        "process_leases": [{**dict(row), "expired": row["expires_at"] <= now} for row in leases],
        "protection": {"management": "paper execution reconciliation remains available",
                       "offline_guarantee": False},
        "economic_evidence": "insufficient_evidence",
        "declared_economic_verdict": declared_verdict,
    })
    return redact(result)
