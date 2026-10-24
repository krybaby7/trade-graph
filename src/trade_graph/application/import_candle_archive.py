"""Import a locally supplied Kraken OHLCVT archive into the candle store (T26 slice A, docs/KRAKEN-ARCHIVE-IMPORT.md).

The importer reads one local file and never touches the network. It makes two passes over the in-scope members:

1. Scan: parse and validate every row, count rejections, and decide whether the file is trustworthy at all. Nothing
   is written, so a refused file leaves the store untouched.
2. Write: after the scan the clock is read once; that moment is the receipt time of every row (the file was obtained
   by this process then, not at the historical close). Rows closing after it are dropped and counted.

Idempotence and revisions are the candle store's own: identical content is a duplicate that keeps its original
receipt; changed content becomes the next revision. One run record holds the file name, size, SHA-256, quarter and
the full per-series report.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from trade_graph.adapters.market.kraken_archive import (
    ARCHIVE_SOURCE,
    ArchiveFingerprint,
    ArchiveLimits,
    ArchiveMember,
    ArchiveRejected,
    ArchiveRow,
    MemberReader,
    ReadBudget,
    archive_signature,
    fingerprint_archive,
    is_fiat_symbol,
    open_archive,
    symbol_for_pair_code,
)
from trade_graph.application.candle_store import CandleStore
from trade_graph.contracts.candles import CANDLE_INTERVALS, EVIDENCE_LABELS, Candle, from_unix, to_unix
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import ValidationFailure

IMPORTER_VERSION = "archive-import-v1"
DEFAULT_INTERVALS = (15, 60)
QUOTE = "USD"
BATCH_CANDLES = 10_000
MAX_GAPS_LISTED = 25
MAX_NAMES_LISTED = 25
COVERAGE_CHUNK_SLOTS = 4_000_000

SURVIVORSHIP_NOTICE = (
    "Delisted pairs are absent from Kraken's data, so any test of coin selection on this history is optimistic "
    "(survivorship bias)."
)
UNCONFIRMED_ASSUMPTIONS = (
    "Row timestamps are candle OPEN times in Unix seconds; Kraken's download page states neither the unit nor "
    "the convention, so check this against the collector's candles where the two overlap.",
    "Every row is a completed candle; Kraken's page does not say so. Rows closing after the import time are "
    "dropped and counted.",
    "Files are named PAIR_INTERVAL.csv at the top level of the archive, headerless, rows in ascending time order; "
    "the page gives no example. Anything else is counted as rejected or refuses the whole file.",
)
GAP_NOTE = (
    "A missing slot means no candle is stored for it. Kraken's archive has no row for an interval without trades, "
    "and this report cannot tell that apart from a row that is absent from the file; no slot is filled or guessed."
)

_SYMBOL = re.compile(r"[A-Z0-9]{1,16}/USD")
_QUARTER_EXACT = re.compile(r"(20[0-9]{2})Q([1-4])")
_QUARTER_YEAR_FIRST = re.compile(r"(?<![0-9])(20[0-9]{2})[-_ ]?[Qq]([1-4])(?![0-9])")
_QUARTER_QUARTER_FIRST = re.compile(r"(?<![0-9A-Za-z])[Qq]([1-4])[-_ ]?(20[0-9]{2})(?![0-9])")


@dataclass(frozen=True)
class ImportScope:
    """USD-quoted pairs at the chosen intervals; ``pairs`` limits it to symbols such as ``BTC/USD``."""

    intervals: tuple[int, ...] = DEFAULT_INTERVALS
    pairs: frozenset[str] | None = None

    def __post_init__(self) -> None:
        intervals = tuple(sorted(set(self.intervals)))
        if not intervals or any(type(value) is not int or value not in CANDLE_INTERVALS for value in intervals):
            raise ValidationFailure(f"intervals must be a non-empty subset of {CANDLE_INTERVALS}")
        object.__setattr__(self, "intervals", intervals)
        if self.pairs is not None:
            pairs = frozenset(self.pairs)
            if not pairs or any(not _SYMBOL.fullmatch(symbol) for symbol in pairs):
                raise ValidationFailure("pairs must be a non-empty set of USD symbols such as BTC/USD")
            object.__setattr__(self, "pairs", pairs)

    def document(self) -> dict[str, Any]:
        return {"quote": QUOTE, "intervals": list(self.intervals),
                "pairs": None if self.pairs is None else sorted(self.pairs)}


@dataclass(frozen=True, slots=True)
class ImportResult:
    run_id: int
    status: str
    report: dict[str, Any]


def normalise_pair_names(names: Iterable[str]) -> frozenset[str]:
    """USD symbols from a mix of 'BTC/USD', 'XBT/USD' and 'XBTUSD' style names."""
    symbols = set()
    for name in names:
        symbol = symbol_for_pair_code(str(name).strip().upper().replace("/", ""))
        if symbol is None:
            raise ValidationFailure(f"not a USD-quoted pair name: {str(name)[:40]!r}")
        if is_fiat_symbol(symbol):
            raise ValidationFailure(f"{symbol} is a currency pair, not a coin; currency pairs are never stored")
        symbols.add(symbol)
    return frozenset(symbols)


# --- archive naming ------------------------------------------------------------------------------------------------


def resolve_quarter(file_name: str, given: str | None) -> tuple[str | None, str | None, str]:
    """(quarter label, where it came from, archive kind). The quarter is metadata; it never changes what is read."""
    if given is not None:
        found = _QUARTER_EXACT.fullmatch(given.strip().upper())
        if found is None:
            raise ValidationFailure("quarter must look like 2026Q3")
        return f"{found.group(1)}Q{found.group(2)}", "argument", "quarterly"
    year_first = _QUARTER_YEAR_FIRST.search(file_name)
    if year_first is not None:
        return f"{year_first.group(1)}Q{year_first.group(2)}", "file_name", "quarterly"
    quarter_first = _QUARTER_QUARTER_FIRST.search(file_name)
    if quarter_first is not None:
        return f"{quarter_first.group(2)}Q{quarter_first.group(1)}", "file_name", "quarterly"
    return None, None, "complete" if "full" in file_name.lower() else "unknown"


def _quarter_bounds(label: str | None) -> tuple[int, int] | None:
    if label is None:
        return None
    year, quarter = int(label[:4]), int(label[5])
    start = datetime(year, 3 * quarter - 2, 1, tzinfo=UTC)
    end = datetime(year + 1, 1, 1, tzinfo=UTC) if quarter == 4 else datetime(year, 3 * quarter + 1, 1, tzinfo=UTC)
    return to_unix(start), to_unix(end)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationFailure("the import clock must return timezone-aware times")
    return value.astimezone(UTC)


def _limits_document(limits: ArchiveLimits) -> dict[str, Any]:
    return {key: str(value) if isinstance(value, Decimal) else value
            for key, value in dataclasses.asdict(limits).items()}


# --- selecting members ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Pick:
    member: ArchiveMember
    symbol: str
    interval: int

    @property
    def key(self) -> tuple[str, int]:
        return (self.symbol, self.interval)


@dataclass
class _Selection:
    picks: list[_Pick] = field(default_factory=list)
    non_usd_files: int = 0
    pair_not_requested_files: int = 0
    interval_not_selected: Counter[int] = field(default_factory=Counter)
    unrecognised: list[str] = field(default_factory=list)
    manifest_present: bool = False
    fiat_files: int = 0
    archive_intervals: dict[str, set[int]] = field(default_factory=dict)  # every USD coin pair found, any interval


def _select(members: Iterable[ArchiveMember], scope: ImportScope) -> _Selection:
    chosen = _Selection()
    for member in members:
        if member.name == "MANIFEST.json":
            chosen.manifest_present = True
            continue
        if member.pair_code is None or member.interval_minutes not in CANDLE_INTERVALS:
            chosen.unrecognised.append(member.name)
            continue
        symbol = symbol_for_pair_code(member.pair_code)
        if symbol is None:
            chosen.non_usd_files += 1
            continue
        if is_fiat_symbol(symbol):
            chosen.fiat_files += 1
            continue
        chosen.archive_intervals.setdefault(symbol, set()).add(member.interval_minutes)
        if scope.pairs is not None and symbol not in scope.pairs:
            chosen.pair_not_requested_files += 1
        elif member.interval_minutes not in scope.intervals:
            chosen.interval_not_selected[member.interval_minutes] += 1
        else:
            chosen.picks.append(_Pick(member, symbol, member.interval_minutes))
    chosen.picks.sort(key=lambda pick: pick.key)
    return chosen


def _selection_document(chosen: _Selection) -> dict[str, Any]:
    listed = sorted(chosen.unrecognised)
    return {
        "non_usd_files": chosen.non_usd_files,
        "fiat_files": chosen.fiat_files,
        "pair_not_requested_files": chosen.pair_not_requested_files,
        "interval_not_selected_files": {str(key): chosen.interval_not_selected[key]
                                        for key in sorted(chosen.interval_not_selected)},
        "unrecognised_member_count": len(listed),
        "unrecognised_members": listed[:MAX_NAMES_LISTED],
    }


def _expected_document(expected: frozenset[str] | None, chosen: _Selection, scope: ImportScope) -> dict[str, Any]:
    if expected is None:
        return {"supplied": False, "count": 0, "missing": None, "missing_intervals": None,
                "note": "No expected pair list was supplied, so pairs absent from the archive cannot be named."}
    missing = sorted(symbol for symbol in expected if symbol not in chosen.archive_intervals)
    partial = {symbol: sorted(set(scope.intervals) - chosen.archive_intervals[symbol])
               for symbol in sorted(expected) if symbol in chosen.archive_intervals
               and set(scope.intervals) - chosen.archive_intervals[symbol]}
    return {"supplied": True, "count": len(expected), "missing": missing, "missing_intervals": partial}


# --- the two passes ------------------------------------------------------------------------------------------------


class _RowDigest:
    """An order-sensitive SHA-256 over the accepted rows of one member, kept again at the end of every batch.

    Pass one records it; pass two compares before each save, so a batch is stored only when every row read so far
    is identical to what pass one saw. Counts alone cannot show a rewrite that keeps the same number of rows.
    """

    __slots__ = ("_hash", "rows", "checkpoints")

    def __init__(self) -> None:
        self._hash = hashlib.sha256()
        self.rows = 0
        self.checkpoints: list[bytes] = []

    def add(self, row: ArchiveRow) -> bool:
        """Take the next accepted row; true when it ends a batch."""
        self._hash.update(f"{row.open_unix},{row.open},{row.high},{row.low},{row.close},{row.volume},{row.trades}\n"
                          .encode("ascii"))
        self.rows += 1
        if self.rows % BATCH_CANDLES:
            return False
        self.checkpoints.append(self._hash.digest())
        return True

    def digest(self) -> bytes:
        return self._hash.digest()


@dataclass
class _SeriesScan:
    rows_read: int = 0
    accepted: int = 0
    rejected: Counter[str] = field(default_factory=Counter)
    first_open: int | None = None
    last_open: int | None = None
    outside_quarter: int = 0
    rows: _RowDigest = field(default_factory=_RowDigest)


@dataclass
class _SeriesWrite:
    dropped: int = 0
    inserted: int = 0
    duplicates: int = 0
    revisions: int = 0
    kept: int = 0
    first_open: int | None = None
    last_open: int | None = None


def _scan(archive, picks: list[_Pick], bounds: tuple[int, int] | None) -> dict[tuple[str, int], _SeriesScan]:
    budget = ReadBudget(archive.limits)
    scans: dict[tuple[str, int], _SeriesScan] = {}
    for pick in picks:
        reader = MemberReader(archive, pick.member, budget)
        outside, rows = 0, _RowDigest()
        for row in reader:
            rows.add(row)
            if bounds is not None and not bounds[0] <= row.open_unix < bounds[1]:
                outside += 1
        scans[pick.key] = _SeriesScan(reader.rows_read, reader.accepted, reader.rejected, reader.first_open,
                                      reader.last_open, outside, rows)
    return scans


def _write(store: CandleStore, run_id: int, archive, picks: list[_Pick], scans: dict[tuple[str, int], _SeriesScan],
           imported_at: datetime) -> dict[tuple[str, int], _SeriesWrite]:
    budget = ReadBudget(archive.limits)
    cutoff = to_unix(imported_at.replace(microsecond=0))  # a candle is complete once its close is at or before this
    written: dict[tuple[str, int], _SeriesWrite] = {}
    for pick in picks:
        outcome = written[pick.key] = _SeriesWrite()
        step = pick.interval * 60
        reader = MemberReader(archive, pick.member, budget)
        scan, rows = scans[pick.key], _RowDigest()
        batch: list[Candle] = []
        for row in reader:
            ends_batch = rows.add(row)
            if row.open_unix + step > cutoff:
                outcome.dropped += 1
            else:
                batch.append(Candle(
                    symbol=pick.symbol, source_pair=pick.member.pair_code, interval_minutes=pick.interval,
                    open_time_utc=from_unix(row.open_unix), close_time_utc=from_unix(row.open_unix + step),
                    available_at_utc=imported_at, open=row.open, high=row.high, low=row.low, close=row.close,
                    vwap=None, volume=row.volume, trade_count=row.trades, source=ARCHIVE_SOURCE))
                outcome.first_open = row.open_unix if outcome.first_open is None else outcome.first_open
                outcome.last_open = row.open_unix
                outcome.kept += 1
            if ends_batch:
                _require_same_batch(pick, scan, rows)
                _flush(store, run_id, batch, outcome)
        if ((reader.rows_read, reader.accepted, dict(reader.rejected)) != (
                scan.rows_read, scan.accepted, dict(scan.rejected))
                or (rows.rows, rows.digest()) != (scan.rows.rows, scan.rows.digest())):
            raise _changed(pick)
        _flush(store, run_id, batch, outcome)
    return written


def _changed(pick: _Pick) -> ArchiveRejected:
    return ArchiveRejected("changed_during_import", f"{pick.member.name} read differently the second time; "
                           "the file changed while it was being imported")


def _require_same_batch(pick: _Pick, scan: _SeriesScan, rows: _RowDigest) -> None:
    """Before a batch is saved: every accepted row read so far must match pass one, in content and order."""
    index = rows.rows // BATCH_CANDLES
    if index > len(scan.rows.checkpoints) or rows.digest() != scan.rows.checkpoints[index - 1]:
        raise _changed(pick)


def _flush(store: CandleStore, run_id: int, batch: list[Candle], outcome: _SeriesWrite) -> None:
    if batch:
        saved = store.save(batch, run_id=run_id)
        outcome.inserted += saved.inserted
        outcome.duplicates += saved.duplicates
        outcome.revisions += saved.revised
        batch.clear()


def _require_unchanged(path: Path, fingerprint: ArchiveFingerprint) -> None:
    if archive_signature(path) != fingerprint.signature:
        raise ArchiveRejected("changed_during_import", "the archive file changed while it was being imported; "
                              "anything already stored came from rows that read identically in both passes")


# --- reporting -----------------------------------------------------------------------------------------------------


def _coverage(store: CandleStore, symbol: str, interval: int, first_open: int, last_open: int) -> dict[str, Any]:
    """The store's own coverage of this series over the rows just imported, in chunks the store accepts."""
    step = interval * 60
    stop = last_open + step
    present = 0
    first_present = last_present_close = None
    ranges: list[list[int]] = []
    position = first_open
    while position < stop:
        end = min(stop, position + COVERAGE_CHUNK_SLOTS * step)
        part = store.coverage(symbol, interval, ARCHIVE_SOURCE, start_open=from_unix(position),
                              end_open=from_unix(end), mode="replay")
        present += part.present
        if part.first_open_utc is not None and first_present is None:
            first_present = to_unix(part.first_open_utc)
        if part.last_close_utc is not None:
            last_present_close = to_unix(part.last_close_utc)
        for gap in part.missing_ranges:
            first_gap, last_gap = to_unix(gap.first_open_utc), to_unix(gap.last_open_utc)
            if ranges and ranges[-1][1] + step == first_gap:
                ranges[-1][1] = last_gap
            else:
                ranges.append([first_gap, last_gap])
        position = end
    longest, run_start = 0, first_open
    for first_gap, last_gap in ranges:
        longest = max(longest, (first_gap - run_start) // step)
        run_start = last_gap + step
    longest = max(longest, (stop - run_start) // step)
    sized = [(last - first) // step + 1 for first, last in ranges]
    keep = sorted(sorted(range(len(ranges)), key=lambda index: (-sized[index], ranges[index][0]))[:MAX_GAPS_LISTED])
    expected = (stop - first_open) // step

    def iso(seconds: int | None) -> str | None:
        return None if seconds is None else utc_iso(from_unix(seconds))

    return {
        "mode": "replay", "window_start_open_utc": iso(first_open), "window_end_open_utc": iso(stop),
        "expected_slots": expected, "present_slots": present, "missing_slots": expected - present,
        "first_open_utc": iso(first_present), "last_close_utc": iso(last_present_close), "longest_run_slots": longest,
        "missing_ranges_total": len(ranges), "missing_ranges_truncated": len(keep) < len(ranges),
        "missing_ranges": [{"first_open_utc": iso(ranges[index][0]), "last_open_utc": iso(ranges[index][1]),
                            "slots": sized[index]} for index in keep],
        "note": GAP_NOTE,
    }


def _iso(seconds: int | None) -> str | None:
    return None if seconds is None else utc_iso(from_unix(seconds))


def _stored_beyond_the_file(store: CandleStore, pick: _Pick, scan: _SeriesScan,
                            bounds: tuple[int, int] | None) -> int:
    """Archive candles stored in this quarter before the file's first row or after its last: the file lacks them.

    Without a known quarter nothing marks where the file should have begun or ended, so nothing can be counted.
    """
    if bounds is None:
        return 0
    step = pick.interval * 60
    low, high = -(-bounds[0] // step) * step, bounds[1] // step * step
    if scan.first_open is None or scan.last_open is None:
        windows = [(low, high)]
    else:
        windows = [(low, scan.first_open), (scan.last_open + step, high)]
    return sum(store.coverage(pick.symbol, pick.interval, ARCHIVE_SOURCE, start_open=from_unix(first),
                              end_open=from_unix(stop), mode="replay").present
               for first, stop in windows if first < stop)


def _series_documents(store: CandleStore, picks: list[_Pick], scans: dict[tuple[str, int], _SeriesScan],
                      written: dict[tuple[str, int], _SeriesWrite], bounds: tuple[int, int] | None,
                      ) -> list[dict[str, Any]]:
    documents = []
    for pick in picks:
        scan, outcome = scans[pick.key], written[pick.key]
        coverage = None if outcome.first_open is None else _coverage(
            store, pick.symbol, pick.interval, outcome.first_open, outcome.last_open or outcome.first_open)
        inside = 0 if coverage is None else coverage["present_slots"] - outcome.kept
        documents.append({
            "symbol": pick.symbol, "source_pair": pick.member.pair_code, "interval_minutes": pick.interval,
            "member": pick.member.name, "rows_read": scan.rows_read, "accepted": scan.accepted,
            "rejected": sum(scan.rejected.values()), "rejected_by_reason": dict(sorted(scan.rejected.items())),
            "dropped_unfinished": outcome.dropped, "inserted": outcome.inserted, "duplicates": outcome.duplicates,
            "revisions": outcome.revisions, "rows_outside_quarter": scan.outside_quarter,
            "first_open_utc": _iso(outcome.first_open), "last_open_utc": _iso(outcome.last_open),
            # Candles that an earlier import stored inside this file's span, or elsewhere in its quarter, and this
            # file does not contain. The store is append-only, so a corrected file that dropped a row cannot take
            # its candle back.
            "stored_slots_not_in_file": inside + _stored_beyond_the_file(store, pick, scan, bounds),
            "coverage": coverage,
        })
    return documents


def _totals(scans: dict[tuple[str, int], _SeriesScan], written: dict[tuple[str, int], _SeriesWrite] | None,
            stored_not_in_file: int = 0) -> dict[str, Any]:
    outcomes = list(written.values()) if written is not None else []
    return {
        "series": len(scans), "pairs": len({symbol for symbol, _ in scans}),
        "rows_read": sum(scan.rows_read for scan in scans.values()),
        "accepted": sum(scan.accepted for scan in scans.values()),
        "rejected": sum(sum(scan.rejected.values()) for scan in scans.values()),
        "rows_outside_quarter": sum(scan.outside_quarter for scan in scans.values()),
        "dropped_unfinished": sum(outcome.dropped for outcome in outcomes),
        "inserted": sum(outcome.inserted for outcome in outcomes),
        "duplicates": sum(outcome.duplicates for outcome in outcomes),
        "revisions": sum(outcome.revisions for outcome in outcomes),
        "stored_slots_not_in_file": stored_not_in_file,
    }


# --- the import ----------------------------------------------------------------------------------------------------


def import_archive(store: CandleStore, clock: Clock, path: Path | str, scope: ImportScope | None = None, *,
                   evidence_label: str = "local_archive_file", quarter: str | None = None,
                   expected_pairs: Iterable[str] | None = None, limits: ArchiveLimits | None = None) -> ImportResult:
    """Import one local archive. Raises ``ArchiveRejected`` (after recording a failed run) when the file is refused."""
    scope = scope or ImportScope()
    limits = limits or ArchiveLimits()
    if evidence_label not in EVIDENCE_LABELS:
        raise ValidationFailure("unsupported evidence label")
    path = Path(path)
    expected = None if expected_pairs is None else normalise_pair_names(expected_pairs)
    label, label_source, kind = resolve_quarter(path.name, quarter)
    fingerprint = fingerprint_archive(path)  # a missing or unreadable file raises here, before any run exists
    started_at = _utc(clock.now())
    seen_before = [run.run_id for run in store.runs()
                   if run.kind == "archive_import" and run.status in ("ok", "partial")
                   and run.parameters.get("archive", {}).get("sha256") == fingerprint.sha256]
    archive_document = {"file_name": fingerprint.file_name, "size_bytes": fingerprint.size_bytes,
                        "sha256": fingerprint.sha256, "format": fingerprint.format}
    run_id = store.begin_run(
        kind="archive_import", source=ARCHIVE_SOURCE, evidence_label=evidence_label, started_at=started_at,
        parameters={"importer_version": IMPORTER_VERSION, "archive": archive_document, "quarter": label,
                    "scope": scope.document(), "limits": _limits_document(limits),
                    "expected_pairs_count": None if expected is None else len(expected)})
    report: dict[str, Any] = {
        "kind": "archive_import", "importer_version": IMPORTER_VERSION, "status": "failed",
        "evidence_label": evidence_label, "started_at_utc": utc_iso(started_at),
        "archive": {**archive_document, "kind": kind, "quarter": label, "quarter_source": label_source},
        "scope": scope.document(), "same_file_seen_in_runs": seen_before,
        "survivorship_notice": SURVIVORSHIP_NOTICE, "assumptions": list(UNCONFIRMED_ASSUMPTIONS),
    }
    try:
        status = _run(store, clock, path, fingerprint, scope, limits, expected, label, run_id, report)
    except ArchiveRejected as error:
        report.update(status="failed", reason=error.reason, message=str(error))
        error.run_id, error.report = run_id, report
        _finish(store, clock, run_id, "failed", report)
        raise
    except ValidationFailure as error:  # the store refused, for example a receipt earlier than a stored revision
        report.update(status="failed", reason="store_refused", message=str(error)[:300])
        _finish(store, clock, run_id, "failed", report)
        refused = ArchiveRejected("store_refused", str(error))
        refused.run_id, refused.report = run_id, report
        raise refused from error
    except Exception as error:
        report.update(status="failed", reason=type(error).__name__, message=str(error)[:300])
        _finish(store, clock, run_id, "failed", report)
        raise
    report["status"] = status
    store.finish_run(run_id, finished_at=_utc(clock.now()), status=status, report=report)
    return ImportResult(run_id=run_id, status=status, report=report)


def _finish(store: CandleStore, clock: Clock, run_id: int, status: str, report: dict[str, Any]) -> None:
    with contextlib.suppress(Exception):  # never hide the real failure behind a bookkeeping one
        store.finish_run(run_id, finished_at=_utc(clock.now()), status=status, report=report)


def _run(store: CandleStore, clock: Clock, path: Path, fingerprint: ArchiveFingerprint, scope: ImportScope,
         limits: ArchiveLimits, expected: frozenset[str] | None, quarter: str | None, run_id: int,
         report: dict[str, Any]) -> str:
    bounds = _quarter_bounds(quarter)
    report["retraction_check"] = "file_span" if bounds is None else "quarter"
    with open_archive(path, limits) as archive:
        chosen = _select(archive.members, scope)
        report["archive"]["members_total"] = len(archive.members)
        report["archive"]["manifest_present"] = chosen.manifest_present
        report["archive"]["usd_pairs_found"] = len(chosen.archive_intervals)
        report["skipped"] = _selection_document(chosen)
        report["expected_pairs"] = _expected_document(expected, chosen, scope)
        keys = Counter(pick.key for pick in chosen.picks)
        clashes = sorted(f"{symbol} {interval}m" for (symbol, interval), count in keys.items() if count > 1)
        if clashes:
            raise ArchiveRejected("ambiguous_pair_codes", f"more than one file maps to {', '.join(clashes)}")
        if not chosen.picks:
            raise ArchiveRejected("nothing_in_scope", "the archive holds no USD-pair file at the chosen intervals "
                                  "for the chosen pairs (see 'skipped' for what it did hold)")
        if sum(pick.member.size for pick in chosen.picks) > limits.max_total_bytes:
            raise ArchiveRejected("total_size_limit", "the in-scope files declare more uncompressed data than the "
                                  "limit allows (raise --max-uncompressed-gb if intended)")
        scans = _scan(archive, chosen.picks, bounds)
        totals = _totals(scans, None)
        report["totals"] = totals
        report["rejected_by_reason"] = dict(sorted(sum((scan.rejected for scan in scans.values()), Counter()).items()))
        if totals["rows_read"] == 0:
            raise ArchiveRejected("no_rows", "the in-scope files contain no rows")
        share = Decimal(totals["rejected"]) / Decimal(totals["rows_read"])
        report["rejected_share"] = format(share.quantize(Decimal("0.000001")), "f")
        if share > limits.max_rejected_share:
            raise ArchiveRejected("rejected_share_exceeded", f"{totals['rejected']} of {totals['rows_read']} rows "
                                  f"were rejected, over the bound of {limits.max_rejected_share}; the file is not "
                                  "in the format this importer expects, so nothing was stored")
        imported_at = _utc(clock.now())
        report["imported_at_utc"] = utc_iso(imported_at)
        _require_unchanged(path, fingerprint)
        written = _write(store, run_id, archive, chosen.picks, scans, imported_at)
    _require_unchanged(path, fingerprint)
    report["series"] = _series_documents(store, chosen.picks, scans, written, bounds)
    report["totals"] = _totals(scans, written, sum(item["stored_slots_not_in_file"] for item in report["series"]))
    totals = report["totals"]
    flagged = totals["rejected"] or totals["dropped_unfinished"] or totals["stored_slots_not_in_file"]
    return "partial" if flagged else "ok"
