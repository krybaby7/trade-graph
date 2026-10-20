"""The strategy lab's queue in the state database (migration 0051, W11-B; D290-D292).

The Trading desk's lab requests arrive in batches: one batch per answer (one wake), applied by the event engine with
the rest of the answer. Each request is checked on its own when it is queued (``LabRequest.problem``); a refused one is
recorded ``refused`` with its reason at once, so nothing is ever dropped silently. The worker then takes the open
requests one at a time in the order they were queued (``next_open``), and records each one's end in the transaction
that writes its practice-range records. A batch is finished when none of its requests is open any more; the event
engine's trigger source then wakes the desk once with ``lab_results`` (``unannounced`` and ``announce``).

Nothing here limits how many requests or batches there are (D116); reads that feed a turn are bounded by the reader.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from trade_graph.adapters.persistence.db import Database
from trade_graph.contracts.strategy_lab import MAX_REASON, AppliedLabRequest, LabRequest
from trade_graph.domain.clock import Clock, parse_utc, utc_iso
from trade_graph.domain.errors import NotFound, ValidationFailure

OPEN = ("queued", "running")


def _json(document: Any) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _reason(text: str) -> str:
    text = " ".join(str(text).split()) or "refused"
    return text[:MAX_REASON]


@dataclass(frozen=True, slots=True)
class LabRequestRecord:
    sequence: int
    request_id: str
    batch_id: str
    position: int
    portfolio_id: str
    family: str | None
    request: LabRequest
    status: str
    attempts: int
    reason: str | None
    result: dict[str, Any] | None
    seconds: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None

    @property
    def kind(self) -> str:
        return self.request.kind


@dataclass(frozen=True, slots=True)
class LabBatch:
    sequence: int
    batch_id: str
    portfolio_id: str
    department: str
    wake_id: str | None
    task_id: str | None
    requests: int
    created_at: str
    finished_at: datetime | None
    trigger_id: str | None


class LabQueue:
    def __init__(self, database: Database, clock: Clock) -> None:
        self.database, self.clock = database, clock

    def available(self) -> bool:
        """False on a database still before migration 0051."""
        return self.database.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' "
                                     "AND name = 'strategy_lab_requests'").fetchone() is not None

    # --- queueing a batch ---------------------------------------------------------------------------------------

    def submit(self, portfolio_id: str, requests: Sequence[LabRequest], *, batch_id: str,
               wake_id: str | None = None, task_id: str | None = None) -> list[AppliedLabRequest]:
        """Queue one answer's requests in order (``<batch_id>:<n>``); the same batch again changes nothing."""
        if not requests:
            return []
        with self.database.immediate():
            if self.database.execute("SELECT 1 FROM strategy_lab_batches WHERE batch_id = ?",
                                     (batch_id,)).fetchone() is not None:
                return self.applied(batch_id)
            now = utc_iso(self.clock.now())
            checked = [(request, request.problem()) for request in requests]
            finished = now if all(problem is not None for _request, problem in checked) else None
            self.database.execute(
                "INSERT INTO strategy_lab_batches (batch_id, portfolio_id, department, wake_id, task_id, requests, "
                "created_at, finished_at, trigger_id) VALUES (?, ?, 'trader', ?, ?, ?, ?, ?, NULL)",
                (batch_id, portfolio_id, wake_id, task_id, len(requests), now, finished))
            for position, (request, problem) in enumerate(checked):
                self.database.execute(
                    "INSERT INTO strategy_lab_requests (request_id, batch_id, position, portfolio_id, kind, family, "
                    "document_json, status, attempts, reason, result_json, seconds, created_at, started_at, "
                    "finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, NULL, NULL, ?, NULL, ?)",
                    (f"{batch_id}:{position}", batch_id, position, portfolio_id, request.kind,
                     (request.family() or "")[:64] or None, _json(request.model_dump(mode="json")),
                     "queued" if problem is None else "refused", None if problem is None else _reason(problem), now,
                     None if problem is None else now))
        return self.applied(batch_id)

    def applied(self, batch_id: str) -> list[AppliedLabRequest]:
        """What became of each request of a batch when it was queued: queued, or refused at once with the reason."""
        rows = self.database.execute(
            "SELECT request_id, kind, status, reason, started_at FROM strategy_lab_requests WHERE batch_id = ? "
            "ORDER BY position", (batch_id,)).fetchall()
        return [AppliedLabRequest(request_id=row["request_id"], kind=row["kind"],
                                  status="refused" if row["status"] == "refused" and row["started_at"] is None
                                  else "queued", reason=row["reason"] if row["started_at"] is None else None)
                for row in rows]

    # --- the worker's side --------------------------------------------------------------------------------------

    def next_open(self) -> LabRequestRecord | None:
        """The oldest request not ended yet: one a restart interrupted (``running``) comes first, as it was queued first."""
        row = self.database.execute(
            "SELECT * FROM strategy_lab_requests WHERE status IN ('queued', 'running') ORDER BY sequence LIMIT 1"
        ).fetchone()
        return None if row is None else self._record(row)

    def claim(self, request_id: str) -> LabRequestRecord:
        with self.database.immediate():
            changed = self.database.execute(
                "UPDATE strategy_lab_requests SET status = 'running', attempts = attempts + 1, started_at = ? "
                "WHERE request_id = ? AND status IN ('queued', 'running')",
                (utc_iso(self.clock.now()), request_id)).rowcount
            if not changed:
                raise ValidationFailure(f"lab request {request_id} is not open")
            return self.request(request_id)

    def put_back(self, request_id: str) -> None:
        """A clean stop: the running request waits again, with nothing of it recorded."""
        with self.database.immediate():
            self.database.execute("UPDATE strategy_lab_requests SET status = 'queued' WHERE request_id = ? "
                                  "AND status = 'running'", (request_id,))

    def finish(self, request_id: str, status: str, *, result: dict[str, Any] | None = None,
               reason: str | None = None, seconds: str | None = None) -> LabBatch:
        """End a running request (inside the caller's transaction, with its records) and finish its batch when it was
        the batch's last open request."""
        if status not in ("done", "refused", "failed"):
            raise ValidationFailure("a lab request ends done, refused or failed")
        with self.database.immediate():
            now = utc_iso(self.clock.now())
            changed = self.database.execute(
                "UPDATE strategy_lab_requests SET status = ?, result_json = ?, reason = ?, seconds = ?, "
                "finished_at = ? WHERE request_id = ? AND status = 'running'",
                (status, None if result is None else _json(result), None if reason is None else _reason(reason),
                 seconds, now, request_id)).rowcount
            if not changed:
                raise ValidationFailure(f"lab request {request_id} is not running")
            batch_id = self.database.execute("SELECT batch_id FROM strategy_lab_requests WHERE request_id = ?",
                                             (request_id,)).fetchone()["batch_id"]
            still_open = self.database.execute(
                "SELECT 1 FROM strategy_lab_requests WHERE batch_id = ? AND status IN ('queued', 'running') LIMIT 1",
                (batch_id,)).fetchone()
            if still_open is None:
                self.database.execute("UPDATE strategy_lab_batches SET finished_at = ? WHERE batch_id = ? "
                                      "AND finished_at IS NULL", (now, batch_id))
            return self.batch(batch_id)

    # --- waking the desk ----------------------------------------------------------------------------------------

    def unannounced(self, portfolio_ids: Iterable[str], *, limit: int = 500) -> list[LabBatch]:
        """Finished batches whose ``lab_results`` trigger is not recorded yet, oldest first (a read bound, not a cap)."""
        wanted = tuple(dict.fromkeys(portfolio_ids))
        if not wanted:
            return []
        marks = ", ".join("?" for _ in wanted)
        rows = self.database.execute(
            f"SELECT * FROM strategy_lab_batches WHERE finished_at IS NOT NULL AND trigger_id IS NULL "
            f"AND portfolio_id IN ({marks}) ORDER BY sequence LIMIT ?", (*wanted, limit)).fetchall()
        return [self._batch(row) for row in rows]

    def announce(self, batch_id: str, trigger_id: str) -> None:
        with self.database.immediate():
            self.database.execute("UPDATE strategy_lab_batches SET trigger_id = ? WHERE batch_id = ? "
                                  "AND trigger_id IS NULL", (trigger_id, batch_id))

    def batch_counts(self, batch_id: str) -> dict[str, int]:
        rows = self.database.execute("SELECT status, COUNT(*) AS n FROM strategy_lab_requests WHERE batch_id = ? "
                                     "GROUP BY status", (batch_id,)).fetchall()
        return {row["status"]: row["n"] for row in rows}

    # --- reading ------------------------------------------------------------------------------------------------

    def batch(self, batch_id: str) -> LabBatch:
        row = self.database.execute("SELECT * FROM strategy_lab_batches WHERE batch_id = ?", (batch_id,)).fetchone()
        if row is None:
            raise NotFound(f"no lab batch {batch_id}")
        return self._batch(row)

    def request(self, request_id: str) -> LabRequestRecord:
        row = self.database.execute("SELECT * FROM strategy_lab_requests WHERE request_id = ?",
                                    (request_id,)).fetchone()
        if row is None:
            raise NotFound(f"no lab request {request_id}")
        return self._record(row)

    def requests(self, *, batch_id: str) -> list[LabRequestRecord]:
        rows = self.database.execute("SELECT * FROM strategy_lab_requests WHERE batch_id = ? ORDER BY position",
                                     (batch_id,)).fetchall()
        return [self._record(row) for row in rows]

    def counts(self, portfolio_id: str | None = None) -> dict[str, int]:
        """How many requests are queued and running (the whole installation's queue, or one portfolio's)."""
        rows = self.database.execute(
            "SELECT status, COUNT(*) AS n FROM strategy_lab_requests WHERE status IN ('queued', 'running') "
            "AND (? IS NULL OR portfolio_id = ?) GROUP BY status", (portfolio_id, portfolio_id)).fetchall()
        found = {row["status"]: row["n"] for row in rows}
        return {"queued": found.get("queued", 0), "running": found.get("running", 0)}

    def waiting(self, portfolio_id: str, *, limit: int = 10) -> list[LabRequestRecord]:
        rows = self.database.execute(
            "SELECT * FROM strategy_lab_requests WHERE status IN ('queued', 'running') AND portfolio_id = ? "
            "ORDER BY sequence LIMIT ?", (portfolio_id, limit)).fetchall()
        return [self._record(row) for row in rows]

    def ended(self, portfolio_id: str, *, limit: int = 20) -> list[LabRequestRecord]:
        """The newest ended requests of a portfolio, newest first."""
        rows = self.database.execute(
            "SELECT * FROM strategy_lab_requests WHERE portfolio_id = ? AND finished_at IS NOT NULL "
            "ORDER BY finished_at DESC, sequence DESC LIMIT ?", (portfolio_id, limit)).fetchall()
        return [self._record(row) for row in rows]

    def ended_count(self, portfolio_id: str) -> dict[str, int]:
        rows = self.database.execute(
            "SELECT status, COUNT(*) AS n FROM strategy_lab_requests WHERE portfolio_id = ? "
            "AND finished_at IS NOT NULL GROUP BY status", (portfolio_id,)).fetchall()
        return {row["status"]: row["n"] for row in rows}

    @staticmethod
    def _record(row: sqlite3.Row) -> LabRequestRecord:
        try:
            request = LabRequest.model_validate_json(row["document_json"])
        except ValidationError:  # a stored request always validated; a later contract change must not hide it
            request = LabRequest(kind=row["kind"])
        return LabRequestRecord(
            sequence=row["sequence"], request_id=row["request_id"], batch_id=row["batch_id"], position=row["position"],
            portfolio_id=row["portfolio_id"], family=row["family"], request=request, status=row["status"],
            attempts=row["attempts"], reason=row["reason"],
            result=None if row["result_json"] is None else json.loads(row["result_json"]), seconds=row["seconds"],
            created_at=row["created_at"], started_at=row["started_at"], finished_at=row["finished_at"])

    @staticmethod
    def _batch(row: sqlite3.Row) -> LabBatch:
        return LabBatch(sequence=row["sequence"], batch_id=row["batch_id"], portfolio_id=row["portfolio_id"],
                        department=row["department"], wake_id=row["wake_id"], task_id=row["task_id"],
                        requests=row["requests"], created_at=row["created_at"],
                        finished_at=None if row["finished_at"] is None else parse_utc(row["finished_at"]),
                        trigger_id=row["trigger_id"])


__all__ = ["OPEN", "LabBatch", "LabQueue", "LabRequestRecord"]
