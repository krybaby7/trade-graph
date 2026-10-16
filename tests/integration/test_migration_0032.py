"""Migration 0032 adds the owner's AI spending records (T40): prepaid credit readings and testing alert amounts.

The store's rules are tested at the SQL level, so they hold whatever code writes to it: append-only, owner-set,
USD only, plain non-negative decimal amounts and a positive alert amount.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from trade_graph.adapters.persistence.ai_spending_schema import STATEMENTS as SPENDING_STATEMENTS
from trade_graph.adapters.persistence.ai_spending_schema import TABLE, VERSION, migration
from trade_graph.adapters.persistence.migrate import STATEMENTS, apply_migrations

ROOT = Path(__file__).resolve().parents[2]
START = "2026-10-01T00:00:00.000000Z"
LATER = "2026-10-16T00:00:00.000000Z"
PORTFOLIO = ("retained", "paper", "USD", "experiment", "active", None, START)
_RECORD = f"""INSERT INTO {TABLE} (record_id, service, kind, amount_usd, observed_at, set_by, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)"""


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
    return connection


@pytest.fixture
def upgraded(tmp_path):
    connection = _database_before(tmp_path / "state.sqlite")
    apply_migrations(connection, LATER)
    yield connection
    connection.close()


def test_0032_is_registered_once_in_order_with_exactly_this_schema() -> None:
    assert migration() == (VERSION, SPENDING_STATEMENTS) == ("0032", SPENDING_STATEMENTS)
    assert _versions().count(VERSION) == 1
    assert dict(STATEMENTS)[VERSION] == SPENDING_STATEMENTS
    assert _versions() == sorted(_versions())
    assert "0029" in _versions()[:_versions().index(VERSION)]


def test_0032_upgrades_an_earlier_database_once_without_rewriting_rows(tmp_path) -> None:
    connection = _database_before(tmp_path / "upgrade.sqlite")
    assert connection.execute("SELECT name FROM sqlite_master WHERE name=?", (TABLE,)).fetchone() is None
    portfolios = connection.execute("SELECT * FROM portfolios").fetchall()
    receipts = connection.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()
    assert apply_migrations(connection, LATER) == [version for version in _versions() if version >= VERSION]
    assert connection.execute("SELECT * FROM portfolios").fetchall() == portfolios
    assert connection.execute("SELECT COUNT(*) FROM usage_receipts").fetchone() == receipts
    assert connection.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0] == 0
    assert apply_migrations(connection, LATER) == []
    connection.close()


def test_records_are_append_only(upgraded) -> None:
    upgraded.execute(_RECORD, ("r1", "perplexity", "prepaid_credit", "50", START, "owner", START))
    for statement in (f"UPDATE {TABLE} SET amount_usd='500'", f"DELETE FROM {TABLE}"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            upgraded.execute(statement)
    assert upgraded.execute(f"SELECT amount_usd FROM {TABLE}").fetchone()[0] == "50"


@pytest.mark.parametrize(("service", "kind", "amount", "set_by"), [
    ("openai", "prepaid_credit", "50", "owner"),          # only the two metered services
    ("perplexity", "monthly_limit", "50", "owner"),       # a limit is owner policy, not a note here
    ("perplexity", "prepaid_credit", "-5", "owner"),      # never negative
    ("perplexity", "prepaid_credit", "5e3", "owner"),     # plain decimals only
    ("perplexity", "prepaid_credit", "", "owner"),
    ("perplexity", "prepaid_credit", "1.2.3", "owner"),
    ("perplexity", "testing_alert", "0", "owner"),        # an alert at zero would always be on
    ("perplexity", "testing_alert", "0.00", "owner"),
    ("jev", "prepaid_credit", "10", "leader"),            # only the owner records these
])
def test_the_store_refuses_anything_but_owner_usd_amounts(upgraded, service, kind, amount, set_by) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_RECORD, ("bad", service, kind, amount, START, set_by, START))


def test_a_zero_credit_and_a_cent_alert_are_valid(upgraded) -> None:
    upgraded.execute(_RECORD, ("r1", "perplexity", "prepaid_credit", "0", START, "owner", START))
    upgraded.execute(_RECORD, ("r2", "jev", "testing_alert", "0.01", START, "owner", START))
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_RECORD, ("r1", "jev", "testing_alert", "1", START, "owner", START))  # ids are unique


def test_alembic_revision_follows_the_previous_version_and_refuses_downgrade() -> None:
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    previous = _versions()[_versions().index(VERSION) - 1]
    assert scripts.get_heads() == [STATEMENTS[-1][0]]
    assert scripts.get_revision(VERSION).down_revision == previous
    spec = importlib.util.spec_from_file_location("migration_0032", ROOT / "migrations" / "versions" /
                                                  "0032_ai_spending_owner_records.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == (VERSION, previous)
    with pytest.raises(RuntimeError, match="not automatic"):
        module.downgrade()
