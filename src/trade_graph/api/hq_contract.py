"""Frozen HQ contracts: the ``trade-graph.hq.v1`` snapshot (spec §B.6) and check-ins (§B.10).

Shared by the read model (``api/hq*.py``), the page scripts (``web/static/hq*.js``)
and the owner check-in store (``api/owner_checkins.py``). Changing anything here needs
the integration owner's agreement.

Projections build plain dicts; tests validate them with :func:`validate_snapshot`.
Models forbid extra keys and use strict types, so a bool never passes for a number
and a number never passes for a string. Conventions:

- ``ISO`` is UTC with millisecond precision, ``YYYY-MM-DDTHH:MM:SS.mmmZ`` (:func:`iso_ms`).
- ``Dec`` is a canonical Decimal string (``domain.money.canonical_decimal``).
- A key typed ``X | None`` is always present; ``null`` means unknown or not applicable,
  never zero (P3). A key listed in ``_omit_not_null`` may be absent but is never null.
- Seconds and counts are integers. Percentages are numbers from 0 to 100.
- ``Text`` is a list of segments. An empty ``Text`` renders nothing (the copy deck's "—").
"""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Annotated, Any, ClassVar, Final, Literal, Protocol, get_args

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from trade_graph.domain.money import canonical_decimal

SCHEMA: Final = "trade-graph.hq.v1"
POLL_SECONDS: Final = 30
CALENDAR_DAYS_DEFAULT: Final = 7
CALENDAR_DAYS_MAX: Final = 14
STORY_WINDOW_HOURS: Final = 72
STORY_MAX_ITEMS: Final = 60
CALENDAR_MAX_ITEMS: Final = 200
SERIES_MAX_PROJECTIONS: Final = 48
# Estimated turns later than this are summarised per schedule in ``calendar.estimates`` (payload budget).
ESTIMATE_ITEMS_HORIZON_HOURS: Final = 48
# Deadlines of one of these kinds that fall within this many seconds of each other are one calendar item with a
# ``count`` and an ``until`` (e.g. several research notes written together expire together).
CALENDAR_GROUPED_KINDS: Final = ("research_expiry", "proposal_expiry", "engineer_deadline")
CALENDAR_GROUP_WINDOW_SECONDS: Final = 7200
QUEST_MAX_ITEMS: Final = 60
UP_NEXT_MAX: Final = 5
TURN_STUCK_MAX_CARDS: Final = 3
QUOTE_MAX_CHARS: Final = 280
QUOTE_LABEL: Final = "In their words"
FLAGGED_MAX_ITEMS: Final = 10
FLAGGED_MAX_CHARS: Final = 200

ISO_MS_PATTERN: Final = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
# Same expression the page scripts use before rendering any link (spec §C.2).
ALLOWED_PATH_PATTERN: Final = (
    r"^/(?:$|journal$|progress$|trading$|organization$|costs$|changes(?:/[^/]+)?$|owner$|"
    r"decisions/[^/]+$|api/v1/review-export$)"
)
CURRENCY_PATTERN: Final = r"^[A-Z0-9]{2,16}$"
IDENTIFIER_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"

# --- Closed vocabularies -----------------------------------------------------------------

DeptId = Literal["trader", "research", "learning", "leader", "optimisation", "engineer", "secretary", "market",
                 "accounts"]
DEPT_IDS: Final = get_args(DeptId)
DeptKind = Literal["ai", "software"]
# ``departments`` always lists all nine rooms in this order: the 3x3 HQ map, row by row (spec §A.4).
DEPARTMENT_ORDER: Final[tuple[tuple[str, str], ...]] = (
    ("leader", "ai"), ("research", "ai"), ("trader", "ai"),
    ("learning", "ai"), ("optimisation", "ai"), ("engineer", "ai"),
    ("secretary", "software"), ("market", "software"), ("accounts", "software"),
)
AI_DEPARTMENTS: Final = tuple(dept for dept, kind in DEPARTMENT_ORDER if kind == "ai")
SOFTWARE_DEPARTMENTS: Final = tuple(dept for dept, kind in DEPARTMENT_ORDER if kind == "software")

Light = Literal["off", "stuck", "working", "waiting", "paused", "on_call", "resting", "ok", "attention"]
LIGHTS: Final = get_args(Light)
AI_ROOM_LIGHTS: Final = ("off", "stuck", "working", "waiting", "paused", "on_call", "resting")
SOFTWARE_ROOM_LIGHTS: Final = ("off", "ok", "attention")
Tone = Literal["calm", "busy", "resting", "attention", "offline", "stuck"]
TONES: Final = get_args(Tone)

DecisionId = Literal["start_trading", "pause_ai", "resume", "start_optimisation", "stop_safely", "record_bill",
                     "declare_bills_complete", "plan_checkin"]
DECISION_IDS: Final = get_args(DecisionId)

NowCode = Literal["no_run", "crashed", "stopped", "attached_unknown", "silent", "stopping", "safety_stop",
                  "owner_stop_setting", "owner_pause", "leader_pause", "model_unavailable", "quota_paused",
                  "timetable_failing", "routine_failing", "orders_unclear", "waiting_allowance", "working",
                  "starting_turn", "probably_low_allowance", "calm"]
