"""Add the owner's AI spending records: prepaid credit readings and testing alert amounts (T40)."""

from alembic import op

revision = "0032"
# W2-C branch: 0030 (W2-A) and 0031 (W2-B) are reserved in parallel; the wave 2 integration re-links this
# revision after whichever of them adds a migration.
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from trade_graph.adapters.persistence.migrate import apply_migrations
    from trade_graph.domain.clock import SystemClock, utc_iso

    apply_migrations(op.get_bind().connection.driver_connection, utc_iso(SystemClock().now()))


def downgrade() -> None:
    raise RuntimeError("AI spending record downgrade is not automatic")
