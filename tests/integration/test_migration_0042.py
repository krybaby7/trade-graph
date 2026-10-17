"""Migration 0042 adds the news alarm's wake routes (T45): whom each would-wake verdict woke, or would have woken.

The rules hold at the SQL level, whatever code writes: a route is kept and never changed, a verdict reaches each
department of a portfolio once, a live route names its trigger and a shadow route none, and only the Leader and the
Trader are routed. Synthetic rows only.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from trade_graph.adapters.persistence.migrate import STATEMENTS, apply_migrations
from trade_graph.adapters.persistence.news_wake_schema import BECAUSE, TABLES, VERSION, migration
from trade_graph.adapters.persistence.news_wake_schema import STATEMENTS as NEWS_WAKE_STATEMENTS

ROOT = Path(__file__).resolve().parents[2]
START = "2026-10-01T00:00:00.000000Z"
LATER = "2026-10-16T12:00:00.000000Z"
SHA = "a" * 64
PORTFOLIO = ("retained", "paper", "USD", "experiment", "active", None, START)
_ROUTE = """INSERT INTO news_alarm_wake_routes (route_id, verdict_id, headline_id, portfolio_id, department, mode,
    because, rule_version, policy_revision, reason_json, note, symbol, source_ref, trigger_id, alarm_at, created_at)
    VALUES (?, 'v-1', 'h-1', 'retained', ?, ?, ?, 'news-wake-route-v1', 'owner-1', '{}', 'News alarm: synthetic',
            NULL, ?, ?, ?, ?)"""


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


def _news(connection) -> None:
    connection.execute("""INSERT INTO news_feed_polls (poll_id, feed_id, url, publisher, started_at, finished_at,
        outcome, evidence, items_seen, items_new, items_repeat, items_rejected) VALUES ('poll-1', 'kraken-blog',
        'https://blog.kraken.com/feed', 'Kraken blog', ?, ?, 'fetched', 'synthetic_fixture', 1, 1, 0, 0)""",
                       (LATER, LATER))
    connection.execute("""INSERT INTO news_headlines (headline_id, link, feed_id, publisher, title, published_at,
        received_at, item_sha256, evidence, poll_id) VALUES ('h-1', 'https://blog.kraken.com/synthetic', 'kraken-blog',
        'Kraken blog', 'Synthetic delisting', ?, ?, ?, 'synthetic_fixture', 'poll-1')""", (LATER, LATER, SHA))
    connection.execute("""INSERT INTO news_jev_attempts (attempt_id, headline_id, attempt_number, reservation_id,
        requested_model, question_set_version, request_sha256, request_json, watch_json, synthetic, started_at)
        VALUES ('a-1', 'h-1', NULL, NULL, 'jev-1.13.0', 'news-alarm-questions-v1', ?, '{}', '{}', 1, ?)""",
                       (SHA, LATER))
    connection.execute("""INSERT INTO news_jev_outcomes (attempt_id, status, usage_reported, finished_at)
        VALUES ('a-1', 'answered', 0, ?)""", (LATER,))
    connection.execute("""INSERT INTO news_alarm_verdicts (verdict_id, attempt_id, headline_id, rule_version,
        question_set_version, urgent_threshold, would_wake, wake_roles_json, subjects_json, held_subjects_json,
        event_type, holder_yes, created_at) VALUES ('v-1', 'a-1', 'h-1', 'news-alarm-rule-v1',
        'news-alarm-questions-v1', '0.5', 1, '["leader"]', '["SOL"]', '[]', 'delisting', '0.93', ?)""", (LATER,))


def _trigger(connection, trigger_id: str) -> None:
    connection.execute("""INSERT INTO wake_triggers (trigger_id, source_ref, portfolio_id, department, kind,
        occurred_at, document_json, status, carried, wake_id, created_at, updated_at)
        VALUES (?, ?, 'retained', 'leader', 'news_alarm', ?, '{}', 'pending', 0, NULL, ?, ?)""",
                       (trigger_id, f"software:news-alarm:{trigger_id}", LATER, LATER, LATER))


def _route(connection, route_id="r-1", *, department="leader", mode="shadow", because="alarm",
           trigger_id=None, source=None) -> None:
    connection.execute(_ROUTE, (route_id, department, mode, because, source or f"news-alarm:{route_id}",
                                trigger_id, LATER, LATER))


@pytest.fixture
def upgraded(tmp_path):
    connection = _database_before(tmp_path / "state.sqlite")
    apply_migrations(connection, LATER)
    _news(connection)
    yield connection
    connection.close()


def test_0042_is_registered_once_in_order_with_exactly_this_schema() -> None:
    assert migration() == (VERSION, NEWS_WAKE_STATEMENTS) == ("0042", NEWS_WAKE_STATEMENTS)
    assert _versions().count(VERSION) == 1
    assert dict(STATEMENTS)[VERSION] == NEWS_WAKE_STATEMENTS
    assert _versions() == sorted(_versions())
    assert {"0026", "0035", "0039"} <= set(_versions()[:_versions().index(VERSION)])
    assert BECAUSE == ("alarm", "held_at_check", "held_now", "kraken_while_holding")


def test_0042_upgrades_an_earlier_database_once_without_rewriting_rows(tmp_path) -> None:
    connection = _database_before(tmp_path / "upgrade.sqlite")
    _news(connection)
    for table in TABLES:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchone() is None
    verdicts = connection.execute("SELECT * FROM news_alarm_verdicts").fetchall()
    assert apply_migrations(connection, LATER) == [version for version in _versions() if version >= VERSION]
    assert connection.execute("SELECT * FROM news_alarm_verdicts").fetchall() == verdicts
    for table in TABLES:
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert apply_migrations(connection, LATER) == []
    connection.close()


def test_a_route_is_kept_and_never_changed(upgraded) -> None:
    _route(upgraded)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute("UPDATE news_alarm_wake_routes SET mode = 'live'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute("DELETE FROM news_alarm_wake_routes")


def test_a_verdict_reaches_each_department_of_a_portfolio_once(upgraded) -> None:
    _route(upgraded, "r-1")
    with pytest.raises(sqlite3.IntegrityError):
        _route(upgraded, "r-2")
    _route(upgraded, "r-3", department="trader", because="held_now")
    with pytest.raises(sqlite3.IntegrityError):  # one trigger identity per route
        _route(upgraded, "r-4", department="trader", source="news-alarm:r-3")


def test_live_routes_name_their_trigger_and_shadow_routes_none(upgraded) -> None:
    _trigger(upgraded, "t-1")
    with pytest.raises(sqlite3.IntegrityError):
        _route(upgraded, "r-1", mode="live")
    with pytest.raises(sqlite3.IntegrityError):
        _route(upgraded, "r-1", mode="shadow", trigger_id="t-1")
    with pytest.raises(sqlite3.IntegrityError):  # the trigger must exist
        _route(upgraded, "r-1", mode="live", trigger_id="t-missing")
    _route(upgraded, "r-1", mode="live", trigger_id="t-1")
    with pytest.raises(sqlite3.IntegrityError):  # and belongs to one route
        _route(upgraded, "r-2", department="trader", because="held_now", mode="live", trigger_id="t-1")


def test_only_the_leader_and_the_trader_are_routed_each_for_its_reason(upgraded) -> None:
    for department, because in (("research", "alarm"), ("optimisation", "alarm"), ("leader", "held_now"),
                                ("trader", "alarm"), ("trader", "because_the_headline_said_so")):
        with pytest.raises(sqlite3.IntegrityError):
            _route(upgraded, f"bad-{department}-{because}", department=department, because=because)
    with pytest.raises(sqlite3.IntegrityError):
        _route(upgraded, "bad-mode", mode="loud")
    with pytest.raises(sqlite3.IntegrityError):  # a route belongs to a recorded verdict
        upgraded.execute(_ROUTE.replace("'v-1'", "'v-missing'"),
                         ("r-9", "leader", "shadow", "alarm", "news-alarm:r-9", None, LATER, LATER))


def test_alembic_revision_follows_the_previous_version_and_refuses_downgrade() -> None:
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    previous = _versions()[_versions().index(VERSION) - 1]
    assert previous == "0039"
    assert scripts.get_heads() == [STATEMENTS[-1][0]]
    assert scripts.get_revision(VERSION).down_revision == previous
    spec = importlib.util.spec_from_file_location("migration_0042", ROOT / "migrations" / "versions" /
                                                  "0042_news_wakes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == (VERSION, previous)
    with pytest.raises(RuntimeError, match="not automatic"):
        module.downgrade()
