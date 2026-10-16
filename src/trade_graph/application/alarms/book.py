"""The alarm book: departments create, re-arm and cancel alarms; the watcher fires them and keeps its cursors.

Rules (T33, D116, D117):

- An alarm belongs to the department that created it. Only that department re-arms or cancels it.
- It fires at most once per arming. ``fire`` records the fire, marks the alarm spent and queues the delivery in one
  transaction; the store refuses a second fire of the same arming, so a restart or a second watcher cannot repeat it.
- A spent alarm waits until its department re-arms it; a cancelled alarm is finished.
- Nothing limits how many alarms exist or how often they are re-armed and fire.

Nothing here calls a model or spends money.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.alarms.queue import canonical_json, check_department, enqueue
from trade_graph.contracts.alarms import (
    ALARM_CONTRACT_VERSION,
    MAX_NOTE_LENGTH,
    AlarmDelivery,
    AlarmRecord,
    AlarmRequest,
)
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import AuthorityDenied, DuplicateRecord, NotFound, StaleState, ValidationFailure

FeedStatus = Literal["lost", "restored"]
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SELECT = """SELECT a.*, w.status, w.generation, w.armed_at, w.fired_at, w.cancelled_at, w.evaluated_through,
                    w.baseline_side, w.baseline_value, w.baseline_at, w.last_reason
             FROM price_alarms a JOIN price_alarm_watch w USING (alarm_id)"""


@dataclass(frozen=True, slots=True)
class FeedSeries:
    """One stream of prices that alarms depend on: the quotes of a symbol, or one candle series."""

    feed: Literal["quote", "candle"]
    symbol: str
    interval_minutes: int | None = None
    source: str | None = None

    @property
    def key(self) -> str:
        if self.feed == "quote":
            return f"quote:{self.symbol}"
        return f"candle:{self.symbol}:{self.interval_minutes}:{self.source}"

    def document(self) -> dict[str, Any]:
        return {"feed": self.feed, "symbol": self.symbol, "interval_minutes": self.interval_minutes,
                "source": self.source, "series_key": self.key}


def series_of(record: AlarmRecord) -> FeedSeries:
    condition = record.condition
    if condition.basis == "quote":
        return FeedSeries("quote", condition.symbol)
    return FeedSeries("candle", condition.symbol, condition.interval_minutes, condition.source)


def _record(row: sqlite3.Row) -> AlarmRecord:
    """A stored alarm, read structurally: its condition is not re-checked against today's indicator menu."""
    return AlarmRecord.model_validate({
        "alarm_id": row["alarm_id"], "department": row["department"],
        "condition": json.loads(row["condition_json"]), "portfolio_id": row["portfolio_id"],
        "day_plan_ref": row["day_plan_ref"], "task_id": row["task_id"], "request_id": row["request_id"],
        "note": row["note"], "created_at": row["created_at"], "status": row["status"],
        "generation": row["generation"], "armed_at": row["armed_at"], "fired_at": row["fired_at"],
        "cancelled_at": row["cancelled_at"], "evaluated_through": row["evaluated_through"],
        "last_reason": row["last_reason"]}, context={"stored": True})


