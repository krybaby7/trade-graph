"""Retain Perplexity research runs, software-run searches and their stored results (T36)."""

from alembic import op

revision = "0028"
# Cloud wave 1 reserves 0026 (W1-A) and 0027 (W1-B); the integrator re-chains this
# revision after whichever of them merges first (docs/CLOUD-BUILD-PLAN.md, rule 11).
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from trade_graph.adapters.persistence.migrate import apply_migrations
    from trade_graph.domain.clock import SystemClock, utc_iso

    apply_migrations(op.get_bind().connection.driver_connection, utc_iso(SystemClock().now()))


def downgrade() -> None:
    raise RuntimeError("web research evidence downgrade is not automatic")
