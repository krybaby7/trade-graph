"""Importing a locally supplied Kraken OHLCVT archive into the candle store. Synthetic archives, sockets disabled."""

from __future__ import annotations

import hashlib
import os
import socket
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.archive_support import (
    DAY,
    FIRST_OPEN,
    csv_line,
    csv_text,
    synthetic_rows,
    universe_members,
    write_directory,
    write_zip,
)

from trade_graph.adapters.market.kraken_archive import ARCHIVE_SOURCE, ArchiveLimits, ArchiveRejected
from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.import_candle_archive import (
    SURVIVORSHIP_NOTICE,
    UNCONFIRMED_ASSUMPTIONS,
    ImportScope,
    import_archive,
)
from trade_graph.contracts.candles import from_unix
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import ValidationFailure

IMPORT_AT = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
LATER = IMPORT_AT + timedelta(days=1)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any socket use during an import is a failure: the importer reads local files only."""
    def blocked(*args, **kwargs):
        raise AssertionError("network access attempted during an archive import")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture
def store(tmp_path):
    database = MarketDataDatabase(tmp_path / "market" / "market_data.sqlite")
    yield CandleStore(database)
    database.close()


def _zip(tmp_path: Path, members: dict, name: str = "Kraken_OHLCVT_2026Q3.zip") -> Path:
    return write_zip(tmp_path / name, members)


def _rows(count: int, interval: int = 15, **options) -> str:
    return csv_text(synthetic_rows(FIRST_OPEN, count, interval, **options))


def _import(store, path, *, at: datetime = IMPORT_AT, scope: ImportScope | None = None, **options):
    return import_archive(store, FrozenClock(at), path, scope or ImportScope(), evidence_label="synthetic_fixture",
                          **options)


def _window(store, symbol: str, interval: int, count: int, *, as_of: datetime, mode="point_in_time", first=FIRST_OPEN):
    end = from_unix(first + count * interval * 60)
    return store.window(symbol, interval, ARCHIVE_SOURCE, end_close_time=end, count=count, as_of=as_of, mode=mode)


# --- scope and recorded run ---------------------------------------------------------------------------------------


def test_the_default_scope_takes_usd_pairs_at_15_and_60_minutes_and_counts_everything_else(store, tmp_path) -> None:
    members = {
        "MANIFEST.json": "{}", "README.txt": "hello",
        "XBTUSD_15.csv": _rows(8), "XBTUSD_60.csv": _rows(4, 60), "XBTUSD_1.csv": _rows(30, 1),
        "XBTUSD_5.csv": _rows(30, 5), "XBTUSD_240.csv": _rows(2, 240), "XDGUSD_15.csv": _rows(8, seed=2),
        "ADAEUR_15.csv": _rows(8), "ETHXBT_60.csv": _rows(4, 60), "odd.csv": "x",
    }
    result = _import(store, _zip(tmp_path, members))
    assert result.status == "ok"
    series = {(s.symbol, s.interval_minutes): s for s in store.series()}
    assert set(series) == {("BTC/USD", 15), ("BTC/USD", 60), ("DOGE/USD", 15)}
    assert {s.source for s in series.values()} == {ARCHIVE_SOURCE}
    assert series[("BTC/USD", 15)].source_pair == "XBTUSD" and series[("DOGE/USD", 15)].source_pair == "XDGUSD"
    assert series[("BTC/USD", 15)].candle_count == 8
    skipped = result.report["skipped"]
    assert skipped["non_usd_files"] == 2
    assert skipped["interval_not_selected_files"] == {"1": 1, "5": 1, "240": 1}
    assert skipped["unrecognised_members"] == ["README.txt", "odd.csv"] and skipped["unrecognised_member_count"] == 2
    assert result.report["archive"]["manifest_present"] is True
    assert result.report["totals"]["inserted"] == 20


def test_opt_in_intervals_and_a_pair_filter(store, tmp_path) -> None:
    members = {"XBTUSD_1.csv": _rows(30, 1), "XBTUSD_15.csv": _rows(4), "ETHUSD_1.csv": _rows(30, 1, seed=3),
               "ETHUSD_15.csv": _rows(4, seed=3)}
    result = _import(store, _zip(tmp_path, members), scope=ImportScope(intervals=(1, 15), pairs=frozenset({"BTC/USD"})))
    assert {(s.symbol, s.interval_minutes) for s in store.series()} == {("BTC/USD", 1), ("BTC/USD", 15)}
    assert result.report["skipped"]["pair_not_requested_files"] == 2
    assert result.report["scope"] == {"quote": "USD", "intervals": [1, 15], "pairs": ["BTC/USD"]}


def test_scope_validation() -> None:
    with pytest.raises(ValidationFailure):
        ImportScope(intervals=())
    with pytest.raises(ValidationFailure):
        ImportScope(intervals=(7,))
    with pytest.raises(ValidationFailure):
        ImportScope(pairs=frozenset({"btc-usd"}))
    assert ImportScope(intervals=(60, 15, 15)).intervals == (15, 60)


def test_the_run_records_the_file_hash_size_quarter_and_label(store, tmp_path) -> None:
    path = _zip(tmp_path, {"XBTUSD_15.csv": _rows(4)})
    result = _import(store, path)
    (run,) = store.runs()
    assert run.run_id == result.run_id and run.kind == "archive_import" and run.source == ARCHIVE_SOURCE
    assert run.evidence_label == "synthetic_fixture" and run.status == "ok" and run.report == result.report
    assert run.parameters["archive"] == {
        "file_name": "Kraken_OHLCVT_2026Q3.zip", "size_bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "format": "zip"}
    assert run.parameters["quarter"] == "2026Q3"
    assert run.parameters["scope"] == {"quote": "USD", "intervals": [15, 60], "pairs": None}
    assert run.parameters["limits"]["max_rejected_share"] == "0.01"


def test_a_real_looking_import_defaults_to_the_local_archive_evidence_label(store, tmp_path) -> None:
    import_archive(store, FrozenClock(IMPORT_AT), _zip(tmp_path, {"XBTUSD_15.csv": _rows(4)}))
    assert store.runs()[0].evidence_label == "local_archive_file"


@pytest.mark.parametrize(("file_name", "given", "quarter", "kind", "source"), [
    ("Kraken_OHLCVT_2026Q3.zip", None, "2026Q3", "quarterly", "file_name"),
    ("Kraken_OHLCVT_Q2_2026.zip", None, "2026Q2", "quarterly", "file_name"),
    ("kraken-2025-q4-update.zip", None, "2025Q4", "quarterly", "file_name"),
    ("Kraken_OHLCVT_Full.zip", None, None, "complete", None),
    ("download.zip", None, None, "unknown", None),
    ("download.zip", "2026Q1", "2026Q1", "quarterly", "argument"),
    ("Kraken_OHLCVT_2026Q3.zip", "2026Q2", "2026Q2", "quarterly", "argument"),
])
def test_the_quarter_comes_from_the_name_or_the_caller(store, tmp_path, file_name, given, quarter, kind,
                                                       source) -> None:
    result = _import(store, _zip(tmp_path, {"XBTUSD_15.csv": _rows(2)}, file_name), quarter=given)
    archive = result.report["archive"]
    assert (archive["quarter"], archive["kind"], archive["quarter_source"]) == (quarter, kind, source)
    assert store.runs()[0].parameters["quarter"] == quarter


def test_a_malformed_quarter_argument_is_refused_before_anything_is_recorded(store, tmp_path) -> None:
    with pytest.raises(ValidationFailure):
        _import(store, _zip(tmp_path, {"XBTUSD_15.csv": _rows(2)}), quarter="Q3")
    assert store.runs() == []


def test_rows_outside_the_stated_quarter_are_counted_not_hidden(store, tmp_path) -> None:
    before_quarter = FIRST_OPEN - 4 * 900
    lines = synthetic_rows(before_quarter, 6, 15)  # four rows before 2026-07-01, two inside
    result = _import(store, _zip(tmp_path, {"XBTUSD_15.csv": csv_text(lines)}))
    assert result.report["totals"]["rows_outside_quarter"] == 4
    assert result.report["series"][0]["rows_outside_quarter"] == 4


def test_a_directory_of_csv_files_imports_like_a_zip(store, tmp_path) -> None:
    folder = write_directory(tmp_path / "csv_dir", {"XBTUSD_15.csv": _rows(6), "MANIFEST.json": "{}"})
    result = _import(store, folder)
    assert result.report["archive"]["format"] == "directory" and result.report["totals"]["inserted"] == 6


# --- exactness and availability -----------------------------------------------------------------------------------


def test_candles_are_exact_with_no_vwap_and_the_archive_source(store, tmp_path) -> None:
    line = csv_line(FIRST_OPEN, "100.10", "101", "99.5", "100.75", "12.50000000", 7)
    _import(store, _zip(tmp_path, {"XBTUSD_15.csv": csv_text([line])}))
    (stored,) = store.revisions("BTC/USD", 15, ARCHIVE_SOURCE, from_unix(FIRST_OPEN))
    candle = stored.candle
    assert (candle.open, candle.high, candle.low, candle.close) == (
        Decimal("100.10"), Decimal("101"), Decimal("99.5"), Decimal("100.75"))
    assert candle.volume == Decimal("12.5") and candle.trade_count == 7 and candle.vwap is None
    assert candle.source == ARCHIVE_SOURCE and candle.source_pair == "XBTUSD"
    assert candle.close_time_utc == from_unix(FIRST_OPEN + 900)


def test_availability_is_the_import_time_not_the_historical_close(store, tmp_path) -> None:
    result = _import(store, _zip(tmp_path, {"XBTUSD_15.csv": _rows(6)}))
    window = _window(store, "BTC/USD", 15, 6, as_of=IMPORT_AT)
    assert window.complete
    assert {slot.stored.candle.available_at_utc for slot in window.slots} == {IMPORT_AT}
    assert result.report["imported_at_utc"] == "2026-10-14T09:00:00.000000Z"


def test_point_in_time_reads_before_the_import_see_nothing_and_replay_sees_the_data(store, tmp_path) -> None:
    _import(store, _zip(tmp_path, {"XBTUSD_15.csv": _rows(6)}))
    last_close = from_unix(FIRST_OPEN + 6 * 900)
    before = _window(store, "BTC/USD", 15, 6, as_of=IMPORT_AT - timedelta(seconds=1))
    assert before.candles == () and len(before.missing_close_times) == 6
    assert _window(store, "BTC/USD", 15, 6, as_of=IMPORT_AT).complete
    replay = _window(store, "BTC/USD", 15, 6, as_of=last_close, mode="replay")
    assert replay.complete and replay.availability_basis == "event_time_only"


def test_rows_that_close_after_the_import_time_are_dropped_and_counted(store, tmp_path) -> None:
    now = from_unix(FIRST_OPEN + 8 * 900)  # eight candles are complete, exactly at their close
    result = _import(store, _zip(tmp_path, {"XBTUSD_15.csv": _rows(10)}), at=now)
    assert result.report["totals"]["dropped_unfinished"] == 2
    assert result.status == "partial"
    (series,) = store.series()
    assert series.candle_count == 8 and result.report["series"][0]["dropped_unfinished"] == 2
    assert result.report["series"][0]["last_open_utc"] == "2026-07-01T01:45:00.000000Z"


# --- bad rows and whole-file refusal ------------------------------------------------------------------------------


def test_a_few_bad_rows_are_counted_by_reason_and_the_rest_imports(store, tmp_path) -> None:
    lines = synthetic_rows(FIRST_OPEN, 300, 15)
    lines[10] = lines[10].replace(",", ";", 2)  # malformed
    lines[20] = lines[20].split(",", 1)[0] + ",0,1,1,1,1,1"  # a zero open price
    lines[30] = lines[29]  # a repeated timestamp
    result = _import(store, _zip(tmp_path, {"XBTUSD_15.csv": csv_text(lines)}))
    series = result.report["series"][0]
    assert series["rejected_by_reason"] == {"duplicate_timestamp": 1, "malformed_row": 1, "non_positive_price": 1}
    assert (series["rows_read"], series["accepted"], series["inserted"]) == (300, 297, 297)
    assert result.status == "partial" and result.report["rejected_by_reason"] == series["rejected_by_reason"]
    assert result.report["totals"]["rejected"] == 3 and result.report["rejected_share"] == "0.010000"


def test_a_file_with_too_many_bad_rows_is_refused_whole_and_stores_nothing(store, tmp_path) -> None:
    good = synthetic_rows(FIRST_OPEN, 200, 15)
    bad = ["not,a,row"] * 7
    path = _zip(tmp_path, {"XBTUSD_15.csv": csv_text(good + bad), "ETHUSD_15.csv": _rows(50, seed=4)})
    with pytest.raises(ArchiveRejected) as refused:
        _import(store, path)
    assert refused.value.reason == "rejected_share_exceeded"
    assert store.series() == []
    (run,) = store.runs()
    assert run.status == "failed" and run.report["reason"] == "rejected_share_exceeded"
    assert refused.value.run_id == run.run_id and refused.value.report == run.report
    assert Decimal(run.report["rejected_share"]) > Decimal("0.01")


def test_the_rejection_bound_is_configurable(store, tmp_path) -> None:
    lines = synthetic_rows(FIRST_OPEN, 100, 15) + ["not,a,row"] * 10
    path = _zip(tmp_path, {"XBTUSD_15.csv": csv_text(lines)})
    with pytest.raises(ArchiveRejected):
        _import(store, path)
    result = _import(store, path, limits=ArchiveLimits(max_rejected_share=Decimal("0.5")))
    assert result.status == "partial" and result.report["totals"]["inserted"] == 100


def test_a_file_with_no_readable_rows_is_refused(store, tmp_path) -> None:
    with pytest.raises(ArchiveRejected) as refused:
        _import(store, _zip(tmp_path, {"XBTUSD_15.csv": ""}))
    assert refused.value.reason == "no_rows"


def test_an_archive_with_nothing_in_scope_is_refused_and_says_what_it_held(store, tmp_path) -> None:
    path = _zip(tmp_path, {"ADAEUR_15.csv": _rows(3), "XBTUSD_1.csv": _rows(3, 1), "MANIFEST.json": "{}"})
    with pytest.raises(ArchiveRejected) as refused:
        _import(store, path)
    assert refused.value.reason == "nothing_in_scope"
    report = refused.value.report
    assert report["skipped"]["non_usd_files"] == 1 and report["skipped"]["interval_not_selected_files"] == {"1": 1}
    assert store.runs()[0].status == "failed"


def test_two_files_mapping_to_one_series_are_refused_as_ambiguous(store, tmp_path) -> None:
    path = _zip(tmp_path, {"XBTUSD_15.csv": _rows(3), "XXBTZUSD_15.csv": _rows(3)})
    with pytest.raises(ArchiveRejected) as refused:
        _import(store, path)
    assert refused.value.reason == "ambiguous_pair_codes" and store.series() == []


def test_an_unsafe_archive_is_refused_with_a_failed_run_and_nothing_written(store, tmp_path) -> None:
    path = tmp_path / "evil.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("XBTUSD_15.csv", _rows(3))
        archive.writestr(zipfile.ZipInfo("../../etc/XBTUSD_60.csv"), _rows(3, 60))
    before = sorted(os.listdir(tmp_path))
    with pytest.raises(ArchiveRejected) as refused:
        _import(store, path)
    assert refused.value.reason == "unsafe_member_name"
    assert store.series() == [] and store.runs()[0].status == "failed"
    assert sorted(os.listdir(tmp_path)) == before
    assert not (tmp_path.parent / "etc").exists()


def test_archive_limits_apply_through_the_importer(store, tmp_path) -> None:
    path = _zip(tmp_path, {"XBTUSD_15.csv": _rows(50)})
    with pytest.raises(ArchiveRejected) as refused:
        _import(store, path, limits=ArchiveLimits(max_rows=10))
    assert refused.value.reason == "row_limit" and store.series() == []
    with pytest.raises(ArchiveRejected) as big:
        _import(store, path, limits=ArchiveLimits(max_total_bytes=500, max_member_bytes=400))
    assert big.value.reason in {"member_size_limit", "total_size_limit"}


def test_a_missing_file_raises_without_recording_a_run(store, tmp_path) -> None:
    with pytest.raises(ArchiveRejected) as refused:
        _import(store, tmp_path / "absent.zip")
    assert refused.value.reason == "not_a_file" and store.runs() == []
    assert refused.value.run_id is None and refused.value.report is None


def test_a_multipart_piece_is_refused_before_any_run_exists(store, tmp_path) -> None:
    piece = tmp_path / "Kraken_OHLCVT_Full.zip.part00"
    piece.write_bytes(b"PK\x03\x04 not a whole archive")
    with pytest.raises(ArchiveRejected) as refused:
        _import(store, piece)
    assert refused.value.reason == "multipart_archive" and store.runs() == []
    assert refused.value.run_id is None and refused.value.report is None


# --- idempotence and changed archives -----------------------------------------------------------------------------


def test_importing_the_same_archive_again_inserts_nothing_and_keeps_the_original_receipts(store, tmp_path) -> None:
    path = _zip(tmp_path, {"XBTUSD_15.csv": _rows(10), "XBTUSD_60.csv": _rows(5, 60)})
    first = _import(store, path)
    second = _import(store, path, at=LATER)
    totals = second.report["totals"]
    assert (totals["inserted"], totals["revisions"], totals["duplicates"]) == (0, 0, 15)
    assert first.report["totals"]["inserted"] == 15
    assert second.report["same_file_seen_in_runs"] == [first.run_id]
    assert first.report["same_file_seen_in_runs"] == []
    window = _window(store, "BTC/USD", 15, 10, as_of=LATER)
    assert {slot.stored.candle.available_at_utc for slot in window.slots} == {IMPORT_AT}
    assert {slot.stored.revision for slot in window.slots} == {1}
    assert len(store.runs()) == 2


def test_a_changed_archive_creates_revisions_only_for_the_changed_rows(store, tmp_path) -> None:
    rows = [csv_line(FIRST_OPEN + i * 900, "100", "102", "98", "101", "5", 3) for i in range(6)]
    first = _import(store, _zip(tmp_path, {"XBTUSD_15.csv": csv_text(rows)}, "Kraken_OHLCVT_2026Q3.zip"))
    changed = [*rows[:2], csv_line(FIRST_OPEN + 2 * 900, "100", "102", "98", "100.5", "5", 3), *rows[3:]]
    extra = csv_line(FIRST_OPEN + 6 * 900, "100", "102", "98", "101", "5", 3)
    second = _import(store, _zip(tmp_path, {"XBTUSD_15.csv": csv_text([*changed, extra])}, "Kraken_OHLCVT_2026Q3b.zip"),
                     at=LATER)
    totals = second.report["totals"]
    assert (totals["inserted"], totals["revisions"], totals["duplicates"]) == (1, 1, 5)
    assert first.report["archive"]["sha256"] != second.report["archive"]["sha256"]
    revisions = store.revisions("BTC/USD", 15, ARCHIVE_SOURCE, from_unix(FIRST_OPEN + 2 * 900))
    assert [r.candle.close for r in revisions] == [Decimal("101"), Decimal("100.5")]
    assert [r.candle.available_at_utc for r in revisions] == [IMPORT_AT, LATER]
    assert len(store.revisions("BTC/USD", 15, ARCHIVE_SOURCE, from_unix(FIRST_OPEN + 1 * 900))) == 1
    seen_then = _window(store, "BTC/USD", 15, 3, as_of=IMPORT_AT + timedelta(hours=1))
    seen_now = _window(store, "BTC/USD", 15, 3, as_of=LATER)
    assert seen_then.slots[2].stored.candle.close == Decimal("101")
    assert seen_now.slots[2].stored.candle.close == Decimal("100.5")


def _quarter_hour_rows(count: int) -> list[str]:
    return [csv_line(FIRST_OPEN + i * 900, "100", "102", "98", "101", "5", 3) for i in range(count)]


def test_a_corrected_file_that_drops_a_row_does_not_look_clean(store, tmp_path) -> None:
    """The store is append-only, so the dropped row's candle stays; the report must say that it is not in the file."""
    rows = _quarter_hour_rows(20)
    first = _import(store, _zip(tmp_path, {"XBTUSD_15.csv": csv_text(rows)}))
    assert first.status == "ok" and first.report["series"][0]["stored_slots_not_in_file"] == 0
    again = _import(store, _zip(tmp_path, {"XBTUSD_15.csv": csv_text(rows)}, "a.zip"), at=LATER)
    assert again.status == "ok" and again.report["totals"]["stored_slots_not_in_file"] == 0
    corrected = _import(store, _zip(tmp_path, {"XBTUSD_15.csv": csv_text(rows[:5] + rows[6:])}, "b.zip"),
                        at=LATER + timedelta(days=1))
    series = corrected.report["series"][0]
    assert corrected.status == "partial" and (series["accepted"], series["duplicates"]) == (19, 19)
    assert series["stored_slots_not_in_file"] == 1 and corrected.report["totals"]["stored_slots_not_in_file"] == 1
    assert series["coverage"]["present_slots"] == 20 and series["coverage"]["missing_slots"] == 0
    assert store.runs()[-1].status == "partial" and store.runs()[-1].report["status"] == "partial"


