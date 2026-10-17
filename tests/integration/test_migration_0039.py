"""Migration 0039 adds the Leader's news-round records (T38): each round and the requests it routed.

The rules are tested at the SQL level, so they hold whatever code writes: records are kept, a round's identity and
what it was handed never change, a round finishes once, a routed request is written with its round before the round
finishes and never changes, and each kind of request goes only where it may. Synthetic rows only.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from trade_graph.adapters.persistence.leader_news_schema import STATEMENTS as LEADER_NEWS_STATEMENTS
from trade_graph.adapters.persistence.leader_news_schema import TABLES, VERSION, migration
from trade_graph.adapters.persistence.migrate import STATEMENTS, apply_migrations

ROOT = Path(__file__).resolve().parents[2]
START = "2026-10-01T00:00:00.000000Z"
LATER = "2026-10-16T08:00:00.000000Z"
DAY = "2026-10-16T00:00:00.000000Z"
SHA = "a" * 64
PORTFOLIO = ("retained", "paper", "USD", "experiment", "active", None, START)
_ROUND = """INSERT INTO leader_news_rounds (round_id, portfolio_id, task_id, wake_id, trading_day_start, status,
    run_id, brief_json, brief_sha256, reply_json, output_json, reason, started_at, finished_at)
    VALUES (?, 'retained', ?, 'w-1', ?, ?, NULL, '{"wake_id": "w-1"}', ?, NULL, ?, NULL, ?, ?)"""
_REQUEST = """INSERT INTO leader_round_requests (request_id, round_id, portfolio_id, kind, department, asked_department,
    position, trigger_source_ref, document_json, created_at) VALUES (?, ?, 'retained', ?, ?, ?, ?, ?, '{}', ?)"""


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


def _round(connection, round_id="r-1", *, task="t-1", status="started") -> None:
    finished = None if status == "started" else LATER
    output = None if status == "started" else '{"wake_outcome": {}}'
    connection.execute(_ROUND, (round_id, task, DAY, status, SHA, output, LATER, finished))


def _request(connection, request_id="q-1", *, round_id="r-1", kind="deep_dive", department="research",
             asked=None, position=0) -> None:
    connection.execute(_REQUEST, (request_id, round_id, kind, department, asked or department, position,
                                  f"wake-request:w-1:{position}:{request_id}", LATER))


def test_0039_is_registered_once_in_order_with_exactly_this_schema() -> None:
    assert migration() == (VERSION, LEADER_NEWS_STATEMENTS) == ("0039", LEADER_NEWS_STATEMENTS)
    assert _versions().count(VERSION) == 1
    assert dict(STATEMENTS)[VERSION] == LEADER_NEWS_STATEMENTS
    assert _versions() == sorted(_versions())
    assert "0035" in _versions()[:_versions().index(VERSION)]


def test_0039_upgrades_an_earlier_database_once_without_rewriting_rows(tmp_path) -> None:
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


def test_no_record_is_ever_deleted_and_requests_never_change(upgraded) -> None:
    _round(upgraded)
    _request(upgraded)
    for table in TABLES:
        with pytest.raises(sqlite3.IntegrityError, match="kept"):
            upgraded.execute(f"DELETE FROM {table}")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute("UPDATE leader_round_requests SET department = 'trader'")


def test_a_round_keeps_its_identity_and_finishes_once(upgraded) -> None:
    _round(upgraded)
    for change in ("task_id = 't-2'", "wake_id = 'w-2'", "brief_json = '{}'", "started_at = 'x'",
                   "trading_day_start = 'x'", "portfolio_id = 'other'"):
        with pytest.raises(sqlite3.IntegrityError, match="identity"):
            upgraded.execute(f"UPDATE leader_news_rounds SET {change}")
    with pytest.raises(sqlite3.IntegrityError):  # a finished round has its output and finish time
        upgraded.execute("UPDATE leader_news_rounds SET status = 'completed'")
    upgraded.execute("""UPDATE leader_news_rounds SET status = 'completed', finished_at = ?, run_id = 'run-1',
        output_json = '{"wake_outcome": {}}'""", (LATER,))
    with pytest.raises(sqlite3.IntegrityError, match="finishes once"):
        upgraded.execute("UPDATE leader_news_rounds SET status = 'failed'")
    with pytest.raises(sqlite3.IntegrityError, match="finishes once"):
        upgraded.execute("UPDATE leader_news_rounds SET output_json = '{}'")
    with pytest.raises(sqlite3.IntegrityError):  # one round per wake task
        _round(upgraded, "r-2")
    with pytest.raises(sqlite3.IntegrityError):
        _round(upgraded, "r-3", task="t-3", status="approved")


def test_requests_are_written_with_their_open_round_and_go_only_where_they_may(upgraded) -> None:
    _round(upgraded)
    _request(upgraded, "q-1", kind="deep_dive", department="research", position=0)
    _request(upgraded, "q-2", kind="trader_note", department="trader", position=1)
    _request(upgraded, "q-3", kind="strategy_idea", department="research", asked="optimisation", position=2)
    for kind, department, asked in (("trader_note", "research", None), ("deep_dive", "trader", None),
                                    ("strategy_idea", "trader", None), ("deep_dive", "leader", None),
                                    ("trader_note", "trader", "research")):
        with pytest.raises(sqlite3.IntegrityError):
            _request(upgraded, f"bad-{kind}-{department}", kind=kind, department=department, asked=asked,
                     position=5)
    with pytest.raises(sqlite3.IntegrityError):  # one request per position of the round's wake outcome
        _request(upgraded, "q-4", position=0)
    with pytest.raises(sqlite3.IntegrityError):  # sixteen wake requests at most in one answer
        _request(upgraded, "q-5", position=16)
    upgraded.execute("""UPDATE leader_news_rounds SET status = 'completed', finished_at = ?,
        output_json = '{"wake_outcome": {}}'""", (LATER,))
    with pytest.raises(sqlite3.IntegrityError, match="before it finishes"):
        _request(upgraded, "q-6", position=3)
    with pytest.raises(sqlite3.IntegrityError):  # a request belongs to a recorded round
        _request(upgraded, "q-7", round_id="missing", position=3)


def test_alembic_revision_follows_the_previous_version_and_refuses_downgrade() -> None:
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    previous = _versions()[_versions().index(VERSION) - 1]
    assert scripts.get_heads() == [STATEMENTS[-1][0]]
    assert scripts.get_revision(VERSION).down_revision == previous
    spec = importlib.util.spec_from_file_location("migration_0039", ROOT / "migrations" / "versions" /
                                                  "0039_leader_news_rounds.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == (VERSION, previous)
    with pytest.raises(RuntimeError, match="not automatic"):
        module.downgrade()
