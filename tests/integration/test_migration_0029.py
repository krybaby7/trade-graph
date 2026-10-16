"""Migration 0029 adds the price-alarm store (T33): additive, append-only where it records history, one fire per arming.

The store's own rules are tested here at the SQL level, so they hold whatever code writes to it.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from trade_graph.adapters.persistence.migrate import STATEMENTS, apply_migrations
from trade_graph.adapters.persistence.price_alarm_schema import PRICE_ALARM_STATEMENTS, VERSION, migration

ROOT = Path(__file__).resolve().parents[2]
START = "2026-10-01T00:00:00.000000Z"
LATER = "2026-10-16T00:00:00.000000Z"
PORTFOLIO = ("retained", "paper", "USD", "experiment", "active", None, START)
CHECKIN = ("r1", "deployment", "q1", "c1", "plan", LATER, "baseline", "Morning review", None, "{}", START)
_ALARM = """INSERT INTO price_alarms (alarm_id, department, basis, kind, symbol, interval_minutes, source,
    condition_json, portfolio_id, day_plan_ref, task_id, request_id, note, contract_version, created_at)
    VALUES (?, 'trader', 'quote', 'quote_price', 'BTC/USD', NULL, NULL, '{}', NULL, NULL, NULL, ?, NULL,
            'price-alarm-v1', ?)"""
_EVENT = """INSERT INTO price_alarm_events (event_id, alarm_id, generation, action, at, task_id, document_json)
    VALUES (?, ?, ?, ?, ?, NULL, '{}')"""
_WATCH = """INSERT INTO price_alarm_watch (alarm_id, status, generation, armed_at, updated_at)
    VALUES (?, ?, ?, ?, ?)"""
_DELIVERY = """INSERT INTO alarm_deliveries (delivery_id, dedup_key, department, kind, alarm_id, generation,
    portfolio_id, document_json, created_at) VALUES (?, ?, 'trader', ?, ?, ?, NULL, '{}', ?)"""
_FEED = """INSERT INTO price_feed_events (event_id, series_key, feed, symbol, interval_minutes, source, status,
    detected_at, document_json) VALUES (?, 'quote:BTC/USD', 'quote', 'BTC/USD', NULL, NULL, ?, ?, '{}')"""


def _versions() -> list[str]:
    return [version for version, _ in STATEMENTS]


def _database_before_0029(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA foreign_keys=ON")
    for version, statements in STATEMENTS:
        if version == VERSION:
            break
        for statement in statements:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations VALUES (?,?)", (version, START))
    connection.execute("INSERT INTO portfolios VALUES (?,?,?,?,?,?,?)", PORTFOLIO)
    connection.execute("""INSERT INTO owner_checkins (record_id,deployment_id,request_id,checkin_id,action,planned_at,
        prompt_kind,title,note,document_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", CHECKIN)
    return connection


@pytest.fixture
def upgraded(tmp_path):
    connection = _database_before_0029(tmp_path / "state.sqlite")
    apply_migrations(connection, LATER)
    yield connection
    connection.close()


def _armed(connection: sqlite3.Connection, alarm_id: str = "a1") -> None:
    connection.execute(_ALARM, (alarm_id, None, START))
    connection.execute(_EVENT, (f"{alarm_id}-armed-1", alarm_id, 1, "armed", START))


def test_0029_is_registered_once_with_exactly_this_schema() -> None:
    assert migration() == (VERSION, PRICE_ALARM_STATEMENTS) == ("0029", PRICE_ALARM_STATEMENTS)
    assert _versions().count(VERSION) == 1
    assert dict(STATEMENTS)[VERSION] == PRICE_ALARM_STATEMENTS
    assert _versions() == sorted(_versions())  # versions apply in numeric order
    assert "0025" in _versions()[:_versions().index(VERSION)]


def test_0029_upgrades_an_earlier_database_once_without_rewriting_rows(tmp_path) -> None:
    connection = _database_before_0029(tmp_path / "upgrade.sqlite")
    assert connection.execute("SELECT name FROM sqlite_master WHERE name='price_alarms'").fetchone() is None
    retained = (connection.execute("SELECT * FROM portfolios").fetchall(),
                connection.execute("SELECT * FROM owner_checkins").fetchall())
    assert apply_migrations(connection, LATER) == [version for version in _versions() if version >= VERSION]
    assert (connection.execute("SELECT * FROM portfolios").fetchall(),
            connection.execute("SELECT * FROM owner_checkins").fetchall()) == retained
    for table in ("price_alarms", "price_alarm_events", "price_alarm_watch", "alarm_deliveries",
                  "alarm_wake_claims", "price_feed_events"):
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert apply_migrations(connection, LATER) == []
    connection.close()


def test_alarm_definitions_and_event_history_are_append_only(upgraded) -> None:
    _armed(upgraded)
    for statement in ("UPDATE price_alarms SET symbol='ETH/USD'", "DELETE FROM price_alarms",
                      "UPDATE price_alarm_events SET at='2026-10-17T00:00:00.000000Z'", "DELETE FROM price_alarm_events"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            upgraded.execute(statement)


def test_one_arming_fires_at_most_once_and_rearming_needs_a_fire(upgraded) -> None:
    _armed(upgraded)
    with pytest.raises(sqlite3.IntegrityError, match="re-armed only after it fired"):
        upgraded.execute(_EVENT, ("a1-armed-2", "a1", 2, "armed", LATER))
    upgraded.execute(_EVENT, ("a1-fired-1", "a1", 1, "fired", LATER))
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_EVENT, ("a1-fired-1b", "a1", 1, "fired", LATER))  # the same arming cannot fire twice
    with pytest.raises(sqlite3.IntegrityError, match="only an armed alarm can fire"):
        upgraded.execute(_EVENT, ("a1-fired-2", "a1", 2, "fired", LATER))
    with pytest.raises(sqlite3.IntegrityError, match="re-armed only after it fired"):
        upgraded.execute(_EVENT, ("a1-armed-3", "a1", 3, "armed", LATER))
    upgraded.execute(_EVENT, ("a1-armed-2", "a1", 2, "armed", LATER))
    upgraded.execute(_EVENT, ("a1-fired-2", "a1", 2, "fired", LATER))


def test_a_cancelled_alarm_never_fires_or_rearms_again(upgraded) -> None:
    _armed(upgraded)
    with pytest.raises(sqlite3.IntegrityError, match="current arming"):
        upgraded.execute(_EVENT, ("a1-cancel-2", "a1", 2, "cancelled", LATER))
    upgraded.execute(_EVENT, ("a1-cancel", "a1", 1, "cancelled", LATER))
    with pytest.raises(sqlite3.IntegrityError, match="only an armed alarm can fire"):
        upgraded.execute(_EVENT, ("a1-fired-1", "a1", 1, "fired", LATER))
    with pytest.raises(sqlite3.IntegrityError, match="re-armed only after it fired"):
        upgraded.execute(_EVENT, ("a1-armed-2", "a1", 2, "armed", LATER))
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_EVENT, ("a1-cancel-again", "a1", 1, "cancelled", LATER))


