"""Upgrade a retained financial database without rewriting native authority."""

import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trade_graph.adapters.persistence import migrate
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.ledger import Ledger
from trade_graph.domain.clock import FrozenClock, utc_iso


def test_fee_holds_upgrade_preserves_original_authority_and_finance(tmp_path):
    path = tmp_path / "prior.sqlite"
    clock = FrozenClock(datetime(2026, 10, 4, tzinfo=UTC))
    now, digest = utc_iso(clock.now()), "a" * 64
    prior = sqlite3.connect(path, isolation_level=None)
    for version, statements in migrate.STATEMENTS:
        if version == "0015":
            break
        for statement in statements:
            prior.execute(statement)
        prior.execute("INSERT INTO schema_migrations VALUES (?, ?)", (version, now))
    prior.execute("INSERT INTO portfolios VALUES ('p','paper','EUR','synthetic','active',NULL,?)", (now,))
    prior.execute("INSERT INTO ledger_events VALUES ('deposit','p',1,'deposit',?,?,'opening')",
                  (json.dumps({"asset": "EUR", "amount": "100"}), now))
    prior.execute("INSERT INTO order_intents VALUES ('intent','p','client','UNKNOWN','BTC/EUR','{}',?,?)", (now, now))
    prior.execute("INSERT INTO position_reservations VALUES ('primary','p','intent','EUR','10','held',?)", (now,))
    prior.execute("INSERT INTO protected_financial_checkpoints VALUES ('checkpoint',?,'p',1,'{}',?,?)",
                  (digest, digest, now))
    prior.close()

    database = Database(path)
    retained = {
        table: [dict(row) for row in database.execute(f"SELECT * FROM {table}")]
        for table in ("ledger_events", "order_intents", "position_reservations", "protected_financial_checkpoints")
    }
    assert "0015" in migrate.applied_versions(database.connection)
    database.execute("INSERT INTO native_fee_reservations VALUES ('fee','p','intent','BTC','0.1','0.1','held',?,?)",
                     (digest, now))
    for field, changed in (("reservation_id", "other"), ("portfolio_id", "other"), ("intent_id", "other"),
                           ("asset", "ETH"), ("original_amount", "1"), ("plan_sha256", "b" * 64),
                           ("created_at", "changed")):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            database.execute(f"UPDATE native_fee_reservations SET {field}=?", (changed,))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        database.execute("DELETE FROM native_fee_reservations")
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "INSERT INTO native_fee_reservations VALUES ('duplicate','p','intent','BTC','1','1','held',?,?)",
            (digest, now),
        )
    database.execute("UPDATE native_fee_reservations SET current_amount='0.05' WHERE reservation_id='fee'")
    database.close()

    reopened = Database(path)
    fee = dict(reopened.execute("SELECT * FROM native_fee_reservations").fetchone())
    assert (fee["original_amount"], fee["current_amount"], fee["state"]) == ("0.1", "0.05", "held")
    reopened.execute("UPDATE native_fee_reservations SET current_amount='0', state='released'")
    assert Ledger(reopened, clock).books("p").cash_amount("EUR") == Decimal("100")
    for table, rows in retained.items():
        assert [dict(row) for row in reopened.execute(f"SELECT * FROM {table}")] == rows
    assert migrate.apply_migrations(reopened.connection, now) == []
    reopened.close()
