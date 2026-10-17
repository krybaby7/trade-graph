"""The HQ wake log document, ``trade-graph.hq.wakes.v1`` (T39, D215-D219).

``GET /api/v1/hq/wakes`` returns one of these; the page (``web/static/hq-wakes.js``) only lays it out. It is served on
its own, never inside the HQ snapshot, so the snapshot's 80 KB budget is unchanged (D215). It holds:

- ``items``: one page of the wake log, newest first: every wake the event engine (T35) recorded for the portfolio,
  with its department, its reasons (the trigger kinds and how many of each), the events in plain words, the AI
  allowance reading saved with it, what came of it and the department's own summary as a labelled quote;
- ``day`` (first page only): today's daily-cycle steps, each department's next check-in, the day's focus coins, the
  alarms that are set and the wake counts of today and the last 7 days.

The wording uses the HQ snapshot's ``Text`` segments (``api/hq_contract.py``), so times read in the viewer's own time
zone and every word comes from the server. Vocabularies are the event engine's (``contracts/wakes.py``). The list
bounds below are document bounds, like a page size: they never limit how many wakes, triggers or alarms there are
(D116); totals and counts are always exact, and a wake lists a few of its events and counts the rest.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Final, Literal

from pydantic import Field, model_validator

from trade_graph.api.hq_contract import (
    ISO,
    AllowedPath,
    Dec,
    Identifier,
    NonEmptyText,
    NonNegative,
    Positive,
    Quote,
    Text,
    _Contract,
)
from trade_graph.api.hq_contract import contract_keys as _contract_keys
from trade_graph.contracts.alarms import SYMBOL_PATTERN
from trade_graph.contracts.wakes import (
    CYCLE_STEPS,
    DEPARTMENTS,
    TRIGGER_KINDS,
    WAKE_STATUSES,
    CheckInBasis,
    CycleStep,
    Department,
    TriggerKind,
    WakeOrigin,
    WakePurpose,
    WakeStatus,
)

SCHEMA: Final = "trade-graph.hq.wakes.v1"
PAGE_DEFAULT: Final = 20  # wakes per page; "Load earlier" continues from ``next_before``
PAGE_MAX: Final = 30
EVENT_LINES_MAX: Final = 6  # events written out per wake; ``more_events`` counts the rest
NOTES_MAX: Final = 3  # notes a wake was handed (wake requests, news alarms, its check-in note), quoted
ALARMS_LISTED_MAX: Final = 20  # alarms written out; ``total`` counts every one that is set
SHORTLIST_MAX: Final = 20
FOCUS_COINS_MAX: Final = 16  # the focus selection's own bound (contracts/wakes.py MAX_FOCUS_COINS)
# The HQ snapshot's budget, reused as this endpoint's own ceiling for its biggest page on a busy synthetic day.
PAYLOAD_BUDGET_BYTES: Final = 80 * 1024
OPEN_STATUSES: Final = ("queued", "applying")

Symbol = Annotated[str, Field(pattern=SYMBOL_PATTERN)]
WakeTone = Literal["good", "neutral", "attention"]
AllowanceStatus = Literal["available", "paused", "unknown"]
CheckInStatus = Literal["planned", "due", "awake"]  # awake: woken now, its answer sets its next time
StepStatus = Literal["not_yet", "started", "done", "failed", "skipped"]
FocusStatus = Literal["named", "none_named", "not_yet", "failed", "skipped"]


class ReasonCount(_Contract):
    kind: TriggerKind
    count: Positive


def _unique_kinds(reasons: list[ReasonCount], where: str) -> None:
    if len({reason.kind for reason in reasons}) != len(reasons):
        raise ValueError(f"{where} name each reason once")


class WakeAllowance(_Contract):
    """The AI allowance reading saved with the wake (D116). ``unknown`` is never shown as plenty."""

    status: AllowanceStatus
    text: NonEmptyText
    remaining_percent: Dec | None  # the lowest remaining percent of that reading, exact as saved
    observed_at: ISO | None


class WakeItem(_Contract):
    id: Identifier
    cursor: Positive  # the wake's place in the log; ``before=<cursor>`` asks for older wakes
    dept: Department
    origin: WakeOrigin
    purpose: WakePurpose
    status: WakeStatus
    tone: WakeTone
    at: ISO
    closed_at: ISO | None
    headline: NonEmptyText
    reasons: Annotated[list[ReasonCount], Field(min_length=1, max_length=len(TRIGGER_KINDS))]
    events: Annotated[list[NonEmptyText], Field(max_length=EVENT_LINES_MAX)]
    more_events: NonNegative
    allowance: WakeAllowance
    result: NonEmptyText
    quote: Quote | None  # the department's own summary of the turn
    notes: Annotated[list[Quote], Field(max_length=NOTES_MAX)]
    link: AllowedPath | None
    technical: str | None

    @model_validator(mode="after")
    def _consistent(self):
        if (self.closed_at is None) != (self.status in OPEN_STATUSES):
            raise ValueError("a closed wake records when it closed, and an open one has not closed")
        _unique_kinds(self.reasons, "a wake's reasons")
        return self


class CheckInView(_Contract):
    """A department's next check-in as the event engine reads it (D123): its own time, or the next day start."""

    dept: Department
    at: ISO
    basis: CheckInBasis
    status: CheckInStatus
    text: NonEmptyText
    quote: Quote | None  # its own note with the time it chose
    set_at: ISO | None  # when it chose; None when it never named a time
    waiting: NonNegative  # recorded reasons that wait for its next wake-up


