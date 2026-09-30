"""Durable task leases and atomic scheduling; recovery never replays external effects."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from trade_graph.adapters.persistence.db import Database, atomic
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure


@dataclass(frozen=True)
class TaskLease:
    """A claim's fencing token. A worker name alone is not proof of ownership."""

    task_id: str
    owner: str
    token: str
    reclaimed: bool = False


class Scheduler:
    def __init__(self, database: Database, clock: Clock) -> None:
        self.database = database
        self.clock = clock
        self.max_depth = 3
        self.max_descendants = 12

    def now(self) -> str:
        return utc_iso(self.clock.now())

    @staticmethod
    def _positive(value: int, name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValidationFailure(f"{name} must be a positive integer")

    @atomic
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
        expected_version: str | None = None,
        allocated_spend: Decimal | None = None,
        deadline_at: str | None = None,
    ) -> str:
        self._positive(max_attempts, "max_attempts")
        task_id = str(uuid.uuid4())
        root = task_id
        if parent_id is not None:
            parent = self.database.execute("SELECT * FROM tasks WHERE task_id = ?", (parent_id,)).fetchone()
            if parent is None:
                raise ValidationFailure("parent task does not exist")
            root = parent["root_task_id"]
            if root_task_id is not None and root_task_id != root:
                raise AuthorityDenied("child cannot switch root task budgets")
            if parent["portfolio_id"] != portfolio_id:
                raise AuthorityDenied("child cannot switch portfolio scope")
            if self._depth(parent_id) + 1 > self.max_depth:
                raise AuthorityDenied("delegation depth")
        elif root_task_id is not None:
            raise ValidationFailure("a delegated root requires an existing parent")

        # SQLite UNIQUE permits multiple NULL portfolios. Serialize the explicit
        # lookup with insertion so deployment-wide tasks also deduplicate.
        if dedup_key is not None:
            existing = self.database.execute(
                "SELECT * FROM tasks WHERE portfolio_id IS ? AND dedup_key = ?", (portfolio_id, dedup_key)
            ).fetchone()
            if existing is not None:
                if existing["role"] != role or existing["parent_id"] != parent_id:
                    raise ValidationFailure("deduplication key belongs to another task scope")
                return str(existing["task_id"])
        if parent_id is not None and self._descendants(root) >= self.max_descendants:
            raise AuthorityDenied("descendant limit")

        self.database.execute(
            """INSERT INTO tasks
            (task_id, root_task_id, parent_id, portfolio_id, role, objective, status, due_at,
             priority, dedup_key, expected_version, allocated_spend, max_steps, max_attempts,
             attempts_used, input_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'QUEUED', ?, 0, ?, ?, NULL, 3, ?, 0, ?, ?)""",
            (task_id, root, parent_id, portfolio_id, role, objective, due_at or self.now(),
             dedup_key, expected_version, max_attempts, json.dumps(payload or {}), self.now()),
        )
        if allocated_spend is not None:
            self.allocate(task_id, allocated_spend)
        self.database.execute('UPDATE tasks SET deadline_at = ? WHERE task_id = ?', (deadline_at, task_id))
        return task_id

    @atomic
    def allocate(self, task_id: str, amount: Decimal) -> None:
        if not amount.is_finite() or amount < 0:
            raise ValidationFailure('nonnegative finite task budget required')
        row = self.database.execute('SELECT * FROM tasks WHERE task_id = ?', (task_id,)).fetchone()
        if row is None:
            raise ValidationFailure('unknown task allocation')
        if row['allocated_spend'] is not None and Decimal(row['allocated_spend']) != amount:
            raise AuthorityDenied('task commitment is immutable')
        if row['parent_id']:
            parent = self.database.execute('SELECT * FROM tasks WHERE task_id = ?', (row['parent_id'],)).fetchone()
            if parent['allocated_spend'] is None:
                raise AuthorityDenied('parent requires a shared monetary ceiling')
            children = self.database.execute("""SELECT allocated_spend FROM tasks
                WHERE parent_id = ? AND task_id != ?""", (row['parent_id'], task_id)).fetchall()
            committed = sum((Decimal(c['allocated_spend'] or '0') for c in children), Decimal('0'))
            holds = self.database.execute("""SELECT amount, synthetic FROM budget_reservations
                WHERE task_id = ? AND state IN ('RESERVED', 'UNCERTAIN', 'COMMITTED', 'CONSERVATIVE', 'RECONCILED')""",
                (row['parent_id'],)).fetchall()
            used = max((sum((Decimal(h['amount']) for h in holds if h['synthetic'] == mode), Decimal('0'))
                        for mode in (0, 1)), default=Decimal('0'))
            if committed + used + amount > Decimal(parent['allocated_spend']):
                raise AuthorityDenied('shared parent monetary commitment exceeded')
        self.database.execute('UPDATE tasks SET allocated_spend = ? WHERE task_id = ?', (str(amount), task_id))

    @atomic
    def finish(self, lease: TaskLease, output: dict, status: str) -> None:
        if status not in {'SUCCEEDED', 'FAILED', 'BLOCKED_BUDGET', 'WAITING_EXTERNAL'}:
            raise ValidationFailure('invalid task completion state')
        self._leased(lease)
        self.database.execute("""UPDATE tasks SET status = ?, output_json = ?, lease_expires_at = NULL,
            lease_token = NULL, lease_owner = NULL WHERE task_id = ?""", (status, json.dumps(output), lease.task_id))

    @atomic
    def claim(self, owner: str, ttl_seconds: int = 30, roles: set[str] | None = None) -> TaskLease | None:
        """Reclaim expired work; the worker must reconcile before retrying effects.

        WAITING_EXTERNAL and terminal tasks are not automatically retried. Taking
        a lease neither resets attempt counts nor authorizes another paid call.
        """
        self._positive(ttl_seconds, "ttl_seconds")
        if not owner.strip():
            raise ValidationFailure("lease owner is required")
        now = self.now()
        if roles is not None and not roles:
            return None
        role_filter = '' if roles is None else ' AND role IN (' + ','.join('?' for _ in roles) + ')'
        row = self.database.execute(
            """SELECT task_id, status FROM tasks WHERE (
            (status = 'QUEUED' AND (due_at IS NULL OR due_at <= ?)) OR
            (status IN ('LEASED', 'RUNNING') AND (lease_expires_at IS NULL OR lease_expires_at <= ?)))
            """ + role_filter + """ ORDER BY priority DESC, COALESCE(due_at, created_at),
            created_at, task_id LIMIT 1""",
            (now, now, *(sorted(roles) if roles is not None else [])),
        ).fetchone()
        if row is None:
            return None
        lease = TaskLease(
            str(row["task_id"]),
            owner,
            uuid.uuid4().hex,
            reclaimed=row["status"] in {"LEASED", "RUNNING"},
        )
        expiry = utc_iso(self.clock.now() + timedelta(seconds=ttl_seconds))
        self.database.execute(
            """UPDATE tasks SET status = 'LEASED', lease_owner = ?, lease_expires_at = ?, lease_token = ?
            WHERE task_id = ?""",
            (owner, expiry, lease.token, lease.task_id),
        )
        return lease

    def _leased(self, lease: TaskLease) -> sqlite3.Row:
        row = self.database.execute("SELECT * FROM tasks WHERE task_id = ?", (lease.task_id,)).fetchone()
        if (row is None or row["status"] not in {"LEASED", "RUNNING"}
                or row["lease_owner"] != lease.owner or row["lease_token"] != lease.token
                or row["lease_expires_at"] is None or row["lease_expires_at"] <= self.now()):
            raise StaleState("task lease expired, replaced, or no longer active")
        return row

    @atomic
    def renew(self, lease: TaskLease, ttl_seconds: int = 30) -> None:
        self._positive(ttl_seconds, "ttl_seconds")
        self._leased(lease)
        expiry = utc_iso(self.clock.now() + timedelta(seconds=ttl_seconds))
        self.database.execute("UPDATE tasks SET lease_expires_at = ? WHERE task_id = ?", (expiry, lease.task_id))

    def leased_row(self, lease: TaskLease) -> sqlite3.Row:
        return self._leased(lease)

    @atomic
    def skip(self, lease: TaskLease, output: dict) -> None:
        """Terminal skip. Does not count as another external attempt."""
        self._leased(lease)
        self.database.execute(
            """UPDATE tasks SET status = 'DEAD_LETTER', output_json = ?, lease_owner = NULL,
            lease_token = NULL, lease_expires_at = NULL WHERE task_id = ?""",
            (json.dumps(output), lease.task_id),
        )

    @atomic
    def succeed(self, lease: TaskLease, output: dict) -> None:
        self._leased(lease)
        self.database.execute(
            """UPDATE tasks SET status = 'SUCCEEDED', output_json = ?, lease_expires_at = NULL,
            lease_token = NULL, lease_owner = NULL WHERE task_id = ?""",
            (json.dumps(output), lease.task_id),
        )

    def note_attempt(self, lease: TaskLease) -> None:
        # Raise only AFTER the transaction commits so DEAD_LETTER is durable.
        # Call this before each attempt; budget receipts remain independently mandatory.
        exhausted = False
        with self.database.immediate():
            row = self._leased(lease)
            if row["attempts_used"] >= row["max_attempts"]:
                self.database.execute(
                    """UPDATE tasks SET status = 'DEAD_LETTER', lease_owner = NULL,
                    lease_token = NULL, lease_expires_at = NULL WHERE task_id = ?""", (lease.task_id,)
                )
                exhausted = True
            else:
                self.database.execute(
                    "UPDATE tasks SET attempts_used = attempts_used + 1, status = 'RUNNING' WHERE task_id = ?",
                    (lease.task_id,),
                )
        if exhausted:
            raise ValidationFailure("attempt limit")

    @atomic
    def ensure_schedule(self, portfolio_id: str, name: str, interval_seconds: int, policy: str) -> None:
        self._positive(interval_seconds, "interval_seconds")
        if policy != "coalesce":
            raise ValidationFailure("unsupported missed-run policy")
        existing = self.database.execute(
            "SELECT * FROM schedules WHERE portfolio_id = ? AND name = ?", (portfolio_id, name)
        ).fetchone()
        if existing is not None:
            if existing["interval_seconds"] != interval_seconds or existing["missed_run_policy"] != policy:
                raise ValidationFailure("schedule already exists with different settings")
            return
        self.database.execute(
            """INSERT INTO schedules
            (schedule_id, portfolio_id, name, last_due_at, next_due_at, missed_run_policy, cursor, interval_seconds)
            VALUES (?, ?, ?, NULL, ?, ?, NULL, ?)""",
            (str(uuid.uuid4()), portfolio_id, name, self.now(), policy, interval_seconds),
        )

    @atomic
    def coalesce_due(self, portfolio_id: str, name: str, role: str) -> str | None:
        row = self.database.execute(
            "SELECT * FROM schedules WHERE portfolio_id = ? AND name = ?", (portfolio_id, name)
        ).fetchone()
        if row is None or row["next_due_at"] > self.now():
            return None
        if row["missed_run_policy"] != "coalesce":
            raise ValidationFailure("unsupported missed-run policy")
        interval = int(row["interval_seconds"])
        self._positive(interval, "interval_seconds")
        # Key by the persisted occurrence, not the current hour: sub-hourly work
        # remains distinct and a failed transaction can retry the same occurrence.
        task_id = self.add_task(
            role=role, objective=name, portfolio_id=portfolio_id,
            dedup_key=f"schedule:{row['schedule_id']}:{row['next_due_at']}",
        )
        next_due = utc_iso(self.clock.now() + timedelta(seconds=interval))
        self.database.execute(
            "UPDATE schedules SET last_due_at = ?, next_due_at = ? WHERE schedule_id = ?",
            (self.now(), next_due, row["schedule_id"]),
        )
        return task_id

    @atomic
    def acquire_process_lease(self, name: str, owner: str, ttl_seconds: int = 30) -> bool:
        """Use a fresh owner ID per process boot; renew before the lease expires."""
        self._positive(ttl_seconds, "ttl_seconds")
        if not name.strip() or not owner.strip():
            raise ValidationFailure("process lease name and owner are required")
        now = self.clock.now()
        expiry = utc_iso(now + timedelta(seconds=ttl_seconds))
        current = utc_iso(now)
        row = self.database.execute(
            "SELECT owner, expires_at FROM process_leases WHERE lease_name = ?", (name,)
        ).fetchone()
        if row is None:
            self.database.execute(
                "INSERT INTO process_leases (lease_name, owner, expires_at) VALUES (?, ?, ?)", (name, owner, expiry)
            )
            return True
        if row["owner"] == owner or row["expires_at"] <= current:
            self.database.execute(
                "UPDATE process_leases SET owner = ?, expires_at = ? WHERE lease_name = ?", (owner, expiry, name)
            )
            return True
        return False

    def _depth(self, task_id: str) -> int:
        depth = 0
        current = task_id
        seen: set[str] = set()
        while True:
            if current in seen:
                raise ValidationFailure("cyclic task ancestry")
            seen.add(current)
            row = self.database.execute("SELECT parent_id FROM tasks WHERE task_id = ?", (current,)).fetchone()
            if row is None:
                raise ValidationFailure("task ancestry is missing")
            if row["parent_id"] is None:
                return depth
            current = row["parent_id"]
            depth += 1

    def _descendants(self, root_task_id: str) -> int:
        row = self.database.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE root_task_id = ? AND task_id <> root_task_id", (root_task_id,)
        ).fetchone()
        return int(row["n"])
