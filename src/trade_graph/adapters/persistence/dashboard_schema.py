"""Protected owner-command revisions and durable idempotency records."""

STATEMENTS = [
    """CREATE TABLE dashboard_control_state (
        scope TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK (revision >= 0)
    )""",
    """CREATE TABLE dashboard_commands (
        command_id TEXT PRIMARY KEY, scope TEXT NOT NULL, request_hash TEXT NOT NULL,
        response_json TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
]
