"""The event engine's contracts: wakes, check-ins and the day's focus step (T35; D115, D116, D123, D138 items 6, 8).

Departments sleep until something wakes them. Software records why, as **triggers**: a price alarm fired, a day-plan
entry or protective exit filled, the daily loss limit stopped new buying, the Leader asked, the department's own
check-in time arrived, or the daily cycle reached a step (day start: scanner, Research focus coins, Trader day
plans; day end: the Learning review). The dispatcher coalesces a department's pending triggers into one **wake**:
triggers that arrive while the department is awake wait and arrive together at its next wake. Coalescing batches,
it never drops: every trigger ends in exactly one delivered wake, and only the day-bound steps of a trading day that
ended unvisited expire, which is recorded too. Nothing here limits how many triggers or wakes there are (D116).

A wake hands the department a ``WakeContext`` (``task["input"]["wake"]``). The department answers through its
handler's output under ``"wake_outcome"`` with a ``WakeOutcome``: when it wants its next check-in (none named means
the next day start, D123), Research's focus coins, the Trader's day plans and position actions (close a day-plan
position or move its safety exit, W8-B) and requests to wake another department with a note. The engine applies
the outcome after the department's result is saved, and day plans go through ``DayPlans.install`` like any plan.
``TraderWakeReply`` and ``ResearchFocusReply`` are the same outcome in the shape of a strict provider reply.

Everything is data: a trigger's note, a focus note or a wake request never carries authority. This module imports no
adapter, library or model code; money and quantities are exact decimals.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from trade_graph.contracts.alarms import REFERENCE_PATTERN, SYMBOL_PATTERN, AlarmDelivery
from trade_graph.contracts.day_plans import DayPlan, DayPlanPosition, PositionAction, PositionActionKind
from trade_graph.contracts.strategy_lab import AppliedLabRequest, LabRequest
from trade_graph.domain.money import canonical_decimal, parse_decimal

WAKE_CONTRACT_VERSION = "wake-v1"
Department = Literal["trader", "research", "leader", "learning", "optimisation"]
DEPARTMENTS: tuple[Department, ...] = ("trader", "research", "leader", "learning", "optimisation")

TriggerKind = Literal[
    "day_start",           # the day start reached a department whose next check-in is the day start (D123)
    "focus_step",          # day start: Research names the day's focus coins from the scanner's shortlist
    "day_plan_step",       # day start, after the focus step: the Trader writes its day plans
    "day_end_review",      # day end: Learning reviews the trading day that ended
    "check_in",            # the department's own named check-in time arrived
    "alarm_fired",         # one of the department's price alarms fired (T33's delivery queue)
    "feed_lost",           # a price feed an armed alarm depends on stopped (T33)
    "feed_restored",       # that feed came back (T33); it rides with the next wake and wakes nobody by itself
    "entry_filled",        # a day-plan entry filled, whole or in part (T34)
    "exit_filled",         # a protective exit filled: its stop or its take-profit (T34)
    "order_filled",        # any other order of the portfolio filled
    "loss_limit_reached",  # the daily loss limit stopped new buying until the next day start (T32)
    "focus_updated",       # Research recorded a new revision of the day's focus coins during the day
    "wake_request",        # another department, normally the Leader, asked for this one with a note (D121)
    "news_alarm",          # a news alarm (T45, W7-A): the engine accepts it like any software trigger
    "lab_results",         # the strategy lab finished the requests of one of the desk's answers (W11-B, D291)
    "task_created",        # a department task made outside the engine (Secretary routing, an owner request)
]
TRIGGER_KINDS: tuple[TriggerKind, ...] = (
    "day_start", "focus_step", "day_plan_step", "day_end_review", "check_in", "alarm_fired", "feed_lost",
    "feed_restored", "entry_filled", "exit_filled", "order_filled", "loss_limit_reached", "focus_updated",
    "wake_request", "news_alarm", "lab_results", "task_created")
# Steps of one trading day: when that day ended before the department saw them, they expire (recorded, not lost).
DAY_BOUND_KINDS: frozenset[str] = frozenset({"day_start", "focus_step", "day_plan_step"})
# Kinds that join a wake but never start one by themselves.
RIDE_ALONG_KINDS: frozenset[str] = frozenset({"feed_restored"})
# Kinds software sources outside the engine may submit (``EventEngine.notify``).
SOFTWARE_KINDS: frozenset[str] = frozenset({"news_alarm"})
# A wake made only of these keeps the department's check-in in force when its answer names none (T45, D228): a news
# alarm only adds wake-ups and never moves a department's own next look; lab results do the same (W11-B, D291).
CHECK_IN_KEEPING_KINDS: frozenset[str] = frozenset({"news_alarm", "lab_results"}) | RIDE_ALONG_KINDS

WakePurpose = Literal["focus", "day_plan", "day_end_review", "event", "check_in", "task"]
WAKE_PURPOSES: tuple[WakePurpose, ...] = ("focus", "day_plan", "day_end_review", "event", "check_in", "task")
WakeStatus = Literal["queued", "applying", "completed", "failed", "obsolete"]
WAKE_STATUSES: tuple[WakeStatus, ...] = ("queued", "applying", "completed", "failed", "obsolete")
WakeOrigin = Literal["engine", "task"]
TriggerStatus = Literal["pending", "claimed", "delivered", "expired"]
TRIGGER_STATUSES: tuple[TriggerStatus, ...] = ("pending", "claimed", "delivered", "expired")
CheckInBasis = Literal["named", "day_start"]
CycleStep = Literal["day_start", "scan", "focus", "day_plan", "day_end_review"]
CYCLE_STEPS: tuple[CycleStep, ...] = ("day_start", "scan", "focus", "day_plan", "day_end_review")
CycleStatus = Literal["started", "done", "failed", "skipped"]
ExitLeg = Literal["stop", "take_profit", "unknown"]

MAX_NOTE_LENGTH = 500
MAX_CHECK_IN_NOTE = 280
MAX_SUMMARY_LENGTH = 1000
# Size bounds of one document, like a request envelope; they never limit how often anything happens (D116).
MAX_FOCUS_COINS = 16
MAX_DAY_PLANS = 16
MAX_WAKE_REQUESTS = 16
MAX_POSITION_ACTIONS = 16
MAX_POSITIONS_SHOWN = 16
MAX_EVIDENCE_REFS = 20
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")


def _plain(value: str | None) -> str | None:
    if value is not None and _CONTROL.search(value):
        raise ValueError("control or text-direction characters are not allowed")
    return value


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return parse_decimal(value)
    except (ValueError, ArithmeticError) as error:
        raise ValueError(f"an exact decimal is required, as text or an integer, never a float ({error})") from None


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- triggers ------------------------------------------------------------------------------------------------------

_TRIGGER_NEEDS: dict[str, tuple[str, ...]] = {
    "day_start": ("trading_day_start",),
    "focus_step": ("trading_day_start",),
    "day_plan_step": ("trading_day_start",),
    "day_end_review": ("trading_day_start",),
    "check_in": ("check_in_id",),
    "alarm_fired": ("delivery_id", "alarm_id"),
    "feed_lost": ("delivery_id",),
    "feed_restored": ("delivery_id",),
    "entry_filled": ("fill_id", "intent_id", "plan_id", "entry_id", "symbol", "quantity", "price"),
    "exit_filled": ("fill_id", "intent_id", "plan_id", "entry_id", "symbol", "quantity", "price", "exit_leg"),
    "order_filled": ("fill_id", "intent_id", "symbol", "quantity", "price"),
    "loss_limit_reached": ("event_id",),
    "focus_updated": ("focus_id",),
    "wake_request": ("requested_by", "note"),
    "news_alarm": ("note",),
    "lab_results": ("note",),
    "task_created": ("task_id",),
}


class WakeTrigger(_Frozen):
    """One reason to wake one department, as software recorded it. Flat and typed; unused fields are empty.

    ``source_ref`` is the trigger's identity in the source (a fill, an alarm delivery, a check-in, a day step); the
    store keeps one trigger per ``source_ref``, so a restart or a repeated scan never records an event twice.
    """

    trigger_id: str = Field(pattern=REFERENCE_PATTERN)
    kind: TriggerKind
    department: Department
    portfolio_id: str = Field(pattern=REFERENCE_PATTERN)
    source_ref: str = Field(min_length=1, max_length=200)
    occurred_at: AwareDatetime
    trading_day_start: AwareDatetime | None = None
    symbol: str | None = Field(default=None, pattern=SYMBOL_PATTERN)
    alarm_id: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    delivery_id: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    fill_id: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    intent_id: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    plan_id: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    entry_id: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    exit_leg: ExitLeg | None = None
    side: Literal["buy", "sell"] | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    check_in_id: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    focus_id: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    scan_id: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    event_id: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    task_id: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    objective: str | None = Field(default=None, max_length=200)
    requested_by: Department | Literal["software"] | None = None
    note: str | None = Field(default=None, min_length=1, max_length=MAX_NOTE_LENGTH)

    @field_validator("quantity", "price", mode="before")
    @classmethod
    def _exact(cls, value: Any) -> Decimal | None:
        return _decimal(value)

    @field_serializer("quantity", "price")
    def _serialize(self, value: Decimal | None) -> str | None:
        return None if value is None else canonical_decimal(value)

    @field_validator("note", "objective")
    @classmethod
    def _text(cls, value: str | None) -> str | None:
        return _plain(value)

    @model_validator(mode="after")
    def _complete(self) -> WakeTrigger:
        missing = [name for name in _TRIGGER_NEEDS[self.kind] if getattr(self, name) is None]
        if missing:
            raise ValueError(f"a {self.kind} trigger names {', '.join(missing)}")
        if self.kind == "wake_request" and self.requested_by == self.department:
            raise ValueError("a department names its own next time as a check-in, not as a wake request")
        return self


# --- allowance -----------------------------------------------------------------------------------------------------

class ProviderAllowance(_Frozen):
    provider: str = Field(min_length=1, max_length=64)
    ai_paused: bool
    reason: str | None = Field(default=None, max_length=200)
    updated_at: str | None = Field(default=None, max_length=40)


class AllowanceWindow(_Frozen):
    window: str = Field(min_length=1, max_length=40)
    remaining_percent: Decimal | None = None
    resets_at: int | None = None

    @field_validator("remaining_percent", mode="before")
    @classmethod
    def _exact(cls, value: Any) -> Decimal | None:
        return _decimal(value)

    @field_serializer("remaining_percent")
    def _serialize(self, value: Decimal | None) -> str | None:
        return None if value is None else canonical_decimal(value)


class AllowanceReading(_Frozen):
    """The AI allowance as software last saw it when the wake was made (D116: HQ shows it per wake).

    ``unknown`` means no reading was recorded; it is never shown as plenty. ``paused`` means new AI work waits
    (the subscription-allowance floor, D98, D138 item 8) while management, protection and records continue.
    """

    read_at: AwareDatetime
    status: Literal["available", "paused", "unknown"]
    ai_paused: bool | None
    lowest_remaining_percent: Decimal | None = None
    observed_at: AwareDatetime | None = None
    providers: tuple[ProviderAllowance, ...] = ()
    windows: tuple[AllowanceWindow, ...] = ()
    note: str = Field(min_length=1, max_length=300)

    @field_validator("lowest_remaining_percent", mode="before")
    @classmethod
    def _exact(cls, value: Any) -> Decimal | None:
        return _decimal(value)

    @field_serializer("lowest_remaining_percent")
    def _serialize(self, value: Decimal | None) -> str | None:
        return None if value is None else canonical_decimal(value)


# --- check-ins -----------------------------------------------------------------------------------------------------

class CheckInRequest(_Frozen):
    """When a department wants its next look. ``at_utc`` None means the next day start (D123)."""

    at_utc: AwareDatetime | None = None
    note: str | None = Field(default=None, min_length=1, max_length=MAX_CHECK_IN_NOTE)

    @field_validator("note")
    @classmethod
    def _text(cls, value: str | None) -> str | None:
        return _plain(value)


class CheckIn(_Frozen):
    """A department's next check-in as recorded. The newest record of a department is the one in force."""

    check_in_id: str = Field(pattern=REFERENCE_PATTERN)
    portfolio_id: str
    department: Department
    due_at: AwareDatetime
    basis: CheckInBasis
    note: str | None = None
    wake_id: str | None = None
    task_id: str | None = None
    created_at: AwareDatetime


