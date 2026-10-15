"""The request limiter models Kraken's counter (maximum 15, decay 0.33 per second) with an injected clock."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.trades_support import FakeSleeper

from trade_graph.adapters.market.trades_rate_limit import (
    LimiterSettings,
    RateLimitWaitTooLong,
    RequestLimiter,
)
from trade_graph.domain.clock import FrozenClock

T0 = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
T0_UNIX = 1_791_892_800


def _limiter(**overrides) -> tuple[RequestLimiter, FrozenClock, FakeSleeper]:
    clock = FrozenClock(T0)
    sleeper = FakeSleeper(clock)
    return RequestLimiter(clock, sleeper, LimiterSettings(**overrides)), clock, sleeper


def test_the_defaults_are_kraken_s_documented_starter_limits_with_headroom() -> None:
    settings = LimiterSettings()
    assert (settings.max_counter, settings.decay_per_second, settings.cost) == (Decimal(15), Decimal("0.33"), 1)
    assert settings.headroom == 2 and settings.min_interval_seconds == 1


def test_the_first_request_goes_at_once() -> None:
    limiter, _, sleeper = _limiter()
    assert limiter.acquire() == 0 and sleeper.waits == [] and limiter.counter == 1


def test_requests_are_spaced_at_least_the_minimum_interval_apart() -> None:
    limiter, clock, sleeper = _limiter()
    limiter.acquire()
    assert limiter.acquire() == Decimal(1) and sleeper.waits == [Decimal(1)]
    assert (clock.now() - T0).total_seconds() == 1


def test_a_long_pause_lets_the_counter_decay_to_zero_and_never_below() -> None:
    limiter, clock, _ = _limiter()
    for _ in range(5):
        limiter.acquire()
    clock.advance(600)
    assert limiter.counter == 0
    assert limiter.acquire() == 0


def test_the_counter_never_passes_the_ceiling_and_the_server_counter_never_passes_fifteen() -> None:
    """Replay 300 requests against a model of Kraken's counter: the limiter must keep it under the maximum."""
    limiter, clock, _ = _limiter()
    server, last_seen = Decimal(0), clock.now()
    peak = Decimal(0)
    for _ in range(300):
        limiter.acquire()
        now = clock.now()
        elapsed = Decimal(str((now - last_seen).total_seconds()))
        server = max(Decimal(0), server - elapsed * Decimal("0.33")) + 1
        last_seen = now
        peak = max(peak, server)
        assert limiter.counter <= 13
    assert peak <= 13, peak


def test_the_long_run_rate_is_the_decay_rate() -> None:
    limiter, clock, sleeper = _limiter()
    moments = []
    for _ in range(200):
        limiter.acquire()
        moments.append(clock.now())
    last_hundred = (moments[-1] - moments[99]).total_seconds() / 100
    assert 3.03 <= last_hundred <= 3.04  # 1 / 0.33 = 3.0303 s per request once the headroom is used up
    assert Decimal("3.030") <= sleeper.waits[-1] <= Decimal("3.031")


def test_waits_round_up_so_the_counter_is_never_over_by_a_hair() -> None:
    limiter, _, sleeper = _limiter(headroom=Decimal(0), min_interval_seconds=Decimal(0), max_counter=Decimal(3))
    for _ in range(6):
        limiter.acquire()
    assert all(wait == wait.quantize(Decimal("0.001")) for wait in sleeper.waits)
    assert limiter.counter <= 3


def test_a_rate_limit_answer_waits_for_the_counter_to_fall_below_the_ceiling() -> None:
    limiter, _, sleeper = _limiter()
    limiter.acquire()
    waited = limiter.penalize()
    assert waited == Decimal("9.091") and sleeper.waits[-1] == waited  # (15 - 12) / 0.33, rounded up
    assert limiter.counter <= 12
    assert limiter.acquire() == 0


def test_after_a_rate_limit_answer_the_counter_is_taken_to_have_been_full() -> None:
    limiter, _, _ = _limiter()
    limiter.acquire()  # the counter is 1 here, but Kraken said it was over 15
    limiter.penalize()
    assert limiter.counter == Decimal("11.99997")  # 15 less 9.091 s of decay at 0.33 a second


def test_a_throttle_timestamp_in_the_future_is_waited_out() -> None:
    limiter, clock, _ = _limiter()
    limiter.acquire()
    waited = limiter.penalize(retry_after_unix=T0_UNIX + 40)
    assert waited == 40 and (clock.now() - T0).total_seconds() == 40


def test_a_throttle_timestamp_in_the_past_still_waits_the_floor() -> None:
    limiter, _, _ = _limiter()
    limiter.acquire()
    assert limiter.penalize(retry_after_unix=T0_UNIX - 500) == Decimal("9.091")


def test_a_wait_beyond_the_bound_is_refused_without_sleeping() -> None:
    limiter, _, sleeper = _limiter()
    limiter.acquire()
    with pytest.raises(RateLimitWaitTooLong) as refused:
        limiter.penalize(retry_after_unix=T0_UNIX + 4000)
    assert refused.value.wait_seconds == 4000 and sleeper.waits == []


def test_a_clock_that_jumps_backwards_never_gives_credit_or_a_negative_wait() -> None:
    limiter, clock, sleeper = _limiter()
    for _ in range(4):
        limiter.acquire()
    before = limiter.counter
    clock.advance(-3600)
    assert limiter.counter == before  # no decay credit for time that went backwards
    assert 0 <= limiter.acquire() <= 5 and all(0 <= wait <= 5 for wait in sleeper.waits)


def test_total_waiting_and_charges_are_counted_for_the_report() -> None:
    limiter, _, _ = _limiter()
    for _ in range(3):
        limiter.acquire()
    limiter.penalize()
    assert limiter.requests_charged == 3 and limiter.penalties == 1
    assert limiter.waited_seconds == Decimal("11.091")  # 1 s + 1 s between the three, then the 9.091 s penalty


@pytest.mark.parametrize("overrides", [
    {"max_counter": Decimal(0)}, {"decay_per_second": Decimal(0)}, {"cost": Decimal(0)}, {"headroom": Decimal(-1)},
    {"headroom": Decimal(15)}, {"min_interval_seconds": Decimal(-1)}, {"max_wait_seconds": Decimal(0)},
    {"penalty_floor_seconds": Decimal(-1)}, {"decay_per_second": 0.33}, {"max_counter": 15},
])
def test_bad_settings_are_refused(overrides) -> None:
    with pytest.raises(ValueError):
        LimiterSettings(**overrides)
