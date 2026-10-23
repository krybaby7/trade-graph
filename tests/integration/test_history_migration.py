"""Additive history migration preserves old financial and retained snapshot rows."""

import sqlite3

from trade_graph.adapters.persistence.db import Database
from trade_graph.adapters.persistence.migrate import STATEMENTS, apply_migrations
from trade_graph.domain.clock import SystemClock, utc_iso


def test_old_database_is_upgraded_without_replacing_financial_or_snapshot_records(tmp_path):
    path = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(path, isolation_level=None)
    now = utc_iso(SystemClock().now())
    for version, statements in STATEMENTS:
        if version == "0017":
            break
        for statement in statements:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?)", (version, now))
    connection.execute("INSERT INTO portfolios VALUES (?, ?, ?, ?, ?, ?, ?)",
                       ("synthetic-portfolio", "paper", "EUR", "synthetic-experiment", "ACTIVE", None, now))
    connection.execute("INSERT INTO ledger_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                       ("synthetic-event", "synthetic-portfolio", 1, "synthetic-deposit",
                        '{"amount": "10000", "currency": "USD"}', now, "synthetic-opening"))
    connection.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
                       ("synthetic-retained", "synthetic-portfolio", now, '{"synthetic": true}', now))
    retained = list(connection.execute("SELECT * FROM snapshots"))
    ledger = list(connection.execute("SELECT * FROM ledger_events"))
    connection.close()
    database = Database(path)
    assert [tuple(row) for row in database.execute("SELECT * FROM snapshots")] == retained
    assert [tuple(row) for row in database.execute("SELECT * FROM ledger_events")] == ledger
    assert database.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0] == 1
    assert database.execute("SELECT COUNT(*) FROM hourly_candles").fetchone()[0] == 0
    assert apply_migrations(database.connection, now) == []
    assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert database.execute("PRAGMA foreign_key_check").fetchall() == []
    database.close()
