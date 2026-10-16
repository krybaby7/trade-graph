"""Add the coin scanner's record: scans, the pairs that passed, and the shortlist entries (T31)."""

from alembic import op

revision = "0031"
# W2-B branched after 0029 (W1-D). The wave 2 integration re-links this to the newest earlier revision (0030, W2-A).
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from trade_graph.adapters.persistence.migrate import apply_migrations
    from trade_graph.domain.clock import SystemClock, utc_iso

    apply_migrations(op.get_bind().connection.driver_connection, utc_iso(SystemClock().now()))


def downgrade() -> None:
    raise RuntimeError("coin scan history downgrade is not automatic")
