"""``trade-graph market-types``: the newest market-type labels, read only (W11-A, C10; D287).

Discovered like the other commands, so ``cli.py`` is unchanged. It opens the state database read-only and never
creates or upgrades it, and prints the newest label of each coin (or of ``--symbol``) at ``--as-of`` (default: the
newest of all): rising, sideways, falling or unclear, the candle it rests on and why, in plain words or as JSON. The
labels are software's (``kernel/market_type.py``), made by the hourly scan; no AI takes part, and reading them starts
nothing. A label older than two hours at the moment read is marked as old.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

COMMAND = "market-types"
DEFAULT_DATABASE = "runtime/trade_graph.sqlite"


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(COMMAND, help="read-only: each coin's newest market type (rising, sideways, falling, "
                                          "unclear) from the hourly scan; no AI")
    parser.add_argument("--database", default=DEFAULT_DATABASE, help="the paper state database")
    parser.add_argument("--symbol", action="append", help="only this coin, for example SOL/USD (repeatable)")
    parser.add_argument("--as-of", help="the newest labels at this UTC moment, ending in Z (default: the newest)")
    parser.add_argument("--format", choices=["text", "json"], default="text")


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int | None:
    if args.command != COMMAND:
        return None
    from trade_graph.application.market_types.store import read_latest_market_types
    from trade_graph.domain.errors import TradeGraphError

    as_of = _moment(args.as_of, parser)
    target = Path(args.database)
    if not target.is_file() or target.stat().st_size == 0:
        parser.error(f"no state database at {args.database}; the report only reads one that already exists")
    try:
        connection = sqlite3.connect(f"{target.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.DatabaseError:
        parser.error(f"cannot open {args.database} read-only")
    connection.row_factory = sqlite3.Row
    try:
        labels = read_latest_market_types(connection, symbols=args.symbol, as_of=as_of)
    except (sqlite3.DatabaseError, TradeGraphError) as error:
        parser.error(f"cannot read the market-type labels ({type(error).__name__})")
    finally:
        connection.close()
    ordered = [labels[symbol] for symbol in sorted(labels)]
    if args.format == "json":
        print(json.dumps({"command": COMMAND, "found": bool(ordered), "as_of_utc": args.as_of,
                          "model_calls": 0, "labels": [item.to_document() for item in ordered]}))
    else:
        print(format_text(ordered, as_of))
    return 0


def _moment(value: str | None, parser: argparse.ArgumentParser) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parser.error(f"cannot read the time {value!r}; use for example 2026-10-20T14:05:00Z")
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        parser.error("--as-of must be UTC, ending in Z")
    return parsed


def format_text(labels: list, as_of: datetime | None) -> str:
    """Plain words for the owner: one line per coin, then the count per type."""
    if not labels:
        return "No market types have been recorded yet."
    from trade_graph.application.market_types.store import DEFAULT_LABEL_AGE

    first = labels[0]
    moment = as_of or max(item.as_of for item in labels)
    lines = [f"Market types (software's labels from {first.interval_minutes}-minute candles, {first.rules_ref}; no AI, "
             "nothing was bought or sold). A label is information for the Trading desk, not advice."]
    for item in labels:
        when = item.as_of.strftime("%Y-%m-%d %H:%M")
        old = "" if item.is_fresh(moment, DEFAULT_LABEL_AGE) else " (older than 2 hours)"
        lines.append(f"- {item.sentence()} Labelled {when} UTC{old}.")
    counts = Counter(item.market_type for item in labels)
    lines.append(f"{counts['rising']} rising, {counts['sideways']} sideways, {counts['falling']} falling, "
                 f"{counts['unclear']} unclear.")
    return "\n".join(lines)


__all__ = ["COMMAND", "format_text", "register", "run"]