# --- the scanner's shortlist and the focus step --------------------------------------------------------------------

class ShortlistCoin(_Frozen):
    symbol: str = Field(pattern=SYMBOL_PATTERN)
    position: int = Field(ge=1)
    flags: tuple[str, ...] = ()


class ScanReference(_Frozen):
    """The scan the day start ran or found: the shortlist Research reviews (T31). ``none`` when there is no scan."""

    status: Literal["ok", "no_candidates", "failed", "none"]
    source: Literal["day_start_scan", "latest_recorded", "none"]
    scan_id: str | None = None
    as_of: AwareDatetime | None = None
    ranking_version: str | None = None
    evidence: str | None = None
    optimistic: bool | None = None
    shortlist: tuple[ShortlistCoin, ...] = ()
    error: str | None = Field(default=None, max_length=500)

    def symbols(self) -> tuple[str, ...]:
        return tuple(coin.symbol for coin in self.shortlist)


class FocusCoin(_Frozen):
    """One coin Research names for today, with a short reason and the evidence it read."""

    symbol: str = Field(pattern=SYMBOL_PATTERN)
    note: str = Field(min_length=1, max_length=MAX_NOTE_LENGTH)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=MAX_EVIDENCE_REFS)

    @field_validator("note")
    @classmethod
    def _text(cls, value: str) -> str:
        return _plain(value) or value

    @field_validator("evidence_refs")
    @classmethod
    def _references(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(re.fullmatch(REFERENCE_PATTERN, item) is None for item in value):
            raise ValueError("evidence references are 1 to 128 letters, digits or ._:/- characters")
        return value


class FocusSelection(_Frozen):
    """Research's focus coins for the day, chosen from the scanner's shortlist (T37 runs it on Perplexity).

    No coins is a valid answer: Research may find nothing worth a plan today. The engine accepts only coins on the
    shortlist the wake handed over; it never ranks or second-guesses the choice.
    """

    coins: tuple[FocusCoin, ...] = Field(default=(), max_length=MAX_FOCUS_COINS)
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_LENGTH)

    @field_validator("summary")
    @classmethod
    def _text(cls, value: str) -> str:
        return _plain(value) or value

    @model_validator(mode="after")
    def _once_each(self) -> FocusSelection:
        symbols = [coin.symbol for coin in self.coins]
        if len(symbols) != len(set(symbols)):
            raise ValueError("each focus coin is named once")
        return self

    def symbols(self) -> tuple[str, ...]:
        return tuple(coin.symbol for coin in self.coins)


