"""Fenced role dispatch. Durable applied results recover before snapshots or paid attempts."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable

from trade_graph.application.scheduler import Scheduler, TaskLease
from trade_graph.domain.errors import TradeGraphError


class RoleWorker:
    def __init__(
        self, scheduler: Scheduler, *, owner: str, system_version_id: str, reconcile: Callable[[], None],
        artifact_runtime=None,
    ) -> None:
        self.scheduler, self.owner = scheduler, owner
        self.system_version_id, self.reconcile = system_version_id, reconcile
        self.artifact_runtime = artifact_runtime

    def run_available(self, handlers: dict[str, Callable[[dict], dict]]) -> int:
        completed = 0
        if self.artifact_runtime:
            self.artifact_runtime.maintain(reconcile=self.reconcile, consumer_id=self.owner)
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
            self._observe(task, output)
            return
        if self.artifact_runtime:
            try:
                bundle = self.artifact_runtime.prepare(task, consumer_id=self.owner, reconcile=self.reconcile)
                task["_artifact_bundle"] = bundle
                task["artifact"] = self.artifact_runtime.identity(bundle)
                task["system_version_id"] = bundle["artifact_hash"]
            except (TradeGraphError, OSError, ValueError) as exc:
                self._finish(lease, {"_status": "FAILED", "reason": str(exc)[:500]})
                self.artifact_runtime.maintain(reconcile=self.reconcile, consumer_id=self.owner)
                return
        if row["expected_version"] and row["expected_version"] != task["system_version_id"]:
            self.scheduler.skip(lease, {"skipped": "version", "expected": row["expected_version"]})
            return
        context = getattr(handler, "context", None)
        task["snapshot"] = context(task) if context else {"input": task["input"]}
        if self.artifact_runtime:
            task["snapshot"]["artifact"] = task["artifact"]
            self.artifact_runtime.assert_task(task)
        task["snapshot_id"] = self._snapshot(row, task["snapshot"], version=task["system_version_id"])
        if not getattr(handler, "manages_attempts", False):
            self.scheduler.note_attempt(lease)
        output = handler(task)
        self._finish(lease, output)
        self._observe(task, output)

    def _observe(self, task: dict, output: dict) -> None:
        if self.artifact_runtime:
            self.artifact_runtime.observe(task, output)
            self.artifact_runtime.maintain(reconcile=self.reconcile, consumer_id=self.owner)

    def _finish(self, lease: TaskLease, output: dict) -> None:
        if "_retry_after_seconds" in output:
            self.scheduler.defer(lease, output, output["_retry_after_seconds"])
            return
        self.scheduler.finish(lease, output, output.get("_status", "SUCCEEDED"))

    def _snapshot(self, row, context: dict, *, version: str | None = None) -> str:
        snapshot_id = str(uuid.uuid4())
        now = self.scheduler.now()
        payload = {
            "task_id": row["task_id"],
            "root_task_id": row["root_task_id"],
            "role": row["role"],
            "system_version_id": version or self.system_version_id,
            **context,
        }
        self.scheduler.database.execute(
            """INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)""",
            (snapshot_id, row["portfolio_id"] or "deployment", now, json.dumps(payload, sort_keys=True), now),
        )
        return snapshot_id
