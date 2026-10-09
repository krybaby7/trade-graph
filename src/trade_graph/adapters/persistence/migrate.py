"""Ordered SQL migrations. Authoritative money columns are TEXT, never REAL."""

from __future__ import annotations

import sqlite3

from trade_graph.adapters.persistence.activation_schema import STATEMENTS as ACTIVATION_STATEMENTS
from trade_graph.adapters.persistence.budget_origin_schema import STATEMENTS as BUDGET_ORIGIN_STATEMENTS
from trade_graph.adapters.persistence.dashboard_schema import STATEMENTS as DASHBOARD_STATEMENTS
from trade_graph.adapters.persistence.engineering_schema import STATEMENTS as ENGINEERING_STATEMENTS
from trade_graph.adapters.persistence.financial_checkpoint_schema import STATEMENTS as FINANCIAL_CHECKPOINT_STATEMENTS
from trade_graph.adapters.persistence.financial_recovery_schema import STATEMENTS as FINANCIAL_RECOVERY_STATEMENTS
from trade_graph.adapters.persistence.financial_transition_schema import STATEMENTS as FINANCIAL_TRANSITION_STATEMENTS
from trade_graph.adapters.persistence.native_fee_reservation_schema import (
    STATEMENTS as NATIVE_FEE_RESERVATION_STATEMENTS,
)
from trade_graph.adapters.persistence.native_incident_schema import STATEMENTS as NATIVE_INCIDENT_STATEMENTS
from trade_graph.adapters.persistence.owner_expense_schema import STATEMENTS as OWNER_EXPENSE_STATEMENTS
from trade_graph.adapters.persistence.pilot_schema import STATEMENTS as PILOT_STATEMENTS
from trade_graph.adapters.persistence.price_history_schema import STATEMENTS as PRICE_HISTORY_STATEMENTS
from trade_graph.adapters.persistence.protected_runtime_schema import STATEMENTS as PROTECTED_RUNTIME_STATEMENTS
from trade_graph.adapters.persistence.review_schema import STATEMENTS as REVIEW_STATEMENTS
from trade_graph.adapters.persistence.service_schema import STATEMENTS as SERVICE_STATEMENTS
from trade_graph.adapters.persistence.startup_schema import STATEMENTS as STARTUP_STATEMENTS
from trade_graph.adapters.persistence.subscription_admission_schema import (
    STATEMENTS as SUBSCRIPTION_ADMISSION_STATEMENTS,
)
from trade_graph.adapters.persistence.subscription_attempt_schema import STATEMENTS as SUBSCRIPTION_ATTEMPT_STATEMENTS
from trade_graph.adapters.persistence.transport_evidence_schema import STATEMENTS as TRANSPORT_EVIDENCE_STATEMENTS
from trade_graph.adapters.persistence.usage_provenance_schema import STATEMENTS as USAGE_PROVENANCE_STATEMENTS

