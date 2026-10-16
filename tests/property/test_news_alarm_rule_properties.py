"""T44 property tests: the would-wake rule can only add wake-ups (D116)."""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from trade_graph.application.news_questions import EVENT_TYPES, evaluate
from trade_graph.contracts.decisions import DecisionAnswer
from trade_graph.contracts.news import NewsAlarmRule, WatchList

COINS = ("BTC", "ETH", "SOL", "EXMPL")
probability = st.integers(min_value=0, max_value=100).map(lambda value: Decimal(value) / 100)


def _answers(watch: WatchList, weights: list[int], yes: Decimal) -> dict[str, DecisionAnswer]:
    options = watch.subject_options()
    total = sum(weights[: len(options)]) or 1
    probabilities = {option: Decimal(weights[index]) / total for index, option in enumerate(options)}
    return {
        "concerns": DecisionAnswer(probabilities=probabilities),
        "event_type": DecisionAnswer(probabilities={name: Decimal("1" if name == "other" else "0")
                                                    for name in EVENT_TYPES}),
        "holder_wants_to_know": DecisionAnswer(probabilities={"yes": yes, "no": 1 - yes}),
    }


watches = st.builds(
    lambda held, listed: WatchList(held=tuple(held), listed=tuple(listed)),
    st.lists(st.sampled_from(COINS), max_size=4, unique=True),
    st.lists(st.sampled_from(COINS), min_size=1, max_size=4, unique=True),
)
weights = st.lists(st.integers(min_value=1, max_value=50), min_size=6, max_size=6)


@settings(max_examples=200, deadline=None)
@given(watch=watches, weights=weights, yes=probability, more=probability)
def test_a_more_urgent_answer_never_removes_a_wake_up(watch, weights, yes, more):
    higher = max(yes, more)
    rule = NewsAlarmRule()
    before = evaluate(_answers(watch, weights, yes), watch, rule)
    after = evaluate(_answers(watch, weights, higher), watch, rule)
    assert set(before.wake_roles) <= set(after.wake_roles)


@settings(max_examples=200, deadline=None)
@given(watch=watches, weights=weights, yes=probability, low=st.integers(min_value=1, max_value=100),
       high=st.integers(min_value=1, max_value=100))
def test_a_more_sensitive_alarm_setting_never_removes_a_wake_up(watch, weights, yes, low, high):
    answers = _answers(watch, weights, yes)
    strict = NewsAlarmRule(urgent_threshold=Decimal(max(low, high)) / 100)
    sensitive = NewsAlarmRule(urgent_threshold=Decimal(min(low, high)) / 100)
    assert set(evaluate(answers, watch, strict).wake_roles) <= set(evaluate(answers, watch, sensitive).wake_roles)


@settings(max_examples=200, deadline=None)
@given(listed=st.lists(st.sampled_from(COINS), min_size=1, max_size=4, unique=True),
       held=st.lists(st.sampled_from(COINS), max_size=4, unique=True), weights=weights, yes=probability)
def test_holding_a_coin_never_removes_a_wake_up(listed, held, weights, yes):
    everything = tuple(sorted(set(listed) | set(held)))
    without = WatchList(listed=everything)
    with_held = WatchList(listed=everything, held=tuple(held))
    assert without.subject_options() == with_held.subject_options()
    answers = _answers(without, weights, yes)
    rule = NewsAlarmRule()
    assert set(evaluate(answers, without, rule).wake_roles) <= set(evaluate(answers, with_held, rule).wake_roles)


@settings(max_examples=200, deadline=None)
@given(watch=watches, weights=weights, yes=probability)
def test_a_verdict_wakes_the_leader_whenever_it_wakes_anyone(watch, weights, yes):
    verdict = evaluate(_answers(watch, weights, yes), watch, NewsAlarmRule())
    assert verdict.would_wake == bool(verdict.wake_roles)
    assert not verdict.wake_roles or verdict.wake_roles[0] == "leader"
    assert set(verdict.wake_roles) <= {"leader", "trader"}
