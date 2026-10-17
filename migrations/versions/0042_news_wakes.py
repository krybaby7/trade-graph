"""Add the news alarm's wake routes: whom each would-wake verdict woke, or would have woken in shadow mode (T45)."""

from alembic import op

revision = "0042"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from trade_graph.adapters.persistence.migrate import apply_migrations
    from trade_graph.domain.clock import SystemClock, utc_iso

    apply_migrations(op.get_bind().connection.driver_connection, utc_iso(SystemClock().now()))


def downgrade() -> None:
    raise RuntimeError("news wake route downgrade is not automatic")
