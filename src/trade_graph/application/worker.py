"""One fenced paper worker. A reclaimed task reconciles before another effect."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable

from trade_graph.application.scheduler import Scheduler, TaskLease


class RoleWorker:
    def __init__(
        self,
        scheduler: Scheduler,
        *,
        owner: str,
        system_version_id: str,
        reconcile: Callable[[], None],
    ) -> None:
        self.scheduler = scheduler
        self.owner = owner
        self.system_version_id = system_version_id
        self.reconcile = reconcile

    def run_available(self, handlers: dict[str, Callable[[dict], dict]]) -> int:
        if not self.scheduler.acquire_process_lease("role-worker", self.owner):
            return 0
        completed = 0
        while True:
            lease = self.scheduler.claim(self.owner)
            if lease is None:
                return completed
            self._run_lease(lease, handlers)
            completed += 1

    def _run_lease(self, lease: TaskLease, handlers: dict[str, Callable[[dict], dict]]) -> None:
        if lease.reclaimed:
            self.reconcile()
        row = self.scheduler.leased_row(lease)
        expected = row["expected_version"]
        if expected and expected != self.system_version_id:
            self.scheduler.skip(lease, {"skipped": "version", "expected": expected})
            return
        handler = handlers.get(row["role"])
        if handler is None:
            self.scheduler.skip(lease, {"skipped": "no-handler", "role": row["role"]})
            return
        self._snapshot(row)
        self.scheduler.note_attempt(lease)
        self.scheduler.succeed(lease, handler({
            "task_id": row["task_id"],
            "role": row["role"],
            "objective": row["objective"],
            "portfolio_id": row["portfolio_id"],
            "input": json.loads(row["input_json"]),
        }))

    def _snapshot(self, row) -> None:
        now = self.scheduler.now()
        payload = {
            "task_id": row["task_id"],
            "role": row["role"],
            "system_version_id": self.system_version_id,
            "input": json.loads(row["input_json"]),
        }
        self.scheduler.database.execute(
            """INSERT INTO snapshots (snapshot_id, portfolio_id, as_of, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                row["portfolio_id"] or "deployment",
                now,
                json.dumps(payload),
                now,
            ),
        )
