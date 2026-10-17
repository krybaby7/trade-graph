"""The event engine's records (state migration 0035): triggers, wakes, check-ins, focus selections, cycle steps.

Every write is a short transaction; callers that need several writes to happen together nest them inside one
``database.immediate()`` (nested calls become savepoints). Reads return the typed contracts of ``contracts/wakes.py``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from trade_graph.adapters.persistence.db import Database
from trade_graph.contracts.wakes import (
    AllowanceReading,
    CheckIn,
    CycleStatus,
    CycleStep,
    FocusRecord,
    FocusSelection,
    WakeContext,
    WakeOutcomeRecord,
    WakeRecord,
    WakeTrigger,
)
from trade_graph.domain.clock import Clock, parse_utc, utc_iso
from trade_graph.domain.errors import NotFound

TERMINAL_WAKES = ("completed", "failed", "obsolete")


def canonical_json(document: Any) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_of(document: Any) -> str:
    return hashlib.sha256(canonical_json(document).encode("ascii")).hexdigest()


def new_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True, slots=True)
class StoredTrigger:
    trigger: WakeTrigger
    status: str
    carried: bool
    wake_id: str | None
    sequence: int


@dataclass(frozen=True, slots=True)
class OpenWake:
    wake_id: str
    portfolio_id: str
    department: str
    origin: str
    purpose: str
    status: str
    task_id: str | None
    trading_day_start: datetime
    trading_day_end: datetime
    context: dict[str, Any]


class WakeStore:
    def __init__(self, database: Database, clock: Clock) -> None:
        self.database = database
        self.clock = clock

    def now(self) -> str:
        return utc_iso(self.clock.now())

    def available(self) -> bool:
        """False on a database still before migration 0035 (the engine then does nothing)."""
        return self.database.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'wakes'").fetchone() is not None

    # --- triggers -----------------------------------------------------------------------------------------------

    def add_trigger(self, *, carried: bool = False, status: str = "pending", wake_id: str | None = None,
                    **fields: Any) -> tuple[StoredTrigger, bool]:
        """Record a trigger once per ``source_ref``; returns it and whether it is new."""
        with self.database.immediate() as connection:
            existing = connection.execute("SELECT * FROM wake_triggers WHERE source_ref = ?",
                                          (fields["source_ref"],)).fetchone()
            if existing is not None:
                return self._stored(existing), False
            trigger = WakeTrigger.model_validate({"trigger_id": new_id(), **fields})
            now = self.now()
            connection.execute(
                """INSERT INTO wake_triggers (trigger_id, source_ref, portfolio_id, department, kind, occurred_at,
                   document_json, status, carried, wake_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (trigger.trigger_id, trigger.source_ref, trigger.portfolio_id, trigger.department, trigger.kind,
                 utc_iso(trigger.occurred_at), canonical_json(trigger.model_dump(mode="json")), status,
                 int(carried), wake_id, now, now))
            row = connection.execute("SELECT * FROM wake_triggers WHERE trigger_id = ?",
                                     (trigger.trigger_id,)).fetchone()
            return self._stored(row), True

    def trigger_by_source(self, source_ref: str) -> StoredTrigger | None:
        row = self.database.execute("SELECT * FROM wake_triggers WHERE source_ref = ?", (source_ref,)).fetchone()
        return None if row is None else self._stored(row)

    def pending(self, portfolio_id: str, department: str) -> list[StoredTrigger]:
        rows = self.database.execute(
            """SELECT * FROM wake_triggers WHERE portfolio_id = ? AND department = ? AND status = 'pending'
               ORDER BY sequence""", (portfolio_id, department)).fetchall()
        return [self._stored(row) for row in rows]

    def waiting(self, portfolio_id: str) -> dict[str, int]:
        """Departments with at least one fresh pending trigger (one that may start a wake), and how many."""
        rows = self.database.execute(
            """SELECT department, COUNT(*) AS n FROM wake_triggers WHERE portfolio_id = ? AND status = 'pending'
               AND carried = 0 GROUP BY department ORDER BY department""", (portfolio_id,)).fetchall()
        return {row["department"]: row["n"] for row in rows}

    def triggers_of(self, wake_id: str) -> list[StoredTrigger]:
        rows = self.database.execute("SELECT * FROM wake_triggers WHERE wake_id = ? ORDER BY sequence",
                                     (wake_id,)).fetchall()
        return [self._stored(row) for row in rows]

    def claim(self, wake_id: str, trigger_ids: Iterable[str]) -> None:
        ids = list(trigger_ids)
        with self.database.immediate() as connection:
            connection.executemany(
                "UPDATE wake_triggers SET status = 'claimed', wake_id = ?, updated_at = ? WHERE trigger_id = ? "
                "AND status = 'pending'", [(wake_id, self.now(), trigger_id) for trigger_id in ids])

    def deliver(self, wake_id: str) -> None:
        with self.database.immediate() as connection:
            connection.execute("UPDATE wake_triggers SET status = 'delivered', updated_at = ? WHERE wake_id = ? "
                               "AND status = 'claimed'", (self.now(), wake_id))

    def release(self, wake_id: str, *, expire_kinds: Iterable[str] = ()) -> tuple[list[str], list[str]]:
        """Put a wake's undelivered triggers back (carried) for the next wake; expire the listed day-bound kinds.

        Returns (carried trigger ids, expired trigger ids)."""
        expire = set(expire_kinds)
        carried: list[str] = []
        expired: list[str] = []
        with self.database.immediate() as connection:
            for stored in self.triggers_of(wake_id):
                if stored.status != "claimed":
                    continue
                if stored.trigger.kind in expire:
                    connection.execute("UPDATE wake_triggers SET status = 'expired', updated_at = ? "
                                       "WHERE trigger_id = ?", (self.now(), stored.trigger.trigger_id))
                    expired.append(stored.trigger.trigger_id)
                else:
                    connection.execute("UPDATE wake_triggers SET status = 'pending', wake_id = NULL, carried = 1, "
                                       "updated_at = ? WHERE trigger_id = ?", (self.now(), stored.trigger.trigger_id))
                    carried.append(stored.trigger.trigger_id)
        return carried, expired

    def expire_pending(self, portfolio_id: str, kinds: Iterable[str], *, before_day_end: str) -> list[str]:
        """Expire pending day-bound triggers of a trading day that started before ``before_day_end`` (never seen)."""
        wanted = tuple(kinds)
        expired = []
        with self.database.immediate() as connection:
            rows = connection.execute(
                f"""SELECT trigger_id, document_json FROM wake_triggers WHERE portfolio_id = ? AND status = 'pending'
                    AND kind IN ({','.join('?' for _ in wanted)})""", (portfolio_id, *wanted)).fetchall()
            for row in rows:
                document = json.loads(row["document_json"])
                start = document.get("trading_day_start")
                if start is not None and _iso(start) < before_day_end:
                    connection.execute("UPDATE wake_triggers SET status = 'expired', updated_at = ? "
                                       "WHERE trigger_id = ?", (self.now(), row["trigger_id"]))
                    expired.append(row["trigger_id"])
        return expired

    @staticmethod
    def _stored(row: sqlite3.Row) -> StoredTrigger:
        return StoredTrigger(trigger=WakeTrigger.model_validate_json(row["document_json"]), status=row["status"],
                             carried=bool(row["carried"]), wake_id=row["wake_id"], sequence=row["sequence"])

    # --- wakes --------------------------------------------------------------------------------------------------

    def insert_wake(self, *, context: WakeContext, origin: str, task_id: str | None) -> None:
        document = context.model_dump(mode="json")
        with self.database.immediate() as connection:
            connection.execute(
                """INSERT INTO wakes (wake_id, portfolio_id, department, origin, purpose, trading_day_start,
                   trading_day_end, task_id, context_json, allowance_json, status, outcome_json, close_reason,
                   created_at, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', NULL, NULL, ?, NULL)""",
                (context.wake_id, context.portfolio_id, context.department, origin, context.purpose,
                 utc_iso(context.trading_day_start), utc_iso(context.trading_day_end), task_id,
                 canonical_json(document), canonical_json(document["allowance"]), utc_iso(context.created_at)))

    def open_wake(self, portfolio_id: str, department: str) -> OpenWake | None:
        row = self.database.execute(
            """SELECT * FROM wakes WHERE portfolio_id = ? AND department = ? AND origin = 'engine'
               AND status IN ('queued', 'applying')""", (portfolio_id, department)).fetchone()
        return None if row is None else self._open(row)

    def open_wakes(self) -> list[OpenWake]:
        rows = self.database.execute(
            "SELECT * FROM wakes WHERE status IN ('queued', 'applying') ORDER BY sequence").fetchall()
        return [self._open(row) for row in rows]

    def wake_for_task(self, task_id: str) -> OpenWake | None:
        row = self.database.execute("SELECT * FROM wakes WHERE task_id = ?", (task_id,)).fetchone()
        return None if row is None else self._open(row)

    def set_applying(self, wake_id: str, outcome: WakeOutcomeRecord) -> None:
        with self.database.immediate() as connection:
            connection.execute("UPDATE wakes SET status = 'applying', outcome_json = ? WHERE wake_id = ? "
                               "AND status = 'queued'", (canonical_json(outcome.model_dump(mode="json")), wake_id))

    def close_wake(self, wake_id: str, status: str, *, outcome: WakeOutcomeRecord | None,
                   reason: str | None) -> None:
        with self.database.immediate() as connection:
            connection.execute(
                "UPDATE wakes SET status = ?, outcome_json = COALESCE(?, outcome_json), close_reason = ?, "
                "closed_at = ? WHERE wake_id = ? AND status IN ('queued', 'applying')",
                (status, None if outcome is None else canonical_json(outcome.model_dump(mode="json")),
                 None if reason is None else reason[:500], self.now(), wake_id))

    def outcome(self, wake_id: str) -> WakeOutcomeRecord | None:
        row = self.database.execute("SELECT outcome_json FROM wakes WHERE wake_id = ?", (wake_id,)).fetchone()
        if row is None or row["outcome_json"] is None:
            return None
        return WakeOutcomeRecord.model_validate_json(row["outcome_json"])

    def wake(self, wake_id: str) -> WakeRecord:
        row = self.database.execute("SELECT * FROM wakes WHERE wake_id = ?", (wake_id,)).fetchone()
        if row is None:
            raise NotFound(f"no wake {wake_id}")
        return self._record(row)

    def wakes(self, portfolio_id: str, *, since: datetime | None = None, department: str | None = None,
              ) -> list[WakeRecord]:
        """Every wake of a portfolio, oldest first (optionally since a moment, or of one department)."""
        rows = self.database.execute(
            """SELECT * FROM wakes WHERE portfolio_id = ? AND (? IS NULL OR created_at >= ?)
               AND (? IS NULL OR department = ?) ORDER BY sequence""",
            (portfolio_id, None if since is None else utc_iso(since), None if since is None else utc_iso(since),
             department, department)).fetchall()
        return [self._record(row) for row in rows]

    def _record(self, row: sqlite3.Row) -> WakeRecord:
        context = json.loads(row["context_json"])
        return WakeRecord(
            wake_id=row["wake_id"], portfolio_id=row["portfolio_id"], department=row["department"],
            origin=row["origin"], purpose=row["purpose"], status=row["status"],
            trading_day_start=parse_utc(row["trading_day_start"]), trading_day_end=parse_utc(row["trading_day_end"]),
            task_id=row["task_id"], reasons=tuple(context["reasons"]),
            triggers=tuple(WakeTrigger.model_validate(item) for item in context["triggers"]),
            alarm_delivery_ids=tuple(item["delivery_id"] for item in context.get("alarms", [])),
            allowance=AllowanceReading.model_validate_json(row["allowance_json"]),
            created_at=parse_utc(row["created_at"]),
            closed_at=parse_utc(row["closed_at"]) if row["closed_at"] else None, close_reason=row["close_reason"],
            outcome=WakeOutcomeRecord.model_validate_json(row["outcome_json"]) if row["outcome_json"] else None)

    @staticmethod
    def _open(row: sqlite3.Row) -> OpenWake:
        return OpenWake(wake_id=row["wake_id"], portfolio_id=row["portfolio_id"], department=row["department"],
                        origin=row["origin"], purpose=row["purpose"], status=row["status"], task_id=row["task_id"],
                        trading_day_start=parse_utc(row["trading_day_start"]),
                        trading_day_end=parse_utc(row["trading_day_end"]), context=json.loads(row["context_json"]))

    # --- check-ins ----------------------------------------------------------------------------------------------

    def record_check_in(self, *, portfolio_id: str, department: str, due_at: datetime, basis: str,
                        note: str | None, wake_id: str | None, task_id: str | None) -> CheckIn:
        """One check-in per wake: a second call for the same wake returns the first."""
        with self.database.immediate() as connection:
            if wake_id is not None:
                existing = connection.execute("SELECT * FROM department_check_ins WHERE wake_id = ?",
                                              (wake_id,)).fetchone()
                if existing is not None:
                    return self._check_in(existing)
            check_in_id = new_id()
            connection.execute(
                """INSERT INTO department_check_ins (check_in_id, portfolio_id, department, due_at, basis, note,
                   wake_id, task_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (check_in_id, portfolio_id, department, utc_iso(due_at), basis, note, wake_id, task_id, self.now()))
            return self._check_in(connection.execute("SELECT * FROM department_check_ins WHERE check_in_id = ?",
                                                     (check_in_id,)).fetchone())

    def latest_check_in(self, portfolio_id: str, department: str) -> CheckIn | None:
        row = self.database.execute(
            """SELECT * FROM department_check_ins WHERE portfolio_id = ? AND department = ?
               ORDER BY sequence DESC LIMIT 1""", (portfolio_id, department)).fetchone()
        return None if row is None else self._check_in(row)

    @staticmethod
    def _check_in(row: sqlite3.Row) -> CheckIn:
        return CheckIn(check_in_id=row["check_in_id"], portfolio_id=row["portfolio_id"], department=row["department"],
                       due_at=parse_utc(row["due_at"]), basis=row["basis"], note=row["note"], wake_id=row["wake_id"],
                       task_id=row["task_id"], created_at=parse_utc(row["created_at"]))

    # --- focus --------------------------------------------------------------------------------------------------

    def record_focus(self, *, portfolio_id: str, day_start: datetime, day_end: datetime, scan_id: str | None,
                     selection: FocusSelection, wake_id: str | None, task_id: str | None) -> FocusRecord:
        document = selection.model_dump(mode="json")
        with self.database.immediate() as connection:
            if wake_id is not None:
                existing = connection.execute("SELECT * FROM focus_selections WHERE wake_id = ?",
                                              (wake_id,)).fetchone()
                if existing is not None:
                    return self._focus(existing)
            revision = connection.execute(
                "SELECT COALESCE(MAX(revision), 0) + 1 FROM focus_selections WHERE portfolio_id = ? "
                "AND trading_day_start = ?", (portfolio_id, utc_iso(day_start))).fetchone()[0]
            focus_id = new_id()
            connection.execute(
                """INSERT INTO focus_selections (focus_id, portfolio_id, trading_day_start, trading_day_end, revision,
                   scan_id, document_json, document_sha256, wake_id, task_id, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (focus_id, portfolio_id, utc_iso(day_start), utc_iso(day_end), revision, scan_id,
                 canonical_json(document), sha256_of(document), wake_id, task_id, self.now()))
            return self._focus(connection.execute("SELECT * FROM focus_selections WHERE focus_id = ?",
                                                  (focus_id,)).fetchone())

    def focus(self, portfolio_id: str, day_start: datetime) -> FocusRecord | None:
        """The newest focus revision of a trading day."""
        row = self.database.execute(
            """SELECT * FROM focus_selections WHERE portfolio_id = ? AND trading_day_start = ?
               ORDER BY revision DESC LIMIT 1""", (portfolio_id, utc_iso(day_start))).fetchone()
        return None if row is None else self._focus(row)

    def focus_by_id(self, focus_id: str) -> FocusRecord:
        row = self.database.execute("SELECT * FROM focus_selections WHERE focus_id = ?", (focus_id,)).fetchone()
        if row is None:
            raise NotFound(f"no focus selection {focus_id}")
        return self._focus(row)

    @staticmethod
    def _focus(row: sqlite3.Row) -> FocusRecord:
        if sha256_of(json.loads(row["document_json"])) != row["document_sha256"]:
            raise NotFound(f"focus selection {row['focus_id']} no longer matches its hash")
        return FocusRecord(focus_id=row["focus_id"], portfolio_id=row["portfolio_id"],
                           trading_day_start=parse_utc(row["trading_day_start"]),
                           trading_day_end=parse_utc(row["trading_day_end"]), revision=row["revision"],
                           scan_id=row["scan_id"], selection=FocusSelection.model_validate_json(row["document_json"]),
                           wake_id=row["wake_id"], task_id=row["task_id"], recorded_at=parse_utc(row["recorded_at"]))

    # --- the daily cycle ----------------------------------------------------------------------------------------

    def step(self, portfolio_id: str, day_start: datetime, step: CycleStep) -> dict[str, Any] | None:
        row = self.database.execute(
            "SELECT * FROM day_cycle_steps WHERE portfolio_id = ? AND trading_day_start = ? AND step = ?",
            (portfolio_id, utc_iso(day_start), step)).fetchone()
        if row is None:
            return None
        return {"step": row["step"], "status": row["status"], "detail": json.loads(row["detail_json"]),
                "trading_day_start": row["trading_day_start"], "trading_day_end": row["trading_day_end"],
                "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def steps(self, portfolio_id: str, day_start: datetime) -> dict[str, dict[str, Any]]:
        rows = self.database.execute(
            "SELECT step FROM day_cycle_steps WHERE portfolio_id = ? AND trading_day_start = ?",
            (portfolio_id, utc_iso(day_start))).fetchall()
        return {row["step"]: self.step(portfolio_id, day_start, row["step"]) for row in rows}  # type: ignore[misc]

    def begin_step(self, portfolio_id: str, day_start: datetime, day_end: datetime, step: CycleStep, *,
                   status: CycleStatus = "started", detail: Mapping[str, Any] | None = None) -> bool:
        """Record a step once; False when it was already recorded."""
        with self.database.immediate() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO day_cycle_steps (portfolio_id, trading_day_start, trading_day_end, step,
                   status, detail_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (portfolio_id, utc_iso(day_start), utc_iso(day_end), step, status, canonical_json(dict(detail or {})),
                 self.now(), self.now()))
            return cursor.rowcount == 1

    def finish_step(self, portfolio_id: str, day_start: datetime, step: CycleStep, status: CycleStatus,
                    detail: Mapping[str, Any] | None = None) -> bool:
        """Finish a started step once; False when it was not started or already finished."""
        with self.database.immediate() as connection:
            row = connection.execute(
                "SELECT status, detail_json FROM day_cycle_steps WHERE portfolio_id = ? AND trading_day_start = ? "
                "AND step = ?", (portfolio_id, utc_iso(day_start), step)).fetchone()
            if row is None or row["status"] != "started":
                return False
            merged = {**json.loads(row["detail_json"]), **dict(detail or {})}
            connection.execute(
                "UPDATE day_cycle_steps SET status = ?, detail_json = ?, updated_at = ? WHERE portfolio_id = ? "
                "AND trading_day_start = ? AND step = ?",
                (status, canonical_json(merged), self.now(), portfolio_id, utc_iso(day_start), step))
            return True

    def started_days(self, portfolio_id: str) -> list[tuple[datetime, datetime]]:
        rows = self.database.execute(
            """SELECT trading_day_start, trading_day_end FROM day_cycle_steps WHERE portfolio_id = ?
               AND step = 'day_start' ORDER BY trading_day_start""", (portfolio_id,)).fetchall()
        return [(parse_utc(row["trading_day_start"]), parse_utc(row["trading_day_end"])) for row in rows]

    def unfinished_steps(self, portfolio_id: str, *, ended_before: datetime) -> list[tuple[datetime, str]]:
        rows = self.database.execute(
            """SELECT trading_day_start, step FROM day_cycle_steps WHERE portfolio_id = ? AND status = 'started'
               AND trading_day_end <= ? AND step != 'day_end_review' ORDER BY trading_day_start, step""",
            (portfolio_id, utc_iso(ended_before))).fetchall()
        return [(parse_utc(row["trading_day_start"]), row["step"]) for row in rows]

    # --- cursors ------------------------------------------------------------------------------------------------

    def cursor(self, name: str) -> int:
        row = self.database.execute("SELECT position FROM wake_cursors WHERE name = ?", (name,)).fetchone()
        return 0 if row is None else int(row["position"])

    def advance(self, name: str, position: int) -> None:
        with self.database.immediate() as connection:
            connection.execute(
                """INSERT INTO wake_cursors (name, position, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET position = MAX(position, excluded.position),
                   updated_at = excluded.updated_at""", (name, position, self.now()))


def _iso(value: str) -> str:
    """A contract timestamp (``...Z`` or ``+00:00``) in the store's sortable form."""
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return utc_iso(moment)


__all__ = ["TERMINAL_WAKES", "OpenWake", "StoredTrigger", "WakeStore", "canonical_json", "new_id", "sha256_of"]
