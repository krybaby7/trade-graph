"""Property tests for price alarms (T33): random price paths, restarts and re-arms against a small oracle.

- Quotes: every arming fires at most once, exactly when the oracle says, across restarts and re-arms.
- Candles: an alarm fires at the first completed candle that satisfies it, and only once that candle was received
  (never from a candle still forming or not yet received), across restarts at random ticks.
"""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from hypothesis import example, given, settings
from hypothesis import strategies as st
from tests.alarm_support import candle, candle_request, quote, quote_request, save
from tests.integration.test_price_alarm_watcher import Rig

from trade_graph.domain.clock import FrozenClock, parse_utc
from trade_graph.domain.errors import StaleState

LEVEL = 100
RELATIONS = ("above", "below", "crosses_above", "crosses_below")
_START = {"crosses_above": "at_or_below", "crosses_below": "at_or_above"}
_TARGET = {"crosses_above": "above", "crosses_below": "below"}

_steps = st.lists(st.one_of(
    st.tuples(st.just("quote"), st.integers(min_value=98, max_value=102)),  # the level is touched often
    st.tuples(st.just("restart"), st.just(0)),
    st.tuples(st.just("rearm"), st.just(0)),
), min_size=1, max_size=40)


def _quote_oracle(relation: str, steps: list[tuple[str, int]]) -> list[tuple[int, int]]:
    """(generation, price) of every fire the rules allow."""
    generation, armed, held, fires = 1, True, None, []
    for action, price in steps:
        if action == "rearm" and not armed:
            generation, armed, held = generation + 1, True, None
        if action != "quote" or not armed:
            continue
        if relation in ("above", "below"):
            fire = price > LEVEL if relation == "above" else price < LEVEL
        else:
            hit = price > LEVEL if relation == "crosses_above" else price < LEVEL
            side = _TARGET[relation] if hit else _START[relation]
            fire = held == _START[relation] and side == _TARGET[relation]
            held = None if fire else side
        if fire:
            fires.append((generation, price))
            armed = False
    return fires


@settings(max_examples=80, derandomize=True, deadline=None)
@given(relation=st.sampled_from(RELATIONS), steps=_steps)
# A path that touches the level and then leaves it crosses once; one that only touches it does not.
@example(relation="crosses_above", steps=[("quote", 100), ("quote", 101)])
@example(relation="crosses_below", steps=[("quote", 100), ("restart", 0), ("quote", 99)])
@example(relation="crosses_above", steps=[("quote", 99), ("quote", 100), ("quote", 100), ("quote", 99)])
def test_quote_alarms_fire_once_per_arming_exactly_when_the_rules_say(relation, steps) -> None:
    clock = FrozenClock(datetime(2026, 10, 16, 12, 0, tzinfo=UTC))
    with tempfile.TemporaryDirectory() as root:
        rig = Rig(Path(root), clock)
        try:
            alarm = rig.book.create(quote_request(str(LEVEL), relation, symbol="SYN/USD"))
            for action, price in steps:
                clock.advance(1)
                if action == "quote":
                    rig.watcher.observe_quote(quote("SYN/USD", clock.now(), bid=str(price), ask=str(price)))
                elif action == "restart":
                    rig.restart()
                else:
                    try:
                        rig.book.rearm(alarm.alarm_id, department="trader")
                    except StaleState:
                        pass  # still armed: re-arming waits for a fire
            rows = rig.state.execute(
                """SELECT e.generation, d.document_json FROM price_alarm_events e
                   JOIN alarm_deliveries d ON d.alarm_id = e.alarm_id AND d.generation = e.generation
                   WHERE e.action = 'fired' ORDER BY e.sequence""").fetchall()
            seen = [(row[0], int(Decimal(json.loads(row[1])["observed"]["value"]))) for row in rows]
            assert seen == _quote_oracle(relation, steps)
            assert len({generation for generation, _ in seen}) == len(seen)  # one fire per arming
        finally:
            rig.close()


@settings(max_examples=80, derandomize=True, deadline=None)
@given(
    relation=st.sampled_from(RELATIONS),
    closes=st.lists(st.integers(min_value=98, max_value=102), min_size=2, max_size=12),
    delays=st.lists(st.integers(min_value=1, max_value=900), min_size=12, max_size=12),
    ticks=st.lists(st.integers(min_value=0, max_value=12 * 900 + 1800), min_size=1, max_size=15, unique=True),
    restarts=st.sets(st.integers(min_value=0, max_value=14), max_size=4),
)
@example(relation="crosses_above", closes=[100, 101], delays=[30] * 12, ticks=[1800], restarts=set())
@example(relation="crosses_below", closes=[99, 100, 99], delays=[30] * 12, ticks=[900, 2700], restarts={1})
@example(relation="crosses_above", closes=[99, 100, 100, 99], delays=[30] * 12, ticks=[3600], restarts=set())
def test_candle_alarms_fire_at_the_first_satisfying_candle_once_it_was_received(
        relation, closes, delays, ticks, restarts) -> None:
    armed_at = datetime(2026, 10, 16, 12, 7, tzinfo=UTC)
    clock = FrozenClock(armed_at)
    first_close = datetime(2026, 10, 16, 12, 15, tzinfo=UTC)
    step = timedelta(minutes=15)
    # A collector hands candles over in order, so receipts never go backwards; each arrives after its close.
    candles, received = [candle(first_close - step, str(closes[0]))], first_close - step
    for index, price in enumerate(closes[1:]):
        close_time = first_close + step * index
        received = max(received, close_time + timedelta(seconds=delays[index]))
        candles.append(candle(close_time, str(price),
                              received_after_seconds=int((received - close_time).total_seconds())))
    expected = None
    for index in range(1, len(candles)):
        value, previous = Decimal(closes[index]), Decimal(closes[index - 1])
        level = Decimal(LEVEL)
        holds = {"above": value > level, "below": value < level,
                 "crosses_above": previous <= level < value, "crosses_below": previous >= level > value}[relation]
        if holds:
            expected = candles[index]
            break
    with tempfile.TemporaryDirectory() as root:
        rig = Rig(Path(root), clock)
        try:
            save(rig.store, candles[:1])
            alarm = rig.book.create(candle_request(str(LEVEL), relation))
            save(rig.store, candles[1:])
            tick_times = [armed_at + timedelta(seconds=offset) for offset in sorted(ticks)]
            fired_at = None
            for number, when in enumerate(tick_times):
                clock.advance((when - clock.now()).total_seconds())
                if number in restarts:
                    rig.restart()
                for delivery in rig.watcher.tick().fired:
                    assert fired_at is None, "an arming fired twice"
                    fired_at = when
                    observed = delivery.document["observed"]
                    assert parse_utc(observed["candle"]["available_at_utc"]) <= when  # received before it fired
                    assert parse_utc(observed["close_time_utc"]) <= when
                    assert observed["close_time_utc"] == expected.close_time_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            should_fire = expected is not None and any(when >= expected.available_at_utc for when in tick_times)
            assert (fired_at is not None) == should_fire
            if should_fire:
                assert fired_at == min(when for when in tick_times if when >= expected.available_at_utc)
            assert rig.fires() == (1 if should_fire else 0)
            assert rig.book.get(alarm.alarm_id).status == ("spent" if should_fire else "armed")
        finally:
            rig.close()