NOW_CODES: Final = get_args(NowCode)
# Spec §B.5.2, first match wins in this order: code -> (tone, light, primary_decision).
NOW_PRESENTATION: Final[Mapping[str, tuple[str, str, str | None]]] = {
    "no_run": ("offline", "off", "start_trading"),
    "crashed": ("offline", "off", "start_trading"),
    "stopped": ("offline", "off", "start_trading"),
    "attached_unknown": ("attention", "off", None),
    "silent": ("attention", "off", None),
    "stopping": ("resting", "paused", None),
    "safety_stop": ("stuck", "stuck", None),
    "owner_stop_setting": ("resting", "paused", "resume"),
    "owner_pause": ("resting", "paused", "resume"),
    "leader_pause": ("resting", "paused", None),
    "model_unavailable": ("attention", "paused", None),
    "quota_paused": ("resting", "paused", None),
    "timetable_failing": ("attention", "stuck", None),
    "routine_failing": ("attention", "waiting", None),
    "orders_unclear": ("attention", "waiting", None),
    "waiting_allowance": ("resting", "waiting", None),
    "working": ("busy", "working", None),
    "starting_turn": ("busy", "waiting", None),
    "probably_low_allowance": ("resting", "waiting", None),
    "calm": ("calm", "resting", None),
}
# Every AI room light is ``off`` for these codes (spec §B.5.5).
OFFLINE_NOW_CODES: Final = ("no_run", "crashed", "stopped", "attached_unknown", "silent")

ServiceStatus = Literal["IDLE", "UNOBSERVED", "STARTING", "RUNNING", "MANAGEMENT_ONLY", "STOPPING", "STOPPED",
                        "FAILED", "INTERRUPTED", "ATTACHED_EXISTING"]
SERVICE_STATUSES: Final = get_args(ServiceStatus)
ACTIVE_SERVICE_STATUSES: Final = ("STARTING", "RUNNING", "MANAGEMENT_ONLY", "STOPPING")  # service_controller.py:28
ProblemPrefix = Literal["feed:", "reconciliation:", "pause:", "dispatch:", "scheduler:", "other"]
PROBLEM_PREFIXES: Final = get_args(ProblemPrefix)

PauseProfile = Literal["RUNNING", "PAUSE_DECISIONS", "NO_NEW_EXPOSURE", "MANAGE_ONLY", "CANCEL_ALL", "FLATTEN",
                       "STOPPED"]
PAUSE_PROFILES: Final = get_args(PauseProfile)
PAUSED_WORK_PROFILES: Final = ("PAUSE_DECISIONS", "MANAGE_ONLY", "CANCEL_ALL", "FLATTEN", "STOPPED")
PauseOriginator = Literal["owner", "system", "leader"]

AiState = Literal["paused_model", "paused_quota", "waiting_for_allowance", "probably_low", "ok_last_reading",
                  "unknown"]
AI_STATES: Final = get_args(AiState)
AllowanceStatus = Literal["ok", "near_line", "below_line", "unknown"]
# The reserve rule ("stop line"): read and on, read and off, or not readable (never shown as "none").
StopLineState = Literal["on", "off", "unknown"]
# A department's next turn: not overdue; overdue within the 120 s grace with nothing in the way; overdue
# while a known gate holds; overdue for a reason HQ can't see (or a passed Engineer deadline).
NextStatus = Literal["planned", "due_now", "waiting", "overdue"]
NEXT_STATUSES: Final = get_args(NextStatus)
WindowLabel = Literal["weekly", "5-hour", "other"]

AttentionCode = Literal["team_off", "team_silent", "safety_stop", "model_unavailable", "timetable_failing",
                        "turn_stuck", "checkin_due", "orders_unclear", "routine_failing", "repeat_problems",
                        "notes_expire_early", "prices_old", "allowance_near", "command_interrupted", "resources",
                        "owner_paused", "costs_unconfirmed", "trial_rollback_likely"]
ATTENTION_CODES: Final = get_args(AttentionCode)
Severity = Literal["act", "watch", "info"]
SEVERITY_ORDER: Final = ("act", "watch", "info")
# Spec §B.5.7. An attention ``id`` is the code, or ``"<code>:<suffix>"`` when one code yields
# several cards (``turn_stuck:<task_id>``).
ATTENTION_SEVERITY: Final[Mapping[str, str]] = {
    "team_off": "act", "team_silent": "act", "safety_stop": "act", "model_unavailable": "act",
    "timetable_failing": "act", "turn_stuck": "act", "checkin_due": "act",
    "orders_unclear": "watch", "routine_failing": "watch", "repeat_problems": "watch",
    "notes_expire_early": "watch", "prices_old": "watch", "allowance_near": "watch",
    "command_interrupted": "watch", "resources": "watch",
    "owner_paused": "info", "costs_unconfirmed": "info", "trial_rollback_likely": "info",
}

LastOutcome = Literal["done", "problem", "skipped", "stuck", "decided"]
QuestOrigin = Literal["schedule", "secretary", "followup", "owner", "commission", "leader", "improvement"]
CalendarKind = Literal["dept_turn", "extra_review", "owner_checkin", "research_expiry", "allowance_refill",
                       "optimisation_deadline", "trial_end", "proposal_expiry", "engineer_deadline", "mandate_expiry"]
CALENDAR_KINDS: Final = get_args(CalendarKind)
CalendarStatus = Literal["planned", "estimated", "waiting", "due", "overdue", "done", "problem", "skipped", "stuck",
                         "missed", "cancelled"]
CALENDAR_STATUSES: Final = get_args(CalendarStatus)
StoryKind = Literal["trader_decision", "fill", "turn_done", "turn_problem", "turn_skipped", "allowance_wait",
                    "ai_paused", "team_started", "team_stopped", "team_crashed", "routine_problem", "prices_problem",
                    "history_problem", "safety_stop", "owner_action", "improvement", "account_opened"]
