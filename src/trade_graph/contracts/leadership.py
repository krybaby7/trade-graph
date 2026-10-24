"""Model-authored intentions. Authority and attribution are supplied only by software."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from trade_graph.contracts.models import ContractModel, Mandate, PauseProfile
from trade_graph.domain.money import Money

Department = Literal["research", "learning", "optimisation", "trader"]

EVIDENCE_CITATION_RULE = (
    "For evidence_refs, copy IDs verbatim only from the top-level context.evidence_refs list. "
    "Nested report references are provenance, not eligible citations unless also listed there."
)


class Assignment(ContractModel):
    kind: Literal["assign", "consult"]
    role: Department
    objective: str = Field(min_length=1, max_length=2000)
    budget: Money
    max_attempts: int = Field(default=3, ge=1, le=3)
    followup_budget: Money | None = None


class Commission(ContractModel):
    kind: Literal["commission"]
    change_id: str


class MandateChange(ContractModel):
    kind: Literal["mandate"]
    mandate: Mandate


class ScheduleChange(ContractModel):
    kind: Literal["schedule"]
    role: Department | Literal["leader"]
    interval_seconds: int = Field(ge=60)


class PauseChange(ContractModel):
    kind: Literal["pause"]
    profile: PauseProfile
    reason: str = Field(min_length=1, max_length=1000)


class ResourceAllocation(ContractModel):
    kind: Literal["allocate"]
    role: Department | Literal["leader", "engineer"]
    budget: Money


class CandidateAction(ContractModel):
    kind: Literal["activate", "reject"]
    candidate_id: str


Action = Annotated[
    Assignment | Commission | MandateChange | ScheduleChange | PauseChange | ResourceAllocation | CandidateAction,
    Field(discriminator="kind"),
]


class LeaderReply(ContractModel):
    evidence_refs: list[str] = Field(min_length=1, max_length=40, description=EVIDENCE_CITATION_RULE)
    rationale: str = Field(min_length=1, max_length=2000)
    intended_outcome: str = Field(min_length=1, max_length=2000)
    review_criteria: str = Field(min_length=1, max_length=2000)
    actions: list[Action] = Field(default_factory=list, max_length=8)


class DepartmentReply(ContractModel):
    # Together these fit the Secretary's 3,000-character/20-reference envelope.
    evidence_refs: list[str] = Field(min_length=1, max_length=20, description=EVIDENCE_CITATION_RULE)
    summary: str = Field(min_length=1, max_length=1500)
    outcome: str = Field(min_length=1, max_length=1400)
