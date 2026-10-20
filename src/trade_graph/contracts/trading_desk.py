"""The Trading desk's playbook, stances and market-type readings (W12-A, C12; D280, D116, D127, D138 item 5, D295-D299).

The Trader is the Trading desk (D280): it trades and owns strategy choice and strategy research. Per coin and situation
it decides whether to wait in cash, buy dips, follow the trend or do something else, and it keeps a **playbook**: per
market type (W11-A's rising, sideways, falling, unclear), the strategy versions it means to use, or "wait", each with a
short reason and the practice-range reports it rests on.

- ``PlaybookRevision`` is what a desk answer may carry to revise its playbook. Software stores every revision
  append-only with who, when, why and the revision it replaced, and reads it at any past moment like lessons.
- ``CoinStance`` is the desk's stance on one coin it considered in a wake: the stance, the market type it saw, the
  strategy version, the news it weighed and a one-sentence reason. News cannot be replayed, so a choice made because of
  news is recorded with its reason for Learning and the scorecard (D280 item 4).
- ``CoinMarketType`` is what a desk wake shows of a coin's label: current, stale or missing, never a guess.

Discretion stays real (D280 item 5, D116, D138 item 5): none of these is a confidence, score, approval or pass mark,
and software never refuses a day plan for not following the playbook, a stance or a market type. The playbook is the
desk's own record of its intentions, not a rule software enforces; money limits, riba rules and safety exits are
unchanged. Bounds are the size of one document, like a request envelope; nothing limits how often the desk revises
its playbook or gives stances.

This module imports no adapter, library or model code.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from trade_graph.contracts.alarms import REFERENCE_PATTERN, SYMBOL_PATTERN
from trade_graph.domain.market_types import MARKET_TYPES, MarketType

DESK_CONTRACT_VERSION = "desk-v1"
Stance = Literal["wait", "buy_dips", "follow_trend", "other"]
STANCES: tuple[Stance, ...] = ("wait", "buy_dips", "follow_trend", "other")
# What the desk says it saw: one of the four words, or "none" when its wake showed no current label for the coin.
SeenMarketType = Literal["rising", "sideways", "falling", "unclear", "none"]
SEEN_MARKET_TYPES: tuple[str, ...] = (*MARKET_TYPES, "none")
LabelStatus = Literal["current", "stale", "missing"]
LABEL_STATUSES: tuple[LabelStatus, ...] = ("current", "stale", "missing")
CoinRole = Literal["held", "planned", "focus", "trigger", "shortlist"]
COIN_ROLES: tuple[CoinRole, ...] = ("held", "planned", "focus", "trigger", "shortlist")

# Size bounds of one document, like a request envelope; they never limit how often anything happens (D116).
MAX_REASON = 300
MAX_WHY = 500
MAX_SENTENCE = 600
MAX_STRATEGIES_PER_TYPE = 8
MAX_REPORT_REFS = 8
MAX_NEWS_REFS = 8
MAX_STANCES = 32
STRATEGY_REF = r"^[a-z0-9][a-z0-9-]{2,63}@[1-9][0-9]{0,6}$"
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")


def _plain(value: str | None) -> str | None:
    if value is not None and _CONTROL.search(value):
        raise ValueError("control or text-direction characters are not allowed")
    return value


def _references(values: tuple[str, ...], pattern: str, what: str) -> tuple[str, ...]:
    if any(re.fullmatch(pattern, item) is None for item in values):
        raise ValueError(f"{what} are 1 to 128 letters, digits or ._:/- characters")
    if len(values) != len(set(values)):
        raise ValueError(f"each of the {what} is named once")
    return values


def canonical_json(document: Any) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- what the desk's answer may carry -----------------------------------------------------------------------------

class PlaybookChoice(_Frozen):
    """The desk's choice for one market type: the strategy versions it means to use, or wait in cash."""

    market_type: MarketType
    stance: Stance = Field(description="wait (cash), buy_dips, follow_trend or other.")
    strategy_refs: tuple[str, ...] = Field(
        default=(), max_length=MAX_STRATEGIES_PER_TYPE,
        description="Registered strategy versions (strategy_id@version) it means to use; empty for wait.")
    reason: str = Field(min_length=1, max_length=MAX_REASON, description="One or two plain sentences.")
    report_ids: tuple[str, ...] = Field(
        default=(), max_length=MAX_REPORT_REFS,
        description="Practice-range report ids (run ids) or lab request ids this choice rests on.")

    @field_validator("reason")
    @classmethod
    def _text(cls, value: str) -> str:
        return _plain(value) or value

    @field_validator("strategy_refs")
    @classmethod
    def _versions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(re.fullmatch(STRATEGY_REF, item) is None for item in value):
            raise ValueError("strategy versions are written strategy_id@version, for example dip-rebound@1")
        if len(value) != len(set(value)):
            raise ValueError("each strategy version is named once")
        return value

    @field_validator("report_ids")
    @classmethod
    def _reports(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _references(value, REFERENCE_PATTERN, "report ids")

    @model_validator(mode="after")
    def _wait_is_cash(self) -> PlaybookChoice:
        if self.stance == "wait" and self.strategy_refs:
            raise ValueError("wait means cash: a wait choice names no strategy version")
        return self


class PlaybookRevision(_Frozen):
    """A new revision of the desk's playbook: per market type, its choice; why it changed. It replaces the whole
    playbook (a market type it leaves out has no choice in the new revision)."""

    choices: tuple[PlaybookChoice, ...] = Field(min_length=1, max_length=len(MARKET_TYPES))
    why: str = Field(min_length=1, max_length=MAX_WHY, description="Why the playbook changes now, in plain words.")

    @field_validator("why")
    @classmethod
    def _text(cls, value: str) -> str:
        return _plain(value) or value

    @model_validator(mode="after")
    def _once_each(self) -> PlaybookRevision:
        kinds = [choice.market_type for choice in self.choices]
        if len(kinds) != len(set(kinds)):
            raise ValueError("a playbook names each market type once")
        return self

    def choice_for(self, market_type: str) -> PlaybookChoice | None:
        return next((choice for choice in self.choices if choice.market_type == market_type), None)

    def canonical_document(self) -> dict[str, Any]:
        """The stored document: the choices in the market types' order, so equal playbooks hash the same."""
        order = {name: index for index, name in enumerate(MARKET_TYPES)}
        choices = sorted(self.choices, key=lambda choice: order[choice.market_type])
        return {"contract_version": DESK_CONTRACT_VERSION,
                "choices": [choice.model_dump(mode="json") for choice in choices]}

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(canonical_json(self.canonical_document()).encode("ascii")).hexdigest()


class CoinStance(_Frozen):
    """The desk's stance on one coin it considered in this wake. A record for Learning and the scorecard, never a
    rule: software does not check a day plan against it."""

    symbol: str = Field(pattern=SYMBOL_PATTERN)
    stance: Stance = Field(description="wait (cash), buy_dips, follow_trend or other.")
    market_type_seen: SeenMarketType = Field(
        description="The coin's market type as your wake showed it (wake.market_types); none when no current label.")
    strategy_ref: str | None = Field(default=None, pattern=STRATEGY_REF,
                                     description="The strategy version you use for this coin, if any.")
    news_refs: tuple[str, ...] = Field(
        default=(), max_length=MAX_NEWS_REFS,
        description="Ids of the news you weighed (headline ids, Leader round or request ids); empty if none.")
    reason: str = Field(min_length=1, max_length=MAX_REASON, description="One plain sentence.")

    @field_validator("reason")
    @classmethod
    def _text(cls, value: str) -> str:
        return _plain(value) or value

    @field_validator("news_refs")
    @classmethod
    def _news(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _references(value, REFERENCE_PATTERN, "news references")


def one_stance_per_coin(stances: tuple[CoinStance, ...]) -> None:
    symbols = [stance.symbol for stance in stances]
    if len(symbols) != len(set(symbols)):
        raise ValueError("one stance per coin in a turn; a later turn may give another")


# --- what a desk wake shows ---------------------------------------------------------------------------------------

class CoinMarketType(_Frozen):
    """A coin's market type as the desk's wake shows it (W11-A's labels; D287: a label is current for 2 hours).

    ``market_type`` is the type in force now: set only when the newest label is current. A stale label keeps its word
    in ``newest_label`` with its age, and a coin never labelled is ``missing``: neither is shown as the type now.
    """

    symbol: str = Field(pattern=SYMBOL_PATTERN)
    status: LabelStatus
    market_type: MarketType | None = None
    newest_label: MarketType | None = None
    reason: str | None = Field(default=None, max_length=40)
    labelled_at: AwareDatetime | None = None
    age_minutes: int | None = Field(default=None, ge=0)
    rules_ref: str | None = Field(default=None, max_length=40)
    label_id: str | None = Field(default=None, max_length=64)
    seen_as: tuple[CoinRole, ...] = ()
    sentence: str = Field(min_length=1, max_length=MAX_SENTENCE)

    @model_validator(mode="after")
    def _as_such(self) -> CoinMarketType:
        if (self.market_type is not None) != (self.status == "current"):
            raise ValueError("market_type is the type in force: set exactly when the newest label is current")
        if (self.status == "missing") != (self.newest_label is None):
            raise ValueError("a missing label has no newest label; a stale or current one has")
        if self.market_type is not None and self.market_type != self.newest_label:
            raise ValueError("a current label's type is its newest label")
        return self


# --- what software recorded (read by HQ's Trading desk room and the scorecard, W13-A) -----------------------------

class PlaybookRecord(_Frozen):
    """One stored playbook revision: who, when, why, the revision it replaced, and its choices."""

    revision_id: str
    portfolio_id: str
    revision: int = Field(ge=1)
    previous_revision_id: str | None
    set_by: Literal["trader"]
    wake_id: str | None
    task_id: str | None
    why: str
    choices: tuple[PlaybookChoice, ...]
    document_sha256: str
    created_at: AwareDatetime

    def choice_for(self, market_type: str) -> PlaybookChoice | None:
        return next((choice for choice in self.choices if choice.market_type == market_type), None)


class StanceRecord(_Frozen):
    """One stored stance, with the label software showed for the coin in that wake (status and type)."""

    stance_id: str
    portfolio_id: str
    wake_id: str
    task_id: str | None
    trading_day_start: AwareDatetime
    symbol: str
    stance: Stance
    market_type_seen: SeenMarketType
    label_status: LabelStatus
    label_market_type: MarketType | None
    label_id: str | None
    strategy_ref: str | None
    news_refs: tuple[str, ...]
    reason: str
    playbook_revision_id: str | None
    created_at: AwareDatetime


class DayPlanDeskRecord(_Frozen):
    """What software recorded about the desk when a day plan was installed: the coin's market type at that moment
    (with its label's status), the playbook revision in force and the desk's stance on the coin in the same answer."""

    plan_id: str
    portfolio_id: str
    symbol: str
    label_status: LabelStatus
    market_type: MarketType | None
    label_id: str | None
    labelled_at: AwareDatetime | None
    rules_ref: str | None
    playbook_revision_id: str | None
    playbook_revision: int | None
    stance_id: str | None
    stance: Stance | None
    recorded_at: AwareDatetime

    @property
    def market_type_in_force(self) -> MarketType | None:
        """The type at installation when its label was current; None for a stale or missing label."""
        return self.market_type if self.label_status == "current" else None


__all__ = [
    "COIN_ROLES", "DESK_CONTRACT_VERSION", "LABEL_STATUSES", "MAX_NEWS_REFS", "MAX_REASON", "MAX_REPORT_REFS",
    "MAX_STANCES", "MAX_WHY", "SEEN_MARKET_TYPES", "STANCES", "STRATEGY_REF", "CoinMarketType", "CoinRole",
    "CoinStance", "DayPlanDeskRecord", "LabelStatus", "PlaybookChoice", "PlaybookRecord", "PlaybookRevision",
    "SeenMarketType", "Stance", "StanceRecord", "canonical_json", "one_stance_per_coin",
]