class FocusStepState(_Frozen):
    """How the day's focus step ended, as the Trader and Learning are handed it (T37, D208).

    ``failed`` with its reason means Research's research failed or its answer was refused: there is no focus, and
    that is not Research choosing nothing (an empty selection is ``done``). The Trader still decides.
    """

    status: CycleStatus
    reason: str | None = Field(default=None, max_length=500)
    focus_id: str | None = None


class FocusRecord(_Frozen):
    """A recorded focus selection. Each revision of a trading day is kept; the newest is the one in force."""

    focus_id: str
    portfolio_id: str
    trading_day_start: AwareDatetime
    trading_day_end: AwareDatetime
    revision: int = Field(ge=1)
    scan_id: str | None
    selection: FocusSelection
    wake_id: str | None
    task_id: str | None
    recorded_at: AwareDatetime


# --- what a department answers -------------------------------------------------------------------------------------

class WakeRequest(_Frozen):
    """Ask software to wake another department, with a note it reads as data (the Leader's wake notes, D121)."""

    department: Department
    note: str = Field(min_length=1, max_length=MAX_NOTE_LENGTH)

    @field_validator("note")
    @classmethod
    def _text(cls, value: str) -> str:
        return _plain(value) or value


class WakeOutcome(_Frozen):
    """What a department's wake turn returns under ``"wake_outcome"`` in its handler output. Every part is optional.

    - ``check_in``: its next check-in; leaving it out means the next day start (D123).
    - ``focus``: Research only, the day's focus coins.
    - ``day_plans``: the Trader only, one plan per coin, installed through ``DayPlans.install``.
    - ``position_actions``: the Trader only, at most one per coin: close a day-plan position now or move its safety
      exit (W8-B, D245), applied through ``DayPlanActions.apply`` before the turn's day plans.
    - ``wake_requests``: wake other departments with a note.
    - ``lab_requests``: the Trader (the Trading desk) only, requests to the strategy lab (W11-B, D290): register a
      strategy version, create a study, tune or confirm. Queued in order, each checked by the grammar and the study
      rules; no cap on how many (D116).
    """

    summary: str | None = Field(default=None, min_length=1, max_length=MAX_SUMMARY_LENGTH)
    check_in: CheckInRequest | None = None
    focus: FocusSelection | None = None
    day_plans: tuple[DayPlan, ...] = Field(default=(), max_length=MAX_DAY_PLANS)
    position_actions: tuple[PositionAction, ...] = Field(default=(), max_length=MAX_POSITION_ACTIONS)
    wake_requests: tuple[WakeRequest, ...] = Field(default=(), max_length=MAX_WAKE_REQUESTS)
    lab_requests: tuple[LabRequest, ...] = ()

    @field_validator("summary")
    @classmethod
    def _text(cls, value: str | None) -> str | None:
        return _plain(value)

    @model_validator(mode="after")
    def _one_plan_per_coin(self) -> WakeOutcome:
        symbols = [plan.symbol for plan in self.day_plans]
        if len(symbols) != len(set(symbols)):
            raise ValueError("one day plan per coin in a turn; a later turn may replace it")
        _one_action_per_coin(self.position_actions)
        return self


