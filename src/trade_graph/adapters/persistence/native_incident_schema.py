"""Immutable protected owner review receipts; integrated by the migration owner."""

STATEMENTS = [
    """CREATE TABLE native_incident_resolutions (
        resolution_id TEXT PRIMARY KEY,
        incident_id TEXT NOT NULL REFERENCES activity_events(event_id),
        generation INTEGER NOT NULL CHECK(generation > 0),
        command_id TEXT NOT NULL UNIQUE REFERENCES dashboard_commands(command_id),
        owner_revision INTEGER NOT NULL CHECK(owner_revision > 0),
        scope_json TEXT NOT NULL,
        incident_sha256 TEXT NOT NULL CHECK(length(incident_sha256)=64),
        financial_sha256 TEXT NOT NULL CHECK(length(financial_sha256)=64),
        observation_sha256 TEXT NOT NULL CHECK(length(observation_sha256)=64),
        controller_sha256 TEXT NOT NULL CHECK(length(controller_sha256)=64),
        receipt_json TEXT NOT NULL,
        signature TEXT NOT NULL CHECK(length(signature)=64),
        created_at TEXT NOT NULL,
        UNIQUE(incident_id,generation)
    )""",
    """CREATE TABLE native_incident_resolution_revocations (
        revocation_id TEXT PRIMARY KEY,
        resolution_id TEXT NOT NULL UNIQUE REFERENCES native_incident_resolutions(resolution_id),
        command_id TEXT NOT NULL UNIQUE REFERENCES dashboard_commands(command_id),
        owner_revision INTEGER NOT NULL CHECK(owner_revision > 0),
        receipt_json TEXT NOT NULL,
        signature TEXT NOT NULL CHECK(length(signature)=64),
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX native_incident_latest ON native_incident_resolutions(incident_id,generation DESC)",
]

for _table in ("native_incident_resolutions", "native_incident_resolution_revocations"):
    for _operation in ("UPDATE", "DELETE"):
        STATEMENTS.append(
            f"CREATE TRIGGER {_table}_{_operation.lower()} BEFORE {_operation} ON {_table} "
            "BEGIN SELECT RAISE(ABORT,'protected native incident receipts are immutable'); END"
        )
