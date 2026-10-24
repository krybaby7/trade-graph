"""Shared option and opening helpers for candle-store commands."""

from __future__ import annotations

import argparse
from pathlib import Path

from trade_graph.adapters.persistence.market_data import DEFAULT_MARKET_DATABASE, MarketDataDatabase
from trade_graph.application.candle_store import CandleStore


def add_market_database_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--market-database", default=DEFAULT_MARKET_DATABASE,
                        help="private candle-history file, separate from the financial database")


def open_store(path: str | Path) -> tuple[MarketDataDatabase, CandleStore]:
    database = MarketDataDatabase(path)
    return database, CandleStore(database)
