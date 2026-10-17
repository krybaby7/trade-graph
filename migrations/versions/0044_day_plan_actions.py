"""Add the Trader's position actions (close a day-plan position, move its exit) and the valuation-mark index (C4)."""

from alembic import op

revision = "0044"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from trade_graph.adapters.persistence.migrate import apply_migrations
    from trade_graph.domain.clock import SystemClock, utc_iso

    apply_migrations(op.get_bind().connection.driver_connection, utc_iso(SystemClock().now()))


def downgrade() -> None:
    raise RuntimeError("day-plan action record downgrade is not automatic")