def _one_action_per_coin(actions: tuple[PositionAction, ...]) -> None:
    symbols = [action.symbol for action in actions]
    if len(symbols) != len(set(symbols)):
        raise ValueError("one position action per coin in a turn; a later turn may act again")


class TraderWakeReply(_Frozen):
    """The Trader's wake turn as a strict provider reply: day plans (possibly none), position actions (close a
    day-plan position or move its safety exit, W8-B), its next check-in, a summary and, as the Trading desk, its
    requests to the strategy lab (W11-B; no cap on how many, D116)."""

    day_plans: tuple[DayPlan, ...] = Field(default=(), max_length=MAX_DAY_PLANS)
    position_actions: tuple[PositionAction, ...] = Field(default=(), max_length=MAX_POSITION_ACTIONS)
    next_check_in: CheckInRequest | None = None
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_LENGTH)
    # W11-B (D290): kept last and separate, so later desk fields (W12-A) extend the reply without touching it.
    lab_requests: tuple[LabRequest, ...] = ()

    @field_validator("summary")
    @classmethod
    def _text(cls, value: str) -> str:
        return _plain(value) or value

    @model_validator(mode="after")
    def _one_action_per_coin(self) -> TraderWakeReply:
        _one_action_per_coin(self.position_actions)
        return self

    def outcome(self) -> WakeOutcome:
        return WakeOutcome(summary=self.summary, check_in=self.next_check_in, day_plans=self.day_plans,
                           position_actions=self.position_actions, lab_requests=self.lab_requests)


