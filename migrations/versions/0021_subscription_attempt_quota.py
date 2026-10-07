"""Retain each dispatch admission observation without modifying older receipts."""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from trade_graph.adapters.persistence.migrate import apply_migrations
    from trade_graph.domain.clock import SystemClock, utc_iso

    apply_migrations(op.get_bind().connection.driver_connection, utc_iso(SystemClock().now()))


def downgrade() -> None:
    raise RuntimeError("retained subscription quota evidence downgrade is not automatic")
