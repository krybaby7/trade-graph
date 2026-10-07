"""An additive upgrade preserves uncertain holds and their protected originals."""

import json
import sqlite3
from datetime import UTC, datetime

import pytest

from trade_graph.adapters.persistence import migrate
from trade_graph.adapters.persistence.db import Database
from trade_graph.domain.clock import utc_iso


def test_upgrade_retains_budget_and_protects_original_authority(tmp_path):
    path = tmp_path / "prior.sqlite"
    now, digest = utc_iso(datetime(2026, 10, 4, tzinfo=UTC)), "a" * 64
    prior = sqlite3.connect(path, isolation_level=None)
    for version, statements in migrate.STATEMENTS:
        if version == "0016":
            break
        for statement in statements:
            prior.execute(statement)
        prior.execute("INSERT INTO schema_migrations VALUES (?, ?)", (version, now))
    prior.execute("INSERT INTO portfolios VALUES ('p','paper','EUR','synthetic','active',NULL,?)", (now,))
    prior.execute("INSERT INTO protected_runtime_instances VALUES ('instance',?,NULL,NULL,0,'MANAGE_ONLY',?)",
                  (digest, now))
    prior.execute("INSERT INTO budget_reservations "
                  "(reservation_id,deployment_id,role,task_id,root_task_id,amount,currency,state,price_card_id,"
                  "purpose,synthetic,created_at,updated_at) VALUES "
                  "('hold','deployment','trader',NULL,NULL,'2','EUR','UNCERTAIN','price','call',1,?,?)", (now, now))
    prior.execute("INSERT INTO order_intents VALUES ('intent','p','client','UNKNOWN','BTC/EUR','{}',?,?)", (now, now))
    prior.execute("INSERT INTO native_fee_reservations VALUES ('fee','p','intent','BTC','0.1','0.1','held',?,?)",
                  (digest, now))
    prior.close()

    database = Database(path)
    before = {
        table: [dict(row) for row in database.execute(f"SELECT * FROM {table}")]
        for table in ("budget_reservations", "native_fee_reservations", "protected_runtime_instances")
    }
    origin = json.dumps(before["budget_reservations"][0], sort_keys=True)
    database.execute("INSERT INTO protected_financial_budget_origins VALUES "
                     "('origin','hold','ORIGIN','deployment',?,'instance','p',?,?,?)", (digest, origin, digest, now))
    with pytest.raises(sqlite3.IntegrityError):
        database.execute("INSERT INTO protected_financial_budget_origins VALUES "
                         "('duplicate','hold','ORIGIN','deployment',?,'instance','p',?,?,?)",
                         (digest, origin, digest, now))
    database.execute("INSERT INTO protected_financial_budget_origins VALUES "
                     "('receipt','hold','RECEIPT','deployment',?,'instance','p','{}',?,?)", (digest, digest, now))
    for operation in ("UPDATE protected_financial_budget_origins SET origin_json='{}'",
                      "DELETE FROM protected_financial_budget_origins"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            database.execute(operation)
    assert "0016" in migrate.applied_versions(database.connection)
    database.close()

    reopened = Database(path)
    retained = reopened.execute(
        "SELECT origin_json FROM protected_financial_budget_origins WHERE event_kind='ORIGIN'"
    ).fetchone()[0]
    assert json.loads(retained)["amount"] == "2"
    assert json.loads(retained)["state"] == "UNCERTAIN"
    for table, rows in before.items():
        assert [dict(row) for row in reopened.execute(f"SELECT * FROM {table}")] == rows
    assert migrate.apply_migrations(reopened.connection, now) == []
    reopened.close()