class ResearchFocusReply(_Frozen):
    """Research's focus step as a strict provider reply (W6-A runs it on Perplexity)."""

    focus: FocusSelection
    next_check_in: CheckInRequest | None = None

    def outcome(self) -> WakeOutcome:
        return WakeOutcome(summary=self.focus.summary, check_in=self.next_check_in, focus=self.focus)


# --- what a department receives ------------------------------------------------------------------------------------

class ActivePlan(_Frozen):
    plan_id: str
    symbol: str
    installed_at: str
    trading_day_end: str


class WakeContext(_Frozen):
    """What a woken department is handed in ``task["input"]["wake"]``. It is data: authority stays in software."""

    contract_version: Literal["wake-v1"] = WAKE_CONTRACT_VERSION
    wake_id: str = Field(pattern=REFERENCE_PATTERN)
    portfolio_id: str
    department: Department
    purpose: WakePurpose
    trading_day_start: AwareDatetime
    trading_day_end: AwareDatetime
    created_at: AwareDatetime
    reasons: tuple[TriggerKind, ...]
    triggers: tuple[WakeTrigger, ...]
    alarms: tuple[AlarmDelivery, ...] = ()
    scan: ScanReference | None = None
    focus: FocusRecord | None = None
    focus_step: FocusStepState | None = None
    plans: tuple[ActivePlan, ...] = ()
    # W8-B: the coins' day-plan positions with their exits, so the Trader can close one or move its exit.
    positions: tuple[DayPlanPosition, ...] = Field(default=(), max_length=MAX_POSITIONS_SHOWN)
    check_in: CheckIn | None = None
    allowance: AllowanceReading
    instruction: str = Field(min_length=1, max_length=1000)


# --- what the engine recorded --------------------------------------------------------------------------------------

class AppliedPlan(_Frozen):
    symbol: str
    request_id: str
    status: Literal["installed", "existing", "refused", "obsolete"]
    plan_id: str | None = None
    error_code: str | None = None
    reason: str | None = Field(default=None, max_length=500)


