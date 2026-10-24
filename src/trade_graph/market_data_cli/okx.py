"""OKX candle commands (T29). Discovered like the other candle commands.

- ``candles-okx-import``: store a recorded folder of OKX history-candles pages (``manifest.json`` plus
  ``page-NNN.json``) under the label its manifest gives, ``synthetic_fixture`` or ``recorded_public_fixture``.
- ``candles-okx-fetch``: page back from the newest candle on OKX's public endpoint (no key, GET only) and store each
  page as ``live_public``; ``--record`` also keeps the pages verbatim for a later fixture. This is the only command
  here that uses the network; it is meant for step L2 on the owner's PC.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from trade_graph.domain.clock import Clock, SystemClock
from trade_graph.domain.errors import TradeGraphError

IMPORT = "candles-okx-import"
FETCH = "candles-okx-fetch"


def build_clock() -> Clock:
    return SystemClock()


def build_transport():
    from trade_graph.adapters.market.okx_candles import make_okx_transport

    return make_okx_transport()


def register(sub: argparse._SubParsersAction) -> None:
    from trade_graph.market_data_cli._common import add_market_database_option

    command = sub.add_parser(IMPORT, help="store a recorded folder of OKX candle pages (never downloads)")
    add_market_database_option(command)
    command.add_argument("--recording", required=True, help="the folder with manifest.json and page-NNN.json")
    command = sub.add_parser(FETCH, help="page OKX public candles into the candle history (network, step L2)")
    add_market_database_option(command)
    command.add_argument("--inst-id", required=True, help="an OKX spot instrument, for example SOL-USDT")
    command.add_argument("--interval", type=int, default=15, choices=[1, 5, 15, 60])
    command.add_argument("--pages", type=int, default=1, help="pages of 100 candles to fetch, newest first")
    command.add_argument("--record", help="also keep the pages verbatim in this new folder")
    command.add_argument("--before-ms", type=int, help="resume: start at candles older than this open time "
                         "(Unix milliseconds), for example the first_open of a run that stopped early")


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int | None:
    if args.command == IMPORT:
        return _import(args, parser)
    if args.command == FETCH:
        return _fetch(args, parser)
    return None


def _import(args, parser) -> int:
    from trade_graph.application.okx_history import import_okx_recording
    from trade_graph.market_data_cli._guard import open_store_or_exit, refuse_financial_database

    refuse_financial_database(args.market_database, parser)
    database, store = open_store_or_exit(args.market_database, parser)
    try:
        report = import_okx_recording(store, Path(args.recording), build_clock())
    except (TradeGraphError, OSError, UnicodeError) as error:
        parser.error(f"OKX recording refused: {error}")
    finally:
        database.close()
    print(json.dumps({"command": IMPORT, **report}, sort_keys=True))
    return 0


def _fetch(args, parser) -> int:
    from trade_graph.adapters.market.okx_candles import OkxCandleFetcher, okx_symbol
    from trade_graph.application.okx_history import collect_okx_candles
    from trade_graph.market_data_cli._guard import open_store_or_exit, refuse_financial_database

    try:
        okx_symbol(args.inst_id)
    except TradeGraphError as error:
        parser.error(str(error))
    if args.record is not None and Path(args.record).exists():
        parser.error("--record must name a new folder")
    refuse_financial_database(args.market_database, parser)
    database, store = open_store_or_exit(args.market_database, parser)
    clock = build_clock()
    try:
        report = collect_okx_candles(store, OkxCandleFetcher(build_transport(), clock), clock,
                                     inst_id=args.inst_id, interval_minutes=args.interval, pages=args.pages,
                                     record_to=args.record, before_ms=args.before_ms)
    except (TradeGraphError, OSError) as error:
        parser.error(f"OKX fetch stopped: {error}; candles saved so far are kept")
    finally:
        database.close()
    print(json.dumps({"command": FETCH, **report}, sort_keys=True))
    return 0 if report["status"] != "failed" else 1
