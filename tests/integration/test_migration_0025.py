"""Migration 0025 adds the owner check-in store without rewriting retained rows (spec §B.10, §E.1 WP4)."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from trade_graph.adapters.persistence.migrate import STATEMENTS, apply_migrations
from trade_graph.adapters.persistence.owner_checkin_schema import OWNER_CHECKIN_STATEMENTS

ROOT = Path(__file__).resolve().parents[2]
START = "2026-01-01T00:00:00.000000Z"
LATER = "2026-01-31T00:00:00.000000Z"
PORTFOLIO = ("retained", "paper", "EUR", "experiment", "active", None, START)
BILL = ("bill-record", "deployment", "bill-request", "expense", "opaque-bill-1", "ai-subscription", "subscription",
        START, LATER, START, "owner-evidence:opaque-bill-1", '{"retained":true}', START)


def _database_at_0024(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    for version, statements in STATEMENTS:
        if version == "0025":
            break
        for statement in statements:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations VALUES (?,?)", (version, START))
    connection.execute("INSERT INTO portfolios VALUES (?,?,?,?,?,?,?)", PORTFOLIO)
    connection.execute("""INSERT INTO owner_expense_evidence
        (record_id,deployment_id,request_id,record_type,bill_id,billing_scope,expense_kind,period_start,
         period_end,incurred_at,evidence_ref,document_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", BILL)
    connection.commit()
    return connection


def _retained(connection: sqlite3.Connection) -> tuple:
    return (connection.execute("SELECT * FROM portfolios").fetchall(),
            connection.execute("SELECT * FROM owner_expense_evidence").fetchall(),
            connection.execute("SELECT version, applied_at FROM schema_migrations ORDER BY version").fetchall())


def test_0025_is_registered_once_with_only_later_versions_after_it():
    versions = [version for version, _ in STATEMENTS]
    assert versions.count("0025") == 1 and dict(STATEMENTS)["0025"] == OWNER_CHECKIN_STATEMENTS
    assert versions == sorted(versions)  # later packages (0026+) only ever append newer versions


def test_0025_upgrades_a_0024_database_once_without_rewriting_rows(tmp_path):
    connection = _database_at_0024(tmp_path / "upgrade.sqlite")
    assert connection.execute("SELECT name FROM sqlite_master WHERE name='owner_checkins'").fetchone() is None
    before = _retained(connection)
    pending = [version for version, _ in STATEMENTS if version >= "0025"]
    assert pending[0] == "0025"
    assert apply_migrations(connection, LATER) == pending
    portfolios, bills, versions = _retained(connection)
    assert (portfolios, bills) == before[:2]
    assert versions == [*before[2], *((version, LATER) for version in pending)]
    assert connection.execute("SELECT COUNT(*) FROM owner_checkins").fetchone()[0] == 0
    assert apply_migrations(connection, LATER) == []
    assert _retained(connection) == (portfolios, bills, versions)
    connection.close()


def test_0025_store_is_append_only_after_upgrade(tmp_path):
    connection = _database_at_0024(tmp_path / "triggers.sqlite")
    apply_migrations(connection, LATER)
    insert = """INSERT INTO owner_checkins (record_id,deployment_id,request_id,checkin_id,action,planned_at,
        prompt_kind,title,note,document_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)"""
    connection.execute(insert, ("r1", "deployment", "q1", "c1", "plan", LATER, "baseline", "Morning review", None,
                                "{}", START))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute("UPDATE owner_checkins SET title='Changed'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute("DELETE FROM owner_checkins")
    with pytest.raises(sqlite3.IntegrityError, match="unknown check-in"):
        connection.execute(insert, ("r2", "deployment", "q2", "c-missing", "done", None, None, None, "ok", "{}", START))
    connection.close()


def test_alembic_revision_follows_0024_and_refuses_downgrade():
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    assert len(scripts.get_heads()) == 1  # one linear chain, whose head may be a later revision
    assert "0025" in [revision.revision for revision in scripts.walk_revisions()]
    assert scripts.get_revision("0025").down_revision == "0024"
    spec = importlib.util.spec_from_file_location("migration_0025", ROOT / "migrations" / "versions" /
                                                  "0025_owner_checkins.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == ("0025", "0024")
    with pytest.raises(RuntimeError, match="not automatic"):
        module.downgrade()
