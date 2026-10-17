"""One Kraken request limiter for every REST caller of a process (W6-D, D221).

The collector's limiter and the Trades backfill's limiter were two classes with the same counter model. They are now
one implementation: the backfill's spacing and its rate-limit penalty live in ``KrakenRestLimiter``, the backfill's
``RequestLimiter`` is that class with the backfill's documented settings, and one instance can carry the collector,
the scan's ticker request and a backfill in the same service process. Scripted clock and sleeper: nothing here waits.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from trade_graph.adapters.market.rate_limit import (
    ASSUMED_CEILING,
    KRAKEN_COUNTER_MAXIMUM,
    KRAKEN_DECAY_PER_SECOND,
    KrakenRestLimiter,
    LimitedTransport,
    LimiterSettings,
    RateLimitWaitTooLong,
    service_limiter,
)
from trade_graph.adapters.market.trades_rate_limit import LimiterSettings as BackfillSettings
from trade_graph.adapters.market.trades_rate_limit import RateLimitWaitTooLong as BackfillTooLong
from trade_graph.adapters.market.trades_rate_limit import RequestLimiter
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import ValidationFailure

NOW = datetime(2026, 10, 17, 3, 0, tzinfo=UTC)
NOW_UNIX = 1_792_206_000


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_seconds: float) -> None:
        raise AssertionError("a test really slept")

    monkeypatch.setattr(time, "sleep", refuse)


class Sleeper:
    def __init__(self, clock: FrozenClock) -> None:
        self.clock = clock
        self.requests: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.requests.append(seconds)
        self.clock.advance(seconds)


def _limiter(**options) -> tuple[KrakenRestLimiter, FrozenClock, Sleeper]:
    clock = FrozenClock(NOW)
    sleeper = Sleeper(clock)
    return KrakenRestLimiter(clock, sleeper, **options), clock, sleeper


def test_there_is_one_limiter_implementation_and_the_backfill_names_are_the_same_objects() -> None:
    assert issubclass(RequestLimiter, KrakenRestLimiter)
    assert BackfillSettings is LimiterSettings and BackfillTooLong is RateLimitWaitTooLong


def test_a_rate_limit_answer_assumes_krakens_counter_is_full_and_waits_until_a_call_fits_again() -> None:
    limiter, clock, sleeper = _limiter()
    limiter.acquire()
    waited = limiter.penalize()
    # Kraken said over 15: wait until 15 has drained to 9, so the next call takes it to the ceiling of 10.
    assert waited == pytest.approx(20.0) and sleeper.requests == [20.0]
    assert limiter.counter() == ASSUMED_CEILING - 1
    assert limiter.acquire() == 0 and limiter.counter() == ASSUMED_CEILING
    assert limiter.penalties == 1 and limiter.requests_charged == 2
    assert clock.now() == NOW + timedelta(seconds=20)


def test_a_throttle_time_is_read_on_the_wall_clock_even_when_the_limiter_counts_on_a_monotonic_one() -> None:
    elapsed = FrozenClock(datetime(1970, 1, 1, tzinfo=UTC))
    wall = FrozenClock(NOW)
    asked: list[float] = []

    def sleeper(seconds: float) -> None:
        asked.append(seconds)
        elapsed.advance(seconds)
        wall.advance(seconds)

    limiter = KrakenRestLimiter(elapsed, sleeper, wall_clock=wall)
    assert limiter.penalize(retry_after_unix=NOW_UNIX + 45) == pytest.approx(45.0)
    assert asked == [45.0]


def test_a_pause_longer_than_the_bound_is_refused_without_sleeping() -> None:
    limiter, _clock, sleeper = _limiter()
    with pytest.raises(RateLimitWaitTooLong) as refused:
        limiter.penalize(retry_after_unix=NOW_UNIX + 4000)
    assert refused.value.wait_seconds == 4000 and sleeper.requests == [] and limiter.penalties == 0


def test_calls_can_be_spaced_a_minimum_interval_apart() -> None:
    limiter, _clock, sleeper = _limiter(min_interval_seconds=Fraction(1))
    assert limiter.acquire() == 0
    assert limiter.acquire() == pytest.approx(1.0) and sleeper.requests == [1.0]


def test_the_settings_view_describes_the_one_counter() -> None:
    limiter, _clock, _sleeper = _limiter(min_interval_seconds=Fraction(1))
    settings = limiter.settings
    assert (settings.max_counter, settings.decay_per_second, settings.cost, settings.headroom,
            settings.min_interval_seconds) == (Decimal(15), Decimal("0.3"), Decimal(1), Decimal(5), Decimal(1))
    assert settings.ceiling == ASSUMED_CEILING
    snapshot = limiter.snapshot()
    assert snapshot["min_interval_seconds"] == "1" and snapshot["penalties"] == 0


@pytest.mark.parametrize("options", [
    {"min_interval_seconds": Fraction(-1)}, {"min_interval_seconds": 1.0},
    {"max_wait_seconds": Fraction(0)}, {"penalty_floor_seconds": Fraction(-1)},
])
def test_bad_spacing_and_penalty_options_are_refused(options: dict) -> None:
    with pytest.raises(ValidationFailure):
        _limiter(**options)


def test_the_service_limiter_starts_at_the_ceiling_with_one_second_spacing_on_a_monotonic_clock() -> None:
    limiter = service_limiter(sleeper=lambda _seconds: None)
    assert limiter.counter() == ASSUMED_CEILING
    assert limiter.settings.min_interval_seconds == 1
    assert limiter.snapshot()["initial_counter"] == "10"


def test_two_threads_sharing_one_limiter_never_take_the_counter_past_the_ceiling() -> None:
    """A thread waiting for its slot does not hold the others up, and nobody charges past the ceiling meanwhile."""
    clock = FrozenClock(NOW)
    gate = threading.Lock()
    release = threading.Event()
    asleep = threading.Event()

    def sleeper(seconds: float) -> None:
        asleep.set()
        assert release.wait(5), "the test never released the sleeper"
        with gate:
            clock.advance(seconds)

    limiter = KrakenRestLimiter(clock, sleeper, initial_counter=ASSUMED_CEILING)
    results: list[float] = []
    threads = [threading.Thread(target=lambda: results.append(limiter.acquire())) for _ in range(2)]
    for thread in threads:
        thread.start()
    assert asleep.wait(5)
    time_ok = limiter.counter() == ASSUMED_CEILING and limiter.calls == 0
    release.set()
    for thread in threads:
        thread.join(5)
    assert time_ok
    assert len(results) == 2 and limiter.calls == 2
    assert limiter.counter() <= ASSUMED_CEILING


def test_interleaved_callers_on_one_limiter_never_take_krakens_own_counter_past_the_ceiling() -> None:
    """The collector's requests and a backfill's requests share one counter (Kraken's documented, faster drain)."""
    limiter, clock, _sleeper = _limiter(initial_counter=ASSUMED_CEILING, min_interval_seconds=Fraction(1))

    class Inner:
        def __init__(self) -> None:
            self.calls = 0

        def get_text(self, url: str) -> str:
            self.calls += 1
            return "{}"

    collector, backfill = LimitedTransport(Inner(), limiter), LimitedTransport(Inner(), limiter)
    real, last, peak = Fraction(ASSUMED_CEILING), clock.now(), Fraction(0)
    for index in range(120):
        (collector if index % 3 else backfill).get_text("https://api.kraken.com/0/public/OHLC")
        elapsed = Fraction((clock.now() - last) // timedelta(microseconds=1), 1_000_000)
        last = clock.now()
        real = max(Fraction(0), real - KRAKEN_DECAY_PER_SECOND * elapsed) + 1
        peak = max(peak, real)
    assert peak <= ASSUMED_CEILING < KRAKEN_COUNTER_MAXIMUM
    assert limiter.calls == 120
