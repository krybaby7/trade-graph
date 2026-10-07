"""Durable subscription retry/fallback attempts; parent is the aggregate receipt."""

STATEMENTS = [
    """CREATE TABLE subscription_attempts (
        attempt_id TEXT PRIMARY KEY,
        invocation_id TEXT NOT NULL REFERENCES subscription_invocations(invocation_id),
        attempt_index INTEGER NOT NULL CHECK(attempt_index >= 1),
        request_hash TEXT NOT NULL,
        provider TEXT NOT NULL,
        requested_model TEXT NOT NULL,
        actual_model TEXT,
        state TEXT NOT NULL CHECK(state IN ('DISPATCHED','COMPLETED','FAILED','UNCERTAIN')),
        result_json TEXT,
        usage_json TEXT,
        cost_status TEXT NOT NULL DEFAULT 'unknown' CHECK(cost_status = 'unknown'),
        actual_cost_native TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(invocation_id, attempt_index)
    )""",
]
