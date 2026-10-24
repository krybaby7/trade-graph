"""T44: Jev's decision contract and adapter with scripted replies only. No key and no network call."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from trade_graph.adapters.decisions.jev import (
    JEV_ENDPOINT,
    JEV_KEY_FILENAME,
    JEV_PINNED_MODEL,
    JEV_PRICE_CARD,
    JEV_PROVIDER,
    JevAdapter,
    JevKey,
    PinnedJevHttp,
    ScriptedJevHttp,
    build_body,
    load_jev_key,
)
from trade_graph.adapters.models.transport import ProviderHttpResponseError, provider_payload
from trade_graph.contracts.decisions import (
    ChoiceQuestion,
    DecisionRequest,
    NoulQuestion,
    ScoreQuestion,
    exact_json,
)
from trade_graph.contracts.models import ModelUsage
from trade_graph.kernel.pricing import usage_cost

FAKE_KEY = "fixture-only-not-a-real-key-0000"


def _request(model: str = JEV_PINNED_MODEL) -> DecisionRequest:
    return DecisionRequest(model=model, state={"title": "Synthetic headline", "publisher": "Synthetic"}, questions={
        "concerns": ChoiceQuestion(text="Which watched coin?", options=("BTC", "kraken", "none")),
        "holder_wants_to_know": NoulQuestion(text="Would a holder want to know within the hour?"),
        "severity": ScoreQuestion(text="How severe?", levels=3),
    })


def _answers() -> dict:
    return {
        "concerns": {"probabilities": {"BTC": Decimal("0.7"), "kraken": Decimal("0.2"), "none": Decimal("0.1")},
                     "confidence": Decimal("0.6")},
        "holder_wants_to_know": {"probabilities": {"yes": Decimal("0.8"), "no": Decimal("0.2")}},
        "severity": {"probabilities": {"1": Decimal("0.1"), "2": Decimal("0.3"), "3": Decimal("0.6")},
                     "confidence": Decimal("0.5")},
    }


def _reply(**overrides) -> dict:
    reply = {"id": "synthetic-1", "model": "jev-1.13.0", "answers": _answers(),
             "usage": {"input_tokens": 420, "output_tokens": 0}}
    reply.update(overrides)
    return {key: value for key, value in reply.items() if value is not ...}


def _call(*script, key: JevKey | None = None):
    transport = ScriptedJevHttp(list(script))
    return JevAdapter(transport, key=key).call(_request(), timeout_seconds=5), transport


def test_wire_body_carries_state_typed_questions_and_the_pinned_model():
    body = build_body(_request())
    assert body == {
        "model": "jev-1.13.0",
        "state": {"title": "Synthetic headline", "publisher": "Synthetic"},
        "questions": {
            "concerns": {"type": "choice", "question": "Which watched coin?", "options": ["BTC", "kraken", "none"]},
            "holder_wants_to_know": {"type": "noul", "question": "Would a holder want to know within the hour?"},
            "severity": {"type": "score", "question": "How severe?", "levels": 3},
        },
    }


def test_success_returns_answers_usage_and_the_answering_model():
    result, transport = _call(_reply())
    assert result.ok and result.failure is None and result.http_status == 200
    assert result.answering_model == "jev-1.13.0"
    assert result.usage.input_tokens == 420 and result.usage.output_tokens == 0 and result.usage.reported
    assert result.provider_request_id == "synthetic-1"
    assert result.answers["concerns"].probabilities["BTC"] == Decimal("0.7")
    assert result.answers["concerns"].confidence == Decimal("0.6")
    assert result.answers["holder_wants_to_know"].probabilities == {"yes": Decimal("0.8"), "no": Decimal("0.2")}
    assert result.answers["severity"].probabilities["3"] == Decimal("0.6")
    assert transport.calls[0]["url"] == JEV_ENDPOINT
    assert "authorization" not in {name.lower() for name in transport.calls[0]["headers"]}


def test_answers_are_kept_exactly_as_received():
    reply = _reply()
    reply["answers"]["concerns"]["probabilities"]["BTC"] = Decimal("0.70000")
    result, _ = _call(reply)
    text = exact_json(result.raw_answers)
    assert '"BTC":0.70000' in text
    assert provider_payload(text.encode()) == reply["answers"]


@pytest.mark.parametrize(("status", "failure"), [
    (400, "invalid_request"), (401, "credentials"), (413, "too_large"), (415, "unsupported_media"),
    (422, "unprocessable"), (429, "rate_limit"), (529, "overloaded"), (500, "server_error"),
])
def test_http_errors_are_classified_and_usage_is_unknown_unless_reported(status, failure):
    result, _ = _call(ProviderHttpResponseError(status, {}))
    assert not result.ok and result.failure == failure and result.http_status == status
    assert result.answers is None and not result.usage.reported
    assert result.usage.input_tokens is None


def test_an_error_body_that_reports_usage_keeps_it():
    result, _ = _call(ProviderHttpResponseError(422, {"usage": {"input_tokens": 37, "output_tokens": 0},
                                                      "model": "jev-1.13.0"}))
    assert result.failure == "unprocessable" and result.usage.reported and result.usage.input_tokens == 37


def test_a_timeout_is_uncertain_and_never_free():
    result, _ = _call(TimeoutError("provider transport outcome uncertain"))
    assert result.failure == "timeout_uncertain" and result.http_status is None
    assert not result.usage.reported


def test_a_refused_response_is_unresolved():
    result, _ = _call(ValueError("provider response byte bound exceeded"))
    assert result.failure == "refused_response" and not result.usage.reported


@pytest.mark.parametrize("usage", [..., None, {}, {"input_tokens": 420}, {"output_tokens": 0},
                                   {"input_tokens": "420", "output_tokens": 0},
                                   {"input_tokens": -1, "output_tokens": 0},
                                   {"input_tokens": True, "output_tokens": 0}])
def test_missing_or_unusable_usage_keeps_answers_but_leaves_usage_unreported(usage):
    result, _ = _call(_reply(usage=usage))
    assert result.ok and result.answers is not None
    assert not result.usage.reported


def test_an_unexpected_answering_model_is_flagged_and_not_trusted():
    result, _ = _call(_reply(model="jev-1.14.0"))
    assert not result.ok and result.failure == "unexpected_model"
    assert result.answering_model == "jev-1.14.0" and result.usage.reported
    assert result.raw_answers == _answers()


def test_a_reply_without_a_model_is_unexpected():
    result, _ = _call(_reply(model=...))
    assert result.failure == "unexpected_model" and result.answering_model is None


def _malformed(mutate):
    reply = _reply()
    mutate(reply)
    return reply


@pytest.mark.parametrize("mutate", [
    lambda r: r.pop("answers"),
    lambda r: r.update(answers=["not", "an", "object"]),
    lambda r: r["answers"].pop("severity"),
    lambda r: r["answers"].update(extra={"probabilities": {"yes": Decimal("1"), "no": Decimal("0")}}),
    lambda r: r["answers"]["concerns"]["probabilities"].pop("none"),
    lambda r: r["answers"]["concerns"]["probabilities"].update(ETH=Decimal("0")),
    lambda r: r["answers"]["concerns"]["probabilities"].update(BTC=Decimal("1.7")),
    lambda r: r["answers"]["concerns"]["probabilities"].update(BTC=Decimal("-0.1")),
    lambda r: r["answers"]["concerns"]["probabilities"].update(BTC="0.7"),
    lambda r: r["answers"]["concerns"]["probabilities"].update(BTC=True),
    lambda r: r["answers"]["concerns"]["probabilities"].update(BTC=Decimal("0.2")),
    lambda r: r["answers"]["concerns"].update(confidence=Decimal("2")),
    lambda r: r["answers"]["concerns"].update(reasoning="free text is not part of a Jev answer"),
    lambda r: r["answers"]["holder_wants_to_know"].update(probabilities={"maybe": Decimal("1")}),
    lambda r: r["answers"]["severity"]["probabilities"].update({"4": Decimal("0")}),
])
def test_malformed_answers_are_refused_but_the_reply_is_kept(mutate):
    reply = _malformed(mutate)
    result, _ = _call(reply)
    assert not result.ok and result.failure == "malformed_answers" and result.answers is None
    assert result.usage.reported  # the call was still billed and gets a receipt
    assert result.raw_answers == reply.get("answers")


def test_rounded_probabilities_that_sum_close_to_one_are_accepted_as_received():
    reply = _reply()
    reply["answers"]["concerns"]["probabilities"] = {"BTC": Decimal("0.333"), "kraken": Decimal("0.333"),
                                                     "none": Decimal("0.333")}
    result, _ = _call(reply)
    assert result.ok and result.answers["concerns"].probabilities["none"] == Decimal("0.333")


@pytest.mark.parametrize("alias", ["jev-latest", "jev-preview", "jev", ""])
def test_moving_aliases_are_refused_before_anything_is_sent(alias):
    transport = ScriptedJevHttp([_reply()])
    with pytest.raises((ValueError, ValidationError)):
        JevAdapter(transport).call(_request(alias), timeout_seconds=5)
    assert transport.calls == []


def test_questions_are_bounded_typed_and_named():
    with pytest.raises(ValidationError):
        ChoiceQuestion(text="one option", options=("only",))
    with pytest.raises(ValidationError):
        ChoiceQuestion(text="repeated", options=("a", "a"))
    with pytest.raises(ValidationError):
        ChoiceQuestion(text="too many", options=tuple(f"c{n}" for n in range(256)))
    assert len(ChoiceQuestion(text="largest", options=tuple(f"c{n}" for n in range(255))).options) == 255
    for levels in (1, 11):
        with pytest.raises(ValidationError):
            ScoreQuestion(text="levels", levels=levels)
    with pytest.raises(ValidationError):
        DecisionRequest(model=JEV_PINNED_MODEL, state={}, questions={"Bad Name": NoulQuestion(text="x")})
    with pytest.raises(ValidationError):
        DecisionRequest(model=JEV_PINNED_MODEL, state={}, questions={})
    with pytest.raises(ValidationError):
        NoulQuestion(text="x", options=("yes", "no"))


def test_the_dated_price_card_charges_input_only():
    card = JEV_PRICE_CARD
    assert (card.provider, card.model, card.endpoint, card.currency) == (JEV_PROVIDER, "jev-1.13.0", JEV_ENDPOINT,
                                                                         "USD")
    assert (card.input_per_million, card.output_per_million) == (Decimal("0.042"), Decimal("0"))
    assert (card.verified_at, card.effective_at, card.source_id) == ("2026-10-15", "2026-10-15", "S40")
    usage = ModelUsage(uncached_input_tokens=1_000_000, billed_output_tokens=5_000)
    assert usage_cost(card, usage) == Decimal("0.042")
    unresolved = ModelUsage(uncached_input_tokens=0, billed_output_tokens=0,
                            unreported_fields=["uncached_input_tokens", "billed_output_tokens"])
    with pytest.raises(ValueError, match="unresolved"):
        usage_cost(card, unresolved)


def test_the_pinned_transport_only_posts_to_the_official_endpoint():
    inner = ScriptedJevHttp([_reply()])
    pinned = PinnedJevHttp(inner)
    with pytest.raises(ValueError, match="endpoint"):
        pinned.post_json("https://api.typesafe.ai.example.net/v1/systemone", {}, {})
    assert inner.calls == []
    assert pinned.post_json(JEV_ENDPOINT, {"model": "jev-1.13.0"}, {}) == _reply()
    assert pinned.synthetic and inner.synthetic  # wrapping a script never makes it look like a real call
    assert PinnedJevHttp().synthetic is False


def _private_key_file(tmp_path: Path, content: str = FAKE_KEY, *, file_mode=0o600, dir_mode=0o700) -> Path:
    directory = tmp_path / "typesafe"
    directory.mkdir()
    path = directory / JEV_KEY_FILENAME
    path.write_text(content + "\n")
    os.chmod(path, file_mode)
    os.chmod(directory, dir_mode)
    return directory


def test_the_key_is_read_only_from_a_private_owner_file_and_never_shown(tmp_path):
    key = load_jev_key(_private_key_file(tmp_path))
    assert FAKE_KEY not in repr(key) and FAKE_KEY not in str(key)
    result, transport = _call(_reply(), key=key)
    assert result.ok
    assert transport.calls[0]["headers"]["authorization"] == f"Bearer {FAKE_KEY}"
    assert FAKE_KEY not in result.model_dump_json()
    assert FAKE_KEY not in str(transport.calls[0]["body"])


@pytest.mark.parametrize(("file_mode", "dir_mode"), [(0o640, 0o700), (0o604, 0o700), (0o600, 0o750),
                                                     (0o600, 0o705)])
def test_a_key_file_others_can_read_is_refused(tmp_path, file_mode, dir_mode):
    directory = _private_key_file(tmp_path, file_mode=file_mode, dir_mode=dir_mode)
    with pytest.raises(PermissionError) as raised:
        load_jev_key(directory)
    assert FAKE_KEY not in str(raised.value)


def test_a_linked_missing_or_unusable_key_file_is_refused(tmp_path):
    with pytest.raises((FileNotFoundError, PermissionError)):
        load_jev_key(tmp_path / "absent")
    directory = _private_key_file(tmp_path)
    target = directory / JEV_KEY_FILENAME
    os.rename(target, directory / "real.key")
    os.symlink(directory / "real.key", target)
    with pytest.raises((PermissionError, OSError)):
        load_jev_key(directory)
    for content in ("short", "has space inside the key value", "x" * 5000):
        other = tmp_path / f"case-{len(content)}"
        other.mkdir()
        (other / "typesafe").mkdir()
        directory = other / "typesafe"
        (directory / JEV_KEY_FILENAME).write_text(content)
        os.chmod(directory / JEV_KEY_FILENAME, 0o600)
        os.chmod(directory, 0o700)
        with pytest.raises(PermissionError) as raised:
            load_jev_key(directory)
        assert content not in str(raised.value)
