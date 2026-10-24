"""Durable model effects and commissioned artifact work; no financial history rewrite."""

STATEMENTS = [
    """CREATE TABLE model_invocations (
        invocation_id TEXT PRIMARY KEY,
        request_hash TEXT NOT NULL,
        request_json TEXT NOT NULL,
        portfolio_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        root_task_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        system_version_id TEXT NOT NULL,
        reservation_id TEXT NOT NULL UNIQUE REFERENCES budget_reservations(reservation_id),
        state TEXT NOT NULL,
        result_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE engineering_jobs (
        task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
        portfolio_id TEXT NOT NULL,
        change_id TEXT NOT NULL UNIQUE REFERENCES change_tasks(change_id),
        commission_hash TEXT NOT NULL,
        state TEXT NOT NULL,
        attempt_id TEXT,
        document_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE INDEX engineering_jobs_state ON engineering_jobs(state)""",
]
