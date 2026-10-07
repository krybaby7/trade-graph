"""Serialize provider admission without holding a financial database writer lock."""

STATEMENTS = [
    """CREATE TABLE subscription_provider_admissions (
        provider TEXT PRIMARY KEY CHECK(provider IN ('codex_subscription','claude_subscription')),
        invocation_id TEXT NOT NULL,
        lease_id TEXT NOT NULL UNIQUE,
        lease_expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
]
