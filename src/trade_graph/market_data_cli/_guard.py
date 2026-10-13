"""Path checks shared by candle-store commands: never the financial database, only real candle-history files."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

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
