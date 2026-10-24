"""Authenticated financial commitments; authoritative facts remain in the ledger."""

STATEMENTS = [
    """CREATE TABLE protected_financial_checkpoints (
        checkpoint_id TEXT PRIMARY KEY,
        manifest_sha256 TEXT NOT NULL,
        portfolio_id TEXT NOT NULL REFERENCES portfolios(portfolio_id),
        generation INTEGER NOT NULL CHECK (generation >= 1),
        payload_json TEXT NOT NULL,
        authentication TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (manifest_sha256, portfolio_id, generation)
    )""",
    """CREATE INDEX protected_financial_checkpoint_latest
        ON protected_financial_checkpoints (manifest_sha256, portfolio_id, generation DESC)""",
    """CREATE TRIGGER protected_financial_checkpoints_no_update
        BEFORE UPDATE ON protected_financial_checkpoints
        BEGIN SELECT RAISE(ABORT, 'protected financial checkpoints are immutable'); END""",
    """CREATE TRIGGER protected_financial_checkpoints_no_delete
        BEFORE DELETE ON protected_financial_checkpoints
        BEGIN SELECT RAISE(ABORT, 'protected financial checkpoints are immutable'); END""",
]
