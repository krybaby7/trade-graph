"""Practice range (migration 0030, T29): registered strategy versions, studies with their windows, replay reports.

- ``practice_strategy_versions``: one immutable row per registered strategy version (``strategy_id@version``) with its
  canonical document and content hash. The same reference can never name two documents.
- ``practice_range_studies``: one immutable row per study: a strategy family, an earlier tuning window and a later
  confirmation window of whole UTC days (``YYYY-MM-DD``, end excluded). A trigger refuses a study that would tune a
  family on days one of its studies set aside for confirmation, confirm on days it already tuned or confirmed on,
  so a confirmation window stays untouched (D166).
- ``practice_range_runs``: one immutable row per replay with its full report. Triggers keep tuning inside the study's
  tuning window and off every confirmation window of the family, make a confirmation replay its whole window, and
  bind each confirmation window to the first strategy version confirmed in it.
- ``practice_range_verifications``: append-only reruns of a stored report and whether they reproduced it.

Money is decimal text inside the report JSON, never REAL. These tables hold no financial state and are not part of
the ledger, the financial checkpoint or any model context.
"""

from typing import Final

VERSION: Final = "0030"


def _append_only(table: str) -> list[str]:
    return [
        f"""CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table}
            BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END""",
        f"""CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
            BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END""",
    ]


_FAMILY = "(SELECT strategy_id FROM practice_strategy_versions WHERE strategy_ref = NEW.strategy_ref)"

