"""Operator commands. Defaults never enable paid calls or live trading."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
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
from trade_graph.domain.errors import TradeGraphError

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
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--database", default="runtime/trade_graph.sqlite")
    doctor.add_argument("--portfolio-id")
    doctor.add_argument("--config")
    subscription = sub.add_parser("subscription-status", help="read-only native subscription CLI metadata")
    subscription.add_argument("--provider", choices=["codex", "claude"], default="codex")
    dashboard = sub.add_parser("dashboard")
    dashboard.add_argument("--database", default="runtime/trade_graph.sqlite")
    dashboard.add_argument("--portfolio-id")
    dashboard.add_argument("--config", help="private owner runtime configuration for dashboard starts")
    dashboard.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "::1"])
    dashboard.add_argument("--port", type=int, default=8000)
    dashboard.add_argument("--session-file", default="runtime/owner-session.json")
    kraken = sub.add_parser("kraken-read-only", help="owner-local bounded native account observation")
    kraken.add_argument("--database", default="runtime/trade_graph.sqlite")
    kraken.add_argument("--owner-directory", default="~/.local/share/trade-graph-owner/kraken")
    kraken.add_argument("--symbol", default="BTC/USD")
    history = sub.add_parser("collect-kraken-history", help="collect bounded public BTC/ETH USD hourly candles")
    history.add_argument("--database", default="runtime/trade_graph.sqlite")
    history.add_argument("--hours", type=int, default=168, help="completed hourly slots to request (1–719)")
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
    run.add_argument("--portfolio-id")
    run.add_argument("--service-run-id", help=argparse.SUPPRESS)
    run.add_argument("--config")
    run.add_argument("--protected-owner", help="read-only owner-pinned protected deployment directory")
    run.add_argument("--once", action="store_true", help="perform one service tick, then exit")
    run.add_argument("--max-ticks", type=int, help="stop after a bounded number of ticks")
    run.add_argument("--public-data", action="store_true", help="enable public REST data; uses no exchange key")
    soak = sub.add_parser("soak", help="explicitly opt in to owner-funded model calls in paper mode")
    soak.add_argument("--mode", default="paper")
    soak.add_argument("--database", default="runtime/trade_graph.sqlite")
    soak.add_argument("--portfolio-id")
    soak.add_argument("--config", required=True)
    soak.add_argument("--duration-seconds", type=int, required=True)
    soak.add_argument("--report", required=True, help="new private evidence file; existing files are retained")
    pause = sub.add_parser("pause")
    pause.add_argument("--profile", required=True)
    pause.add_argument("--database", default="runtime/trade_graph.sqlite")
    pause.add_argument("--reason", default="owner pause")
    pause.add_argument("--position-policy", choices=["manage-only", "flatten"])
    backup = sub.add_parser("backup")
    backup.add_argument("--destination", required=True)
    backup.add_argument("--database", default="runtime/trade_graph.sqlite")
    restore = sub.add_parser("restore")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--database", default="runtime/trade_graph.sqlite")
    restore.add_argument("--offline", action="store_true")
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--database", default="runtime/trade_graph.sqlite")
    report = sub.add_parser("report")
    report.add_argument("--format", default="json", choices=["json"])
    report.add_argument("--database", default="runtime/trade_graph.sqlite")
    report.add_argument("--portfolio-id")
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

    path = Path(database)
    if path.is_symlink() or not path.is_file():
        raise ValueError("database does not exist; initialize a paper account first")
    if path.parent.stat().st_mode & 0o077:
        raise ValueError("paper database requires a private directory with mode 0700")
    clock = SystemClock()
    db = Database(path)
    ledger = Ledger(db, clock)
    execution = Execution(db, ledger, clock, PaperBroker(db, clock))
    return db, execution


def _record_start_failure(database: str, run_id: str | None, error_type: str) -> None:
    """Retain bounded failure type when startup failed before worker acquisition."""
    if not run_id or not Path(database).is_file():
        return
    from trade_graph.adapters.persistence.db import Database
    from trade_graph.application.service_controller import service_finished
    from trade_graph.domain.clock import SystemClock

    db = None
    try:
        db = Database(Path(database))
        service_finished(db, SystemClock(), run_id, error_type)
    except (OSError, ValueError, TradeGraphError):
        # An unavailable journal is diagnosed as an interrupted process by the
        # controller; private exception detail never enters operator output.
        pass
    finally:
        if db is not None:
            db.close()


def _latest_portfolio(db) -> str:
    row = db.execute(
        "SELECT portfolio_id FROM portfolios WHERE mode = 'paper' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise LookupError("no paper portfolio")
    return str(row["portfolio_id"])


def _kraken_summary(result: dict) -> dict:
    """Project only fixed public observation facts from the verified bridge."""
    from trade_graph.api.account_checks import PENDING_LABELS
    from trade_graph.application.venue_conformance import STAGES

    observation = result.get("account_observation", {})
    if not isinstance(observation, dict):
        raise ValueError("invalid observation summary")
    summary = {"symbol": "BTC/USD", "full_reconciliation": "pending"}
    run_id = result.get("run_id")
    if isinstance(run_id, str) and re.fullmatch(r"[0-9a-f]{32}", run_id):
        summary["run_id"] = run_id
    status = result.get("status")
    if status in {"incomplete", "stale"}:
        summary["status"] = status
    stages = observation.get("verified_completed_stages", [])
    if not isinstance(stages, list):
        raise ValueError("invalid observation summary")
    summary["stages"] = [stage for stage in STAGES if stage in stages]
    for key, allowed in (("transport_basis", {"owned_https", "injected_transport"}),
                         ("freshness", {"fresh", "stale"})):
        value = observation.get(key)
        if value in allowed:
            summary[key] = value
    for key in ("started_at", "finished_at"):
        value = observation.get("observation_" + key)
        if isinstance(value, str) and len(value) <= 64:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is not None:
                summary[key] = stamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
    allowed_pending = set(PENDING_LABELS.values())
    pending = observation.get("pending_checks", [])
    if not isinstance(pending, list):
        raise ValueError("invalid observation summary")
    summary["pending"] = [reason for reason in dict.fromkeys(pending) if reason in allowed_pending]
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    owner_command = "kraken-read-only" in (argv if argv is not None else sys.argv[1:])
    if owner_command:
        args, unknown = parser.parse_known_args(argv)
        if unknown:
            print("Read-only observation refused: unrecognized owner command options.", file=sys.stderr)
            return 2
    else:
        args = parser.parse_args(argv)
    if args.version or args.command is None:
        print(
            f"trade-graph {__version__} mode={MODE} "
            f"capital={CAPITAL} {CAPITAL_CURRENCY} reporting={REPORTING_CURRENCY} "
            f"paid_calls_enabled={PAID_CALLS_ENABLED} live_enabled={LIVE_ENABLED}"
        )
        return 0
    if args.command == "subscription-status":
        from trade_graph.adapters.models.subscription_process import native_subscription_status

        print(json.dumps(native_subscription_status(args.provider)))
        return 0
    if args.command == "kraken-read-only":
        try:
            from trade_graph.application.kraken_onboarding import run_kraken_read_only

            result = run_kraken_read_only(Path(args.database), Path(args.owner_directory), args.symbol)
            print(json.dumps(_kraken_summary(result)))
            return 0
        except (Exception, KeyboardInterrupt):
            print("Read-only observation failed or refused; private evidence is retained when collection began.",
                  file=sys.stderr)
            return 1
    if args.command == "collect-kraken-history":
        if not 1 <= args.hours <= 719:
            parser.error("history hours must be between 1 and 719")
        database = None
        try:
            from trade_graph.adapters.market.public import HttpxTextTransport
            from trade_graph.adapters.persistence.db import Database
            from trade_graph.application.collect_price_history import collect_public_hourly_history
            from trade_graph.domain.clock import SystemClock

            path = Path(args.database)
            if (path.is_symlink() or path.parent.is_symlink() or not path.is_file()
                    or path.parent.stat().st_mode & 0o077):
                raise ValueError("an existing private paper database is required")
            database = Database(path)
            if (database.execute("SELECT 1 FROM portfolios WHERE mode != 'paper' LIMIT 1").fetchone()
                    or not database.execute("SELECT 1 FROM portfolios WHERE mode = 'paper' LIMIT 1").fetchone()):
                raise ValueError("an existing paper-only database is required")
            report = collect_public_hourly_history(
                database, SystemClock(), HttpxTextTransport(timeout=10, maximum_response_bytes=1_048_576),
                hours=args.hours, symbols=("BTC/USD", "ETH/USD"),
            )
            print(json.dumps(report))
            return 0 if report["status"] == "ok" else 1
        except (Exception, KeyboardInterrupt):
            print("Public hourly history collection failed or refused; existing records are retained.",
                  file=sys.stderr)
            return 1
        finally:
            if database is not None:
                database.close()
    if args.command == "doctor":
        from trade_graph.application.operations import doctor_report

        report = doctor_report(args.database, portfolio_id=args.portfolio_id, config_path=args.config)
        print(json.dumps(report, default=str))
        return 1 if report["status"] == "error" else 0
    if args.command == "dashboard":
        if not 1 <= args.port <= 65535:
            parser.error("port must be between 1 and 65535")
        import uvicorn

        from trade_graph.api.app import create_app
        from trade_graph.dashboard import dashboard_runtime, owner_session_file

        try:
            runtime = dashboard_runtime(Path(args.database), args.portfolio_id,
                                        config_path=Path(args.config) if args.config else None)
        except ValueError as exc:
            parser.error(str(exc))
        try:
            session_path = owner_session_file(runtime, Path(args.session_file))
            address = f"[{args.host}]" if args.host == "::1" else args.host
            print(f"Dashboard: http://{address}:{args.port}/login")
            print(f"Owner session file: {session_path} (private; paste session_token on the login page)")
            uvicorn.run(create_app(runtime), host=args.host, port=args.port, workers=1, access_log=False)
        except ValueError as exc:
            parser.error(str(exc))
        finally:
            runtime.database.close()
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
        from trade_graph.application.activation import VersionController
        from trade_graph.application.authority import AuthorityRecord, paper_mandate, paper_owner_policy
        from trade_graph.application.ledger import Ledger
        from trade_graph.domain.clock import SystemClock
        from trade_graph.domain.errors import AuthorityDenied
        from trade_graph.domain.money import Money
        from trade_graph.paper_runtime import installed_artifacts

        path = Path(args.database)
        if path.is_symlink() or path.parent.is_symlink():
            parser.error("paper runtime storage cannot be a symlink")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.parent.stat().st_mode & 0o077:
            parser.error("paper runtime storage requires a private directory with mode 0700")
        try:
            capital = Decimal(args.capital)
            if not capital.is_finite() or capital <= 0:
                raise ValueError
        except Exception:
            parser.error("capital must be a positive finite decimal")
        ledger = Ledger(Database(path), SystemClock())
        try:
            with ledger.database.immediate():
                portfolio = ledger.create_portfolio(reporting_currency=args.reporting_currency, mode="paper")
                ledger.deposit(portfolio, args.capital_currency, capital, "opening")
                authority = AuthorityRecord(ledger.database, ledger.clock)
                try:
                    policy = authority.active_policy()
                except AuthorityDenied:
                    policy = paper_owner_policy(revision_id="initial-paper-policy").model_copy(update={
                        "reporting_currency": args.reporting_currency,
                        "virtual_capital": Money(amount=args.capital, currency=args.capital_currency),
                    })
                    authority.install_policy(policy, role="owner")
                authority.install_mandate(paper_mandate(
                    portfolio, symbols=policy.allowed_symbols,
                    gross=str(policy.maximum_gross_exposure_fraction),
                    asset=str(policy.maximum_single_asset_exposure_fraction),
                    quote_age=policy.maximum_quote_age_seconds,
                ), role="owner")
                VersionController(ledger.database, ledger.clock).register_baseline(
                    portfolio, "installed-r1-baseline", installed_artifacts(),
                )
        except (ValueError, AuthorityDenied):
            ledger.database.close()
            parser.error("paper initialization rejected; no portfolio or opening capital was committed")
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
    if args.command in {"run", "soak"}:
        if args.mode != "paper":
            parser.error("run only starts paper mode; live trading stays disabled")
        if args.command == "run" and args.max_ticks is not None and args.max_ticks < 1:
            parser.error("max-ticks must be a positive integer")
        if args.command == "run" and args.once and args.max_ticks is not None:
            parser.error("choose --once or --max-ticks")
        if args.command == "run" and args.protected_owner and args.config:
            parser.error("protected deployment loads paper-config.json only from its pinned owner directory")
        from trade_graph.application.owner_commands import recover_owner_commands
        from trade_graph.application.paper_service import PaperService
        from trade_graph.paper_runtime import assemble_paper_runtime, load_runtime_config

        try:
            if args.command == "run" and args.protected_owner:
                from trade_graph.kernel.deployment_image import read_owner_file
                from trade_graph.paper_runtime import PaperRuntimeConfig

                try:
                    config_bytes = read_owner_file(Path(args.protected_owner), "paper-config.json", 262144)
                except FileNotFoundError:
                    config = PaperRuntimeConfig()
                else:
                    config = PaperRuntimeConfig.model_validate_json(config_bytes)
            else:
                config = load_runtime_config(Path(args.config) if args.config else None)
            if args.command == "run" and args.public_data:
                config = config.model_copy(update={"public_data_enabled": True})
            runtime = assemble_paper_runtime(
                Path(args.database), portfolio_id=args.portfolio_id, config=config,
                protected_owner=Path(args.protected_owner) if args.command == "run" and args.protected_owner else None,
            )
        except (ValueError, LookupError, OSError, TradeGraphError) as exc:
            if args.command == "run":
                _record_start_failure(args.database, args.service_run_id, type(exc).__name__)
            parser.error(f"paper startup failed ({type(exc).__name__}); "
                         "use doctor to inspect private runtime readiness")
        try:
            if args.command == "soak":
                from trade_graph.application.paper_soak import run_funded_soak

                result = asyncio.run(run_funded_soak(
                    runtime, duration_seconds=args.duration_seconds, report_path=Path(args.report),
                ))
                print(json.dumps(result, default=str))
                expenses = result.get("expenses")
                return 1 if result.get("failure_type") or expenses is None or expenses["unresolved_reservations"] else 0
            initial_decisions = runtime.database.execute(
                "SELECT COUNT(*) FROM decisions WHERE portfolio_id = ?", (runtime.portfolio_id,),
            ).fetchone()[0]
            service = PaperService(
                runtime.database, runtime.execution, clock=runtime.clock,
                portfolio_ids=[runtime.portfolio_id], handlers=runtime.handlers,
                artifact_runtime=runtime.artifact_runtime, public_feed=runtime.public_feed,
                secretary=runtime.secretary, tick_interval_seconds=config.tick_interval_seconds,
                recover_commands=lambda: recover_owner_commands(runtime),
                prepare_runtime=runtime.prepare_runtime, runtime_ready=runtime.runtime_ready,
                service_run_id=args.service_run_id,
            )
            outcome = asyncio.run(service.run(max_ticks=1 if args.once else args.max_ticks))
            decisions_created = runtime.database.execute(
                "SELECT COUNT(*) FROM decisions WHERE portfolio_id = ?", (runtime.portfolio_id,),
            ).fetchone()[0] - initial_decisions
            paid_enabled = runtime.paid_calls_enabled and runtime.execution.authority.active_policy().paid_calls_enabled
        except (ValueError, LookupError, OSError, RuntimeError, TradeGraphError) as exc:
            _record_start_failure(args.database, args.service_run_id, type(exc).__name__)
            parser.error(f"paper operation failed ({type(exc).__name__}); "
                         "use doctor to inspect private runtime readiness")
        finally:
            runtime.database.close()
        print(
            json.dumps(
                {
                    "mode": "paper",
                    "portfolio_id": runtime.portfolio_id,
                    "paid_calls_enabled": paid_enabled,
                    "live_enabled": False,
                    "recovery": "degraded" if outcome["failures"] else "reconciled",
                    "new_decisions": decisions_created > 0,
                    "decisions_created": decisions_created,
                    "service": outcome,
                }
            )
        )
        return 1 if outcome["failures"] else 0
    if args.command == "pause":
        from trade_graph.api.controls import _nonflat

        try:
            profile = _normalize_profile(args.profile)
        except ValueError:
            parser.error(f"unknown pause profile {args.profile}")
        if profile == "RUNNING":
            parser.error("use authenticated owner resume after reconciliation to lift a pause")
        db, execution = _paper_stack(args.database)
        try:
            portfolio = _latest_portfolio(db)
            native_holdings = _nonflat(SimpleNamespace(ledger=execution.ledger, portfolio_id=portfolio))
            if profile == "STOPPED" and (native_holdings or execution._has_outstanding(portfolio)):
                if args.position_policy is None:
                    parser.error("nonflat stop requires --position-policy manage-only or flatten")
                profile = "MANAGE_ONLY" if args.position_policy == "manage-only" else "FLATTEN"
            execution.set_pause(portfolio, cast(PauseProfile, profile), "owner", args.reason)
            achieved = asyncio.run(execution.advance_pause(portfolio))
        except LookupError as exc:
            parser.error(str(exc))
        finally:
            db.close()
        print(json.dumps({"portfolio_id": portfolio, "profile": profile, "originator": "owner", "achieved": achieved}))
        return 0
    if args.command == "backup":
        from trade_graph.application.operations import private_backup

        source = Path(args.database)
        if not source.exists():
            parser.error(f"database not found: {source}")
        try:
            digest = private_backup(source, Path(args.destination))
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        print(json.dumps({"destination": args.destination, "sha256": digest}))
        return 0
    if args.command == "restore":
        from trade_graph.application.operations import offline_restore

        try:
            result = offline_restore(Path(args.backup), Path(args.database), offline_confirmed=args.offline)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, default=str))
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
        from trade_graph.application.operations import financial_report

        try:
            result = financial_report(args.database, portfolio_id=args.portfolio_id)
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps(result, default=str))
        return 0
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