def test_files_that_only_touch_or_overlap_the_stored_range_are_not_flagged(store, tmp_path) -> None:
    rows = _quarter_hour_rows(20)
    _import(store, _zip(tmp_path, {"XBTUSD_15.csv": csv_text(rows[:10])}))
    after = _import(store, _zip(tmp_path, {"XBTUSD_15.csv": csv_text(rows[10:])}, "b.zip"), at=LATER)
    overlapping = _import(store, _zip(tmp_path, {"XBTUSD_15.csv": csv_text(rows[5:15])}, "c.zip"), at=LATER)
    assert after.status == "ok" and overlapping.status == "ok"
    assert after.report["totals"]["stored_slots_not_in_file"] == overlapping.report["totals"][
        "stored_slots_not_in_file"] == 0


def test_a_file_missing_several_stored_slots_counts_them_all(store, tmp_path) -> None:
    rows = _quarter_hour_rows(20)
    _import(store, _zip(tmp_path, {"XBTUSD_15.csv": csv_text(rows[:10])}))
    gappy = rows[:4] + rows[8:20]  # 4 to 7 are stored from the first file but absent from this one
    result = _import(store, _zip(tmp_path, {"XBTUSD_15.csv": csv_text(gappy)}, "b.zip"), at=LATER)
    assert result.report["series"][0]["stored_slots_not_in_file"] == 4 and result.status == "partial"


