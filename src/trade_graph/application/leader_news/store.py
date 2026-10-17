"""The Leader's news-round records (T38, migration 0039): rounds, the requests they routed, and reads for HQ.

A round is written ``started`` before any paid call and finishes once. Its receipts and stored sources are not copied:
they stay where T36 keeps them (``usage_receipts``, ``web_search_sources``) and are read through the round's web
research run, so a later reconciliation of an unknown bill shows here too.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from trade_graph.adapters.persistence.db import Database
from trade_graph.contracts.leader_news import LeaderBrief, LeaderNewsReply, LeaderRound, RoutedRequest
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import StaleState

_UNRESOLVED = ("RESERVED", "UNCERTAIN")


def canonical_json(document: Any) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def new_round_id() -> str:
    return "lnr-" + uuid.uuid4().hex


def new_request_id() -> str:
    return "ldr-" + uuid.uuid4().hex[:16]


class LeaderNewsStore:
    def __init__(self, database: Database, clock: Clock) -> None:
        self.database, self.clock = database, clock

    def available(self) -> bool:
        """False on a database still before migration 0039."""
        return self.database.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = "
                                     "'leader_news_rounds'").fetchone() is not None

    # --- writing ------------------------------------------------------------------------------------------------

    def start(self, *, portfolio_id: str, task_id: str, wake_id: str, trading_day_start: datetime,
              brief: dict[str, Any]) -> str:
        """Record the round before anything is sent. One round per wake task (the table refuses a second)."""
        LeaderBrief.model_validate(brief)
        round_id = new_round_id()
        document = canonical_json(brief)
        with self.database.immediate():
            self.database.execute(
                """INSERT INTO leader_news_rounds (round_id, portfolio_id, task_id, wake_id, trading_day_start, status,
                   run_id, brief_json, brief_sha256, reply_json, output_json, reason, started_at, finished_at)
                   VALUES (?, ?, ?, ?, ?, 'started', NULL, ?, ?, NULL, NULL, NULL, ?, NULL)""",
                (round_id, portfolio_id, task_id, wake_id, utc_iso(trading_day_start), document,
                 hashlib.sha256(document.encode()).hexdigest(), utc_iso(self.clock.now())))
        return round_id

    def finish(self, round_id: str, status: str, *, output: dict[str, Any], run_id: str | None = None,
               reply: dict[str, Any] | None = None, reason: str | None = None,
               requests: Iterable[RoutedRequest] = ()) -> None:
        """Finish a started round once, with the requests it routed, in one transaction."""
        now = utc_iso(self.clock.now())
        with self.database.immediate():
            for request in requests:
                document = request.model_dump(mode="json")
                self.database.execute(
                    """INSERT INTO leader_round_requests (request_id, round_id, portfolio_id, kind, department,
                       asked_department, position, trigger_source_ref, document_json, created_at)
                       SELECT ?, round_id, portfolio_id, ?, ?, ?, ?, ?, ?, ? FROM leader_news_rounds
                       WHERE round_id = ?""",
                    (request.request_id, request.kind, request.department, request.asked_department,
                     request.position, request.trigger_source_ref, canonical_json(document), now, round_id))
            changed = self.database.execute(
                """UPDATE leader_news_rounds SET status = ?, run_id = ?, reply_json = ?, output_json = ?, reason = ?,
                   finished_at = ? WHERE round_id = ? AND status = 'started'""",
                (status, run_id, None if reply is None else canonical_json(reply), canonical_json(output),
                 None if reason is None else reason[:500], now, round_id)).rowcount
            if not changed:
                raise StaleState("this news round has already finished")

    # --- reading ------------------------------------------------------------------------------------------------

    def by_task(self, task_id: str) -> sqlite3.Row | None:
        if not self.available():
            return None
        return self.database.execute("SELECT * FROM leader_news_rounds WHERE task_id = ?", (task_id,)).fetchone()

    def round(self, round_id: str) -> LeaderRound:
        row = self.database.execute("SELECT * FROM leader_news_rounds WHERE round_id = ?", (round_id,)).fetchone()
        if row is None:
            raise KeyError(round_id)
        return self._record(row)

    def rounds(self, portfolio_id: str, *, since: datetime | None = None) -> list[LeaderRound]:
        """The portfolio's rounds in order; ``since`` keeps those started at or after it."""
        rows = self.database.execute(
            "SELECT * FROM leader_news_rounds WHERE portfolio_id = ? AND (? IS NULL OR started_at >= ?) "
            "ORDER BY sequence", (portfolio_id, *(2 * [None if since is None else utc_iso(since)]))).fetchall()
        return [self._record(row) for row in rows]

    def latest_completed(self, portfolio_id: str) -> LeaderRound | None:
        row = self.database.execute(
            "SELECT * FROM leader_news_rounds WHERE portfolio_id = ? AND status = 'completed' "
            "ORDER BY sequence DESC LIMIT 1", (portfolio_id,)).fetchone()
        return None if row is None else self._record(row)

    def requests_of(self, round_id: str) -> tuple[RoutedRequest, ...]:
        rows = self.database.execute("SELECT document_json FROM leader_round_requests WHERE round_id = ? "
                                     "ORDER BY position", (round_id,)).fetchall()
        return tuple(RoutedRequest.model_validate_json(row["document_json"]) for row in rows)

    def evidence(self, run_id: str | None) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        """A run's billing, receipt ids, unresolved reservations and stored sources, as T36 recorded them."""
        if run_id is None:
            return "none", (), (), ()
        rows = self.database.execute(
            """SELECT b.reservation_id, b.state, r.receipt_id FROM (
                 SELECT reservation_id, sequence, 0 AS kind FROM web_research_steps WHERE run_id = ?
                 UNION ALL SELECT reservation_id, sequence, 1 AS kind FROM web_search_calls WHERE run_id = ?) AS x
               JOIN budget_reservations b ON b.reservation_id = x.reservation_id
               LEFT JOIN usage_receipts r ON r.reservation_id = b.reservation_id
               ORDER BY b.created_at, x.kind, x.sequence""", (run_id, run_id)).fetchall()
        receipts = tuple(row["receipt_id"] for row in rows if row["receipt_id"] is not None)
        unresolved = tuple(row["reservation_id"] for row in rows if row["state"] in _UNRESOLVED)
        billing = "none" if not rows else "unresolved" if unresolved else "resolved"
        sources = tuple(row["source_ref"] for row in self.database.execute(
            """SELECT s.source_ref FROM web_search_sources s JOIN web_search_calls c ON c.call_id = s.call_id
               WHERE s.run_id = ? ORDER BY c.sequence, s.rank""", (run_id,)).fetchall())
        return billing, receipts, unresolved, sources

    def _record(self, row: sqlite3.Row) -> LeaderRound:
        reply = None if row["reply_json"] is None else json.loads(row["reply_json"])
        output = None if row["output_json"] is None else json.loads(row["output_json"])
        summary = check_in = None
        if reply is not None and row["status"] == "completed":
            parsed = LeaderNewsReply.model_validate(reply)
            summary, check_in = parsed.summary, parsed.next_check_in
        elif output is not None:
            summary = (output.get("wake_outcome") or {}).get("summary")
        billing, receipts, unresolved, sources = self.evidence(row["run_id"])
        return LeaderRound(
            round_id=row["round_id"], portfolio_id=row["portfolio_id"], task_id=row["task_id"],
            wake_id=row["wake_id"], trading_day_start=row["trading_day_start"], status=row["status"],
            run_id=row["run_id"], reason=row["reason"], brief=json.loads(row["brief_json"]), reply=reply,
            summary=summary, check_in=check_in, billing=billing, receipt_ids=receipts,
            unresolved_reservations=unresolved, source_refs=sources, requests=self.requests_of(row["round_id"]),
            started_at=row["started_at"], finished_at=row["finished_at"])


__all__ = ["LeaderNewsStore", "canonical_json", "new_request_id", "new_round_id"]
