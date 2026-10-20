"""Migration 0052 lets strategy ideas go to the Trading desk and adds its records (W12-A, C12; D295-D299).

SQLite cannot change a CHECK constraint in place, so 0052 rebuilds ``leader_round_requests`` with the desk in the list
of departments a strategy idea may go to: every row is copied back with its sequence, and every rule of 0039 holds
again afterwards. The desk's playbook revisions, stances and day-plan records are append-only; playbook revisions form
one chain per portfolio, and a day plan's record names its own plan, coin, playbook revision and stance. Synthetic
rows only.
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

from trade_graph.adapters.persistence import leader_news_schema
from trade_graph.adapters.persistence import trading_desk_schema as desk_schema
from trade_graph.adapters.persistence.migrate import STATEMENTS, apply_migrations
from trade_graph.adapters.persistence.trading_desk_schema import TABLES, VERSION, migration
from trade_graph.contracts import trading_desk

ROOT = Path(__file__).resolve().parents[2]
START = "2026-10-01T00:00:00.000000Z"
LATER = "2026-10-16T12:00:00.000000Z"
LATEST = "2026-10-16T13:00:00.000000Z"
DAY = "2026-10-16T00:00:00.000000Z"
SHA = "a" * 64
PORTFOLIO = ("retained", "paper", "USD", "experiment", "active", None, START)
OTHER = ("other", "paper", "USD", "experiment", "active", None, START)
_ROUND = """INSERT INTO leader_news_rounds (round_id, portfolio_id, task_id, wake_id, trading_day_start, status,
    run_id, brief_json, brief_sha256, reply_json, output_json, reason, started_at, finished_at)
    VALUES (?, 'retained', ?, 'w-1', ?, 'started', NULL, '{"wake_id": "w-1"}', ?, NULL, NULL, NULL, ?, NULL)"""
_REQUEST = """INSERT INTO leader_round_requests (request_id, round_id, portfolio_id, kind, department, asked_department,
    position, trigger_source_ref, document_json, created_at) VALUES (?, ?, 'retained', ?, ?, ?, ?, ?, ?, ?)"""


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
    connection.execute("INSERT INTO portfolios VALUES (?,?,?,?,?,?,?)", OTHER)
    return connection


@pytest.fixture
def upgraded(tmp_path):
    connection = _database_before(tmp_path / "state.sqlite")
    apply_migrations(connection, LATER)
    yield connection
    connection.close()


def _round(connection, round_id: str = "r-1", *, task: str = "t-1") -> None:
    connection.execute(_ROUND, (round_id, task, DAY, SHA, LATER))


def _finish(connection, round_id: str = "r-1") -> None:
    connection.execute("""UPDATE leader_news_rounds SET status = 'completed', finished_at = ?,
        output_json = '{"wake_outcome": {}}' WHERE round_id = ?""", (LATER, round_id))


def _request(connection, request_id: str, *, kind: str = "strategy_idea", department: str = "research",
             asked: str | None = None, position: int = 0, round_id: str = "r-1") -> None:
    document = json.dumps({"request_id": request_id, "kind": kind})
    connection.execute(_REQUEST, (request_id, round_id, kind, department, asked or department, position,
                                  f"wake-request:w-1:{position}:{request_id}", document, LATER))


def _revision(connection, revision_id: str, revision: int, previous: str | None, *, at: str = LATER,
              portfolio: str = "retained", set_by: str = "trader") -> None:
    connection.execute("""INSERT INTO desk_playbook_revisions (revision_id, portfolio_id, revision,
        previous_revision_id, set_by, wake_id, task_id, why, document_json, document_sha256, created_at)
        VALUES (?, ?, ?, ?, ?, 'w-1', 't-1', 'Synthetic: wait in falling markets.', '{"choices": []}', ?, ?)""",
                       (revision_id, portfolio, revision, previous, set_by, SHA, at))


def _label(connection, label_id: str = "mt-" + "0" * 32) -> str:
    connection.execute("""INSERT INTO market_type_labels (label_id, symbol, as_of, market_type, reason, candle_close,
        lag_bars, interval_minutes, source, rules_ref, rules_sha256, scan_id, evidence, document_json, document_sha256,
        recorded_at) VALUES (?, 'BTC/USD', ?, 'falling', NULL, ?, 0, 60, 'synthetic', 'market-type-rules@1', ?, NULL,
        'synthetic_fixture', '{}', ?, ?)""", (label_id, LATER, LATER, SHA, SHA, LATER))
    return label_id


def _stance(connection, stance_id: str = "s-1", *, symbol: str = "BTC/USD", stance: str = "wait",
            status: str = "current", label: str | None = "mt-" + "0" * 32, label_type: str | None = "falling",
            revision: str | None = None, wake: str = "w-1") -> None:
    connection.execute("""INSERT INTO desk_stances (stance_id, portfolio_id, wake_id, task_id, trading_day_start,
        symbol, stance, market_type_seen, label_status, label_market_type, label_id, strategy_ref, news_refs_json,
        reason, playbook_revision_id, created_at) VALUES (?, 'retained', ?, 't-1', ?, ?, ?, 'falling', ?, ?, ?, NULL,
        '["nh-1"]', 'Synthetic: it is falling.', ?, ?)""",
                       (stance_id, wake, DAY, symbol, stance, status, label_type, label, revision, LATER))


PLAN = "dp-" + "1" * 32
PLAN_2 = "dp-" + "2" * 32


def _plan(connection, plan_id: str = PLAN, *, symbol: str = "SOL/USD") -> None:
    connection.execute("""INSERT INTO day_plans (plan_id, portfolio_id, request_id, symbol, trading_day_start,
        trading_day_end, strategy_ref, strategy_sha256, practice_reports, supersedes_plan_id, policy_revision,
        mandate_revision, task_id, contract_version, document_json, document_sha256, installed_at)
        VALUES (?, 'retained', ?, ?, ?, '2026-10-17T00:00:00.000000Z', 'dip-rebound@1', ?, '["pr-1"]', NULL,
        'policy-1',
        '1', 't-1', 'day-plan-v1', '{}', ?, ?)""", (plan_id, f"req-{plan_id}", symbol, DAY, SHA, SHA, LATER))


def _record(connection, plan_id: str = PLAN, *, symbol: str = "SOL/USD", status: str = "missing",
            market_type: str | None = None, label: str | None = None, labelled: str | None = None,
            revision_id: str | None = None, revision: int | None = None, stance_id: str | None = None,
            stance: str | None = None) -> None:
    connection.execute("""INSERT INTO day_plan_desk_records (plan_id, portfolio_id, symbol, label_status, market_type,
        label_id, labelled_at, rules_ref, playbook_revision_id, playbook_revision, stance_id, stance, recorded_at)
        VALUES (?, 'retained', ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)""",
                       (plan_id, symbol, status, market_type, label, labelled, revision_id, revision, stance_id,
                        stance, LATEST))


def test_0052_is_registered_once_after_0051_with_exactly_this_schema() -> None:
    assert migration() == (VERSION, desk_schema.STATEMENTS) and VERSION == "0052"
    assert _versions().count(VERSION) == 1 and _versions()[-1] == VERSION
    assert dict(STATEMENTS)[VERSION] == desk_schema.STATEMENTS
    assert _versions() == sorted(_versions())
    assert _versions()[_versions().index(VERSION) - 1] == "0051"


def test_0039_itself_is_unchanged_and_the_value_lists_match_the_contracts() -> None:
    text = json.dumps(leader_news_schema.STATEMENTS)
    assert "'trader', 'research', 'optimisation'" not in text  # 0039 keeps its own list
    assert "department IN ('research', 'optimisation')" in text
    assert desk_schema.STANCES == trading_desk.STANCES
    assert desk_schema.SEEN_MARKET_TYPES == trading_desk.SEEN_MARKET_TYPES
    assert desk_schema.LABEL_STATUSES == trading_desk.LABEL_STATUSES
    assert hashlib.sha256(json.dumps(desk_schema.STATEMENTS).encode()).hexdigest()  # stable, serialisable


def test_0052_upgrades_a_0051_database_keeping_every_routed_request_with_its_sequence(tmp_path) -> None:
    connection = _database_before(tmp_path / "upgrade.sqlite")
    _round(connection)
    _request(connection, "q-1", kind="deep_dive", department="research", position=0)
    _request(connection, "q-2", kind="trader_note", department="trader", position=1)
    _request(connection, "q-3", kind="strategy_idea", department="research", asked="optimisation", position=2)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _request(connection, "q-desk", department="trader", position=3)  # not a destination before 0052
    _finish(connection)
    before = [tuple(row) for row in connection.execute("SELECT * FROM leader_round_requests ORDER BY sequence")]
    for table in TABLES:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchone() is None
    assert apply_migrations(connection, LATER) == [VERSION]
    after = [tuple(row) for row in connection.execute("SELECT * FROM leader_round_requests ORDER BY sequence")]
    assert after == before  # every row, every column, every sequence
    assert connection.execute("SELECT name FROM sqlite_master WHERE name LIKE '%0052%'").fetchall() == []
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    _round(connection, "r-2", task="t-2")
    _request(connection, "q-desk", department="trader", position=0, round_id="r-2")
    assert connection.execute("SELECT sequence FROM leader_round_requests WHERE request_id = 'q-desk'"
                              ).fetchone()[0] == 4
    for table in TABLES:
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert apply_migrations(connection, LATER) == []
    connection.close()


def test_the_rebuilt_request_table_keeps_every_rule_of_0039(upgraded) -> None:
    _round(upgraded)
    _request(upgraded, "q-1", department="trader", position=0)  # a strategy idea for the desk
    _request(upgraded, "q-2", department="research", asked="trader", position=1)  # asked for the desk, sent to Research
    for kind, department, asked in (("trader_note", "research", None), ("deep_dive", "trader", None),
                                    ("strategy_idea", "leader", None), ("deep_dive", "leader", None),
                                    ("trader_note", "trader", "research"), ("strategy_idea", "trader", "leader")):
        with pytest.raises(sqlite3.IntegrityError):
            _request(upgraded, f"bad-{kind}-{department}-{asked}", kind=kind, department=department, asked=asked,
                     position=5)
    with pytest.raises(sqlite3.IntegrityError):  # one request per position of the round's wake outcome
        _request(upgraded, "q-3", position=0)
    with pytest.raises(sqlite3.IntegrityError):  # sixteen wake requests at most in one answer
        _request(upgraded, "q-4", position=16)
    _finish(upgraded)
    with pytest.raises(sqlite3.IntegrityError, match="before it finishes"):
        _request(upgraded, "q-5", position=3)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute("UPDATE leader_round_requests SET department = 'research'")
    with pytest.raises(sqlite3.IntegrityError, match="kept"):
        upgraded.execute("DELETE FROM leader_round_requests")
    names = {row[0] for row in upgraded.execute("SELECT name FROM sqlite_master WHERE tbl_name='leader_round_requests'"
                                                " AND type IN ('index', 'trigger') AND name NOT LIKE 'sqlite_%'")}
    assert names == {"leader_round_requests_by_round", "leader_round_requests_with_open_round",
                     "leader_round_requests_no_delete", "leader_round_requests_no_update"}


def test_playbook_revisions_form_one_append_only_chain_per_portfolio(upgraded) -> None:
    _revision(upgraded, "pb-1", 1, None)
    with pytest.raises(sqlite3.IntegrityError, match="follows the newest"):
        _revision(upgraded, "pb-x", 3, "pb-1")  # skips a revision
    with pytest.raises(sqlite3.IntegrityError):
        _revision(upgraded, "pb-y", 2, None)  # a later revision names the one it replaced
    with pytest.raises(sqlite3.IntegrityError, match="follows the newest"):
        _revision(upgraded, "pb-z", 2, "pb-1", at=START)  # never earlier than the revision it replaced
    _revision(upgraded, "pb-2", 2, "pb-1", at=LATEST)
    with pytest.raises(sqlite3.IntegrityError, match="follows the newest"):
        _revision(upgraded, "pb-w", 3, "pb-1", at=LATEST)  # follows the newest, not an older one
    with pytest.raises(sqlite3.IntegrityError):
        _revision(upgraded, "pb-o", 1, None, set_by="leader", portfolio="other")  # only the desk sets its playbook
    _revision(upgraded, "pb-o", 1, None, portfolio="other")  # another portfolio has its own chain
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute("UPDATE desk_playbook_revisions SET why = 'changed'")
    with pytest.raises(sqlite3.IntegrityError, match="kept"):
        upgraded.execute("DELETE FROM desk_playbook_revisions")


def test_stances_are_append_only_once_per_coin_and_wake_with_the_label_shown(upgraded) -> None:
    _revision(upgraded, "pb-1", 1, None)
    _label(upgraded)
    _stance(upgraded, revision="pb-1")
    _stance(upgraded, "s-2", symbol="SOL/USD", stance="buy_dips", status="missing", label=None, label_type=None)
    with pytest.raises(sqlite3.IntegrityError):
        _stance(upgraded, "s-3")  # one stance per coin and wake
    with pytest.raises(sqlite3.IntegrityError):
        _stance(upgraded, "s-4", wake="w-2", stance="short")  # spot, long only: no such stance
    with pytest.raises(sqlite3.IntegrityError):
        _stance(upgraded, "s-5", wake="w-2", status="missing")  # a missing label names none
    with pytest.raises(sqlite3.IntegrityError):
        _stance(upgraded, "s-6", wake="w-2", label="mt-" + "f" * 32)  # a label that was never recorded
    with pytest.raises(sqlite3.IntegrityError):
        _stance(upgraded, "s-7", wake="w-2", revision="pb-missing")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute("UPDATE desk_stances SET stance = 'buy_dips'")
    with pytest.raises(sqlite3.IntegrityError, match="kept"):
        upgraded.execute("DELETE FROM desk_stances")


def test_a_day_plans_desk_record_names_its_own_plan_coin_playbook_and_stance(upgraded) -> None:
    _revision(upgraded, "pb-1", 1, None)
    label = _label(upgraded)
    _stance(upgraded, symbol="SOL/USD", stance="buy_dips", revision="pb-1")
    _plan(upgraded)
    _plan(upgraded, PLAN_2, symbol="BTC/USD")
    with pytest.raises(sqlite3.IntegrityError, match="its own plan"):
        _record(upgraded, symbol="BTC/USD")  # another coin than the plan's
    with pytest.raises(sqlite3.IntegrityError, match="its own plan"):
        _record(upgraded, revision_id="pb-1", revision=2)  # the revision's number is its own
    with pytest.raises(sqlite3.IntegrityError, match="its own plan"):
        _record(upgraded, stance_id="s-1", stance="wait")  # the stance as recorded
    with pytest.raises(sqlite3.IntegrityError):
        _record(upgraded, status="missing", market_type="sideways")  # a missing label has no type
    with pytest.raises(sqlite3.IntegrityError):
        _record(upgraded, "dp-" + "9" * 32)
    _record(upgraded, status="current", market_type="falling", label=label, labelled=LATER, revision_id="pb-1",
            revision=1, stance_id="s-1", stance="buy_dips")
    _record(upgraded, PLAN_2, symbol="BTC/USD")  # nothing known: recorded as missing, no playbook, no stance
    with pytest.raises(sqlite3.IntegrityError):
        _record(upgraded)  # one record per plan
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute("UPDATE day_plan_desk_records SET market_type = 'rising'")
    with pytest.raises(sqlite3.IntegrityError, match="kept"):
        upgraded.execute("DELETE FROM day_plan_desk_records")


def test_alembic_revision_follows_0051_and_refuses_downgrade() -> None:
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == [STATEMENTS[-1][0]] == [VERSION]
    assert scripts.get_revision(VERSION).down_revision == "0051"
    spec = importlib.util.spec_from_file_location("migration_0052", ROOT / "migrations" / "versions" /
                                                  "0052_trading_desk.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == (VERSION, "0051")
    with pytest.raises(RuntimeError, match="not automatic"):
        module.downgrade()
