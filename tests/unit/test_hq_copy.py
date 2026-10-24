"""HQ copy: money display, quotes, times, reason mapping, and redaction-safe plain words (spec §D)."""

import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trade_graph.api import hq_copy
from trade_graph.api.hq_contract import ALLOWED_PATH_PATTERN, ISO_MS_PATTERN, contract_keys
from trade_graph.api.security import _PRIVATE_KEYS, redact
from trade_graph.application.paper_service import DEFAULT_SCHEDULES

JARGON = ("container", "worker process", "reconciliation", "inference", "native", "token", "quota admission")
CASE_SENSITIVE_JARGON = ("MANAGE_ONLY", "SMA")


def _strings(value, *, values_only: bool):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if not values_only:
                yield from _strings(key, values_only=values_only)
            yield from _strings(item, values_only=values_only)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _strings(item, values_only=values_only)


def _copy_constants():
    return {name: value for name, value in vars(hq_copy).items()
            if name.isupper() and not name.startswith("_") and isinstance(value, (str, dict, tuple))}


@pytest.mark.parametrize(("amount", "currency", "display"), [
    ("8939.7", "EUR", "€8,939.70"),
    ("10000", "USD", "$10,000.00"),
    ("0.125", "GBP", "£0.12"),
    ("0.135", "GBP", "£0.14"),
    ("-12.4", "EUR", "−€12.40"),
    ("1234567.891", "CHF", "1,234,567.89 CHF"),
    ("-0.004", "EUR", "€0.00"),
])
def test_money_display_rounds_half_even_groups_and_signs(amount, currency, display):
    money = hq_copy.money(Decimal(amount), currency)
    assert money["display"] == display
    assert money["amount"] == str(Decimal(amount).normalize()) if "E" not in str(Decimal(amount).normalize()) \
        else money["amount"]
    assert hq_copy.money(None, "EUR") is None
    assert hq_copy.money_segment(amount, currency) == {"t": "money", **money}


def test_money_keeps_the_unrounded_canonical_amount():
    assert hq_copy.money(Decimal("8939.7000"), "EUR")["amount"] == "8939.7"
    assert hq_copy.money("0", "USD") == {"amount": "0", "currency": "USD", "display": "$0.00"}
    assert hq_copy.quantity(Decimal("0.500000000")) == "0.5"
    assert hq_copy.quantity(Decimal("1.123456789")) == "1.12345679"


def test_quote_cuts_at_a_word_boundary_and_redacts_first():
    text = " ".join(["momentum"] * 60)
    quote = hq_copy.quote(text)
    assert quote["label"] == "In their words"
    assert len(quote["text"]) <= 280 and quote["text"].endswith("…")
    assert quote["text"].removesuffix("…").split(" ")[-1] == "momentum"
    assert hq_copy.quote("Short and complete.") == {"label": "In their words", "text": "Short and complete."}
    assert hq_copy.quote(None) is None and hq_copy.quote("") is None
    secret = hq_copy.quote("api_key=sk-live-123 then more words")
    assert "sk-live" not in secret["text"]
    assert len(hq_copy.quote("word " * 100, 160)["text"]) <= 160


def test_iso_ms_is_utc_with_milliseconds():
    moment = datetime(2026, 10, 9, 15, 59, 10, 506789, tzinfo=timezone(timedelta(hours=2)))
    assert hq_copy.iso_ms(moment) == "2026-10-09T13:59:10.506Z"
    assert hq_copy.iso_ms("2026-10-09T13:59:10.506789Z") == "2026-10-09T13:59:10.506Z"
    assert re.fullmatch(ISO_MS_PATTERN, hq_copy.time("2026-01-01T00:00:00Z", "clock")["at"])
    with pytest.raises(ValueError):
        hq_copy.iso_ms(datetime(2026, 1, 1))


def test_plain_text_renders_segments_for_the_review_prompt():
    text = hq_copy.says("Last sign of life ", hq_copy.time(datetime(2026, 1, 1, 21, 3, tzinfo=UTC), "clock"),
                        ", silent for ", hq_copy.duration(245), " · ", hq_copy.money_segment("12.5", "EUR"), ".")
    assert hq_copy.plain(text) == "Last sign of life 21:03 UTC, silent for 4 min · €12.50."
    assert hq_copy.says("a", "b", hq_copy.text("c"), hq_copy.text("d", code="x")) == [
        {"t": "text", "v": "abc"}, {"t": "text", "v": "d", "code": "x"}]
    assert hq_copy.join(hq_copy.says("One."), None, [], hq_copy.says("Two.")) == [{"t": "text", "v": "One. Two."}]


