"""The alarm delivery queue: what a wake dispatcher (W5-A, T35) reads to wake a department and hand it its alarms.

Contract (also in docs/PRICE-ALARMS.md):

- Every fire, and every lost or restored feed an armed alarm depends on, adds one ``pending`` item to the owning
  department's queue in the same transaction that records it. Items are never dropped, merged or rewritten.
- ``waiting()`` names the departments with pending items. A pending item is a reason to wake a sleeping department.
- When a department wakes, the dispatcher calls ``claim(department, wake_id)``: every pending item of that
  department, oldest first, becomes that wake's batch. Items that arrive while the department is awake stay pending,
  so they are delivered together at its next wake (D116 batching, not a cap).
- ``claim`` with the same ``wake_id`` returns the same batch, so a dispatcher that saved its wake id before claiming
  can recover after a crash. A department has at most one open claim; ``open_claim`` finds it.
- ``acknowledge(wake_id)`` once the department has received the batch: items become ``delivered``.
  ``release(wake_id)`` if the wake ended before the department saw them: items return to ``pending`` in their
  original order and join the next wake. A wake id is used once.

Nothing here calls a model, spends money or limits how many items or wakes there are.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Mapping
from typing import Any

from trade_graph.adapters.persistence.db import Database
from trade_graph.contracts.alarms import ALARM_DEPARTMENTS, REFERENCE_PATTERN, AlarmDelivery, DeliveryBatch
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import NotFound, StaleState, ValidationFailure

_REFERENCE = re.compile(REFERENCE_PATTERN)


def canonical_json(document: Mapping[str, Any]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def check_department(department: object) -> str:
    if department not in ALARM_DEPARTMENTS:
        raise ValidationFailure(f"department must be one of {', '.join(ALARM_DEPARTMENTS)}")
    return department  # type: ignore[return-value]


def delivery_from_row(row: sqlite3.Row) -> AlarmDelivery:
    return AlarmDelivery(delivery_id=row["delivery_id"], sequence=row["sequence"], department=row["department"],
                         kind=row["kind"], alarm_id=row["alarm_id"], generation=row["generation"],
                         portfolio_id=row["portfolio_id"], created_at=row["created_at"],
                         document=json.loads(row["document_json"]))


def enqueue(connection: sqlite3.Connection, *, department: str, kind: str, dedup_key: str,
            document: Mapping[str, Any], created_at: str, alarm_id: str | None = None,
            generation: int | None = None, portfolio_id: str | None = None) -> AlarmDelivery | None:
    """Add one pending item inside the caller's transaction; None when ``dedup_key`` was already queued."""
    if connection.execute("SELECT 1 FROM alarm_deliveries WHERE dedup_key = ?", (dedup_key,)).fetchone():
        return None
    delivery_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO alarm_deliveries (delivery_id, dedup_key, department, kind, alarm_id, generation,
           portfolio_id, document_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (delivery_id, dedup_key, department, kind, alarm_id, generation, portfolio_id, canonical_json(document),
         created_at))
    row = connection.execute("SELECT * FROM alarm_deliveries WHERE delivery_id = ?", (delivery_id,)).fetchone()
    return delivery_from_row(row)


