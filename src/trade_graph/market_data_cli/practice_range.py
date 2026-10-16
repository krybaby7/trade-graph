"""Practice-range commands (T29). Discovered like the candle commands, so no shared list changes.

- ``practice-range-register``: register a strategy version from a JSON file (the grammar in
  ``contracts/practice_range.py``; examples in ``strategies/practice``).
- ``practice-range-study``: fix a study's earlier tuning and later confirmation windows (whole UTC days).
- ``practice-range-run``: replay one version on one candle series for a phase, once per cost profile, and store each
  labelled report.
- ``practice-range-report``: read stored reports (read-only; never creates or upgrades a database).
- ``practice-range-verify``: replay a stored report from its own inputs and record whether it was reproduced.

Reports are evidence for the Trader's discretion, not a pass mark, a profitability claim or permission for real money
(D138 item 5). Nothing here calls a model, spends money or sends an order. The state database must already exist (an
initialised paper account) in a private folder; the candle-history file is the separate ``--market-database``.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from trade_graph.domain.clock import Clock, SystemClock
from trade_graph.domain.errors import TradeGraphError

REGISTER = "practice-range-register"
STUDY = "practice-range-study"
RUN = "practice-range-run"
REPORT = "practice-range-report"
VERIFY = "practice-range-verify"
DEFAULT_DATABASE = "runtime/trade_graph.sqlite"
SOURCE_VENUE = {"kraken_public_ohlc": "kraken", "kraken_ohlcvt_archive": "kraken", "kraken_public_trades": "kraken",
                "okx_public_candles": "okx", "synthetic": "synthetic"}


def build_clock() -> Clock:
    return SystemClock()


def register(sub: argparse._SubParsersAction) -> None:
    from trade_graph.contracts.candles import CANDLE_SOURCES
    from trade_graph.market_data_cli._common import add_market_database_option

    def database(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--database", default=DEFAULT_DATABASE, help="the paper state database")

    command = sub.add_parser(REGISTER, help="practice range: register a strategy version from a JSON file")
    database(command)
    command.add_argument("--strategy", required=True, help="the strategy version's JSON file")

    command = sub.add_parser(STUDY, help="practice range: fix a study's tuning and later confirmation windows")
    database(command)
    command.add_argument("--study-id", required=True)
    command.add_argument("--strategy-id", required=True, help="the strategy family the study replays")
    for name in ("tune-start", "tune-end", "confirm-start", "confirm-end"):
        command.add_argument(f"--{name}", required=True, help="a UTC day, YYYY-MM-DD (ends are excluded)")
    command.add_argument("--note", default="")

    command = sub.add_parser(RUN, help="practice range: replay one version for a study phase under each cost profile")
    database(command)
    add_market_database_option(command)
    command.add_argument("--study-id", required=True)
    command.add_argument("--phase", required=True, choices=["tune", "confirm"])
    command.add_argument("--strategy", required=True, help="the registered version, for example slow-trend-pullback@1")
    command.add_argument("--symbol", required=True, help="the candle series' symbol, for example BTC/USD")
    command.add_argument("--source", required=True, choices=CANDLE_SOURCES)
    command.add_argument("--profile", action="append", default=None,
                         help="a cost profile id, id@revision or 'all' (default all); repeat for several")
    command.add_argument("--capital", default="10000", help="starting virtual capital in the quote currency")
    command.add_argument("--window-start", help="tuning only: a sub-window inside the study's tuning window")
    command.add_argument("--window-end")
    command.add_argument("--price-increment", help="the venue's price tick, for rounding order levels")
    command.add_argument("--quantity-increment", help="the venue's lot size (default 0.00000001)")
    command.add_argument("--min-quantity")
    command.add_argument("--min-notional")
    command.add_argument("--benchmark-symbol", help="the coin held as the benchmark (default BTC/<quote>)")
    command.add_argument("--benchmark-source", choices=CANDLE_SOURCES)

    command = sub.add_parser(REPORT, help="practice range: read stored reports (read-only)")
    database(command)
    command.add_argument("--run-id")
    command.add_argument("--strategy", help="only this registered version's reports")
    command.add_argument("--study-id")
    command.add_argument("--format", choices=["json", "text"], default="json")

    command = sub.add_parser(VERIFY, help="practice range: replay a stored report and say whether it reproduces")
    database(command)
    add_market_database_option(command)
    command.add_argument("--run-id", required=True)


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int | None:
    handlers = {REGISTER: _register, STUDY: _study, RUN: _run, REPORT: _report, VERIFY: _verify}
    handler = handlers.get(args.command)
    return None if handler is None else handler(args, parser)


def _state_database(path: str, parser: argparse.ArgumentParser) -> Path:
    target = Path(path)
    if target.is_symlink() or target.parent.is_symlink() or not target.is_file():
        parser.error(f"no paper state database at {path}; initialise the paper account first")
    if target.parent.stat().st_mode & 0o077:
        parser.error("the paper state database requires a private directory with mode 0700")
    return target


def _day(value: str | None, parser: argparse.ArgumentParser, name: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        parser.error(f"--{name} must be a day such as 2026-07-01")


def _decimal(value: str | None, parser: argparse.ArgumentParser, name: str) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        parser.error(f"--{name} must be a decimal number")
    if not number.is_finite() or number < 0:
        parser.error(f"--{name} must be a finite, non-negative number")
    return number


def _open(args, parser):
    from trade_graph.adapters.persistence.db import Database
    from trade_graph.application.practice_range.store import PracticeRangeStore

    database = Database(_state_database(args.database, parser))
    clock = build_clock()
    return database, clock, PracticeRangeStore(database, clock)


def _print(document: dict) -> None:
    print(json.dumps(document, sort_keys=True))


def _register(args, parser) -> int:
    from pydantic import ValidationError

    from trade_graph.contracts.practice_range import PracticeStrategy

    try:
        strategy = PracticeStrategy.model_validate_json(Path(args.strategy).read_text(encoding="utf-8"))
    except OSError as error:
        parser.error(f"cannot read the strategy file ({type(error).__name__})")
    except ValidationError as error:
        fields = sorted({".".join(map(str, item["loc"])) or "document" for item in error.errors()})
        parser.error(f"the strategy file is outside the grammar ({', '.join(fields)[:400]}); nothing was registered")
    database, _, store = _open(args, parser)
    try:
        registered = store.register(strategy)
    except TradeGraphError as error:
        parser.error(str(error))
    finally:
        database.close()
    _print({"command": REGISTER, "strategy_ref": registered.ref, "content_sha256": registered.content_sha256,
            "created": registered.created, "registered_at": registered.registered_at})
    return 0


def _study(args, parser) -> int:
    from pydantic import ValidationError

    from trade_graph.contracts.practice_range import ReplayWindow, StudyPlan

    try:
        plan = StudyPlan(study_id=args.study_id, strategy_id=args.strategy_id, note=args.note,
                         tune=ReplayWindow(start=_day(args.tune_start, parser, "tune-start"),
                                           end=_day(args.tune_end, parser, "tune-end")),
                         confirm=ReplayWindow(start=_day(args.confirm_start, parser, "confirm-start"),
                                              end=_day(args.confirm_end, parser, "confirm-end")))
    except ValidationError as error:
        parser.error(f"study refused: {error.errors()[0]['msg']}")
    database, _, store = _open(args, parser)
    try:
        stored, created = store.create_study(plan)
    except TradeGraphError as error:
        parser.error(str(error))
    finally:
        database.close()
    _print({"command": STUDY, "created": created, "study": stored.to_document()})
    return 0


def _rules(args, parser):
    from trade_graph.contracts.models import InstrumentRules
    from trade_graph.domain.errors import ValidationFailure

    tick = _decimal(args.price_increment, parser, "price-increment")
    lot = _decimal(args.quantity_increment, parser, "quantity-increment")
    minimum = _decimal(args.min_quantity, parser, "min-quantity")
    notional = _decimal(args.min_notional, parser, "min-notional")
    if tick is None and lot is None and minimum is None and notional is None:
        return None
    if tick is None or lot is None or tick <= 0 or lot <= 0:
        parser.error("instrument rules need a positive --price-increment and --quantity-increment")
    if args.symbol.count("/") != 1:
        parser.error("--symbol is BASE/QUOTE")
    base, quote = args.symbol.split("/")
    try:
        return InstrumentRules(venue=SOURCE_VENUE.get(args.source, "unknown"), symbol=args.symbol, base_asset=base,
                               quote_asset=quote, price_increment=tick, quantity_increment=lot,
                               min_quantity=lot if minimum is None else minimum,
                               min_notional=Decimal("0") if notional is None else notional,
                               synthetic=args.source == "synthetic")
    except (ValueError, ValidationFailure) as error:
        parser.error(f"instrument rules refused: {error}")


def _run(args, parser) -> int:
    from trade_graph.application.practice_range.service import PracticeRange
    from trade_graph.application.practice_range.summary import report_overview
    from trade_graph.contracts.practice_range import ReplayWindow
    from trade_graph.market_data_cli._guard import open_store_or_exit, refuse_financial_database

    capital = _decimal(args.capital, parser, "capital")
    if capital is None or capital <= 0:
        parser.error("--capital must be positive")
    window = None
    if args.window_start or args.window_end:
        if args.phase != "tune" or not (args.window_start and args.window_end):
            parser.error("--window-start and --window-end together, for a tuning sub-window only")
        try:
            window = ReplayWindow(start=_day(args.window_start, parser, "window-start"),
                                  end=_day(args.window_end, parser, "window-end"))
        except ValueError as error:
            parser.error(f"window refused: {error}")
    rules = _rules(args, parser)
    refuse_financial_database(args.market_database, parser)
    market, candles = open_store_or_exit(args.market_database, parser)
    database = None
    try:
        database, clock, store = _open(args, parser)
        practice = PracticeRange(store, candles, clock)
        records = practice.run(study_id=args.study_id, phase=args.phase, strategy_ref=args.strategy,
                               symbol=args.symbol, source=args.source, profiles=args.profile or ["all"],
                               capital=capital, rules=rules, window=window, benchmark_symbol=args.benchmark_symbol,
                               benchmark_source=args.benchmark_source)
    except (TradeGraphError, sqlite3.DatabaseError) as error:
        parser.error(f"practice-range run refused: {error}")
    finally:
        if database is not None:
            database.close()
        market.close()
    _print({"command": RUN, "paid_calls": 0, "orders_sent": 0,
            "reports": [report_overview(record) for record in records]})
    return 0


def _report(args, parser) -> int:
    from trade_graph.application.practice_range.store import ReadOnlyReports
    from trade_graph.application.practice_range.summary import report_overview, report_text

    try:
        reports = ReadOnlyReports(args.database)
    except TradeGraphError as error:
        parser.error(f"{error}; the report only reads one that already exists")
    try:
        records = ([reports.run(args.run_id)] if args.run_id
                   else reports.runs(study_id=args.study_id, strategy_ref=args.strategy))
    except TradeGraphError as error:
        parser.error(str(error))
    finally:
        reports.close()
    if args.format == "text":
        print("\n\n".join(report_text(record) for record in records) if records else "No practice-range reports.")
    elif args.run_id:
        _print({"command": REPORT, "report": records[0].report, "run_id": records[0].run_id})
    else:
        _print({"command": REPORT, "reports": [report_overview(record) for record in records]})
    return 0


def _verify(args, parser) -> int:
    from trade_graph.application.practice_range.service import PracticeRange
    from trade_graph.market_data_cli._guard import open_store_or_exit, require_existing_market_database

    require_existing_market_database(args.market_database, parser)
    market, candles = open_store_or_exit(args.market_database, parser)
    database = None
    try:
        database, clock, store = _open(args, parser)
        check = PracticeRange(store, candles, clock).verify(args.run_id)
    except (TradeGraphError, sqlite3.DatabaseError) as error:
        parser.error(f"verification refused: {error}")
    finally:
        if database is not None:
            database.close()
        market.close()
    _print({"command": VERIFY, "run_id": check.run_id, "reproduced": check.reproduced, **check.document})
    return 0 if check.reproduced else 1
