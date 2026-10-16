"""The coin scanner's record (migration 0031, T31, D174): scans, the pairs that passed, the shortlist entries.

- ``coin_scans``: one row per scan, live or replay, with its ranking version, status, evidence label and the scan
  document (rules, catalog, ticker, cost profile, counts, every exclusion by reason). A replay is always labelled
  optimistic and a live scan never is; a failed scan says why; only a scan that ranked something is ``ok``.
- ``coin_scan_pairs``: every pair that passed every check, with its place, score, per-factor places and the values
  it was ranked on. This is what "passes the scanner's liquidity and exclusion checks at that moment" (D138 item 3)
  reads. Places run 1..ranked and exactly the first ``shortlisted`` places are marked.
- ``coin_shortlist_entries``: the shortlist, each entry with every input value and its source time.

All three are append-only. Money-like values (volumes, prices, spreads) are exact decimal text inside JSON, never
REAL. Nothing here is financial state: no ledger, checkpoint or model context reads these tables.
"""

from typing import Final

from trade_graph.contracts.candles import EVIDENCE_LABELS

VERSION: Final = "0031"
_EVIDENCE = "(" + ",".join(f"'{label}'" for label in EVIDENCE_LABELS) + ")"


def _append_only(table: str, words: str) -> list[str]:
    return [f"""CREATE TRIGGER {table}_no_{action} BEFORE {action.upper()} ON {table}
        BEGIN SELECT RAISE(ABORT, '{words} are append-only'); END""" for action in ("update", "delete")]


STATEMENTS: Final[list[str]] = [
    f"""CREATE TABLE coin_scans (
        sequence         INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id          TEXT NOT NULL UNIQUE CHECK (length(scan_id) BETWEEN 1 AND 64),
        mode             TEXT NOT NULL CHECK (mode IN ('live','replay')),
        as_of            TEXT NOT NULL,
        computed_at      TEXT NOT NULL,
        recorded_at      TEXT NOT NULL,
        ranking_version  TEXT NOT NULL CHECK (length(ranking_version) BETWEEN 1 AND 128),
        status           TEXT NOT NULL CHECK (status IN ('ok','no_candidates','failed')),
        error            TEXT CHECK (error IS NULL OR length(error) BETWEEN 1 AND 64),
        evidence         TEXT NOT NULL CHECK (evidence IN {_EVIDENCE}),
        optimistic       INTEGER NOT NULL CHECK (optimistic IN (0,1)),
        usd_pairs        INTEGER NOT NULL CHECK (usd_pairs >= 0),
        ranked_pairs     INTEGER NOT NULL CHECK (ranked_pairs BETWEEN 0 AND usd_pairs),
        shortlisted      INTEGER NOT NULL CHECK (shortlisted BETWEEN 0 AND ranked_pairs),
        document_json    TEXT NOT NULL,
        document_sha256  TEXT NOT NULL CHECK (length(document_sha256) = 64),
        CHECK (optimistic = (mode = 'replay')),
        CHECK ((status = 'failed') = (error IS NOT NULL)),
        CHECK ((status = 'ok') = (ranked_pairs > 0)),
        CHECK ((ranked_pairs > 0) = (shortlisted > 0))
    )""",
    "CREATE INDEX coin_scans_latest ON coin_scans (mode, as_of, sequence)",
    *_append_only("coin_scans", "coin scans"),
    """CREATE TABLE coin_scan_pairs (
        scan_sequence  INTEGER NOT NULL REFERENCES coin_scans (sequence),
        position       INTEGER NOT NULL CHECK (position >= 1),
        symbol         TEXT NOT NULL CHECK (length(symbol) BETWEEN 3 AND 32),
        score          INTEGER NOT NULL CHECK (score >= 1),
        shortlisted    INTEGER NOT NULL CHECK (shortlisted IN (0,1)),
        ranking_json   TEXT NOT NULL,
        PRIMARY KEY (scan_sequence, position),
        UNIQUE (scan_sequence, symbol)
    )""",
    "CREATE INDEX coin_scan_pairs_symbol ON coin_scan_pairs (symbol, scan_sequence)",
    *_append_only("coin_scan_pairs", "coin scan places"),
    """CREATE TRIGGER coin_scan_pairs_follow_the_scan BEFORE INSERT ON coin_scan_pairs
        WHEN NOT EXISTS (SELECT 1 FROM coin_scans s WHERE s.sequence = NEW.scan_sequence AND s.status = 'ok'
                         AND NEW.position <= s.ranked_pairs
                         AND NEW.shortlisted = (NEW.position <= s.shortlisted))
        BEGIN SELECT RAISE(ABORT, 'a place needs a scan that ranked it, inside its counts'); END""",
    """CREATE TABLE coin_shortlist_entries (
        scan_sequence  INTEGER NOT NULL,
        position       INTEGER NOT NULL,
        symbol         TEXT NOT NULL,
        inputs_json    TEXT NOT NULL,
        PRIMARY KEY (scan_sequence, position),
        UNIQUE (scan_sequence, symbol),
        FOREIGN KEY (scan_sequence, position) REFERENCES coin_scan_pairs (scan_sequence, position)
    )""",
    *_append_only("coin_shortlist_entries", "coin shortlist entries"),
    """CREATE TRIGGER coin_shortlist_entries_need_a_place BEFORE INSERT ON coin_shortlist_entries
        WHEN NOT EXISTS (SELECT 1 FROM coin_scan_pairs p WHERE p.scan_sequence = NEW.scan_sequence
                         AND p.position = NEW.position AND p.symbol = NEW.symbol AND p.shortlisted = 1)
        BEGIN SELECT RAISE(ABORT, 'a shortlist entry needs its shortlisted place'); END""",
]
COIN_SCAN_STATEMENTS: Final = STATEMENTS


def migration() -> tuple[str, list[str]]:
    """The ``(version, statements)`` entry that ``migrate.STATEMENTS`` registers."""
    return VERSION, STATEMENTS
