"""Path checks shared by candle-store commands: never the financial database, only real candle-history files."""

from __future__ import annotations

import argparse
import shlex
import sqlite3
from pathlib import Path

from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.application.candle_store import CandleStore
from trade_graph.domain.errors import ValidationFailure
from trade_graph.market_data_cli._common import open_store

FINANCIAL_DATABASE_NAME = "trade_graph.sqlite"
MARKET_DATA_MARKER_TABLE = "market_data_migrations"


def refuse_financial_database(path: str, parser: argparse.ArgumentParser) -> None:
    """Exit with a usage error unless ``path`` is absent, empty or an existing candle-history file."""
    target = Path(path)
    if target.name == FINANCIAL_DATABASE_NAME:
        parser.error(f"refusing {path}: that is the financial database; candles use their own file "
                     "(default runtime/market_data.sqlite)")
    if not target.is_file() or target.stat().st_size == 0:
        return
    try:
        connection = sqlite3.connect(f"{target.resolve().as_uri()}?mode=ro", uri=True)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        parser.error(f"refusing {path}: it is not a SQLite candle-history file")
    if MARKET_DATA_MARKER_TABLE not in tables:
        parser.error(f"refusing {path}: it is a database that is not a candle-history file (for example the "
                     "financial database); give a separate --market-database path")


def require_existing_market_database(path: str, parser: argparse.ArgumentParser) -> None:
    """Read-only commands must not create a database: exit with a usage error when the file is missing or empty."""
    refuse_financial_database(path, parser)
    target = Path(path)
    if not target.is_file() or target.stat().st_size == 0:
        parser.error(f"no candle-history file at {path}; this command only reads one that already exists")


def unusable_database(path: str, error: ValidationFailure) -> str:
    """The store refuses open permissions, and the default runtime/ folder often has them: say how to fix it."""
    message = f"cannot use the candle-history file {path}: {error}"
    if "must be private" not in str(error):
        return message
    folder = shlex.quote(str(Path(path).parent))
    return (f"{message}. Candle history is kept private; nothing was changed. Fix it with: chmod 700 {folder}"
            f" and, if the file already exists, chmod 600 {shlex.quote(path)}")


def open_store_or_exit(path: str, parser: argparse.ArgumentParser) -> tuple[MarketDataDatabase, CandleStore]:
    """Open the candle-history file; a file or folder the store refuses ends the command as a usage error."""
    try:
        return open_store(path)
    except ValidationFailure as error:
        parser.error(unusable_database(path, error))
