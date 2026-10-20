"""Migration 0050 records live market-type labels (W11-A, C10; D287).

The rules hold at the SQL level, whatever code writes: every label is kept and never changed; a label is one of the
four types and names a reason exactly when it is unclear; it never rests on a candle that closed after its moment; it
names its rules version and hash, its source and interval, and the scan that asked for it. Existing databases upgrade
without a change to their rows, and the newest label of a coin is read through an index. Synthetic rows only.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from trade_graph.adapters.persistence.market_type_schema import INDEX, TABLE, VERSION, migration
from trade_graph.adapters.persistence.market_type_schema import STATEMENTS as LABEL_STATEMENTS
from trade_graph.adapters.persistence.migrate import STATEMENTS, apply_migrations

ROOT = Path(__file__).resolve().parents[2]
START = "2026-10-01T00:00:00.000000Z"
AS_OF = "2026-10-20T14:05:00.000000Z"
CANDLE = "2026-10-20T14:00:00.000000Z"
SHA = "b" * 64


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
    return connection


def _scan(connection, scan_id: str = "scan-1") -> None:
    connection.execute(
        """INSERT INTO coin_scans (scan_id, mode, as_of, computed_at, recorded_at, ranking_version, status, error,
           evidence, optimistic, usd_pairs, ranked_pairs, shortlisted, document_json, document_sha256)
           VALUES (?, 'live', ?, ?, ?, 'coin-scanner-r1-synthetic/000000000000', 'ok', NULL, 'synthetic_fixture', 0,
           1, 1, 1, '{}', ?)""", (scan_id, AS_OF, AS_OF, AS_OF, SHA))


def _label(connection, label_id: str = "mt-" + "1" * 32, *, market_type: str = "rising", reason=None,
           candle=CANDLE, lag=0, as_of=AS_OF, symbol="SOL/USD", scan_id="scan-1", rules_ref="market-type-rules@1",
           source="kraken_public_ohlc", interval=60, evidence="live_public") -> None:
    connection.execute(
        f"""INSERT INTO {TABLE} (label_id, symbol, as_of, market_type, reason, candle_close, lag_bars,
           interval_minutes, source, rules_ref, rules_sha256, scan_id, evidence, document_json, document_sha256,
           recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{{}}', ?, ?)""",
        (label_id, symbol, as_of, market_type, reason, candle, lag, interval, source, rules_ref, SHA, scan_id,
         evidence, SHA, AS_OF))


@pytest.fixture
def upgraded(tmp_path):
    connection = _database_before(tmp_path / "state.sqlite")
    apply_migrations(connection, AS_OF)
    _scan(connection)
    yield connection
    connection.close()


def test_0050_is_registered_once_in_order_with_exactly_this_schema() -> None:
    assert migration() == (VERSION, LABEL_STATEMENTS) == ("0050", LABEL_STATEMENTS)
    assert _versions().count(VERSION) == 1
    assert dict(STATEMENTS)[VERSION] == LABEL_STATEMENTS
    assert _versions() == sorted(_versions())
    assert {"0031", "0044"} <= set(_versions()[:_versions().index(VERSION)])


def test_0050_upgrades_an_earlier_database_once_without_rewriting_rows(tmp_path) -> None:
    connection = _database_before(tmp_path / "upgrade.sqlite")
    _scan(connection)
    scans = [tuple(row) for row in connection.execute("SELECT * FROM coin_scans")]
    assert connection.execute("SELECT name FROM sqlite_master WHERE name=?", (TABLE,)).fetchone() is None
    assert apply_migrations(connection, AS_OF) == [version for version in _versions() if version >= VERSION]
    assert [tuple(row) for row in connection.execute("SELECT * FROM coin_scans")] == scans
    assert connection.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0] == 0
    assert apply_migrations(connection, AS_OF) == []
    connection.close()


def test_every_label_is_kept_and_never_changed(upgraded) -> None:
    _label(upgraded)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute(f"UPDATE {TABLE} SET market_type = 'falling'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upgraded.execute(f"DELETE FROM {TABLE}")


def test_a_label_is_one_of_four_types_with_a_reason_exactly_when_unclear(upgraded) -> None:
    _label(upgraded)
    _label(upgraded, "mt-" + "2" * 32, market_type="unclear", reason="gap")
    _label(upgraded, "mt-" + "3" * 32, market_type="unclear", reason="no_recent_candle", candle=None, lag=None)
    bad = [
        dict(market_type="bullish"), dict(market_type="unclear"), dict(market_type="rising", reason="gap"),
        dict(market_type="unclear", reason="guess"), dict(candle=None), dict(lag=-1),
        dict(candle="2026-10-20T15:00:00.000000Z"),  # closed after the label's moment
        dict(as_of="2026-10-20 14:05"), dict(symbol="SOLUSD"), dict(interval=5), dict(source="tradingview"),
        dict(rules_ref="other@1"), dict(scan_id="no-such-scan"), dict(evidence="rumour"),
    ]
    for number, changes in enumerate(bad, start=10):
        with pytest.raises(sqlite3.IntegrityError):
            _label(upgraded, f"mt-{number:032x}", **changes)
    with pytest.raises(sqlite3.IntegrityError):
        _label(upgraded, "mt-not-hex")
    with pytest.raises(sqlite3.IntegrityError):  # one id, one label
        _label(upgraded, "mt-" + "1" * 32)
    _label(upgraded, "mt-" + "4" * 32, scan_id=None)  # a label outside a scan names none


def test_the_newest_label_of_a_coin_is_read_through_the_index(upgraded) -> None:
    index = upgraded.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (INDEX,)).fetchone()
    assert index is not None and "(symbol, as_of, sequence)" in index["sql"]
    plan = " ".join(row[-1] for row in upgraded.execute(
        f"EXPLAIN QUERY PLAN SELECT * FROM {TABLE} WHERE symbol = ? AND as_of <= ? ORDER BY as_of DESC, "
        "sequence DESC LIMIT 1", ("SOL/USD", AS_OF)))
    assert INDEX in plan and f"SCAN {TABLE}" not in plan


def test_alembic_revision_follows_the_previous_version_and_refuses_downgrade() -> None:
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    previous = _versions()[_versions().index(VERSION) - 1]
    assert previous == "0044"
    assert scripts.get_heads() == [STATEMENTS[-1][0]]
    assert scripts.get_revision(VERSION).down_revision == previous
    spec = importlib.util.spec_from_file_location("migration_0050", ROOT / "migrations" / "versions" /
                                                  "0050_market_type_labels.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == (VERSION, previous)
    with pytest.raises(RuntimeError, match="not automatic"):
        module.downgrade()


def test_the_fixed_value_lists_match_the_code_that_writes_them() -> None:
    from trade_graph.adapters.persistence.market_type_schema import EVIDENCE, INTERVALS, MARKET_TYPES, REASONS, SOURCES
    from trade_graph.application.market_types.labeller import NO_CANDLES
    from trade_graph.contracts.candles import CANDLE_SOURCES, EVIDENCE_LABELS
    from trade_graph.domain.market_types import MARKET_TYPES as TYPES
    from trade_graph.kernel.market_type import LABEL_INTERVALS, UNCLEAR_REASONS

    assert (MARKET_TYPES, REASONS, SOURCES, INTERVALS) == (TYPES, UNCLEAR_REASONS, CANDLE_SOURCES, LABEL_INTERVALS)
    assert set(EVIDENCE) == {*EVIDENCE_LABELS, NO_CANDLES, "unknown"}