def test_a_changed_row_imported_with_an_earlier_clock_fails_loudly_and_records_the_failure(store, tmp_path) -> None:
    rows = [csv_line(FIRST_OPEN + i * 900, "100", "102", "98", "101", "5", 3) for i in range(3)]
    _import(store, _zip(tmp_path, {"XBTUSD_15.csv": csv_text(rows)}))
    changed = [rows[0], csv_line(FIRST_OPEN + 900, "100", "102", "98", "100.5", "5", 3), rows[2]]
    with pytest.raises(ArchiveRejected) as refused:
        _import(store, _zip(tmp_path, {"XBTUSD_15.csv": csv_text(changed)}, "b.zip"), at=IMPORT_AT - timedelta(days=1))
    assert refused.value.reason == "store_refused" and isinstance(refused.value, ValidationFailure)
    assert [run.status for run in store.runs()] == ["ok", "failed"]
    assert store.runs()[1].report["reason"] == "store_refused" and refused.value.run_id == store.runs()[1].run_id


# --- the file changes while it is being imported ------------------------------------------------------------------


class _TamperingClock:
    """Returns a fixed time and rewrites the archive the second time it is asked (between the two passes)."""

    def __init__(self, now: datetime, tamper) -> None:
        self._now, self._tamper, self.calls = now, tamper, 0

    def now(self) -> datetime:
        self.calls += 1
        if self.calls == 2:
            self._tamper()
        return self._now


