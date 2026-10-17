"""The owner's money-limit records (migration 0033, T32; D138 items 1, 2, 3 and 6; D127).

- ``trading_day_values``: the account's value at the start of each trading day, per portfolio, in its native
  currency. ``at_day_start`` is a point-in-time valuation at the day start itself; ``first_valuation_after_start``
  is the first valuation made later that day when none was possible at the start (the portfolio did not exist
  yet, or a held coin's mark was missing or too old). One row per portfolio and day; the loss limit is measured
  from it.
- ``daily_loss_stops``: the moment a portfolio reached the daily loss limit. While a row exists for the current
  day, new entries are refused and resting entries are cancelled; protective exits and reconciliation continue.
  The row holds until ``resumes_at``, the next day start.
- ``owner_policy_audit``: one row for each owner-policy revision recorded by an owner command, naming the
  revision it replaced and both document hashes, with the changed fields. Only the owner writes it.
- ``riba_posting_flags``: ledger postings that look like interest, staking or earn income, a margin rollover or a
  funding payment (D127), flagged once for the owner's review.

Every table is append-only. Amounts are exact decimal text, never REAL arithmetic. These are controls and
evidence, not authoritative financial state: the ledger and its journal stay the books.
"""

from typing import Final

VERSION: Final = "0033"
TABLES: Final = ("trading_day_values", "daily_loss_stops", "owner_policy_audit", "riba_posting_flags")


def _plain(column: str, *, positive: bool) -> str:
    """A plain non-negative decimal string (no sign, exponent or trailing dot); ``positive`` also refuses zero."""
    check = (f"length({column}) BETWEEN 1 AND 48 AND {column} GLOB '[0-9]*' AND {column} NOT GLOB '*[^0-9.]*' "
             f"AND {column} NOT GLOB '*.*.*' AND {column} NOT GLOB '*.'")
    return f"({check} AND {column} GLOB '*[1-9]*')" if positive else f"({check})"


def _append_only(table: str, words: str) -> list[str]:
    return [f"""CREATE TRIGGER {table}_no_{action} BEFORE {action.upper()} ON {table}
        BEGIN SELECT RAISE(ABORT, '{words} are append-only'); END""" for action in ("update", "delete")]


STATEMENTS: Final[list[str]] = [
    f"""CREATE TABLE trading_day_values (
        sequence         INTEGER PRIMARY KEY AUTOINCREMENT,
        portfolio_id     TEXT NOT NULL REFERENCES portfolios (portfolio_id),
        day_start        TEXT NOT NULL,
        day_end          TEXT NOT NULL CHECK (day_end > day_start),
        currency         TEXT NOT NULL CHECK (length(currency) BETWEEN 3 AND 5),
        value            TEXT NOT NULL CHECK {_plain('value', positive=True)},
        valued_at        TEXT NOT NULL,
        basis            TEXT NOT NULL CHECK (basis IN ('at_day_start', 'first_valuation_after_start')),
        policy_revision  TEXT NOT NULL CHECK (length(policy_revision) BETWEEN 1 AND 128),
        created_at       TEXT NOT NULL,
        UNIQUE (portfolio_id, day_start, currency),
        CHECK (basis <> 'at_day_start' OR valued_at = day_start),
        CHECK (basis <> 'first_valuation_after_start' OR (valued_at > day_start AND valued_at < day_end))
    )""",
    *_append_only("trading_day_values", "day-start values"),
    f"""CREATE TABLE daily_loss_stops (
        sequence         INTEGER PRIMARY KEY AUTOINCREMENT,
        portfolio_id     TEXT NOT NULL REFERENCES portfolios (portfolio_id),
        day_start        TEXT NOT NULL,
        resumes_at       TEXT NOT NULL CHECK (resumes_at > day_start),
        currency         TEXT NOT NULL CHECK (length(currency) BETWEEN 3 AND 5),
        day_start_value  TEXT NOT NULL CHECK {_plain('day_start_value', positive=True)},
        value_at_stop    TEXT NOT NULL CHECK {_plain('value_at_stop', positive=False)},
        loss             TEXT NOT NULL CHECK {_plain('loss', positive=True)},
        limit_amount     TEXT NOT NULL CHECK {_plain('limit_amount', positive=True)},
        limit_fraction   TEXT NOT NULL CHECK ({_plain('limit_fraction', positive=True)} AND limit_fraction GLOB '0.*'),
        reached_at       TEXT NOT NULL CHECK (reached_at >= day_start AND reached_at < resumes_at),
        policy_revision  TEXT NOT NULL CHECK (length(policy_revision) BETWEEN 1 AND 128),
        created_at       TEXT NOT NULL,
        UNIQUE (portfolio_id, day_start),
        -- a guard only; the comparison that decides is exact Decimal in kernel/money_limits.py
        CHECK (CAST(loss AS REAL) >= CAST(limit_amount AS REAL))
    )""",
    *_append_only("daily_loss_stops", "daily loss stops"),
    """CREATE TABLE owner_policy_audit (
        sequence              INTEGER PRIMARY KEY AUTOINCREMENT,
        revision_id           TEXT NOT NULL UNIQUE REFERENCES owner_policy_revisions (revision_id),
        previous_revision_id  TEXT REFERENCES owner_policy_revisions (revision_id),
        previous_sha256       TEXT CHECK (previous_sha256 IS NULL OR length(previous_sha256) = 64),
        revision_sha256       TEXT NOT NULL CHECK (length(revision_sha256) = 64),
        command               TEXT NOT NULL CHECK (length(command) BETWEEN 1 AND 64),
        changes_json          TEXT NOT NULL,
        set_by                TEXT NOT NULL CHECK (set_by = 'owner'),
        created_at            TEXT NOT NULL,
        CHECK ((previous_revision_id IS NULL) = (previous_sha256 IS NULL))
    )""",
    *_append_only("owner_policy_audit", "owner policy audit rows"),
    """CREATE TABLE riba_posting_flags (
        sequence         INTEGER PRIMARY KEY AUTOINCREMENT,
        portfolio_id     TEXT NOT NULL REFERENCES portfolios (portfolio_id),
        ledger_event_id  TEXT NOT NULL UNIQUE,
        kind             TEXT NOT NULL CHECK (length(kind) BETWEEN 1 AND 32),
        reasons_json     TEXT NOT NULL,
        flagged_at       TEXT NOT NULL
    )""",
    *_append_only("riba_posting_flags", "riba posting flags"),
]


def migration() -> tuple[str, list[str]]:
    """The ``(version, statements)`` entry that ``migrate.STATEMENTS`` registers."""
    return VERSION, STATEMENTS
