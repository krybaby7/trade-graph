"""The strategy lab's queue (state migration 0051, W11-B, C11; D280, D116, D290-D294).

0051 does two things in one migration:

1. it adds the trigger kind ``lab_results`` to ``wake_triggers`` (``wake_schema.LAB_RESULTS_STATEMENTS``: SQLite
   rebuilds the table, and the news wake routes that name a trigger with it; every row keeps its sequence);
2. it adds the lab's two tables:

- ``strategy_lab_batches``: the lab requests of one answer of the Trading desk (one wake), with when they all ended
  (``finished_at``, set once) and the ``lab_results`` trigger that woke the desk with them (``trigger_id``, set once,
  only after the batch finished). Only the desk (the Trader's role) asks the lab.
- ``strategy_lab_requests``: one row per request, in the order the lab runs them (``sequence``): its kind, the request
  as the desk sent it (``document_json``, immutable) and its path ``queued`` → ``running`` → ``done``, ``refused`` or
  ``failed``. A request refused when the answer was applied is recorded ``refused`` at once. A ``running`` request
  may be claimed again (a restart re-runs an interrupted request; ``attempts`` only grows) or put back to ``queued``
  (a clean stop); an ended request is final. ``done`` names its result, ``refused`` and ``failed`` their reason.

The practice-range reports a request produced are written in the same transaction that marks it ``done``, so an
interrupted request is run again, never half recorded. Rows are never deleted. These are orchestration and evidence
records, not financial state; nothing in them can place, send or cancel an order, and nothing limits how many
requests there are (D116).
"""

from __future__ import annotations

from typing import Final

from trade_graph.adapters.persistence.wake_schema import LAB_RESULTS_STATEMENTS

VERSION: Final = "0051"
TABLES: Final = ("strategy_lab_batches", "strategy_lab_requests")
# Frozen at the migration; ``contracts/strategy_lab.py`` holds the same lists and a test keeps them equal.
KINDS: Final = ("register_strategy", "create_study", "tune", "confirm")
STATUSES: Final = ("queued", "running", "done", "refused", "failed")
_ENDED = "('done', 'refused', 'failed')"


