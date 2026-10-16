"""Price alarms (migration 0029, T33): definitions, arming history, watch cursors, the delivery queue, feed health.

- ``price_alarms``: one immutable row per alarm (owner department, condition, optional day-plan reference).
- ``price_alarm_events``: append-only ``armed`` / ``fired`` / ``cancelled`` history. ``generation`` counts armings.
  One fire per generation is a UNIQUE constraint, so no restart, retry or second watcher can fire an arming twice;
  re-arming needs the previous arming to have fired; a cancelled alarm never fires or re-arms.
- ``price_alarm_watch``: the watcher's current view of each alarm (status, candle cursor, quote baseline). It may be
  updated, but a trigger keeps its status in line with the event log, and rows are never deleted.
- ``alarm_deliveries``: the queue a wake dispatcher (W5-A, T35) consumes. Items go pending -> claimed -> delivered
  (or back to pending when a claim is released); their content never changes and rows are never deleted.
- ``alarm_wake_claims``: one row per wake that claimed a batch, closed once (acknowledged or released).
- ``price_feed_events``: append-only lost / restored transitions of the feeds that armed alarms depend on.

Money-like values (levels, prices) are decimal text inside JSON, never REAL. The tables hold no financial state:
they are not part of the ledger, the financial checkpoint or any model context except through the queue contract.
"""

from typing import Final

VERSION: Final = "0029"
_DEPARTMENTS = "('trader','research','leader','learning','optimisation')"

