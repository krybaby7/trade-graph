"""TypeSafe's Jev decision model over plain HTTPS (T44, D137, source S40).

``POST https://api.typesafe.ai/v1/systemone`` takes a ``model``, a ``state`` and named typed ``questions``
(``noul`` yes/no, ``choice`` up to 255 options, ``score`` 2 to 10 levels) and replies with probabilities, the
answering ``model`` and ``usage``. No text is written, so Jev runs no department. The version is pinned
(``jev-1.13.0``): the ``jev-latest`` and ``jev-preview`` aliases move and are refused.

The wire field names beyond those S40 records (``question``, ``options``, ``levels``, ``probabilities``,
``confidence``, ``id``) and the ``Authorization: Bearer`` header are this adapter's reading of the published
API; the first credentialed call (step L2) confirms them. A reply in another shape fails closed as
``malformed_answers`` and still gets its receipt.

The key is read only from the owner's private file and is sent only in the request header. It never enters
a body, a record, a log or model context.
"""

from __future__ import annotations

import copy
import errno
import json
import os
import re
import stat
import uuid
from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from trade_graph.adapters.models.transport import (
    HttpxProviderHttp,
    ProviderHttp,
    ProviderHttpResponseError,
    provider_request_bytes,
)
from trade_graph.contracts.decisions import (
    ChoiceQuestion,
    DecisionAnswer,
    DecisionFailure,
    DecisionRequest,
    DecisionResult,
    DecisionUsage,
    NoulQuestion,
)
from trade_graph.contracts.models import PriceCard

JEV_PROVIDER = "typesafe"
JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
JEV_PINNED_MODEL = "jev-1.13.0"
JEV_MODEL_PATTERN = r"^jev-\d{1,3}\.\d{1,3}\.\d{1,3}$"
JEV_MAX_INPUT_TOKENS = 64_000  # S40: the per-request limit; a reservation holds the largest possible call
JEV_MAX_RESPONSE_BYTES = 262_144
JEV_KEY_FILENAME = "typesafe.key"
_KEY_BYTES = (16, 4096)
_PROBABILITY_SUM = (Decimal("0.99"), Decimal("1.01"))

# Dated price record from TypeSafe's official models page, read 2026-10-15 (S40). Output is free. A newer
# card is a new record with a new id in the owner's settings; this one is never edited.
JEV_PRICE_CARD = PriceCard(
    price_card_id="typesafe-jev-1.13.0-2026-10-15",
    provider=JEV_PROVIDER,
    model=JEV_PINNED_MODEL,
    endpoint=JEV_ENDPOINT,
    currency="USD",
    input_per_million="0.042",
    output_per_million="0",
    effective_at="2026-10-15",
    verified_at="2026-10-15",
    source_id="S40",
    tier="standard",
    context_band="request<=64k",
)


class JevKey:
    """The owner's Jev key. Its text is never shown, logged or serialised."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def __repr__(self) -> str:
        return "JevKey(<redacted>)"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("the Jev key cannot be serialised")

    def header(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self._value}"}


def load_jev_key(directory: Path | str) -> JevKey:
    """Read ``typesafe.key`` from the owner's private folder (mode 0700, file 0600, owned by this user)."""
    folder = Path(directory).expanduser().absolute()
    if ".." in folder.parts:
        raise PermissionError("unsafe Jev key folder")
    try:
        directory_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        raise FileNotFoundError("no Jev key folder") from None
    except OSError as exc:
        raise PermissionError("unsafe Jev key folder") from exc
    try:
        info = os.fstat(directory_fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise PermissionError("the Jev key folder must be private to its owner (mode 0700)")
        try:
            fd = os.open(JEV_KEY_FILENAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=directory_fd)
        except FileNotFoundError:
            raise FileNotFoundError("no Jev key file") from None
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise PermissionError("the Jev key file cannot be a link") from None
            raise PermissionError("unsafe Jev key file") from None
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) & 0o077):
                raise PermissionError("the Jev key file must be private to its owner (mode 0600)")
            raw = os.read(fd, _KEY_BYTES[1] + 3)
        finally:
            os.close(fd)
    finally:
        os.close(directory_fd)
    value = raw.removesuffix(b"\n").removesuffix(b"\r")
    if not _KEY_BYTES[0] <= len(value) <= _KEY_BYTES[1] or any(byte < 33 or byte > 126 for byte in value):
        raise PermissionError("the Jev key file does not hold one key of printable characters")
    return JevKey(value.decode("ascii"))


