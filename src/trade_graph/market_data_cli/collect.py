"""Commands ``collect-kraken-candles`` and ``candle-coverage`` (docs/KRAKEN-CANDLE-COLLECTION.md).

Both print one JSON report. ``collect-kraken-candles`` exits 0 only when the run is ``ok``; a partial or failed
run exits 1, a bad option exits 2, an interrupted run exits 130. Neither command ever opens the financial
database: a path that is, or holds, the financial database is refused before anything is read or written.
Only public Kraken GET requests are made, through the rate limiter, and never with a key.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from trade_graph.adapters.market.kraken_candles import make_ohlc_transport
from trade_graph.adapters.market.kraken_catalog import make_catalog_transport
from trade_graph.adapters.market.public import TextTransport
from trade_graph.adapters.market.rate_limit import (
    ASSUMED_CEILING,
    KrakenRestLimiter,
    LimitedTransport,
    MonotonicClock,
)
from trade_graph.application.candle_collection_plan import DEFAULT_ONE_MINUTE_SYMBOLS, CollectionScope
from trade_graph.application.collect_candles import (
    CATALOG_RATE_LIMIT_STOPS,
    MAXIMUM_REQUESTS_PER_RUN,
    collect_candles,
    refresh_catalog,
)
from trade_graph.contracts.candles import CANDLE_INTERVALS, CANDLE_SOURCES, COLLECTED_INTERVALS, from_unix, to_unix
from trade_graph.domain.clock import Clock, SystemClock, utc_iso
from trade_graph.domain.errors import ValidationFailure
from trade_graph.market_data_cli._common import add_market_database_option, open_store

COLLECT_COMMAND = "collect-kraken-candles"
COVERAGE_COMMAND = "candle-coverage"
SMOKE_SYMBOLS = ("BTC/USD", "ETH/USD")
SMOKE_INTERVAL = 15
SMOKE_MAXIMUM_REQUESTS = 4  # the pair-list refresh included
DEFAULT_MAX_REQUESTS = 2000
MAXIMUM_COVERAGE_RANGES = 1000
FINANCIAL_DATABASE_NAME = "trade_graph.sqlite"
FINANCIAL_DEFAULT_PATH = "runtime/trade_graph.sqlite"
FINANCIAL_TABLES = frozenset({"schema_migrations", "portfolios", "ledger_events"})
CANDLE_MARKER_TABLE = "market_data_migrations"
ALL_PAIRS_WORD = "all"
_OPTION_NAMES = {"intervals": "--intervals", "pairs": "--pairs", "one_minute_pairs": "--one-minute-pairs",
                 "max_requests": "--max-requests"}


# --- seams the tests replace ----------------------------------------------------------------------------------------


def build_clock() -> Clock:
    return SystemClock()


def build_sleeper() -> Callable[[float], None]:
    return time.sleep


def build_transports() -> tuple[TextTransport, TextTransport]:
    """The pair-list transport (large response limit) and the OHLC transport (small one)."""
    return make_catalog_transport(), make_ohlc_transport()


def limiter_clock_for(clock: Clock) -> Clock:
    """The clock the rate limiter reads: monotonic for the real host clock, so a correction of the host time
    neither empties nor freezes the counter. A scripted clock is used as given."""
    return MonotonicClock() if isinstance(clock, SystemClock) else clock


# --- registration ---------------------------------------------------------------------------------------------------


def register(sub: argparse._SubParsersAction) -> None:
    collect = sub.add_parser(
        COLLECT_COMMAND, help="collect bounded public Kraken USD-pair candles at 1, 5, 15 and 60 minutes")
    add_market_database_option(collect)
    collect.add_argument("--intervals", default=None, help="comma list from 1,5,15,60 (default: all four)")
    collect.add_argument("--pairs", default=None, help="comma list of BASE/USD symbols for the 5, 15 and 60 minute "
                         "candles (default: every eligible USD pair)")
    collect.add_argument("--one-minute-pairs", default=None, help="comma list of BASE/USD symbols for 1-minute "
                         "candles (default: BTC/USD,ETH/USD; an empty value collects none; 'all' uses the same "
                         "pairs as the other intervals, about 131,000 rows per pair and quarter)")
    collect.add_argument("--max-requests", type=int, default=None,
                         help=f"most requests of the whole run, the pair-list refresh included (default "
                              f"{DEFAULT_MAX_REQUESTS}, at most {MAXIMUM_REQUESTS_PER_RUN + 1})")
    collect.add_argument("--smoke", action="store_true", help="BTC/USD and ETH/USD at 15 minutes only, at most "
                         f"{SMOKE_MAXIMUM_REQUESTS} requests, labelled live_public_smoke")
    collect.add_argument("--catalog-only", action="store_true", help="refresh the pair list and collect no candles")
    coverage = sub.add_parser(COVERAGE_COMMAND, help="read-only: stored candle series, counts and missing ranges")
    add_market_database_option(coverage)
    coverage.add_argument("--source", default="kraken_public_ohlc", choices=CANDLE_SOURCES)
    coverage.add_argument("--symbols", default=None, help="comma list of symbols to show (default: all)")
    coverage.add_argument("--intervals", default=None, help="comma list of intervals to show (default: all)")
    coverage.add_argument("--max-ranges", type=int, default=20, help="missing ranges listed per series")


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int | None:
    if args.command == COLLECT_COMMAND:
        return _collect(args, parser)
    if args.command == COVERAGE_COMMAND:
        return _coverage(args, parser)
    return None


def _emit(report: dict, code: int) -> int:
    print(json.dumps(report))
    return code


# --- the financial database is never touched ------------------------------------------------------------------------


def _main_file_tables(path: Path) -> set[str]:
    """Table names in the main file alone. immutable: no lock, no -shm file, no write of any kind."""
    connection = sqlite3.connect(f"file:{quote(str(path.resolve()))}?mode=ro&immutable=1", uri=True)
    try:
        return {item[0] for item in connection.execute("SELECT name FROM sqlite_master")}
    finally:
        connection.close()


def _log_in_use(path: Path) -> bool:
    log = path.with_name(path.name + "-wal")
    return log.is_file() and log.stat().st_size > 0


def financial_database_refusal(path: Path) -> str | None:
    """Why this path may not be used as the candle database, or None. Nothing is created or changed."""
    if path.name == FINANCIAL_DATABASE_NAME:
        return "the file name is the financial database's"
    try:
        default = Path(FINANCIAL_DEFAULT_PATH)
        if path.resolve() == default.resolve():
            return "the path is the default financial database"
        if path.is_file() and default.is_file() and path.samefile(default):
            return "the file is the default financial database under another name"
        if path.is_file():
            names = _main_file_tables(path)
            if names & FINANCIAL_TABLES:
                return "the file holds the financial database's tables"
            # A database in use keeps its newest pages in the -wal log, which the read above cannot see. A log in
            # use beside a main file that shows no candle tables yet could be a live financial database.
            if CANDLE_MARKER_TABLE not in names and _log_in_use(path):
                return "the file has a write-ahead log in use but shows no candle tables, so it may be a live database"
    except (OSError, sqlite3.Error):
        return None  # not readable as SQLite: opening it as a candle database will fail on its own
    return None


def _unusable(command: str, path: Path, error: Exception) -> int:
    # Our own refusals carry a plain message; an SQLite error text is not echoed.
    detail = str(error) if isinstance(error, ValidationFailure) else "the file cannot be used as a SQLite database"
    return _emit({"command": command, "status": "failed", "error": "market_database_unusable", "detail": detail,
                  "market_database": str(path)}, 1)


def _refuse(command: str, path: Path, reason: str) -> int:
    print(f"refused: {reason}; the candle database is a separate file", file=sys.stderr)
    return _emit({"command": command, "status": "refused", "error": "financial_database", "detail": reason,
                  "market_database": str(path)}, 1)


# --- collect-kraken-candles -----------------------------------------------------------------------------------------


def _split(parser: argparse.ArgumentParser, flag: str, text: str, *, allow_empty: bool) -> tuple[str, ...]:
    items = tuple(part.strip() for part in text.split(",")) if text.strip() else ()
    if not items and not allow_empty:
        parser.error(f"{flag} needs at least one value")
    return items


def _options(args: argparse.Namespace, parser: argparse.ArgumentParser) -> tuple[CollectionScope, int, str, str]:
    """The scope, the request limit for the whole run, the evidence label and the mode name."""
    given = {name: getattr(args, name) for name in _OPTION_NAMES if getattr(args, name) is not None}
    if args.smoke:
        clashes = [_OPTION_NAMES[name] for name in given] + (["--catalog-only"] if args.catalog_only else [])
        if clashes:
            parser.error(f"--smoke fixes its own scope and limit; it cannot be combined with {', '.join(clashes)}")
        scope = CollectionScope(intervals=(SMOKE_INTERVAL,), symbols=SMOKE_SYMBOLS, one_minute_symbols=())
        return scope, SMOKE_MAXIMUM_REQUESTS, "live_public_smoke", "smoke"
    if args.catalog_only:
        if given:
            named = ", ".join(_OPTION_NAMES[name] for name in given)
            parser.error(f"--catalog-only takes no scope or limit options ({named})")
        return CollectionScope(), DEFAULT_MAX_REQUESTS, "live_public", "catalog_only"
    intervals = COLLECTED_INTERVALS
    if args.intervals is not None:
        try:
            intervals = tuple(int(part) for part in _split(parser, "--intervals", args.intervals, allow_empty=False))
        except ValueError:
            parser.error("--intervals must be a comma list of whole numbers from 1, 5, 15 and 60")
    symbols = None if args.pairs is None else _split(parser, "--pairs", args.pairs, allow_empty=False)
    one_minute: tuple[str, ...] | None = DEFAULT_ONE_MINUTE_SYMBOLS
    if args.one_minute_pairs is not None:
        every = args.one_minute_pairs.strip().lower() == ALL_PAIRS_WORD
        one_minute = None if every else _split(parser, "--one-minute-pairs", args.one_minute_pairs, allow_empty=True)
    # An option that selects nothing the run collects would be dropped without a word; refuse it instead.
    if args.pairs is not None and all(interval == 1 for interval in intervals):
        parser.error("--pairs applies to the 5, 15 and 60 minute candles and --intervals selects none of them; "
                     "name the coins for 1-minute candles with --one-minute-pairs")
    if args.one_minute_pairs is not None and 1 not in intervals and one_minute != ():
        parser.error("--one-minute-pairs applies to 1-minute candles and --intervals does not include 1")
    limit = DEFAULT_MAX_REQUESTS if args.max_requests is None else args.max_requests
    if not 2 <= limit <= MAXIMUM_REQUESTS_PER_RUN + 1:
        parser.error(f"--max-requests must be from 2 to {MAXIMUM_REQUESTS_PER_RUN + 1}")
    try:
        scope = CollectionScope(intervals=intervals, symbols=symbols, one_minute_symbols=one_minute)
    except ValidationFailure as error:
        parser.error(str(error))
    return scope, limit, "live_public", "normal"


def _compact_catalog(report: dict) -> dict:
    keys = ("status", "error", "observed_at_utc", "catalog_row_added", "usd_pairs", "eligible_for_collection",
            "rejected_count", "unreviewed_asset_count", "rules_version", "request_count")
    return {key: report.get(key) for key in keys}


def _collect(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    scope, limit, label, mode = _options(args, parser)
    path = Path(args.market_database)
    refusal = financial_database_refusal(path)
    if refusal is not None:
        return _refuse(COLLECT_COMMAND, path, refusal)
    try:
        database, store = open_store(path)
    except (ValidationFailure, sqlite3.Error) as error:
        return _unusable(COLLECT_COMMAND, path, error)
    clock = build_clock()
    # A new process cannot know what an earlier one just asked for, so it assumes the counter is already at the ceiling.
    limiter = KrakenRestLimiter(limiter_clock_for(clock), build_sleeper(), initial_counter=ASSUMED_CEILING)
    try:
        catalog_transport, ohlc_transport = build_transports()
        catalog = refresh_catalog(store, clock, LimitedTransport(catalog_transport, limiter), label)
        if mode == "catalog_only":
            return _emit({"command": COLLECT_COMMAND, "mode": mode, "status": catalog["status"],
                          "market_database": str(path), "catalog": catalog, "requests_total": limiter.calls},
                         0 if catalog["status"] == "ok" else 1)
        stop = CATALOG_RATE_LIMIT_STOPS.get(catalog["error"])
        if stop is not None:  # Kraken asked for fewer calls: asking for candles now would only lengthen the restriction
            return _emit({"command": COLLECT_COMMAND, "mode": mode, "market_database": str(path), "status": "failed",
                          "error": catalog["error"], "stop_reason": stop, "paid_calls_enabled": False,
                          "live_trading_enabled": False, "catalog_refresh": _compact_catalog(catalog),
                          "requests_total": limiter.calls,
                          "note": "the pair list was rate limited; no candle was requested. Run again later"}, 1)
        report = collect_candles(store, clock, ohlc_transport, limiter, scope, limit - 1, label)
        status = "partial" if report["status"] == "ok" and catalog["status"] != "ok" else report["status"]
        return _emit({"command": COLLECT_COMMAND, "mode": mode, "market_database": str(path), **report,
                      "status": status, "catalog_refresh": _compact_catalog(catalog),
                      "requests_total": limiter.calls}, 0 if status == "ok" else 1)
    except KeyboardInterrupt:
        return _emit({"command": COLLECT_COMMAND, "mode": mode, "status": "interrupted",
                      "market_database": str(path), "requests_total": limiter.calls,
                      "note": "the run is recorded as interrupted; candles of finished series are kept"}, 130)
    finally:
        database.close()


# --- candle-coverage ------------------------------------------------------------------------------------------------


def _coverage(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if not 1 <= args.max_ranges <= MAXIMUM_COVERAGE_RANGES:
        parser.error(f"--max-ranges must be from 1 to {MAXIMUM_COVERAGE_RANGES}")
    symbols = None if args.symbols is None else set(_split(parser, "--symbols", args.symbols, allow_empty=False))
    if symbols is not None and any(not _plausible_symbol(item) for item in symbols):
        parser.error("--symbols must be a comma list of BASE/QUOTE symbols in upper case")
    intervals = None
    if args.intervals is not None:
        try:
            intervals = {int(part) for part in _split(parser, "--intervals", args.intervals, allow_empty=False)}
        except ValueError:
            parser.error("--intervals must be a comma list of whole numbers")
        if not intervals <= set(CANDLE_INTERVALS):
            parser.error(f"--intervals must come from {', '.join(map(str, CANDLE_INTERVALS))}")
    path = Path(args.market_database)
    refusal = financial_database_refusal(path)
    if refusal is not None:
        return _refuse(COVERAGE_COMMAND, path, refusal)
    if not path.is_file():
        return _emit({"command": COVERAGE_COMMAND, "status": "failed", "error": "market_database_not_found",
                      "market_database": str(path)}, 1)
    try:
        database, store = open_store(path)
    except (ValidationFailure, sqlite3.Error) as error:
        return _unusable(COVERAGE_COMMAND, path, error)
    try:
        series = []
        for item in store.series():
            if (item.source != args.source or item.first_open_utc is None or item.last_open_utc is None
                    or (symbols is not None and item.symbol not in symbols)
                    or (intervals is not None and item.interval_minutes not in intervals)):
                continue
            series.append(_series_document(store, item, args.max_ranges))
        totals = {"series": len(series), "candles": sum(entry["candles"] for entry in series),
                  "missing_slots": sum(entry.get("missing_slots", 0) for entry in series)}
        return _emit({"command": COVERAGE_COMMAND, "status": "ok", "market_database": str(path),
                      "source": args.source, "basis": "all stored candles, receipt time ignored (event time only)",
                      "series": series, "totals": totals}, 0)
    finally:
        database.close()


def _plausible_symbol(value: str) -> bool:
    base, slash, quote = value.partition("/")
    return bool(slash) and base.isalnum() and quote.isalpha() and value == value.upper()


def _series_document(store, item, max_ranges: int) -> dict:
    base = {"symbol": item.symbol, "source": item.source, "source_pair": item.source_pair,
            "interval_minutes": item.interval_minutes, "candles": item.candle_count}
    step = item.interval_minutes * 60
    try:
        report = store.coverage(item.symbol, item.interval_minutes, item.source, start_open=item.first_open_utc,
                                end_open=from_unix(to_unix(item.last_open_utc) + step), mode="replay")
    except ValidationFailure:
        return {**base, "first_open_utc": utc_iso(item.first_open_utc), "last_close_utc": None,
                "error": "coverage_window_too_large"}
    document = report.to_document()
    ranges = document["missing_ranges"]
    return {
        **base, "first_open_utc": document["first_open_utc"], "last_close_utc": document["last_close_utc"],
        "expected_slots": document["expected_slots"], "present_slots": document["present_slots"],
        "missing_slots": document["missing_slots"], "longest_run_slots": document["longest_run_slots"],
        "missing_ranges": ranges[:max_ranges], "missing_ranges_total": len(ranges),
        "missing_ranges_truncated": len(ranges) > max_ranges,
    }
