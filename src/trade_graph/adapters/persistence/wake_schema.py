"""The event engine's records (migration 0035, T35; D115, D116, D123, D138 items 6 and 8).

- ``wake_triggers``: every reason to wake a department, one row per source event (``source_ref`` is unique, so a
  restart or a repeated scan never records an event twice). A trigger moves ``pending`` → ``claimed`` (by one wake)
  → ``delivered``; a wake that ended before its department saw it puts it back to ``pending`` (``carried``), so it
  joins the next wake; only a day step of a trading day that ended unvisited becomes ``expired``. Never deleted.
- ``wakes``: every wake: department, purpose, trading day, the task it runs as, exactly what the department was
  handed and the allowance reading at that moment (both immutable), and what came of it. One open engine wake per
  department and portfolio (a partial unique index): triggers that arrive meanwhile wait for the next wake.
  ``origin = 'task'`` records a department task made outside the engine (Secretary routing, an owner request).
- ``department_check_ins``: each department's chosen next check-in (D123); the newest row is in force. Append-only.
- ``focus_selections``: Research's focus coins per trading day, one row per revision. Append-only.
- ``day_cycle_steps``: the daily cycle per portfolio and trading day: day start, scan, focus, day plans, day-end
  review, each started once and finished once.
- ``wake_cursors``: how far the engine has read a source (fills, activity events, tasks); only moves forward.

These are orchestration records, not financial state: fills, intents and the ledger stay the books. Nothing here
limits how many triggers, wakes or check-ins there are (D116).

Migration 0051 (W11-B, D291) adds the trigger kind ``lab_results``. SQLite cannot change a CHECK constraint in place,
so ``LAB_RESULTS_STATEMENTS`` rebuild ``wake_triggers`` with the new kind list: its rows are copied aside, the table is
dropped and created again with the same columns, indexes and rules, and every row is copied back with its sequence.
``news_alarm_wake_routes`` (0042), whose rows name their trigger, is rebuilt the same way around it, so its foreign
key holds throughout; nothing else refers to the table. The 0035 statements below stay exactly as merged.
"""

from typing import Final

VERSION: Final = "0035"
# The value lists are frozen here, at the migration, like every CHECK constraint; ``contracts/wakes.py`` holds the
# same lists and a test keeps them equal, so a new kind needs a new migration rather than a silent mismatch.
DEPARTMENTS: Final = ("trader", "research", "leader", "learning", "optimisation")
TRIGGER_KINDS_0035: Final = (
    "day_start", "focus_step", "day_plan_step", "day_end_review", "check_in", "alarm_fired", "feed_lost",
    "feed_restored", "entry_filled", "exit_filled", "order_filled", "loss_limit_reached", "focus_updated",
    "wake_request", "news_alarm", "task_created")
# The kind list in force since migration 0051 (W11-B): the strategy lab's results wake the desk.
TRIGGER_KINDS: Final = (
    "day_start", "focus_step", "day_plan_step", "day_end_review", "check_in", "alarm_fired", "feed_lost",
    "feed_restored", "entry_filled", "exit_filled", "order_filled", "loss_limit_reached", "focus_updated",
    "wake_request", "news_alarm", "lab_results", "task_created")
TRIGGER_STATUSES: Final = ("pending", "claimed", "delivered", "expired")
WAKE_PURPOSES: Final = ("focus", "day_plan", "day_end_review", "event", "check_in", "task")
WAKE_STATUSES: Final = ("queued", "applying", "completed", "failed", "obsolete")
CYCLE_STEPS: Final = ("day_start", "scan", "focus", "day_plan", "day_end_review")
TABLES: Final = ("wake_triggers", "wakes", "department_check_ins", "focus_selections", "day_cycle_steps",
                 "wake_cursors")


