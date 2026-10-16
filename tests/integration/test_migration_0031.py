"""Migration 0031 adds the coin scanner's record (T31): scans, the pairs that passed, and the shortlist entries.

The store's rules are tested at the SQL level, so they hold whatever code writes to it: rows are append-only, a
replay is always optimistic, only a scan that ranked something has places, and a shortlist entry needs a
shortlisted place.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from trade_graph.adapters.persistence.coin_scan_schema import COIN_SCAN_STATEMENTS, VERSION, migration
from trade_graph.adapters.persistence.migrate import STATEMENTS, apply_migrations

ROOT = Path(__file__).resolve().parents[2]
START = "2026-10-01T00:00:00.000000Z"
LATER = "2026-10-16T00:00:00.000000Z"
PORTFOLIO = ("retained", "paper", "USD", "experiment", "active", None, START)
_SCAN = """INSERT INTO coin_scans (scan_id, mode, as_of, computed_at, recorded_at, ranking_version, status, error,
    evidence, optimistic, usd_pairs, ranked_pairs, shortlisted, document_json, document_sha256)
    VALUES (?, ?, ?, ?, ?, 'coin-scanner-r1-2026-10-16/abc', ?, ?, 'synthetic_fixture', ?, 5, ?, ?, '{}', ?)"""
_PAIR = """INSERT INTO coin_scan_pairs (scan_sequence, position, symbol, score, shortlisted, ranking_json)
    VALUES (?, ?, ?, 4, ?, '{}')"""
_ENTRY = "INSERT INTO coin_shortlist_entries (scan_sequence, position, symbol, inputs_json) VALUES (?, ?, ?, '{}')"
_SHA = "0" * 64


def _versions() -> list[str]:
    return [version for version, _ in STATEMENTS]


def _database_before_0031(path: Path) -> sqlite3.Connection:
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
    connection = _database_before_0031(tmp_path / "state.sqlite")
    apply_migrations(connection, LATER)
    yield connection
    connection.close()


def _scan(connection, scan_id="s1", *, mode="live", status="ok", error=None, optimistic=0, ranked=2,
          shortlisted=1) -> int:
    cursor = connection.execute(_SCAN, (scan_id, mode, START, START, START, status, error, optimistic, ranked,
                                        shortlisted, _SHA))
    return cursor.lastrowid


def test_0031_is_registered_once_with_exactly_this_schema() -> None:
    assert migration() == (VERSION, COIN_SCAN_STATEMENTS) == ("0031", COIN_SCAN_STATEMENTS)
    assert _versions().count(VERSION) == 1
    assert dict(STATEMENTS)[VERSION] == COIN_SCAN_STATEMENTS
    assert _versions() == sorted(_versions())
    assert "0029" in _versions()[:_versions().index(VERSION)]


def test_0031_upgrades_an_earlier_database_once_without_rewriting_rows(tmp_path) -> None:
    connection = _database_before_0031(tmp_path / "upgrade.sqlite")
    assert connection.execute("SELECT name FROM sqlite_master WHERE name='coin_scans'").fetchone() is None
    retained = connection.execute("SELECT * FROM portfolios").fetchall()
    assert apply_migrations(connection, LATER) == [version for version in _versions() if version >= VERSION]
    assert connection.execute("SELECT * FROM portfolios").fetchall() == retained
    for table in ("coin_scans", "coin_scan_pairs", "coin_shortlist_entries"):
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert apply_migrations(connection, LATER) == []
    connection.close()


def test_every_table_is_append_only(upgraded) -> None:
    sequence = _scan(upgraded)
    upgraded.execute(_PAIR, (sequence, 1, "SOL/USD", 1))
    upgraded.execute(_PAIR, (sequence, 2, "BTC/USD", 0))
    upgraded.execute(_ENTRY, (sequence, 1, "SOL/USD"))
    for statement in ("UPDATE coin_scans SET status='failed'", "DELETE FROM coin_scans",
                      "UPDATE coin_scan_pairs SET score=1", "DELETE FROM coin_scan_pairs",
                      "UPDATE coin_shortlist_entries SET inputs_json='[]'", "DELETE FROM coin_shortlist_entries"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            upgraded.execute(statement)


def test_a_replay_is_always_optimistic_and_a_live_scan_never_is(upgraded) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _scan(upgraded, "r1", mode="replay", optimistic=0)
    with pytest.raises(sqlite3.IntegrityError):
        _scan(upgraded, "l1", mode="live", optimistic=1)
    _scan(upgraded, "r2", mode="replay", optimistic=1)


def test_status_error_and_counts_agree(upgraded) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _scan(upgraded, "f1", status="failed", error=None, ranked=0, shortlisted=0)
    with pytest.raises(sqlite3.IntegrityError):
        _scan(upgraded, "f2", status="ok", error="no_pair_catalog")
    with pytest.raises(sqlite3.IntegrityError):
        _scan(upgraded, "f3", status="ok", ranked=0, shortlisted=0)
    with pytest.raises(sqlite3.IntegrityError):
        _scan(upgraded, "f4", status="no_candidates", ranked=1, shortlisted=0)
    with pytest.raises(sqlite3.IntegrityError):
        _scan(upgraded, "f5", ranked=2, shortlisted=3)
    _scan(upgraded, "ok1", status="failed", error="no_pair_catalog", ranked=0, shortlisted=0)


def test_places_belong_to_a_scan_that_ranked_them(upgraded) -> None:
    sequence = _scan(upgraded, ranked=2, shortlisted=1)
    empty = _scan(upgraded, "s2", status="no_candidates", ranked=0, shortlisted=0)
    with pytest.raises(sqlite3.IntegrityError, match="place"):
        upgraded.execute(_PAIR, (empty, 1, "SOL/USD", 0))
    with pytest.raises(sqlite3.IntegrityError, match="place"):
        upgraded.execute(_PAIR, (sequence, 3, "ETH/USD", 0))       # beyond the ranked count
    with pytest.raises(sqlite3.IntegrityError, match="place"):
        upgraded.execute(_PAIR, (sequence, 2, "ETH/USD", 1))       # beyond the shortlist
    with pytest.raises(sqlite3.IntegrityError, match="place"):
        upgraded.execute(_PAIR, (sequence, 1, "SOL/USD", 0))       # inside the shortlist but not marked
    upgraded.execute(_PAIR, (sequence, 1, "SOL/USD", 1))
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_PAIR, (sequence, 2, "SOL/USD", 0))       # one place per pair


def test_a_shortlist_entry_needs_its_shortlisted_place(upgraded) -> None:
    sequence = _scan(upgraded, ranked=2, shortlisted=1)
    upgraded.execute(_PAIR, (sequence, 1, "SOL/USD", 1))
    upgraded.execute(_PAIR, (sequence, 2, "BTC/USD", 0))
    with pytest.raises(sqlite3.IntegrityError, match="shortlisted"):
        upgraded.execute(_ENTRY, (sequence, 2, "BTC/USD"))
    with pytest.raises(sqlite3.IntegrityError, match="shortlisted"):
        upgraded.execute(_ENTRY, (sequence, 1, "BTC/USD"))
    upgraded.execute(_ENTRY, (sequence, 1, "SOL/USD"))


def test_alembic_revision_follows_the_previous_version_and_refuses_downgrade() -> None:
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    previous = _versions()[_versions().index(VERSION) - 1]
    assert len(scripts.get_heads()) == 1
    assert scripts.get_revision(VERSION).down_revision == previous
    spec = importlib.util.spec_from_file_location("migration_0031", ROOT / "migrations" / "versions" /
                                                  "0031_coin_scans.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == (VERSION, previous)
    with pytest.raises(RuntimeError, match="not automatic"):
        module.downgrade()