class AlarmBook:
    """Persistent alarms and their arming history, in the state database (migration 0029)."""

    def __init__(self, database: Database, clock: Clock, *, new_id: Callable[[], str] | None = None) -> None:
        self.database = database
        self.clock = clock
        self._new_id = new_id or (lambda: str(uuid.uuid4()))

    # --- department actions -------------------------------------------------------------------------------------

    def create(self, request: AlarmRequest) -> AlarmRecord:
        """Arm a new alarm (generation 1). A repeated ``request_id`` returns the alarm it created."""
        if not isinstance(request, AlarmRequest):
            raise ValidationFailure("an AlarmRequest is required")
        condition = request.condition
        condition_json = canonical_json(condition.model_dump(mode="json"))
        now = self._now()
        with self.database.immediate() as connection:
            if request.request_id is not None:
                existing = connection.execute(
                    "SELECT * FROM price_alarms WHERE department = ? AND request_id = ?",
                    (request.department, request.request_id)).fetchone()
                if existing is not None:
                    same = (existing["condition_json"], existing["portfolio_id"], existing["day_plan_ref"],
                            existing["note"]) == (condition_json, request.portfolio_id, request.day_plan_ref,
                                                  request.note)
                    if not same:
                        raise DuplicateRecord(f"request {request.request_id} already created a different alarm")
                    return self._get(connection, existing["alarm_id"])
            alarm_id = self._new_id()
            candle = condition.basis == "candle"
            connection.execute(
                """INSERT INTO price_alarms (alarm_id, department, basis, kind, symbol, interval_minutes, source,
                   condition_json, portfolio_id, day_plan_ref, task_id, request_id, note, contract_version,
                   created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (alarm_id, request.department, condition.basis, condition.kind, condition.symbol,
                 condition.interval_minutes if candle else None, condition.source if candle else None,
                 condition_json, request.portfolio_id, request.day_plan_ref, request.task_id, request.request_id,
                 request.note, ALARM_CONTRACT_VERSION, now))
            self._event(connection, alarm_id, 1, "armed", now, request.task_id, {"request_id": request.request_id})
            connection.execute(
                "INSERT INTO price_alarm_watch (alarm_id, status, generation, armed_at, updated_at) "
                "VALUES (?, 'armed', 1, ?, ?)", (alarm_id, now, now))
            return self._get(connection, alarm_id)

    def rearm(self, alarm_id: str, *, department: str, after_generation: int | None = None,
              task_id: str | None = None) -> AlarmRecord:
        """Arm a spent alarm again (generation + 1). ``after_generation`` makes a retried re-arm return the same."""
        check_department(department)
        now = self._now()
        with self.database.immediate() as connection:
            record = self._get(connection, alarm_id)
            self._owner(record, department)
            if record.status == "cancelled":
                raise StaleState("the alarm is cancelled; create a new one")
            if record.status == "armed":
                if after_generation is not None and record.generation == after_generation + 1:
                    return record
                raise StaleState("the alarm is still armed; it is re-armed only after it fired")
            if after_generation is not None and after_generation != record.generation:
                raise StaleState(f"the alarm's latest arming is {record.generation}, not {after_generation}")
            generation = record.generation + 1
            self._event(connection, alarm_id, generation, "armed", now, task_id, {})
            connection.execute(
                """UPDATE price_alarm_watch SET status = 'armed', generation = ?, armed_at = ?, fired_at = NULL,
                   evaluated_through = NULL, baseline_side = NULL, baseline_value = NULL, baseline_at = NULL,
                   last_reason = NULL, updated_at = ? WHERE alarm_id = ?""", (generation, now, now, alarm_id))
            return self._get(connection, alarm_id)

    def cancel(self, alarm_id: str, *, department: str, reason: str | None = None,
               task_id: str | None = None) -> AlarmRecord:
        """Retire an armed or spent alarm for good. Cancelling a cancelled alarm changes nothing."""
        check_department(department)
        if reason is not None and (not isinstance(reason, str) or not 1 <= len(reason) <= MAX_NOTE_LENGTH
                                   or _CONTROL.search(reason)):
            raise ValidationFailure(f"a cancellation reason is 1 to {MAX_NOTE_LENGTH} plain characters")
        now = self._now()
        with self.database.immediate() as connection:
            record = self._get(connection, alarm_id)
            self._owner(record, department)
            if record.status == "cancelled":
                return record
            self._event(connection, alarm_id, record.generation, "cancelled", now, task_id, {"reason": reason})
            connection.execute(
                "UPDATE price_alarm_watch SET status = 'cancelled', cancelled_at = ?, updated_at = ? "
                "WHERE alarm_id = ?", (now, now, alarm_id))
            return self._get(connection, alarm_id)

    # --- reading ------------------------------------------------------------------------------------------------

    def get(self, alarm_id: str) -> AlarmRecord:
        with self.database.snapshot() as connection:
            return self._get(connection, alarm_id)

    def alarms(self, *, department: str | None = None, status: str | None = None) -> list[AlarmRecord]:
        clauses, values = [], []
        if department is not None:
            clauses.append("a.department = ?")
            values.append(check_department(department))
        if status is not None:
            clauses.append("w.status = ?")
            values.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.database.execute(f"{_SELECT}{where} ORDER BY a.sequence", tuple(values)).fetchall()
        return [_record(row) for row in rows]

    def armed(self, *, basis: str | None = None, symbol: str | None = None) -> list[AlarmRecord]:
        """Armed alarms, oldest first, optionally of one basis (quote or candle) and symbol."""
        clauses, values = ["w.status = 'armed'"], []
        if basis is not None:
            clauses.append("a.basis = ?")
            values.append(basis)
        if symbol is not None:
            clauses.append("a.symbol = ?")
            values.append(symbol)
        rows = self.database.execute(f"{_SELECT} WHERE {' AND '.join(clauses)} ORDER BY a.sequence",
                                     tuple(values)).fetchall()
        return [_record(row) for row in rows]

    # --- the watcher's side -------------------------------------------------------------------------------------

    def fire(self, alarm_id: str, generation: int, *, observed: Mapping[str, Any]) -> AlarmDelivery | None:
        """Fire one arming: record it, mark the alarm spent and queue the delivery, atomically.

        Returns None, and changes nothing, when that arming is no longer armed (it fired already, was re-armed or
        was cancelled). The store's unique fire per arming backs this up against any other writer.
        """
        now = self._now()
        with self.database.immediate() as connection:
            record = self._get(connection, alarm_id)
            if record.status != "armed" or record.generation != generation:
                return None
            document = {
                "contract": ALARM_CONTRACT_VERSION, "alarm_id": alarm_id, "generation": generation,
                "department": record.department, "portfolio_id": record.portfolio_id,
                "day_plan_ref": record.day_plan_ref, "note": record.note,
                "condition": record.condition.model_dump(mode="json"), "armed_at": record.armed_at,
                "fired_at": now, "observed": dict(observed),
            }
            try:
                self._event(connection, alarm_id, generation, "fired", now, None, {"observed": dict(observed)})
            except sqlite3.IntegrityError:
                return None
            connection.execute(
                "UPDATE price_alarm_watch SET status = 'spent', fired_at = ?, updated_at = ? WHERE alarm_id = ?",
                (now, now, alarm_id))
            return enqueue(connection, department=record.department, kind="alarm_fired",
                           dedup_key=f"fire:{alarm_id}:{generation}", document=document, created_at=now,
                           alarm_id=alarm_id, generation=generation, portfolio_id=record.portfolio_id)

    def advance(self, alarm_id: str, generation: int, *, evaluated_through: str | None = None,
                last_reason: str | None = None) -> bool:
        """Move a candle alarm's cursor (and say why the last candle was not compared). False if no longer armed."""
        with self.database.immediate() as connection:
            cursor = connection.execute(
                """UPDATE price_alarm_watch SET evaluated_through = COALESCE(?, evaluated_through), last_reason = ?,
                   updated_at = ? WHERE alarm_id = ? AND generation = ? AND status = 'armed'""",
                (evaluated_through, last_reason, self._now(), alarm_id, generation))
            return cursor.rowcount == 1

    def set_baseline(self, alarm_id: str, generation: int, *, side: str, value: str, at: str) -> bool:
        """Remember which side of its level a quote alarm last saw, so a crossing survives a restart."""
        with self.database.immediate() as connection:
            cursor = connection.execute(
                """UPDATE price_alarm_watch SET baseline_side = ?, baseline_value = ?, baseline_at = ?, updated_at = ?
                   WHERE alarm_id = ? AND generation = ? AND status = 'armed'""",
                (side, value, at, self._now(), alarm_id, generation))
            return cursor.rowcount == 1

    def baseline(self, alarm_id: str, generation: int) -> tuple[str, str, str] | None:
        row = self.database.execute(
            """SELECT baseline_side, baseline_value, baseline_at FROM price_alarm_watch
               WHERE alarm_id = ? AND generation = ? AND status = 'armed'""", (alarm_id, generation)).fetchone()
        if row is None or row["baseline_side"] is None:
            return None
        return row["baseline_side"], row["baseline_value"], row["baseline_at"]

    def feed_status(self, series: FeedSeries) -> FeedStatus | None:
        row = self.database.execute(
            "SELECT status FROM price_feed_events WHERE series_key = ? ORDER BY sequence DESC LIMIT 1",
            (series.key,)).fetchone()
        return None if row is None else row["status"]

    def record_feed_transition(self, series: FeedSeries, status: FeedStatus,
                               detail: Mapping[str, Any]) -> list[AlarmDelivery]:
        """Record that a feed was lost or restored and tell every department whose alarms depend on it.

        Idempotent: recording the state the feed is already in repeats no notice (nor does a restart). While a feed
        stays lost, recording "lost" again tells only departments that armed an alarm on it since the loss, under
        the same loss event. A restored notice also goes to every department that was told about the loss.
        """
        now = self._now()
        with self.database.immediate() as connection:
            last = connection.execute(
                """SELECT event_id, status, document_json FROM price_feed_events WHERE series_key = ?
                   ORDER BY sequence DESC LIMIT 1""", (series.key,)).fetchone()
            if (last["status"] if last is not None else "restored") == status:
                if status == "restored" or last is None:
                    return []
                # Still lost: a department that armed an alarm on this feed since the loss is told about it too.
                event_id, document = last["event_id"], json.loads(last["document_json"])
            else:
                event_id = str(uuid.uuid4())
                document = {**series.document(), "status": status, "detected_at": now, **dict(detail)}
                connection.execute(
                    """INSERT INTO price_feed_events (event_id, series_key, feed, symbol, interval_minutes, source,
                       status, detected_at, document_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (event_id, series.key, series.feed, series.symbol, series.interval_minutes, series.source,
                     status, now, canonical_json(document)))
                if status == "restored" and last is not None:
                    told = connection.execute("SELECT department FROM alarm_deliveries WHERE dedup_key LIKE ?",
                                              (f"feed:{last['event_id']}:%",)).fetchall()
                else:
                    told = []
            affected: dict[str, list[str]] = {}
            for row in connection.execute(f"{_SELECT} WHERE w.status = 'armed' AND a.basis = ? AND a.symbol = ? "
                                          "AND a.interval_minutes IS ? AND a.source IS ? ORDER BY a.sequence",
                                          (series.feed, series.symbol, series.interval_minutes, series.source)):
                affected.setdefault(row["department"], []).append(row["alarm_id"])
            if document["status"] == "restored":
                for row in told:
                    affected.setdefault(row["department"], [])
            notices = []
            for department in sorted(affected):
                notice = enqueue(connection, department=department, kind=f"feed_{status}",
                                 dedup_key=f"feed:{event_id}:{department}", created_at=now,
                                 document={"contract": ALARM_CONTRACT_VERSION, "feed_event_id": event_id,
                                           **document, "armed_alarm_ids": affected[department]})
                if notice is not None:
                    notices.append(notice)
            return notices

    # --- helpers ------------------------------------------------------------------------------------------------

    @staticmethod
    def _get(connection: sqlite3.Connection, alarm_id: str) -> AlarmRecord:
        row = connection.execute(f"{_SELECT} WHERE a.alarm_id = ?", (alarm_id,)).fetchone()
        if row is None:
            raise NotFound(f"no alarm {alarm_id}")
        return _record(row)

    @staticmethod
    def _owner(record: AlarmRecord, department: str) -> None:
        if record.department != department:
            raise AuthorityDenied("an alarm is re-armed or cancelled only by the department that owns it")

    @staticmethod
    def _event(connection: sqlite3.Connection, alarm_id: str, generation: int, action: str, at: str,
               task_id: str | None, document: Mapping[str, Any]) -> None:
        connection.execute(
            """INSERT INTO price_alarm_events (event_id, alarm_id, generation, action, at, task_id, document_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), alarm_id, generation, action, at, task_id, canonical_json(document)))

    def _now(self) -> str:
        return utc_iso(self.clock.now())


__all__ = ["AlarmBook", "FeedSeries", "FeedStatus", "series_of"]
