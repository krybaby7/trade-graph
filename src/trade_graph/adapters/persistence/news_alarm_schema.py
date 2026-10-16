"""News alarm log (migration 0026, reserved for W1-A / T44): append-only, shadow mode.

``news_feed_polls`` records every feed request. ``news_headlines`` holds each headline once (repeats are
detected by canonical link) as a dated source, saved before Jev reads it. ``news_jev_attempts`` is written
in the same transaction as the budget reservation, before the call; ``news_jev_outcomes`` holds the reply as
received; ``news_alarm_verdicts`` says whom the headline would have woken. Nobody is woken from these rows.

Rows are never updated or deleted. Money stays in ``budget_reservations`` and ``usage_receipts``; these
tables hold only references to them.

Registration (``migrate.py``)::

    from trade_graph.adapters.persistence.news_alarm_schema import NEWS_ALARM_STATEMENTS
    STATEMENTS.append(("0026", NEWS_ALARM_STATEMENTS))
"""

from typing import Final

VERSION: Final = "0026"
TABLES: Final = ("news_feed_polls", "news_headlines", "news_jev_attempts", "news_jev_outcomes",
                 "news_alarm_verdicts")
_EVIDENCE = "('live_public','recorded_public_fixture','synthetic_fixture')"


def _append_only(table: str) -> list[str]:
    return [
        f"""CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table}
            BEGIN SELECT RAISE(ABORT, 'news alarm records are append-only'); END""",
        f"""CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
            BEGIN SELECT RAISE(ABORT, 'news alarm records are append-only'); END""",
    ]


STATEMENTS: Final[list[str]] = [
    f"""CREATE TABLE news_feed_polls (
        poll_id        TEXT PRIMARY KEY,
        feed_id        TEXT NOT NULL,
        url            TEXT NOT NULL,
        publisher      TEXT NOT NULL,
        started_at     TEXT NOT NULL,
        finished_at    TEXT NOT NULL,
        outcome        TEXT NOT NULL CHECK (outcome IN ('fetched','not_modified','http_error','transport_error',
                                                       'refused','unreadable')),
        http_status    INTEGER,
        error_category TEXT,
        evidence       TEXT NOT NULL CHECK (evidence IN {_EVIDENCE}),
        body_sha256    TEXT,
        body_bytes     INTEGER CHECK (body_bytes IS NULL OR body_bytes >= 0),
        items_seen     INTEGER NOT NULL CHECK (items_seen >= 0),
        items_new      INTEGER NOT NULL CHECK (items_new >= 0),
        items_repeat   INTEGER NOT NULL CHECK (items_repeat >= 0),
        items_rejected INTEGER NOT NULL CHECK (items_rejected >= 0),
        etag           TEXT,
        last_modified  TEXT
    )""",
    "CREATE INDEX news_feed_polls_feed ON news_feed_polls (feed_id, started_at)",
    f"""CREATE TABLE news_headlines (
        headline_id  TEXT PRIMARY KEY,
        link         TEXT NOT NULL UNIQUE,
        feed_id      TEXT NOT NULL,
        publisher    TEXT NOT NULL,
        title        TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 1000),
        published_at TEXT,
        received_at  TEXT NOT NULL,
        item_sha256  TEXT NOT NULL CHECK (length(item_sha256) = 64),
        evidence     TEXT NOT NULL CHECK (evidence IN {_EVIDENCE}),
        poll_id      TEXT NOT NULL REFERENCES news_feed_polls (poll_id)
    )""",
    "CREATE INDEX news_headlines_received ON news_headlines (received_at)",
    """CREATE TABLE news_jev_attempts (
        attempt_id           TEXT PRIMARY KEY,
        headline_id          TEXT NOT NULL REFERENCES news_headlines (headline_id),
        attempt_number       INTEGER CHECK (attempt_number IS NULL OR attempt_number >= 1),
        reservation_id       TEXT UNIQUE REFERENCES budget_reservations (reservation_id),
        requested_model      TEXT NOT NULL,
        question_set_version TEXT NOT NULL,
        request_sha256       TEXT NOT NULL CHECK (length(request_sha256) = 64),
        request_json         TEXT NOT NULL,
        watch_json           TEXT NOT NULL,
        synthetic            INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
        started_at           TEXT NOT NULL,
        UNIQUE (headline_id, attempt_number),
        CHECK ((reservation_id IS NULL) = (attempt_number IS NULL))
    )""",
    "CREATE INDEX news_jev_attempts_headline ON news_jev_attempts (headline_id)",
    """CREATE TABLE news_jev_outcomes (
        attempt_id          TEXT PRIMARY KEY REFERENCES news_jev_attempts (attempt_id),
        status              TEXT NOT NULL CHECK (status IN ('answered','unexpected_model','malformed_answers',
                                                            'http_error','timeout_uncertain','refused_response',
                                                            'interrupted','budget_refused')),
        failure             TEXT,
        http_status         INTEGER,
        answering_model     TEXT,
        provider_request_id TEXT,
        answers_json        TEXT,
        input_tokens        INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
        output_tokens       INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
        usage_reported      INTEGER NOT NULL CHECK (usage_reported IN (0, 1)),
        reservation_id      TEXT,
        reservation_state   TEXT,
        receipt_id          TEXT,
        latency_ms          INTEGER CHECK (latency_ms IS NULL OR latency_ms >= 0),
        finished_at         TEXT NOT NULL
    )""",
    """CREATE TABLE news_alarm_verdicts (
        verdict_id           TEXT PRIMARY KEY,
        attempt_id           TEXT NOT NULL UNIQUE REFERENCES news_jev_outcomes (attempt_id),
        headline_id          TEXT NOT NULL REFERENCES news_headlines (headline_id),
        rule_version         TEXT NOT NULL,
        question_set_version TEXT NOT NULL,
        urgent_threshold     TEXT NOT NULL,
        would_wake           INTEGER NOT NULL CHECK (would_wake IN (0, 1)),
        wake_roles_json      TEXT NOT NULL,
        subjects_json        TEXT NOT NULL,
        held_subjects_json   TEXT NOT NULL,
        event_type           TEXT NOT NULL,
        holder_yes           TEXT NOT NULL,
        created_at           TEXT NOT NULL,
        CHECK (would_wake = (wake_roles_json <> '[]'))
    )""",
    "CREATE INDEX news_alarm_verdicts_headline ON news_alarm_verdicts (headline_id)",
    *(statement for table in TABLES for statement in _append_only(table)),
]
NEWS_ALARM_STATEMENTS: Final = STATEMENTS


def migration() -> tuple[str, list[str]]:
    """The ``(version, statements)`` entry that ``migrate.STATEMENTS`` appends after ``0025``."""
    return VERSION, STATEMENTS