STATEMENTS: Final[list[str]] = [
    f"""CREATE TABLE price_alarms (
        sequence          INTEGER PRIMARY KEY AUTOINCREMENT,
        alarm_id          TEXT NOT NULL UNIQUE,
        department        TEXT NOT NULL CHECK (department IN {_DEPARTMENTS}),
        basis             TEXT NOT NULL CHECK (basis IN ('quote','candle')),
        kind              TEXT NOT NULL CHECK (kind IN ('quote_price','candle_price','indicator')),
        symbol            TEXT NOT NULL,
        interval_minutes  INTEGER,
        source            TEXT,
        condition_json    TEXT NOT NULL,
        portfolio_id      TEXT,
        day_plan_ref      TEXT,
        task_id           TEXT,
        request_id        TEXT,
        note              TEXT CHECK (note IS NULL OR length(note) BETWEEN 1 AND 280),
        contract_version  TEXT NOT NULL,
        created_at        TEXT NOT NULL,
        UNIQUE (department, request_id),
        CHECK ((kind = 'quote_price') = (basis = 'quote')),
        CHECK ((basis = 'quote') = (interval_minutes IS NULL)),
        CHECK ((basis = 'quote') = (source IS NULL))
    )""",
    "CREATE INDEX price_alarms_series ON price_alarms (basis, symbol)",
    """CREATE TRIGGER price_alarms_no_update BEFORE UPDATE ON price_alarms
        BEGIN SELECT RAISE(ABORT, 'price alarms are append-only'); END""",
    """CREATE TRIGGER price_alarms_no_delete BEFORE DELETE ON price_alarms
        BEGIN SELECT RAISE(ABORT, 'price alarms are append-only'); END""",
    """CREATE TABLE price_alarm_events (
        sequence       INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id       TEXT NOT NULL UNIQUE,
        alarm_id       TEXT NOT NULL REFERENCES price_alarms (alarm_id),
        generation     INTEGER NOT NULL CHECK (generation >= 1),
        action         TEXT NOT NULL CHECK (action IN ('armed','fired','cancelled')),
        at             TEXT NOT NULL,
        task_id        TEXT,
        document_json  TEXT NOT NULL,
        UNIQUE (alarm_id, generation, action)
    )""",
    "CREATE INDEX price_alarm_events_alarm ON price_alarm_events (alarm_id, sequence)",
    """CREATE UNIQUE INDEX price_alarm_events_cancelled_once ON price_alarm_events (alarm_id)
        WHERE action = 'cancelled'""",
    """CREATE TRIGGER price_alarm_events_no_update BEFORE UPDATE ON price_alarm_events
        BEGIN SELECT RAISE(ABORT, 'price alarm events are append-only'); END""",
    """CREATE TRIGGER price_alarm_events_no_delete BEFORE DELETE ON price_alarm_events
        BEGIN SELECT RAISE(ABORT, 'price alarm events are append-only'); END""",
    """CREATE TRIGGER price_alarm_events_arm_in_order BEFORE INSERT ON price_alarm_events
        WHEN NEW.action = 'armed' AND (
            EXISTS (SELECT 1 FROM price_alarm_events WHERE alarm_id = NEW.alarm_id AND action = 'cancelled')
            OR NEW.generation <> 1 + COALESCE(
                (SELECT MAX(generation) FROM price_alarm_events WHERE alarm_id = NEW.alarm_id), 0)
            OR (NEW.generation > 1 AND NOT EXISTS (SELECT 1 FROM price_alarm_events
                WHERE alarm_id = NEW.alarm_id AND generation = NEW.generation - 1 AND action = 'fired')))
        BEGIN SELECT RAISE(ABORT, 'an alarm is re-armed only after it fired, and never after it was cancelled'); END""",
    """CREATE TRIGGER price_alarm_events_fire_when_armed BEFORE INSERT ON price_alarm_events
        WHEN NEW.action = 'fired' AND (
            NOT EXISTS (SELECT 1 FROM price_alarm_events
                WHERE alarm_id = NEW.alarm_id AND generation = NEW.generation AND action = 'armed')
            OR EXISTS (SELECT 1 FROM price_alarm_events WHERE alarm_id = NEW.alarm_id AND action = 'cancelled')
            OR NEW.generation <> (SELECT MAX(generation) FROM price_alarm_events WHERE alarm_id = NEW.alarm_id))
        BEGIN SELECT RAISE(ABORT, 'only an armed alarm can fire'); END""",
    """CREATE TRIGGER price_alarm_events_cancel_current BEFORE INSERT ON price_alarm_events
        WHEN NEW.action = 'cancelled' AND NEW.generation IS NOT
            (SELECT MAX(generation) FROM price_alarm_events WHERE alarm_id = NEW.alarm_id)
        BEGIN SELECT RAISE(ABORT, 'a cancellation names the current arming'); END""",
    """CREATE TABLE price_alarm_watch (
        alarm_id           TEXT PRIMARY KEY REFERENCES price_alarms (alarm_id),
        status             TEXT NOT NULL CHECK (status IN ('armed','spent','cancelled')),
        generation         INTEGER NOT NULL CHECK (generation >= 1),
        armed_at           TEXT NOT NULL,
        fired_at           TEXT,
        cancelled_at       TEXT,
        evaluated_through  TEXT,
        baseline_side      TEXT CHECK (baseline_side IS NULL OR baseline_side IN ('at_or_below','above',
                                                                                    'at_or_above','below')),
        baseline_value     TEXT,
        baseline_at        TEXT,
        last_reason        TEXT,
        updated_at         TEXT NOT NULL
    )""",
    "CREATE INDEX price_alarm_watch_status ON price_alarm_watch (status)",
    """CREATE TRIGGER price_alarm_watch_no_delete BEFORE DELETE ON price_alarm_watch
        BEGIN SELECT RAISE(ABORT, 'price alarm watch rows are append-only'); END""",
    *[f"""CREATE TRIGGER price_alarm_watch_follows_events_{when} BEFORE {when.upper()} ON price_alarm_watch
        WHEN NOT EXISTS (SELECT 1 FROM price_alarm_events
                WHERE alarm_id = NEW.alarm_id AND generation = NEW.generation
                AND action = CASE NEW.status WHEN 'armed' THEN 'armed' WHEN 'spent' THEN 'fired' ELSE 'cancelled' END)
            OR (NEW.status <> 'cancelled' AND EXISTS (SELECT 1 FROM price_alarm_events
                WHERE alarm_id = NEW.alarm_id AND action = 'cancelled'))
            OR (NEW.status = 'armed' AND EXISTS (SELECT 1 FROM price_alarm_events
                WHERE alarm_id = NEW.alarm_id AND generation = NEW.generation AND action = 'fired'))
        BEGIN SELECT RAISE(ABORT, 'the watch state must follow the alarm events'); END""" for when in ("insert",
                                                                                                        "update")],
    f"""CREATE TABLE alarm_wake_claims (
        wake_id     TEXT PRIMARY KEY CHECK (length(wake_id) BETWEEN 1 AND 128),
        department  TEXT NOT NULL CHECK (department IN {_DEPARTMENTS}),
        status      TEXT NOT NULL CHECK (status IN ('claimed','acknowledged','released')),
        item_count  INTEGER NOT NULL CHECK (item_count >= 0),
        claimed_at  TEXT NOT NULL,
        closed_at   TEXT,
        CHECK ((status = 'claimed') = (closed_at IS NULL))
    )""",
    """CREATE TRIGGER alarm_wake_claims_no_delete BEFORE DELETE ON alarm_wake_claims
        BEGIN SELECT RAISE(ABORT, 'alarm wake claims are append-only'); END""",
    """CREATE TRIGGER alarm_wake_claims_close_once BEFORE UPDATE ON alarm_wake_claims
        WHEN OLD.status <> 'claimed' OR NEW.status NOT IN ('acknowledged','released')
            OR NEW.wake_id IS NOT OLD.wake_id OR NEW.department IS NOT OLD.department
            OR NEW.item_count IS NOT OLD.item_count OR NEW.claimed_at IS NOT OLD.claimed_at
        BEGIN SELECT RAISE(ABORT, 'a wake claim closes once, as acknowledged or released'); END""",
    f"""CREATE TABLE alarm_deliveries (
        sequence       INTEGER PRIMARY KEY AUTOINCREMENT,
        delivery_id    TEXT NOT NULL UNIQUE,
        dedup_key      TEXT NOT NULL UNIQUE,
        department     TEXT NOT NULL CHECK (department IN {_DEPARTMENTS}),
        kind           TEXT NOT NULL CHECK (kind IN ('alarm_fired','feed_lost','feed_restored')),
        alarm_id       TEXT REFERENCES price_alarms (alarm_id),
        generation     INTEGER CHECK (generation IS NULL OR generation >= 1),
        portfolio_id   TEXT,
        document_json  TEXT NOT NULL,
        created_at     TEXT NOT NULL,
        status         TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','claimed','delivered')),
        wake_id        TEXT REFERENCES alarm_wake_claims (wake_id),
        CHECK ((kind = 'alarm_fired') = (alarm_id IS NOT NULL AND generation IS NOT NULL)),
        CHECK (kind = 'alarm_fired' OR (alarm_id IS NULL AND generation IS NULL)),
        CHECK ((status = 'pending') = (wake_id IS NULL))
    )""",
    "CREATE INDEX alarm_deliveries_queue ON alarm_deliveries (department, status, sequence)",
    "CREATE INDEX alarm_deliveries_wake ON alarm_deliveries (wake_id, sequence)",
    """CREATE UNIQUE INDEX alarm_deliveries_fire_once ON alarm_deliveries (alarm_id, generation)
        WHERE kind = 'alarm_fired'""",
    """CREATE TRIGGER alarm_deliveries_no_delete BEFORE DELETE ON alarm_deliveries
        BEGIN SELECT RAISE(ABORT, 'alarm deliveries are append-only'); END""",
    """CREATE TRIGGER alarm_deliveries_need_a_fire BEFORE INSERT ON alarm_deliveries
        WHEN NEW.kind = 'alarm_fired' AND NOT EXISTS (SELECT 1 FROM price_alarm_events
            WHERE alarm_id = NEW.alarm_id AND generation = NEW.generation AND action = 'fired')
        BEGIN SELECT RAISE(ABORT, 'an alarm delivery needs its fire event'); END""",
    """CREATE TRIGGER alarm_deliveries_start_pending BEFORE INSERT ON alarm_deliveries
        WHEN NEW.status <> 'pending'
        BEGIN SELECT RAISE(ABORT, 'a new delivery starts pending'); END""",
    """CREATE TRIGGER alarm_deliveries_immutable BEFORE UPDATE ON alarm_deliveries
        WHEN NEW.sequence IS NOT OLD.sequence OR NEW.delivery_id IS NOT OLD.delivery_id
            OR NEW.dedup_key IS NOT OLD.dedup_key OR NEW.department IS NOT OLD.department
            OR NEW.kind IS NOT OLD.kind OR NEW.alarm_id IS NOT OLD.alarm_id
            OR NEW.generation IS NOT OLD.generation OR NEW.portfolio_id IS NOT OLD.portfolio_id
            OR NEW.document_json IS NOT OLD.document_json OR NEW.created_at IS NOT OLD.created_at
        BEGIN SELECT RAISE(ABORT, 'a delivery''s content is immutable'); END""",
    """CREATE TRIGGER alarm_deliveries_status_moves BEFORE UPDATE ON alarm_deliveries
        WHEN NOT ((NEW.status = OLD.status AND NEW.wake_id IS OLD.wake_id)
                  OR (OLD.status = 'pending' AND NEW.status = 'claimed')
                  OR (OLD.status = 'claimed' AND NEW.status = 'pending')
                  OR (OLD.status = 'claimed' AND NEW.status = 'delivered' AND NEW.wake_id IS OLD.wake_id))
        BEGIN SELECT RAISE(ABORT, 'a delivery status moves pending -> claimed -> delivered, or back to pending');
        END""",
    """CREATE TABLE price_feed_events (
        sequence          INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id          TEXT NOT NULL UNIQUE,
        series_key        TEXT NOT NULL,
        feed              TEXT NOT NULL CHECK (feed IN ('quote','candle')),
        symbol            TEXT NOT NULL,
        interval_minutes  INTEGER,
        source            TEXT,
        status            TEXT NOT NULL CHECK (status IN ('lost','restored')),
        detected_at       TEXT NOT NULL,
        document_json     TEXT NOT NULL,
        CHECK ((feed = 'quote') = (interval_minutes IS NULL)),
        CHECK ((feed = 'quote') = (source IS NULL))
    )""",
    "CREATE INDEX price_feed_events_series ON price_feed_events (series_key, sequence)",
    """CREATE TRIGGER price_feed_events_no_update BEFORE UPDATE ON price_feed_events
        BEGIN SELECT RAISE(ABORT, 'price feed events are append-only'); END""",
    """CREATE TRIGGER price_feed_events_no_delete BEFORE DELETE ON price_feed_events
        BEGIN SELECT RAISE(ABORT, 'price feed events are append-only'); END""",
    """CREATE TRIGGER price_feed_events_alternate BEFORE INSERT ON price_feed_events
        WHEN NEW.status = COALESCE((SELECT status FROM price_feed_events WHERE series_key = NEW.series_key
                                    ORDER BY sequence DESC LIMIT 1), 'restored')
        BEGIN SELECT RAISE(ABORT, 'a feed alternates lost and restored, starting with lost'); END""",
]
PRICE_ALARM_STATEMENTS: Final = STATEMENTS


def migration() -> tuple[str, list[str]]:
    """The ``(version, statements)`` entry that ``migrate.STATEMENTS`` registers."""
    return VERSION, STATEMENTS
