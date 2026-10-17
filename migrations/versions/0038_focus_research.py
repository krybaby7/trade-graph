"""Add the owner's Perplexity switch and Research's focus-round records (T37)."""

from alembic import op

revision = "0038"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from trade_graph.adapters.persistence.migrate import apply_migrations
    from trade_graph.domain.clock import SystemClock, utc_iso

    apply_migrations(op.get_bind().connection.driver_connection, utc_iso(SystemClock().now()))


def downgrade() -> None:
    raise RuntimeError("focus-research record downgrade is not automatic")
