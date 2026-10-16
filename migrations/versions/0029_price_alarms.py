"""Add the price-alarm store: one-shot alarms, their delivery queue and feed health (T33)."""

from alembic import op

revision = "0029"
# 0026-0028 are reserved for other wave-1 packages; the integrator re-links this to the newest earlier revision.
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from trade_graph.adapters.persistence.migrate import apply_migrations
    from trade_graph.domain.clock import SystemClock, utc_iso

    apply_migrations(op.get_bind().connection.driver_connection, utc_iso(SystemClock().now()))


def downgrade() -> None:
    raise RuntimeError("price alarm history downgrade is not automatic")
