"""import-kraken-archive: read a Kraken OHLCVT archive that the owner already downloaded.

The command never downloads. It takes a local ZIP (or a directory of CSV files), writes only the separate
candle-history file, and prints one JSON report. Exit code 0 means imported (status ok or partial), 1 means the
file was refused (the report says why and names the recorded run), 2 means a usage error.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
from decimal import Decimal, InvalidOperation

from trade_graph.adapters.market.kraken_archive import GIB, ArchiveLimits, ArchiveRejected
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.import_candle_archive import (
    DEFAULT_INTERVALS,
    ImportScope,
    import_archive,
    normalise_pair_names,
    resolve_quarter,
)
from trade_graph.domain.clock import SystemClock
from trade_graph.domain.errors import ValidationFailure
from trade_graph.market_data_cli._common import add_market_database_option
from trade_graph.market_data_cli._guard import open_store_or_exit, refuse_financial_database

COMMAND = "import-kraken-archive"
MAX_UNCOMPRESSED_GB = Decimal(1024)
DEFAULT_MAX_MEMBER_BYTES = ArchiveLimits().max_member_bytes
_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        COMMAND, help="import a Kraken OHLCVT archive you downloaded yourself (this command never downloads)")
    parser.add_argument("--archive", required=True, help="path to the local ZIP file (or a directory of CSV files)")
    add_market_database_option(parser)
    parser.add_argument("--intervals", default=",".join(str(value) for value in DEFAULT_INTERVALS),
                        help="candle sizes in minutes, comma separated (1 and 5 are very large)")
    parser.add_argument("--pairs", help="only these USD pairs, comma separated (BTC/USD or XBTUSD style)")
    parser.add_argument("--expected-pairs", help="pairs you expect to find, to name the ones the archive lacks; "
                        "default: the newest stored collector catalog")
    parser.add_argument("--quarter", help="the quarter the file covers, such as 2026Q3 (default: read from the name)")
    parser.add_argument("--max-uncompressed-gb", default="16",
                        help="refuse to read more than this many GB of CSV text from the archive")


def _scope(args: argparse.Namespace, parser: argparse.ArgumentParser) -> ImportScope:
    try:
        intervals = tuple(int(part) for part in args.intervals.split(","))
        pairs = None if args.pairs is None else normalise_pair_names(args.pairs.split(","))
        return ImportScope(intervals=intervals, pairs=pairs)
    except (ValueError, ValidationFailure) as error:
        parser.error(f"--intervals/--pairs: {error}")


def _limits(args: argparse.Namespace, parser: argparse.ArgumentParser) -> ArchiveLimits:
    try:
        gigabytes = Decimal(args.max_uncompressed_gb)
    except InvalidOperation:
        parser.error("--max-uncompressed-gb must be a number")
    if not gigabytes.is_finite() or not Decimal(0) < gigabytes <= MAX_UNCOMPRESSED_GB:  # NaN cannot be ordered
        parser.error(f"--max-uncompressed-gb must be above 0 and at most {MAX_UNCOMPRESSED_GB}")
    total = max(1, int(gigabytes * GIB))
    return ArchiveLimits(max_total_bytes=total, max_member_bytes=min(DEFAULT_MAX_MEMBER_BYTES, total))


def _catalog_pairs(store: CandleStore, clock: SystemClock) -> frozenset[str] | None:
    """USD symbols from the newest stored collector catalog, when there is one."""
    observation = store.latest_catalog(as_of=clock.now())
    if observation is None:
        return None
    symbols: set[str] = set()
    for entry in observation.pairs:
        name = entry.get("symbol") if isinstance(entry, dict) else None
        if isinstance(name, str) and name.endswith("/USD"):
            with contextlib.suppress(ValidationFailure):
                symbols |= normalise_pair_names([name])
    return frozenset(symbols) or None


def _emit(document: dict, code: int) -> int:
    print(json.dumps(document, sort_keys=True))
    return code


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int | None:
    if args.command != COMMAND:
        return None
    if _URL.match(args.archive):
        parser.error("--archive must be a file on this computer; this command never downloads (see "
                     "docs/KRAKEN-ARCHIVE-IMPORT.md for the manual download steps)")
    scope, limits = _scope(args, parser), _limits(args, parser)
    try:
        quarter = None if args.quarter is None else resolve_quarter("", args.quarter)[0]
        expected = None if args.expected_pairs is None else normalise_pair_names(args.expected_pairs.split(","))
    except ValidationFailure as error:
        parser.error(f"--quarter/--expected-pairs: {error}")
    refuse_financial_database(args.market_database, parser)
    clock = SystemClock()
    database, store = open_store_or_exit(args.market_database, parser)
    try:
        if expected is None:
            expected = _catalog_pairs(store, clock)
        try:
            result = import_archive(store, clock, args.archive, scope, quarter=quarter, expected_pairs=expected,
                                    limits=limits)
        except ArchiveRejected as error:
            refusal = error.report or {"status": "failed", "reason": error.reason, "message": str(error)}
            return _emit({"run_id": error.run_id, **refusal}, 1)
        return _emit({"run_id": result.run_id, **result.report}, 0)
    finally:
        database.close()
