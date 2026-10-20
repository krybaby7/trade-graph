"""Live market-type labels (state migration 0050, W11-A / C10; D287).

``market_type_labels``: one row per coin per labelling moment (the hourly scan's moment): the type (``rising``,
``sideways``, ``falling`` or ``unclear`` with its reason), the newest candle it rests on and how many intervals that
candle was behind, the candle source and interval, the rules version and hash (``kernel/market_type.py``), the scan
that asked for it, the weakest evidence label of its candles, and the whole label document (every reading, the menu
versions, the candle runs and their input hash) with its SHA-256.

The rules hold at the SQL level, whatever code writes: rows are append-only; a label names a reason exactly when it
is unclear; it never rests on a candle that closed after its moment (point in time); its scan, when it names one, is
a recorded scan. The value lists below are fixed with the migration, so a later change in code cannot widen them.

Labels are evidence for the Trading desk (W12-A), not financial state: no ledger, checkpoint, runtime evidence or
review export reads them, and no model context does until W12-A shows them to the desk.
"""

from typing import Final

VERSION: Final = "0050"
TABLE: Final = "market_type_labels"
INDEX: Final = "market_type_labels_newest"
MARKET_TYPES: Final = ("rising", "sideways", "falling", "unclear")
REASONS: Final = ("no_recent_candle", "not_enough_candles", "gap", "no_value", "evaluation_failed")
SOURCES: Final = ("kraken_public_ohlc", "kraken_ohlcvt_archive", "kraken_public_trades", "synthetic",
                  "okx_public_candles")
EVIDENCE: Final = ("synthetic_fixture", "recorded_public_fixture", "live_public_smoke", "live_public",
                   "local_archive_file", "no_candles", "unknown")
INTERVALS: Final = (15, 60)


def _listed(values: tuple) -> str:
    return "(" + ", ".join(repr(value) for value in values) + ")"


_DIGITS = "[0-9]"
_UTC = ("length({0}) = 27 AND {0} GLOB '" + _DIGITS * 4 + "-" + _DIGITS * 2 + "-" + _DIGITS * 2 + "T" + _DIGITS * 2
        + ":" + _DIGITS * 2 + ":" + _DIGITS * 2 + "." + _DIGITS * 6 + "Z'")
_HEX64 = "length({0}) = 64 AND {0} NOT GLOB '*[^0-9a-f]*'"

STATEMENTS: Final[list[str]] = [
    f"""CREATE TABLE {TABLE} (
        sequence          INTEGER PRIMARY KEY AUTOINCREMENT,
        label_id          TEXT NOT NULL UNIQUE CHECK (length(label_id) = 35 AND label_id GLOB 'mt-*'
                                                      AND substr(label_id, 4) NOT GLOB '*[^0-9a-f]*'),
        symbol            TEXT NOT NULL CHECK (length(symbol) BETWEEN 3 AND 24 AND symbol GLOB '?*/?*'),
        as_of             TEXT NOT NULL CHECK ({_UTC.format('as_of')}),
        market_type       TEXT NOT NULL CHECK (market_type IN {_listed(MARKET_TYPES)}),
        reason            TEXT CHECK (reason IS NULL OR reason IN {_listed(REASONS)}),
        candle_close      TEXT CHECK (candle_close IS NULL OR ({_UTC.format('candle_close')}
                                                               AND candle_close <= as_of)),
        lag_bars          INTEGER CHECK (lag_bars IS NULL OR lag_bars >= 0),
        interval_minutes  INTEGER NOT NULL CHECK (interval_minutes IN {_listed(INTERVALS)}),
        source            TEXT NOT NULL CHECK (source IN {_listed(SOURCES)}),
        rules_ref         TEXT NOT NULL CHECK (length(rules_ref) BETWEEN 19 AND 40
                                               AND rules_ref GLOB 'market-type-rules@[1-9]*'),
        rules_sha256      TEXT NOT NULL CHECK ({_HEX64.format('rules_sha256')}),
        scan_id           TEXT REFERENCES coin_scans (scan_id),
        evidence          TEXT NOT NULL CHECK (evidence IN {_listed(EVIDENCE)}),
        document_json     TEXT NOT NULL CHECK (json_valid(document_json)),
        document_sha256   TEXT NOT NULL CHECK ({_HEX64.format('document_sha256')}),
        recorded_at       TEXT NOT NULL,
        CHECK ((market_type = 'unclear') = (reason IS NOT NULL)),
        CHECK ((candle_close IS NULL) = (lag_bars IS NULL))
    )""",
    f"CREATE INDEX {INDEX} ON {TABLE} (symbol, as_of, sequence)",
    *[f"""CREATE TRIGGER {TABLE}_no_{action} BEFORE {action.upper()} ON {TABLE}
        BEGIN SELECT RAISE(ABORT, 'market-type labels are append-only'); END""" for action in ("update", "delete")],
]


def migration() -> tuple[str, list[str]]:
    """The ``(version, statements)`` entry that ``migrate.STATEMENTS`` registers."""
    return VERSION, STATEMENTS
