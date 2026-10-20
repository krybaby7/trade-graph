"""The strategy lab's requests (W11-B, C11; D280, D116, D117, D138 item 5, D165-D169, D290-D294).

The Trading desk (the Trader's role, D280) asks the lab inside the installed service to do four things, all data and
never code:

- ``register_strategy``: register a strategy version, a ``PracticeStrategy`` document in the practice-range grammar
  (D165), given as its JSON text so the reply stays a strict provider schema;
- ``create_study``: fix a study's earlier tuning window and later confirmation window of whole UTC days (D166);
- ``tune``: replay a registered version inside its study's tuning window (or a sub-window of it) on one coin's
  candles, under the cost profiles it names and always the account's own;
- ``confirm``: replay a version once on its study's whole, untouched confirmation window, which the first version
  confirmed there spends.

The reply's shape is checked strictly (``LabRequest``); whether a request can be carried out is the lab's question:
``LabRequest.problem`` finds what is wrong with one request alone (a malformed strategy, a missing field, windows in
the wrong order), and the protected practice-range store finds what the study rules refuse (tuning on confirmation
days, confirming on spent or touched days). Either way the request is refused with its reason and reported back to
the desk at its next wake; one bad request never sinks the desk's whole answer, and none is dropped silently.

There is no cap on how many requests the desk makes (D116): ``lab_requests`` has no item bound; one provider reply
is bounded by the model response size, which is an envelope, not a count. A request carries no confidence, score or
approval field, and nothing in it can place, send or cancel an order.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from trade_graph.contracts.alarms import SYMBOL_PATTERN
from trade_graph.contracts.candles import CANDLE_SOURCES
from trade_graph.contracts.practice_range import PracticeStrategy, ReplayWindow, StudyPlan

LAB_CONTRACT_VERSION = "strategy-lab-v1"
LabRequestKind = Literal["register_strategy", "create_study", "tune", "confirm"]
LAB_REQUEST_KINDS: tuple[LabRequestKind, ...] = ("register_strategy", "create_study", "tune", "confirm")
LabRequestStatus = Literal["queued", "running", "done", "refused", "failed"]
LAB_REQUEST_STATUSES: tuple[LabRequestStatus, ...] = ("queued", "running", "done", "refused", "failed")
ENDED_STATUSES: frozenset[str] = frozenset({"done", "refused", "failed"})
# Text bounds of one request, like a request envelope; they never limit how many requests there are (D116).
MAX_STRATEGY_JSON = 20_000
MAX_LAB_NOTE = 500
MAX_REASON = 1000
MAX_COST_PROFILES = 8
DEFAULT_SOURCE = "kraken_public_ohlc"  # the installed price collector's candles (D222)
STRATEGY_REF_PATTERN = r"^[a-z0-9][a-z0-9-]{2,63}@[1-9][0-9]{0,6}$"
_STRATEGY_ID = r"^[a-z0-9][a-z0-9-]{2,63}$"
_STUDY_ID = r"^[a-z0-9][a-z0-9._-]{2,63}$"
_PROFILE = r"^(all|[a-z0-9][a-z0-9-]{1,63}(@[1-9][0-9]{0,6})?)$"
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")

# What each kind needs, and what else it may name (a note always). Anything else named is refused with the reason,
# because a value the lab would ignore is a misunderstanding the desk should hear about.
_NEEDS: dict[str, tuple[str, ...]] = {
    "register_strategy": ("strategy_json",),
    "create_study": ("study_id", "strategy_id", "tune_start", "tune_end", "confirm_start", "confirm_end"),
    "tune": ("study_id", "strategy_ref", "symbol"),
    "confirm": ("study_id", "strategy_ref", "symbol"),
}
_MAY: dict[str, tuple[str, ...]] = {
    "register_strategy": (),
    "create_study": (),
    "tune": ("source", "window_start", "window_end", "cost_profiles"),
    "confirm": ("source", "cost_profiles"),
}
_FIELDS = ("strategy_json", "strategy_ref", "strategy_id", "study_id", "tune_start", "tune_end", "confirm_start",
           "confirm_end", "window_start", "window_end", "symbol", "source", "cost_profiles")


def _plain(value: str | None) -> str | None:
    if value is not None and _CONTROL.search(value):
        raise ValueError("control or text-direction characters are not allowed")
    return value


def _errors(error: ValidationError, limit: int = 3) -> str:
    parts = []
    for item in error.errors()[:limit]:
        where = ".".join(str(part) for part in item.get("loc", ())) or "document"
        parts.append(f"{where}: {item.get('msg', 'invalid')}")
    more = len(error.errors()) - limit
    return "; ".join(parts) + (f"; and {more} more" if more > 0 else "")


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LabRequest(_Frozen):
    """One request to the strategy lab, in the flat shape of a strict provider reply (unused fields are null).

    ``problem()`` says what is wrong with it on its own, or None; the lab then checks it against the store's study
    rules when its turn in the queue comes.
    """

    kind: LabRequestKind
    strategy_json: str | None = Field(default=None, min_length=1, max_length=MAX_STRATEGY_JSON)
    strategy_ref: str | None = Field(default=None, min_length=1, max_length=100)
    strategy_id: str | None = Field(default=None, min_length=1, max_length=100)
    study_id: str | None = Field(default=None, min_length=1, max_length=100)
    tune_start: date | None = None
    tune_end: date | None = None
    confirm_start: date | None = None
    confirm_end: date | None = None
    window_start: date | None = None
    window_end: date | None = None
    symbol: str | None = Field(default=None, min_length=1, max_length=40)
    source: str | None = Field(default=None, min_length=1, max_length=40)
    cost_profiles: tuple[str, ...] = Field(default=(), max_length=MAX_COST_PROFILES)
    note: str | None = Field(default=None, min_length=1, max_length=MAX_LAB_NOTE)

    @field_validator("note")
    @classmethod
    def _text(cls, value: str | None) -> str | None:
        return _plain(value)

    @field_validator("cost_profiles")
    @classmethod
    def _profiles(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(len(item) > 80 for item in value):
            raise ValueError("a cost profile name is at most 80 characters")
        return value

    # --- what is wrong with this request on its own ---------------------------------------------------------------

    def problem(self) -> str | None:
        """The reason this request cannot be carried out, judged from the request alone; None when it may queue."""
        missing = [name for name in _NEEDS[self.kind] if getattr(self, name) is None]
        if missing:
            return f"a {self.kind} request names {', '.join(missing)}"
        allowed = set(_NEEDS[self.kind]) | set(_MAY[self.kind])
        if self.kind == "confirm" and (self.window_start is not None or self.window_end is not None):
            return ("a confirmation replays the whole confirmation window of its study; leave window_start and "
                    "window_end empty")
        named = [name for name in _FIELDS if name not in allowed and getattr(self, name) not in (None, ())]
        if named:
            return f"a {self.kind} request does not use {', '.join(named)}; leave them empty"
        if self.kind == "register_strategy":
            return self._strategy_problem()
        if self.kind == "create_study":
            return self._study_problem()
        return self._replay_problem()

    def _strategy_problem(self) -> str | None:
        try:
            document = json.loads(self.strategy_json or "")
        except (ValueError, RecursionError):
            return "the strategy document is not JSON; send the PracticeStrategy document as JSON text"
        if not isinstance(document, dict):
            return "the strategy document is a JSON object in the practice-range grammar (PracticeStrategy)"
        try:
            PracticeStrategy.model_validate(document)
        except ValidationError as error:
            return f"the strategy document is refused by the practice-range grammar: {_errors(error)}"[:MAX_REASON]
        return None

    def _study_problem(self) -> str | None:
        if re.fullmatch(_STUDY_ID, self.study_id or "") is None:
            return "study_id is 3 to 64 lowercase letters, digits or ._- characters, starting with a letter or digit"
        if re.fullmatch(_STRATEGY_ID, self.strategy_id or "") is None:
            return "strategy_id is a strategy family: 3 to 64 lowercase letters, digits or hyphens"
        try:
            self.study_plan()
        except ValidationError as error:
            return f"the study's windows are refused: {_errors(error)}"[:MAX_REASON]
        return None

    def _replay_problem(self) -> str | None:
        if re.fullmatch(_STUDY_ID, self.study_id or "") is None:
            return "study_id is 3 to 64 lowercase letters, digits or ._- characters, starting with a letter or digit"
        if re.fullmatch(STRATEGY_REF_PATTERN, self.strategy_ref or "") is None:
            return "strategy_ref names a registered version as strategy_id@version, for example range-reversion@2"
        if re.fullmatch(SYMBOL_PATTERN, self.symbol or "") is None or (self.symbol or "").count("/") != 1:
            return "the symbol is a pair written BASE/QUOTE, for example SOL/USD"
        if self.source is not None and self.source not in CANDLE_SOURCES:
            return f"source {self.source!r} is not a candle source; use one of {', '.join(CANDLE_SOURCES)}"
        if (self.window_start is None) != (self.window_end is None):
            return "a tuning sub-window names both window_start and window_end, or neither"
        if self.window_start is not None:
            try:
                self.window()
            except ValidationError as error:
                return f"the tuning sub-window is refused: {_errors(error)}"[:MAX_REASON]
        bad = [name for name in self.cost_profiles if re.fullmatch(_PROFILE, name) is None]
        if bad:
            return f"cost profiles are named as all, a profile id or id@revision; not {', '.join(bad)}"
        return None

    # --- the validated forms (call after problem() returned None) -------------------------------------------------

    def strategy(self) -> PracticeStrategy:
        return PracticeStrategy.model_validate_json(self.strategy_json or "")

    def study_plan(self) -> StudyPlan:
        return StudyPlan.model_validate({
            "study_id": self.study_id, "strategy_id": self.strategy_id,
            "tune": {"start": self.tune_start, "end": self.tune_end},
            "confirm": {"start": self.confirm_start, "end": self.confirm_end}, "note": self.note or ""})

    def window(self) -> ReplayWindow | None:
        if self.window_start is None:
            return None
        return ReplayWindow(start=self.window_start, end=self.window_end)

    def replay_source(self) -> str:
        return self.source or DEFAULT_SOURCE

    def family(self) -> str | None:
        """The strategy family the request is about, when it names one."""
        if self.kind == "create_study":
            return self.strategy_id
        if self.kind in ("tune", "confirm") and self.strategy_ref:
            return self.strategy_ref.split("@", 1)[0]
        if self.kind == "register_strategy":
            try:
                document = json.loads(self.strategy_json or "")
            except (ValueError, RecursionError):
                return None
            family = document.get("strategy_id") if isinstance(document, dict) else None
            return family if isinstance(family, str) and re.fullmatch(_STRATEGY_ID, family) else None
        return None

    def brief(self) -> dict[str, Any]:
        """The request's identity in a few fields, for the desk's lab context and HQ (never the whole document)."""
        document: dict[str, Any] = {"kind": self.kind}
        if self.kind == "register_strategy":
            family = self.family()
            try:
                version = json.loads(self.strategy_json or "").get("version")
            except (ValueError, RecursionError, AttributeError):
                version = None
            if family and isinstance(version, int) and not isinstance(version, bool):
                document["strategy_ref"] = f"{family}@{version}"
        for name in ("strategy_ref", "strategy_id", "study_id", "symbol", "source"):
            value = getattr(self, name)
            if value is not None:
                document[name] = value
        for name in ("tune_start", "tune_end", "confirm_start", "confirm_end", "window_start", "window_end"):
            value = getattr(self, name)
            if value is not None:
                document[name] = value.isoformat()
        if self.cost_profiles:
            document["cost_profiles"] = list(self.cost_profiles)
        if self.note:
            document["note"] = self.note
        return document


class AppliedLabRequest(_Frozen):
    """What became of one lab request when the desk's answer was applied: queued in the lab, or refused at once."""

    request_id: str = Field(min_length=1, max_length=200)
    kind: LabRequestKind
    status: Literal["queued", "refused"]
    reason: str | None = Field(default=None, max_length=MAX_REASON)


__all__ = [
    "DEFAULT_SOURCE", "ENDED_STATUSES", "LAB_CONTRACT_VERSION", "LAB_REQUEST_KINDS", "LAB_REQUEST_STATUSES",
    "MAX_COST_PROFILES", "MAX_LAB_NOTE", "MAX_REASON", "MAX_STRATEGY_JSON", "STRATEGY_REF_PATTERN",
    "AppliedLabRequest", "LabRequest", "LabRequestKind", "LabRequestStatus",
]
