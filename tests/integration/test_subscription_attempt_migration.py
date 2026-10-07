"""Subscription attempts remain separate from financial and aggregate receipts."""

import sqlite3

import pytest

from trade_graph.adapters.persistence.db import Database


def test_subscription_attempt_schema_is_additive_and_rejects_orphans(tmp_path):
    path = tmp_path / "account.sqlite"
    database = Database(path)
    assert database.execute("SELECT 1 FROM schema_migrations WHERE version='0019'").fetchone()
    columns = {row[1] for row in database.execute("PRAGMA table_info(subscription_attempts)")}
    assert {"attempt_id", "invocation_id", "attempt_index", "request_hash", "provider",
            "requested_model", "actual_model", "state", "result_json", "usage_json",
            "cost_status", "actual_cost_native", "created_at", "updated_at"} <= columns
    with pytest.raises(sqlite3.IntegrityError):
        database.execute("""INSERT INTO subscription_attempts
            (attempt_id,invocation_id,attempt_index,request_hash,provider,requested_model,state,created_at,updated_at)
            VALUES ('attempt','missing',1,'hash','codex_subscription','gpt-6.1-sol','DISPATCHED','now','now')""")
    assert database.execute("SELECT COUNT(*) FROM journal_postings").fetchone()[0] == 0
    database.close()
