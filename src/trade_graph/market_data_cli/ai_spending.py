"""Owner commands for AI spending (T40). Discovered here like the other commands, so ``cli.py`` is unchanged.

``ai-spending-report`` reads the state database without writing or upgrading it and prints Perplexity and Jev
spending per department from receipts (actual, estimated and not known yet apart), the testing alerts, the
prepaid credit readings and Codex use per department. ``ai-spending-record`` records the prepaid credit the owner
read on the provider's account page, or another testing alert amount: it previews by default and writes one
append-only row only with ``--confirm``. Both are information only; neither pauses, limits nor sends anything.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from trade_graph.domain.clock import Clock, SystemClock, utc_iso
from trade_graph.domain.errors import TradeGraphError
from trade_graph.domain.money import canonical_decimal

REPORT = "ai-spending-report"
RECORD = "ai-spending-record"
DEFAULT_DATABASE = "runtime/trade_graph.sqlite"
EFFECT = "information only: nothing is paused, limited or sent because of this record"


def build_clock() -> Clock:
    return SystemClock()


def register(sub: argparse._SubParsersAction) -> None:
    report = sub.add_parser(REPORT, help="read-only AI spending per department from receipts (T40)")
    report.add_argument("--database", default=DEFAULT_DATABASE, help="the paper state database")
    report.add_argument("--format", choices=["json", "text"], default="json")
    record = sub.add_parser(RECORD, help="owner: preview a prepaid credit reading or testing alert amount; "
                                         "--confirm records it (information only)")
    record.add_argument("--database", default=DEFAULT_DATABASE, help="the paper state database")
    record.add_argument("--service", required=True, choices=["perplexity", "jev"])
    record.add_argument("--prepaid-credit", help="USD balance read on the provider's own account page")
    record.add_argument("--checked-at", help="when you read the balance, UTC ending in Z (default: now)")
    record.add_argument("--testing-alert", help="USD amount at which HQ alerts you during testing")
    record.add_argument("--confirm", action="store_true", help="record the previewed value")


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int | None:
    if args.command == REPORT:
        return _report(args, parser)
    if args.command == RECORD:
        return _record(args, parser)
    return None


def _read_only(path: str, parser: argparse.ArgumentParser) -> sqlite3.Connection:
    target = Path(path)
    if not target.is_file() or target.stat().st_size == 0:
        parser.error(f"no state database at {path}; this command only reads one that already exists")
    try:
        connection = sqlite3.connect(f"{target.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.DatabaseError:
        parser.error(f"cannot open {path} read-only")
    connection.row_factory = sqlite3.Row
    return connection


def _report(args, parser) -> int:
    from trade_graph.application.ai_spending import format_report_text, spending_report

    connection = _read_only(args.database, parser)
    try:
        report = spending_report(connection, build_clock().now())
    except sqlite3.DatabaseError as error:
        parser.error(f"cannot read the spending records ({type(error).__name__})")
    finally:
        connection.close()
    print(format_report_text(report) if args.format == "text" else json.dumps(report.document()))
    return 0


def _amount(value: str, parser, *, positive: bool) -> Decimal:
    try:
        amount = Decimal(value)
    except InvalidOperation:
        parser.error(f"cannot read the amount {value!r}; give USD as a plain number such as 25 or 19.50")
    if not amount.is_finite() or amount < 0 or (positive and amount == 0):
        parser.error("the amount must be a positive USD number" if positive else
                     "the amount must be zero or more USD")
    return amount


def _when(value: str | None, parser, now: datetime) -> datetime:
    if value is None:
        return now
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parser.error(f"cannot read the time {value!r}; use for example 2026-10-16T09:00:00Z")
    if moment.tzinfo is None:
        parser.error("the time must be UTC, ending in Z")
    if moment > now:
        parser.error("the balance can't have been read in the future")
    return moment


def _record(args, parser) -> int:
    from trade_graph.adapters.persistence.ai_spending_schema import TABLE
    from trade_graph.adapters.persistence.db import Database
    from trade_graph.application.ai_spending import record_owner_amount

    if (args.prepaid_credit is None) == (args.testing_alert is None):
        parser.error("give exactly one of --prepaid-credit or --testing-alert")
    if args.testing_alert is not None and args.checked_at is not None:
        parser.error("--checked-at belongs to a prepaid credit reading")
    path = Path(args.database)
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
        parser.error("database does not exist; initialize a paper account first")
    if path.parent.stat().st_mode & 0o077:
        parser.error("paper database requires a private directory with mode 0700")
    now = build_clock().now()
    kind = "prepaid_credit" if args.prepaid_credit is not None else "testing_alert"
    amount = _amount(args.prepaid_credit or args.testing_alert, parser, positive=kind == "testing_alert")
    observed = _when(args.checked_at, parser, now) if kind == "prepaid_credit" else now
    previous = _previous(args.database, parser, args.service, kind, TABLE)
    applied = False
    if args.confirm:
        database = None
        try:
            database = Database(path)
            record_owner_amount(database, service=args.service, kind=kind, amount=amount, observed_at=observed,
                                now=now, record_id=str(uuid.uuid4()))
            applied = True
        except (ValueError, OSError, sqlite3.DatabaseError, TradeGraphError) as error:
            parser.error(f"AI spending record refused ({type(error).__name__}: {error}); nothing was written")
        finally:
            if database is not None:
                database.close()
    print(json.dumps({"command": RECORD, "applied": applied, "service": args.service, "kind": kind,
                      "amount_usd": canonical_decimal(amount), "observed_at": utc_iso(observed),
                      "previous": previous, "effect": EFFECT, "paid_calls_enabled": False, "live_enabled": False}))
    return 0


def _previous(path: str, parser, service: str, kind: str, table: str) -> dict | None:
    connection = _read_only(path, parser)
    try:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is None:
            return None
        row = connection.execute(f"""SELECT amount_usd, observed_at FROM {table} WHERE service=? AND kind=?
            ORDER BY sequence DESC LIMIT 1""", (service, kind)).fetchone()
    except sqlite3.DatabaseError as error:
        parser.error(f"cannot read the spending records ({type(error).__name__})")
    finally:
        connection.close()
    return None if row is None else {"amount_usd": row["amount_usd"], "observed_at": row["observed_at"]}
