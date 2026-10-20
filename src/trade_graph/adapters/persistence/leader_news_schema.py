"""The Leader's news-round records (migration 0039, T38; D121, D124, D123).

- ``leader_news_rounds``: every round the Leader's wake handler ran or declined: the wake and task it belongs to (one
  round per wake task), what the Leader was handed (immutable, with its hash), the web research run (T36) that holds
  its receipts and stored sources, the reply and the answer given to the event engine. Written ``started`` before any
  paid call; it finishes once, and a finished round never changes.
- ``leader_round_requests``: each request a completed round routed through the event engine as a wake request: a
  deep dive for Research, a note for the Trader, a strategy idea for Research or Optimisation (with the practice range
  first). Written with its round before the round finishes, never changed. ``trigger_source_ref`` is the engine's
  trigger (``wake-request:<wake_id>:<position>``).

Migration 0052 (W12-A, D280, D296) lets a strategy idea go to the Trading desk (``trader``): SQLite cannot change a
CHECK in place, so ``DESK_IDEA_STATEMENTS`` rebuild ``leader_round_requests`` with the wider list (rows copied aside
and back with their sequence, then its rules again). The 0039 statements below stay exactly as merged.

These are orchestration records, not financial state, and they hold no trade decision: the Leader never places,
approves or blocks a trade. Receipts stay in ``usage_receipts`` and stored sources in ``web_search_sources``. Nothing
here limits how many rounds there are (D116).
"""

from typing import Final

VERSION: Final = "0039"
# Frozen at the migration like every CHECK list; ``contracts/leader_news.py`` holds the same lists and a test keeps
# them equal, so a new status or kind needs a new migration.
ROUND_STATUSES: Final = ("started", "completed", "switched_off", "disabled", "paused_at_limit", "rejected", "failed",
                         "uncertain", "interrupted")
REQUEST_KINDS: Final = ("deep_dive", "trader_note", "strategy_idea")
ROUTED_DEPARTMENTS: Final = ("research", "trader", "optimisation")
TABLES: Final = ("leader_news_rounds", "leader_round_requests")