def test_a_file_replaced_between_reading_and_writing_is_refused(store, tmp_path) -> None:
    path = _zip(tmp_path, {"XBTUSD_15.csv": _rows(5)})
    clock = _TamperingClock(IMPORT_AT, lambda: write_zip(path, {"XBTUSD_15.csv": _rows(9)}))
    with pytest.raises(ArchiveRejected) as refused:
        import_archive(store, clock, path, ImportScope(), evidence_label="synthetic_fixture")
    assert refused.value.reason == "changed_during_import" and store.series() == []
    assert store.runs()[0].status == "failed"


def test_same_size_content_changed_with_the_timestamp_restored_is_still_caught(store, tmp_path) -> None:
    folder = write_directory(tmp_path / "csv", {"XBTUSD_15.csv": _rows(5)})
    target = folder / "XBTUSD_15.csv"

    def tamper() -> None:
        status = target.stat()
        text = target.read_text().replace("\n", "\nx", 1)[:-1]  # same length, now one malformed line
        target.write_text(text)
        os.utime(target, ns=(status.st_atime_ns, status.st_mtime_ns))

    clock = _TamperingClock(IMPORT_AT, tamper)
    with pytest.raises(ArchiveRejected) as refused:
        import_archive(store, clock, folder, ImportScope(), evidence_label="synthetic_fixture")
    assert refused.value.reason == "changed_during_import" and store.series() == []


