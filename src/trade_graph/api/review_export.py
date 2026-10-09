"""Complete safe record export from one scoped SQLite read transaction."""

from __future__ import annotations

import json

from fastapi import HTTPException

from trade_graph.api import evidence, live
from trade_graph.api.security import redact

# Explicit scope registry prevents exporting sessions, credentials, raw model
# conversations, protected RPC capabilities or unrelated account histories.
PORTFOLIO_TABLES = (
    "portfolios", "ledger_events", "journal_transactions", "journal_postings", "order_intents", "fills",
    "position_reservations", "native_fee_reservations", "valuation_marks", "tasks", "schedules", "decisions",
    "leader_decisions", "snapshots", "findings", "lessons", "role_results", "secretary_reports", "secretary_digests",
    "activity_events", "active_versions", "version_history", "version_rollouts", "consumer_loads",
    "engineering_commissions", "pause_states", "graph_service_runs",
)
DEPLOYMENT_TABLES = ("deployment_budget", "role_allocations", "budget_reservations", "invoice_reconciliations",
                     "owner_expense_evidence")
SCOPES = {
    "tasks": "portfolio_id=? OR portfolio_id IS NULL",
    "findings": "portfolio_id=? OR portfolio_id IS NULL",
    "version_observations": "rollout_id IN (SELECT rollout_id FROM version_rollouts WHERE portfolio_id=?)",
    "order_attempts": "intent_id IN (SELECT intent_id FROM order_intents WHERE portfolio_id=?)",
    "broker_orders": "client_order_id IN (SELECT client_order_id FROM order_intents WHERE portfolio_id=?)",
    "subscription_invocations": "task_id IN (SELECT task_id FROM tasks WHERE portfolio_id=? OR portfolio_id IS NULL)",
    "subscription_attempts": "invocation_id IN (SELECT invocation_id FROM subscription_invocations WHERE task_id IN "
                             "(SELECT task_id FROM tasks WHERE portfolio_id=? OR portfolio_id IS NULL))",
    "engineering_attempts": "change_id IN (SELECT change_id FROM engineering_commissions WHERE portfolio_id=?)",
    "cost_allocations": "portfolio_id=?",
    "usage_receipts": "reservation_id IN (SELECT reservation_id FROM budget_reservations WHERE deployment_id=?)",
    "model_invocations": "reservation_id IN (SELECT reservation_id FROM budget_reservations WHERE deployment_id=?)",
    "provider_transport_attempts": "reservation_id IN (SELECT reservation_id FROM budget_reservations "
                                   "WHERE deployment_id=?)",
}
OMIT_COLUMNS = {"request_json", "response_json", "input_json", "output_json", "result_json", "raw_response",
                "pid", "pid_start_ticks", "lease_token", "token", "token_hash", "csrf_secret"}


def _safe_row(table, row, runtime):
    if table == "tasks":
        return evidence._task(row, runtime.clock.now().isoformat())
    result = {}
    for key in row.keys():
        if key in OMIT_COLUMNS:
            continue
        value = row[key]
        if key.endswith("_json") and value is not None:
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                value = {"status": "unreadable retained JSON"}
        result[key.removesuffix("_json")] = value
    return redact(result)


def export(runtime, *, reporting_end: str | None = None) -> dict:
    with runtime.database.snapshot():
        # Establish the SQLite snapshot before acquiring any external observation.
        runtime.database.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()
        reader = live.projection_runtime(runtime)
        overview = live.overview(runtime, reporting_end=reporting_end)
        pid, deployment = runtime.portfolio_id, getattr(runtime, "deployment_id", "deployment")
        registry = {table: ("portfolio_id=?", pid) for table in PORTFOLIO_TABLES}
        registry.update({table: ("deployment_id=?", deployment) for table in DEPLOYMENT_TABLES})
        for table, scope in SCOPES.items():
            registry[table] = scope, deployment if "deployment_id" in scope else pid
        # Public observations and FX are retained shared inputs in this installation.
        registry.update({table: ("1", None) for table in ("fx_rates", "observations", "instruments", "hourly_candles",
                                                         "price_cards", "subscription_provider_state",
                                                         "process_leases")})
        records, count, byte_count = {}, 0, len(json.dumps(overview).encode())
        for table, (scope, identifier) in registry.items():
            values = []
            cursor = runtime.database.execute(f"SELECT * FROM {table} WHERE {scope} ORDER BY rowid",
                                              () if identifier is None else (identifier,))
            for row in cursor:
                value = _safe_row(table, row, reader)
                byte_count += len(json.dumps(value).encode())
                count += 1
                if count > 1_000_000 or byte_count > 64 * 1024**2:
                    raise HTTPException(status_code=413,
                                        detail="Complete review exceeds export bound; nothing was truncated.")
                values.append(value)
            scope_label = "shared public input" if identifier is None else "selected account or deployment"
            records[table] = {"count": len(values), "scope": scope_label,
                              "records": values}
        decision_ids = [row["decision_id"] for table in ("decisions", "leader_decisions")
                        for row in records[table]["records"]]
        details = []
        for decision_id in decision_ids:
            detail = evidence.decision(reader, decision_id)
            byte_count += len(json.dumps(detail).encode())
            if byte_count > 64 * 1024**2:
                raise HTTPException(413, "Complete review exceeds export bound; nothing was truncated.")
            details.append(detail)
        return redact({"schema_version": 1, "snapshot_at": overview["as_of"], "portfolio_id": pid,
            "snapshot_consistency": "All database projections and records use one SQLite read transaction.",
            "reporting_period": overview["reporting_period"], "reporting_currency": overview["reporting_currency"],
            "overview": overview, "record_count": count, "records": records, "decision_details": details,
            "external_observations": overview["external_observations"],
            "limitations": ["Public and quota inputs retain their own observed/retrieved timestamps; "
                             "snapshot time is not their freshness.",
                "Export covers all retained safe records in its declared scopes; "
                "missing history and usage are not reconstructed.",
                "Request/response transcripts, credentials, sessions and internal capability records are excluded.",
                "Account inception results include retained earlier runs. "
                "Recovery's evaluation period does not reset expenses or capital.",
                "Paper execution is simulated; economic evidence and live authority remain separate."]})