@pytest.mark.parametrize(("raw", "words"), [
    ("model quota_reserve: below threshold", "the AI allowance was below the line"),
    ("model credentials: expired", "the AI sign-in needs attention"),
    ("model unsupported: gone", "the AI model wasn't available"),
    ("model rate_limit: 429", "the AI allowance ran out"),
    ("model temporary: retry", "a temporary AI error"),
    ("model timeout_uncertain: no answer", "the AI didn't answer in time, and whether it finished is unknown"),
    ("model refusal: declined", "the AI declined"),
    ("model truncation: cut", "the AI's answer was cut off"),
    ("model validation: bad json", "the AI's answer didn't pass the checks"),
    ("decision cites evidence outside its persisted context", "its answer cited information it wasn't given"),
    ("pause changed after snapshot", "something changed while it was working (for example a pause), so its answer "
                                     "was set aside"),
    ("owner halt prevents new exposure", "a pause was on, so its answer was set aside"),
    ("attempt limit or root step limit", "it used all its tries"),
    ("active mandate expired", "its trading permission had expired"),
    ("stale or missing quote for BTC/USD", "prices were too old to place a practice order"),
    ("market feed blocks new exposure", "prices were too old to place a practice order"),
    ("unresolved execution blocks new exposure", "an earlier practice order was still being checked"),
    ("room exhausted", "this job reached its own cost limit"),
    ("BudgetExhausted", "this job reached its own cost limit"),
    ("ValueError", "a technical error"),
    ("StaleState", "a technical error"),
    ("something odd happened", "a problem HQ can't describe in plain words"),
])
def test_reason_mapping_rows(raw, words):
    assert hq_copy.reason_plain(raw) == words
    segment = hq_copy.reason_segment(raw)
    assert segment["v"] == words and segment["code"] == raw
    assert hq_copy.reason_plain(None) is None
    live = hq_copy.reason_plain(raw, live=True)
    assert "practice" not in live
    assert hq_copy.reason_segment(raw, live=True) == {"t": "text", "v": live, "code": raw}


@pytest.mark.parametrize(("raw", "words"), [
    ("stale or missing quote for BTC/USD", "prices were too old to place an order"),
    ("market feed blocks new exposure", "prices were too old to place an order"),
    ("unresolved execution blocks new exposure", "an earlier order was still being checked"),
    ("model rate_limit: 429", "the AI allowance ran out"),
    ("room exhausted", hq_copy.REASON["budget"]),
])
def test_live_reason_rows_never_say_practice(raw, words):
    assert hq_copy.reason_plain(raw, live=True) == words


def test_live_twins_never_say_practice():
    twins = {name: value for name, value in vars(hq_copy).items() if name.startswith("LIVE_") and name.isupper()}
    assert set(twins) >= {"LIVE_ATTENTION", "LIVE_NOW", "LIVE_PROBLEM_PREFIX", "LIVE_STORY", "LIVE_ROOM_JOB",
                          "LIVE_ROOM_NOW", "LIVE_REASON", "LIVE_ECON", "LIVE_MONEY"}
    strings = list(_strings(twins, values_only=True))
    assert strings and not [value for value in strings if "practice" in value.lower()]
    assert hq_copy.problem_prefix("dispatch:", live=True)[0] == "couldn't send orders"
    assert hq_copy.problem_prefix("dispatch:", live=False)[0] == "couldn't send practice orders"
    assert hq_copy.problem_prefix("feed:", live=True) == hq_copy.PROBLEM_PREFIX["feed:"]
    assert "practice" not in hq_copy.room_job("trader", live=True)
    assert hq_copy.room_job("research", live=True) == hq_copy.ROOM["research"][2]
    assert hq_copy.econ("nope", live=True) == hq_copy.ECON["other"]


def test_cadence_names():
    assert [hq_copy.cadence(value) for value in (14400, 86400, 302400, 604800, 1800, None)] == [
        "every 4 hours", "daily", "every 3½ days", "weekly", "every 30 min", None]


def test_only_research_turns_are_daily_by_default():
    # The cadence comes from each schedule's interval, never from the department's name.
    cadences = {role: hq_copy.cadence(seconds) for role, seconds in DEFAULT_SCHEDULES.items()}
    assert cadences == {"trader": "every 4 hours", "research": "daily", "learning": "every 3½ days",
                        "leader": "weekly", "optimisation": "weekly"}
    assert not [value for value in _strings(_copy_constants(), values_only=True)
                if "daily turn" in value or re.search(r"(?:Learning|Leader|Optimisation).*daily", value)]


def test_every_copy_string_survives_redaction():
    strings = [value for value in _strings(_copy_constants(), values_only=False)]
    assert len(strings) > 300
    # Route paths are links, not copy; the redaction allowlist for /journal lands with the routes (spec B.11).
    strings = [value for value in strings if not re.fullmatch(ALLOWED_PATH_PATTERN, value)]
    changed = [value for value in strings if redact(value) != value]
    assert changed == []


def test_owner_facing_copy_avoids_jargon():
    leaks = []
    for name, value in _copy_constants().items():
        if name in {"SKIP_REASONS", "BARRIER", "RESOURCE_WARNING", "AI_PAUSE_REASON", "MODEL_FAILURE"}:
            values = [item for item in _strings(value, values_only=True)]
        else:
            values = list(_strings(value, values_only=True))
        for text in values:
            lowered = text.lower()
            leaks += [(name, word, text) for word in JARGON if word in lowered]
            leaks += [(name, word, text) for word in CASE_SENSITIVE_JARGON if re.search(rf"\b{word}\b", text)]
    assert leaks == []


def test_no_contract_key_is_redacted():
    keys = contract_keys()
    assert not {key for key in keys if key.lower() in _PRIVATE_KEYS}
    assert not {key for key in keys if key.lower().endswith(("_api_key", "_password", "_secret"))}
    assert redact({key: "x" for key in keys}) == {key: "x" for key in keys}