def _rewritten_after_the_first_save(store, monkeypatch, tamper) -> list[int]:
    """Runs ``tamper`` right after the store's first save, that is, between the two passes' reads of later members."""
    real_save, sizes = store.save, []

    def save(candles, *, run_id):
        sizes.append(len(candles))
        saved = real_save(candles, run_id=run_id)
        if len(sizes) == 1:
            tamper()
        return saved

    monkeypatch.setattr(store, "save", save)
    return sizes


def _two_member_directory(tmp_path, closes: str = "100") -> Path:
    rows = [csv_line(FIRST_OPEN + i * 900, "100", "102", "98", closes, "5", 3) for i in range(6)]
    return write_directory(tmp_path / "csv", {"ADAUSD_15.csv": _rows(6, seed=2), "XBTUSD_15.csv": csv_text(rows)})


def test_a_same_size_rewrite_between_two_batches_never_reaches_the_store(store, tmp_path, monkeypatch) -> None:
    """Counts stay equal and the signature is only compared before and after the write pass; the rows are not."""
    import trade_graph.application.import_candle_archive as importer
    monkeypatch.setattr(importer, "BATCH_CANDLES", 2)
    folder = _two_member_directory(tmp_path)
    target = folder / "XBTUSD_15.csv"
    sizes = _rewritten_after_the_first_save(
        store, monkeypatch, lambda: target.write_text(target.read_text().replace(",100,5,", ",101,5,")))
    with pytest.raises(ArchiveRejected) as refused:
        _import(store, folder)
    assert refused.value.reason == "changed_during_import" and sizes[0] == 2
    assert {s.symbol for s in store.series()} == {"ADA/USD"}  # BTC/USD would hold six candles closing at 101
    assert store.runs()[0].status == "failed"


