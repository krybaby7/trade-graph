"""Protected pre-dispatch attempts and bounded observed wire facts.

No headers, keys, request bodies or response bodies belong in this journal.
Hashes establish retained runtime observations, not external authentication.
"""

STATEMENTS = [
    """CREATE TABLE provider_transport_attempts (
        attempt_id TEXT PRIMARY KEY,
        reservation_id TEXT NOT NULL UNIQUE REFERENCES budget_reservations(reservation_id),
        invocation_id TEXT,
        provider TEXT NOT NULL,
        endpoint_sha256 TEXT NOT NULL,
        request_sha256 TEXT NOT NULL,
        transport_basis TEXT NOT NULL,
        synthetic INTEGER NOT NULL,
        outcome TEXT NOT NULL,
        response_sha256 TEXT,
        status_code INTEGER,
        response_bytes INTEGER,
        error_category TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT
    )""",
]
