"""Migration 0028 adds the append-only web research evidence store (T36) without rewriting rows."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from trade_graph.adapters.persistence.migrate import STATEMENTS, apply_migrations
from trade_graph.adapters.persistence.web_research_schema import STATEMENTS as WEB_RESEARCH_STATEMENTS

ROOT = Path(__file__).resolve().parents[2]
START = "2026-01-01T00:00:00.000000Z"
LATER = "2026-10-16T00:00:00.000000Z"
TABLES = ("web_research_runs", "web_research_steps", "web_search_calls", "web_search_outcomes",
          "web_search_sources", "web_research_outcomes")


def _database_before_0028(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    for version, statements in STATEMENTS:
        if version >= "0028":
            break
        for statement in statements:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations VALUES (?,?)", (version, START))
    connection.execute("INSERT INTO portfolios VALUES ('retained','paper','EUR','experiment','active',NULL,?)",
                       (START,))
    connection.execute("""INSERT INTO budget_reservations (reservation_id, deployment_id, role, task_id, root_task_id,
        amount, currency, state, price_card_id, purpose, synthetic, created_at, updated_at)
        VALUES ('held', 'deployment', 'research', 't', 't', '0.5', 'EUR', 'UNCERTAIN', 'card', 'research', 0, ?, ?)""",
                       (START, START))
    connection.execute("""INSERT INTO findings VALUES ('old-finding', 'retained', '{}', 'hash', ?, ?, ?)""",
                       (START, LATER, START))
    connection.commit()
    return connection


def _retained(connection: sqlite3.Connection) -> tuple:
    return tuple(connection.execute(f"SELECT * FROM {table}").fetchall()
                 for table in ("portfolios", "budget_reservations", "findings"))


def test_0028_is_registered_once_after_0025_with_its_schema():
    versions = [version for version, _ in STATEMENTS]
    assert versions.count("0028") == 1 and versions == sorted(versions)
    assert dict(STATEMENTS)["0028"] == WEB_RESEARCH_STATEMENTS
    assert versions.index("0028") > versions.index("0025")


def test_0028_upgrades_an_existing_database_once_without_rewriting_rows(tmp_path):
    connection = _database_before_0028(tmp_path / "upgrade.sqlite")
    before = _retained(connection)
    pending = [version for version, _ in STATEMENTS if version >= "0028"]
    assert apply_migrations(connection, LATER) == pending
    assert _retained(connection) == before
    for table in TABLES:
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert apply_migrations(connection, LATER) == []
    assert _retained(connection) == before
    connection.close()


def test_every_web_research_table_is_append_only(tmp_path):
    connection = _database_before_0028(tmp_path / "triggers.sqlite")
    apply_migrations(connection, LATER)
    connection.execute("""INSERT INTO web_research_runs VALUES ('run', 'deployment', 'retained', 'research', 't', 't',
        'v', 'perplexity', 'anthropic/claude-sonnet-5-5', 'agent-card', 'search-card', 'null', 'question?', ?)""",
                       (LATER,))
    connection.execute("""INSERT INTO web_search_calls VALUES ('call', 'run', 1, 'held', '{"query":"q"}', 0, ?)""",
                       (LATER,))
    connection.execute("""INSERT INTO web_search_outcomes VALUES ('call', 'results', NULL, 'id', 1, 'receipt', ?)""",
                       (LATER,))
    connection.execute("""INSERT INTO web_search_sources VALUES ('web:call:1', 'call', 'run', 1, 'https://a.example.org',
        ?, '{}', ?)""", ("0" * 64, LATER))
    connection.execute("""INSERT INTO web_research_steps VALUES ('held', 'run', 1, 'output', NULL, 'id', 'm', '0.01',
        ?)""", (LATER,))
    connection.execute("INSERT INTO web_research_outcomes VALUES ('run', 'completed', '{}', ?)", (LATER,))
    for table in TABLES:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(f"UPDATE {table} SET run_id = run_id" if table != "web_search_outcomes"
                               else f"UPDATE {table} SET call_id = call_id")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(f"DELETE FROM {table}")
    connection.close()


def test_a_failed_search_cannot_carry_a_receipt_and_sources_need_a_full_hash(tmp_path):
    connection = _database_before_0028(tmp_path / "checks.sqlite")
    apply_migrations(connection, LATER)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO web_search_outcomes VALUES ('c', 'failed', 'credentials', NULL, 0, 'r', ?)",
                           (LATER,))
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO web_search_sources VALUES ('s', 'c', 'run', 1, 'https://a.example.org', "
                           "'short', '{}', ?)", (LATER,))
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO web_research_runs VALUES ('r', 'd', 'p', 'trader', 't', 't', 'v', 'perplexity',"
                           " 'm', 'a', 's', 'null', 'q', ?)", (LATER,))
    connection.close()


def test_alembic_revision_0028_refuses_downgrade():
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    assert len(scripts.get_heads()) == 1
    assert "0028" in [revision.revision for revision in scripts.walk_revisions()]
    spec = importlib.util.spec_from_file_location("migration_0028", ROOT / "migrations" / "versions" /
                                                  "0028_web_research_evidence.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0028" and module.down_revision < "0028"
    with pytest.raises(RuntimeError, match="not automatic"):
        module.downgrade()
