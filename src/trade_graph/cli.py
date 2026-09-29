"""Operator commands. Defaults never enable paid calls or live trading."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC
from decimal import Decimal
from pathlib import Path
from typing import cast

from trade_graph import (
    CAPITAL,
    CAPITAL_CURRENCY,
    LIVE_ENABLED,
    MODE,
    PAID_CALLS_ENABLED,
    REPORTING_CURRENCY,
    __version__,
)
from trade_graph.contracts.models import PauseProfile

PAUSE_PROFILES = {
    "RUNNING",
    "PAUSE_DECISIONS",
    "NO_NEW_EXPOSURE",
    "MANAGE_ONLY",
    "CANCEL_ALL",
    "FLATTEN",
    "STOPPED",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trade-graph")
    parser.add_argument("--version", action="store_true")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("doctor")
    demo = sub.add_parser("demo")
    demo.add_argument("--offline", action="store_true")
    demo.add_argument("--work", default="runtime/demo")
    init = sub.add_parser("init")
    init.add_argument("--mode", default="paper")
    init.add_argument("--capital", default="10000")
    init.add_argument("--capital-currency", default="USD")
    init.add_argument("--reporting-currency", default="EUR")
    init.add_argument("--database", default="runtime/trade_graph.sqlite")
    run = sub.add_parser("run")
    run.add_argument("--mode", default="paper")
    run.add_argument("--database", default="runtime/trade_graph.sqlite")
    pause = sub.add_parser("pause")
    pause.add_argument("--profile", required=True)
    pause.add_argument("--database", default="runtime/trade_graph.sqlite")
    pause.add_argument("--reason", default="owner pause")
    backup = sub.add_parser("backup")
    backup.add_argument("--destination", required=True)
    backup.add_argument("--database", default="runtime/trade_graph.sqlite")
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--database", default="runtime/trade_graph.sqlite")
    report = sub.add_parser("report")
    report.add_argument("--format", default="json", choices=["json"])
    report.add_argument("--database", default="runtime/trade_graph.sqlite")
    return parser


def _normalize_profile(value: str) -> str:
    normalized = value.strip().upper().replace("-", "_")
    if normalized not in PAUSE_PROFILES:
        raise ValueError(value)
    return normalized


def _paper_stack(database: str):
    from trade_graph.adapters.brokers.paper import PaperBroker
    from trade_graph.adapters.persistence.db import Database
    from trade_graph.application.execution import Execution
    from trade_graph.application.ledger import Ledger
    from trade_graph.domain.clock import SystemClock

    clock = SystemClock()
    db = Database(Path(database))
    ledger = Ledger(db, clock)
    execution = Execution(db, ledger, clock, PaperBroker(db, clock))
    return db, execution


def _latest_portfolio(db) -> str:
    row = db.execute(
        "SELECT portfolio_id FROM portfolios WHERE mode = 'paper' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise LookupError("no paper portfolio")
    return str(row["portfolio_id"])


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version or args.command is None:
        print(
            f"trade-graph {__version__} mode={MODE} "
            f"capital={CAPITAL} {CAPITAL_CURRENCY} reporting={REPORTING_CURRENCY} "
            f"paid_calls_enabled={PAID_CALLS_ENABLED} live_enabled={LIVE_ENABLED}"
        )
        return 0
    if args.command == "doctor":
        print("doctor: paper defaults loaded; paid calls disabled; live trading disabled")
        print("schema=available credentials=not-required")
        print("credentialed_providers=pending live=disabled")
        return 0
    if args.command == "demo":
        if not args.offline:
            parser.error("demo requires --offline unless a later credentialed command is configured")
        from trade_graph.demo import run_offline

        report = run_offline(Path(args.work))
        print(json.dumps(report, default=str))
        return 0
    if args.command == "init":
        if args.mode != "paper":
            parser.error("only paper mode can be initialized by this command")
        from datetime import datetime

        from trade_graph.adapters.persistence.db import Database
        from trade_graph.application.ledger import Ledger
        from trade_graph.domain.clock import SystemClock

        path = Path(args.database)
        ledger = Ledger(Database(path), SystemClock())
        portfolio = ledger.create_portfolio(reporting_currency=args.reporting_currency, mode="paper")
        ledger.deposit(portfolio, args.capital_currency, Decimal(args.capital), "opening")
        print(
            json.dumps(
                {
                    "portfolio_id": portfolio,
                    "mode": "paper",
                    "capital": args.capital,
                    "capital_currency": args.capital_currency,
                    "reporting_currency": args.reporting_currency,
                    "paid_calls_enabled": False,
                    "live_enabled": False,
                    "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                }
            )
        )
        ledger.database.close()
        return 0
    if args.command == "run":
        if args.mode != "paper":
            parser.error("run only starts paper mode; live trading stays disabled")
        db, execution = _paper_stack(args.database)
        try:
            portfolio = _latest_portfolio(db)
            asyncio.run(execution.startup())
        except LookupError as exc:
            parser.error(str(exc))
        finally:
            db.close()
        print(
            json.dumps(
                {
                    "mode": "paper",
                    "portfolio_id": portfolio,
                    "paid_calls_enabled": False,
                    "live_enabled": False,
                    "recovery": "reconciled",
                    "new_decisions": False,
                }
            )
        )
        return 0
    if args.command == "pause":
        try:
            profile = _normalize_profile(args.profile)
        except ValueError:
            parser.error(f"unknown pause profile {args.profile}")
        db, execution = _paper_stack(args.database)
        try:
            portfolio = _latest_portfolio(db)
            execution.set_pause(portfolio, cast(PauseProfile, profile), "owner", args.reason)
        except LookupError as exc:
            parser.error(str(exc))
        finally:
            db.close()
        print(json.dumps({"portfolio_id": portfolio, "profile": profile, "originator": "owner"}))
        return 0
    if args.command == "backup":
        from trade_graph.adapters.persistence.backup import backup_database

        source = Path(args.database)
        if not source.exists():
            parser.error(f"database not found: {source}")
        digest = backup_database(source, Path(args.destination))
        print(json.dumps({"destination": args.destination, "sha256": digest}))
        return 0
    if args.command == "reconcile":
        db, execution = _paper_stack(args.database)
        try:
            portfolio = _latest_portfolio(db)
            asyncio.run(execution.reconcile())
        except LookupError as exc:
            parser.error(str(exc))
        finally:
            db.close()
        print(
            json.dumps(
                {
                    "portfolio_id": portfolio,
                    "reconciled": True,
                    "paid_calls_enabled": False,
                    "live_enabled": False,
                }
            )
        )
        return 0
    if args.command == "report":
        print(
            json.dumps(
                {
                    "format": args.format,
                    "paid_calls_enabled": False,
                    "live_enabled": False,
                    "credentialed_soak": "pending",
                    "mode": "paper",
                }
            )
        )
        return 0
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
