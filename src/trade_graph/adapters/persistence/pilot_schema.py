"""Protected pilot grants, bounded pending effects and immutable lifecycle events."""

STATEMENTS = [
    """CREATE TABLE live_pilot_grants (
        authorization_id TEXT PRIMARY KEY,
        deployment_id TEXT NOT NULL,
        portfolio_id TEXT NOT NULL REFERENCES portfolios(portfolio_id),
        scope_json TEXT NOT NULL,
        authorization_json TEXT NOT NULL,
        authorization_sha256 TEXT NOT NULL,
        bundle_sha256 TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('PENDING','ACTIVE','RECOVERY_REQUIRED','STOPPING','STOPPED','REVOKED')),
        generation INTEGER NOT NULL DEFAULT 0 CHECK(generation >= 0),
        stop_profile TEXT NOT NULL CHECK(stop_profile IN ('MANAGE_ONLY','FLATTEN')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE UNIQUE INDEX live_pilot_one_open_portfolio ON live_pilot_grants(portfolio_id)
        WHERE state IN ('PENDING','ACTIVE','RECOVERY_REQUIRED','STOPPING')""",
    """CREATE TABLE live_pilot_effects (
        effect_id TEXT PRIMARY KEY,
        authorization_id TEXT NOT NULL REFERENCES live_pilot_grants(authorization_id),
        intent_id TEXT NOT NULL UNIQUE REFERENCES order_intents(intent_id),
        generation INTEGER NOT NULL CHECK(generation >= 0),
        request_sha256 TEXT NOT NULL,
        native_currency TEXT NOT NULL,
        maximum_native_cost TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('PREPARED','SUBMITTING','UNKNOWN','COMMITTED','RELEASED')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE live_pilot_events (
        event_id TEXT PRIMARY KEY,
        authorization_id TEXT NOT NULL REFERENCES live_pilot_grants(authorization_id),
        generation INTEGER NOT NULL CHECK(generation >= 0),
        kind TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TRIGGER live_pilot_events_no_update BEFORE UPDATE ON live_pilot_events
        BEGIN SELECT RAISE(ABORT, 'pilot events are append-only'); END""",
    """CREATE TRIGGER live_pilot_events_no_delete BEFORE DELETE ON live_pilot_events
        BEGIN SELECT RAISE(ABORT, 'pilot events are append-only'); END""",
]