STORY_KINDS: Final = get_args(StoryKind)
StoryTone = Literal["good", "neutral", "attention"]

EstimateNoteCode = Literal["euro_rate_from_earlier_day", "euro_rate_too_old", "prices_old", "no_prices"]
PaysStatus = Literal["covered", "not_covered", "unknown"]
# Spec §B.2 / §D.11, in the order live.py appends the underlying reasons; ``other`` catches the rest.
EconCode = Literal["subscription_bills_unconfirmed", "other_costs_unconfirmed", "bill_matches_existing_records",
                   "bill_euro_rate_missing", "bill_euro_rate_old", "shared_between_accounts", "valuation_not_final",
                   "ai_usage_records_incomplete", "other_cost_value_unknown", "invoice_differences", "history_gap",
                   "other"]
ECON_CODES: Final = get_args(EconCode)
NoticeCode = Literal["history_gap", "history_unverified", "resources", "read_only", "live_mode"]
MarketSymbol = Literal["BTC/USD", "ETH/USD"]
Trend = Literal["below", "above", "level", "not_enough_history"]
TimeStyle = Literal["relative", "clock", "day_clock", "relative_clock", "date"]
# Keys of ``GLOSSARY`` in hq-glossary.js (spec §D.12).
GlossaryKey = Literal["practice_money", "real_trading_off", "protect_only", "safety_stop", "allowance", "stop_line",
                      "turn", "timetable", "research_note", "wait", "double_check", "unclear_order", "estimate",
                      "euro_rate", "currency_effect", "team_effect", "pays_for_itself", "bill_share",
                      "bills_complete", "history_gap", "avg_20h", "avg_50h", "trial", "rules", "sign_of_life",
                      "review_checkin", "review_prompt"]
GLOSSARY_KEYS: Final = get_args(GlossaryKey)

# --- Owner check-ins (spec §B.10) ----------------------------------------------------------

CheckinAction = Literal["plan", "reschedule", "done", "skip", "cancel"]
CHECKIN_ACTIONS: Final = get_args(CheckinAction)
CheckinPromptKind = Literal["baseline", "interval", "bedtime", "next_day", "custom"]
CHECKIN_PROMPT_KINDS: Final = get_args(CheckinPromptKind)
CheckinState = Literal["planned", "due", "overdue", "missed", "done", "skipped", "cancelled"]
CHECKIN_STATES: Final = get_args(CheckinState)
CHECKIN_TERMINAL_STATES: Final = ("done", "skipped", "cancelled")
CHECKIN_TITLE_MAX_CHARS: Final = 80
CHECKIN_NOTE_MAX_CHARS: Final = 500
CHECKIN_BODY_MAX_BYTES: Final = 4096
CHECKIN_WINDOW_MAX_ITEMS: Final = 200
CHECKIN_QUERY_MAX_DAYS: Final = 120
CHECKIN_QUERY_DEFAULT_PAST_DAYS: Final = 7
CHECKIN_QUERY_DEFAULT_FUTURE_DAYS: Final = 30
CHECKIN_PLAN_PAST_DAYS: Final = 1    # plan/reschedule: now - 1 d <= planned_at <= now + 60 d
CHECKIN_PLAN_FUTURE_DAYS: Final = 60
# Derived non-terminal state, relative to planned_at: planned before -15 min, due until +60 min,
# overdue until +6 h, missed after that.
CHECKIN_DUE_BEFORE_SECONDS: Final = 15 * 60
CHECKIN_DUE_AFTER_SECONDS: Final = 60 * 60
CHECKIN_MISSED_AFTER_SECONDS: Final = 6 * 60 * 60