class AlarmQueue:
    """Per-department queue of fired alarms and feed notices, claimed one batch per wake."""

    def __init__(self, database: Database, clock: Clock) -> None:
        self.database = database
        self.clock = clock

    def waiting(self) -> dict[str, int]:
        """Departments with pending items and how many each has."""
        rows = self.database.execute(
            """SELECT department, COUNT(*) AS items FROM alarm_deliveries WHERE status = 'pending'
               GROUP BY department ORDER BY department""").fetchall()
        return {row["department"]: row["items"] for row in rows}

    def pending(self, department: str) -> tuple[AlarmDelivery, ...]:
        check_department(department)
        rows = self.database.execute(
            "SELECT * FROM alarm_deliveries WHERE department = ? AND status = 'pending' ORDER BY sequence",
            (department,)).fetchall()
        return tuple(delivery_from_row(row) for row in rows)

    def claim(self, department: str, wake_id: str) -> DeliveryBatch:
        """Every pending item of ``department`` becomes this wake's batch; the same ``wake_id`` gets the same batch."""
        check_department(department)
        self._check_wake_id(wake_id)
        with self.database.immediate() as connection:
            row = connection.execute("SELECT * FROM alarm_wake_claims WHERE wake_id = ?", (wake_id,)).fetchone()
            if row is not None:
                if row["department"] != department:
                    raise ValidationFailure(f"wake {wake_id} belongs to another department")
                if row["status"] == "released":
                    raise StaleState(f"wake {wake_id} was released; a wake id is used once")
                return self._batch(connection, row)
            open_row = connection.execute(
                "SELECT wake_id FROM alarm_wake_claims WHERE department = ? AND status = 'claimed'",
                (department,)).fetchone()
            if open_row is not None:
                raise StaleState(f"{department} still has wake {open_row['wake_id']} open; acknowledge or release "
                                 "it first")
            count = connection.execute(
                "SELECT COUNT(*) FROM alarm_deliveries WHERE department = ? AND status = 'pending'",
                (department,)).fetchone()[0]
            connection.execute(
                """INSERT INTO alarm_wake_claims (wake_id, department, status, item_count, claimed_at, closed_at)
                   VALUES (?, ?, 'claimed', ?, ?, NULL)""", (wake_id, department, count, self._now()))
            connection.execute(
                "UPDATE alarm_deliveries SET status = 'claimed', wake_id = ? WHERE department = ? "
                "AND status = 'pending'", (wake_id, department))
            return self._batch(connection, self._claim_row(connection, wake_id))

    def acknowledge(self, wake_id: str) -> DeliveryBatch:
        """The department received the batch: its items are delivered. Acknowledging twice changes nothing."""
        with self.database.immediate() as connection:
            row = self._claim_row(connection, wake_id)
            if row["status"] == "released":
                raise StaleState(f"wake {wake_id} was released; its items went back to the queue")
            if row["status"] == "claimed":
                connection.execute(
                    "UPDATE alarm_deliveries SET status = 'delivered' WHERE wake_id = ? AND status = 'claimed'",
                    (wake_id,))
                connection.execute(
                    "UPDATE alarm_wake_claims SET status = 'acknowledged', closed_at = ? WHERE wake_id = ?",
                    (self._now(), wake_id))
            return self._batch(connection, self._claim_row(connection, wake_id))

    def release(self, wake_id: str) -> DeliveryBatch:
        """The wake ended before the department saw its batch: the items return to pending, in their order.

        The first release returns the items it put back (for the dispatcher's log); a repeated one returns none.
        """
        with self.database.immediate() as connection:
            row = self._claim_row(connection, wake_id)
            if row["status"] == "acknowledged":
                raise StaleState(f"wake {wake_id} was acknowledged; its items were delivered")
            if row["status"] != "claimed":
                return self._batch(connection, row)  # released before: its items are back in the queue
            returned = self._batch(connection, row).items
            connection.execute(
                "UPDATE alarm_deliveries SET status = 'pending', wake_id = NULL WHERE wake_id = ? "
                "AND status = 'claimed'", (wake_id,))
            connection.execute(
                "UPDATE alarm_wake_claims SET status = 'released', closed_at = ? WHERE wake_id = ?",
                (self._now(), wake_id))
            closed = self._claim_row(connection, wake_id)
            return DeliveryBatch(wake_id=wake_id, department=closed["department"], status="released",
                                 claimed_at=closed["claimed_at"], items=returned)

    def open_claim(self, department: str) -> DeliveryBatch | None:
        """The department's claimed, not yet acknowledged or released wake, if any (for recovery after a crash)."""
        check_department(department)
        with self.database.snapshot() as connection:
            row = connection.execute(
                "SELECT * FROM alarm_wake_claims WHERE department = ? AND status = 'claimed'",
                (department,)).fetchone()
            return None if row is None else self._batch(connection, row)

    def batch(self, wake_id: str) -> DeliveryBatch:
        with self.database.snapshot() as connection:
            return self._batch(connection, self._claim_row(connection, wake_id))

    @staticmethod
    def _claim_row(connection: sqlite3.Connection, wake_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM alarm_wake_claims WHERE wake_id = ?", (wake_id,)).fetchone()
        if row is None:
            raise NotFound(f"no wake {wake_id} has claimed alarm deliveries")
        return row

    @staticmethod
    def _batch(connection: sqlite3.Connection, row: sqlite3.Row) -> DeliveryBatch:
        items = connection.execute("SELECT * FROM alarm_deliveries WHERE wake_id = ? ORDER BY sequence",
                                   (row["wake_id"],)).fetchall()
        return DeliveryBatch(wake_id=row["wake_id"], department=row["department"], status=row["status"],
                             claimed_at=row["claimed_at"], items=tuple(delivery_from_row(item) for item in items))

    @staticmethod
    def _check_wake_id(wake_id: object) -> None:
        if not isinstance(wake_id, str) or not _REFERENCE.match(wake_id):
            raise ValidationFailure("a wake id is 1 to 128 letters, digits or ._:/-")

    def _now(self) -> str:
        return utc_iso(self.clock.now())


__all__ = ["AlarmQueue", "canonical_json", "check_department", "delivery_from_row", "enqueue"]
