"""Owner-approved financial continuity transitions are immutable audit records."""

STATEMENTS = [
    """CREATE TABLE protected_financial_transitions (
        operation_id TEXT PRIMARY KEY,
        transition_sha256 TEXT NOT NULL UNIQUE,
        approval_sha256 TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        authentication TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TRIGGER protected_financial_transitions_no_update
        BEFORE UPDATE ON protected_financial_transitions
        BEGIN SELECT RAISE(ABORT, 'protected financial transitions are immutable'); END""",
    """CREATE TRIGGER protected_financial_transitions_no_delete
        BEFORE DELETE ON protected_financial_transitions
        BEGIN SELECT RAISE(ABORT, 'protected financial transitions are immutable'); END""",
]
