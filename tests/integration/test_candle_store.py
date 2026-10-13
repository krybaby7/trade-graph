"""Append-only candle revisions with receipt-time availability; synthetic data, no network."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.application.candle_store import CandleStore
from trade_graph.contracts.candles import Candle
from trade_graph.domain.errors import ValidationFailure

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def _candle(index: int = 0, *, interval: int = 15, close: str = "100.75", source: str = "synthetic",
            symbol: str = "BTC/USD", pair: str = "XBTUSD", receipt_delay: int = 4) -> Candle:
    opened = T0 + timedelta(minutes=interval * index)
    closed = opened + timedelta(minutes=interval)
    return Candle(
        symbol=symbol, source_pair=pair, interval_minutes=interval, open_time_utc=opened, close_time_utc=closed,
        available_at_utc=closed + timedelta(seconds=receipt_delay),
        open=Decimal("100.10"), high=Decimal("101"), low=Decimal("99.5"), close=Decimal(close),
        vwap=Decimal("100.4"), volume=Decimal("12.5"), trade_count=7, source=source,
    )


@pytest.fixture
def store(tmp_path):
    database = MarketDataDatabase(tmp_path / "market" / "market_data.sqlite")
    yield CandleStore(database)
    database.close()


def _run(store: CandleStore, *, at: datetime = T0 + timedelta(days=1)) -> int:
    return store.begin_run(kind="public_collect", source="synthetic", evidence_label="synthetic_fixture",
                           started_at=at, parameters={"note": "test"})


def test_database_is_private_and_reopening_is_idempotent(tmp_path) -> None:
    path = tmp_path / "private" / "market_data.sqlite"
    first = MarketDataDatabase(path)
    first.close()
    assert os.stat(path).st_mode & 0o077 == 0
    assert os.stat(path.parent).st_mode & 0o077 == 0
    second = MarketDataDatabase(path)
    versions = [row[0] for row in second.execute("SELECT version FROM market_data_migrations ORDER BY version")]
    assert versions == ["0001"]
    second.close()


def test_database_refuses_a_symlinked_file(tmp_path) -> None:
    target = tmp_path / "real.sqlite"
    target.touch(mode=0o600)
    link = tmp_path / "link.sqlite"
    link.symlink_to(target)
    with pytest.raises(ValidationFailure):
        MarketDataDatabase(link)


def test_saved_candles_are_read_back_exactly_as_one_window(store: CandleStore) -> None:
    run_id = _run(store)
    candles = [_candle(i) for i in range(4)]
    result = store.save(candles, run_id=run_id)
    assert (result.inserted, result.revised, result.duplicates) == (4, 0, 0)
    end = candles[-1].close_time_utc
    window = store.window("BTC/USD", 15, "synthetic", end_close_time=end, count=4, as_of=end + timedelta(minutes=1))
    assert [slot.stored.candle for slot in window.slots] == candles
    assert window.complete and window.missing_close_times == ()
    assert window.mode == "point_in_time" and window.availability_basis == "receipt_time"
    assert all(slot.stored.revision == 1 and slot.stored.run_id == run_id for slot in window.slots)


def test_missing_slots_are_listed_and_never_filled(store: CandleStore) -> None:
    run_id = _run(store)
    store.save([_candle(0), _candle(1), _candle(3)], run_id=run_id)
    end = _candle(3).close_time_utc
    window = store.window("BTC/USD", 15, "synthetic", end_close_time=end, count=4, as_of=end + timedelta(minutes=1))
    assert not window.complete
    assert window.missing_close_times == (_candle(2).close_time_utc,)
    assert [slot.stored is None for slot in window.slots] == [False, False, True, False]
    assert len(window.candles) == 3


def test_an_unknown_series_gives_all_missing_slots(store: CandleStore) -> None:
    end = T0 + timedelta(hours=1)
    window = store.window("ETH/USD", 15, "synthetic", end_close_time=end, count=3, as_of=end)
    assert window.candles == () and len(window.missing_close_times) == 3


def test_saving_identical_content_again_changes_nothing(store: CandleStore) -> None:
    run_id = _run(store)
    candle = _candle(0)
    store.save([candle], run_id=run_id)
    again = _candle(0, receipt_delay=600)
    result = store.save([again], run_id=run_id)
    assert (result.inserted, result.revised, result.duplicates) == (0, 0, 1)
    revisions = store.revisions("BTC/USD", 15, "synthetic", candle.open_time_utc)
    assert len(revisions) == 1 and revisions[0].candle.available_at_utc == candle.available_at_utc


def test_changed_content_becomes_the_next_revision_and_both_stay_readable(store: CandleStore) -> None:
    run_id = _run(store)
    first = _candle(0)
    store.save([first], run_id=run_id)
    corrected = _candle(0, close="100.80", receipt_delay=3600)
    result = store.save([corrected], run_id=run_id)
    assert (result.inserted, result.revised, result.duplicates) == (0, 1, 0)
    revisions = store.revisions("BTC/USD", 15, "synthetic", first.open_time_utc)
    assert [r.revision for r in revisions] == [1, 2]
    assert [r.candle.close for r in revisions] == [Decimal("100.75"), Decimal("100.80")]


def test_a_then_b_then_a_is_three_revisions(store: CandleStore) -> None:
    run_id = _run(store)
    store.save([_candle(0)], run_id=run_id)
    store.save([_candle(0, close="100.80", receipt_delay=100)], run_id=run_id)
    store.save([_candle(0, receipt_delay=200)], run_id=run_id)
    assert len(store.revisions("BTC/USD", 15, "synthetic", _candle(0).open_time_utc)) == 3


def test_point_in_time_reads_see_the_revision_known_at_as_of(store: CandleStore) -> None:
    run_id = _run(store)
    first = _candle(0)
    store.save([first], run_id=run_id)
    corrected = _candle(0, close="100.80", receipt_delay=3600)
    store.save([corrected], run_id=run_id)
    end = first.close_time_utc
    before = store.window("BTC/USD", 15, "synthetic", end_close_time=end, count=1,
                          as_of=end + timedelta(minutes=10))
    after = store.window("BTC/USD", 15, "synthetic", end_close_time=end, count=1,
                         as_of=end + timedelta(hours=2))
    assert before.slots[0].stored.candle.close == Decimal("100.75")
    assert after.slots[0].stored.candle.close == Decimal("100.80")
    assert after.slots[0].stored.revision == 2


def test_a_candle_received_after_as_of_is_invisible_in_point_in_time_mode(store: CandleStore) -> None:
    run_id = _run(store)
    late = _candle(0, receipt_delay=3600)
    store.save([late], run_id=run_id)
    end = late.close_time_utc
    window = store.window("BTC/USD", 15, "synthetic", end_close_time=end, count=1, as_of=end + timedelta(minutes=30))
    assert window.slots[0].stored is None and window.missing_close_times == (end,)


def test_replay_mode_uses_event_time_and_says_so(store: CandleStore) -> None:
    run_id = _run(store)
    imported = _candle(0, receipt_delay=86400 * 30)  # an archive imported a month later
    store.save([imported], run_id=run_id)
    end = imported.close_time_utc
    window = store.window("BTC/USD", 15, "synthetic", end_close_time=end, count=1, as_of=end, mode="replay")
    assert window.slots[0].stored.candle == imported
    assert window.mode == "replay" and window.availability_basis == "event_time_only"


def test_replay_mode_refuses_a_future_candle(store: CandleStore) -> None:
    with pytest.raises(ValidationFailure):
        store.window("BTC/USD", 15, "synthetic", end_close_time=T0 + timedelta(hours=1), count=1,
                     as_of=T0, mode="replay")


def test_point_in_time_mode_requires_as_of(store: CandleStore) -> None:
    with pytest.raises(ValidationFailure):
        store.window("BTC/USD", 15, "synthetic", end_close_time=T0 + timedelta(hours=1), count=1)


def test_a_receipt_earlier_than_the_stored_revision_is_refused(store: CandleStore) -> None:
    run_id = _run(store)
    store.save([_candle(0, receipt_delay=600)], run_id=run_id)
    with pytest.raises(ValidationFailure):
        store.save([_candle(0, close="100.80", receipt_delay=60)], run_id=run_id)
    assert len(store.revisions("BTC/USD", 15, "synthetic", _candle(0).open_time_utc)) == 1


def test_sources_and_intervals_are_separate_series_that_never_merge(store: CandleStore) -> None:
    run_id = _run(store)
    store.save([_candle(0, source="synthetic"), _candle(0, source="kraken_public_ohlc", close="100.9"),
                _candle(0, interval=5)], run_id=run_id)
    summaries = {(s.symbol, s.source, s.interval_minutes): s.candle_count for s in store.series()}
    assert summaries == {("BTC/USD", "synthetic", 15): 1, ("BTC/USD", "kraken_public_ohlc", 15): 1,
                         ("BTC/USD", "synthetic", 5): 1}
    end = _candle(0).close_time_utc
    synthetic = store.window("BTC/USD", 15, "synthetic", end_close_time=end, count=1, as_of=end + timedelta(hours=1))
    kraken = store.window("BTC/USD", 15, "kraken_public_ohlc", end_close_time=end, count=1,
                          as_of=end + timedelta(hours=1))
    assert synthetic.slots[0].stored.candle.close != kraken.slots[0].stored.candle.close


def test_a_series_cannot_change_its_venue_pair_code(store: CandleStore) -> None:
    run_id = _run(store)
    store.save([_candle(0)], run_id=run_id)
    with pytest.raises(ValidationFailure):
        store.save([_candle(1, pair="XXBTZUSD")], run_id=run_id)


def test_a_batch_is_atomic(store: CandleStore) -> None:
    run_id = _run(store)
    good, bad = _candle(0), _candle(1, pair="OTHER")
    store.save([good], run_id=run_id)
    with pytest.raises(ValidationFailure):
        store.save([_candle(2), bad], run_id=run_id)
    assert [s.candle_count for s in store.series()] == [1]


def test_rows_are_append_only(store: CandleStore) -> None:
    run_id = _run(store)
    store.save([_candle(0)], run_id=run_id)
    with pytest.raises(sqlite3.DatabaseError):
        store.database.execute("UPDATE market_candles SET close='1'")
    with pytest.raises(sqlite3.DatabaseError):
        store.database.execute("DELETE FROM market_candles")
    with pytest.raises(sqlite3.DatabaseError):
        store.database.execute("UPDATE candle_series SET source_pair='X'")
    with pytest.raises(sqlite3.DatabaseError):
        store.database.execute("DELETE FROM candle_runs")


def test_state_survives_a_restart_and_a_second_save_is_deduplicated(tmp_path) -> None:
    path = tmp_path / "m" / "market_data.sqlite"
    first = MarketDataDatabase(path)
    store = CandleStore(first)
    store.save([_candle(i) for i in range(3)], run_id=_run(store))
    first.close()
    second = MarketDataDatabase(path)
    again = CandleStore(second)
    result = again.save([_candle(i) for i in range(3)], run_id=_run(again, at=T0 + timedelta(days=2)))
    assert (result.inserted, result.revised, result.duplicates) == (0, 0, 3)
    second.close()


def test_coverage_reports_first_last_and_every_missing_range(store: CandleStore) -> None:
    run_id = _run(store)
    store.save([_candle(i) for i in (0, 1, 4, 5, 8)], run_id=run_id)
    start = _candle(0).open_time_utc
    end = _candle(9).open_time_utc  # exclusive: slots 0..8
    report = store.coverage("BTC/USD", 15, "synthetic", start_open=start, end_open=end,
                            as_of=end + timedelta(days=1))
    assert report.expected == 9 and report.present == 5
    assert report.first_open_utc == _candle(0).open_time_utc
    assert report.last_close_utc == _candle(8).close_time_utc
    assert [(r.first_open_utc, r.last_open_utc, r.slots) for r in report.missing_ranges] == [
        (_candle(2).open_time_utc, _candle(3).open_time_utc, 2),
        (_candle(6).open_time_utc, _candle(7).open_time_utc, 2),
    ]
    assert report.longest_run == 2
    document = report.to_document()
    assert document["missing_slots"] == 4 and document["missing_ranges"][0]["slots"] == 2


def test_coverage_of_an_unknown_series_is_one_missing_range(store: CandleStore) -> None:
    start = T0
    report = store.coverage("SOL/USD", 15, "synthetic", start_open=start, end_open=start + timedelta(hours=1),
                            as_of=start + timedelta(days=1))
    assert (report.expected, report.present, report.first_open_utc) == (4, 0, None)
    assert [r.slots for r in report.missing_ranges] == [4]


def test_runs_record_start_and_one_result_and_an_interrupted_run_is_visible(store: CandleStore) -> None:
    done = _run(store)
    interrupted = _run(store, at=T0 + timedelta(days=2))
    store.finish_run(done, finished_at=T0 + timedelta(days=1, minutes=1), status="ok", report={"requests": 3})
    with pytest.raises(ValidationFailure):
        store.finish_run(done, finished_at=T0 + timedelta(days=1, minutes=2), status="ok", report={})
    runs = {run.run_id: run for run in store.runs()}
    assert runs[done].status == "ok" and runs[done].report == {"requests": 3}
    assert runs[interrupted].status == "interrupted" and runs[interrupted].report is None
    assert runs[done].evidence_label == "synthetic_fixture" and runs[done].parameters == {"note": "test"}


def test_saving_under_an_unknown_run_is_refused(store: CandleStore) -> None:
    with pytest.raises((ValidationFailure, sqlite3.IntegrityError)):
        store.save([_candle(0)], run_id=999)


def test_oversized_batches_are_refused(store: CandleStore) -> None:
    with pytest.raises(ValidationFailure):
        store.save([_candle(0)] * 50_001, run_id=_run(store))


def test_catalog_observations_deduplicate_unchanged_lists_and_keep_changes(store: CandleStore) -> None:
    run_id = _run(store)
    pairs = [{"symbol": "BTC/USD", "source_pair": "XBTUSD", "status": "online"}]
    first = store.save_catalog(pairs, observed_at=T0, run_id=run_id, rules_version="r1")
    same = store.save_catalog(pairs, observed_at=T0 + timedelta(hours=1), run_id=run_id, rules_version="r1")
    changed = store.save_catalog([{**pairs[0], "status": "cancel_only"}], observed_at=T0 + timedelta(hours=2),
                                 run_id=run_id, rules_version="r1")
    assert (first, same, changed) == (True, False, True)
    latest = store.latest_catalog(as_of=T0 + timedelta(hours=3))
    assert latest.pairs[0]["status"] == "cancel_only" and latest.rules_version == "r1"
    earlier = store.latest_catalog(as_of=T0 + timedelta(minutes=30))
    assert earlier.pairs[0]["status"] == "online"
    assert store.latest_catalog(as_of=T0 - timedelta(days=1)) is None