STATEMENTS: list[tuple[str, list[str]]] = [
    (
        "0001",
        [
            """CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )""",
            """CREATE TABLE portfolios (
                portfolio_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                reporting_currency TEXT NOT NULL,
                experiment_id TEXT NOT NULL,
                status TEXT NOT NULL,
                reset_of TEXT,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE ledger_events (
                event_id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                effective_at TEXT NOT NULL,
                external_ref TEXT,
                UNIQUE (portfolio_id, sequence),
                UNIQUE (portfolio_id, external_ref)
            )""",
            """CREATE TABLE journal_transactions (
                transaction_id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                external_ref TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (portfolio_id, external_ref)
            )""",
            """CREATE TABLE journal_postings (
                posting_id TEXT PRIMARY KEY,
                transaction_id TEXT NOT NULL,
                portfolio_id TEXT NOT NULL,
                account TEXT NOT NULL,
                asset TEXT NOT NULL,
                amount TEXT NOT NULL
            )""",
            """CREATE TABLE fx_rates (
                rate_id TEXT PRIMARY KEY,
                base TEXT NOT NULL,
                quote TEXT NOT NULL,
                rate TEXT NOT NULL,
                source TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                valid_as_of TEXT NOT NULL,
                retrieved_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                stale INTEGER NOT NULL
            )""",
            """CREATE TABLE valuation_marks (
                mark_id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL,
                asset TEXT NOT NULL,
                quote_currency TEXT NOT NULL,
                mark TEXT NOT NULL,
                convention TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                stale INTEGER NOT NULL,
                source TEXT NOT NULL
            )""",
            """CREATE TABLE owner_policy_revisions (
                revision_id TEXT PRIMARY KEY,
                document_json TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE mandates (
                mandate_id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                document_json TEXT NOT NULL,
                active INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (portfolio_id, revision)
            )""",
            """CREATE TABLE pause_states (
                portfolio_id TEXT PRIMARY KEY,
                profile TEXT NOT NULL,
                originator TEXT NOT NULL,
                reason TEXT NOT NULL,
                scope TEXT NOT NULL,
                requested_at TEXT NOT NULL,
                achieved TEXT NOT NULL,
                details_json TEXT NOT NULL
            )""",
            """CREATE TABLE instruments (
                venue TEXT NOT NULL,
                symbol TEXT NOT NULL,
                document_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (venue, symbol)
            )""",
            """CREATE TABLE observations (
                observation_id TEXT PRIMARY KEY,
                venue TEXT NOT NULL,
                symbol TEXT NOT NULL,
                event_time TEXT NOT NULL,
                available_at TEXT NOT NULL,
                document_json TEXT NOT NULL,
                sequence INTEGER NOT NULL
            )""",
            """CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                root_task_id TEXT NOT NULL,
                parent_id TEXT,
                portfolio_id TEXT,
                role TEXT NOT NULL,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                due_at TEXT,
                priority INTEGER NOT NULL,
                dedup_key TEXT,
                expected_version TEXT,
                allocated_spend TEXT,
                max_steps INTEGER NOT NULL,
                max_attempts INTEGER NOT NULL,
                attempts_used INTEGER NOT NULL DEFAULT 0,
                lease_owner TEXT,
                lease_expires_at TEXT,
                input_json TEXT NOT NULL,
                output_json TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (portfolio_id, dedup_key)
            )""",
            """CREATE TABLE schedules (
                schedule_id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL,
                name TEXT NOT NULL,
                last_due_at TEXT,
                next_due_at TEXT NOT NULL,
                missed_run_policy TEXT NOT NULL,
                cursor TEXT,
                interval_seconds INTEGER NOT NULL
            )""",
            """CREATE TABLE decisions (
                decision_id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL,
                action TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                mandate_revision TEXT NOT NULL,
                policy_revision TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                system_version_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                task_id TEXT
            )""",
            """CREATE TABLE order_intents (
                intent_id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL,
                client_order_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL,
                symbol TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE order_attempts (
                attempt_id TEXT PRIMARY KEY,
                intent_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                created_at TEXT NOT NULL,
                result_json TEXT NOT NULL
            )""",
            """CREATE TABLE fills (
                fill_id TEXT PRIMARY KEY,
                venue TEXT NOT NULL,
                account_id TEXT NOT NULL,
                trade_id TEXT NOT NULL,
                portfolio_id TEXT NOT NULL,
                intent_id TEXT,
                document_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (venue, account_id, trade_id)
            )""",
            """CREATE TABLE position_reservations (
                reservation_id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL,
                intent_id TEXT NOT NULL UNIQUE,
                asset TEXT NOT NULL,
                amount TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE deployment_budget (
                deployment_id TEXT PRIMARY KEY,
                currency TEXT NOT NULL,
                total_allowance TEXT NOT NULL,
                period_allowance TEXT NOT NULL,
                priority_reserve TEXT NOT NULL,
                daily_limit TEXT NOT NULL,
                root_limit TEXT NOT NULL
            )""",
            """CREATE TABLE role_allocations (
                deployment_id TEXT NOT NULL,
                role TEXT NOT NULL,
                amount TEXT NOT NULL,
                PRIMARY KEY (deployment_id, role)
            )""",
            """CREATE TABLE budget_reservations (
                reservation_id TEXT PRIMARY KEY,
                deployment_id TEXT NOT NULL,
                role TEXT NOT NULL,
                task_id TEXT,
                root_task_id TEXT,
                amount TEXT NOT NULL,
                currency TEXT NOT NULL,
                state TEXT NOT NULL,
                price_card_id TEXT NOT NULL,
                purpose TEXT NOT NULL,
                synthetic INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE usage_receipts (
                receipt_id TEXT PRIMARY KEY,
                reservation_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                provider_request_id TEXT,
                model TEXT NOT NULL,
                native_cost TEXT NOT NULL,
                native_currency TEXT NOT NULL,
                reporting_cost TEXT,
                reporting_currency TEXT,
                status TEXT NOT NULL,
                usage_json TEXT NOT NULL,
                synthetic INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (provider, provider_request_id)
            )""",
            """CREATE TABLE cost_allocations (
                allocation_id TEXT PRIMARY KEY,
                receipt_id TEXT NOT NULL,
                portfolio_id TEXT NOT NULL,
                weight TEXT NOT NULL,
                amount TEXT NOT NULL
            )""",
            """CREATE TABLE price_cards (
                price_card_id TEXT PRIMARY KEY,
                document_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE findings (
                finding_id TEXT PRIMARY KEY,
                portfolio_id TEXT,
                document_json TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                available_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE lessons (
                revision_id TEXT PRIMARY KEY,
                lesson_id TEXT NOT NULL,
                portfolio_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                document_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (lesson_id, revision)
            )""",
            """CREATE TABLE experiments (
                experiment_id TEXT PRIMARY KEY,
                document_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE change_tasks (
                change_id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL,
                state TEXT NOT NULL,
                document_json TEXT NOT NULL,
                baseline_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE candidates (
                candidate_id TEXT PRIMARY KEY,
                change_id TEXT NOT NULL,
                state TEXT NOT NULL,
                content_hash TEXT,
                baseline_hash TEXT NOT NULL,
                attestation_json TEXT,
                document_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE active_versions (
                portfolio_id TEXT PRIMARY KEY,
                version_id TEXT NOT NULL,
                artifact_hash TEXT NOT NULL,
                fingerprint_json TEXT NOT NULL,
                activated_at TEXT NOT NULL
            )""",
            """CREATE TABLE version_events (
                event_id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                from_hash TEXT,
                to_hash TEXT,
                created_at TEXT NOT NULL,
                details_json TEXT NOT NULL
            )""",
            """CREATE TABLE snapshots (
                snapshot_id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL,
                as_of TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE process_leases (
                lease_name TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )""",
            """CREATE TABLE activity_events (
                event_id TEXT PRIMARY KEY,
                portfolio_id TEXT,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                hash TEXT NOT NULL,
                prev_hash TEXT
            )""",
            """CREATE TABLE broker_orders (
                client_order_id TEXT PRIMARY KEY,
                venue_order_id TEXT NOT NULL,
                status TEXT NOT NULL,
                document_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE outbox (
                outbox_id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload_ref TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (kind, payload_ref)
            )""",
            """CREATE TABLE sessions (
                token_hash TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                csrf_secret TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE live_gate (
                deployment_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL,
                document_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE component_fingerprints (
                portfolio_id TEXT NOT NULL,
                name TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                PRIMARY KEY (portfolio_id, name)
            )""",
        ],
    ),
    (
        "0002",
        [
            "ALTER TABLE tasks ADD COLUMN lease_token TEXT",
            "CREATE INDEX tasks_claimable ON tasks (status, due_at, lease_expires_at)",
        ],
    ),
    (
        "0003",
        [
            "ALTER TABLE budget_reservations ADD COLUMN system_version_id TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE budget_reservations ADD COLUMN attempt_kind TEXT NOT NULL DEFAULT 'primary'",
            """CREATE TABLE invoice_reconciliations (
                reconciliation_id TEXT PRIMARY KEY,
                deployment_id TEXT NOT NULL,
                invoice_id TEXT NOT NULL UNIQUE,
                invoice_total TEXT NOT NULL,
                recorded_total TEXT NOT NULL,
                unexplained TEXT NOT NULL,
                currency TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
        ],
    ),
    (
        "0004",
        [
            """CREATE TABLE controller_attestations (
                attestation_id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL UNIQUE,
                checks_module_hash TEXT NOT NULL,
                exit_code INTEGER NOT NULL,
                command TEXT NOT NULL,
                stdout_sha256 TEXT NOT NULL,
                stderr_sha256 TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
        ],
    ),
]


STATEMENTS.append(("0005", REVIEW_STATEMENTS))
STATEMENTS.append(("0006", ENGINEERING_STATEMENTS))
STATEMENTS.append(("0007", ACTIVATION_STATEMENTS))
STATEMENTS.append(("0008", DASHBOARD_STATEMENTS))
STATEMENTS.append(("0009", SERVICE_STATEMENTS))
STATEMENTS.append(("0010", PROTECTED_RUNTIME_STATEMENTS))
STATEMENTS.append(("0011", USAGE_PROVENANCE_STATEMENTS))
STATEMENTS.append(("0012", TRANSPORT_EVIDENCE_STATEMENTS))
STATEMENTS.append(("0013", PILOT_STATEMENTS))
STATEMENTS.append(("0014", [*NATIVE_INCIDENT_STATEMENTS, *FINANCIAL_CHECKPOINT_STATEMENTS]))
STATEMENTS.append(("0015", NATIVE_FEE_RESERVATION_STATEMENTS))
STATEMENTS.append(("0016", BUDGET_ORIGIN_STATEMENTS))
STATEMENTS.append(("0017", PRICE_HISTORY_STATEMENTS))
STATEMENTS.append(("0018", STARTUP_STATEMENTS))
STATEMENTS.append(("0019", SUBSCRIPTION_ATTEMPT_STATEMENTS))
STATEMENTS.append(("0020", SUBSCRIPTION_ADMISSION_STATEMENTS))
STATEMENTS.append(("0021", ["ALTER TABLE subscription_attempts ADD COLUMN quota_json TEXT NOT NULL DEFAULT '{}'"]))
STATEMENTS.append(("0022", FINANCIAL_TRANSITION_STATEMENTS))
STATEMENTS.append(("0023", FINANCIAL_RECOVERY_STATEMENTS))
STATEMENTS.append(("0024", OWNER_EXPENSE_STATEMENTS))

def applied_versions(connection: sqlite3.Connection) -> set[str]:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if row is None:
        return set()
    return {item[0] for item in connection.execute("SELECT version FROM schema_migrations")}


def apply_migrations(connection: sqlite3.Connection, applied_at: str) -> list[str]:
    """Apply the pending schema atomically, also when called inside Alembic.

    A writer lock is acquired before reading versions on standalone startup.
    Nested callers retain ownership of their outer transaction.
    """
    nested = connection.in_transaction
    if nested:
        connection.execute("SAVEPOINT trade_graph_migrations")
    else:
        connection.execute("BEGIN IMMEDIATE")
    try:
        done = applied_versions(connection)
        ran: list[str] = []
        for version, statements in STATEMENTS:
            if version in done:
                continue
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, applied_at),
            )
            ran.append(version)
        connection.execute("RELEASE SAVEPOINT trade_graph_migrations" if nested else "COMMIT")
        return ran
    except BaseException:
        if nested:
            connection.execute("ROLLBACK TO SAVEPOINT trade_graph_migrations")
            connection.execute("RELEASE SAVEPOINT trade_graph_migrations")
        elif connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