def build_body(request: DecisionRequest) -> dict[str, Any]:
    questions: dict[str, dict[str, Any]] = {}
    for name, question in request.questions.items():
        if isinstance(question, NoulQuestion):
            questions[name] = {"type": "noul", "question": question.text}
        elif isinstance(question, ChoiceQuestion):
            questions[name] = {"type": "choice", "question": question.text, "options": list(question.options)}
        else:
            questions[name] = {"type": "score", "question": question.text, "levels": question.levels}
    return {"model": request.model, "state": copy.deepcopy(request.state), "questions": questions}


def failure_for_status(status: int) -> DecisionFailure:
    if status in {401, 403}:
        return "credentials"
    if status in {408, 504}:
        return "timeout_uncertain"
    named: dict[int, DecisionFailure] = {413: "too_large", 415: "unsupported_media", 422: "unprocessable",
                                         429: "rate_limit", 529: "overloaded"}
    if status in named:
        return named[status]
    return "server_error" if 500 <= status < 600 else "invalid_request"


def _usage(value: Any) -> DecisionUsage:
    if not isinstance(value, dict):
        return DecisionUsage()

    def count(name: str) -> int | None:
        item = value.get(name)
        return item if type(item) is int and item >= 0 else None

    return DecisionUsage(input_tokens=count("input_tokens"), output_tokens=count("output_tokens"))


def _text(value: Any, limit: int = 200) -> str | None:
    return value if isinstance(value, str) and 1 <= len(value) <= limit else None


def _answers(raw: Any, request: DecisionRequest) -> dict[str, DecisionAnswer] | None:
    if not isinstance(raw, dict) or set(raw) != set(request.questions):
        return None
    answers = {}
    for name, question in request.questions.items():
        item = raw[name]
        if not isinstance(item, dict) or "probabilities" not in item or set(item) - {"probabilities", "confidence"}:
            return None
        probabilities = item["probabilities"]
        if not isinstance(probabilities, dict) or set(probabilities) != set(question.answer_options()):
            return None
        try:
            answer = DecisionAnswer.model_validate(item)
        except ValidationError:
            return None
        total = sum(answer.probabilities.values(), Decimal("0"))
        if not _PROBABILITY_SUM[0] <= total <= _PROBABILITY_SUM[1]:
            return None
        answers[name] = answer
    return answers


def parse_reply(payload: Any, request: DecisionRequest) -> DecisionResult:
    if not isinstance(payload, dict):
        return DecisionResult(ok=False, failure="malformed_answers", http_status=200)
    raw = payload.get("answers")
    answers = _answers(raw, request)
    model = _text(payload.get("model"), 64)
    common = {"raw_answers": raw, "http_status": 200, "answering_model": model, "usage": _usage(payload.get("usage")),
              "provider_request_id": _text(payload.get("id"))}
    if model != request.model:
        return DecisionResult(ok=False, failure="unexpected_model", answers=answers, **common)
    if answers is None:
        return DecisionResult(ok=False, failure="malformed_answers", **common)
    return DecisionResult(ok=True, answers=answers, **common)


def pinned_model(model: str) -> str:
    if not isinstance(model, str) or not re.fullmatch(JEV_MODEL_PATTERN, model):
        raise ValueError("a pinned Jev version is required; the moving aliases are refused")
    return model


class JevAdapter:
    def __init__(self, transport: ProviderHttp, *, key: JevKey | None = None) -> None:
        self.transport, self._key = transport, key

    @property
    def synthetic(self) -> bool:
        return bool(getattr(self.transport, "synthetic", False))

    def prepare(self, request: DecisionRequest) -> dict[str, Any]:
        """The exact body to send; refuses an unpinned model or an oversized request before anything is sent."""
        pinned_model(request.model)
        body = build_body(request)
        provider_request_bytes(body)
        return body

    def call(self, request: DecisionRequest, *, timeout_seconds: float) -> DecisionResult:
        body = self.prepare(request)
        headers = {"accept": "application/json", **(self._key.header() if self._key is not None else {})}
        try:
            payload = self.transport.post_json(JEV_ENDPOINT, body, headers, timeout_seconds=timeout_seconds)
        except ProviderHttpResponseError as error:
            facts = error.usage_payload if isinstance(error.usage_payload, dict) else {}
            status = getattr(error, "status_code", None)
            return DecisionResult(ok=False, failure=failure_for_status(status) if status else "invalid_request",
                                  http_status=status, usage=_usage(facts.get("usage")),
                                  answering_model=_text(facts.get("model"), 64),
                                  provider_request_id=_text(facts.get("id")))
        except (TimeoutError, OSError):
            return DecisionResult(ok=False, failure="timeout_uncertain")
        except ValueError:
            return DecisionResult(ok=False, failure="refused_response")
        return parse_reply(payload, request)


