"""The coin scanner's persisted record (T31, D174): what a scan stored, the latest shortlist and the coin rule.

Synthetic candles and ticker (tests/coin_scanner_support.py); no network, model or key.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from decimal import Decimal

import pytest
from tests.coin_scanner_support import AS_OF, Path_, hourly, new_store, record, save_candles, save_catalog, ticker
from tests.integration.test_coin_scanner import ELIGIBLE, RULES

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.coin_scanner.scanner import CoinScanner
from trade_graph.application.coin_scanner.store import CoinScanStore, read_latest_scan
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import NotFound, StaleState

BASES = ("SOL", "BTC", "ETH", "ADA", "DOT", "AVAX", "XRP", "DOGE", "LINK", "WILDX", "ATOM")


@pytest.fixture(scope="module")
def candles(tmp_path_factory):
    store = new_store(tmp_path_factory.mktemp("scan-store") / "private")
    save_catalog(store, [record(base) for base in (*BASES, "USDT")], observed_at=AS_OF - timedelta(hours=1))
    for base in BASES:
        save_candles(store, hourly(f"{base}/USD", ELIGIBLE[base][0]))
    return store


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / "state" / "trade_graph.sqlite")
    yield database
    database.close()


def _quotes(*, without: str | None = None) -> dict:
    from tests.coin_scanner_support import tight_quote

    return {f"{base}/USD": tight_quote(ELIGIBLE[base][0].level, ELIGIBLE[base][1]) for base in BASES
            if base != without}


def _scan(candles, *, as_of=AS_OF, quotes=None, mode="live"):
    scanner = CoinScanner(candles, clock=FrozenClock(as_of + timedelta(seconds=30)), rules=RULES)
    if mode == "replay":
        return scanner.scan(as_of=as_of, ticker=None, mode="replay", source="synthetic")
    return scanner.scan(as_of=as_of, ticker=ticker(quotes or _quotes(), received_at=as_of - timedelta(seconds=20)))


def test_a_recorded_scan_reads_back_exactly_as_it_was_made(candles, database) -> None:
    result = _scan(candles)
    store = CoinScanStore(database, FrozenClock(AS_OF + timedelta(minutes=1)))
    stored = store.record(result)
    again = store.get(stored.scan_id)
    assert again.document == result.document() == stored.document
    assert again.entries == result.entries()
    assert (again.mode, again.status, again.optimistic, again.ranking_version) == (
        "live", "ok", False, RULES.ranking_version)
    assert [row["symbol"] for row in again.document["ranking"]] == [item.symbol for item in result.ranked]
    counts = database.execute("SELECT usd_pairs, ranked_pairs, shortlisted FROM coin_scans").fetchone()
    assert tuple(counts) == (12, 11, 10)
    assert database.execute("SELECT COUNT(*) FROM coin_scan_pairs").fetchone()[0] == 11
    assert database.execute("SELECT COUNT(*) FROM coin_shortlist_entries").fetchone()[0] == 10


def test_each_shortlist_entry_row_holds_every_input_and_the_ranking_version(candles, database) -> None:
    store = CoinScanStore(database, FrozenClock(AS_OF))
    store.record(_scan(candles))
    row = database.execute("SELECT symbol, inputs_json FROM coin_shortlist_entries WHERE position = 1").fetchone()
    entry = json.loads(row["inputs_json"])
    assert row["symbol"] == "SOL/USD" and entry["ranking_version"] == RULES.ranking_version
    assert {"catalog", "candles", "volume", "swing", "trend", "peg_check", "spread", "cost"} <= set(entry["inputs"])
    assert entry["inputs"]["spread"]["received_at_utc"] == "2026-10-16T12:04:40.000000Z"
    assert isinstance(entry["inputs"]["volume"]["value_usd"], str)  # money is exact decimal text, never a float
    Decimal(entry["inputs"]["volume"]["value_usd"])


def test_a_failed_scan_is_recorded_with_its_reason_and_no_places(tmp_path, database) -> None:
    empty = new_store(tmp_path / "private")
    result = CoinScanner(empty, clock=FrozenClock(AS_OF), rules=RULES).scan(as_of=AS_OF, ticker=None)
    stored = CoinScanStore(database, FrozenClock(AS_OF)).record(result)
    assert (stored.status, stored.error, stored.entries) == ("failed", "no_pair_catalog", [])
    assert database.execute("SELECT COUNT(*) FROM coin_scan_pairs").fetchone()[0] == 0


def test_the_latest_shortlist_is_the_newest_live_scan_at_or_before_the_moment(candles, database) -> None:
    store = CoinScanStore(database, FrozenClock(AS_OF))
    assert store.latest() is None
    earlier = store.record(_scan(candles, as_of=AS_OF - timedelta(hours=1)))
    newer = store.record(_scan(candles))
    replay = store.record(_scan(candles, mode="replay"))
    assert store.latest().scan_id == newer.scan_id
    assert store.latest(as_of=AS_OF - timedelta(minutes=30)).scan_id == earlier.scan_id
    assert store.latest(as_of=AS_OF - timedelta(hours=2)) is None
    assert store.latest(mode="replay").scan_id == replay.scan_id and replay.optimistic
    assert store.latest().optimistic is False  # Research is never handed an optimistic replay as the live list


def test_the_coin_rule_answers_from_the_newest_live_scan(candles, database) -> None:
    store = CoinScanStore(database, FrozenClock(AS_OF))
    assert store.coin_rule("SOL/USD", as_of=AS_OF).status == "no_recent_scan"
    stored = store.record(_scan(candles, quotes=_quotes(without="ADA")))
    passed = store.coin_rule("SOL/USD", as_of=AS_OF + timedelta(minutes=10))
    assert (passed.status, passed.passed, passed.scan_id) == ("passed_checks", True, stored.scan_id)
    assert passed.position == 1 and passed.age_seconds == 600
    excluded = store.coin_rule("ADA/USD", as_of=AS_OF)
    assert (excluded.status, excluded.passed, excluded.reasons) == ("excluded", False, ("missing_spread",))
    stable = store.coin_rule("USDT/USD", as_of=AS_OF)
    assert (stable.status, stable.passed, stable.reasons) == ("excluded", False, ("stablecoin",))
    unknown = store.coin_rule("ZZZ/USD", as_of=AS_OF)
    assert (unknown.status, unknown.passed) == ("not_in_catalog", False)
    stale = store.coin_rule("SOL/USD", as_of=AS_OF + timedelta(hours=3))
    assert (stale.status, stale.passed) == ("no_recent_scan", None)


def test_a_failed_newest_scan_answers_unknown_not_no(candles, tmp_path, database) -> None:
    store = CoinScanStore(database, FrozenClock(AS_OF))
    store.record(_scan(candles, as_of=AS_OF - timedelta(minutes=50)))
    failed = CoinScanner(new_store(tmp_path / "empty"), clock=FrozenClock(AS_OF), rules=RULES).scan(
        as_of=AS_OF, ticker=None)
    store.record(failed)
    answer = store.coin_rule("SOL/USD", as_of=AS_OF)
    assert (answer.status, answer.passed, answer.error) == ("scan_failed", None, "no_pair_catalog")


def test_a_stored_document_that_no_longer_matches_its_hash_is_refused(candles, database) -> None:
    store = CoinScanStore(database, FrozenClock(AS_OF))
    stored = store.record(_scan(candles))
    with database.immediate() as connection:
        connection.execute("DROP TRIGGER coin_scans_no_update")
        connection.execute("UPDATE coin_scans SET document_json = replace(document_json, 'SOL/USD', 'SOX/USD')")
    with pytest.raises(StaleState, match="hash"):
        store.get(stored.scan_id)
    with pytest.raises(NotFound):
        store.get("no-such-scan")


def test_the_read_only_report_reads_without_creating_or_upgrading(candles, database, tmp_path) -> None:
    CoinScanStore(database, FrozenClock(AS_OF)).record(_scan(candles))
    connection = sqlite3.connect(f"{database.path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        latest = read_latest_scan(connection)
    finally:
        connection.close()
    assert latest.document["shortlist"][0] == "SOL/USD" and len(latest.entries) == 10


def test_a_replay_on_synthetic_history_uses_close_volume_only_when_vwap_is_absent(tmp_path, database) -> None:
    store = new_store(tmp_path / "private")
    save_catalog(store, [record("SOL")], observed_at=AS_OF - timedelta(hours=1))
    path = ELIGIBLE["SOL"][0]
    save_candles(store, hourly("SOL/USD", Path_(path.level, path.swing, path.drift, path.volume)))
    stored = CoinScanStore(database, FrozenClock(AS_OF)).record(_scan(store, mode="replay"))
    assert stored.optimistic and stored.entries[0]["inputs"]["volume"]["price_basis"] == "vwap"
