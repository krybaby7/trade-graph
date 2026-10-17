"""Add the Leader's news-round records: each round and the requests it routed through the event engine (T38)."""

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from trade_graph.adapters.persistence.migrate import apply_migrations
    from trade_graph.domain.clock import SystemClock, utc_iso

    apply_migrations(op.get_bind().connection.driver_connection, utc_iso(SystemClock().now()))


def downgrade() -> None:
    raise RuntimeError("leader news-round record downgrade is not automatic")
