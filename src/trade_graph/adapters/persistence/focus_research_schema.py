"""The owner's Perplexity switch and Research's focus rounds (migration 0038, T37; D124, D125, D205-D209).

- ``perplexity_department_settings``: each revision of the owner's switch that moves Research and the Leader to
  Perplexity, with its spend rule. Only the owner writes it (``set_by`` is ``owner``); a department switched on
  always has a spend rule; each revision names the one it replaced. No row means switched off, as shipped.
- ``focus_research_rounds``: one row per Research wake turn on the focus workflow, written before anything is sent
  (persist before effects): its wake, task, purpose (the day's ``focus`` step or a ``refresh``), trading day, the
  scan it reviewed and the switch revision in force. One round per task, so a restarted turn never runs a second
  paid round.
- ``focus_research_outcomes``: what came of a round, once: its status, whether the turn answered (a focus, an
  empty selection, a kept focus) or failed visibly, the Perplexity run behind it, billing, the coins it named, the
  reason in plain words and the handler output. No status passes an error off as a deliberate "nothing today".

Every table is append-only. Nothing here is financial state: the ledger stays the books, and receipts stay in
``usage_receipts`` (T36). These tables are not added to agent context.
"""

from typing import Final

VERSION: Final = "0038"
TABLES: Final = ("perplexity_department_settings", "focus_research_rounds", "focus_research_outcomes")
# The turn answered (the engine records a focus, an empty selection or a kept focus) ...
ANSWERED_STATUSES: Final = ("completed", "no_candidates", "outside_owner_scope")
# ... or it failed visibly, with its reason; the Trader is still woken for its day plans.
FAILED_STATUSES: Final = ("no_shortlist", "switched_off", "disabled", "paused_at_limit", "rejected", "failed",
                          "uncertain", "interrupted", "refused")
ROUND_STATUSES: Final = ANSWERED_STATUSES + FAILED_STATUSES


def _listed(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def _append_only(table: str, words: str) -> list[str]:
    return [f"""CREATE TRIGGER {table}_no_{action} BEFORE {action.upper()} ON {table}
        BEGIN SELECT RAISE(ABORT, '{words} are append-only'); END""" for action in ("update", "delete")]


STATEMENTS: Final[list[str]] = [
    """CREATE TABLE perplexity_department_settings (
        sequence              INTEGER PRIMARY KEY AUTOINCREMENT,
        revision_id           TEXT NOT NULL UNIQUE CHECK (length(revision_id) BETWEEN 1 AND 128),
        previous_revision_id  TEXT REFERENCES perplexity_department_settings (revision_id),
        research              INTEGER NOT NULL CHECK (research IN (0, 1)),
        leader                INTEGER NOT NULL CHECK (leader IN (0, 1)),
        spend_mode            TEXT CHECK (spend_mode IS NULL OR spend_mode IN ('unlimited_testing', 'monthly_limit')),
        document_json         TEXT NOT NULL,
        document_sha256       TEXT NOT NULL CHECK (length(document_sha256) = 64),
        command               TEXT NOT NULL CHECK (length(command) BETWEEN 1 AND 64),
        set_by                TEXT NOT NULL CHECK (set_by = 'owner'),
        created_at            TEXT NOT NULL,
        CHECK ((research = 0 AND leader = 0) OR spend_mode IS NOT NULL)
    )""",
    *_append_only("perplexity_department_settings", "Perplexity department settings"),
    """CREATE TABLE focus_research_rounds (
        sequence            INTEGER PRIMARY KEY AUTOINCREMENT,
        round_id            TEXT NOT NULL UNIQUE CHECK (length(round_id) BETWEEN 1 AND 64),
        portfolio_id        TEXT NOT NULL REFERENCES portfolios (portfolio_id),
        wake_id             TEXT NOT NULL CHECK (length(wake_id) BETWEEN 1 AND 128),
        task_id             TEXT NOT NULL UNIQUE CHECK (length(task_id) BETWEEN 1 AND 128),
        purpose             TEXT NOT NULL CHECK (purpose IN ('focus', 'refresh')),
        trading_day_start   TEXT NOT NULL,
        trading_day_end     TEXT NOT NULL CHECK (trading_day_end > trading_day_start),
        scan_id             TEXT CHECK (scan_id IS NULL OR length(scan_id) BETWEEN 1 AND 128),
        switch_revision_id  TEXT CHECK (switch_revision_id IS NULL OR length(switch_revision_id) BETWEEN 1 AND 128),
        context_sha256      TEXT NOT NULL CHECK (length(context_sha256) = 64),
        started_at          TEXT NOT NULL
    )""",
    "CREATE INDEX focus_research_rounds_day ON focus_research_rounds (portfolio_id, trading_day_start, sequence)",
    *_append_only("focus_research_rounds", "focus research rounds"),
    f"""CREATE TABLE focus_research_outcomes (
        round_id            TEXT PRIMARY KEY REFERENCES focus_research_rounds (round_id),
        status              TEXT NOT NULL CHECK (status IN {_listed(ROUND_STATUSES)}),
        answered            INTEGER NOT NULL CHECK (answered IN (0, 1)),
        web_run_id          TEXT CHECK (web_run_id IS NULL OR length(web_run_id) BETWEEN 1 AND 64),
        billing             TEXT NOT NULL CHECK (billing IN ('resolved', 'unresolved', 'none')),
        focus_symbols_json  TEXT,
        reason              TEXT CHECK (reason IS NULL OR length(reason) BETWEEN 1 AND 1000),
        output_json         TEXT NOT NULL,
        output_sha256       TEXT NOT NULL CHECK (length(output_sha256) = 64),
        completed_at        TEXT NOT NULL,
        CHECK (answered = (status IN {_listed(ANSWERED_STATUSES)}))
    )""",
    *_append_only("focus_research_outcomes", "focus research outcomes"),
]
FOCUS_RESEARCH_STATEMENTS: Final = STATEMENTS


def migration() -> tuple[str, list[str]]:
    """The ``(version, statements)`` entry that ``migrate.STATEMENTS`` registers."""
    return VERSION, STATEMENTS


__all__ = ["ANSWERED_STATUSES", "FAILED_STATUSES", "FOCUS_RESEARCH_STATEMENTS", "ROUND_STATUSES", "STATEMENTS",
           "TABLES", "VERSION", "migration"]
