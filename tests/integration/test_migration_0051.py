"""Migration 0051 adds the strategy lab's queue and the ``lab_results`` trigger kind (W11-B, C11; D290-D294).

SQLite cannot change a CHECK constraint in place, so 0051 rebuilds ``wake_triggers`` with ``lab_results`` in its kind
list, and ``news_alarm_wake_routes`` with it (its rows name their trigger): every row is copied back with its
sequence, and every rule of 0035 and 0042 holds again afterwards. The lab's batches and requests are kept, never
deleted, and move along their paths only. Synthetic rows only.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from trade_graph.adapters.persistence import strategy_lab_schema as lab_schema
from trade_graph.adapters.persistence import wake_schema
from trade_graph.adapters.persistence.migrate import STATEMENTS, apply_migrations
from trade_graph.adapters.persistence.strategy_lab_schema import STATEMENTS as LAB_STATEMENTS
from trade_graph.adapters.persistence.strategy_lab_schema import TABLES, VERSION, migration
from trade_graph.contracts import strategy_lab

ROOT = Path(__file__).resolve().parents[2]
START = "2026-10-01T00:00:00.000000Z"
LATER = "2026-10-16T12:00:00.000000Z"
SHA = "a" * 64
PORTFOLIO = ("retained", "paper", "USD", "experiment", "active", None, START)
# The 0035 statements as merged (wave 5): 0051 changes the kind list by a new migration, never by editing 0035.
STATEMENTS_0035_SHA256 = "4159fd2989b08be3dfe4f9254fd5b24754f7673b9a5c85af7e92ee05ad6f703e"


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


def _trigger(connection, trigger_id: str, kind: str = "news_alarm", *, status: str = "pending",
             wake_id: str | None = None) -> None:
    connection.execute("""INSERT INTO wake_triggers (trigger_id, source_ref, portfolio_id, department, kind,
        occurred_at, document_json, status, carried, wake_id, created_at, updated_at)
        VALUES (?, ?, 'retained', 'leader', ?, ?, '{"synthetic": true}', ?, 0, ?, ?, ?)""",
                       (trigger_id, f"src:{trigger_id}", kind, LATER, status, wake_id, LATER, LATER))


def _route(connection, route_id: str, trigger_id: str | None, *, department: str = "leader") -> None:
    because = "alarm" if department == "leader" else "held_now"
    connection.execute("""INSERT INTO news_alarm_wake_routes (route_id, verdict_id, headline_id, portfolio_id,
        department, mode, because, rule_version, policy_revision, reason_json, note, symbol, source_ref, trigger_id,
        alarm_at, created_at) VALUES (?, 'v-1', 'h-1', 'retained', ?, ?, ?, 'news-wake-route-v1',
        'owner-1', '{}', 'News alarm: synthetic', NULL, ?, ?, ?, ?)""",
                       (route_id, department, "live" if trigger_id else "shadow", because, f"news-alarm:{route_id}",
                        trigger_id, LATER, LATER))


def _batch(connection, batch_id: str = "b-1", *, requests: int = 2, finished: str | None = None) -> None:
    connection.execute("""INSERT INTO strategy_lab_batches (batch_id, portfolio_id, department, wake_id, task_id,
        requests, created_at, finished_at, trigger_id) VALUES (?, 'retained', 'trader', 'w-1', 't-1', ?, ?, ?, NULL)""",
                       (batch_id, requests, LATER, finished))


def _request(connection, request_id: str = "r-1", *, batch_id: str = "b-1", position: int = 0,
             kind: str = "tune", status: str = "queued", reason: str | None = None, result: str | None = None,
             finished: str | None = None) -> None:
    connection.execute("""INSERT INTO strategy_lab_requests (request_id, batch_id, position, portfolio_id, kind,
        family, document_json, status, attempts, reason, result_json, seconds, created_at, started_at, finished_at)
        VALUES (?, ?, ?, 'retained', ?, 'slow-trend-pullback', '{"kind": "tune"}', ?, 0, ?, ?, NULL, ?, NULL, ?)""",
                       (request_id, batch_id, position, kind, status, reason, result, LATER, finished))


@pytest.fixture
def upgraded(tmp_path):
    connection = _database_before(tmp_path / "state.sqlite")
    apply_migrations(connection, LATER)
    yield connection
    connection.close()


def test_0051_is_registered_once_in_order_with_exactly_this_schema() -> None:
    assert migration() == (VERSION, LAB_STATEMENTS) == ("0051", LAB_STATEMENTS)
    assert _versions().count(VERSION) == 1
    assert dict(STATEMENTS)[VERSION] == LAB_STATEMENTS
    assert _versions() == sorted(_versions())
    assert {"0035", "0042", "0044"} <= set(_versions()[:_versions().index(VERSION)])


def test_0035_itself_is_unchanged_and_the_kind_list_is_frozen_per_migration() -> None:
    """0035 keeps its own list; the contract's list now equals 0051's (``test_wake_contracts`` checks the latter)."""
    text = json.dumps(wake_schema.STATEMENTS).encode()
    assert "lab_results" not in json.dumps(wake_schema.STATEMENTS)
    assert hashlib.sha256(text).hexdigest() == STATEMENTS_0035_SHA256
    assert set(wake_schema.TRIGGER_KINDS) - set(wake_schema.TRIGGER_KINDS_0035) == {"lab_results"}
    assert strategy_lab.LAB_REQUEST_KINDS == lab_schema.KINDS
    assert strategy_lab.LAB_REQUEST_STATUSES == lab_schema.STATUSES


def test_0051_upgrades_an_earlier_database_keeping_every_trigger_and_route_with_its_sequence(tmp_path) -> None:
    connection = _database_before(tmp_path / "upgrade.sqlite")
    _news(connection)
    for number in range(1, 6):
        _trigger(connection, f"t-{number}", "alarm_fired" if number % 2 else "news_alarm")
    _route(connection, "r-live", "t-2")
    _route(connection, "r-shadow", None, department="trader")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _trigger(connection, "t-lab", "lab_results")  # not a kind before 0051
    triggers = [tuple(row) for row in connection.execute("SELECT * FROM wake_triggers ORDER BY sequence")]
    routes = [tuple(row) for row in connection.execute("SELECT * FROM news_alarm_wake_routes ORDER BY sequence")]
    for table in TABLES:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchone() is None
    assert apply_migrations(connection, LATER) == [version for version in _versions() if version >= VERSION]
    assert [tuple(row) for row in connection.execute("SELECT * FROM wake_triggers ORDER BY sequence")] == triggers
    after = [tuple(row) for row in connection.execute("SELECT * FROM news_alarm_wake_routes ORDER BY sequence")]
    assert after == routes
    assert connection.execute("SELECT name FROM sqlite_master WHERE name LIKE '%0051%'").fetchall() == []
    _trigger(connection, "t-lab", "lab_results")
    assert connection.execute("SELECT sequence FROM wake_triggers WHERE trigger_id = 't-lab'").fetchone()[0] == 6
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    for table in TABLES:
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert apply_migrations(connection, LATER) == []
    connection.close()


def test_the_rebuilt_trigger_table_keeps_every_rule_of_0035(upgraded) -> None:
    _trigger(upgraded, "t-1", "lab_results")
    with pytest.raises(sqlite3.IntegrityError, match="kept"):
        upgraded.execute("DELETE FROM wake_triggers")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        upgraded.execute("UPDATE wake_triggers SET kind = 'alarm_fired'")
    with pytest.raises(sqlite3.IntegrityError, match="path"):
        upgraded.execute("UPDATE wake_triggers SET status = 'delivered'")
    with pytest.raises(sqlite3.IntegrityError, match="names the one wake"):
        upgraded.execute("UPDATE wake_triggers SET status = 'claimed'")
    with pytest.raises(sqlite3.IntegrityError):
        _trigger(upgraded, "t-2", "anything_else")
    with pytest.raises(sqlite3.IntegrityError):
        _trigger(upgraded, "t-1", "lab_results")  # one trigger per id and per source
    indexes = {row[0] for row in upgraded.execute("SELECT name FROM sqlite_master WHERE tbl_name='wake_triggers' "
                                                  "AND type IN ('index', 'trigger') AND name NOT LIKE 'sqlite_%'")}
    assert indexes == {"wake_triggers_waiting", "wake_triggers_by_wake", "wake_triggers_no_delete",
                       "wake_triggers_identity", "wake_triggers_path", "wake_triggers_holder"}


def test_the_rebuilt_routes_keep_their_trigger_reference_and_stay_append_only(upgraded) -> None:
    _news(upgraded)
    _trigger(upgraded, "t-1")
    _route(upgraded, "r-1", "t-1")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        _route(upgraded, "r-2", "t-missing", department="trader")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute("UPDATE news_alarm_wake_routes SET note = 'changed'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute("DELETE FROM news_alarm_wake_routes")


def test_lab_records_are_kept_and_their_identity_never_changes(upgraded) -> None:
    _batch(upgraded)
    _request(upgraded)
    for table in TABLES:
        with pytest.raises(sqlite3.IntegrityError, match="kept"):
            upgraded.execute(f"DELETE FROM {table}")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        upgraded.execute("UPDATE strategy_lab_requests SET document_json = '{}'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        upgraded.execute("UPDATE strategy_lab_batches SET requests = 3")
    with pytest.raises(sqlite3.IntegrityError):
        _request(upgraded, "r-2", position=0)  # one request per position of a batch
    with pytest.raises(sqlite3.IntegrityError):
        _request(upgraded, "r-3", batch_id="b-missing", position=0)
    with pytest.raises(sqlite3.IntegrityError):
        _request(upgraded, "r-4", position=1, kind="optimise")
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute("""INSERT INTO strategy_lab_batches (batch_id, portfolio_id, department, requests, created_at)
            VALUES ('b-leader', 'retained', 'leader', 1, ?)""", (LATER,))  # the desk asks the lab


def test_a_request_moves_along_its_path_and_an_ended_one_is_final(upgraded) -> None:
    _batch(upgraded)
    _request(upgraded)
    with pytest.raises(sqlite3.IntegrityError, match="path"):
        upgraded.execute("UPDATE strategy_lab_requests SET status = 'done', result_json = '{}', finished_at = ?",
                         (LATER,))  # done only after running
    upgraded.execute("UPDATE strategy_lab_requests SET status = 'running', attempts = 1, started_at = ?", (LATER,))
    upgraded.execute("UPDATE strategy_lab_requests SET status = 'running', attempts = 2")  # re-run after a restart
    with pytest.raises(sqlite3.IntegrityError, match="attempts"):
        upgraded.execute("UPDATE strategy_lab_requests SET attempts = 1")
    upgraded.execute("UPDATE strategy_lab_requests SET status = 'queued'")  # a clean stop puts it back
    upgraded.execute("UPDATE strategy_lab_requests SET status = 'running', attempts = 3")
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute("UPDATE strategy_lab_requests SET status = 'refused', finished_at = ?", (LATER,))  # why?
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute("UPDATE strategy_lab_requests SET status = 'done', finished_at = ?", (LATER,))  # no result
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute("UPDATE strategy_lab_requests SET status = 'done', result_json = '{}'")  # no end time
    upgraded.execute("UPDATE strategy_lab_requests SET status = 'done', result_json = '{\"runs\": []}', "
                     "seconds = '1.25', finished_at = ?", (LATER,))
    with pytest.raises(sqlite3.IntegrityError, match="final"):
        upgraded.execute("UPDATE strategy_lab_requests SET status = 'queued', finished_at = NULL, result_json = NULL")
    with pytest.raises(sqlite3.IntegrityError):
        _request(upgraded, "r-2", position=1, status="refused", finished=LATER)  # refused names its reason
    _request(upgraded, "r-2", position=1, status="refused", reason="not JSON", finished=LATER)  # refused at once


def test_a_batch_finishes_once_and_is_announced_once_after_it_finished(upgraded) -> None:
    _batch(upgraded)
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute("UPDATE strategy_lab_batches SET trigger_id = 't-1'")  # not finished yet
    upgraded.execute("UPDATE strategy_lab_batches SET finished_at = ?", (LATER,))
    with pytest.raises(sqlite3.IntegrityError, match="once"):
        upgraded.execute("UPDATE strategy_lab_batches SET finished_at = ?", (START,))
    upgraded.execute("UPDATE strategy_lab_batches SET trigger_id = 't-1'")
    with pytest.raises(sqlite3.IntegrityError, match="once"):
        upgraded.execute("UPDATE strategy_lab_batches SET trigger_id = 't-2'")


def test_alembic_revision_follows_the_previous_version_and_refuses_downgrade() -> None:
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    previous = _versions()[_versions().index(VERSION) - 1]
    assert scripts.get_heads() == [STATEMENTS[-1][0]]
    assert scripts.get_revision(VERSION).down_revision == previous
    spec = importlib.util.spec_from_file_location("migration_0051", ROOT / "migrations" / "versions" /
                                                  "0051_strategy_lab.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == (VERSION, previous)
    with pytest.raises(RuntimeError, match="not automatic"):
        module.downgrade()