def test_a_same_size_rewrite_of_a_member_smaller_than_one_batch_is_caught_before_its_only_save(
        store, tmp_path, monkeypatch) -> None:
    folder = _two_member_directory(tmp_path)
    target = folder / "XBTUSD_15.csv"
    _rewritten_after_the_first_save(
        store, monkeypatch, lambda: target.write_text(target.read_text().replace(",100,5,", ",101,5,")))
    with pytest.raises(ArchiveRejected) as refused:
        _import(store, folder)
    assert refused.value.reason == "changed_during_import"
    assert {s.symbol for s in store.series()} == {"ADA/USD"}


def test_rows_appended_to_a_member_during_the_import_are_not_stored(store, tmp_path, monkeypatch) -> None:
    import trade_graph.application.import_candle_archive as importer
    monkeypatch.setattr(importer, "BATCH_CANDLES", 2)
    folder = _two_member_directory(tmp_path)
    target = folder / "XBTUSD_15.csv"
    extra = [csv_line(FIRST_OPEN + i * 900, "100", "102", "98", "100", "5", 3) for i in range(6, 11)]
    _rewritten_after_the_first_save(
        store, monkeypatch, lambda: target.write_text(target.read_text() + csv_text(extra)))
    with pytest.raises(ArchiveRejected) as refused:
        _import(store, folder)
    assert refused.value.reason == "changed_during_import"
    held = [s for s in store.series() if s.symbol == "BTC/USD"]
    # the first six rows read the same in both passes and may be stored; the appended ones must not be
    assert sum(s.candle_count for s in held) == 6


