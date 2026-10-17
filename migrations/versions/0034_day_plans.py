"""Add the Trader day plans: plans, their resting entries, the protective exits attached to fills, and events (T34)."""

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from trade_graph.adapters.persistence.migrate import apply_migrations
    from trade_graph.domain.clock import SystemClock, utc_iso

    apply_migrations(op.get_bind().connection.driver_connection, utc_iso(SystemClock().now()))


def downgrade() -> None:
    raise RuntimeError("day-plan record downgrade is not automatic")