def _among(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _kept(table: str, words: str) -> str:
    return f"""CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
        BEGIN SELECT RAISE(ABORT, '{words} are kept'); END"""


def _append_only(table: str, words: str) -> list[str]:
    return [_kept(table, words), f"""CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table}
        BEGIN SELECT RAISE(ABORT, '{words} are append-only'); END"""]


_DEPARTMENT = _among("department", DEPARTMENTS)
_TRIGGER_PATH = ("((OLD.status = 'pending' AND NEW.status IN ('pending', 'claimed', 'expired')) "
                 "OR (OLD.status = 'claimed' AND NEW.status IN ('pending', 'delivered', 'expired')))")
_STEP_IDENTITY_KEPT = ("NEW.portfolio_id IS OLD.portfolio_id AND NEW.trading_day_start IS OLD.trading_day_start "
                       "AND NEW.trading_day_end IS OLD.trading_day_end AND NEW.step IS OLD.step "
                       "AND NEW.created_at IS OLD.created_at")
_REFERENCE = "length({0}) BETWEEN 1 AND 128"



def _trigger_table(kinds: tuple[str, ...]) -> list[str]:
    """``wake_triggers`` with its indexes and rules, for a kind list (0035's, then 0051's)."""
    return [
    f"""CREATE TABLE wake_triggers (
        sequence      INTEGER PRIMARY KEY AUTOINCREMENT,
        trigger_id    TEXT NOT NULL UNIQUE CHECK ({_REFERENCE.format('trigger_id')}),
        source_ref    TEXT NOT NULL UNIQUE CHECK (length(source_ref) BETWEEN 1 AND 200),
        portfolio_id  TEXT NOT NULL REFERENCES portfolios (portfolio_id),
        department    TEXT NOT NULL CHECK ({_DEPARTMENT}),
        kind          TEXT NOT NULL CHECK ({_among('kind', kinds)}),
        occurred_at   TEXT NOT NULL,
        document_json TEXT NOT NULL CHECK (json_valid(document_json) AND json_type(document_json) = 'object'),
        status        TEXT NOT NULL CHECK ({_among('status', TRIGGER_STATUSES)}),
        carried       INTEGER NOT NULL DEFAULT 0 CHECK (carried IN (0, 1)),
        wake_id       TEXT CHECK (wake_id IS NULL OR {_REFERENCE.format('wake_id')}),
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL
    )""",
    "CREATE INDEX wake_triggers_waiting ON wake_triggers (portfolio_id, department, status, sequence)",
    "CREATE INDEX wake_triggers_by_wake ON wake_triggers (wake_id, sequence)",
    _kept("wake_triggers", "wake triggers"),
    """CREATE TRIGGER wake_triggers_identity BEFORE UPDATE ON wake_triggers
        WHEN NEW.trigger_id IS NOT OLD.trigger_id OR NEW.source_ref IS NOT OLD.source_ref
          OR NEW.portfolio_id IS NOT OLD.portfolio_id OR NEW.department IS NOT OLD.department
          OR NEW.kind IS NOT OLD.kind OR NEW.occurred_at IS NOT OLD.occurred_at
          OR NEW.document_json IS NOT OLD.document_json OR NEW.created_at IS NOT OLD.created_at
        BEGIN SELECT RAISE(ABORT, 'a trigger''s identity and document are immutable'); END""",
    f"""CREATE TRIGGER wake_triggers_path BEFORE UPDATE OF status ON wake_triggers
        WHEN NOT {_TRIGGER_PATH}
        BEGIN SELECT RAISE(ABORT, 'trigger status moves along its path only: pending, claimed, delivered'); END""",
    f"""CREATE TRIGGER wake_triggers_holder BEFORE UPDATE ON wake_triggers
        WHEN {_TRIGGER_PATH}
         AND ((NEW.status = 'claimed' AND NEW.wake_id IS NULL)
          OR (OLD.status = 'claimed' AND NEW.status IN ('claimed', 'delivered') AND NEW.wake_id IS NOT OLD.wake_id)
          OR (NEW.status = 'pending' AND NEW.wake_id IS NOT NULL))
        BEGIN SELECT RAISE(ABORT, 'a claimed or delivered trigger names the one wake that holds it'); END""",
    ]


STATEMENTS: Final[list[str]] = [
    *_trigger_table(TRIGGER_KINDS_0035),

    f"""CREATE TABLE wakes (
        sequence          INTEGER PRIMARY KEY AUTOINCREMENT,
        wake_id           TEXT NOT NULL UNIQUE CHECK ({_REFERENCE.format('wake_id')}),
        portfolio_id      TEXT NOT NULL REFERENCES portfolios (portfolio_id),
        department        TEXT NOT NULL CHECK ({_DEPARTMENT}),
        origin            TEXT NOT NULL CHECK (origin IN ('engine', 'task')),
        purpose           TEXT NOT NULL CHECK ({_among('purpose', WAKE_PURPOSES)}),
        trading_day_start TEXT NOT NULL,
        trading_day_end   TEXT NOT NULL CHECK (trading_day_end > trading_day_start),
        task_id           TEXT UNIQUE CHECK (task_id IS NULL OR {_REFERENCE.format('task_id')}),
        context_json      TEXT NOT NULL CHECK (json_valid(context_json) AND json_type(context_json) = 'object'),
        allowance_json    TEXT NOT NULL CHECK (json_valid(allowance_json) AND json_type(allowance_json) = 'object'),
        status            TEXT NOT NULL CHECK ({_among('status', WAKE_STATUSES)}),
        outcome_json      TEXT CHECK (outcome_json IS NULL OR json_valid(outcome_json)),
        close_reason      TEXT CHECK (close_reason IS NULL OR length(close_reason) <= 500),
        created_at        TEXT NOT NULL,
        closed_at         TEXT
    )""",
    """CREATE UNIQUE INDEX wakes_one_open_per_department ON wakes (portfolio_id, department)
        WHERE origin = 'engine' AND status IN ('queued', 'applying')""",
    "CREATE INDEX wakes_by_time ON wakes (portfolio_id, created_at, sequence)",
    _kept("wakes", "wakes"),
    """CREATE TRIGGER wakes_closed_is_final BEFORE UPDATE ON wakes
        WHEN OLD.status IN ('completed', 'failed', 'obsolete')
        BEGIN SELECT RAISE(ABORT, 'a closed wake is final'); END""",
    """CREATE TRIGGER wakes_identity BEFORE UPDATE ON wakes
        WHEN NEW.wake_id IS NOT OLD.wake_id OR NEW.portfolio_id IS NOT OLD.portfolio_id
          OR NEW.department IS NOT OLD.department OR NEW.origin IS NOT OLD.origin OR NEW.purpose IS NOT OLD.purpose
          OR NEW.trading_day_start IS NOT OLD.trading_day_start OR NEW.trading_day_end IS NOT OLD.trading_day_end
          OR NEW.task_id IS NOT OLD.task_id OR NEW.context_json IS NOT OLD.context_json
          OR NEW.allowance_json IS NOT OLD.allowance_json OR NEW.created_at IS NOT OLD.created_at
        BEGIN SELECT RAISE(ABORT, 'a wake''s identity and what it was handed are immutable'); END""",
    """CREATE TRIGGER wakes_path BEFORE UPDATE OF status ON wakes
        WHEN NOT ((OLD.status = 'queued' AND NEW.status IN ('applying', 'completed', 'failed', 'obsolete'))
               OR (OLD.status = 'applying' AND NEW.status IN ('completed', 'failed', 'obsolete')))
        BEGIN SELECT RAISE(ABORT, 'wake status moves along its path only: queued, applying, then closed'); END""",
    """CREATE TRIGGER wakes_close_time BEFORE UPDATE ON wakes
        WHEN (NEW.status IN ('completed', 'failed', 'obsolete')) <> (NEW.closed_at IS NOT NULL)
        BEGIN SELECT RAISE(ABORT, 'a wake that ends records closed_at, and only then'); END""",

    f"""CREATE TABLE department_check_ins (
        sequence     INTEGER PRIMARY KEY AUTOINCREMENT,
        check_in_id  TEXT NOT NULL UNIQUE CHECK ({_REFERENCE.format('check_in_id')}),
        portfolio_id TEXT NOT NULL REFERENCES portfolios (portfolio_id),
        department   TEXT NOT NULL CHECK ({_DEPARTMENT}),
        due_at       TEXT NOT NULL,
        basis        TEXT NOT NULL CHECK (basis IN ('named', 'day_start')),
        note         TEXT CHECK (note IS NULL OR length(note) BETWEEN 1 AND 280),
        wake_id      TEXT UNIQUE,
        task_id      TEXT,
        created_at   TEXT NOT NULL
    )""",
    "CREATE INDEX department_check_ins_latest ON department_check_ins (portfolio_id, department, sequence)",
    *_append_only("department_check_ins", "check-ins"),

    """CREATE TABLE focus_selections (
        sequence          INTEGER PRIMARY KEY AUTOINCREMENT,
        focus_id          TEXT NOT NULL UNIQUE CHECK (length(focus_id) BETWEEN 1 AND 128),
        portfolio_id      TEXT NOT NULL REFERENCES portfolios (portfolio_id),
        trading_day_start TEXT NOT NULL,
        trading_day_end   TEXT NOT NULL CHECK (trading_day_end > trading_day_start),
        revision          INTEGER NOT NULL CHECK (revision >= 1),
        scan_id           TEXT,
        document_json     TEXT NOT NULL CHECK (json_valid(document_json) AND json_type(document_json) = 'object'),
        document_sha256   TEXT NOT NULL CHECK (length(document_sha256) = 64
                                               AND document_sha256 NOT GLOB '*[^0-9a-f]*'),
        wake_id           TEXT UNIQUE,
        task_id           TEXT,
        recorded_at       TEXT NOT NULL,
        UNIQUE (portfolio_id, trading_day_start, revision)
    )""",
    *_append_only("focus_selections", "focus selections"),

    f"""CREATE TABLE day_cycle_steps (
        portfolio_id      TEXT NOT NULL REFERENCES portfolios (portfolio_id),
        trading_day_start TEXT NOT NULL,
        trading_day_end   TEXT NOT NULL CHECK (trading_day_end > trading_day_start),
        step              TEXT NOT NULL CHECK ({_among('step', CYCLE_STEPS)}),
        status            TEXT NOT NULL CHECK (status IN ('started', 'done', 'failed', 'skipped')),
        detail_json       TEXT NOT NULL CHECK (json_valid(detail_json) AND json_type(detail_json) = 'object'),
        created_at        TEXT NOT NULL,
        updated_at        TEXT NOT NULL,
        PRIMARY KEY (portfolio_id, trading_day_start, step)
    )""",
    _kept("day_cycle_steps", "day cycle steps"),
    """CREATE TRIGGER day_cycle_steps_identity BEFORE UPDATE ON day_cycle_steps
        WHEN NEW.portfolio_id IS NOT OLD.portfolio_id OR NEW.trading_day_start IS NOT OLD.trading_day_start
          OR NEW.trading_day_end IS NOT OLD.trading_day_end OR NEW.step IS NOT OLD.step
          OR NEW.created_at IS NOT OLD.created_at
        BEGIN SELECT RAISE(ABORT, 'a cycle step''s identity is immutable'); END""",
    f"""CREATE TRIGGER day_cycle_steps_path BEFORE UPDATE ON day_cycle_steps
        WHEN {_STEP_IDENTITY_KEPT} AND NOT (OLD.status = 'started' AND NEW.status IN ('done', 'failed', 'skipped'))
        BEGIN SELECT RAISE(ABORT, 'a cycle step finishes once: its status moves along its path only'); END""",

    """CREATE TABLE wake_cursors (
        name       TEXT PRIMARY KEY CHECK (length(name) BETWEEN 1 AND 128),
        position   INTEGER NOT NULL CHECK (position >= 0),
        updated_at TEXT NOT NULL
    )""",
    _kept("wake_cursors", "wake cursors"),
    """CREATE TRIGGER wake_cursors_forward BEFORE UPDATE ON wake_cursors
        WHEN NEW.position < OLD.position OR NEW.name IS NOT OLD.name
        BEGIN SELECT RAISE(ABORT, 'wake cursors only move forward'); END""",
]


def migration() -> tuple[str, list[str]]:
    """The ``(version, statements)`` entry that ``migrate.STATEMENTS`` registers."""
    return VERSION, STATEMENTS


# --- migration 0051 (W11-B, D291): the lab_results kind -------------------------------------------------------------

_TRIGGER_COLUMNS = ("sequence, trigger_id, source_ref, portfolio_id, department, kind, occurred_at, document_json, "
                    "status, carried, wake_id, created_at, updated_at")
_ROUTE_COLUMNS = ("sequence, route_id, verdict_id, headline_id, portfolio_id, department, mode, because, rule_version, "
                  "policy_revision, reason_json, note, symbol, source_ref, trigger_id, alarm_at, created_at")


def _lab_results_statements() -> list[str]:
    from trade_graph.adapters.persistence.news_wake_schema import STATEMENTS as ROUTE_STATEMENTS

    return [
        # Copies first (plain tables without rules), then the child before its parent, so no foreign key is broken.
        "CREATE TABLE wake_triggers_0051_copy AS SELECT * FROM wake_triggers",
        "CREATE TABLE news_alarm_wake_routes_0051_copy AS SELECT * FROM news_alarm_wake_routes",
        "DROP TABLE news_alarm_wake_routes",
        "DROP TABLE wake_triggers",
        *_trigger_table(TRIGGER_KINDS),
        f"INSERT INTO wake_triggers ({_TRIGGER_COLUMNS}) SELECT {_TRIGGER_COLUMNS} FROM wake_triggers_0051_copy "
        "ORDER BY sequence",
        "DROP TABLE wake_triggers_0051_copy",
        *ROUTE_STATEMENTS,
        f"INSERT INTO news_alarm_wake_routes ({_ROUTE_COLUMNS}) SELECT {_ROUTE_COLUMNS} "
        "FROM news_alarm_wake_routes_0051_copy ORDER BY sequence",
        "DROP TABLE news_alarm_wake_routes_0051_copy",
    ]


LAB_RESULTS_STATEMENTS: Final[list[str]] = _lab_results_statements()


__all__ = ["CYCLE_STEPS", "DEPARTMENTS", "LAB_RESULTS_STATEMENTS", "STATEMENTS", "TABLES", "TRIGGER_KINDS",
           "TRIGGER_KINDS_0035", "TRIGGER_STATUSES", "VERSION", "WAKE_PURPOSES", "WAKE_STATUSES", "migration"]
