"""The owner's protected switch that moves departments to Perplexity (T37, T38; D124, D125, D138 item 4, D205).

Research and the Leader's news rounds may think with Claude Sonnet 5.5 through Perplexity's pay-as-you-go Agent
API (D124). Whether they do is an owner setting, never an agent's: it ships switched off, and only the owner
command ``trade-graph perplexity-departments --confirm`` records a revision (step L3). A revision that switches a
department on carries the owner's Perplexity spend rule (``PerplexitySpendPolicy``): during testing no money limit
(D125, D138 item 4), later a monthly limit. The Trader stays on its subscription and is never switched here.

Provider-neutral and SDK-free. A revision is data the software reads; nothing in it reaches a model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from trade_graph.contracts.models import ContractModel
from trade_graph.contracts.web_research import PerplexitySpendPolicy

PerplexityDepartment = Literal["research", "leader"]
PERPLEXITY_DEPARTMENTS: tuple[PerplexityDepartment, ...] = ("research", "leader")


class PerplexityDepartments(ContractModel):
    """One revision of the switch. ``revision_id`` None is the shipped default: everything off, no spend rule."""

    schema_version: Literal[1] = 1
    revision_id: str | None = Field(default=None, min_length=1, max_length=128)
    research: bool = False
    leader: bool = False
    spend_policy: PerplexitySpendPolicy | None = None
    set_by: Literal["owner"] = "owner"

    @model_validator(mode="after")
    def _spend_rule(self) -> PerplexityDepartments:
        if (self.research or self.leader) and self.spend_policy is None:
            raise ValueError("a department switched to Perplexity needs the owner's spend rule (D124, D125)")
        if self.revision_id is None and (self.research or self.leader or self.spend_policy is not None):
            raise ValueError("only a recorded owner revision switches anything on")
        return self

    @classmethod
    def off(cls) -> PerplexityDepartments:
        return cls()

    def enabled_for(self, department: str) -> bool:
        if department not in PERPLEXITY_DEPARTMENTS:
            raise ValueError(f"only {' and '.join(PERPLEXITY_DEPARTMENTS)} can be switched to Perplexity; the "
                             "Trader stays on its subscription (D124)")
        return bool(getattr(self, department))


__all__ = ["PERPLEXITY_DEPARTMENTS", "PerplexityDepartment", "PerplexityDepartments"]
