"""Migration 0034 adds the Trader day plans (T34): plans, their resting entries, the protective exits attached to
fills, and plan events.

The rules are tested at the SQL level, so they hold whatever code writes: append-only, one plan per request, one chain
of plans per coin, entries that are limit buys of the plan's coin, exits that are protective sells of the entry's
coin, positive plain decimals. Synthetic rows only.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from trade_graph.adapters.persistence.day_plan_schema import STATEMENTS as PLAN_STATEMENTS
from trade_graph.adapters.persistence.day_plan_schema import TABLES, VERSION, migration
from trade_graph.adapters.persistence.migrate import STATEMENTS, apply_migrations

ROOT = Path(__file__).resolve().parents[2]
START = "2026-10-01T00:00:00.000000Z"
LATER = "2026-10-16T08:00:00.000000Z"
DAY = "2026-10-16T00:00:00.000000Z"
NEXT = "2026-10-17T00:00:00.000000Z"
PORTFOLIO = ("retained", "paper", "USD", "experiment", "active", None, START)
PLAN_A = "dp-" + "a" * 32
PLAN_B = "dp-" + "b" * 32
_PLAN = """INSERT INTO day_plans (plan_id, portfolio_id, request_id, symbol, trading_day_start, trading_day_end,
    strategy_ref, strategy_sha256, practice_reports, supersedes_plan_id, policy_revision, mandate_revision, task_id,
    contract_version, document_json, document_sha256, installed_at)
    VALUES (?, 'retained', ?, ?, ?, ?, 'slow-trend-pullback@1', ?, '["pr-1"]', ?, '1', '1', NULL, 'day-plan-v1', '{}',
            ?, ?)"""
_INTENT = """INSERT INTO order_intents (intent_id, portfolio_id, client_order_id, state, symbol, payload_json,
    created_at, updated_at) VALUES (?, 'retained', ?, 'SUBMISSION_PENDING', ?, ?, ?, ?)"""
_ENTRY = """INSERT INTO day_plan_entries (entry_id, plan_id, position, intent_id, decision_id, quantity, limit_price,
    stop_price, take_profit_price, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
_EXIT = """INSERT INTO day_plan_exits (exit_id, entry_id, intent_id, quantity, stop_price, take_profit_price, cause,
    fill_trade_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"""
_EVENT = """INSERT INTO day_plan_events (plan_id, entry_id, kind, reason, document_json, created_at)
    VALUES (?, ?, ?, ?, '{}', ?)"""


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


def _plan(connection, plan_id=PLAN_A, *, request="r-1", symbol="SOL/USD", supersedes=None) -> None:
    connection.execute(_PLAN, (plan_id, request, symbol, DAY, NEXT, "c" * 64, supersedes, "d" * 64, LATER))


def _intent(connection, intent_id, *, side="buy", order_type="limit", symbol="SOL/USD", entry_id=None) -> None:
    payload = {"side": side, "order_type": order_type, "symbol": symbol}
    if entry_id is not None:
        payload["protective_exit"] = {"entry_id": entry_id}
    connection.execute(_INTENT, (intent_id, "client-" + intent_id, symbol, json.dumps(payload), LATER, LATER))


def _entry(connection, *, entry_id=PLAN_A + ":e0", plan_id=PLAN_A, intent_id="buy-1", quantity="5") -> None:
    connection.execute(_ENTRY, (entry_id, plan_id, 0, intent_id, plan_id + ":d0", quantity, "99.5", "97", "104",
                                NEXT, LATER))


def test_0034_is_registered_once_in_order_with_exactly_this_schema() -> None:
    assert migration() == (VERSION, PLAN_STATEMENTS) == ("0034", PLAN_STATEMENTS)
    assert _versions().count(VERSION) == 1
    assert dict(STATEMENTS)[VERSION] == PLAN_STATEMENTS
    assert _versions() == sorted(_versions())
    assert "0033" in _versions()[:_versions().index(VERSION)]


def test_0034_upgrades_an_earlier_database_once_without_rewriting_rows(tmp_path) -> None:
    connection = _database_before(tmp_path / "upgrade.sqlite")
    for table in TABLES:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchone() is None
    portfolios = connection.execute("SELECT * FROM portfolios").fetchall()
    assert apply_migrations(connection, LATER) == [version for version in _versions() if version >= VERSION]
    assert connection.execute("SELECT * FROM portfolios").fetchall() == portfolios
    for table in TABLES:
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert apply_migrations(connection, LATER) == []
    connection.close()


def test_every_record_is_append_only(upgraded) -> None:
    _plan(upgraded)
    _intent(upgraded, "buy-1")
    _entry(upgraded)
    _intent(upgraded, "stop-1", side="sell", order_type="stop", entry_id=PLAN_A + ":e0")
    upgraded.execute(_EXIT, ("dx-1", PLAN_A + ":e0", "stop-1", "5", "97", "104", "fill", "trade-1", LATER))
    upgraded.execute(_EVENT, (PLAN_A, None, "installed", None, LATER))
    for table in TABLES:
        for statement in (f"UPDATE {table} SET created_at = created_at" if table != "day_plans"
                          else f"UPDATE {table} SET installed_at = installed_at", f"DELETE FROM {table}"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                upgraded.execute(statement)


def test_a_request_installs_one_plan_and_each_coin_has_one_chain(upgraded) -> None:
    _plan(upgraded)
    with pytest.raises(sqlite3.IntegrityError):
        _plan(upgraded, PLAN_B, request="r-1")  # the same request again
    with pytest.raises(sqlite3.IntegrityError, match="latest plan"):
        _plan(upgraded, PLAN_B, request="r-2")  # a second plan for the coin must replace the first
    _plan(upgraded, PLAN_B, request="r-2", supersedes=PLAN_A)
    with pytest.raises(sqlite3.IntegrityError):
        _plan(upgraded, "dp-" + "c" * 32, request="r-3", supersedes=PLAN_A)  # a plan is replaced once
    _plan(upgraded, "dp-" + "d" * 32, request="r-4", symbol="BTC/USD")  # another coin has its own chain
    with pytest.raises(sqlite3.IntegrityError, match="same portfolio and coin"):
        _plan(upgraded, "dp-" + "e" * 32, request="r-5", symbol="ETH/USD", supersedes=PLAN_B)


@pytest.mark.parametrize("intent", [
    {"side": "sell", "order_type": "limit"},
    {"side": "buy", "order_type": "market"},
    {"side": "buy", "order_type": "limit", "symbol": "BTC/USD"},
])
def test_an_entry_is_a_resting_limit_buy_of_the_plans_coin(upgraded, intent) -> None:
    _plan(upgraded)
    _intent(upgraded, "other", **intent)
    with pytest.raises(sqlite3.IntegrityError, match="limit buy"):
        _entry(upgraded, intent_id="other")


@pytest.mark.parametrize("quantity", ["0", "-1", "1e3", "5.", ""])
def test_entry_amounts_are_positive_plain_decimals(upgraded, quantity) -> None:
    _plan(upgraded)
    _intent(upgraded, "buy-1")
    with pytest.raises(sqlite3.IntegrityError):
        _entry(upgraded, quantity=quantity)


def test_an_exit_is_a_protective_sell_stop_of_the_entrys_coin(upgraded) -> None:
    _plan(upgraded)
    _intent(upgraded, "buy-1")
    _entry(upgraded)
    _intent(upgraded, "plain-stop", side="sell", order_type="stop")
    with pytest.raises(sqlite3.IntegrityError, match="protective sell"):
        upgraded.execute(_EXIT, ("dx-1", PLAN_A + ":e0", "plain-stop", "5", "97", None, "sweep", None, LATER))
    _intent(upgraded, "stop-1", side="sell", order_type="stop", entry_id=PLAN_A + ":e0")
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_EXIT, ("dx-1", PLAN_A + ":e0", "stop-1", "5", "97", None, "fill", None, LATER))
    upgraded.execute(_EXIT, ("dx-1", PLAN_A + ":e0", "stop-1", "5", "97", None, "sweep", None, LATER))
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_EXIT, ("dx-2", PLAN_A + ":e0", "stop-1", "5", "97", None, "sweep", None, LATER))


