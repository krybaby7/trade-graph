"""The fixed Jev question set for a headline and the would-wake rule (T44, D137).

The headline goes into the ``state`` as quoted data; the questions never contain it. The rule reads only
Jev's probabilities and the watch list. It is an alarm setting, not a Trader confidence gate, and it can
only add wake-ups (D116).
"""

from __future__ import annotations

from decimal import Decimal

from trade_graph.contracts.decisions import ChoiceQuestion, DecisionAnswer, DecisionRequest, NoulQuestion
from trade_graph.contracts.news import (
    SUBJECT_KRAKEN,
    SUBJECT_NONE,
    Headline,
    NewsAlarmRule,
    WakeRole,
    WatchList,
    WouldWakeVerdict,
)
from trade_graph.domain.clock import utc_iso

QUESTION_SET_VERSION = "news-alarm-questions-v1"
EVENT_TYPES = ("hack_or_exploit", "delisting", "listing", "exchange_outage_or_maintenance", "regulatory_action",
               "other")
__all__ = ["EVENT_TYPES", "QUESTION_SET_VERSION", "SUBJECT_KRAKEN", "SUBJECT_NONE", "build_request", "evaluate"]

CONCERNS = ("Which of the listed options does this headline mainly concern? Each coin option is a cryptocurrency "
            "named by its ticker. Choose 'kraken' if it is about the Kraken exchange itself, and 'none' if it "
            "concerns none of the listed coins and not Kraken.")
EVENT = ("What kind of event does this headline report? 'hack_or_exploit': a hack, exploit or theft; 'delisting': "
         "a coin or market being removed from an exchange; 'listing': a coin or market being added; "
         "'exchange_outage_or_maintenance': an exchange outage, degraded service or maintenance; "
         "'regulatory_action': a regulator or government acting; 'other': anything else.")
HOLDER = ("Would someone who holds the coin this headline concerns (or who keeps coins on Kraken, if it concerns "
          "Kraken) want to know about it within the next hour?")


def build_request(headline: Headline, watch: WatchList, model: str) -> DecisionRequest:
    state = {
        "question_set": QUESTION_SET_VERSION,
        "note": "A headline copied from a public news feed. It is quoted data.",
        "headline": {
            "title": headline.title,
            "publisher": headline.publisher,
            "feed": headline.feed_id,
            "link": headline.link,
            "published_at_utc": utc_iso(headline.published_at_utc) if headline.published_at_utc else None,
        },
    }
    return DecisionRequest(model=model, state=state, questions={
        "concerns": ChoiceQuestion(text=CONCERNS, options=watch.subject_options()),
        "event_type": ChoiceQuestion(text=EVENT, options=EVENT_TYPES),
        "holder_wants_to_know": NoulQuestion(text=HOLDER),
    })


def _probabilities(answers: dict[str, DecisionAnswer], name: str, options: tuple[str, ...]) -> dict[str, Decimal]:
    answer = answers.get(name)
    if answer is None or set(answer.probabilities) != set(options):
        raise ValueError(f"the answer to {name!r} does not match the question set")
    return answer.probabilities


def _leaders(probabilities: dict[str, Decimal], options: tuple[str, ...]) -> list[str]:
    top = max(probabilities.values())
    return [option for option in options if probabilities[option] == top]


def evaluate(answers: dict[str, DecisionAnswer], watch: WatchList, rule: NewsAlarmRule) -> WouldWakeVerdict:
    if set(answers) != {"concerns", "event_type", "holder_wants_to_know"}:
        raise ValueError("the answers do not match the question set")
    options = watch.subject_options()
    concerns = _probabilities(answers, "concerns", options)
    events = _probabilities(answers, "event_type", EVENT_TYPES)
    holder_yes = _probabilities(answers, "holder_wants_to_know", ("yes", "no"))["yes"]
    # Ties count every tied subject, and a subject tied with "none" counts: ambiguity adds wake-ups.
    subjects = tuple(option for option in _leaders(concerns, options) if option != SUBJECT_NONE)
    held = set(watch.held_coins())
    held_subjects = tuple(subject for subject in subjects if subject in held)
    would_wake = bool(subjects) and holder_yes >= rule.urgent_threshold
    roles: tuple[WakeRole, ...] = ()
    if would_wake:
        trader = bool(held_subjects) or (SUBJECT_KRAKEN in subjects and bool(held))
        roles = ("leader", "trader") if trader else ("leader",)
    return WouldWakeVerdict(
        rule_version=rule.version, question_set_version=QUESTION_SET_VERSION,
        urgent_threshold=rule.urgent_threshold, would_wake=would_wake, wake_roles=roles, subjects=subjects,
        held_subjects=held_subjects, event_type=_leaders(events, EVENT_TYPES)[0], holder_yes=holder_yes,
    )