def _among(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _kept(table: str, words: str) -> str:
    return f"""CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
        BEGIN SELECT RAISE(ABORT, '{words} are kept'); END"""


LAB_STATEMENTS: Final[list[str]] = [
    """CREATE TABLE strategy_lab_batches (
        sequence     INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id     TEXT NOT NULL UNIQUE CHECK (length(batch_id) BETWEEN 1 AND 200),
        portfolio_id TEXT NOT NULL REFERENCES portfolios (portfolio_id),
        department   TEXT NOT NULL CHECK (department = 'trader'),
        wake_id      TEXT CHECK (wake_id IS NULL OR length(wake_id) BETWEEN 1 AND 128),
        task_id      TEXT CHECK (task_id IS NULL OR length(task_id) BETWEEN 1 AND 128),
        requests     INTEGER NOT NULL CHECK (requests >= 1),
        created_at   TEXT NOT NULL,
        finished_at  TEXT,
        trigger_id   TEXT CHECK (trigger_id IS NULL OR length(trigger_id) BETWEEN 1 AND 128),
        CHECK (trigger_id IS NULL OR finished_at IS NOT NULL)
    )""",
    """CREATE INDEX strategy_lab_batches_unannounced ON strategy_lab_batches (sequence)
        WHERE finished_at IS NOT NULL AND trigger_id IS NULL""",
    "CREATE INDEX strategy_lab_batches_portfolio ON strategy_lab_batches (portfolio_id, sequence)",
    _kept("strategy_lab_batches", "lab batches"),
    """CREATE TRIGGER strategy_lab_batches_identity BEFORE UPDATE ON strategy_lab_batches
        WHEN NEW.batch_id IS NOT OLD.batch_id OR NEW.portfolio_id IS NOT OLD.portfolio_id
          OR NEW.department IS NOT OLD.department OR NEW.wake_id IS NOT OLD.wake_id OR NEW.task_id IS NOT OLD.task_id
          OR NEW.requests IS NOT OLD.requests OR NEW.created_at IS NOT OLD.created_at
        BEGIN SELECT RAISE(ABORT, 'a lab batch''s identity is immutable'); END""",
    """CREATE TRIGGER strategy_lab_batches_finished_once BEFORE UPDATE OF finished_at ON strategy_lab_batches
        WHEN OLD.finished_at IS NOT NULL AND NEW.finished_at IS NOT OLD.finished_at
        BEGIN SELECT RAISE(ABORT, 'a lab batch finishes once'); END""",
    """CREATE TRIGGER strategy_lab_batches_announced_once BEFORE UPDATE OF trigger_id ON strategy_lab_batches
        WHEN OLD.trigger_id IS NOT NULL AND NEW.trigger_id IS NOT OLD.trigger_id
        BEGIN SELECT RAISE(ABORT, 'a lab batch wakes the desk once'); END""",

    f"""CREATE TABLE strategy_lab_requests (
        sequence      INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id    TEXT NOT NULL UNIQUE CHECK (length(request_id) BETWEEN 1 AND 200),
        batch_id      TEXT NOT NULL REFERENCES strategy_lab_batches (batch_id),
        position      INTEGER NOT NULL CHECK (position >= 0),
        portfolio_id  TEXT NOT NULL REFERENCES portfolios (portfolio_id),
        kind          TEXT NOT NULL CHECK ({_among('kind', KINDS)}),
        family        TEXT CHECK (family IS NULL OR length(family) BETWEEN 1 AND 64),
        document_json TEXT NOT NULL CHECK (json_valid(document_json) AND json_type(document_json) = 'object'),
        status        TEXT NOT NULL CHECK ({_among('status', STATUSES)}),
        attempts      INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        reason        TEXT CHECK (reason IS NULL OR length(reason) BETWEEN 1 AND 1000),
        result_json   TEXT CHECK (result_json IS NULL OR (json_valid(result_json)
                                                          AND json_type(result_json) = 'object')),
        seconds       TEXT CHECK (seconds IS NULL OR (length(seconds) BETWEEN 1 AND 24
                                                      AND seconds NOT GLOB '*[^0-9.]*')),
        created_at    TEXT NOT NULL,
        started_at    TEXT,
        finished_at   TEXT,
        UNIQUE (batch_id, position),
        CHECK ((status IN {_ENDED}) = (finished_at IS NOT NULL)),
        CHECK (status NOT IN ('refused', 'failed') OR reason IS NOT NULL),
        CHECK (status <> 'done' OR result_json IS NOT NULL)
    )""",
    """CREATE INDEX strategy_lab_requests_open ON strategy_lab_requests (sequence)
        WHERE status IN ('queued', 'running')""",
    "CREATE INDEX strategy_lab_requests_batch ON strategy_lab_requests (batch_id, position)",
    "CREATE INDEX strategy_lab_requests_ended ON strategy_lab_requests (portfolio_id, finished_at, sequence)",
    _kept("strategy_lab_requests", "lab requests"),
    """CREATE TRIGGER strategy_lab_requests_identity BEFORE UPDATE ON strategy_lab_requests
        WHEN NEW.request_id IS NOT OLD.request_id OR NEW.batch_id IS NOT OLD.batch_id
          OR NEW.position IS NOT OLD.position OR NEW.portfolio_id IS NOT OLD.portfolio_id
          OR NEW.kind IS NOT OLD.kind OR NEW.family IS NOT OLD.family OR NEW.document_json IS NOT OLD.document_json
          OR NEW.created_at IS NOT OLD.created_at
        BEGIN SELECT RAISE(ABORT, 'a lab request''s identity is immutable'); END""",
    f"""CREATE TRIGGER strategy_lab_requests_final BEFORE UPDATE ON strategy_lab_requests
        WHEN OLD.status IN {_ENDED}
        BEGIN SELECT RAISE(ABORT, 'an ended lab request is final'); END""",
    f"""CREATE TRIGGER strategy_lab_requests_path BEFORE UPDATE OF status ON strategy_lab_requests
        WHEN OLD.status IN ('queued', 'running')
         AND NOT ((OLD.status = 'queued' AND NEW.status IN ('queued', 'running', 'refused', 'failed'))
               OR (OLD.status = 'running' AND NEW.status IN ('queued', 'running', {_ENDED[1:-1]})))
        BEGIN SELECT RAISE(ABORT, 'a lab request moves along its path only: queued, running, then ended'); END""",
    """CREATE TRIGGER strategy_lab_requests_attempts BEFORE UPDATE OF attempts ON strategy_lab_requests
        WHEN NEW.attempts < OLD.attempts
        BEGIN SELECT RAISE(ABORT, 'a lab request''s attempts only grow'); END""",
]

STATEMENTS: Final[list[str]] = [*LAB_RESULTS_STATEMENTS, *LAB_STATEMENTS]


def migration() -> tuple[str, list[str]]:
    """The ``(version, statements)`` entry that ``migrate.STATEMENTS`` appends after ``0044``."""
    return VERSION, STATEMENTS


__all__ = ["KINDS", "LAB_STATEMENTS", "STATEMENTS", "STATUSES", "TABLES", "VERSION", "migration"]
