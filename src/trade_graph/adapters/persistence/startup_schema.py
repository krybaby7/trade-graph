"""Additive owner lifecycle and subscription usage state; never financial authority."""

STATEMENTS = [
    """CREATE TABLE graph_service_runs (
        run_id TEXT PRIMARY KEY,
        portfolio_id TEXT NOT NULL,
        mode TEXT NOT NULL,
        status TEXT NOT NULL,
        pid INTEGER,
        pid_start_ticks TEXT,
        requested_at TEXT NOT NULL,
        heartbeat_at TEXT,
        finished_at TEXT,
        error_type TEXT,
        stop_requested INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE service_control_requests (
        request_id TEXT PRIMARY KEY,
        portfolio_id TEXT NOT NULL,
        action TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE subscription_invocations (
        invocation_id TEXT PRIMARY KEY,
        request_hash TEXT NOT NULL,
        task_id TEXT NOT NULL,
        root_task_id TEXT NOT NULL,
        role TEXT NOT NULL,
        run_id TEXT NOT NULL,
        system_version_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        requested_model TEXT NOT NULL,
        actual_model TEXT,
        state TEXT NOT NULL CHECK(state IN ('BLOCKED','DISPATCHED','COMPLETED','FAILED','UNCERTAIN')),
        result_json TEXT,
        quota_json TEXT NOT NULL,
        usage_json TEXT,
        cost_status TEXT NOT NULL DEFAULT 'unknown' CHECK(cost_status IN ('unknown','not_incurred')),
        actual_cost_native TEXT,
        synthetic INTEGER NOT NULL DEFAULT 0 CHECK(synthetic IN (0,1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE subscription_provider_state (
        provider TEXT PRIMARY KEY,
        ai_paused INTEGER NOT NULL DEFAULT 0 CHECK(ai_paused IN (0,1)),
        reason TEXT NOT NULL DEFAULT '',
        quota_json TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL
    )""",
]