def test_a_rewrite_that_leaves_every_row_alone_is_not_an_error(store, tmp_path, monkeypatch) -> None:
    import trade_graph.application.import_candle_archive as importer
    monkeypatch.setattr(importer, "BATCH_CANDLES", 2)
    folder = _two_member_directory(tmp_path)
    target = folder / "XBTUSD_15.csv"
    status = target.stat()

    def rewrite_identically() -> None:
        target.write_bytes(target.read_bytes())
        os.utime(target, ns=(status.st_atime_ns, status.st_mtime_ns))

    _rewritten_after_the_first_save(store, monkeypatch, rewrite_identically)
    result = _import(store, folder)
    assert result.status == "ok" and result.report["totals"]["inserted"] == 12


# --- coverage, missing pairs and the survivorship statement -------------------------------------------------------


def test_each_series_reports_its_coverage_and_a_planted_gap(store, tmp_path) -> None:
    skip = {("XBTUSD", 15): range(100, 108)}  # two hours of quiet slots
    members = universe_members(["XBTUSD"], days=10, intervals=(15,), skip=skip)
    result = _import(store, _zip(tmp_path, members))
    (series,) = result.report["series"]
    coverage = series["coverage"]
    assert coverage["expected_slots"] == 960 and coverage["present_slots"] == 952 and coverage["missing_slots"] == 8
    assert coverage["mode"] == "replay" and coverage["missing_ranges_total"] == 1
    assert coverage["missing_ranges"] == [{
        "first_open_utc": "2026-07-02T01:00:00.000000Z", "last_open_utc": "2026-07-02T02:45:00.000000Z", "slots": 8}]
    assert coverage["longest_run_slots"] == 852 and coverage["first_open_utc"] == "2026-07-01T00:00:00.000000Z"
    assert "no candle" in coverage["note"] and "not" in coverage["note"]
    assert (series["first_open_utc"], series["last_open_utc"]) == (
        "2026-07-01T00:00:00.000000Z", "2026-07-10T23:45:00.000000Z")


def test_long_gap_lists_are_capped_but_counted(store, tmp_path, monkeypatch) -> None:
    import trade_graph.application.import_candle_archive as importer
    monkeypatch.setattr(importer, "MAX_GAPS_LISTED", 3)
    skip = {("XBTUSD", 15): [i for i in range(1, 40, 2)]}  # twenty single-slot gaps
    result = _import(store, _zip(tmp_path, universe_members(["XBTUSD"], days=1, intervals=(15,), skip=skip)))
    coverage = result.report["series"][0]["coverage"]
    assert coverage["missing_ranges_total"] == 20 and len(coverage["missing_ranges"]) == 3
    assert coverage["missing_ranges_truncated"] is True and coverage["missing_slots"] == 20


def test_coverage_in_chunks_matches_one_pass(store, tmp_path, monkeypatch) -> None:
    import trade_graph.application.import_candle_archive as importer
    skip = {("XBTUSD", 15): [5, 6, 7, 20, 21, 50]}
    path = _zip(tmp_path, universe_members(["XBTUSD"], days=1, intervals=(15,), skip=skip))
    whole = _import(store, path).report["series"][0]["coverage"]
    monkeypatch.setattr(importer, "COVERAGE_CHUNK_SLOTS", 6)
    chunked = _import(store, path, at=LATER).report["series"][0]["coverage"]
    assert chunked == whole
    assert [r["slots"] for r in whole["missing_ranges"]] == [3, 2, 1]


