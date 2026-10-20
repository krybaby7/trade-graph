"""The Trading desk's records (state migration 0052, W12-A, C12; D280, D295-D299).

0052 does two things in one migration:

1. **Strategy ideas for the desk.** ``leader_round_requests`` (0039) lets a strategy idea go to the Trading desk
   (``trader``) as well as to Research or Optimisation (D280: the desk owns strategy research). SQLite cannot change a
   CHECK in place, so the table is rebuilt: its rows are copied aside, it is dropped and created again with the same
   columns, index and rules and the wider list, and every row is copied back with its sequence before the rule that
   a request is written with its open round is created again. Nothing else refers to the table. The 0039 statements
   stay exactly as merged.
2. **The desk's own records**, all append-only (never updated, never deleted):

- ``desk_playbook_revisions``: each revision of a portfolio's playbook (per market type, the strategy versions the
  desk means to use or "wait", with reasons and the reports they rest on): who set it (the desk), the wake and task
  that set it, why, the revision it replaced, the document and its SHA-256, when. Revisions of a portfolio form one
  chain: each follows the newest (``revision`` + 1, ``previous_revision_id``), never earlier than it, so the
  revision in force at any past moment is the newest one made by then.
- ``desk_stances``: each stance the desk gave on a coin in a wake (wait, buy dips, follow the trend, other), the
  market type it said it saw, the label software showed it (status, type, label id), the strategy version, the news
  it weighed, its reason and the playbook revision in force after its answer. One per coin and wake.
- ``day_plan_desk_records``: for each day plan installed, the coin's market type at that moment (the newest label,
  with its status: current, stale or missing), the playbook revision in force and the desk's stance on the coin in
  the same answer, for Learning and the scorecard (W13-A). Written in the plan's installation transaction.

These are decision and evidence records, not financial state: no ledger, money limit or order reads them, and
software never refuses a day plan because of them (D280 item 5). Nothing here limits how often the desk revises its
playbook or gives stances (D116). The value lists are fixed with the migration; ``contracts/trading_desk.py`` holds
the same lists and a test keeps them equal.
"""

from __future__ import annotations

from typing import Final

from trade_graph.adapters.persistence.leader_news_schema import DESK_IDEA_STATEMENTS, IDEA_DEPARTMENTS_0052

VERSION: Final = "0052"
TABLES: Final = ("desk_playbook_revisions", "desk_stances", "day_plan_desk_records")
STANCES: Final = ("wait", "buy_dips", "follow_trend", "other")
MARKET_TYPES: Final = ("rising", "sideways", "falling", "unclear")
SEEN_MARKET_TYPES: Final = (*MARKET_TYPES, "none")
LABEL_STATUSES: Final = ("current", "stale", "missing")
# 0052's list of departments a strategy idea may go to (0039's was research and optimisation); the statements that
# rebuild ``leader_round_requests`` for it live with the table, in ``leader_news_schema.DESK_IDEA_STATEMENTS``.
IDEA_DEPARTMENTS: Final = IDEA_DEPARTMENTS_0052
IDEA_STATEMENTS: Final = DESK_IDEA_STATEMENTS


