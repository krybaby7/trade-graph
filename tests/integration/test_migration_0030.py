"""Migration 0030 adds the practice-range store (T29): additive, append-only, untouched confirmation windows in SQL.

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
from trade_graph.adapters.persistence.practice_range_schema import PRACTICE_RANGE_STATEMENTS, VERSION, migration

ROOT = Path(__file__).resolve().parents[2]
START = "2026-10-01T00:00:00.000000Z"
LATER = "2026-10-16T00:00:00.000000Z"
TABLES = ("practice_strategy_versions", "practice_range_studies", "practice_range_runs",
          "practice_range_verifications")
HASH = "a" * 64
_VERSION = """INSERT INTO practice_strategy_versions (strategy_ref, strategy_id, version, content_sha256,
    document_json, registered_at) VALUES (?, ?, ?, ?, '{}', ?)"""
_STUDY = """INSERT INTO practice_range_studies (study_id, strategy_id, tune_start, tune_end, confirm_start, confirm_end,
    document_json, created_at) VALUES (?, ?, ?, ?, ?, ?, '{}', ?)"""
_RUN = """INSERT INTO practice_range_runs (run_id, study_id, phase, strategy_ref, symbol, interval_minutes, source,
    cost_profile_ref, fill_policy_ref, window_start, window_end, data_as_of, evidence, result_sha256, report_json,
    created_at) VALUES (?, ?, ?, ?, 'BTC/USD', 15, 'synthetic', 'kraken-spot-starting-tier@1', 'bar-replay@1', ?, ?,
    ?, 'synthetic_fixture', ?, '{}', ?)"""


def _versions() -> list[str]:
    return [version for version, _ in STATEMENTS]


def _database_before_0030(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA foreign_keys=ON")
    for version, statements in STATEMENTS:
        if version == VERSION:
            break
        for statement in statements:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations VALUES (?,?)", (version, START))
    connection.execute("INSERT INTO portfolios VALUES (?,?,?,?,?,?,?)",
                       ("retained", "paper", "USD", "experiment", "active", None, START))
    return connection


@pytest.fixture
def upgraded(tmp_path):
    connection = _database_before_0030(tmp_path / "state.sqlite")
    apply_migrations(connection, LATER)
    connection.execute(_VERSION, ("trend@1", "trend", 1, HASH, START))
    connection.execute(_VERSION, ("trend@2", "trend", 2, HASH, START))
    connection.execute(_VERSION, ("other@1", "other", 1, HASH, START))
    connection.execute(_STUDY, ("s1", "trend", "2026-04-01", "2026-06-01", "2026-06-01", "2026-07-01", START))
    yield connection
    connection.close()


def test_0030_is_registered_once_after_0029_with_exactly_this_schema() -> None:
    assert migration() == (VERSION, PRACTICE_RANGE_STATEMENTS) == ("0030", PRACTICE_RANGE_STATEMENTS)
    assert _versions().count(VERSION) == 1
    assert dict(STATEMENTS)[VERSION] == PRACTICE_RANGE_STATEMENTS
    assert _versions() == sorted(_versions())
    assert _versions()[_versions().index(VERSION) - 1] == "0029"


def test_0030_upgrades_an_earlier_database_once_without_rewriting_rows(tmp_path) -> None:
    connection = _database_before_0030(tmp_path / "upgrade.sqlite")
    before = connection.execute("SELECT * FROM portfolios").fetchall()
    pending = _versions()[_versions().index(VERSION):]  # 0030 and any version registered after it
    assert apply_migrations(connection, LATER) == pending
    assert connection.execute("SELECT * FROM portfolios").fetchall() == before
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(TABLES) <= tables
    assert all(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0 for table in TABLES)
    assert apply_migrations(connection, LATER) == []
    connection.close()


def test_alembic_revision_follows_the_previous_version_and_refuses_downgrade() -> None:
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    previous = _versions()[_versions().index(VERSION) - 1]
    assert scripts.get_heads() == [STATEMENTS[-1][0]]  # one linear chain, headed by the newest registered version
    assert scripts.get_revision(VERSION).down_revision == previous
    path = ROOT / "migrations" / "versions" / "0030_practice_range.py"
    spec = importlib.util.spec_from_file_location("migration_0030", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == (VERSION, previous)
    with pytest.raises(RuntimeError):
        module.downgrade()


@pytest.mark.parametrize("table", TABLES[:3])
def test_every_table_is_append_only(upgraded, table) -> None:
    upgraded.execute(_RUN, ("r1", "s1", "tune", "trend@1", "2026-04-01", "2026-05-01", LATER, HASH, LATER))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute(f"UPDATE {table} SET sequence = sequence")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute(f"DELETE FROM {table}")


def test_a_reference_names_one_document(upgraded) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_VERSION, ("trend@1", "trend", 1, "b" * 64, START))
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_VERSION, ("trend@3", "trend", 4, HASH, START))  # the reference must spell the version


@pytest.mark.parametrize(("tune", "confirm"), [
    (("2026-06-15", "2026-07-15"), ("2026-08-01", "2026-09-01")),  # tunes on the first study's confirmation days
    (("2026-01-01", "2026-02-01"), ("2026-06-20", "2026-08-01")),  # confirms on days already set aside
    (("2026-01-01", "2026-02-01"), ("2026-05-01", "2026-05-15")),  # confirms on days the family tuned on
])
def test_a_study_cannot_touch_another_studys_confirmation_days(upgraded, tune, confirm) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="untouched"):
        upgraded.execute(_STUDY, ("s2", "trend", *tune, *confirm, START))


def test_studies_of_other_families_or_later_untouched_windows_are_accepted(upgraded) -> None:
    upgraded.execute(_STUDY, ("s2", "trend", "2026-04-01", "2026-06-01", "2026-07-01", "2026-08-01", START))
    upgraded.execute(_STUDY, ("s3", "other", "2026-06-01", "2026-07-01", "2026-07-01", "2026-08-01", START))
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(_STUDY, ("s4", "trend", "2026-09-01", "2026-08-01", "2026-09-01", "2026-10-01", START))


def test_tuning_stays_inside_its_window_and_off_every_confirmation_window(upgraded) -> None:
    upgraded.execute(_RUN, ("r1", "s1", "tune", "trend@1", "2026-04-01", "2026-06-01", LATER, HASH, LATER))
    with pytest.raises(sqlite3.IntegrityError, match="inside"):
        upgraded.execute(_RUN, ("r2", "s1", "tune", "trend@1", "2026-03-31", "2026-05-01", LATER, HASH, LATER))
    with pytest.raises(sqlite3.IntegrityError, match="inside|reused"):  # one more day reaches the confirmation
        upgraded.execute(_RUN, ("r2", "s1", "tune", "trend@1", "2026-04-01", "2026-06-02", LATER, HASH, LATER))
    upgraded.execute(_STUDY, ("s2", "trend", "2026-04-01", "2026-06-01", "2026-07-01", "2026-08-01", START))
    upgraded.execute(_RUN, ("r3", "s2", "tune", "trend@2", "2026-05-01", "2026-06-01", LATER, HASH, LATER))


def test_a_confirmation_replays_its_whole_window_for_one_version_only(upgraded) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="whole"):
        upgraded.execute(_RUN, ("r1", "s1", "confirm", "trend@1", "2026-06-01", "2026-06-20", LATER, HASH, LATER))
    upgraded.execute(_RUN, ("r1", "s1", "confirm", "trend@1", "2026-06-01", "2026-07-01", LATER, HASH, LATER))
    upgraded.execute(_RUN, ("r2", "s1", "confirm", "trend@1", "2026-06-01", "2026-07-01", LATER, HASH, LATER))
    with pytest.raises(sqlite3.IntegrityError, match="spent"):
        upgraded.execute(_RUN, ("r3", "s1", "confirm", "trend@2", "2026-06-01", "2026-07-01", LATER, HASH, LATER))


def test_a_study_replays_its_own_family_only(upgraded) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="family"):
        upgraded.execute(_RUN, ("r1", "s1", "tune", "other@1", "2026-04-01", "2026-05-01", LATER, HASH, LATER))
