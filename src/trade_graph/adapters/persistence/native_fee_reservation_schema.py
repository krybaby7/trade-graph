"""Secondary native fee holds retain their original protected allocation."""

STATEMENTS = [
    """CREATE TABLE native_fee_reservations (
        reservation_id TEXT PRIMARY KEY,
        portfolio_id TEXT NOT NULL REFERENCES portfolios(portfolio_id),
        intent_id TEXT NOT NULL REFERENCES order_intents(intent_id),
        asset TEXT NOT NULL,
        original_amount TEXT NOT NULL,
        current_amount TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('held', 'released')),
        plan_sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(intent_id, asset)
    )""",
    """CREATE INDEX native_fee_reservations_held
        ON native_fee_reservations(portfolio_id, asset, state)""",
    """CREATE TRIGGER native_fee_reservations_original_immutable
        BEFORE UPDATE ON native_fee_reservations
        WHEN NEW.reservation_id IS NOT OLD.reservation_id
          OR NEW.portfolio_id IS NOT OLD.portfolio_id
          OR NEW.intent_id IS NOT OLD.intent_id
          OR NEW.asset IS NOT OLD.asset
          OR NEW.original_amount IS NOT OLD.original_amount
          OR NEW.plan_sha256 IS NOT OLD.plan_sha256
          OR NEW.created_at IS NOT OLD.created_at
        BEGIN SELECT RAISE(ABORT, 'original native fee reservation is immutable'); END""",
    """CREATE TRIGGER native_fee_reservations_no_delete
        BEFORE DELETE ON native_fee_reservations
        BEGIN SELECT RAISE(ABORT, 'native fee reservation history is immutable'); END""",
]