def test_events_name_their_kind_and_happen_once_where_they_must(upgraded) -> None:
    _plan(upgraded)
    _intent(upgraded, "buy-1")
    _entry(upgraded)
    upgraded.execute(_EVENT, (PLAN_A, None, "installed", None, LATER))
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_EVENT, (PLAN_A, None, "installed", None, LATER))
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_EVENT, (PLAN_A, None, "approved", None, LATER))
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_EVENT, (PLAN_A, PLAN_A + ":e0", "entry_cancelled", "because", LATER))
    upgraded.execute(_EVENT, (PLAN_A, PLAN_A + ":e0", "entry_cancelled", "pause", LATER))
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_EVENT, (PLAN_A, PLAN_A + ":e0", "entry_cancelled", "expired", LATER))
    upgraded.execute(_EVENT, (PLAN_A, PLAN_A + ":e0", "protection_deferred", "no quote", LATER))
    upgraded.execute(_EVENT, (PLAN_A, PLAN_A + ":e0", "protection_deferred", "no quote", LATER))


def test_alembic_revision_follows_the_previous_version_and_refuses_downgrade() -> None:
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    previous = _versions()[_versions().index(VERSION) - 1]
    assert scripts.get_heads() == [STATEMENTS[-1][0]]
    assert scripts.get_revision(VERSION).down_revision == previous
    spec = importlib.util.spec_from_file_location("migration_0034", ROOT / "migrations" / "versions" /
                                                  "0034_day_plans.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == (VERSION, previous)
    with pytest.raises(RuntimeError, match="not automatic"):
        module.downgrade()
