"""Append-only hourly revisions. Decimal values are retained inside JSON strings."""

STATEMENTS = [
    """CREATE TABLE hourly_candles (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        candle_id TEXT NOT NULL UNIQUE,
        symbol TEXT NOT NULL CHECK (symbol IN ('BTC/USD', 'ETH/USD')),
        source TEXT NOT NULL CHECK (source IN ('kraken_public_ohlc', 'synthetic')),
        interval_minutes INTEGER NOT NULL CHECK (interval_minutes = 60),
        close_time_utc TEXT NOT NULL,
        available_at_utc TEXT NOT NULL,
        document_json TEXT NOT NULL
    )""",
    """CREATE INDEX hourly_candles_point_in_time
        ON hourly_candles (symbol, source, close_time_utc, available_at_utc, sequence)""",
    """CREATE TRIGGER hourly_candles_no_update BEFORE UPDATE ON hourly_candles
        BEGIN SELECT RAISE(ABORT, 'hourly candles are append-only'); END""",
    """CREATE TRIGGER hourly_candles_no_delete BEFORE DELETE ON hourly_candles
        BEGIN SELECT RAISE(ABORT, 'hourly candles are append-only'); END""",
]