class PinnedJevHttp:
    """The network transport: HTTPS POST to the one official endpoint, bounded, no redirects."""

    def __init__(self, inner: ProviderHttp | None = None, *, timeout: float = 10, proxy: str | None = None,
                 trust_env: bool = True) -> None:
        self.inner = inner if inner is not None else HttpxProviderHttp(
            timeout, maximum_response_bytes=JEV_MAX_RESPONSE_BYTES, proxy=proxy, trust_env=trust_env)
        self.synthetic = bool(getattr(self.inner, "synthetic", False))

    def post_json(self, url: str, body: dict[str, Any], headers: Mapping[str, str], *,
                  timeout_seconds: float | None = None) -> dict[str, Any]:
        if url != JEV_ENDPOINT:
            raise ValueError("Jev calls go only to the official TypeSafe endpoint")
        return self.inner.post_json(url, body, headers, timeout_seconds=timeout_seconds)


Script = dict[str, Any] | BaseException


class ScriptedJevHttp:
    """Scripted replies for tests and fixture runs. Always synthetic; never a real Jev or paid call."""

    synthetic = True

    def __init__(self, script: list[Script] | None = None, *, on_call: Callable[[dict], None] | None = None,
                 responder: Callable[[dict], Script] | None = None) -> None:
        self.script, self.on_call, self.responder = list(script or []), on_call, responder
        self.calls: list[dict[str, Any]] = []

    @classmethod
    def from_fixture(cls, path: Path | str, *, on_call: Callable[[dict], None] | None = None) -> ScriptedJevHttp:
        return cls.from_script(json.loads(Path(path).read_text()), on_call=on_call)

    @classmethod
    def from_script(cls, document: dict[str, Any], *,
                    on_call: Callable[[dict], None] | None = None) -> ScriptedJevHttp:
        if document.get("evidence") != "synthetic_fixture" or not isinstance(document.get("by_link"), dict):
            raise ValueError("a scripted Jev file must be a labelled synthetic fixture with replies by link")
        model, tokens, by_link = str(document["model"]), int(document["input_tokens"]), document["by_link"]

        def respond(body: dict) -> Script:
            link = body["state"]["headline"]["link"]
            if link not in by_link:
                raise LookupError("the scripted Jev file has no reply for this headline")
            spec = by_link[link]
            answers = {}
            for name, question in body["questions"].items():
                options = (question["options"] if question["type"] == "choice" else ["yes", "no"]
                           if question["type"] == "noul" else [str(level) for level in
                                                               range(1, question["levels"] + 1)])
                given = spec.get(name, {})
                if set(given) - set(options):
                    raise ValueError("a scripted reply names an option the request does not offer")
                answers[name] = {"probabilities": {option: Decimal(given.get(option, "0")) for option in options}}
            return {"id": f"synthetic-{uuid.uuid4().hex}", "model": model, "answers": answers,
                    "usage": {"input_tokens": tokens, "output_tokens": 0}}

        return cls(on_call=on_call, responder=respond)

    def post_json(self, url: str, body: dict[str, Any], headers: Mapping[str, str], *,
                  timeout_seconds: float | None = None) -> dict[str, Any]:
        self.calls.append({"url": url, "body": copy.deepcopy(body), "headers": dict(headers)})
        if self.on_call is not None:
            self.on_call(body)
        if self.responder is not None:
            entry = self.responder(body)
        elif self.script:
            entry = self.script.pop(0)
        else:
            raise RuntimeError("scripted Jev replies exhausted")
        if isinstance(entry, BaseException):
            raise entry
        return copy.deepcopy(entry)
