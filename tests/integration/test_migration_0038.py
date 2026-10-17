"""Migration 0038 adds the owner's Perplexity switch and Research's focus-round records (T37; D205-D209).

The rules hold at the SQL level, whatever code writes: every record is kept and never rewritten, only the owner
sets the switch, a department switched on always has a spend rule, one round per task and one outcome per round,
and an outcome's status says whether the turn answered. Synthetic rows only.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from trade_graph.adapters.persistence.focus_research_schema import (
    ANSWERED_STATUSES,
    ROUND_STATUSES,
    TABLES,
    VERSION,
    migration,
)
from trade_graph.adapters.persistence.focus_research_schema import STATEMENTS as FOCUS_STATEMENTS
from trade_graph.adapters.persistence.migrate import STATEMENTS, apply_migrations

ROOT = Path(__file__).resolve().parents[2]
START = "2026-10-01T00:00:00.000000Z"
LATER = "2026-10-16T08:00:00.000000Z"
DAY = "2026-10-16T00:00:00.000000Z"
NEXT = "2026-10-17T00:00:00.000000Z"
PORTFOLIO = ("retained", "paper", "USD", "experiment", "active", None, START)
_SWITCH = """INSERT INTO perplexity_department_settings (revision_id, previous_revision_id, research, leader,
    spend_mode, document_json, document_sha256, command, set_by, created_at) VALUES (?, ?, ?, ?, ?, '{}', ?,
    'perplexity-departments', ?, ?)"""
_ROUND = """INSERT INTO focus_research_rounds (round_id, portfolio_id, wake_id, task_id, purpose, trading_day_start,
    trading_day_end, scan_id, switch_revision_id, context_sha256, started_at)
    VALUES (?, 'retained', 'wake-1', ?, ?, ?, ?, 'scan-1', NULL, ?, ?)"""
_OUTCOME = """INSERT INTO focus_research_outcomes (round_id, status, answered, web_run_id, billing, focus_symbols_json,
    reason, output_json, output_sha256, completed_at) VALUES (?, ?, ?, NULL, 'none', NULL, NULL, '{}', ?, ?)"""


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


def _switch(connection, revision="s-1", *, previous=None, research=1, leader=1, spend="unlimited_testing",
            set_by="owner") -> None:
    connection.execute(_SWITCH, (revision, previous, research, leader, spend, "a" * 64, set_by, LATER))


def _round(connection, round_id="r-1", *, task="task-1", purpose="focus", end=NEXT) -> None:
    connection.execute(_ROUND, (round_id, task, purpose, DAY, end, "b" * 64, LATER))


def test_0038_is_registered_once_in_order_with_exactly_this_schema() -> None:
    assert migration() == (VERSION, FOCUS_STATEMENTS) == ("0038", FOCUS_STATEMENTS)
    assert _versions().count(VERSION) == 1
    assert dict(STATEMENTS)[VERSION] == FOCUS_STATEMENTS
    assert _versions() == sorted(_versions())
    assert "0035" in _versions()[:_versions().index(VERSION)]
    assert set(ANSWERED_STATUSES) < set(ROUND_STATUSES)


def test_0038_upgrades_an_earlier_database_once_without_rewriting_rows(tmp_path) -> None:
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


def test_every_record_is_kept_and_never_rewritten(upgraded) -> None:
    _switch(upgraded)
    _round(upgraded)
    upgraded.execute(_OUTCOME, ("r-1", "completed", 1, "c" * 64, LATER))
    for table in TABLES:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            upgraded.execute(f"DELETE FROM {table}")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute("UPDATE perplexity_department_settings SET research = 0")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute("UPDATE focus_research_rounds SET scan_id = 'scan-2'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute("UPDATE focus_research_outcomes SET status = 'failed'")


def test_only_the_owner_sets_the_switch_and_a_department_switched_on_has_a_spend_rule(upgraded) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _switch(upgraded, set_by="leader")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _switch(upgraded, spend=None)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _switch(upgraded, spend="free_for_all")
    with pytest.raises(sqlite3.IntegrityError):
        _switch(upgraded, previous="never-recorded")
    _switch(upgraded, "s-1")
    _switch(upgraded, "s-2", previous="s-1", research=0, leader=0, spend=None)  # off needs no spend rule
    with pytest.raises(sqlite3.IntegrityError):
        _switch(upgraded, "s-1")  # one row per revision


def test_one_round_per_task_and_one_outcome_per_round(upgraded) -> None:
    _round(upgraded, "r-1", task="task-1")
    with pytest.raises(sqlite3.IntegrityError):
        _round(upgraded, "r-2", task="task-1")  # a task never runs a second paid round
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _round(upgraded, "r-3", task="task-3", purpose="news")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _round(upgraded, "r-4", task="task-4", end=DAY)
    upgraded.execute(_OUTCOME, ("r-1", "failed", 0, "c" * 64, LATER))
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_OUTCOME, ("r-1", "completed", 1, "c" * 64, LATER))
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_OUTCOME, ("r-9", "completed", 1, "c" * 64, LATER))  # an outcome needs its round


def test_an_outcome_says_whether_the_turn_answered(upgraded) -> None:
    for number, status in enumerate(ROUND_STATUSES):
        _round(upgraded, f"r-{number}", task=f"task-{number}")
        wrong = 0 if status in ANSWERED_STATUSES else 1
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            upgraded.execute(_OUTCOME, (f"r-{number}", status, wrong, "c" * 64, LATER))
        upgraded.execute(_OUTCOME, (f"r-{number}", status, 1 - wrong, "c" * 64, LATER))
    _round(upgraded, "r-x", task="task-x")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        upgraded.execute(_OUTCOME, ("r-x", "hold", 1, "c" * 64, LATER))  # no status passes an error off as a hold


def test_alembic_revision_follows_the_previous_version_and_refuses_downgrade() -> None:
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    previous = _versions()[_versions().index(VERSION) - 1]
    assert scripts.get_heads() == [STATEMENTS[-1][0]]
    assert scripts.get_revision(VERSION).down_revision == previous
    spec = importlib.util.spec_from_file_location("migration_0038", ROOT / "migrations" / "versions" /
                                                  "0038_focus_research.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == (VERSION, previous)
    with pytest.raises(RuntimeError, match="not automatic"):
        module.downgrade()
