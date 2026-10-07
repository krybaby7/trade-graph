"""Fenced scheduler leases; application migrations remain authoritative."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from trade_graph.adapters.persistence.migrate import apply_migrations
    from trade_graph.domain.clock import SystemClock, utc_iso

    connection = op.get_bind().connection.driver_connection
    apply_migrations(connection, utc_iso(SystemClock().now()))


def downgrade() -> None:
    raise RuntimeError("financial schema downgrade is not automatic")
