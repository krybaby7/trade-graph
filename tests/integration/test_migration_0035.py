"""Migration 0035 adds the event engine's records (T35): triggers, wakes, check-ins, focus selections, the daily
cycle's steps and the source cursors.

The rules are tested at the SQL level, so they hold whatever code writes: records are kept (never deleted), what a
wake was handed never changes, a trigger moves only along its path and is never dropped, one open engine wake per
department, one check-in per wake, cursors only move forward. Synthetic rows only.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from trade_graph.adapters.persistence.migrate import STATEMENTS, apply_migrations
from trade_graph.adapters.persistence.wake_schema import STATEMENTS as WAKE_STATEMENTS
from trade_graph.adapters.persistence.wake_schema import TABLES, VERSION, migration

ROOT = Path(__file__).resolve().parents[2]
START = "2026-10-01T00:00:00.000000Z"
LATER = "2026-10-16T08:00:00.000000Z"
DAY = "2026-10-16T00:00:00.000000Z"
NEXT = "2026-10-17T00:00:00.000000Z"
PORTFOLIO = ("retained", "paper", "USD", "experiment", "active", None, START)
_TRIGGER = """INSERT INTO wake_triggers (trigger_id, source_ref, portfolio_id, department, kind, occurred_at,
    document_json, status, carried, wake_id, created_at, updated_at)
    VALUES (?, ?, 'retained', ?, ?, ?, '{}', ?, ?, ?, ?, ?)"""
_WAKE = """INSERT INTO wakes (wake_id, portfolio_id, department, origin, purpose, trading_day_start,
    trading_day_end, task_id, context_json, allowance_json, status, outcome_json, close_reason, created_at, closed_at)
    VALUES (?, 'retained', ?, ?, ?, ?, ?, ?, '{}', '{}', ?, NULL, NULL, ?, NULL)"""
_CHECK_IN = """INSERT INTO department_check_ins (check_in_id, portfolio_id, department, due_at, basis, note, wake_id,
    task_id, created_at) VALUES (?, 'retained', ?, ?, ?, NULL, ?, NULL, ?)"""
_FOCUS = """INSERT INTO focus_selections (focus_id, portfolio_id, trading_day_start, trading_day_end, revision,
    scan_id, document_json, document_sha256, wake_id, task_id, recorded_at)
    VALUES (?, 'retained', ?, ?, ?, NULL, '{}', ?, ?, NULL, ?)"""
_STEP = """INSERT INTO day_cycle_steps (portfolio_id, trading_day_start, trading_day_end, step, status, detail_json,
    created_at, updated_at) VALUES ('retained', ?, ?, ?, ?, '{}', ?, ?)"""


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


def _trigger(connection, trigger_id="t-1", *, source=None, kind="alarm_fired", department="trader",
             status="pending", carried=0, wake=None) -> None:
    connection.execute(_TRIGGER, (trigger_id, source or "src:" + trigger_id, department, kind, LATER, status,
                                  carried, wake, LATER, LATER))


def _wake(connection, wake_id="w-1", *, department="trader", origin="engine", purpose="event", status="queued",
          task=None) -> None:
    connection.execute(_WAKE, (wake_id, department, origin, purpose, DAY, NEXT, task, status, LATER))


def test_0035_is_registered_once_in_order_with_exactly_this_schema() -> None:
    assert migration() == (VERSION, WAKE_STATEMENTS) == ("0035", WAKE_STATEMENTS)
    assert _versions().count(VERSION) == 1
    assert dict(STATEMENTS)[VERSION] == WAKE_STATEMENTS
    assert _versions() == sorted(_versions())
    assert "0034" in _versions()[:_versions().index(VERSION)]


def test_0035_upgrades_an_earlier_database_once_without_rewriting_rows(tmp_path) -> None:
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


def test_no_record_is_ever_deleted(upgraded) -> None:
    _trigger(upgraded)
    _wake(upgraded)
    upgraded.execute(_CHECK_IN, ("c-1", "trader", NEXT, "day_start", "w-1", LATER))
    upgraded.execute(_FOCUS, ("f-1", DAY, NEXT, 1, "a" * 64, "w-1", LATER))
    upgraded.execute(_STEP, (DAY, NEXT, "day_start", "done", LATER, LATER))
    upgraded.execute("INSERT INTO wake_cursors VALUES ('fills', 3, ?)", (LATER,))
    for table in TABLES:
        with pytest.raises(sqlite3.IntegrityError, match="kept"):
            upgraded.execute(f"DELETE FROM {table}")


def test_check_ins_and_focus_selections_are_append_only(upgraded) -> None:
    upgraded.execute(_CHECK_IN, ("c-1", "trader", NEXT, "named", None, LATER))
    upgraded.execute(_FOCUS, ("f-1", DAY, NEXT, 1, "a" * 64, None, LATER))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute("UPDATE department_check_ins SET due_at = ?", (DAY,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute("UPDATE focus_selections SET revision = 2")
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_FOCUS, ("f-2", DAY, NEXT, 1, "b" * 64, None, LATER))  # one row per revision
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        upgraded.execute(_CHECK_IN, ("c-2", "trader", NEXT, "every_hour", None, LATER))


def test_a_wake_sets_at_most_one_check_in_and_focus(upgraded) -> None:
    upgraded.execute(_CHECK_IN, ("c-1", "trader", NEXT, "named", "w-1", LATER))
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_CHECK_IN, ("c-2", "trader", NEXT, "named", "w-1", LATER))
    upgraded.execute(_FOCUS, ("f-1", DAY, NEXT, 1, "a" * 64, "w-1", LATER))
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_FOCUS, ("f-2", DAY, NEXT, 2, "b" * 64, "w-1", LATER))


def test_a_trigger_keeps_its_identity_and_moves_only_along_its_path(upgraded) -> None:
    _trigger(upgraded)
    with pytest.raises(sqlite3.IntegrityError):
        _trigger(upgraded, "t-2", source="src:t-1")  # one trigger per source event
    with pytest.raises(sqlite3.IntegrityError, match="identity"):
        upgraded.execute("UPDATE wake_triggers SET kind = 'check_in'")
    with pytest.raises(sqlite3.IntegrityError, match="identity"):
        upgraded.execute("UPDATE wake_triggers SET document_json = '{\"x\":1}'")
    with pytest.raises(sqlite3.IntegrityError, match="path"):
        upgraded.execute("UPDATE wake_triggers SET status = 'delivered'")  # pending cannot skip its wake
    with pytest.raises(sqlite3.IntegrityError, match="wake"):
        upgraded.execute("UPDATE wake_triggers SET status = 'claimed'")  # a claim names its wake
    upgraded.execute("UPDATE wake_triggers SET status = 'claimed', wake_id = 'w-1'")
    with pytest.raises(sqlite3.IntegrityError, match="wake"):
        upgraded.execute("UPDATE wake_triggers SET status = 'delivered', wake_id = 'w-2'")
    upgraded.execute("UPDATE wake_triggers SET status = 'pending', wake_id = NULL, carried = 1")  # released
    upgraded.execute("UPDATE wake_triggers SET status = 'claimed', wake_id = 'w-2'")
    upgraded.execute("UPDATE wake_triggers SET status = 'delivered'")
    for status in ("pending", "claimed", "expired"):
        with pytest.raises(sqlite3.IntegrityError, match="path"):
            upgraded.execute("UPDATE wake_triggers SET status = ?", (status,))
    _trigger(upgraded, "t-3", kind="day_start")
    upgraded.execute("UPDATE wake_triggers SET status = 'expired' WHERE trigger_id = 't-3'")
    with pytest.raises(sqlite3.IntegrityError, match="path"):
        upgraded.execute("UPDATE wake_triggers SET status = 'pending' WHERE trigger_id = 't-3'")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _trigger(upgraded, "t-4", kind="every_four_hours")


def test_one_open_engine_wake_per_department_and_what_it_was_handed_never_changes(upgraded) -> None:
    _wake(upgraded, "w-1", task="task-1")
    with pytest.raises(sqlite3.IntegrityError):
        _wake(upgraded, "w-2")  # the Trader already has an open wake: new triggers wait for the next one
    _wake(upgraded, "w-3", department="research")
    _wake(upgraded, "w-4", origin="task", purpose="task")  # a task made outside the engine is recorded too
    for column, value in (("context_json", '{"x":1}'), ("allowance_json", '{"x":1}'), ("purpose", "focus"),
                          ("department", "leader"), ("trading_day_start", NEXT), ("task_id", "task-2")):
        with pytest.raises(sqlite3.IntegrityError, match="identity"):
            upgraded.execute(f"UPDATE wakes SET {column} = ? WHERE wake_id = 'w-1'", (value,))
    upgraded.execute("UPDATE wakes SET status = 'applying' WHERE wake_id = 'w-1'")
    with pytest.raises(sqlite3.IntegrityError, match="path"):
        upgraded.execute("UPDATE wakes SET status = 'queued' WHERE wake_id = 'w-1'")
    with pytest.raises(sqlite3.IntegrityError, match="closed"):
        upgraded.execute("UPDATE wakes SET status = 'completed' WHERE wake_id = 'w-1'")  # needs closed_at
    upgraded.execute("UPDATE wakes SET status = 'completed', closed_at = ?, outcome_json = '{}' WHERE wake_id = 'w-1'",
                     (LATER,))
    _wake(upgraded, "w-2")  # closed: the next wake may open
    with pytest.raises(sqlite3.IntegrityError, match="closed wake"):
        upgraded.execute("UPDATE wakes SET outcome_json = '{\"x\":2}' WHERE wake_id = 'w-1'")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _wake(upgraded, "w-5", department="engineer")


def test_cycle_steps_finish_once_and_cursors_only_move_forward(upgraded) -> None:
    upgraded.execute(_STEP, (DAY, NEXT, "focus", "started", LATER, LATER))
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_STEP, (DAY, NEXT, "focus", "started", LATER, LATER))
    upgraded.execute("UPDATE day_cycle_steps SET status = 'done', detail_json = '{\"focus_id\":\"f\"}'")
    with pytest.raises(sqlite3.IntegrityError, match="path"):
        upgraded.execute("UPDATE day_cycle_steps SET status = 'failed'")
    with pytest.raises(sqlite3.IntegrityError, match="identity"):
        upgraded.execute("UPDATE day_cycle_steps SET step = 'scan'")
    upgraded.execute("INSERT INTO wake_cursors VALUES ('fills', 3, ?)", (LATER,))
    upgraded.execute("UPDATE wake_cursors SET position = 9")
    with pytest.raises(sqlite3.IntegrityError, match="forward"):
        upgraded.execute("UPDATE wake_cursors SET position = 2")


def test_alembic_revision_follows_the_previous_version_and_refuses_downgrade() -> None:
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    previous = _versions()[_versions().index(VERSION) - 1]
    assert scripts.get_heads() == [STATEMENTS[-1][0]]
    assert scripts.get_revision(VERSION).down_revision == previous
    spec = importlib.util.spec_from_file_location("migration_0035", ROOT / "migrations" / "versions" /
                                                  "0035_event_engine.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == (VERSION, previous)
    with pytest.raises(RuntimeError, match="not automatic"):
        module.downgrade()