def _among(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


_DIGITS = "[0-9]"
_UTC = ("length({0}) = 27 AND {0} GLOB '" + _DIGITS * 4 + "-" + _DIGITS * 2 + "-" + _DIGITS * 2 + "T" + _DIGITS * 2
        + ":" + _DIGITS * 2 + ":" + _DIGITS * 2 + "." + _DIGITS * 6 + "Z'")
_HEX64 = "length({0}) = 64 AND {0} NOT GLOB '*[^0-9a-f]*'"
_REFERENCE = "length({0}) BETWEEN 1 AND 128"
_SYMBOL = "length({0}) BETWEEN 3 AND 24 AND {0} GLOB '?*/?*'"


def _append_only(table: str, words: str) -> list[str]:
    return [f"""CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
        BEGIN SELECT RAISE(ABORT, '{words} are kept'); END""",
            f"""CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table}
        BEGIN SELECT RAISE(ABORT, '{words} are append-only'); END"""]


# --- the desk's records -----------------------------------------------------------------------------------------

DESK_STATEMENTS: Final[list[str]] = [
    f"""CREATE TABLE desk_playbook_revisions (
        sequence             INTEGER PRIMARY KEY AUTOINCREMENT,
        revision_id          TEXT NOT NULL UNIQUE CHECK ({_REFERENCE.format('revision_id')}),
        portfolio_id         TEXT NOT NULL REFERENCES portfolios (portfolio_id),
        revision             INTEGER NOT NULL CHECK (revision >= 1),
        previous_revision_id TEXT REFERENCES desk_playbook_revisions (revision_id),
        set_by               TEXT NOT NULL CHECK (set_by = 'trader'),
        wake_id              TEXT CHECK (wake_id IS NULL OR {_REFERENCE.format('wake_id')}),
        task_id              TEXT CHECK (task_id IS NULL OR {_REFERENCE.format('task_id')}),
        why                  TEXT NOT NULL CHECK (length(why) BETWEEN 1 AND 500),
        document_json        TEXT NOT NULL CHECK (json_valid(document_json) AND json_type(document_json) = 'object'),
        document_sha256      TEXT NOT NULL CHECK ({_HEX64.format('document_sha256')}),
        created_at           TEXT NOT NULL CHECK ({_UTC.format('created_at')}),
        UNIQUE (portfolio_id, revision),
        CHECK ((revision = 1) = (previous_revision_id IS NULL))
    )""",
    "CREATE INDEX desk_playbook_revisions_by_moment ON desk_playbook_revisions (portfolio_id, created_at, revision)",
    """CREATE TRIGGER desk_playbook_revisions_chain BEFORE INSERT ON desk_playbook_revisions
        WHEN NEW.revision IS NOT COALESCE((SELECT MAX(revision) FROM desk_playbook_revisions
                                           WHERE portfolio_id = NEW.portfolio_id), 0) + 1
          OR NEW.previous_revision_id IS NOT (SELECT revision_id FROM desk_playbook_revisions
                                               WHERE portfolio_id = NEW.portfolio_id ORDER BY revision DESC LIMIT 1)
          OR NEW.created_at < COALESCE((SELECT MAX(created_at) FROM desk_playbook_revisions
                                        WHERE portfolio_id = NEW.portfolio_id), '')
        BEGIN SELECT RAISE(ABORT, 'a playbook revision follows the newest revision of its portfolio'); END""",
    *_append_only("desk_playbook_revisions", "playbook revisions"),

    f"""CREATE TABLE desk_stances (
        sequence             INTEGER PRIMARY KEY AUTOINCREMENT,
        stance_id            TEXT NOT NULL UNIQUE CHECK ({_REFERENCE.format('stance_id')}),
        portfolio_id         TEXT NOT NULL REFERENCES portfolios (portfolio_id),
        wake_id              TEXT NOT NULL CHECK ({_REFERENCE.format('wake_id')}),
        task_id              TEXT CHECK (task_id IS NULL OR {_REFERENCE.format('task_id')}),
        trading_day_start    TEXT NOT NULL CHECK ({_UTC.format('trading_day_start')}),
        symbol               TEXT NOT NULL CHECK ({_SYMBOL.format('symbol')}),
        stance               TEXT NOT NULL CHECK ({_among('stance', STANCES)}),
        market_type_seen     TEXT NOT NULL CHECK ({_among('market_type_seen', SEEN_MARKET_TYPES)}),
        label_status         TEXT NOT NULL CHECK ({_among('label_status', LABEL_STATUSES)}),
        label_market_type    TEXT CHECK (label_market_type IS NULL OR {_among('label_market_type', MARKET_TYPES)}),
        label_id             TEXT REFERENCES market_type_labels (label_id),
        strategy_ref         TEXT CHECK (strategy_ref IS NULL OR length(strategy_ref) BETWEEN 5 AND 72),
        news_refs_json       TEXT NOT NULL CHECK (json_valid(news_refs_json) AND json_type(news_refs_json) = 'array'),
        reason               TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 300),
        playbook_revision_id TEXT REFERENCES desk_playbook_revisions (revision_id),
        created_at           TEXT NOT NULL CHECK ({_UTC.format('created_at')}),
        UNIQUE (wake_id, symbol),
        CHECK ((label_status = 'missing') = (label_market_type IS NULL)),
        CHECK ((label_status = 'missing') = (label_id IS NULL))
    )""",
    "CREATE INDEX desk_stances_by_moment ON desk_stances (portfolio_id, created_at, sequence)",
    "CREATE INDEX desk_stances_by_coin ON desk_stances (portfolio_id, symbol, created_at)",
    *_append_only("desk_stances", "stances"),

    f"""CREATE TABLE day_plan_desk_records (
        plan_id              TEXT PRIMARY KEY REFERENCES day_plans (plan_id),
        portfolio_id         TEXT NOT NULL REFERENCES portfolios (portfolio_id),
        symbol               TEXT NOT NULL CHECK ({_SYMBOL.format('symbol')}),
        label_status         TEXT NOT NULL CHECK ({_among('label_status', LABEL_STATUSES)}),
        market_type          TEXT CHECK (market_type IS NULL OR {_among('market_type', MARKET_TYPES)}),
        label_id             TEXT REFERENCES market_type_labels (label_id),
        labelled_at          TEXT CHECK (labelled_at IS NULL OR ({_UTC.format('labelled_at')})),
        rules_ref            TEXT CHECK (rules_ref IS NULL OR length(rules_ref) BETWEEN 1 AND 40),
        playbook_revision_id TEXT REFERENCES desk_playbook_revisions (revision_id),
        playbook_revision    INTEGER CHECK (playbook_revision IS NULL OR playbook_revision >= 1),
        stance_id            TEXT REFERENCES desk_stances (stance_id),
        stance               TEXT CHECK (stance IS NULL OR {_among('stance', STANCES)}),
        recorded_at          TEXT NOT NULL CHECK ({_UTC.format('recorded_at')}),
        CHECK ((label_status = 'missing') = (market_type IS NULL)),
        CHECK ((label_status = 'missing') = (label_id IS NULL)),
        CHECK ((label_id IS NULL) = (labelled_at IS NULL)),
        CHECK ((playbook_revision_id IS NULL) = (playbook_revision IS NULL)),
        CHECK ((stance_id IS NULL) = (stance IS NULL)),
        CHECK (labelled_at IS NULL OR labelled_at <= recorded_at)
    )""",
    "CREATE INDEX day_plan_desk_records_by_portfolio ON day_plan_desk_records (portfolio_id, recorded_at)",
    """CREATE TRIGGER day_plan_desk_records_match_the_plan BEFORE INSERT ON day_plan_desk_records
        WHEN (SELECT portfolio_id FROM day_plans WHERE plan_id = NEW.plan_id) IS NOT NEW.portfolio_id
          OR (SELECT symbol FROM day_plans WHERE plan_id = NEW.plan_id) IS NOT NEW.symbol
          OR (NEW.playbook_revision_id IS NOT NULL AND (
                (SELECT portfolio_id FROM desk_playbook_revisions WHERE revision_id = NEW.playbook_revision_id)
                    IS NOT NEW.portfolio_id
             OR (SELECT revision FROM desk_playbook_revisions WHERE revision_id = NEW.playbook_revision_id)
                    IS NOT NEW.playbook_revision))
          OR (NEW.stance_id IS NOT NULL AND (
                (SELECT symbol FROM desk_stances WHERE stance_id = NEW.stance_id) IS NOT NEW.symbol
             OR (SELECT stance FROM desk_stances WHERE stance_id = NEW.stance_id) IS NOT NEW.stance))
        BEGIN SELECT RAISE(ABORT, 'a day plan''s desk record names its own plan, coin, playbook and stance'); END""",
    *_append_only("day_plan_desk_records", "day-plan desk records"),
]

STATEMENTS: Final[list[str]] = [*IDEA_STATEMENTS, *DESK_STATEMENTS]


def migration() -> tuple[str, list[str]]:
    """The ``(version, statements)`` entry that ``migrate.STATEMENTS`` registers after ``0051``."""
    return VERSION, STATEMENTS


__all__ = ["DESK_STATEMENTS", "IDEA_DEPARTMENTS", "IDEA_STATEMENTS", "LABEL_STATUSES", "MARKET_TYPES",
           "SEEN_MARKET_TYPES", "STANCES", "STATEMENTS", "TABLES", "VERSION", "migration"]
