"""Add the price-alarm store: one-shot alarms, their delivery queue and feed health (T33)."""

from alembic import op

revision = "0029"
# Wave 1 integration linked this to 0028 (W1-C); W1-B left its reserved 0027 unused.
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from trade_graph.adapters.persistence.migrate import apply_migrations
    from trade_graph.domain.clock import SystemClock, utc_iso

    apply_migrations(op.get_bind().connection.driver_connection, utc_iso(SystemClock().now()))


def downgrade() -> None:
    raise RuntimeError("price alarm history downgrade is not automatic")
