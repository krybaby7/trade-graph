"""Provider-neutral decision questions: a state and typed questions in, probabilities out (T44).

A decision model such as Jev writes no text and runs no department. Its answers are data that a fixed
software rule reads (the news alarm). Nothing in this contract can place, approve or block an order, change
a budget or owner policy, or call a tool.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trade_graph.domain.money import parse_decimal

QUESTION_NAME = r"^[a-z][a-z0-9_]{0,63}$"
OPTION_NAME = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
MAX_QUESTIONS = 16
MAX_CHOICE_OPTIONS = 255  # S40
NOUL_OPTIONS = ("yes", "no")

DecisionFailure = Literal[
    "invalid_request",     # HTTP 400 and other 4xx
    "credentials",         # HTTP 401/403
    "too_large",           # HTTP 413
    "unsupported_media",   # HTTP 415
    "unprocessable",       # HTTP 422
    "rate_limit",          # HTTP 429
    "overloaded",          # HTTP 529
    "server_error",        # other 5xx
    "timeout_uncertain",   # no reply, or a reply that did not finish: the call may have been processed
    "refused_response",    # a reply refused by the transport bounds
    "unexpected_model",    # answered by a model other than the pinned one
    "malformed_answers",   # answers that do not match the questions asked
]
# A later attempt may succeed; every attempt gets its own reservation and receipt.
RETRYABLE_FAILURES = frozenset({"credentials", "rate_limit", "overloaded", "server_error", "timeout_uncertain"})


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NoulQuestion(_Frozen):
    """A yes/no question; the answer gives the probability of each."""

    type: Literal["noul"] = "noul"
    text: str = Field(min_length=1, max_length=2000)

    def answer_options(self) -> tuple[str, ...]:
        return NOUL_OPTIONS


class ChoiceQuestion(_Frozen):
    type: Literal["choice"] = "choice"
    text: str = Field(min_length=1, max_length=2000)
    options: tuple[Annotated[str, Field(pattern=OPTION_NAME)], ...] = Field(min_length=2,
                                                                           max_length=MAX_CHOICE_OPTIONS)

    @field_validator("options")
    @classmethod
    def _unique(cls, options: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(options)) != len(options):
            raise ValueError("choice options must be unique")
        return options

    def answer_options(self) -> tuple[str, ...]:
        return self.options


class ScoreQuestion(_Frozen):
    """A score from 1 to ``levels``; the answer gives the probability of each level."""

    type: Literal["score"] = "score"
    text: str = Field(min_length=1, max_length=2000)
    levels: int = Field(ge=2, le=10, strict=True)

    def answer_options(self) -> tuple[str, ...]:
        return tuple(str(level) for level in range(1, self.levels + 1))


DecisionQuestion = Annotated[NoulQuestion | ChoiceQuestion | ScoreQuestion, Field(discriminator="type")]


class DecisionRequest(_Frozen):
    model: str = Field(min_length=1, max_length=64)
    state: dict[str, Any]
    questions: dict[Annotated[str, Field(pattern=QUESTION_NAME)], DecisionQuestion] = Field(
        min_length=1, max_length=MAX_QUESTIONS)


class DecisionAnswer(_Frozen):
    """Probabilities per option, as the model reported them. Software never recomputes them."""

    probabilities: dict[str, Decimal] = Field(min_length=1, max_length=MAX_CHOICE_OPTIONS)
    confidence: Decimal | None = None

    @field_validator("probabilities", mode="before")
    @classmethod
    def _probabilities(cls, values: Any) -> dict[str, Decimal]:
        if not isinstance(values, dict):
            raise ValueError("probabilities must be an object")
        return {key: _unit(value) for key, value in values.items()}

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: Any) -> Decimal | None:
        return None if value is None else _unit(value)


def _unit(value: Any) -> Decimal:
    if isinstance(value, str):
        raise ValueError("a probability must be a number")
    result = parse_decimal(value)
    if not Decimal("0") <= result <= Decimal("1"):
        raise ValueError("a probability must be between 0 and 1")
    return result


class DecisionUsage(_Frozen):
    """Reported token counts; ``None`` means the provider did not report it, never zero."""

    input_tokens: int | None = Field(default=None, ge=0, strict=True)
    output_tokens: int | None = Field(default=None, ge=0, strict=True)

    @property
    def reported(self) -> bool:
        return self.input_tokens is not None and self.output_tokens is not None


class DecisionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    answers: dict[str, DecisionAnswer] | None = None
    raw_answers: Any = None  # the ``answers`` value exactly as received, for storage
    failure: DecisionFailure | None = None
    http_status: int | None = None
    answering_model: str | None = None
    usage: DecisionUsage = Field(default_factory=DecisionUsage)
    provider_request_id: str | None = None


def exact_json(value: Any) -> str:
    """Compact JSON that writes decimals with their exact received digits (``0.70000`` stays ``0.70000``)."""
    if value is None or isinstance(value, bool):
        return json.dumps(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite number")
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return "{" + ",".join(f"{json.dumps(str(key), ensure_ascii=False)}:{exact_json(item)}"
                              for key, item in value.items()) + "}"
    if isinstance(value, list | tuple):
        return "[" + ",".join(exact_json(item) for item in value) + "]"
    raise TypeError(f"cannot store {type(value).__name__} as JSON")
