"""Bounded model analyses publish sourced findings, lesson revisions and proposals."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta
from typing import Literal

from pydantic import Field

from trade_graph.adapters.persistence.db import atomic
from trade_graph.application.leadership import GatewayRole
from trade_graph.application.learning import LearningJournal, OptimisationReview
from trade_graph.application.research import ResearchStore
from trade_graph.contracts.leadership import DepartmentReply
from trade_graph.contracts.models import (
    ChangeTask,
    ContractModel,
    LessonRevision,
    OptimisationProposal,
    ResearchFinding,
)
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.money import Money, parse_decimal


class FindingAnalysis(ContractModel):
    source_ref: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=500)
    claim: str = Field(min_length=1, max_length=500)
    counterevidence: str = Field(min_length=1, max_length=1000)
    invalidation: str = Field(min_length=1, max_length=1000)
    expires_after_seconds: int = Field(ge=60, le=86400)


class ResearchReply(DepartmentReply):
    findings: list[FindingAnalysis] = Field(default_factory=list, max_length=4)


class ResearchHandler(GatewayRole):
    allowed_roles = {"research"}
    reply_type = ResearchReply

    def context(self, task: dict) -> dict:
        context = super().context(task)
        sources = []
        for symbol in context["guard"]["mandate"]["symbols"][:8]:
            observation = self.office.execution.latest_observation(
                symbol, self.scheduler.now(), self.office.execution.venue)
            if observation is not None:
                sources.append({"source_ref": observation.observation_id, "kind": "market_observation",
                                "document": observation.model_dump(mode="json")})
            for finding in ResearchStore(self.database, self.clock).fresh(
                task["portfolio_id"], symbol, as_of=self.clock.now())[:8]:
                sources.append({"source_ref": finding.record_id, "kind": "cached_finding",
                                "document": finding.model_dump(mode="json")})
        sources = list({source["source_ref"]: source for source in sources}.values())[:20]
        return {**context, "sources": sources,
                "evidence_refs": list(dict.fromkeys(context["evidence_refs"] + [s["source_ref"] for s in sources])),
                "analysis_instruction": "Cite supplied sources only. Findings are hypotheses, never trade authority."}

    @atomic
    def apply(self, task: dict, reply: ResearchReply) -> dict:
        self._eligible(task)
        sources = {source["source_ref"]: source for source in task["snapshot"]["sources"]}
        findings = []
        for analysis in reply.findings:
            source = sources.get(analysis.source_ref)
            if source is None:
                raise ValidationFailure("research source is outside the persisted snapshot")
            document = source["document"]
            is_market = source["kind"] == "market_observation"
            # Software preserves provenance/times instead of accepting invented URLs,
            # currencies, source hashes or an asserted provider-managed web search.
            finding = ResearchFinding(
                **self._envelope(task), question=analysis.question, claim=analysis.claim,
                source_url=(f"urn:trade-graph:observation:{analysis.source_ref}"
                            if is_market else document["source_url"]),
                publisher=document.get("venue", document.get("publisher", "cached source")),
                published_at_utc=document.get("event_time_utc", document.get("published_at_utc")),
                event_at_utc=document.get("event_time_utc", document.get("event_at_utc")),
                retrieved_at_utc=self.clock.now(), available_at_utc=self.clock.now(),
                source_hash=hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest(),
                instruments=[document["symbol"]] if is_market else document["instruments"],
                relevance="source-analysis", counterevidence=analysis.counterevidence,
                expires_at_utc=self.clock.now() + timedelta(seconds=analysis.expires_after_seconds),
                invalidation=analysis.invalidation,
            )
            ResearchStore(self.database, self.clock)._insert(finding)
            findings.append(finding.record_id)
        output = super().apply(task, reply)
        return self._enrich(task, output, finding_ids=findings)

    def _envelope(self, task: dict) -> dict:
        return {"record_id": str(uuid.uuid4()), "created_at_utc": self.clock.now(), "run_id": task["snapshot_id"],
                "task_id": task["task_id"], "root_task_id": task["root_task_id"], "portfolio_id": task["portfolio_id"],
                "mode": "paper", "system_version_id": task["system_version_id"], "trace_id": task["task_id"],
                "evidence_refs": task["snapshot"]["evidence_refs"]}

    def _enrich(self, task: dict, output: dict, **fields) -> dict:
        output.update(fields)
        record_ids = [record_id for values in fields.values() for record_id in values]
        if record_ids:
            output["journal_report_id"] = self.secretary.report(
                task["portfolio_id"], role=task["role"], kind="journal", summary=output["summary"],
                evidence_refs=record_ids, source_key=f"journal:{task['task_id']}", material=False,
            )
        self.database.execute("UPDATE role_results SET document_json = ? WHERE task_id = ?",
                              (json.dumps(output, sort_keys=True), task["task_id"]))
        return output


class LessonAnalysis(ContractModel):
    lesson_id: str | None = Field(default=None, max_length=128)
    observation: str = Field(min_length=1, max_length=1000)
    supporting_cases: list[str] = Field(max_length=20)
    counterexamples: list[str] = Field(min_length=1, max_length=20)
    explanation: str = Field(min_length=1, max_length=1000)
    proposed_improvement: str = Field(min_length=1, max_length=1000)
    validation_method: str = Field(min_length=1, max_length=1000)
    scope: str = Field(min_length=1, max_length=500)
    sample_note: str = Field(min_length=1, max_length=500)
    linked_decisions: list[str] = Field(min_length=1, max_length=20)
    confidence_category: Literal["insufficient", "tentative", "testing", "supported"]
    status: Literal["tentative", "testing", "supported", "contradicted", "retired"]
    process_assessment: Literal["valid_thesis", "invalid_process", "unclassified"]
    outcome_sign: Literal["gain", "loss", "flat", "unknown"]


class LearningReply(DepartmentReply):
    lessons: list[LessonAnalysis] = Field(default_factory=list, max_length=4)


class LearningHandler(ResearchHandler):
    allowed_roles = {"learning"}
    reply_type = LearningReply

    def context(self, task: dict) -> dict:
        context = GatewayRole.context(self, task)
        rows = self.database.execute("SELECT payload_json FROM decisions WHERE portfolio_id = ? AND created_at <= ? "
                                     "ORDER BY created_at DESC, rowid DESC LIMIT 20",
                                     (task["portfolio_id"], self.scheduler.now())).fetchall()
        decisions = [json.loads(row[0]) for row in rows]
        lesson_rows = self.database.execute("SELECT document_json FROM lessons WHERE portfolio_id = ? "
                                            "AND created_at <= ? ORDER BY created_at DESC, rowid DESC LIMIT 20",
                                            (task["portfolio_id"], self.scheduler.now())).fetchall()
        lessons = [json.loads(row[0]) for row in lesson_rows]
        return {**context, "decisions": decisions, "lesson_revisions": lessons,
                "evidence_refs": list(dict.fromkeys(context["evidence_refs"] + [d["record_id"] for d in decisions])),
                "analysis_instruction": ("Grade process separately from outcome. "
                                         "Preserve counterevidence across revisions.")}

    @atomic
    def apply(self, task: dict, reply: LearningReply) -> dict:
        self._eligible(task)
        decision_ids = {decision["record_id"] for decision in task["snapshot"]["decisions"]}
        journal = LearningJournal(self.database, self.clock)
        records = []
        for analysis in reply.lessons:
            if not set(analysis.linked_decisions).issubset(decision_ids):
                raise ValidationFailure("lesson cites decisions outside the persisted snapshot")
            prior = None
            if analysis.lesson_id:
                history = journal.history(analysis.lesson_id)
                prior = history[-1] if history else None
                if prior and (prior.portfolio_id != task["portfolio_id"] or prior.record_id not in {
                    item["record_id"] for item in task["snapshot"]["lesson_revisions"]
                }):
                    raise ValidationFailure("lesson revision is outside the persisted snapshot")
            lesson = LessonRevision(
                **self._envelope(task), **analysis.model_dump(exclude={"lesson_id"}),
                lesson_id=analysis.lesson_id or str(uuid.uuid4()), revision=prior.revision + 1 if prior else 1,
                supersedes=prior.record_id if prior else None,
            )
            journal.append(task["portfolio_id"], lesson)
            records.append(lesson.record_id)
        return self._enrich(task, GatewayRole.apply(self, task, reply), lesson_revision_ids=records)


class ProposalAnalysis(ContractModel):
    issue: str = Field(min_length=1, max_length=1000)
    resources: str = Field(min_length=1, max_length=1000)
    interval: str = Field(min_length=1, max_length=1000)
    modification: str = Field(min_length=1, max_length=1000)
    expected_benefit: str = Field(min_length=1, max_length=1000)
    quality_risk: str = Field(min_length=1, max_length=1000)
    validation_metrics: list[str] = Field(min_length=1, max_length=10)


class OptimisationReply(DepartmentReply):
    proposals: list[ProposalAnalysis] = Field(default_factory=list, max_length=4)
    changes: list[ChangeAnalysis] = Field(default_factory=list, max_length=2)


class ChangeAnalysis(ContractModel):
    objective: str = Field(min_length=1, max_length=1000)
    allowed_classes: list[Literal["artifact_config", "prompt", "report_template", "context_policy", "schedule",
                                  "approved_model_routing"]] = Field(min_length=1, max_length=6)
    allowed_paths: list[str] = Field(min_length=1, max_length=5)
    invariants: list[str] = Field(min_length=1, max_length=10)
    max_spend_eur: str = Field(min_length=1, max_length=32, pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
    max_steps: int = Field(ge=1, le=10)
    test_plan: str = Field(min_length=1, max_length=1000)
    success_criteria: str = Field(min_length=1, max_length=1000)
    rollback_criteria: str = Field(min_length=1, max_length=1000)
    expires_after_seconds: int = Field(ge=60, le=86400)


OptimisationReply.model_rebuild()


class OptimisationHandler(ResearchHandler):
    allowed_roles = {"optimisation"}
    reply_type = OptimisationReply

    def context(self, task: dict) -> dict:
        context = GatewayRole.context(self, task)
        expenses = self.office.budget.expense_views(self.deployment_id)
        counts = self.database.execute("SELECT role, status, COUNT(*) AS count FROM tasks WHERE portfolio_id = ? "
                                       "GROUP BY role, status ORDER BY role, status",
                                       (task["portfolio_id"],)).fetchall()
        return {**context, "expenses": {key: str(value) if not isinstance(value, dict) else {
            name: str(amount) for name, amount in value.items()} for key, value in expenses.items()},
                "task_counts": [dict(row) for row in counts],
                "analysis_instruction": ("Propose measurable bounded improvements. "
                                         "Reconciliation and protected controls stay active.")}

    @atomic
    def apply(self, task: dict, reply: OptimisationReply) -> dict:
        self._eligible(task)
        records = []
        for analysis in reply.proposals:
            proposal = OptimisationProposal(
                **self._envelope(task), **analysis.model_dump(), disables_reconciliation=False)
            OptimisationReview(self.database, self.clock).propose(proposal)
            records.append(proposal.record_id)
        changes = []
        policy = self.office.execution.authority.active_policy()
        for analysis in reply.changes:
            spend = parse_decimal(analysis.max_spend_eur)
            if (spend < 0 or spend > policy.root_paid_limit.amount
                    or analysis.max_steps > policy.engineer_max_paid_attempts
                    or not set(analysis.allowed_classes).issubset(policy.allowed_change_classes)):
                raise ValidationFailure("proposed change exceeds protected owner bounds")
            active = task["snapshot"]["guard"]["active"]
            change = ChangeTask(
                **self._envelope(task),
                **analysis.model_dump(exclude={"max_spend_eur", "expires_after_seconds"}),
                baseline_version=active["version_id"], baseline_hash=task["system_version_id"],
                max_spend=Money(amount=spend, currency="EUR"),
                expires_at_utc=self.clock.now() + timedelta(seconds=analysis.expires_after_seconds),
            )
            self.engineer.propose(task["portfolio_id"], change)
            changes.append(change.record_id)
        return self._enrich(task, GatewayRole.apply(self, task, reply), proposal_ids=records, change_ids=changes)