class StepView(_Contract):
    step: CycleStep
    status: StepStatus
    text: NonEmptyText
    at: ISO | None


class FocusCoinView(_Contract):
    symbol: Symbol
    quote: Quote | None
    evidence: NonNegative  # how many evidence records Research cited


class FocusView(_Contract):
    status: FocusStatus
    text: NonEmptyText
    coins: Annotated[list[FocusCoinView], Field(max_length=FOCUS_COINS_MAX)]
    quote: Quote | None  # Research's own summary
    revision: Positive | None
    recorded_at: ISO | None
    shortlist: Annotated[list[Symbol], Field(max_length=SHORTLIST_MAX)]

    @model_validator(mode="after")
    def _coins(self):
        if (self.status == "named") != bool(self.coins):
            raise ValueError("named focus coins are listed, and only then")
        if (self.revision is None) != (self.status not in ("named", "none_named")):
            raise ValueError("a recorded focus selection has its revision")
        return self


class AlarmView(_Contract):
    id: Identifier
    dept: Department
    symbol: Symbol
    text: NonEmptyText
    armed_at: ISO | None
    quote: Quote | None  # the note its department wrote with it
    in_day_plan: bool


class WaitingAlarms(_Contract):
    dept: Department
    count: Positive


class AlarmsView(_Contract):
    total: NonNegative
    text: NonEmptyText
    items: Annotated[list[AlarmView], Field(max_length=ALARMS_LISTED_MAX)]
    waiting: list[WaitingAlarms]  # alarms that went off and wait for their department's next wake-up

    @model_validator(mode="after")
    def _listed(self):
        if len(self.items) > self.total:
            raise ValueError("the total counts every listed alarm")
        return self


class DeptCount(_Contract):
    dept: Department
    today: NonNegative
    week: NonNegative


class CountsView(_Contract):
    today: NonNegative
    week: NonNegative
    text: NonEmptyText
    departments: Annotated[list[DeptCount], Field(min_length=len(DEPARTMENTS), max_length=len(DEPARTMENTS))]
    reasons_today: list[ReasonCount]

    @model_validator(mode="after")
    def _sums(self):
        if [item.dept for item in self.departments] != list(DEPARTMENTS):
            raise ValueError("counts list the five departments in order")
        if sum(item.today for item in self.departments) != self.today or \
                sum(item.week for item in self.departments) != self.week:
            raise ValueError("department counts add up to the totals")
        _unique_kinds(self.reasons_today, "today's reasons")
        return self


class WakeDay(_Contract):
    start: ISO
    end: ISO
    steps: Annotated[list[StepView], Field(min_length=len(CYCLE_STEPS), max_length=len(CYCLE_STEPS))]
    check_ins: Annotated[list[CheckInView], Field(min_length=len(DEPARTMENTS), max_length=len(DEPARTMENTS))]
    focus: FocusView
    alarms: AlarmsView
    counts: CountsView

    @model_validator(mode="after")
    def _order(self):
        if self.end <= self.start:
            raise ValueError("a trading day ends after it starts")
        if [item.step for item in self.steps] != list(CYCLE_STEPS):
            raise ValueError("steps follow the daily cycle's order")
        if [item.dept for item in self.check_ins] != list(DEPARTMENTS):
            raise ValueError("check-ins list the five departments in order")
        return self


class WakeLog(_Contract):
    schema_: Literal["trade-graph.hq.wakes.v1"] = Field(alias="schema")
    as_of: ISO
    available: bool  # False on a database from before the event engine's records (migration 0035)
    status_text: Text | None
    day: WakeDay | None  # the first page only
    items: Annotated[list[WakeItem], Field(max_length=PAGE_MAX)]
    total: NonNegative
    next_before: Positive | None

    @model_validator(mode="after")
    def _page(self):
        cursors = [item.cursor for item in self.items]
        if cursors != sorted(cursors, reverse=True) or len(set(cursors)) != len(cursors):
            raise ValueError("wakes are newest first, each once")
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("wake ids are unique")
        if self.next_before is not None and (not cursors or self.next_before > cursors[-1]):
            raise ValueError("the next page starts below this one")
        if len(self.items) > self.total:
            raise ValueError("the total counts every listed wake")
        if not self.available and (self.items or self.day is not None or self.total or self.next_before):
            raise ValueError("without wake records there is nothing to list")
        return self


def validate_wake_log(document: Mapping[str, Any]) -> WakeLog:
    """Validate a wake log document (raises ``pydantic.ValidationError``)."""
    return WakeLog.model_validate(document)


def contract_keys() -> set[str]:
    """Every JSON key the document can emit, for redaction-safety checks."""
    return _contract_keys(WakeLog)


__all__ = ["ALARMS_LISTED_MAX", "CYCLE_STEPS", "DEPARTMENTS", "EVENT_LINES_MAX", "NOTES_MAX", "OPEN_STATUSES",
           "PAGE_DEFAULT", "PAGE_MAX", "PAYLOAD_BUDGET_BYTES", "SCHEMA", "SHORTLIST_MAX", "TRIGGER_KINDS",
           "WAKE_STATUSES", "WakeLog", "contract_keys", "validate_wake_log"]