def test_the_watch_state_must_follow_the_event_log(upgraded) -> None:
    _armed(upgraded)
    with pytest.raises(sqlite3.IntegrityError, match="follow the alarm events"):
        upgraded.execute(_WATCH, ("a1", "spent", 1, START, START))
    upgraded.execute(_WATCH, ("a1", "armed", 1, START, START))
    upgraded.execute(_EVENT, ("a1-fired-1", "a1", 1, "fired", LATER))
    with pytest.raises(sqlite3.IntegrityError, match="follow the alarm events"):
        upgraded.execute("UPDATE price_alarm_watch SET evaluated_through=? WHERE alarm_id='a1'", (LATER,))
    upgraded.execute("UPDATE price_alarm_watch SET status='spent', fired_at=? WHERE alarm_id='a1'", (LATER,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute("DELETE FROM price_alarm_watch")


def test_a_delivery_moves_pending_claimed_delivered_and_never_back_from_delivered(upgraded) -> None:
    _armed(upgraded)
    with pytest.raises(sqlite3.IntegrityError, match="needs its fire"):
        upgraded.execute(_DELIVERY, ("d1", "fire:a1:1", "alarm_fired", "a1", 1, LATER))
    upgraded.execute(_EVENT, ("a1-fired-1", "a1", 1, "fired", LATER))
    upgraded.execute(_DELIVERY, ("d1", "fire:a1:1", "alarm_fired", "a1", 1, LATER))
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_DELIVERY, ("d2", "fire:a1:1-again", "alarm_fired", "a1", 1, LATER))
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_DELIVERY, ("d3", "feed:x", "feed_lost", "a1", 1, LATER))  # a notice names no alarm
    upgraded.execute("INSERT INTO alarm_wake_claims VALUES ('w1', 'trader', 'claimed', 1, ?, NULL)", (LATER,))
    with pytest.raises(sqlite3.IntegrityError, match="delivery status"):
        upgraded.execute("UPDATE alarm_deliveries SET status='delivered', wake_id='w1'")
    upgraded.execute("UPDATE alarm_deliveries SET status='claimed', wake_id='w1'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        upgraded.execute("UPDATE alarm_deliveries SET document_json='{\"changed\":1}'")
    upgraded.execute("UPDATE alarm_deliveries SET status='delivered'")
    with pytest.raises(sqlite3.IntegrityError, match="delivery status"):
        upgraded.execute("UPDATE alarm_deliveries SET status='pending', wake_id=NULL")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute("DELETE FROM alarm_deliveries")


def test_a_wake_claim_closes_once(upgraded) -> None:
    upgraded.execute("INSERT INTO alarm_wake_claims VALUES ('w1', 'trader', 'claimed', 0, ?, NULL)", (LATER,))
    upgraded.execute("UPDATE alarm_wake_claims SET status='acknowledged', closed_at=?", (LATER,))
    with pytest.raises(sqlite3.IntegrityError, match="closes once"):
        upgraded.execute("UPDATE alarm_wake_claims SET status='released'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute("DELETE FROM alarm_wake_claims")


def test_feed_events_alternate_lost_and_restored(upgraded) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="alternate"):
        upgraded.execute(_FEED, ("f0", "restored", START))
    upgraded.execute(_FEED, ("f1", "lost", START))
    with pytest.raises(sqlite3.IntegrityError, match="alternate"):
        upgraded.execute(_FEED, ("f2", "lost", LATER))
    upgraded.execute(_FEED, ("f2", "restored", LATER))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute("UPDATE price_feed_events SET status='lost'")


def test_alembic_revision_follows_the_previous_version_and_refuses_downgrade() -> None:
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    previous = _versions()[_versions().index(VERSION) - 1]
    assert len(scripts.get_heads()) == 1
    assert scripts.get_revision(VERSION).down_revision == previous
    spec = importlib.util.spec_from_file_location("migration_0029", ROOT / "migrations" / "versions" /
                                                  "0029_price_alarms.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == (VERSION, previous)
    with pytest.raises(RuntimeError, match="not automatic"):
        module.downgrade()
