"""Owner controls and subscription journal are additive to retained financial state."""
import sqlite3

from trade_graph.adapters.persistence.db import Database
from trade_graph.adapters.persistence.migrate import STATEMENTS, apply_migrations


def test_startup_migration_preserves_legacy_rows_and_is_idempotent(tmp_path):
    path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(path, isolation_level=None)
    stamp = "2026-10-06T00:00:00Z"
    for version, statements in STATEMENTS:
        if version >= "0018":
            break
        for statement in statements:
            conn.execute(statement)
        conn.execute("INSERT INTO schema_migrations VALUES (?, ?)", (version, stamp))
    conn.execute("INSERT INTO portfolios VALUES (?, ?, ?, ?, ?, ?, ?)",
                 ("synthetic-portfolio", "paper", "EUR", "synthetic-experiment", "ACTIVE", None, stamp))
    conn.execute("INSERT INTO ledger_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                 ("synthetic-event", "synthetic-portfolio", 1, "synthetic-deposit",
                  '{"amount":"10000","currency":"USD"}', stamp, "synthetic-opening"))
    before = list(conn.execute("SELECT * FROM ledger_events"))
    conn.close()
    db = Database(path)
    assert [tuple(row) for row in db.execute("SELECT * FROM ledger_events")] == before
    for table in ("graph_service_runs", "service_control_requests",
                  "subscription_invocations", "subscription_provider_state"):
        assert db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
    assert "pid_start_ticks" in {row[1] for row in db.execute("PRAGMA table_info(graph_service_runs)")}
    assert apply_migrations(db.connection, stamp) == []
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    db.close()
