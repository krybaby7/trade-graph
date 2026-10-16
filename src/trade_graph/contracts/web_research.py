"""Web research contracts (T36): stored search evidence, cited findings and the owner spend policy.

Provider-neutral and SDK-free. Search results are data, never instructions. A finding
may cite only a ``source_ref`` that software stored before the model read it (D124).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, field_serializer, field_validator, model_validator

from trade_graph.contracts.models import ContractModel
from trade_graph.domain.money import canonical_decimal, parse_decimal

# Text that tries to steer the reader. It stays stored as data and is flagged; it never acts.
INJECTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "disregard previous",
    "system prompt",
    "you must buy",
    "you must sell",
    "tool call",
)

Recency = Literal["day", "week", "month", "year"]


def looks_like_instruction(text: str) -> bool:
    folded = " ".join(text.casefold().split())
    return any(marker in folded for marker in INJECTION_MARKERS)


class SearchQuery(ContractModel):
    """One software-run search. Domain filters are not offered, so the model cannot pick sources."""

    query: str = Field(min_length=1, max_length=400)
    recency: Recency | None = None
    max_results: int = Field(default=5, ge=1, le=20)

    @field_validator("query", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return " ".join(value.split()) if isinstance(value, str) else value


class SearchHit(ContractModel):
    """One result as returned, bounded. Rank starts at 1."""

    rank: int = Field(ge=1, le=20)
    title: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=8, max_length=2048)
    publisher: str = Field(min_length=1, max_length=253)
    snippet: str = Field(max_length=4000)
    published_on: date | None = None
    last_updated: date | None = None
    untrusted_text: bool = False


class StoredSource(ContractModel):
    """A search result after software stored it. Only these can be cited."""

    source_ref: str = Field(min_length=1, max_length=200)
    run_id: str
    call_id: str
    query: str
    hit: SearchHit
    retrieved_at_utc: str
    content_sha256: str = Field(min_length=64, max_length=64)


class WebFinding(ContractModel):
    question: str = Field(min_length=1, max_length=500)
    claim: str = Field(min_length=1, max_length=1000)
    source_ref: str = Field(min_length=1, max_length=200)
    instruments: list[str] = Field(default_factory=list, max_length=10)
    counterevidence: str = Field(min_length=1, max_length=1000)
    invalidation: str = Field(min_length=1, max_length=500)
    # Notes may last for a day plan (T37). Three days is the outer bound.
    expires_after_seconds: int = Field(ge=300, le=259_200)


class WebResearchReply(ContractModel):
    """What a Perplexity-backed round returns. Later rounds (T37, T38) may subclass it."""

    summary: str = Field(min_length=1, max_length=2000)
    findings: list[WebFinding] = Field(default_factory=list, max_length=12)


class PerplexitySpendPolicy(ContractModel):
    """Owner money rule for Perplexity-backed rounds. A money limit, not an activity cap (D116).

    ``unlimited_testing`` is D125's explicit testing mode: no limit, but every call is still
    reserved and receipted. ``monthly_limit`` pauses Research and Leader rounds once the UTC
    month's Perplexity spend, counting unresolved calls at their reserved maximum, would pass it.
    There is no default: without an owner policy, Perplexity calls stay off.
    """

    revision_id: str = Field(min_length=1, max_length=128)
    mode: Literal["unlimited_testing", "monthly_limit"]
    monthly_limit_usd: Decimal | None = None
    decision_ref: str = Field(min_length=1, max_length=64)
    set_by: Literal["owner"] = "owner"

    @field_validator("monthly_limit_usd", mode="before")
    @classmethod
    def _limit(cls, value: Any) -> Decimal | None:
        return None if value is None else parse_decimal(value)

    @model_validator(mode="after")
    def _consistent(self) -> PerplexitySpendPolicy:
        if self.mode == "unlimited_testing":
            if self.monthly_limit_usd is not None:
                raise ValueError("unlimited testing mode has no limit")
            if self.decision_ref != "D125":
                raise ValueError("unlimited testing mode is the owner's D125 decision")
        elif self.monthly_limit_usd is None or not self.monthly_limit_usd.is_finite() or self.monthly_limit_usd <= 0:
            raise ValueError("a monthly limit needs a positive USD amount")
        return self

    @field_serializer("monthly_limit_usd")
    def _ser(self, value: Decimal | None) -> str | None:
        return None if value is None else canonical_decimal(value)


RunStatus = Literal["completed", "paused_at_limit", "rejected", "failed", "uncertain", "disabled"]


class WebResearchOutcome(ContractModel):
    """The result of one round. Receipts and stored sources survive every status."""

    run_id: str
    status: RunStatus
    reason: str = Field(default="", max_length=500)
    failure: str | None = None
    billing: Literal["resolved", "unresolved", "none"]
    reply: dict[str, Any] | None = None
    finding_ids: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    agent_reservations: list[str] = Field(default_factory=list)
    search_call_ids: list[str] = Field(default_factory=list)
    unresolved_reservations: list[str] = Field(default_factory=list)
