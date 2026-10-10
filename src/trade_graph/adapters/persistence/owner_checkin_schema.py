"""Owner review check-ins (migration 0025): owner-only, append-only, never agent context.

One row per owner action. A check-in is planned exactly once, can be rescheduled,
and is closed at most once by ``done``, ``skip`` or ``cancel``. Rows are never
updated or deleted. ``(deployment_id, request_id)`` makes every owner request
idempotent; ``document_json`` is the canonical request the replay is compared with.

The table holds non-financial owner notes. It is not part of the financial checkpoint,
the review export, ``activity_events`` or any agent context; only
``trade_graph.api.owner_checkins`` reads or writes it.

Registration (integration owner, ``migrate.py``)::

    from trade_graph.adapters.persistence.owner_checkin_schema import OWNER_CHECKIN_STATEMENTS
    STATEMENTS.append(("0025", OWNER_CHECKIN_STATEMENTS))   # or: STATEMENTS.append(migration())
"""

from typing import Final

VERSION: Final = "0025"
TABLE: Final = "owner_checkins"

STATEMENTS: Final[list[str]] = [
    """CREATE TABLE owner_checkins (
        sequence      INTEGER PRIMARY KEY AUTOINCREMENT,
        record_id     TEXT NOT NULL UNIQUE,
        deployment_id TEXT NOT NULL,
        request_id    TEXT NOT NULL,
        checkin_id    TEXT NOT NULL,
        action        TEXT NOT NULL CHECK (action IN ('plan','reschedule','done','skip','cancel')),
        planned_at    TEXT,
        prompt_kind   TEXT CHECK (prompt_kind IS NULL
                                  OR prompt_kind IN ('baseline','interval','bedtime','next_day','custom')),
        title         TEXT CHECK (title IS NULL OR length(title) BETWEEN 1 AND 80),
        note          TEXT CHECK (note IS NULL OR length(note) <= 500),
        document_json TEXT NOT NULL,
        created_at    TEXT NOT NULL,
        UNIQUE (deployment_id, request_id),
        CHECK ((action IN ('plan','reschedule')) = (planned_at IS NOT NULL)),
        CHECK (action <> 'plan' OR (prompt_kind IS NOT NULL AND title IS NOT NULL)),
        CHECK (action = 'plan' OR (prompt_kind IS NULL AND title IS NULL)),
        CHECK (note IS NULL OR action IN ('done','skip'))
    )""",
    "CREATE INDEX owner_checkins_item ON owner_checkins (deployment_id, checkin_id, sequence)",
    "CREATE INDEX owner_checkins_planned ON owner_checkins (deployment_id, planned_at)",
    """CREATE TRIGGER owner_checkins_no_update BEFORE UPDATE ON owner_checkins
        BEGIN SELECT RAISE(ABORT, 'owner check-ins are append-only'); END""",
    """CREATE TRIGGER owner_checkins_no_delete BEFORE DELETE ON owner_checkins
        BEGIN SELECT RAISE(ABORT, 'owner check-ins are append-only'); END""",
    """CREATE TRIGGER owner_checkins_plan_once BEFORE INSERT ON owner_checkins
        WHEN NEW.action = 'plan' AND EXISTS (SELECT 1 FROM owner_checkins
             WHERE deployment_id = NEW.deployment_id AND checkin_id = NEW.checkin_id)
        BEGIN SELECT RAISE(ABORT, 'check-in already planned'); END""",
    """CREATE TRIGGER owner_checkins_needs_plan BEFORE INSERT ON owner_checkins
        WHEN NEW.action <> 'plan' AND NOT EXISTS (SELECT 1 FROM owner_checkins
             WHERE deployment_id = NEW.deployment_id AND checkin_id = NEW.checkin_id AND action = 'plan')
        BEGIN SELECT RAISE(ABORT, 'unknown check-in'); END""",
    """CREATE TRIGGER owner_checkins_closed BEFORE INSERT ON owner_checkins
        WHEN NEW.action <> 'plan' AND EXISTS (SELECT 1 FROM owner_checkins
             WHERE deployment_id = NEW.deployment_id AND checkin_id = NEW.checkin_id
             AND action IN ('done','skip','cancel'))
        BEGIN SELECT RAISE(ABORT, 'check-in already closed'); END""",
]
OWNER_CHECKIN_STATEMENTS: Final = STATEMENTS


def migration() -> tuple[str, list[str]]:
    """The ``(version, statements)`` entry that ``migrate.STATEMENTS`` appends after ``0024``."""
    return VERSION, STATEMENTS
