"""Migration 0033 adds the money-limit records (T32): day-start values, daily loss stops, the owner-policy audit and
riba posting flags.

The rules are tested at the SQL level, so they hold whatever code writes: append-only, one value and one stop per
portfolio and trading day, positive plain decimals, a stop inside its own day, owner-only audit rows that name a
stored policy revision. Synthetic values only.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from trade_graph.adapters.persistence.migrate import STATEMENTS, apply_migrations
from trade_graph.adapters.persistence.money_limit_schema import STATEMENTS as LIMIT_STATEMENTS
from trade_graph.adapters.persistence.money_limit_schema import TABLES, VERSION, migration

ROOT = Path(__file__).resolve().parents[2]
START = "2026-10-01T00:00:00.000000Z"
LATER = "2026-10-16T00:00:00.000000Z"
DAY = "2026-10-16T00:00:00.000000Z"
NEXT = "2026-10-17T00:00:00.000000Z"
PORTFOLIO = ("retained", "paper", "USD", "experiment", "active", None, START)
_VALUE = """INSERT INTO trading_day_values (portfolio_id, day_start, day_end, currency, value, valued_at, basis,
    policy_revision, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"""
_STOP = """INSERT INTO daily_loss_stops (portfolio_id, day_start, resumes_at, currency, day_start_value, value_at_stop,
    loss, limit_amount, limit_fraction, reached_at, policy_revision, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
_AUDIT = """INSERT INTO owner_policy_audit (revision_id, previous_revision_id, previous_sha256, revision_sha256,
    command, changes_json, set_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""
_FLAG = """INSERT INTO riba_posting_flags (portfolio_id, ledger_event_id, kind, reasons_json, flagged_at)
    VALUES (?, ?, ?, ?, ?)"""


def _versions() -> list[str]:
    return [version for version, _ in STATEMENTS]


def _database_before(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA foreign_keys=ON")
    for version, statements in STATEMENTS:
        if version == VERSION:
            break
        for statement in statements:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations VALUES (?,?)", (version, START))
    connection.execute("INSERT INTO portfolios VALUES (?,?,?,?,?,?,?)", PORTFOLIO)
    connection.execute("INSERT INTO owner_policy_revisions VALUES ('1', '{}', ?, ?)", ("a" * 64, START))
    return connection


@pytest.fixture
def upgraded(tmp_path):
    connection = _database_before(tmp_path / "state.sqlite")
    apply_migrations(connection, LATER)
    yield connection
    connection.close()


def test_0033_is_registered_once_in_order_with_exactly_this_schema() -> None:
    assert migration() == (VERSION, LIMIT_STATEMENTS) == ("0033", LIMIT_STATEMENTS)
    assert _versions().count(VERSION) == 1
    assert dict(STATEMENTS)[VERSION] == LIMIT_STATEMENTS
    assert _versions() == sorted(_versions())
    assert "0032" in _versions()[:_versions().index(VERSION)]


def test_0033_upgrades_an_earlier_database_once_without_rewriting_rows(tmp_path) -> None:
    connection = _database_before(tmp_path / "upgrade.sqlite")
    for table in TABLES:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchone() is None
    portfolios = connection.execute("SELECT * FROM portfolios").fetchall()
    policies = connection.execute("SELECT * FROM owner_policy_revisions").fetchall()
    assert apply_migrations(connection, LATER) == [version for version in _versions() if version >= VERSION]
    assert connection.execute("SELECT * FROM portfolios").fetchall() == portfolios
    assert connection.execute("SELECT * FROM owner_policy_revisions").fetchall() == policies
    for table in TABLES:
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert apply_migrations(connection, LATER) == []
    connection.close()


def test_every_record_is_append_only(upgraded) -> None:
    upgraded.execute(_VALUE, ("retained", DAY, NEXT, "USD", "10000", DAY, "at_day_start", "1", LATER))
    upgraded.execute(_STOP, ("retained", DAY, NEXT, "USD", "10000", "9600", "400", "400", "0.04",
                             "2026-10-16T09:00:00.000000Z", "1", LATER))
    upgraded.execute(_AUDIT, ("1", None, None, "a" * 64, "money-limits", "{}", "owner", LATER))
    upgraded.execute(_FLAG, ("retained", "event-1", "deposit", '["interest"]', LATER))
    for table in TABLES:
        for statement in (f"UPDATE {table} SET created_at = created_at" if table != "riba_posting_flags"
                          else f"UPDATE {table} SET flagged_at = flagged_at", f"DELETE FROM {table}"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                upgraded.execute(statement)


def test_one_day_start_value_and_one_stop_per_portfolio_and_day(upgraded) -> None:
    upgraded.execute(_VALUE, ("retained", DAY, NEXT, "USD", "10000", DAY, "at_day_start", "1", LATER))
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_VALUE, ("retained", DAY, NEXT, "USD", "9000", DAY, "at_day_start", "1", LATER))
    stop = ("retained", DAY, NEXT, "USD", "10000", "9600", "400", "400", "0.04", "2026-10-16T09:00:00.000000Z",
            "1", LATER)
    upgraded.execute(_STOP, stop)
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_STOP, stop)
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_FLAG, ("retained", "event-1", "deposit", "[]", LATER))
        upgraded.execute(_FLAG, ("retained", "event-1", "deposit", "[]", LATER))


@pytest.mark.parametrize("row", [
    ("retained", DAY, NEXT, "USD", "0", DAY, "at_day_start", "1", LATER),            # a value must be positive
    ("retained", DAY, NEXT, "USD", "-1", DAY, "at_day_start", "1", LATER),
    ("retained", DAY, NEXT, "USD", "1e4", DAY, "at_day_start", "1", LATER),          # plain decimals only
    ("retained", DAY, NEXT, "USD", "10000", DAY, "guessed", "1", LATER),             # a named basis
    ("retained", DAY, NEXT, "USD", "10000", "2026-10-16T01:00:00.000000Z", "at_day_start", "1", LATER),
    ("retained", DAY, NEXT, "USD", "10000", DAY, "first_valuation_after_start", "1", LATER),
    ("retained", DAY, NEXT, "USD", "10000", NEXT, "first_valuation_after_start", "1", LATER),
    ("missing", DAY, NEXT, "USD", "10000", DAY, "at_day_start", "1", LATER),         # a known portfolio
])
def test_day_start_values_are_positive_plain_decimals_valued_inside_their_day(upgraded, row) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_VALUE, row)


@pytest.mark.parametrize("row", [
    ("retained", DAY, NEXT, "USD", "10000", "9600", "400", "400", "0.04", NEXT, "1", LATER),   # inside its day
    ("retained", DAY, NEXT, "USD", "10000", "9700", "300", "400", "0.04", "2026-10-16T09:00:00.000000Z", "1",
     LATER),                                                                                  # loss below limit
    ("retained", DAY, NEXT, "USD", "10000", "9600", "400", "400", "1", "2026-10-16T09:00:00.000000Z", "1", LATER),
])
def test_a_stop_is_recorded_only_inside_its_day_and_at_or_past_its_limit(upgraded, row) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_STOP, row)


@pytest.mark.parametrize("row", [
    ("1", None, None, "a" * 64, "money-limits", "{}", "leader", LATER),     # only the owner
    ("2", "1", "a" * 64, "b" * 64, "money-limits", "{}", "owner", LATER),  # a stored revision
    ("1", None, None, "short", "money-limits", "{}", "owner", LATER),
])
def test_the_policy_audit_is_owner_only_and_names_a_stored_revision(upgraded, row) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_AUDIT, row)


def test_alembic_revision_follows_the_previous_version_and_refuses_downgrade() -> None:
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    previous = _versions()[_versions().index(VERSION) - 1]
    assert scripts.get_heads() == [STATEMENTS[-1][0]]
    assert scripts.get_revision(VERSION).down_revision == previous
    spec = importlib.util.spec_from_file_location("migration_0033", ROOT / "migrations" / "versions" /
                                                  "0033_money_limits.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == (VERSION, previous)
    with pytest.raises(RuntimeError, match="not automatic"):
        module.downgrade()
