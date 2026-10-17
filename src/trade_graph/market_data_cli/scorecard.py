"""The owner's paper scorecard command (T42). Discovered here like the other commands, so ``cli.py`` is unchanged.

``scorecard`` reads the state database without writing or upgrading it and prints the paper account's result after
fees against holding cash and holding BTC over one period, the trades, win rate, average win and loss, worst fall
and losing streak, the EUR view, and the AI spending of the same days from receipts. It is information only:
nothing is paused, limited, sent or allowed because of it, and it never permits real-money trading.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from trade_graph.domain.clock import Clock, SystemClock

COMMAND = "scorecard"
DEFAULT_DATABASE = "runtime/trade_graph.sqlite"


def build_clock() -> Clock:
    return SystemClock()


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(COMMAND, help="read-only paper scorecard: result after fees against cash and holding "
                                          "BTC, with AI spending beside it (T42)")
    parser.add_argument("--database", default=DEFAULT_DATABASE, help="the paper state database")
    parser.add_argument("--portfolio-id", help="the paper portfolio to score (default: the newest)")
    parser.add_argument("--start", help="UTC start ending in Z (default: the opening deposit, or the first BTC "
                                        "price after it)")
    parser.add_argument("--end", help="UTC end ending in Z (default: now)")
    parser.add_argument("--cost-profile", default="kraken-spot-starting-tier",
                        help="the fee profile holding BTC pays, and selling coins still held at the end (default: "
                             "the paper broker's, kraken-spot-starting-tier)")
    parser.add_argument("--format", choices=["json", "text"], default="json")


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int | None:
    if args.command != COMMAND:
        return None
    from trade_graph.application.scorecard import ScorecardUnavailable, compute_scorecard, format_scorecard_text

    now = build_clock().now()
    start = _when(args.start, "--start", parser, now)
    end = _when(args.end, "--end", parser, now) or now
    connection = _read_only(args.database, parser)
    try:
        connection.execute("BEGIN")  # one consistent read of every table
        portfolio = _portfolio(connection, args.portfolio_id, parser)
        card = compute_scorecard(connection, portfolio_id=portfolio, start=start, end=end,
                                 cost_profile=args.cost_profile)
    except ScorecardUnavailable as error:
        print(json.dumps({"report": "paper-scorecard", "available": False, "reason": str(error),
                          "paid_calls_enabled": False, "live_enabled": False}))
        return 1
    except sqlite3.DatabaseError as error:
        parser.error(f"cannot read the paper records ({type(error).__name__})")
    finally:
        connection.close()
    print(format_scorecard_text(card) if args.format == "text" else json.dumps(card.document()))
    return 0


def _when(value: str | None, flag: str, parser, now: datetime) -> datetime | None:
    if value is None:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parser.error(f"cannot read the time {value!r} for {flag}; use for example 2026-11-02T00:00:00Z")
    if moment.tzinfo is None:
        parser.error(f"{flag} must be UTC, ending in Z")
    if moment > now:
        parser.error(f"{flag} is in the future; the scorecard reads what has been recorded")
    return moment


def _read_only(path: str, parser) -> sqlite3.Connection:
    target = Path(path)
    if not target.is_file() or target.stat().st_size == 0:
        parser.error(f"no state database at {path}; this command only reads one that already exists")
    try:
        connection = sqlite3.connect(f"{target.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.DatabaseError:
        parser.error(f"cannot open {path} read-only")
    connection.row_factory = sqlite3.Row
    return connection


def _portfolio(connection, portfolio_id: str | None, parser) -> str:
    if portfolio_id is not None:
        found = connection.execute("SELECT 1 FROM portfolios WHERE portfolio_id = ? AND mode = 'paper'",
                                   (portfolio_id,)).fetchone()
        if found is None:
            parser.error(f"no paper portfolio {portfolio_id} in this database")
        return portfolio_id
    row = connection.execute(
        "SELECT portfolio_id FROM portfolios WHERE mode = 'paper' ORDER BY created_at DESC LIMIT 1").fetchone()
    if row is None:
        parser.error("no paper portfolio in this database")
    return str(row["portfolio_id"])