def test_expected_pairs_missing_from_the_archive_are_named(store, tmp_path) -> None:
    members = {"XBTUSD_15.csv": _rows(4), "XBTUSD_60.csv": _rows(2, 60), "ETHUSD_15.csv": _rows(4, seed=2)}
    result = _import(store, _zip(tmp_path, members), expected_pairs=["BTC/USD", "ETH/USD", "SOL/USD", "XDGUSD"])
    expected = result.report["expected_pairs"]
    assert expected["supplied"] is True and expected["count"] == 4
    assert expected["missing"] == ["DOGE/USD", "SOL/USD"]
    assert expected["missing_intervals"] == {"ETH/USD": [60]}


def test_without_an_expected_list_the_report_says_it_cannot_name_missing_pairs(store, tmp_path) -> None:
    result = _import(store, _zip(tmp_path, {"XBTUSD_15.csv": _rows(4)}))
    expected = result.report["expected_pairs"]
    assert expected["supplied"] is False and expected["missing"] is None


def test_the_report_always_states_the_survivorship_bias(store, tmp_path) -> None:
    result = _import(store, _zip(tmp_path, {"XBTUSD_15.csv": _rows(4)}))
    assert result.report["survivorship_notice"] == SURVIVORSHIP_NOTICE
    assert "delisted" in SURVIVORSHIP_NOTICE.lower() and "optimistic" in SURVIVORSHIP_NOTICE
    assert store.runs()[0].report["survivorship_notice"] == SURVIVORSHIP_NOTICE
    assert result.report["assumptions"] == list(UNCONFIRMED_ASSUMPTIONS)
    refusal = None
    try:
        _import(store, _zip(tmp_path, {"ADAEUR_15.csv": _rows(3)}, "again.zip"))
    except ArchiveRejected as error:
        refusal = error.report
    assert refusal is not None and refusal["survivorship_notice"] == SURVIVORSHIP_NOTICE


# --- end to end ---------------------------------------------------------------------------------------------------


def test_three_months_of_15_minute_history_for_a_candidate_universe(store, tmp_path, monkeypatch) -> None:
    def refuse(*args, **kwargs):
        raise AssertionError("the importer must never extract an archive")

    monkeypatch.setattr(zipfile.ZipFile, "extract", refuse)
    monkeypatch.setattr(zipfile.ZipFile, "extractall", refuse)
    codes = ["XBTUSD", "ETHUSD", "XDGUSD", "SOLUSD", "ADAUSD"]
    gap = {("SOLUSD", 15): range(3000, 3040)}  # ten hours missing from one candidate
    members = universe_members(codes, days=95, skip=gap)
    members["SOLEUR_15.csv"] = _rows(10)
    path = _zip(tmp_path, members, "Kraken_OHLCVT_2026Q3.zip")
    before = sorted(os.listdir(tmp_path))
    expected = ["BTC/USD", "ETH/USD", "DOGE/USD", "SOL/USD", "ADA/USD", "LUNA/USD"]
    result = _import(store, path, expected_pairs=expected)
    assert sorted(os.listdir(tmp_path)) == before  # nothing extracted next to it
    assert result.status == "ok"
    by_key = {(s["symbol"], s["interval_minutes"]): s for s in result.report["series"]}
    assert len(by_key) == 10  # five pairs at 15 and 60 minutes
    days_15 = 95 * 96
    for symbol in ("BTC/USD", "ETH/USD", "DOGE/USD", "ADA/USD"):
        coverage = by_key[(symbol, 15)]["coverage"]
        assert coverage["present_slots"] == days_15 and coverage["missing_slots"] == 0
        assert by_key[(symbol, 15)]["inserted"] == days_15
    sol = by_key[("SOL/USD", 15)]["coverage"]
    assert sol["present_slots"] == days_15 - 40 and sol["missing_ranges_total"] == 1
    assert sol["missing_ranges"][0]["slots"] == 40
    assert result.report["expected_pairs"]["missing"] == ["LUNA/USD"]
    assert result.report["skipped"]["non_usd_files"] == 1
    assert sum(1 for k in by_key if k[1] == 15) == 5

    # Three months replayed in one read, the way the practice range will use it.
    three_months = 92 * 96
    window = _window(store, "BTC/USD", 15, three_months, as_of=from_unix(FIRST_OPEN + DAY * 92), mode="replay")
    assert window.complete and len(window.candles) == three_months
    sol_window = _window(store, "SOL/USD", 15, days_15, as_of=from_unix(FIRST_OPEN + DAY * 95), mode="replay")
    assert len(sol_window.missing_close_times) == 40