def _among(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _object(column: str) -> str:
    return f"json_valid({column}) AND json_type({column}) = 'object'"


_IDENTITY_CHANGED = (
    "NEW.round_id IS NOT OLD.round_id OR NEW.portfolio_id IS NOT OLD.portfolio_id OR NEW.task_id IS NOT OLD.task_id "
    "OR NEW.wake_id IS NOT OLD.wake_id OR NEW.trading_day_start IS NOT OLD.trading_day_start "
    "OR NEW.brief_json IS NOT OLD.brief_json OR NEW.brief_sha256 IS NOT OLD.brief_sha256 "
    "OR NEW.started_at IS NOT OLD.started_at OR NEW.sequence IS NOT OLD.sequence")

STATEMENTS: Final[list[str]] = [
    f"""CREATE TABLE leader_news_rounds (
        sequence          INTEGER PRIMARY KEY AUTOINCREMENT,
        round_id          TEXT NOT NULL UNIQUE CHECK (length(round_id) BETWEEN 1 AND 128),
        portfolio_id      TEXT NOT NULL REFERENCES portfolios (portfolio_id),
        task_id           TEXT NOT NULL UNIQUE CHECK (length(task_id) BETWEEN 1 AND 128),
        wake_id           TEXT NOT NULL CHECK (length(wake_id) BETWEEN 1 AND 128),
        trading_day_start TEXT NOT NULL,
        status            TEXT NOT NULL CHECK ({_among('status', ROUND_STATUSES)}),
        run_id            TEXT CHECK (run_id IS NULL OR length(run_id) BETWEEN 1 AND 128),
        brief_json        TEXT NOT NULL CHECK ({_object('brief_json')}),
        brief_sha256      TEXT NOT NULL CHECK (length(brief_sha256) = 64 AND brief_sha256 NOT GLOB '*[^0-9a-f]*'),
        reply_json        TEXT CHECK (reply_json IS NULL OR ({_object('reply_json')})),
        output_json       TEXT CHECK (output_json IS NULL OR ({_object('output_json')})),
        reason            TEXT CHECK (reason IS NULL OR length(reason) <= 500),
        started_at        TEXT NOT NULL,
        finished_at       TEXT,
        CHECK ((status = 'started') = (finished_at IS NULL)),
        CHECK (status = 'started' OR output_json IS NOT NULL)
    )""",
    "CREATE INDEX leader_news_rounds_by_portfolio ON leader_news_rounds (portfolio_id, sequence)",
    """CREATE TRIGGER leader_news_rounds_no_delete BEFORE DELETE ON leader_news_rounds
        BEGIN SELECT RAISE(ABORT, 'leader news rounds are kept'); END""",
    f"""CREATE TRIGGER leader_news_rounds_identity BEFORE UPDATE ON leader_news_rounds
        WHEN {_IDENTITY_CHANGED}
        BEGIN SELECT RAISE(ABORT, 'a news round''s identity and brief are immutable'); END""",
    f"""CREATE TRIGGER leader_news_rounds_finish_once BEFORE UPDATE ON leader_news_rounds
        WHEN NOT ({_IDENTITY_CHANGED}) AND NOT (OLD.status = 'started' AND NEW.status != 'started')
        BEGIN SELECT RAISE(ABORT, 'a news round finishes once and is then kept as it is'); END""",

    f"""CREATE TABLE leader_round_requests (
        sequence           INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id         TEXT NOT NULL UNIQUE CHECK (length(request_id) BETWEEN 1 AND 128),
        round_id           TEXT NOT NULL REFERENCES leader_news_rounds (round_id),
        portfolio_id       TEXT NOT NULL REFERENCES portfolios (portfolio_id),
        kind               TEXT NOT NULL CHECK ({_among('kind', REQUEST_KINDS)}),
        department         TEXT NOT NULL CHECK ({_among('department', ROUTED_DEPARTMENTS)}),
        asked_department   TEXT NOT NULL CHECK ({_among('asked_department', ROUTED_DEPARTMENTS)}),
        position           INTEGER NOT NULL CHECK (position BETWEEN 0 AND 15),
        trigger_source_ref TEXT NOT NULL UNIQUE CHECK (length(trigger_source_ref) BETWEEN 1 AND 200),
        document_json      TEXT NOT NULL CHECK ({_object('document_json')}),
        created_at         TEXT NOT NULL,
        UNIQUE (round_id, position),
        CHECK ((kind = 'trader_note' AND department = 'trader' AND asked_department = 'trader')
            OR (kind = 'deep_dive' AND department = 'research' AND asked_department = 'research')
            OR (kind = 'strategy_idea' AND department IN ('research', 'optimisation')
                AND asked_department IN ('research', 'optimisation')))
    )""",
    "CREATE INDEX leader_round_requests_by_round ON leader_round_requests (round_id, position)",
    """CREATE TRIGGER leader_round_requests_with_open_round BEFORE INSERT ON leader_round_requests
        WHEN (SELECT status FROM leader_news_rounds WHERE round_id = NEW.round_id) IS NOT 'started'
          OR (SELECT portfolio_id FROM leader_news_rounds WHERE round_id = NEW.round_id) IS NOT NEW.portfolio_id
        BEGIN SELECT RAISE(ABORT, 'a routed request is recorded with its round, before it finishes'); END""",
    """CREATE TRIGGER leader_round_requests_no_delete BEFORE DELETE ON leader_round_requests
        BEGIN SELECT RAISE(ABORT, 'routed requests are kept'); END""",
    """CREATE TRIGGER leader_round_requests_no_update BEFORE UPDATE ON leader_round_requests
        BEGIN SELECT RAISE(ABORT, 'routed requests are append-only'); END""",
]


# --- migration 0052 (W12-A, D280, D296): strategy ideas for the Trading desk ------------------------------------

_REQUEST_COLUMNS = ("sequence, request_id, round_id, portfolio_id, kind, department, asked_department, position, "
                    "trigger_source_ref, document_json, created_at")
# 0052's list of departments a strategy idea may go to: the Trading desk too (0039's was research and optimisation).
IDEA_DEPARTMENTS_0052: Final = ("trader", "research", "optimisation")

DESK_IDEA_STATEMENTS: Final[list[str]] = [
    "CREATE TABLE leader_round_requests_0052_copy AS SELECT * FROM leader_round_requests",
    "DROP TABLE leader_round_requests",
    f"""CREATE TABLE leader_round_requests (
        sequence           INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id         TEXT NOT NULL UNIQUE CHECK (length(request_id) BETWEEN 1 AND 128),
        round_id           TEXT NOT NULL REFERENCES leader_news_rounds (round_id),
        portfolio_id       TEXT NOT NULL REFERENCES portfolios (portfolio_id),
        kind               TEXT NOT NULL CHECK (kind IN ('deep_dive', 'trader_note', 'strategy_idea')),
        department         TEXT NOT NULL CHECK ({_among('department', ROUTED_DEPARTMENTS)}),
        asked_department   TEXT NOT NULL CHECK ({_among('asked_department', ROUTED_DEPARTMENTS)}),
        position           INTEGER NOT NULL CHECK (position BETWEEN 0 AND 15),
        trigger_source_ref TEXT NOT NULL UNIQUE CHECK (length(trigger_source_ref) BETWEEN 1 AND 200),
        document_json      TEXT NOT NULL CHECK (json_valid(document_json) AND json_type(document_json) = 'object'),
        created_at         TEXT NOT NULL,
        UNIQUE (round_id, position),
        CHECK ((kind = 'trader_note' AND department = 'trader' AND asked_department = 'trader')
            OR (kind = 'deep_dive' AND department = 'research' AND asked_department = 'research')
            OR (kind = 'strategy_idea' AND {_among('department', IDEA_DEPARTMENTS_0052)}
                AND {_among('asked_department', IDEA_DEPARTMENTS_0052)}))
    )""",
    "CREATE INDEX leader_round_requests_by_round ON leader_round_requests (round_id, position)",
    f"INSERT INTO leader_round_requests ({_REQUEST_COLUMNS}) SELECT {_REQUEST_COLUMNS} "
    "FROM leader_round_requests_0052_copy ORDER BY sequence",
    "DROP TABLE leader_round_requests_0052_copy",
    # The rules of 0039 again, after the copy (finished rounds' requests are copied back as they were).
    """CREATE TRIGGER leader_round_requests_with_open_round BEFORE INSERT ON leader_round_requests
        WHEN (SELECT status FROM leader_news_rounds WHERE round_id = NEW.round_id) IS NOT 'started'
          OR (SELECT portfolio_id FROM leader_news_rounds WHERE round_id = NEW.round_id) IS NOT NEW.portfolio_id
        BEGIN SELECT RAISE(ABORT, 'a routed request is recorded with its round, before it finishes'); END""",
    """CREATE TRIGGER leader_round_requests_no_delete BEFORE DELETE ON leader_round_requests
        BEGIN SELECT RAISE(ABORT, 'routed requests are kept'); END""",
    """CREATE TRIGGER leader_round_requests_no_update BEFORE UPDATE ON leader_round_requests
        BEGIN SELECT RAISE(ABORT, 'routed requests are append-only'); END""",
]


def migration() -> tuple[str, list[str]]:
    """The ``(version, statements)`` entry that ``migrate.STATEMENTS`` registers."""
    return VERSION, STATEMENTS


__all__ = ["DESK_IDEA_STATEMENTS", "IDEA_DEPARTMENTS_0052", "REQUEST_KINDS", "ROUND_STATUSES", "ROUTED_DEPARTMENTS",
           "STATEMENTS", "TABLES", "VERSION", "migration"]
