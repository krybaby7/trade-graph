"""Migration 0026 adds the T44 news alarm log without rewriting retained rows (W1-A reservation)."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from trade_graph.adapters.persistence.migrate import STATEMENTS, apply_migrations
from trade_graph.adapters.persistence.news_alarm_schema import NEWS_ALARM_STATEMENTS, TABLES, migration

ROOT = Path(__file__).resolve().parents[2]
START = "2026-10-01T00:00:00.000000Z"
LATER = "2026-10-16T12:00:00.000000Z"
PORTFOLIO = ("retained", "paper", "EUR", "experiment", "active", None, START)


def _database_at_0025(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    for version, statements in STATEMENTS:
        if version == "0026":
            break
        for statement in statements:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations VALUES (?,?)", (version, START))
    connection.execute("INSERT INTO portfolios VALUES (?,?,?,?,?,?,?)", PORTFOLIO)
    connection.execute("""INSERT INTO budget_reservations (reservation_id, deployment_id, role, task_id, root_task_id,
        amount, currency, state, price_card_id, purpose, synthetic, created_at, updated_at)
        VALUES ('r', 'deployment', 'trader', NULL, NULL, '0.01', 'EUR', 'COMMITTED', 'card', 'trader', 0, ?, ?)""",
                       (START, START))
    connection.commit()
    return connection


def _retained(connection: sqlite3.Connection) -> tuple:
    return (connection.execute("SELECT * FROM portfolios").fetchall(),
            connection.execute("SELECT * FROM budget_reservations").fetchall(),
            connection.execute("SELECT version, applied_at FROM schema_migrations ORDER BY version").fetchall())


def test_0026_is_registered_once_right_after_0025():
    versions = [version for version, _ in STATEMENTS]
    assert versions.count("0026") == 1
    assert versions[versions.index("0026") - 1] == "0025"
    assert dict(STATEMENTS)["0026"] == NEWS_ALARM_STATEMENTS
    assert migration() == ("0026", NEWS_ALARM_STATEMENTS)


def test_0026_upgrades_a_0025_database_once_without_rewriting_rows(tmp_path):
    connection = _database_at_0025(tmp_path / "upgrade.sqlite")
    assert connection.execute("SELECT name FROM sqlite_master WHERE name='news_headlines'").fetchone() is None
    before = _retained(connection)
    ran = apply_migrations(connection, LATER)
    assert ran[0] == "0026"
    portfolios, reservations, versions = _retained(connection)
    assert (portfolios, reservations) == before[:2]
    assert versions[: len(before[2]) + 1] == [*before[2], ("0026", LATER)]
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(TABLES) <= tables
    for table in TABLES:
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert apply_migrations(connection, LATER) == []
    connection.close()


def test_0026_tables_do_not_collide_with_the_candle_store():
    from trade_graph.adapters.persistence.market_data_schema import MARKET_DATA_MIGRATIONS

    text = " ".join(" ".join(statements) for _, statements in MARKET_DATA_MIGRATIONS)
    for table in TABLES:
        assert table.startswith("news_") and f"CREATE TABLE {table}" not in text


def test_alembic_revision_follows_0025_and_refuses_downgrade():
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == [STATEMENTS[-1][0]]
    assert scripts.get_revision("0026").down_revision == "0025"
    spec = importlib.util.spec_from_file_location("migration_0026", ROOT / "migrations" / "versions" /
                                                  "0026_news_alarm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == ("0026", "0025")
    with pytest.raises(RuntimeError, match="not automatic"):
        module.downgrade()