class OwnerCheckins(Protocol):
    """Interface of the ``trade_graph.api.owner_checkins`` module (WP3, spec §B.10).

    The read model imports it lazily so HQ works without migration 0025::

        try:
            from trade_graph.api import owner_checkins
        except ImportError:
            owner_checkins = None
        available = owner_checkins is not None and owner_checkins.available(runtime)

    When unavailable, the owner's snapshot has ``checkins.available == False``, no items
    and no ``checkin_due`` card. Every timestamp returned is ``ISO`` (:func:`iso_ms`).
    """

    def available(self, runtime: Any) -> bool:
        """True when the ``owner_checkins`` table exists (a ``sqlite_master`` lookup)."""

    def window(self, runtime: Any, start: str, end: str) -> list[dict[str, Any]]:
        """``Checkin`` dicts with ``start <= planned_at < end``, at most 200, ordered by ``planned_at``.

        ``start``/``end`` are timezone-aware ISO-8601 strings. Non-terminal states are
        derived from ``runtime.clock.now()``.
        """

    def last_done_at(self, runtime: Any) -> str | None:
        """``done_at`` of the most recent ``done`` check-in, or None."""

    def record(self, runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
        """Validate and append one ``CheckinCommand``; returns ``{"checkin": Checkin, "replayed": bool}``."""

    def register(self, app: Any, runtime: Any, identity: Any, owner_write: Any) -> None:
        """Add ``GET`` and ``POST /api/v1/owner/checkins``."""


# --- Scalars ---------------------------------------------------------------------------


def iso_ms(value: datetime | str) -> str:
    """Render an aware instant as ``ISO`` (UTC, milliseconds truncated, ``Z``)."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    value = value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


_CURRENCY_PREFIX: Final = {"EUR": "€", "USD": "$", "GBP": "£"}
MINUS: Final = chr(0x2212)  # display minus sign for negative money


def money_display(amount: str, currency: str) -> str:
    """Spec §B.6 money rules: half-even to cents, grouped, ``€``/``$``/``£`` prefix, U+2212 minus."""
    quantized = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    number = f"{abs(quantized):,.2f}"
    sign = MINUS if quantized < 0 else ""
    prefix = _CURRENCY_PREFIX.get(currency)
    return f"{sign}{prefix}{number}" if prefix else f"{sign}{number} {currency}"


def _real_instant(value: str) -> str:
    datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    return value


def _instant(value: str) -> datetime:
    """An already validated ``ISO`` string as an aware datetime."""
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def _canonical(value: str) -> str:
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("expected a canonical decimal string") from exc
    if not number.is_finite() or canonical_decimal(number) != value:
        raise ValueError("expected a canonical decimal string")
    return value


ISO = Annotated[str, Field(pattern=ISO_MS_PATTERN), AfterValidator(_real_instant)]
Dec = Annotated[str, Field(max_length=64), AfterValidator(_canonical)]
AllowedPath = Annotated[str, Field(pattern=ALLOWED_PATH_PATTERN)]
Currency = Annotated[str, Field(pattern=CURRENCY_PATTERN)]
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=IDENTIFIER_PATTERN)]
NonNegative = Annotated[int, Field(ge=0)]
Positive = Annotated[int, Field(gt=0)]
Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Name = Annotated[str, Field(min_length=1)]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    _omit_not_null: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def _omitted_keys_are_never_null(cls, data: Any) -> Any:
        if isinstance(data, Mapping):
            for key in cls._omit_not_null:
                if key in data and data[key] is None:
                    raise ValueError(f"{key!r} may be omitted but is never null")
        return data


def _check_display(amount: str, currency: str, display: str) -> None:
    if display != money_display(amount, currency):
        raise ValueError("money display does not follow the contract's money rules")


# --- Text ------------------------------------------------------------------------------


class TextSegment(_Contract):
    _omit_not_null = frozenset({"code"})
    t: Literal["text"]
    v: str
    code: str | None = None  # raw technical name, shown only with "Show technical names"


class TimeSegment(_Contract):
    t: Literal["time"]
    at: ISO
    style: TimeStyle


class DurationSegment(_Contract):
    t: Literal["duration"]
    seconds: NonNegative


class MoneySegment(_Contract):
    t: Literal["money"]
    amount: Dec
    currency: Currency
    display: str

    @model_validator(mode="after")
    def _display(self):
        _check_display(self.amount, self.currency, self.display)
        return self


class PercentSegment(_Contract):
    t: Literal["percent"]
    value: Annotated[float, Field(allow_inf_nan=False)]
    display: str


class TermSegment(_Contract):
    _omit_not_null = frozenset({"code"})
    t: Literal["term"]
    key: GlossaryKey
    v: str
    code: str | None = None


class LinkSegment(_Contract):
    t: Literal["link"]
    href: AllowedPath
    v: str


Segment = Annotated[
    TextSegment | TimeSegment | DurationSegment | MoneySegment | PercentSegment | TermSegment | LinkSegment,
    Field(discriminator="t"),
]
Text = list[Segment]
NonEmptyText = Annotated[list[Segment], Field(min_length=1)]


class Money(_Contract):
    amount: Dec
    currency: Currency
    display: str

    @model_validator(mode="after")
    def _display(self):
        _check_display(self.amount, self.currency, self.display)
        return self


class Quote(_Contract):
    label: Literal["In their words"]
    text: Annotated[str, Field(min_length=1, max_length=QUOTE_MAX_CHARS)]


# --- Header, now, team -------------------------------------------------------------------


class Account(_Contract):
    started_at: ISO
    day_number: Annotated[int, Field(ge=1)]  # 1 on the opening day
    reporting_currency: Currency


class Viewer(_Contract):
    role: Literal["owner", "leader", "reader"]
    auth: Literal["cookie", "bearer"]
    can_act: bool  # owner, cookie session with CSRF, paper mode
    act_block_reason: Text | None  # null exactly when can_act
    owner_revision: NonNegative | None  # owner only


class Now(_Contract):
    code: NowCode
    tone: Tone
    light: Light
    headline: NonEmptyText
    detail: Text
    if_nothing: Text | None
    since: ISO | None
    primary_decision: DecisionId | None
    attention_count: NonNegative  # attention items with sev act or watch ("N things need you")

    @model_validator(mode="after")
    def _presentation(self):
        tone, light, primary = NOW_PRESENTATION[self.code]
        if (self.tone, self.light) != (tone, light):
            raise ValueError(f"now.code {self.code} has tone {tone} and light {light}")
        if self.primary_decision not in (None, primary):
            raise ValueError(f"now.code {self.code} can only point at {primary}")
        return self


class ServiceProblem(_Contract):
    prefix: ProblemPrefix
    code: str  # raw failure text after the prefix, e.g. "ValueError"
    text: NonEmptyText


class Service(_Contract):
    status: ServiceStatus
    active: bool
    run_id: str | None
    started_at: ISO | None
    last_sign_of_life_at: ISO | None
    silent_seconds: int | None
    stale: bool | None
    problems: list[ServiceProblem]
    plain: NonEmptyText

    @model_validator(mode="after")
    def _active(self):
        if self.active != (self.status in ACTIVE_SERVICE_STATUSES):
            raise ValueError("service.active is true exactly for STARTING, RUNNING, MANAGEMENT_ONLY and STOPPING")
        return self


class Pause(_Contract):
    profile: PauseProfile
    originator: PauseOriginator | None
    since: ISO | None
    achieved: str | None  # raw achieved state; copy in spec §D.3
    departments_paused: bool
    plain: NonEmptyText
    reason: Text | None

    @model_validator(mode="after")
    def _departments(self):
        if self.departments_paused != (self.profile in PAUSED_WORK_PROFILES):
            raise ValueError("pause.departments_paused follows the profile (paper_service.PAUSED_WORK)")
        return self


class Ai(_Contract):
    state: AiState
    plain: NonEmptyText
    clears_by_itself: bool | None
    since: ISO | None


class AllowanceWindow(_Contract):
    name: Name
    label: WindowLabel
    duration_minutes: NonNegative | None
    remaining_percent: Percent | None
    resets_at: ISO | None


class Allowance(_Contract):
    _omit_not_null = frozenset({"stop_line_state"})
    status: AllowanceStatus
    observed_at: ISO | None
    age_seconds: int | None
    stop_line_percent: Percent | None  # new work needs MORE than this
    windows: list[AllowanceWindow]
    unreported_windows: list[str]
    plain: NonEmptyText
    # Absent from older servers: then a null percent means "couldn't be read", never "none".
    stop_line_state: StopLineState | None = None

    @model_validator(mode="after")
    def _stop_line(self):
        if self.stop_line_state is not None and (self.stop_line_state == "on") != (
                self.stop_line_percent is not None):
            raise ValueError("stop_line_percent is a number exactly when stop_line_state is 'on'")
        return self


class Team(_Contract):
    service: Service
    pause: Pause
    ai: Ai
    allowance: Allowance
    automatic_turns: bool
    automatic_optimisation: bool


# --- Attention, departments, quests ----------------------------------------------------------


class Link(_Contract):
    href: AllowedPath
    label: Name


class AttentionItem(_Contract):
    id: Annotated[str, Field(pattern=r"^[a-z_]+(?::\S+)?$")]
    sev: Severity
    title: NonEmptyText
    why: Text
    consequence: Text
    can_fix_here: bool
    how: Text | None
    decision: DecisionId | None
    checkin_id: str | None
    link: Link | None
    in_next_prompt: bool
    since: ISO | None

    @property
    def code(self) -> str:
        return self.id.split(":", 1)[0]

    @model_validator(mode="after")
    def _code(self):
        if self.code not in ATTENTION_SEVERITY:
            raise ValueError(f"unknown attention code {self.code}")
        if self.sev != ATTENTION_SEVERITY[self.code]:
            raise ValueError(f"attention {self.code} has severity {ATTENTION_SEVERITY[self.code]}")
        if self.code == "checkin_due" and self.checkin_id is None:
            raise ValueError("a checkin_due card names its check-in")
        return self


class DepartmentLast(_Contract):
    at: ISO
    outcome: LastOutcome
    text: NonEmptyText
    quote: Quote | None
    link: AllowedPath | None


class DepartmentNext(_Contract):
    _omit_not_null = frozenset({"status"})
    at: ISO | None
    exact: bool
    every_seconds: Positive | None
    overdue: bool
    waiting_because: Text | None  # only for overdue turns (spec §B.5.4)
    note: Text | None
    extra_rows: NonNegative
    # Absent from older servers: then an overdue tile says "Overdue", never "Due now".
    status: NextStatus | None = None

    @model_validator(mode="after")
    def _waiting(self):
        if self.waiting_because is not None and not self.overdue:
            raise ValueError("waiting_because explains an overdue turn only")
        if self.status is not None and (self.status == "planned") != (not self.overdue):
            raise ValueError("next.status is 'planned' exactly when the turn is not overdue")
        return self


class Record7d(_Contract):
    done: NonNegative
    problems: NonNegative
    skipped: NonNegative


class Fact(_Contract):
    label: Name
    value: NonEmptyText


class Department(_Contract):
    id: DeptId
    kind: DeptKind
    name: Name
    room: Name
    job: NonEmptyText
    light: Light
    light_text: NonEmptyText
    had_problem: bool
    now: NonEmptyText
    last: DepartmentLast | None
    next: DepartmentNext | None
    heads_up: Text | None
    if_nothing: Text | None
    rules: Text | None  # trader only: mandate summary
    record_7d: Record7d
    open_quests: NonNegative
    facts: list[Fact]
    details_href: AllowedPath

    @model_validator(mode="after")
    def _room(self):
        allowed = AI_ROOM_LIGHTS if self.kind == "ai" else SOFTWARE_ROOM_LIGHTS
        if self.light not in allowed:
            raise ValueError(f"{self.kind} rooms use lights {allowed}")
        if self.rules is not None and self.id != "trader":
            raise ValueError("only the trader room shows rules")
        return self


class QuestAttempts(_Contract):
    used: NonNegative
    max: NonNegative


class Quest(_Contract):
    id: Name
    dept: DeptId
    origin: QuestOrigin
    origin_text: NonEmptyText
    state: Name  # raw task, change or candidate state
    state_text: NonEmptyText
    needs_you: bool
    terminal: bool
    created_at: ISO
    due_at: ISO | None
    deadline_at: ISO | None
    finished_at: ISO | None
    attempts: QuestAttempts | None
    reason: Text | None
    link: AllowedPath | None


# --- Calendar and story -----------------------------------------------------------------


class CalendarSeries(_Contract):
    schedule_id: Name
    every_seconds: Positive


class CalendarItem(_Contract):
    """One calendar entry.

    ``count`` and ``until`` (additive; absent on older servers and on single items) mark a group: ``count``
    deadlines of one ``CALENDAR_GROUPED_KINDS`` kind, with the same department, status and texts, from ``at``
    (the earliest) to ``until`` (the latest, at most ``CALENDAR_GROUP_WINDOW_SECONDS`` later). The group keeps
    its earliest member's ``id`` and its title already says how many, e.g. "18 research notes stop being used".
    """

    _omit_not_null = frozenset({"count", "until"})
    id: Name
    kind: CalendarKind
    dept: DeptId | Literal["you"] | None
    at: ISO
    title: NonEmptyText
    status: CalendarStatus
    exact: bool
    conditional: bool
    past: bool
    detail: Text | None
    warning: Text | None
    waiting_because: Text | None
    link: AllowedPath | None
    checkin_id: str | None
    series: CalendarSeries | None
    count: Annotated[int, Field(ge=2)] | None = None
    until: ISO | None = None

    @model_validator(mode="after")
    def _kind(self):
        if self.kind == "owner_checkin" and (self.dept != "you" or self.checkin_id is None):
            raise ValueError("owner_checkin items belong to you and name their check-in")
        if self.kind == "dept_turn" and self.dept not in AI_DEPARTMENTS:
            raise ValueError("dept_turn items belong to an AI department")
        if (self.count is None) != (self.until is None):
            raise ValueError("a grouped calendar item has both count and until")
        if self.count is not None:
            if self.kind not in CALENDAR_GROUPED_KINDS:
                raise ValueError("only close deadlines of one kind are grouped")
            spread = (_instant(self.until) - _instant(self.at)).total_seconds()
            if not 0 <= spread <= CALENDAR_GROUP_WINDOW_SECONDS:
                raise ValueError("a group runs from at to until, within the group window")
        return self


class CalendarRecurring(_Contract):
    id: Name
    title: NonEmptyText
    every_seconds: Positive
    next_at: ISO | None
    machine: bool


class CalendarEstimateSeries(_Contract):
    """Estimated turns of one schedule beyond ``ESTIMATE_ITEMS_HORIZON_HOURS``, summarised.

    The projections are ``first_at + k * every_seconds`` for ``k = 0 .. count - 1``: the same
    ones the listed ``estimated`` items would have continued with.
    """

    schedule_id: Name
    dept: DeptId
    title: NonEmptyText  # the same title as the series' listed items, e.g. "Trader's turn"
    first_at: ISO
    every_seconds: Positive
    count: Positive
    conditional: bool  # as CalendarItem.conditional for these projections
    detail: Text | None  # the text each projected item would have carried

    @model_validator(mode="after")
    def _dept(self):
        if self.dept not in AI_DEPARTMENTS:
            raise ValueError("estimate series belong to an AI department")
        return self


class Calendar(_Contract):
    _omit_not_null = frozenset({"estimates"})
    from_: ISO = Field(alias="from")
    to: ISO
    truncated: bool
    up_next: Annotated[list[str], Field(max_length=UP_NEXT_MAX)]
    items: Annotated[list[CalendarItem], Field(max_length=CALENDAR_MAX_ITEMS)]
    recurring: list[CalendarRecurring]
    notes: list[NonEmptyText]
    # Absent from older servers: then far estimates are listed as ``estimated`` items.
    estimates: list[CalendarEstimateSeries] | None = None

    @model_validator(mode="after")
    def _up_next(self):
        if self.to < self.from_:
            raise ValueError("calendar.to precedes calendar.from")
        if self.estimates is not None:
            if len({series.schedule_id for series in self.estimates}) != len(self.estimates):
                raise ValueError("one estimate series per schedule")
            end = _instant(self.to)
            for series in self.estimates:
                last = _instant(series.first_at) + timedelta(seconds=(series.count - 1) * series.every_seconds)
                if last > end:
                    raise ValueError("an estimate series ends within the calendar range")
        by_id = {item.id: item for item in self.items}
        if len(by_id) != len(self.items):
            raise ValueError("calendar item ids are unique")
        chosen = []
        for item_id in self.up_next:
            item = by_id.get(item_id)
            if item is None or item.past or item.status == "estimated":
                raise ValueError("up_next lists future, non-estimated calendar items")
            chosen.append(item.at)
        if chosen != sorted(chosen):
            raise ValueError("up_next is soonest first")
        return self


class StoryItem(_Contract):
    id: Name
    at: ISO
    until: ISO | None
    kind: StoryKind
    dept: DeptId | Literal["you", "hq"]
    tone: StoryTone
    headline: NonEmptyText
    consequence: Text | None
    quote: Quote | None
    count: Positive | None
    link: AllowedPath | None
    problem: bool
    technical: str | None


class Story(_Contract):
    """One page of the story, newest first.

    - ``window_start`` is the oldest moment this page covers completely. It may be later than
      ``as_of - 72 h`` when a capped reader hit its cap.
    - ``next_before`` is the ``before`` value for the next older page. Pages may overlap by up to
      1 ms; clients de-duplicate by ``id``. When nothing was cut, ``next_before == window_start``.
    """

    window_start: ISO
    next_before: ISO | None
    items: Annotated[list[StoryItem], Field(max_length=STORY_MAX_ITEMS)]

    @model_validator(mode="after")
    def _order(self):
        moments = [item.at for item in self.items]
        if moments != sorted(moments, reverse=True):
            raise ValueError("story items are newest first")
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("story item ids are unique")
        return self


class StoryPage(_Contract):
    """``GET /api/v1/hq/story`` response."""

    story: Story


# --- Money and market ----------------------------------------------------------------------


class EstimateNote(_Contract):
    code: EstimateNoteCode
    tone: Literal["calm", "attention"]
    text: NonEmptyText


class Opening(_Contract):
    native: list[Money]
    reporting: Money | None
    at: ISO | None


class Change(_Contract):
    total: Money | None
    currency_effect: Money | None
    team_effect: Money | None
    unknown_reason: Text | None

    @model_validator(mode="after")
    def _unknown(self):
        if self.total is None and self.unknown_reason is None:
            raise ValueError("an unknown change carries its reason")
        return self


class Holdings(_Contract):
    coins: Money | None
    cash: Money | None
    flat: bool | None
    open_orders: NonNegative
    unclear_orders: NonNegative


class Counts(_Contract):
    decisions: NonNegative
    waits: NonNegative
    buys: NonNegative
    sells: NonNegative
    other_decisions: NonNegative
    orders: NonNegative
    completed_trades: NonNegative
    research_notes: NonNegative
    notes_in_use: NonNegative
    lessons: NonNegative


class MissingItem(_Contract):
    code: EconCode
    text: NonEmptyText
    fixable_here: bool


class PaysForItself(_Contract):
    status: PaysStatus
    net: Money | None
    missing: list[MissingItem]
    honest_note: Text | None

    @model_validator(mode="after")
    def _net(self):
        if (self.status == "unknown") != (self.net is None):
            raise ValueError("pays_for_itself.net is known exactly when the status is known")
        return self


class MoneyBlock(_Contract):
    available: bool
    unavailable_reason: Text | None
    as_of: ISO | None
    practice_value: Money | None
    value_final: bool
    estimate_notes: list[EstimateNote]
    opening: Opening
    change: Change
    holdings: Holdings
    counts: Counts
    pays_for_itself: PaysForItself
    at_risk: NonEmptyText

    @model_validator(mode="after")
    def _available(self):
        if self.available == (self.unavailable_reason is not None):
            raise ValueError("unavailable_reason is given exactly when money is unavailable")
        if not self.available and self.practice_value is not None:
            raise ValueError("unavailable money has no value")
        return self


class MarketItem(_Contract):
    symbol: MarketSymbol
    price: Money | None
    price_at: ISO | None
    price_age_seconds: int | None
    avg_20h: Money | None
    avg_50h: Money | None
    trend: Trend
    text: NonEmptyText


# --- Decisions, check-ins, prompt facts, notices, links ------------------------------------


class Preview(_Contract):
    what_happens: list[NonEmptyText]
    cost: NonEmptyText
    undo: NonEmptyText
    reversible: Literal["yes", "partly", "no"]
    when: NonEmptyText
    right_now: list[NonEmptyText]


class Blocker(_Contract):
    code: Name  # raw barrier string from controls._resume_barriers
    text: NonEmptyText
    definite: bool  # true only for a system pause


class Period(_Contract):
    start: ISO
    end: ISO


class DecisionFacts(_Contract):
    _omit_not_null = frozenset({"predicted_blockers", "graph_share_presets", "scheduled_departments"})
    predicted_blockers: list[Blocker] | None = None
    max_attempts: NonNegative | None = None
    deadline_seconds: NonNegative | None = None
    flat: bool | None = None
    graph_share_presets: list[Dec] | None = None
    scheduled_departments: list[str] | None = None
    suggested_period: Period | None = None


class DecisionCard(_Contract):
    id: DecisionId
    shown: bool
    available: bool
    unavailable_because: Text | None
    recommended: bool
    recommended_because: Text | None
    title: Name
    button_label: Name
    confirm_label: Name
    weight: Literal["light", "careful"]
    preview: Preview
    facts: DecisionFacts

    @model_validator(mode="after")
    def _availability(self):
        if self.available == (self.unavailable_because is not None):
            raise ValueError("unavailable_because is given exactly when the decision is unavailable")
        if self.recommended and (not self.available or self.recommended_because is None):
            raise ValueError("a recommended decision is available and says why")
        return self


class CheckinHistory(_Contract):
    action: CheckinAction
    at: ISO
    planned_at: ISO | None


class Checkin(_Contract):
    """One owner check-in, as in ``checkins.items`` and the ``/api/v1/owner/checkins`` responses."""

    checkin_id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=CHECKIN_TITLE_MAX_CHARS)]
    prompt_kind: CheckinPromptKind
    planned_at: ISO
    state: CheckinState
    done_at: ISO | None  # created_at of the done row
    note: Annotated[str, Field(max_length=CHECKIN_NOTE_MAX_CHARS)] | None
    history: list[CheckinHistory]

    @model_validator(mode="after")
    def _terminal(self):
        if (self.state == "done") != (self.done_at is not None):
            raise ValueError("done_at is set exactly for a done check-in")
        if self.note is not None and self.state not in ("done", "skipped"):
            raise ValueError("only done or skipped check-ins carry a note")
        return self


class Checkins(_Contract):
    available: bool
    items: list[Checkin]
    next_id: str | None
    last_done_at: ISO | None
    suggested_at: ISO | None

    @model_validator(mode="after")
    def _available(self):
        if not self.available and (self.items or self.next_id is not None):
            raise ValueError("without the check-in store there are no check-ins")
        return self


class CheckinsResponse(_Contract):
    """``GET /api/v1/owner/checkins`` response."""

    as_of: ISO
    checkins: Annotated[list[Checkin], Field(max_length=CHECKIN_WINDOW_MAX_ITEMS)]


class CheckinRecordResponse(_Contract):
    """``POST /api/v1/owner/checkins`` response."""

    checkin: Checkin
    replayed: bool


class PromptNextTurn(_Contract):
    dept: DeptId
    name: Name
    at: ISO | None


class PromptSince(_Contract):
    from_: ISO | None = Field(alias="from")  # null: "the last 24 hours"
    label: Name
    decisions: NonNegative
    waits: NonNegative
    orders: NonNegative
    completed_trades: NonNegative
    problems: NonNegative
    research_notes: NonNegative


class PromptFacts(_Contract):
    as_of: ISO
    team: Name
    setting: Name
    allowance: Name
    next_turns: list[PromptNextTurn]
    since: PromptSince
    flagged: Annotated[list[Annotated[str, Field(max_length=FLAGGED_MAX_CHARS)]], Field(max_length=FLAGGED_MAX_ITEMS)]
    practice_value: str | None
    value_final: bool
    pays_for_itself: Name


class Notice(_Contract):
    code: NoticeCode
    tone: Literal["info", "attention"]
    title: NonEmptyText
    text: NonEmptyText
    link: AllowedPath | None


class Links(_Contract):
    journal: Literal["/journal"]
    mission_control: Literal["/progress"]
    trading: Literal["/trading"]
    organisation: Literal["/organization"]
    costs: Literal["/costs"]
    improvements: Literal["/changes"]
    owner: Literal["/owner"] | None  # owner only
    review_export: Literal["/api/v1/review-export"]


class HQSnapshot(_Contract):
    """``GET /api/v1/hq`` response and the page's JSON island."""

    schema_: Literal["trade-graph.hq.v1"] = Field(alias="schema")
    as_of: ISO
    poll_seconds: Literal[30]
    mode: Literal["paper", "live"]
    account: Account
    viewer: Viewer
    now: Now
    team: Team
    attention: list[AttentionItem]
    departments: list[Department]
    quests: Annotated[list[Quest], Field(max_length=QUEST_MAX_ITEMS)]
    calendar: Calendar
    story: Story
    money: MoneyBlock
    market: list[MarketItem]
    decisions: list[DecisionCard]
    checkins: Checkins | None  # null unless owner
    prompt_facts: PromptFacts
    notices: list[Notice]
    links: Links

    @model_validator(mode="after")
    def _invariants(self):
        viewer, owner = self.viewer, self.viewer.role == "owner"
        if viewer.can_act and (not owner or viewer.auth != "cookie" or self.mode != "paper"):
            raise ValueError("only an owner cookie session in paper mode can act")
        if viewer.can_act == (viewer.act_block_reason is not None):
            raise ValueError("act_block_reason is given exactly when the viewer cannot act")
        if not viewer.can_act and self.decisions:
            raise ValueError("decisions are empty when the viewer cannot act")
        if owner == (self.checkins is None):
            raise ValueError("checkins is an object for the owner and null otherwise")
        if not owner and (viewer.owner_revision is not None or self.links.owner is not None):
            raise ValueError("owner revision and owner link are for the owner only")
        if [(dept.id, dept.kind) for dept in self.departments] != list(DEPARTMENT_ORDER):
            raise ValueError("departments lists the nine rooms in DEPARTMENT_ORDER")
        if self.now.code in OFFLINE_NOW_CODES and any(
            dept.light != "off" for dept in self.departments if dept.kind == "ai"
        ):
            raise ValueError("every AI room is off while the team is off or silent")
        ranks = [SEVERITY_ORDER.index(item.sev) for item in self.attention]
        if ranks != sorted(ranks):
            raise ValueError("attention is sorted act, watch, info")
        if self.now.attention_count != sum(item.sev != "info" for item in self.attention):
            raise ValueError("now.attention_count counts act and watch attention items")
        if sum(item.code == "turn_stuck" for item in self.attention) > TURN_STUCK_MAX_CARDS:
            raise ValueError("at most three turn_stuck cards")
        if len({item.id for item in self.attention}) != len(self.attention):
            raise ValueError("attention ids are unique")
        if len({card.id for card in self.decisions}) != len(self.decisions):
            raise ValueError("decision ids are unique")
        if len({quest.id for quest in self.quests}) != len(self.quests):
            raise ValueError("quest ids are unique")
        if len({item.symbol for item in self.market}) != len(self.market):
            raise ValueError("one market entry per symbol")
        return self


def validate_snapshot(data: Mapping[str, Any]) -> HQSnapshot:
    """Validate a projected snapshot dict against ``trade-graph.hq.v1``."""
    return HQSnapshot.model_validate(data)


def contract_keys(model: type[BaseModel] = HQSnapshot) -> set[str]:
    """Every JSON key any contract model can emit, for redaction-safety checks."""
    keys: set[str] = set()
    pending, seen = [model], set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for name, field in current.model_fields.items():
            keys.add(field.alias or name)
            pending.extend(_models_in(field.annotation))
    return keys


def _models_in(annotation: Any) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    found: list[type[BaseModel]] = []
    for argument in get_args(annotation):
        found.extend(_models_in(argument))
    return found

