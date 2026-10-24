"""Additive review migration. Legacy global attestations never confer new authority."""

STATEMENTS = [
    """CREATE TABLE secretary_inputs (portfolio_id TEXT PRIMARY KEY,
        activity_row INTEGER NOT NULL, task_row INTEGER NOT NULL)""",
    "ALTER TABLE tasks ADD COLUMN deadline_at TEXT",
    "ALTER TABLE change_tasks ADD COLUMN attempts_used INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE change_tasks ADD COLUMN lease_token TEXT",
    "ALTER TABLE change_tasks ADD COLUMN lease_expires_at TEXT",
    """CREATE TABLE secretary_reports (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, report_id TEXT NOT NULL UNIQUE,
        portfolio_id TEXT NOT NULL, role TEXT NOT NULL, kind TEXT NOT NULL,
        document_json TEXT NOT NULL, material INTEGER NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE secretary_cursors (portfolio_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL)""",
    """CREATE TABLE secretary_digests (
        digest_id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL, from_sequence INTEGER NOT NULL,
        to_sequence INTEGER NOT NULL, document_json TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE (portfolio_id, from_sequence, to_sequence))""",
    """CREATE TABLE role_results (
        task_id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL, role TEXT NOT NULL,
        status TEXT NOT NULL, document_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE leader_decisions (
        decision_id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE, portfolio_id TEXT NOT NULL,
        snapshot_id TEXT NOT NULL, document_json TEXT NOT NULL, state TEXT NOT NULL,
        result_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE engineering_commissions (
        change_id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL, decision_id TEXT NOT NULL,
        task_hash TEXT NOT NULL, worker_task_id TEXT NOT NULL UNIQUE,
        policy_revision TEXT NOT NULL, mandate_revision INTEGER NOT NULL,
        baseline_hash TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL,
        FOREIGN KEY (decision_id) REFERENCES leader_decisions(decision_id))""",
    """CREATE TABLE engineering_attempts (
        attempt_id TEXT PRIMARY KEY, change_id TEXT NOT NULL, number INTEGER NOT NULL,
        state TEXT NOT NULL, details_json TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE (change_id, number))""",
    """CREATE TABLE candidate_attestations (
        attestation_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL UNIQUE,
        portfolio_id TEXT NOT NULL, change_id TEXT NOT NULL, decision_id TEXT NOT NULL,
        task_hash TEXT NOT NULL, content_hash TEXT NOT NULL, baseline_hash TEXT NOT NULL,
        checks_module_hash TEXT NOT NULL, exit_code INTEGER NOT NULL,
        report_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE version_history (
        portfolio_id TEXT NOT NULL, version_id TEXT NOT NULL, artifact_hash TEXT NOT NULL,
        PRIMARY KEY (portfolio_id, version_id))""",
    """INSERT INTO version_history SELECT portfolio_id, version_id, artifact_hash FROM active_versions""",
]
