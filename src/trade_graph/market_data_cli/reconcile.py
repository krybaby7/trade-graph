"""reconcile-candles: compare the same candle slots across the collector, the archive and the Trades-built series.

Read-only: it opens an existing candle-history file, writes nothing and prints one JSON report (counts of identical,
differing, single-source and absent slots, a per-field summary for every pair of sources, and the worst slots).
Differences are reported, never merged. Exit code 0 means the report was produced, 2 is a usage error.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta

from trade_graph.application.candle_store import CandleStore
from trade_graph.application.import_candle_archive import normalise_pair_names
from trade_graph.application.reconcile_candles import (
    DEFAULT_SOURCE_PRECEDENCE,
    MAX_RECONCILE_SLOTS,
    MAX_WORST,
    reconcile_candles,
)
from trade_graph.contracts.candles import CANDLE_INTERVALS
from trade_graph.domain.clock import SystemClock
from trade_graph.domain.errors import ValidationFailure
from trade_graph.market_data_cli._common import add_market_database_option
from trade_graph.market_data_cli._guard import open_store_or_exit, require_existing_market_database

COMMAND = "reconcile-candles"


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(COMMAND, help="compare candle sources over a window (read-only; differences are "
                            "reported, never merged)")
    parser.add_argument("--pair", required=True, help="one USD pair, such as BTC/USD")
    add_market_database_option(parser)
    parser.add_argument("--interval", type=int, default=15, help="candle size in minutes (default 15)")
    parser.add_argument("--sources", default=",".join(DEFAULT_SOURCE_PRECEDENCE),
                        help="two or more sources, comma separated, in precedence order (default: "
                             + ", ".join(DEFAULT_SOURCE_PRECEDENCE) + ")")
    parser.add_argument("--end", help="close time of the last slot, such as 2026-10-13T14:00:00Z "
                        "(default: the newest candle of the chosen sources)")
    parser.add_argument("--count", type=int, default=2000, help="slots in the window (default 2000)")
    parser.add_argument("--worst", type=int, default=10, help="how many of the most different slots to list")
    parser.add_argument("--mode", choices=("replay", "point_in_time"), default="replay",
                        help="replay judges by event time; point_in_time hides candles received after --as-of")
    parser.add_argument("--as-of", help="for point_in_time: the moment to judge by (default: now)")


def _moment(text: str | None, name: str, parser: argparse.ArgumentParser) -> datetime | None:
    if text is None:
        return None
    try:
        value = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        parser.error(f"{name} must look like 2026-10-13T14:00:00Z")
    if value.tzinfo is None or value.utcoffset() is None:
        parser.error(f"{name} must name its time zone, for example end it with Z")
    return value.astimezone(UTC)


def _newest_close(store: CandleStore, symbol: str, interval: int, sources: tuple[str, ...]) -> datetime | None:
    opens = [summary.last_open_utc for summary in store.series()
             if summary.symbol == symbol and summary.interval_minutes == interval and summary.source in sources
             and summary.last_open_utc is not None]
    return None if not opens else max(opens) + timedelta(minutes=interval)


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int | None:
    if args.command != COMMAND:
        return None
    try:
        (symbol,) = normalise_pair_names([args.pair])
    except (ValueError, ValidationFailure) as error:
        parser.error(f"--pair: {error}")
    if args.interval not in CANDLE_INTERVALS:
        parser.error(f"--interval must be one of {CANDLE_INTERVALS}")
    sources = tuple(part.strip() for part in args.sources.split(","))
    if not 1 <= args.count <= MAX_RECONCILE_SLOTS or not 1 <= args.worst <= MAX_WORST:
        parser.error(f"--count must be 1 to {MAX_RECONCILE_SLOTS} and --worst 1 to {MAX_WORST}")
    end, as_of = _moment(args.end, "--end", parser), _moment(args.as_of, "--as-of", parser)
    require_existing_market_database(args.market_database, parser)
    database, store = open_store_or_exit(args.market_database, parser)
    try:
        if end is None:
            end = _newest_close(store, symbol, args.interval, sources)
            if end is None:
                parser.error(f"no {symbol} candles at {args.interval} minutes from the chosen sources; "
                             "pass --end or collect or import candles first")
        if args.mode == "point_in_time" and as_of is None:
            as_of = SystemClock().now()
        try:
            report = reconcile_candles(store, symbol, args.interval, sources=sources, end_close_time=end,
                                       count=args.count, as_of=as_of if args.mode == "point_in_time" else None,
                                       mode=args.mode, worst=args.worst)
        except ValidationFailure as error:
            parser.error(str(error))
        print(json.dumps(report.to_document(), sort_keys=True))
        return 0
    finally:
        database.close()
