"""Provider admission adds no financial history mutation."""
import sqlite3

import pytest

from trade_graph.adapters.persistence.db import Database
from trade_graph.adapters.persistence.migrate import STATEMENTS, apply_migrations


def test_admission_migration_retains_financial_rows_and_exclusive_provider_slot(tmp_path):
    path = tmp_path / "retained.sqlite"
    conn = sqlite3.connect(path, isolation_level=None)
    stamp = "2026-10-07T00:00:00Z"
    for version, statements in STATEMENTS:
        if version >= "0020":
            break
        for statement in statements:
            conn.execute(statement)
        conn.execute("INSERT INTO schema_migrations VALUES (?, ?)", (version, stamp))
    conn.execute("INSERT INTO portfolios VALUES (?, ?, ?, ?, ?, ?, ?)",
                 ("synthetic", "paper", "EUR", "experiment", "ACTIVE", None, stamp))
    before = list(conn.execute("SELECT * FROM portfolios"))
    conn.close()
    db = Database(path)
    assert db.execute("SELECT 1 FROM schema_migrations WHERE version='0020'").fetchone()
    assert "quota_json" in {row[1] for row in db.execute("PRAGMA table_info(subscription_attempts)")}
    assert [tuple(row) for row in db.execute("SELECT * FROM portfolios")] == before
    sql = "INSERT INTO subscription_provider_admissions VALUES (?, ?, ?, ?, ?)"
    db.execute(sql, ("codex_subscription", "invocation", "lease", stamp, stamp))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(sql, ("codex_subscription", "other", "other-lease", stamp, stamp))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(sql, ("api_billed", "other", "other-lease", stamp, stamp))
    assert apply_migrations(db.connection, stamp) == []
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    db.close()


def test_attempt_quota_column_preserves_existing_attempt_receipt(tmp_path):
    path = tmp_path / "legacy-attempt.sqlite"
    conn = sqlite3.connect(path, isolation_level=None)
    stamp = "2026-10-07T00:00:00Z"
    for version, statements in STATEMENTS:
        if version >= "0021":
            break
        for statement in statements:
            conn.execute(statement)
        conn.execute("INSERT INTO schema_migrations VALUES (?, ?)", (version, stamp))
    conn.execute("""INSERT INTO subscription_invocations
        (invocation_id,request_hash,task_id,root_task_id,role,run_id,system_version_id,
         provider,requested_model,state,quota_json,created_at,updated_at)
        VALUES ('inv','hash','task','task','research','run','v','codex_subscription',
                'gpt-6.1-sol','FAILED','{}',?,?)""", (stamp, stamp))
    conn.execute("""INSERT INTO subscription_attempts
        (attempt_id,invocation_id,attempt_index,request_hash,provider,requested_model,state,created_at,updated_at)
        VALUES ('attempt','inv',1,'hash','codex_subscription','gpt-6.1-sol','FAILED',?,?)""", (stamp, stamp))
    before = conn.execute("SELECT * FROM subscription_attempts").fetchone()
    conn.close()
    db = Database(path)
    after = db.execute("SELECT * FROM subscription_attempts").fetchone()
    assert tuple(after)[:-1] == before
    assert after["quota_json"] == "{}"
    assert apply_migrations(db.connection, stamp) == []
    db.close()
