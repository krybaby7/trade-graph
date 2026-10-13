"""backfill-kraken-trades: fill the gap between the archive and the collector from Kraken's public Trades list.

Public, unauthenticated GET requests only, at most ``--max-requests`` of them per run, paced by a limiter that models
Kraken's request counter. It writes only the separate candle-history file and prints one JSON report. The report
carries the resume cursor, so running the same command again continues where the last run stopped. Exit code 0 means
the run was recorded (status ok or partial), 1 means nothing could be done (status failed, the report says why),
2 is a usage error and 130 is Ctrl-C (the run is still recorded).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal

from trade_graph.adapters.market.kraken_trades import MAXIMUM_RESPONSE_BYTES, KrakenTrades
from trade_graph.adapters.market.public import HttpxTextTransport, TextTransport
from trade_graph.adapters.market.trades_rate_limit import LimiterSettings, RequestLimiter
from trade_graph.application.backfill_trades import (
    BACKFILL_INTERVALS,
    MAX_REQUESTS_LIMIT,
    BackfillScope,
    backfill_trades,
)
from trade_graph.application.import_candle_archive import normalise_pair_names
from trade_graph.domain.clock import SystemClock
from trade_graph.domain.errors import ValidationFailure
from trade_graph.market_data_cli._common import add_market_database_option, open_store
from trade_graph.market_data_cli._guard import refuse_financial_database

COMMAND = "backfill-kraken-trades"
DEFAULT_MAX_REQUESTS = 100
SMOKE_MAX_REQUESTS = 3
HTTP_TIMEOUT_SECONDS = 10


class _Sleeper:
    def sleep(self, seconds: Decimal) -> None:
        time.sleep(float(seconds))


def make_transport() -> TextTransport:
    """The one place a real HTTP client is built; tests replace this function with a scripted transport."""
    return HttpxTextTransport(timeout=HTTP_TIMEOUT_SECONDS, maximum_response_bytes=MAXIMUM_RESPONSE_BYTES)


def make_sleeper() -> _Sleeper:
    return _Sleeper()


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        COMMAND, help="fill the gap between the archive and the collector from Kraken's public Trades list "
        "(public requests only, bounded, resumable)")
    parser.add_argument("--pairs", required=True, help="USD pairs, comma separated (BTC/USD or XBTUSD style)")
    add_market_database_option(parser)
    parser.add_argument("--intervals", default="15",
                        help=f"candle sizes in minutes, comma separated, from {BACKFILL_INTERVALS} (default 15)")
    parser.add_argument("--max-requests", type=int, default=None,
                        help=f"most public requests this run may make (default {DEFAULT_MAX_REQUESTS}, "
                             f"smoke {SMOKE_MAX_REQUESTS}, at most {MAX_REQUESTS_LIMIT})")
    parser.add_argument("--start", help="start of the range, such as 2026-10-01T00:00:00Z; replaces the archive end")
    parser.add_argument("--end", help="end of the range, such as 2026-10-13T00:00:00Z; replaces the collector start")
    parser.add_argument("--smoke", action="store_true",
                        help=f"one pair and at most {SMOKE_MAX_REQUESTS} requests, recorded as live_public_smoke")
    parser.add_argument("--from-start", action="store_true",
                        help="ignore the cursor of earlier runs and start at the beginning of the range")


def _moment(text: str | None, name: str, parser: argparse.ArgumentParser) -> datetime | None:
    if text is None:
        return None
    try:
        value = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        parser.error(f"{name} must look like 2026-10-13T00:00:00Z")
    if value.tzinfo is None or value.utcoffset() is None:
        parser.error(f"{name} must name its time zone, for example end it with Z")
    return value.astimezone(UTC)


def _scope(args: argparse.Namespace, parser: argparse.ArgumentParser) -> BackfillScope:
    try:
        pairs = normalise_pair_names(args.pairs.split(","))
        intervals = tuple(int(part) for part in args.intervals.split(","))
    except (ValueError, ValidationFailure) as error:
        parser.error(f"--pairs/--intervals: {error}")
    if args.smoke and len(pairs) != 1:
        parser.error("--smoke takes exactly one pair")
    try:
        return BackfillScope(symbols=tuple(pairs), intervals=intervals, start=_moment(args.start, "--start", parser),
                             end=_moment(args.end, "--end", parser))
    except ValidationFailure as error:
        parser.error(str(error))


def _budget(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.smoke:
        budget = SMOKE_MAX_REQUESTS if args.max_requests is None else args.max_requests
        if not 1 <= budget <= SMOKE_MAX_REQUESTS:
            parser.error(f"--smoke allows from 1 to {SMOKE_MAX_REQUESTS} requests")
        return budget
    budget = DEFAULT_MAX_REQUESTS if args.max_requests is None else args.max_requests
    if not 1 <= budget <= MAX_REQUESTS_LIMIT:
        parser.error(f"--max-requests must be from 1 to {MAX_REQUESTS_LIMIT}")
    return budget


def _emit(document: dict, code: int) -> int:
    print(json.dumps(document, sort_keys=True))
    return code


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int | None:
    if args.command != COMMAND:
        return None
    scope, budget = _scope(args, parser), _budget(args, parser)
    refuse_financial_database(args.market_database, parser)
    label = "live_public_smoke" if args.smoke else "live_public"
    seconds = int(Decimal(budget) / LimiterSettings().decay_per_second)
    print(f"{COMMAND}: up to {budget} public requests, about {seconds // 60} minutes at Kraken's request rate; "
          "Ctrl-C stops safely and the next run resumes.", file=sys.stderr)
    clock = SystemClock()
    database, store = open_store(args.market_database)
    try:
        limiter = RequestLimiter(clock, make_sleeper(), LimiterSettings())
        client = KrakenTrades(make_transport(), clock)
        try:
            result = backfill_trades(store, clock, client, limiter, scope, max_requests=budget,
                                     evidence_label=label, ignore_cursor=args.from_start)
        except KeyboardInterrupt:
            recorded = [run_ for run_ in store.runs() if run_.kind == "trades_backfill" and run_.report]
            document = {} if not recorded else {"run_id": recorded[-1].run_id, **recorded[-1].report}
            return _emit(document or {"status": "interrupted"}, 130)
        for symbol, pair in result.report["pairs"].items():
            if pair["stop_reason"] == "max_requests" and pair["message"]:  # the budget could not finish one candle
                print(f"{COMMAND}: {symbol}: {pair['message']}", file=sys.stderr)
        return _emit({"run_id": result.run_id, **result.report}, 1 if result.status == "failed" else 0)
    finally:
        database.close()
