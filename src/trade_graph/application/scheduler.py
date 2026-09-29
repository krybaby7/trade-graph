"""Durable tasks, leases and missed-run coalescing. A second worker cannot start the scheduler."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

from trade_graph.adapters.persistence.db import Database
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import AuthorityDenied, ValidationFailure


class Scheduler:
    def __init__(self, database: Database, clock: Clock) -> None:
        self.database = database
        self.clock = clock
        self.max_depth = 3
        self.max_descendants = 12

    def now(self) -> str:
        return utc_iso(self.clock.now())

    def add_task(
        self,
        *,
        role: str,
        objective: str,
        portfolio_id: str | None,
        root_task_id: str | None = None,
        parent_id: str | None = None,
        dedup_key: str | None = None,
        max_attempts: int = 3,
        due_at: str | None = None,
        payload: dict | None = None,
    ) -> str:
        task_id = str(uuid.uuid4())
        root = root_task_id or task_id
        if parent_id is not None:
            depth = self._depth(parent_id) + 1
            if depth > self.max_depth:
                raise AuthorityDenied("delegation depth")
            if self._descendants(root) >= self.max_descendants:
                raise AuthorityDenied("descendant limit")
        now = self.now()
        try:
            with self.database.immediate() as conn:
                conn.execute(
                    """INSERT INTO tasks
                    (task_id, root_task_id, parent_id, portfolio_id, role, objective, status, due_at,
                     priority, dedup_key, expected_version, allocated_spend, max_steps, max_attempts,
                     attempts_used, input_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'QUEUED', ?, 0, ?, NULL, NULL, 3, ?, 0, ?, ?)""",
                    (
                        task_id,
                        root,
                        parent_id,
                        portfolio_id,
                        role,
                        objective,
                        due_at or now,
                        dedup_key,
                        max_attempts,
                        json.dumps(payload or {}),
                        now,
                    ),
                )
        except Exception as exc:
            if "UNIQUE" in str(exc):
                row = self.database.execute(
                    "SELECT task_id FROM tasks WHERE portfolio_id IS ? AND dedup_key = ?",
                    (portfolio_id, dedup_key),
                ).fetchone()
                if row:
                    return row["task_id"]
            raise
        return task_id

    def claim(self, owner: str, ttl_seconds: int = 30) -> str | None:
        now = self.clock.now()
        expiry = utc_iso(now + timedelta(seconds=ttl_seconds))
        with self.database.immediate() as conn:
            row = conn.execute(
                """SELECT task_id FROM tasks
                WHERE status = 'QUEUED' AND (due_at IS NULL OR due_at <= ?)
                ORDER BY created_at LIMIT 1""",
                (utc_iso(now),),
            ).fetchone()
            if row is None:
                return None
            cursor = conn.execute(
                """UPDATE tasks SET status = 'LEASED', lease_owner = ?, lease_expires_at = ?
                WHERE task_id = ? AND status = 'QUEUED'""",
                (owner, expiry, row["task_id"]),
            )
            if cursor.rowcount != 1:
                return None
        return row["task_id"]

    def succeed(self, task_id: str, output: dict) -> None:
        with self.database.immediate() as conn:
            conn.execute(
                "UPDATE tasks SET status = 'SUCCEEDED', output_json = ? WHERE task_id = ?",
                (json.dumps(output), task_id),
            )

    def note_attempt(self, task_id: str) -> None:
        with self.database.immediate() as conn:
            row = conn.execute(
                "SELECT attempts_used, max_attempts FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            used = row["attempts_used"] + 1
            status = "DEAD_LETTER" if used > row["max_attempts"] else "QUEUED"
            if used > row["max_attempts"]:
                raise ValidationFailure("attempt limit")
            conn.execute(
                "UPDATE tasks SET attempts_used = ?, status = ? WHERE task_id = ?",
                (used, "RUNNING" if status == "QUEUED" else status, task_id),
            )

    def ensure_schedule(self, portfolio_id: str, name: str, interval_seconds: int, policy: str) -> None:
        now = self.now()
        with self.database.immediate() as conn:
            existing = conn.execute(
                "SELECT schedule_id FROM schedules WHERE portfolio_id = ? AND name = ?",
                (portfolio_id, name),
            ).fetchone()
            if existing:
                return
            conn.execute(
                """INSERT INTO schedules
                (schedule_id, portfolio_id, name, last_due_at, next_due_at, missed_run_policy, cursor, interval_seconds)
                VALUES (?, ?, ?, NULL, ?, ?, NULL, ?)""",
                (str(uuid.uuid4()), portfolio_id, name, now, policy, interval_seconds),
            )

    def coalesce_due(self, portfolio_id: str, name: str, role: str) -> str | None:
        row = self.database.execute(
            "SELECT * FROM schedules WHERE portfolio_id = ? AND name = ?",
            (portfolio_id, name),
        ).fetchone()
        if row is None or row["next_due_at"] > self.now():
            return None
        if row["missed_run_policy"] != "coalesce":
            raise ValidationFailure("unsupported missed-run policy")
        task_id = self.add_task(
            role=role,
            objective=name,
            portfolio_id=portfolio_id,
            dedup_key=f"{name}:{self.now()[:13]}",
        )
        interval = int(row["interval_seconds"])
        next_due = utc_iso(self.clock.now() + timedelta(seconds=interval))
        with self.database.immediate() as conn:
            conn.execute(
                "UPDATE schedules SET last_due_at = ?, next_due_at = ? WHERE schedule_id = ?",
                (self.now(), next_due, row["schedule_id"]),
            )
        return task_id

    def acquire_process_lease(self, name: str, owner: str, ttl_seconds: int = 30) -> bool:
        now = self.clock.now()
        expiry = utc_iso(now + timedelta(seconds=ttl_seconds))
        current = utc_iso(now)
        with self.database.immediate() as conn:
            row = conn.execute(
                "SELECT owner, expires_at FROM process_leases WHERE lease_name = ?",
                (name,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO process_leases (lease_name, owner, expires_at) VALUES (?, ?, ?)",
                    (name, owner, expiry),
                )
                return True
            if row["owner"] == owner or row["expires_at"] <= current:
                cursor = conn.execute(
                    """UPDATE process_leases SET owner = ?, expires_at = ?
                    WHERE lease_name = ? AND (owner = ? OR expires_at <= ?)""",
                    (owner, expiry, name, row["owner"], current),
                )
                return cursor.rowcount == 1
        return False

    def _depth(self, task_id: str) -> int:
        depth = 0
        current = task_id
        while True:
            row = self.database.execute(
                "SELECT parent_id FROM tasks WHERE task_id = ?",
                (current,),
            ).fetchone()
            if row is None or row["parent_id"] is None:
                return depth
            current = row["parent_id"]
            depth += 1

    def _descendants(self, root_task_id: str) -> int:
        row = self.database.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE root_task_id = ?",
            (root_task_id,),
        ).fetchone()
        return int(row["n"])
