"""Original protected budget authority retained before model transport effects."""

STATEMENTS = [
    """CREATE TABLE protected_financial_budget_origins (
        origin_id TEXT PRIMARY KEY,
        reservation_id TEXT NOT NULL REFERENCES budget_reservations(reservation_id),
        event_kind TEXT NOT NULL CHECK (event_kind IN ('ORIGIN','RECEIPT')),
        deployment_id TEXT NOT NULL,
        manifest_sha256 TEXT NOT NULL,
        instance_id TEXT NOT NULL REFERENCES protected_runtime_instances(instance_id),
        portfolio_id TEXT NOT NULL REFERENCES portfolios(portfolio_id),
        origin_json TEXT NOT NULL,
        authentication TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (reservation_id, event_kind)
    )""",
    """CREATE INDEX protected_financial_budget_origin_deployment
        ON protected_financial_budget_origins (deployment_id, reservation_id)""",
    """CREATE TRIGGER protected_financial_budget_origins_no_update
        BEFORE UPDATE ON protected_financial_budget_origins
        BEGIN SELECT RAISE(ABORT, 'protected budget origins are immutable'); END""",
    """CREATE TRIGGER protected_financial_budget_origins_no_delete
        BEFORE DELETE ON protected_financial_budget_origins
        BEGIN SELECT RAISE(ABORT, 'protected budget origins are immutable'); END""",
]
