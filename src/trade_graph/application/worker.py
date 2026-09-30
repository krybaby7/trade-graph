"""Fenced role dispatch. Durable applied results recover before snapshots or paid attempts."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable

from trade_graph.application.scheduler import Scheduler, TaskLease


class RoleWorker:
    def __init__(
        self, scheduler: Scheduler, *, owner: str, system_version_id: str, reconcile: Callable[[], None]
    ) -> None:
        self.scheduler, self.owner = scheduler, owner
        self.system_version_id, self.reconcile = system_version_id, reconcile

    def run_available(self, handlers: dict[str, Callable[[dict], dict]]) -> int:
        completed = 0
        while self.scheduler.acquire_process_lease("role-worker", self.owner):
            lease = self.scheduler.claim(self.owner, roles=set(handlers))
            if lease is None:
                break
            self._run_lease(lease, handlers)
            completed += 1
        return completed

    def _run_lease(self, lease: TaskLease, handlers: dict[str, Callable[[dict], dict]]) -> None:
        if lease.reclaimed:
            self.reconcile()
        row = self.scheduler.leased_row(lease)
        handler = handlers[row["role"]]
        task = {
            "task_id": row["task_id"],
            "root_task_id": row["root_task_id"],
            "role": row["role"],
            "objective": row["objective"],
            "portfolio_id": row["portfolio_id"],
            "input": json.loads(row["input_json"]),
            "_lease": lease,
            "system_version_id": self.system_version_id,
        }
        recover = getattr(handler, "recover", None)
        output = recover(task) if recover else None
        if output is not None:
            self._finish(lease, output)
            return
        if row["expected_version"] and row["expected_version"] != self.system_version_id:
            self.scheduler.skip(lease, {"skipped": "version", "expected": row["expected_version"]})
            return
        context = getattr(handler, "context", None)
        task["snapshot"] = context(task) if context else {"input": task["input"]}
        task["snapshot_id"] = self._snapshot(row, task["snapshot"])
        self.scheduler.note_attempt(lease)
        self._finish(lease, handler(task))

    def _finish(self, lease: TaskLease, output: dict) -> None:
        self.scheduler.finish(lease, output, output.get("_status", "SUCCEEDED"))

    def _snapshot(self, row, context: dict) -> str:
        snapshot_id = str(uuid.uuid4())
        now = self.scheduler.now()
        payload = {
            "task_id": row["task_id"],
            "root_task_id": row["root_task_id"],
            "role": row["role"],
            "system_version_id": self.system_version_id,
            **context,
        }
        self.scheduler.database.execute(
            """INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)""",
            (snapshot_id, row["portfolio_id"] or "deployment", now, json.dumps(payload, sort_keys=True), now),
        )
        return snapshot_id
