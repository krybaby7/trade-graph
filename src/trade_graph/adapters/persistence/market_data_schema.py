"""Candle store schema: append-only, compact (integer times, exact decimal text, no floats).

Each entry of MARKET_DATA_MIGRATIONS is applied once, in order, to the separate market-data
database. Never edit an applied version; append a new one.
"""

_INTERVALS = "1, 5, 15, 30, 60, 240, 720, 1440"
_SOURCES = "'kraken_public_ohlc', 'kraken_ohlcvt_archive', 'kraken_public_trades', 'synthetic'"
_KINDS = "'public_collect', 'archive_import', 'trades_backfill', 'catalog'"
_LABELS = (
    "'synthetic_fixture', 'recorded_public_fixture', 'live_public_smoke', 'live_public', 'local_archive_file'"
)


def _append_only(table: str) -> list[str]:
    return [
        f"""CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table}
            BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END""",
        f"""CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
            BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END""",
    ]


VERSION_0001 = [
    f"""CREATE TABLE candle_series (
        series_id INTEGER PRIMARY KEY,
        symbol TEXT NOT NULL,
        source TEXT NOT NULL CHECK (source IN ({_SOURCES})),
        interval_minutes INTEGER NOT NULL CHECK (interval_minutes IN ({_INTERVALS})),
        source_pair TEXT NOT NULL,
        UNIQUE (symbol, source, interval_minutes)
    )""",
    f"""CREATE TABLE candle_runs (
        run_id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL CHECK (kind IN ({_KINDS})),
        source TEXT NOT NULL CHECK (source IN ({_SOURCES})),
        evidence_label TEXT NOT NULL CHECK (evidence_label IN ({_LABELS})),
        started_at INTEGER NOT NULL,
        parameters_json TEXT NOT NULL
    )""",
    """CREATE TABLE candle_run_results (
        run_id INTEGER PRIMARY KEY REFERENCES candle_runs (run_id),
        finished_at INTEGER NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('ok', 'partial', 'failed')),
        report_json TEXT NOT NULL
    )""",
    """CREATE TABLE market_candles (
        series_id INTEGER NOT NULL REFERENCES candle_series (series_id),
        open_time INTEGER NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        available_at INTEGER NOT NULL,
        run_id INTEGER NOT NULL REFERENCES candle_runs (run_id),
        open TEXT NOT NULL,
        high TEXT NOT NULL,
        low TEXT NOT NULL,
        close TEXT NOT NULL,
        vwap TEXT,
        volume TEXT NOT NULL,
        trade_count INTEGER NOT NULL CHECK (trade_count >= 0),
        content_hash BLOB NOT NULL CHECK (length(content_hash) = 32),
        PRIMARY KEY (series_id, open_time, revision)
    ) WITHOUT ROWID""",
    """CREATE TABLE pair_catalog_observations (
        observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
        observed_at INTEGER NOT NULL,
        run_id INTEGER NOT NULL REFERENCES candle_runs (run_id),
        rules_version TEXT NOT NULL,
        content_hash BLOB NOT NULL CHECK (length(content_hash) = 32),
        document_json TEXT NOT NULL
    )""",
    "CREATE INDEX pair_catalog_observed ON pair_catalog_observations (observed_at, observation_id)",
    *_append_only("candle_series"),
    *_append_only("candle_runs"),
    *_append_only("candle_run_results"),
    *_append_only("market_candles"),
    *_append_only("pair_catalog_observations"),
]

MARKET_DATA_MIGRATIONS: list[tuple[str, list[str]]] = [("0001", VERSION_0001)]
