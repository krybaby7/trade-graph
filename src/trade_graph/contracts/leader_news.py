"""The Leader's news rounds (T38; D121, D122, D124, D125, D123, D116): what a round returns and what software records.

The Leader works like a human manager. On its wakes (T35) it runs a round of broad, shallow reading of crypto market
news and news about AI trading agents on Perplexity, through W1-C's adapter: software runs every search, stores each
result before the model reads it, and receipts every call. From a round the Leader may

- note the headlines it read (each citing one stored source),
- ask Research for a deep dive,
- wake the Trader with one note,
- route a strategy-level idea to the Trading desk (W12-A, D280: it decides what to test in its strategy lab), or
  to Research or Optimisation; any idea is tested on the practice range before any use,

and it names its next check-in (D123: no time means the next day start; nothing limits how soon, D116).

It never places, approves or blocks a trade. Nothing in ``LeaderNewsReply`` could: every request is a wake note,
which is data and grants nothing (D121, D192), and the system does not rewrite itself while it runs. Findings with
counterevidence are Research's work; the Leader leaves ``findings`` empty and asks for a deep dive instead.

This module imports no adapter, library or model code.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from trade_graph.contracts.alarms import REFERENCE_PATTERN, SYMBOL_PATTERN
from trade_graph.contracts.models import ContractModel
from trade_graph.contracts.wakes import MAX_NOTE_LENGTH, MAX_WAKE_REQUESTS, CheckInRequest
from trade_graph.contracts.web_research import WebFinding, WebResearchReply

LEADER_BRIEF_VERSION = "leader-brief-v1"

RoundStatus = Literal["started", "completed", "switched_off", "disabled", "paused_at_limit", "rejected", "failed",
                      "uncertain", "interrupted"]
# started: recorded before any paid call. completed: a valid reply was routed. switched_off: the owner's protected
# switch keeps the Leader off Perplexity, nothing was sent. disabled: no owner spend policy or paid calls are off.
# paused_at_limit: the owner's money limit (or the task's allocation) stopped the round; only news rounds pause.
# rejected / failed / uncertain: the reply could not be used, or a call's outcome is unknown; receipts stay.
# interrupted: a restart stopped the round; no second paid round runs for that wake.
ROUND_STATUSES: tuple[str, ...] = ("started", "completed", "switched_off", "disabled", "paused_at_limit", "rejected",
                                   "failed", "uncertain", "interrupted")
RequestKind = Literal["deep_dive", "trader_note", "strategy_idea"]
REQUEST_KINDS: tuple[str, ...] = ("deep_dive", "trader_note", "strategy_idea")
RoutedDepartment = Literal["research", "trader", "optimisation"]
ROUTED_DEPARTMENTS: tuple[str, ...] = ("research", "trader", "optimisation")

# Size bounds of one reply, like a request envelope. They never limit how often the Leader runs a round (D116).
MAX_HEADLINES = 12
MAX_DEEP_DIVES = 8
MAX_STRATEGY_IDEAS = 4
MAX_SOURCES_PER_REQUEST = 3
MAX_ROUTED_REQUESTS = MAX_DEEP_DIVES + 1 + MAX_STRATEGY_IDEAS
MAX_TEXT = 300

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")
CITE = ("Copy one source_ref verbatim from a web_search result returned in this round. Citing anything else rejects "
        "the whole reply.")


def plain(value: str | None) -> str | None:
    """Text a person reads: no control or text-direction characters."""
    if value is not None and _CONTROL.search(value):
        raise ValueError("control or text-direction characters are not allowed")
    return value


SourceRef = Annotated[str, Field(pattern=REFERENCE_PATTERN, description=CITE)]
Symbol = Annotated[str, Field(pattern=SYMBOL_PATTERN, description="A Kraken pair such as SOL/USD.")]


class Headline(ContractModel):
    """One thing the Leader read, in a line, with the stored source it came from."""

    source_ref: SourceRef
    takeaway: str = Field(min_length=1, max_length=280)
    symbols: list[Symbol] = Field(default_factory=list, max_length=5)

    @field_validator("takeaway")
    @classmethod
    def _text(cls, value: str) -> str:
        return plain(value) or value


class DeepDiveRequest(ContractModel):
    """Ask Research to dig deeper. The Leader stays broad; Research goes deep (D121)."""

    question: str = Field(min_length=1, max_length=MAX_TEXT, description="What Research should find out.")
    why: str = Field(min_length=1, max_length=MAX_TEXT, description="Why it matters to the team today.")
    source_refs: list[SourceRef] = Field(min_length=1, max_length=MAX_SOURCES_PER_REQUEST)
    symbols: list[Symbol] = Field(default_factory=list, max_length=5)

    @field_validator("question", "why")
    @classmethod
    def _text(cls, value: str) -> str:
        return plain(value) or value


class TraderNote(ContractModel):
    """Wake the Trader with a note. The Trader reads it as data and decides for itself; it is never an order."""

    note: str = Field(min_length=1, max_length=MAX_TEXT,
                      description="What the Trader should see before its next check-in. It decides what to do.")
    source_refs: list[SourceRef] = Field(min_length=1, max_length=MAX_SOURCES_PER_REQUEST)
    symbols: list[Symbol] = Field(default_factory=list, max_length=5)

    @field_validator("note")
    @classmethod
    def _text(cls, value: str) -> str:
        return plain(value) or value


class StrategyIdea(ContractModel):
    """A strategy-level idea. Strategy ideas go to the Trading desk (``trader``, D280: it owns strategy choice and
    research and decides what to test in its strategy lab); cost and quality ideas to Optimisation; Research may still
    be asked. Any idea goes through the practice range before any use."""

    department: Literal["trader", "research", "optimisation"] = Field(
        description="trader (the Trading desk: strategy ideas for its lab), optimisation (cost and quality) or "
                    "research.")
    idea: str = Field(min_length=1, max_length=MAX_TEXT)
    source_refs: list[SourceRef] = Field(min_length=1, max_length=MAX_SOURCES_PER_REQUEST)

    @field_validator("idea")
    @classmethod
    def _text(cls, value: str) -> str:
        return plain(value) or value


class LeaderNewsReply(WebResearchReply):
    """What the Leader returns from a news round, as a strict provider reply (Perplexity, D124).

    There is no field for orders, approvals, vetoes, plans, pauses, mandates, settings or code: extra fields are
    refused, so a reply that tries is rejected whole. ``next_check_in`` is required: the Leader names its next look.
    """

    summary: str = Field(min_length=1, max_length=1000, description="What you read this round, in plain sentences.")
    findings: list[WebFinding] = Field(
        default_factory=list, max_length=0,
        description="Always an empty list: the Leader skims; Research writes findings when you ask for a deep dive.")
    headlines: list[Headline] = Field(default_factory=list, max_length=MAX_HEADLINES)
    deep_dives: list[DeepDiveRequest] = Field(default_factory=list, max_length=MAX_DEEP_DIVES)
    trader_note: TraderNote | None = Field(
        default=None, description="Only for news the Trader should see before its next check-in; null otherwise.")
    strategy_ideas: list[StrategyIdea] = Field(default_factory=list, max_length=MAX_STRATEGY_IDEAS)
    next_check_in: CheckInRequest = Field(
        description="When you want your next round. at_utc null means the next day start; nothing limits how soon.")

    @field_validator("summary")
    @classmethod
    def _text(cls, value: str) -> str:
        return plain(value) or value

    def cited(self) -> frozenset[str]:
        """Every source this reply cites: each must be stored for this round."""
        refs = {headline.source_ref for headline in self.headlines}
        for dive in self.deep_dives:
            refs.update(dive.source_refs)
        if self.trader_note is not None:
            refs.update(self.trader_note.source_refs)
        for idea in self.strategy_ideas:
            refs.update(idea.source_refs)
        refs.update(finding.source_ref for finding in self.findings)
        return frozenset(refs)


def compose_note(head: str, source_refs: Sequence[str], body: str) -> str:
    """A wake note that always fits the wake contract: the request id first, then its sources, then the text.

    The full text and every source stay in the stored request; a note that would run long is cut with an ellipsis,
    and references that would crowd the text out are left to the stored request.
    """
    prefix = f"{head} (sources {', '.join(source_refs)}): " if source_refs else f"{head}: "
    if MAX_NOTE_LENGTH - len(prefix) < 120:
        prefix = f"{head}: "
    room = MAX_NOTE_LENGTH - len(prefix)
    text = " ".join(body.split())
    if len(text) > room:
        text = text[:room - 1].rstrip() + "…"
    return prefix + text


# --- what software records --------------------------------------------------------------------------------------

class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BriefWake(_Frozen):
    wake_id: str
    purpose: str
    reasons: tuple[str, ...]
    trading_day_start: AwareDatetime
    trading_day_end: AwareDatetime
    created_at: AwareDatetime


class BriefAsk(_Frozen):
    """A note another department or software left for the Leader (a wake request, a news alarm). Data only."""

    kind: str
    requested_by: str | None
    symbol: str | None
    note: str
    occurred_at: AwareDatetime


class BriefFocus(_Frozen):
    symbol: str
    note: str


class BriefWakeSummary(_Frozen):
    department: str
    purpose: str
    status: str
    reasons: tuple[str, ...]
    summary: str | None
    created_at: AwareDatetime


class BriefLastRound(_Frozen):
    round_id: str
    finished_at: AwareDatetime
    summary: str


class LeaderBrief(_Frozen):
    """What the Leader is handed for a round: its wake, the notes left for it and how the departments stand.

    Persisted with the task's snapshot and the round, so what the Leader saw is kept as it was (AGENTS.md).
    """

    version: Literal["leader-brief-v1"] = LEADER_BRIEF_VERSION
    wake: BriefWake
    asked: tuple[BriefAsk, ...] = ()
    focus: tuple[BriefFocus, ...] = ()
    plans: tuple[str, ...] = ()
    check_ins: dict[str, dict[str, Any]] = Field(default_factory=dict)
    today: tuple[BriefWakeSummary, ...] = ()
    awake_departments: tuple[str, ...] = ()
    last_round: BriefLastRound | None = None
    instruction: str = Field(min_length=1, max_length=1000)


class RoutedRequest(_Frozen):
    """A request a round sent through the event engine as a wake request (``trigger_source_ref`` is its trigger)."""

    request_id: str
    round_id: str
    kind: RequestKind
    department: RoutedDepartment
    asked_department: RoutedDepartment
    position: int = Field(ge=0, lt=MAX_WAKE_REQUESTS)
    trigger_source_ref: str
    note: str = Field(min_length=1, max_length=MAX_NOTE_LENGTH)
    text: str
    why: str | None = None
    source_refs: tuple[str, ...]
    symbols: tuple[str, ...] = ()
    practice_range_required: bool = False
    created_at: AwareDatetime


class LeaderRound(_Frozen):
    """One news round as saved: its wake, what the Leader was handed, its receipts, sources and what it routed."""

    round_id: str
    portfolio_id: str
    task_id: str
    wake_id: str
    trading_day_start: AwareDatetime
    status: RoundStatus
    run_id: str | None
    reason: str | None
    brief: dict[str, Any]
    reply: dict[str, Any] | None
    summary: str | None
    check_in: CheckInRequest | None
    billing: Literal["resolved", "unresolved", "none"]
    receipt_ids: tuple[str, ...]
    unresolved_reservations: tuple[str, ...]
    source_refs: tuple[str, ...]
    requests: tuple[RoutedRequest, ...]
    started_at: AwareDatetime
    finished_at: AwareDatetime | None


__all__ = [
    "CITE", "LEADER_BRIEF_VERSION", "MAX_DEEP_DIVES", "MAX_HEADLINES", "MAX_ROUTED_REQUESTS",
    "MAX_SOURCES_PER_REQUEST", "MAX_STRATEGY_IDEAS", "REQUEST_KINDS", "ROUND_STATUSES", "ROUTED_DEPARTMENTS",
    "BriefAsk", "BriefFocus", "BriefLastRound", "BriefWake", "BriefWakeSummary", "DeepDiveRequest", "Headline",
    "LeaderBrief", "LeaderNewsReply", "LeaderRound", "RequestKind", "RoundStatus", "RoutedDepartment",
    "RoutedRequest", "StrategyIdea", "TraderNote", "compose_note", "plain",
]
