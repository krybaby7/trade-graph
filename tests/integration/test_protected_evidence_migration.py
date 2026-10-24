"""Upgrade real financial state and keep its new protected receipts append-only."""

import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trade_graph.adapters.persistence import migrate
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.ledger import Ledger
from trade_graph.domain.clock import FrozenClock, utc_iso


def test_upgrade_preserves_finance_and_immutable_evidence(tmp_path):
    path = tmp_path / "prior.sqlite"
    clock = FrozenClock(datetime(2026, 10, 4, tzinfo=UTC))
    legacy = sqlite3.connect(path, isolation_level=None)
    legacy.row_factory = sqlite3.Row
    for version, statements in migrate.STATEMENTS:
        if version == "0014":
            break
        for statement in statements:
            legacy.execute(statement)
        legacy.execute("INSERT INTO schema_migrations VALUES (?, ?)", (version, utc_iso(clock.now())))
    legacy.execute("""INSERT INTO portfolios VALUES ('p','paper','EUR','synthetic','active',NULL,?)""",
                   (utc_iso(clock.now()),))
    legacy.execute("""INSERT INTO ledger_events VALUES ('deposit','p',1,'deposit',?,?,'opening')""",
                   (json.dumps({"asset": "EUR", "amount": "100"}), utc_iso(clock.now())))
    legacy.close()

    database = Database(path)
    ledger = Ledger(database, clock)
    before = [dict(row) for row in database.execute("SELECT * FROM ledger_events")]
    assert ledger.books("p").cash_amount("EUR") == Decimal("100")
    assert "0014" in migrate.applied_versions(database.connection)
    now, digest = utc_iso(clock.now()), "a" * 64
    database.execute("""INSERT INTO activity_events VALUES ('incident','p',?,?,?, ?,NULL)""",
                     ("native_fill_execution_limit_discrepancy", "{}", now, digest))
    for command in ("review", "revoke"):
        database.execute("INSERT INTO dashboard_commands VALUES (?, 'owner:synthetic', ?, NULL, 'PROCESSING', ?)",
                         (command, digest, now))
    database.execute("""INSERT INTO native_incident_resolutions VALUES
        ('resolution','incident',1,'review',1,'{}',?,?,?,?,?, ?,?)""",
                     (digest, digest, digest, digest, "{}", digest, now))
    database.execute("""INSERT INTO native_incident_resolution_revocations VALUES
        ('revocation','resolution','revoke',2,'{}',?,?)""", (digest, now))
    database.execute("""INSERT INTO protected_financial_checkpoints VALUES
        ('checkpoint',?,'p',1,'{}',?,?)""", (digest, digest, now))
    for table in ("native_incident_resolutions", "native_incident_resolution_revocations",
                  "protected_financial_checkpoints"):
        for sql in (f"UPDATE {table} SET created_at = 'changed'", f"DELETE FROM {table}"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                database.execute(sql)
        assert database.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1
    assert [dict(row) for row in database.execute("SELECT * FROM ledger_events")] == before
    database.close()
    reopened = Database(path)
    assert Ledger(reopened, clock).books("p").cash_amount("EUR") == Decimal("100")
    assert reopened.execute("SELECT count(*) FROM protected_financial_checkpoints").fetchone()[0] == 1
    reopened.close()
