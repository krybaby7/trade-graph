"""Migration 0044 records the Trader's position actions and indexes the newest valuation mark (W8-B, C4).

The rules hold at the SQL level, whatever code writes: every record is kept and never changed; an action belongs to
one portfolio and coin and one request; a coin has at most one action still running; an action ends once and records
nothing after its end; a close's sell is a market sell of the entry's coin and an exit an action attached is a
day-plan exit of that entry. Existing databases upgrade, and the index serves the "newest mark of a coin" read.
Synthetic rows only.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from trade_graph.adapters.persistence.day_plan_action_schema import STATEMENTS as ACTION_STATEMENTS
from trade_graph.adapters.persistence.day_plan_action_schema import (
    STEP_KINDS,
    TABLES,
    TERMINAL_STEPS,
    VALUATION_MARK_INDEX,
    VERSION,
    migration,
)
from trade_graph.adapters.persistence.migrate import STATEMENTS, apply_migrations

ROOT = Path(__file__).resolve().parents[2]
START = "2026-10-01T00:00:00.000000Z"
LATER = "2026-10-16T10:00:00.000000Z"
SHA = "a" * 64
PLAN = "dp-" + "1" * 32
ACTION = "da-" + "2" * 32
OTHER = "da-" + "3" * 32
ENTRY = PLAN + ":e0"


def _versions() -> list[str]:
    return [version for version, _ in STATEMENTS]


def _database_before(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for version, statements in STATEMENTS:
        if version == VERSION:
            break
        for statement in statements:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations VALUES (?,?)", (version, START))
    connection.execute("INSERT INTO portfolios VALUES ('p-1','paper','EUR','experiment','active',NULL,?)", (START,))
    return connection


def _intent(connection, intent_id: str, *, side: str, order_type: str, symbol: str = "SOL/USD", extra=None) -> None:
    payload = {"side": side, "order_type": order_type, "symbol": symbol, **(extra or {})}
    connection.execute("INSERT INTO order_intents VALUES (?, 'p-1', ?, 'OPEN', ?, ?, ?, ?)",
                       (intent_id, "client-" + intent_id, symbol, json.dumps(payload), LATER, LATER))


def _plan(connection) -> None:
    connection.execute(
        """INSERT INTO day_plans (plan_id, portfolio_id, request_id, symbol, trading_day_start, trading_day_end,
           strategy_ref, strategy_sha256, practice_reports, supersedes_plan_id, policy_revision, mandate_revision,
           task_id, contract_version, document_json, document_sha256, installed_at)
           VALUES (?, 'p-1', 'plan-1', 'SOL/USD', '2026-10-16T00:00:00.000000Z', '2026-10-17T00:00:00.000000Z',
           'slow-trend-pullback@1', ?, '["run-1"]', NULL, 'policy-1', '1', NULL, 'day-plan-v1', '{}', ?, ?)""",
        (PLAN, SHA, SHA, LATER))
    _intent(connection, "entry-1", side="buy", order_type="limit")
    connection.execute(
        """INSERT INTO day_plan_entries (entry_id, plan_id, position, intent_id, decision_id, quantity, limit_price,
           stop_price, take_profit_price, expires_at, created_at)
           VALUES (?, ?, 0, 'entry-1', 'decision-1', '5', '99.5', '97', '104', '2026-10-17T00:00:00.000000Z', ?)""",
        (ENTRY, PLAN, LATER))
    _intent(connection, "exit-1", side="sell", order_type="stop", extra={"protective_exit": {"entry_id": ENTRY}})
    connection.execute(
        """INSERT INTO day_plan_exits (exit_id, entry_id, intent_id, quantity, stop_price, take_profit_price, cause,
           fill_trade_id, created_at) VALUES ('dx-1', ?, 'exit-1', '5', '99', NULL, 'sweep', NULL, ?)""",
        (ENTRY, LATER))


def _action(connection, action_id=ACTION, *, action="move_exit", stop="99", target=None, request="wake:w:SOL",
            symbol="SOL/USD") -> None:
    connection.execute(
        """INSERT INTO day_plan_actions (action_id, portfolio_id, request_id, symbol, action, stop_price,
           take_profit_price, task_id, policy_revision, mandate_revision, document_json, document_sha256,
           requested_at) VALUES (?, 'p-1', ?, ?, ?, ?, ?, NULL, 'policy-1', '1', '{}', ?, ?)""",
        (action_id, request, symbol, action, stop, target, SHA, LATER))


def _step(connection, kind: str, action_id=ACTION, reason=None) -> None:
    connection.execute("INSERT INTO day_plan_action_steps (action_id, kind, document_json, reason, created_at) "
                       "VALUES (?, ?, '{}', ?, ?)", (action_id, kind, reason, LATER))


def _order(connection, intent_id: str, role: str, action_id=ACTION, quantity="5") -> None:
    connection.execute("INSERT INTO day_plan_action_orders (action_id, entry_id, intent_id, role, quantity, "
                       "created_at) VALUES (?, ?, ?, ?, ?, ?)", (action_id, ENTRY, intent_id, role, quantity, LATER))


@pytest.fixture
def upgraded(tmp_path):
    connection = _database_before(tmp_path / "state.sqlite")
    apply_migrations(connection, LATER)
    _plan(connection)
    yield connection
    connection.close()


def test_0044_is_registered_once_in_order_with_exactly_this_schema() -> None:
    assert migration() == (VERSION, ACTION_STATEMENTS) == ("0044", ACTION_STATEMENTS)
    assert _versions().count(VERSION) == 1
    assert dict(STATEMENTS)[VERSION] == ACTION_STATEMENTS
    assert _versions() == sorted(_versions())
    assert {"0034", "0035", "0042"} <= set(_versions()[:_versions().index(VERSION)])
    assert set(TERMINAL_STEPS) < set(STEP_KINDS)


def test_0044_upgrades_an_earlier_database_once_without_rewriting_rows(tmp_path) -> None:
    connection = _database_before(tmp_path / "upgrade.sqlite")
    _plan(connection)
    connection.execute("INSERT INTO valuation_marks VALUES ('m-1','p-1','SOL','USD','100','mid',?,0,'synthetic')",
                       (LATER,))
    marks = [tuple(row) for row in connection.execute("SELECT * FROM valuation_marks")]
    exits = [tuple(row) for row in connection.execute("SELECT * FROM day_plan_exits")]
    for table in TABLES:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchone() is None
    assert apply_migrations(connection, LATER) == [version for version in _versions() if version >= VERSION]
    assert [tuple(row) for row in connection.execute("SELECT * FROM valuation_marks")] == marks
    assert [tuple(row) for row in connection.execute("SELECT * FROM day_plan_exits")] == exits
    for table in TABLES:
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert apply_migrations(connection, LATER) == []
    connection.close()


def test_the_newest_mark_of_a_coin_is_read_through_the_new_index(upgraded) -> None:
    index = upgraded.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                             (VALUATION_MARK_INDEX,)).fetchone()
    assert index is not None and "(portfolio_id, asset, observed_at)" in index["sql"]
    plan = " ".join(row[-1] for row in upgraded.execute(
        "EXPLAIN QUERY PLAN SELECT mark, quote_currency FROM valuation_marks WHERE portfolio_id = ? AND asset = ? "
        "AND observed_at <= ? ORDER BY observed_at DESC, rowid DESC LIMIT 1", ("p-1", "SOL", LATER)))
    assert VALUATION_MARK_INDEX in plan and "SCAN valuation_marks" not in plan


def test_every_action_record_is_kept_and_never_changed(upgraded) -> None:
    _action(upgraded)
    _step(upgraded, "cancelling")
    _order(upgraded, "exit-1", "moved_exit")
    for table in TABLES:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            upgraded.execute(f"UPDATE {table} SET created_at = created_at" if table != "day_plan_actions"
                             else "UPDATE day_plan_actions SET requested_at = requested_at")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            upgraded.execute(f"DELETE FROM {table}")


def test_an_action_is_one_request_of_one_portfolio_and_coin_with_terms_that_fit_it(upgraded) -> None:
    _action(upgraded)
    with pytest.raises(sqlite3.IntegrityError):  # one action per request
        _action(upgraded, OTHER)
    with pytest.raises(sqlite3.IntegrityError):  # a close names no stop
        _action(upgraded, OTHER, action="close", stop="99", request="other")
    with pytest.raises(sqlite3.IntegrityError):  # a move names its stop
        _action(upgraded, OTHER, action="move_exit", stop=None, request="other")
    with pytest.raises(sqlite3.IntegrityError):  # a close names no take-profit
        _action(upgraded, OTHER, action="close", stop=None, target="104", request="other")
    with pytest.raises(sqlite3.IntegrityError):
        _action(upgraded, OTHER, action="open_short", request="other")
    with pytest.raises(sqlite3.IntegrityError):
        _action(upgraded, OTHER, stop="9.9e1", request="other")  # exact plain decimals only
    with pytest.raises(sqlite3.IntegrityError):
        _action(upgraded, "da-not-hex", request="other")


def test_a_coin_has_at_most_one_running_action_and_an_action_ends_once(upgraded) -> None:
    _action(upgraded)
    with pytest.raises(sqlite3.IntegrityError, match="still running"):
        _action(upgraded, OTHER, request="second")
    _action(upgraded, OTHER, request="btc", symbol="BTC/USD")  # another coin is independent
    with pytest.raises(sqlite3.IntegrityError):
        _step(upgraded, "failed")  # a failure says why
    _step(upgraded, "done")
    with pytest.raises(sqlite3.IntegrityError, match="finished"):
        _step(upgraded, "exits_attached")
    with pytest.raises(sqlite3.IntegrityError):
        _step(upgraded, "refused", reason="late")
    _action(upgraded, "da-" + "4" * 32, request="third")  # the coin's earlier action has ended
    with pytest.raises(sqlite3.IntegrityError):
        _step(upgraded, "paused", action_id=OTHER)


def test_a_close_sells_the_entrys_coin_at_the_market_and_a_moved_exit_is_a_day_plan_exit(upgraded) -> None:
    _action(upgraded, OTHER, action="close", stop=None, request="close")
    _intent(upgraded, "sell-1", side="sell", order_type="market")
    _intent(upgraded, "sell-limit", side="sell", order_type="limit")
    _intent(upgraded, "sell-btc", side="sell", order_type="market", symbol="BTC/USD")
    _intent(upgraded, "buy-1", side="buy", order_type="market")
    for intent_id in ("sell-limit", "sell-btc", "buy-1", "exit-1"):
        with pytest.raises(sqlite3.IntegrityError, match="market"):
            _order(upgraded, intent_id, "sell", action_id=OTHER)
    _order(upgraded, "sell-1", "sell", action_id=OTHER)
    with pytest.raises(sqlite3.IntegrityError, match="day-plan exit"):  # a close attaches no moved exit
        _order(upgraded, "exit-1", "moved_exit", action_id=OTHER)
    _order(upgraded, "exit-1", "restored_exit", action_id=OTHER)  # but may restore the old one
    _step(upgraded, "done", action_id=OTHER)
    _action(upgraded)
    with pytest.raises(sqlite3.IntegrityError, match="day-plan exit"):
        _order(upgraded, "sell-1", "moved_exit")
    _order(upgraded, "exit-1", "moved_exit")
    with pytest.raises(sqlite3.IntegrityError):
        _order(upgraded, "exit-1", "moved_exit", quantity="-1")


def test_alembic_revision_follows_the_previous_version_and_refuses_downgrade() -> None:
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    previous = _versions()[_versions().index(VERSION) - 1]
    assert scripts.get_heads() == [STATEMENTS[-1][0]]
    assert scripts.get_revision(VERSION).down_revision == previous
    spec = importlib.util.spec_from_file_location("migration_0044", ROOT / "migrations" / "versions" /
                                                  "0044_day_plan_actions.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == (VERSION, previous)
    with pytest.raises(RuntimeError, match="not automatic"):
        module.downgrade()
