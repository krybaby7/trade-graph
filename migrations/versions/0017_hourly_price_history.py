"""Retain completed hourly candle revisions without rewriting earlier evidence."""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from trade_graph.adapters.persistence.migrate import apply_migrations
    from trade_graph.domain.clock import SystemClock, utc_iso

    apply_migrations(op.get_bind().connection.driver_connection, utc_iso(SystemClock().now()))


def downgrade() -> None:
    raise RuntimeError("retained evidence schema downgrade is not automatic")