STATEMENTS: Final[list[str]] = [
    """CREATE TABLE practice_strategy_versions (
        sequence        INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_ref    TEXT NOT NULL UNIQUE,
        strategy_id     TEXT NOT NULL,
        version         INTEGER NOT NULL CHECK (version >= 1),
        content_sha256  TEXT NOT NULL CHECK (length(content_sha256) = 64),
        document_json   TEXT NOT NULL,
        registered_at   TEXT NOT NULL,
        UNIQUE (strategy_id, version),
        CHECK (strategy_ref = strategy_id || '@' || version)
    )""",
    *_append_only("practice_strategy_versions"),
    """CREATE TABLE practice_range_studies (
        sequence        INTEGER PRIMARY KEY AUTOINCREMENT,
        study_id        TEXT NOT NULL UNIQUE,
        strategy_id     TEXT NOT NULL,
        tune_start      TEXT NOT NULL,
        tune_end        TEXT NOT NULL,
        confirm_start   TEXT NOT NULL,
        confirm_end     TEXT NOT NULL,
        document_json   TEXT NOT NULL,
        created_at      TEXT NOT NULL,
        CHECK (tune_start < tune_end AND tune_end <= confirm_start AND confirm_start < confirm_end),
        CHECK (length(tune_start) = 10 AND length(tune_end) = 10 AND length(confirm_start) = 10
               AND length(confirm_end) = 10)
    )""",
    "CREATE INDEX practice_range_studies_family ON practice_range_studies (strategy_id)",
    """CREATE TRIGGER practice_range_studies_untouched BEFORE INSERT ON practice_range_studies
        WHEN EXISTS (SELECT 1 FROM practice_range_studies s WHERE s.strategy_id = NEW.strategy_id AND (
            (NEW.tune_start < s.confirm_end AND s.confirm_start < NEW.tune_end)
            OR (NEW.confirm_start < s.confirm_end AND s.confirm_start < NEW.confirm_end)
            OR (NEW.confirm_start < s.tune_end AND s.tune_start < NEW.confirm_end)))
        BEGIN SELECT RAISE(ABORT, 'a confirmation window must stay untouched by tuning'); END""",
    *_append_only("practice_range_studies"),
    """CREATE TABLE practice_range_runs (
        sequence          INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id            TEXT NOT NULL UNIQUE,
        study_id          TEXT NOT NULL REFERENCES practice_range_studies (study_id),
        phase             TEXT NOT NULL CHECK (phase IN ('tune','confirm')),
        strategy_ref      TEXT NOT NULL REFERENCES practice_strategy_versions (strategy_ref),
        symbol            TEXT NOT NULL,
        interval_minutes  INTEGER NOT NULL,
        source            TEXT NOT NULL,
        cost_profile_ref  TEXT NOT NULL,
        fill_policy_ref   TEXT NOT NULL,
        window_start      TEXT NOT NULL,
        window_end        TEXT NOT NULL,
        data_as_of        TEXT NOT NULL,
        evidence          TEXT NOT NULL,
        result_sha256     TEXT NOT NULL CHECK (length(result_sha256) = 64),
        report_json       TEXT NOT NULL,
        created_at        TEXT NOT NULL,
        CHECK (window_start < window_end)
    )""",
    "CREATE INDEX practice_range_runs_strategy ON practice_range_runs (strategy_ref, phase)",
    "CREATE INDEX practice_range_runs_study ON practice_range_runs (study_id, phase)",
    f"""CREATE TRIGGER practice_range_runs_one_family BEFORE INSERT ON practice_range_runs
        WHEN {_FAMILY} IS NOT (SELECT strategy_id FROM practice_range_studies WHERE study_id = NEW.study_id)
        BEGIN SELECT RAISE(ABORT, 'a study replays versions of its own strategy family only'); END""",
    """CREATE TRIGGER practice_range_runs_tune_inside BEFORE INSERT ON practice_range_runs
        WHEN NEW.phase = 'tune' AND NOT EXISTS (SELECT 1 FROM practice_range_studies s WHERE s.study_id = NEW.study_id
            AND s.tune_start <= NEW.window_start AND NEW.window_end <= s.tune_end)
        BEGIN SELECT RAISE(ABORT, 'tuning stays inside the study tuning window'); END""",
    f"""CREATE TRIGGER practice_range_runs_tune_untouched BEFORE INSERT ON practice_range_runs
        WHEN NEW.phase = 'tune' AND EXISTS (SELECT 1 FROM practice_range_studies s WHERE s.strategy_id = {_FAMILY}
            AND NEW.window_start < s.confirm_end AND s.confirm_start < NEW.window_end)
        BEGIN SELECT RAISE(ABORT, 'a confirmation window cannot be reused for tuning'); END""",
    """CREATE TRIGGER practice_range_runs_confirm_whole BEFORE INSERT ON practice_range_runs
        WHEN NEW.phase = 'confirm' AND NOT EXISTS (SELECT 1 FROM practice_range_studies s
            WHERE s.study_id = NEW.study_id AND s.confirm_start = NEW.window_start AND s.confirm_end = NEW.window_end)
        BEGIN SELECT RAISE(ABORT, 'a confirmation replays the whole confirmation window'); END""",
    """CREATE TRIGGER practice_range_runs_confirm_one_version BEFORE INSERT ON practice_range_runs
        WHEN NEW.phase = 'confirm' AND EXISTS (SELECT 1 FROM practice_range_runs r WHERE r.study_id = NEW.study_id
            AND r.phase = 'confirm' AND r.strategy_ref <> NEW.strategy_ref)
        BEGIN SELECT RAISE(ABORT, 'this confirmation window is spent on the first version confirmed in it'); END""",
    *_append_only("practice_range_runs"),
    """CREATE TABLE practice_range_verifications (
        sequence        INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          TEXT NOT NULL REFERENCES practice_range_runs (run_id),
        reproduced      INTEGER NOT NULL CHECK (reproduced IN (0, 1)),
        result_sha256   TEXT NOT NULL CHECK (length(result_sha256) = 64),
        document_json   TEXT NOT NULL,
        verified_at     TEXT NOT NULL
    )""",
    *_append_only("practice_range_verifications"),
]

PRACTICE_RANGE_STATEMENTS: Final = STATEMENTS


def migration() -> tuple[str, list[str]]:
    return VERSION, STATEMENTS
