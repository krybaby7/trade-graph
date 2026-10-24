"""Immutable offline owner-approved database identity recovery receipts."""

STATEMENTS = [
    """CREATE TABLE protected_financial_recoveries (
        operation_id TEXT PRIMARY KEY,
        recovery_sha256 TEXT NOT NULL UNIQUE,
        approval_sha256 TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        authentication TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TRIGGER protected_financial_recoveries_no_update
        BEFORE UPDATE ON protected_financial_recoveries
        BEGIN SELECT RAISE(ABORT, 'protected financial recoveries are immutable'); END""",
    """CREATE TRIGGER protected_financial_recoveries_no_delete
        BEFORE DELETE ON protected_financial_recoveries
        BEGIN SELECT RAISE(ABORT, 'protected financial recoveries are immutable'); END""",
]
