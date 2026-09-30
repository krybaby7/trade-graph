"""Deterministic, bounded, restart-safe evidence routing; never a model authority source."""

from __future__ import annotations

import hashlib
import json

from trade_graph.adapters.persistence.db import atomic
from trade_graph.domain.errors import DuplicateRecord, ValidationFailure


def stable_id(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


class Secretary:
    def __init__(self, execution, scheduler) -> None:
        self.execution, self.scheduler = execution, scheduler
        self.database = scheduler.database

    @atomic
    def report(
        self,
        portfolio_id: str,
        *,
        role: str,
        kind: str,
        summary: str,
        evidence_refs: list[str],
        source_key: str,
        material: bool = False,
    ) -> str:
        if role not in {"research", "learning", "optimisation", "trader", "engineer", "system"}:
            raise ValidationFailure("unknown reporting department")
        if not summary or len(summary) > 3000 or len(evidence_refs) > 20 or not evidence_refs:
            raise ValidationFailure("bounded evidence-linked report required")
        if not kind or len(kind) > 80 or not source_key or len(source_key) > 256:
            raise ValidationFailure("bounded report identity required")
        report_id = stable_id(portfolio_id, role, source_key)
        doc = json.dumps(
            {"report_id": report_id, "role": role, "kind": kind, "summary": summary, "evidence_refs": evidence_refs},
            sort_keys=True,
        )
        old = self.database.execute("SELECT * FROM secretary_reports WHERE report_id = ?", (report_id,)).fetchone()
        if old:
            if old["document_json"] != doc or old["material"] != int(material):
                raise DuplicateRecord("report identity is immutable")
            return report_id
        self.database.execute(
            """INSERT INTO secretary_reports
            (report_id, portfolio_id, role, kind, document_json, material, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (report_id, portfolio_id, role, kind, doc, int(material), self.scheduler.now()),
        )
        return report_id

    @atomic
    def collect(self, portfolio_id: str) -> None:
        # Ordered source cursors bound each scan and survive restarts. Routine activity
        # advances the cursor too; unrelated events cannot starve later incidents.
        self.database.execute("INSERT OR IGNORE INTO secretary_inputs VALUES (?, 0, 0)", (portfolio_id,))
        cursor = self.database.execute(
            "SELECT * FROM secretary_inputs WHERE portfolio_id = ?", (portfolio_id,)
        ).fetchone()
        events = self.database.execute(
            """SELECT rowid AS seq, * FROM activity_events
            WHERE portfolio_id = ? AND rowid > ? ORDER BY rowid LIMIT 40""",
            (portfolio_id, cursor["activity_row"]),
        ).fetchall()
        for event in events:
            if any(word in event["kind"].lower() for word in ("incident", "failed", "blocked", "pause", "reconcil")):
                self.report(
                    portfolio_id,
                    role="system",
                    kind="incident",
                    summary=event["kind"],
                    evidence_refs=[event["event_id"]],
                    source_key=event["event_id"],
                    material=True,
                )
        if events:
            self.database.execute(
                "UPDATE secretary_inputs SET activity_row = ? WHERE portfolio_id = ?", (events[-1]["seq"], portfolio_id)
            )
        tasks = self.database.execute(
            """SELECT rowid AS seq, * FROM tasks
            WHERE portfolio_id = ? AND rowid > ? ORDER BY rowid LIMIT 40""",
            (portfolio_id, cursor["task_row"]),
        ).fetchall()
        # A round-robin task cursor revisits changed/overdue tasks without unbounded scans.
        for task in tasks:
            state = task["status"]
            overdue = (
                task["deadline_at"]
                and task["deadline_at"] < self.scheduler.now()
                and state not in {"SUCCEEDED", "FAILED", "CANCELLED", "DEAD_LETTER"}
            )
            if state in {"FAILED", "DEAD_LETTER", "BLOCKED_BUDGET", "WAITING_EXTERNAL"} or overdue:
                kind = "overdue" if overdue else state.lower()
                self.report(
                    portfolio_id,
                    role="system",
                    kind=kind,
                    summary=f"{task['role']} work is {kind}",
                    evidence_refs=[task["task_id"]],
                    source_key=f"task:{task['task_id']}:{kind}",
                    material=True,
                )
        self.database.execute(
            "UPDATE secretary_inputs SET task_row = ? WHERE portfolio_id = ?",
            (tasks[-1]["seq"] if tasks else 0, portfolio_id),
        )

    @atomic
    def process(self, portfolio_id: str, *, route: bool = True) -> dict:
        self.collect(portfolio_id)
        self.database.execute("INSERT OR IGNORE INTO secretary_cursors VALUES (?, 0)", (portfolio_id,))
        cursor = self.database.execute(
            "SELECT sequence FROM secretary_cursors WHERE portfolio_id = ?", (portfolio_id,)
        ).fetchone()["sequence"]
        reports = self.database.execute(
            """SELECT * FROM secretary_reports WHERE portfolio_id = ? AND sequence > ?
            ORDER BY sequence LIMIT 20""",
            (portfolio_id, cursor),
        ).fetchall()
        if reports:
            digest_id = stable_id(portfolio_id, str(cursor), str(reports[-1]["sequence"]))
            groups: dict[str, list[str]] = {}
            for report in reports:
                groups.setdefault(f"{report['role']}:{report['kind']}", []).append(report["report_id"])
            doc = {
                "digest_id": digest_id,
                "portfolio_id": portfolio_id,
                "groups": groups,
                "reports": [json.loads(r["document_json"]) for r in reports],
                "evidence_refs": [r["report_id"] for r in reports],
                "material": any(r["material"] for r in reports),
            }
            self.database.execute(
                "INSERT INTO secretary_digests VALUES (?, ?, ?, ?, ?, ?)",
                (
                    digest_id,
                    portfolio_id,
                    cursor,
                    reports[-1]["sequence"],
                    json.dumps(doc, sort_keys=True),
                    self.scheduler.now(),
                ),
            )
            self.database.execute(
                "UPDATE secretary_cursors SET sequence = ? WHERE portfolio_id = ?",
                (reports[-1]["sequence"], portfolio_id),
            )
            if doc["material"] and route:
                self.route(portfolio_id, doc)
        return self.digest(portfolio_id)

    @atomic
    def route(self, portfolio_id: str, digest: dict) -> str:
        # Material events coalesce by the durable digest identity, not current time.
        policy = self.execution.authority.active_policy()
        task_id = self.scheduler.add_task(
            role="leader",
            objective="Review departmental evidence",
            portfolio_id=portfolio_id,
            dedup_key=f"secretary:{digest['digest_id']}",
            payload={"digest_id": digest["digest_id"]},
            allocated_spend=policy.root_paid_limit.amount,
        )
        return task_id

    @atomic
    def scheduled(self, portfolio_id: str, interval_seconds: int = 3600) -> str | None:
        self.process(portfolio_id, route=False)
        self.scheduler.ensure_schedule(portfolio_id, "leader-review", interval_seconds, "coalesce")
        task_id = self.scheduler.coalesce_due(portfolio_id, "leader-review", "leader")
        if task_id:
            self.scheduler.allocate(task_id, self.execution.authority.active_policy().root_paid_limit.amount)
        return task_id

    def digest(self, portfolio_id: str) -> dict:
        row = self.database.execute(
            """SELECT document_json FROM secretary_digests WHERE portfolio_id = ?
            ORDER BY rowid DESC LIMIT 1""",
            (portfolio_id,),
        ).fetchone()
        doc = json.loads(row["document_json"]) if row else {"reports": [], "evidence_refs": [], "groups": {}}
        pause = self.execution.pause(portfolio_id)
        return {
            **doc,
            "portfolio_id": portfolio_id,
            "pause": pause["profile"] if pause else "RUNNING",
            "tasks": self.database.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE portfolio_id = ?", (portfolio_id,)
            ).fetchone()["n"],
            "orders_created": 0,
        }