class AppliedAction(_Frozen):
    """What came of one of the Trader's position actions (W8-B): the action's own record holds every step."""

    symbol: str
    action: PositionActionKind
    request_id: str
    status: Literal["done", "refused", "failed", "obsolete"]
    action_id: str | None = None
    error_code: str | None = None
    reason: str | None = Field(default=None, max_length=500)


class WakeOutcomeRecord(_Frozen):
    """What the engine did with a department's answer. ``refused`` says why an answer could not be used."""

    received: bool
    refused: str | None = Field(default=None, max_length=500)
    summary: str | None = None
    check_in_id: str | None = None
    focus_id: str | None = None
    focus_refused: str | None = Field(default=None, max_length=500)
    plans: tuple[AppliedPlan, ...] = ()
    actions: tuple[AppliedAction, ...] = ()
    requested_trigger_ids: tuple[str, ...] = ()
    # W11-B (D290): each lab request of the answer, queued in the strategy lab or refused at once with its reason.
    lab: tuple[AppliedLabRequest, ...] = ()


class WakeRecord(_Frozen):
    """One wake as saved: its department, purpose, reasons and triggers, the allowance reading and what came of it."""

    wake_id: str
    portfolio_id: str
    department: Department
    origin: WakeOrigin
    purpose: WakePurpose
    status: WakeStatus
    trading_day_start: AwareDatetime
    trading_day_end: AwareDatetime
    task_id: str | None
    reasons: tuple[TriggerKind, ...]
    triggers: tuple[WakeTrigger, ...]
    alarm_delivery_ids: tuple[str, ...]
    allowance: AllowanceReading
    created_at: AwareDatetime
    closed_at: AwareDatetime | None
    close_reason: str | None
    outcome: WakeOutcomeRecord | None


def reasons_of(triggers: tuple[WakeTrigger, ...] | list[WakeTrigger]) -> tuple[TriggerKind, ...]:
    """The distinct trigger kinds of a wake, in the contract's order."""
    present = {trigger.kind for trigger in triggers}
    return tuple(kind for kind in TRIGGER_KINDS if kind in present)


def purpose_of(triggers: tuple[WakeTrigger, ...] | list[WakeTrigger]) -> WakePurpose:
    """A wake's purpose: the day's steps first, then events, then check-ins."""
    kinds = {trigger.kind for trigger in triggers}
    if "task_created" in kinds:
        return "task"
    if "day_plan_step" in kinds:
        return "day_plan"
    if "focus_step" in kinds:
        return "focus"
    if "day_end_review" in kinds:
        return "day_end_review"
    if kinds - {"day_start", "check_in"}:
        return "event"
    return "check_in"


def is_obsolete(purpose: str, trading_day_end: datetime, now: datetime) -> bool:
    """A wake whose trading day has ended may no longer act on it; a day-end review looks back by design."""
    return purpose != "day_end_review" and now >= trading_day_end


__all__ = [
    "CHECK_IN_KEEPING_KINDS",
    "CYCLE_STEPS", "DAY_BOUND_KINDS", "DEPARTMENTS", "MAX_DAY_PLANS", "MAX_FOCUS_COINS", "MAX_POSITION_ACTIONS",
    "MAX_POSITIONS_SHOWN", "MAX_WAKE_REQUESTS",
    "RIDE_ALONG_KINDS", "SOFTWARE_KINDS", "TRIGGER_KINDS", "TRIGGER_STATUSES", "WAKE_CONTRACT_VERSION",
    "WAKE_PURPOSES", "WAKE_STATUSES", "ActivePlan", "AllowanceReading", "AllowanceWindow", "AppliedAction",
    "AppliedPlan", "CheckIn",
    "CheckInBasis", "CheckInRequest", "CycleStatus", "CycleStep", "Department", "ExitLeg", "FocusCoin",
    "FocusRecord", "FocusSelection", "FocusStepState", "ProviderAllowance", "ResearchFocusReply", "ScanReference",
    "ShortlistCoin", "TraderWakeReply", "TriggerKind", "TriggerStatus", "WakeContext", "WakeOrigin", "WakeOutcome",
    "WakeOutcomeRecord", "WakePurpose", "WakeRecord", "WakeRequest", "WakeStatus", "WakeTrigger", "is_obsolete",
    "purpose_of", "reasons_of",
]
