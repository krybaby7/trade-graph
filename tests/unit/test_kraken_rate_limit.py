"""Client-side mirror of Kraken's REST call counter. Scripted clock and sleeper: nothing here really waits."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest

from trade_graph.adapters.market.rate_limit import (
    ASSUMED_CEILING,
    ASSUMED_DECAY_PER_SECOND,
    KRAKEN_COUNTER_MAXIMUM,
    KRAKEN_DECAY_PER_SECOND,
    KrakenRestLimiter,
    LimitedTransport,
)
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import ValidationFailure

NOW = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_seconds: float) -> None:
        raise AssertionError("a test really slept")

    monkeypatch.setattr(time, "sleep", refuse)


class Sleeper:
    """Advances the scripted clock and records what was asked."""

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


def test_the_assumed_numbers_are_deliberately_below_krakens_starter_tier() -> None:
    assert (KRAKEN_COUNTER_MAXIMUM, KRAKEN_DECAY_PER_SECOND) == (15, Fraction(33, 100))
    assert ASSUMED_CEILING == 10 and ASSUMED_CEILING <= KRAKEN_COUNTER_MAXIMUM - 5
    assert ASSUMED_DECAY_PER_SECOND == Fraction(3, 10) < KRAKEN_DECAY_PER_SECOND


def test_a_burst_up_to_the_ceiling_does_not_wait() -> None:
    limiter, clock, sleeper = _limiter()
    waited = [limiter.acquire() for _ in range(ASSUMED_CEILING)]
    assert waited == [0] * ASSUMED_CEILING and sleeper.requests == []
    assert limiter.calls == ASSUMED_CEILING and limiter.counter() == ASSUMED_CEILING
    assert clock.now() == NOW


def test_the_call_after_the_ceiling_waits_exactly_until_it_fits() -> None:
    limiter, clock, sleeper = _limiter()
    for _ in range(ASSUMED_CEILING):
        limiter.acquire()
    waited = limiter.acquire()
    # One call must drain at 0.3 per second: 1 / 0.3 = 3.333333... rounded up to a whole microsecond.
    assert sleeper.requests == [3.333334] and waited == pytest.approx(3.333334)
    assert clock.now() == NOW + timedelta(seconds=3.333334)
    assert limiter.counter() <= ASSUMED_CEILING


def test_steady_state_is_one_call_per_3_33_seconds_and_kraken_would_never_exceed_the_ceiling() -> None:
    limiter, clock, _sleeper = _limiter()
    # An independent reference of Kraken's own counter, using the documented (faster) decay.
    reference = Fraction(0)
    last = clock.now()
    highest = Fraction(0)
    for _ in range(200):
        limiter.acquire()
        elapsed = Fraction((clock.now() - last) // timedelta(microseconds=1), 1_000_000)
        last = clock.now()
        reference = max(Fraction(0), reference - KRAKEN_DECAY_PER_SECOND * elapsed) + 1
        highest = max(highest, reference)
    assert highest <= ASSUMED_CEILING < KRAKEN_COUNTER_MAXIMUM
    elapsed_total = (clock.now() - NOW).total_seconds()
    assert (200 - ASSUMED_CEILING) / 0.3 <= elapsed_total <= (200 - ASSUMED_CEILING) / 0.3 + 1


def test_idle_time_restores_credit() -> None:
    limiter, clock, sleeper = _limiter()
    for _ in range(ASSUMED_CEILING):
        limiter.acquire()
    clock.advance(20)  # 20 s x 0.3 = 6 calls of credit
    for _ in range(6):
        assert limiter.acquire() == 0
    assert sleeper.requests == [] and limiter.counter() == ASSUMED_CEILING
    clock.advance(1000)
    assert limiter.counter() == 0


def test_a_clock_that_moves_backwards_grants_no_credit() -> None:
    limiter, clock, sleeper = _limiter()
    for _ in range(ASSUMED_CEILING):
        limiter.acquire()
    clock.advance(-600)
    limiter.acquire()
    assert sleeper.requests == [3.333334]
    assert limiter.counter() <= ASSUMED_CEILING


def test_a_sleeper_that_does_not_let_time_pass_fails_instead_of_looping() -> None:
    clock = FrozenClock(NOW)
    asked: list[float] = []
    limiter = KrakenRestLimiter(clock, asked.append)
    for _ in range(ASSUMED_CEILING):
        limiter.acquire()
    with pytest.raises(ValidationFailure, match="did not advance"):
        limiter.acquire()
    assert 1 <= len(asked) <= 5
    assert limiter.calls == ASSUMED_CEILING


@pytest.mark.parametrize("options", [
    {"ceiling": 0}, {"ceiling": 16}, {"ceiling": True}, {"ceiling": 10.0},
    {"decay_per_second": Fraction(0)}, {"decay_per_second": Fraction(34, 100)}, {"decay_per_second": 0.3},
])
def test_settings_that_assume_more_than_krakens_starter_tier_are_refused(options: dict) -> None:
    with pytest.raises(ValidationFailure):
        _limiter(**options)


def test_a_stricter_setting_is_allowed() -> None:
    limiter, _clock, sleeper = _limiter(ceiling=2, decay_per_second=Fraction(1, 10))
    limiter.acquire()
    limiter.acquire()
    limiter.acquire()
    assert sleeper.requests == [10.0]


def test_a_naive_clock_is_refused() -> None:
    class Naive:
        def now(self) -> datetime:
            return datetime(2026, 10, 13, 15, 0)

    limiter = KrakenRestLimiter(Naive(), lambda _seconds: None)
    with pytest.raises(ValidationFailure):
        limiter.acquire()


def test_a_requested_pause_goes_through_the_injected_sleeper_and_counts_as_waiting() -> None:
    limiter, clock, sleeper = _limiter()
    for _ in range(ASSUMED_CEILING):
        limiter.acquire()
    limiter.pause(6.0)
    assert sleeper.requests == [6.0] and clock.now() == NOW + timedelta(seconds=6)
    assert limiter.counter() == pytest.approx(ASSUMED_CEILING - 1.8)
    assert limiter.snapshot()["waited_seconds"] == pytest.approx(6.0)
    for bad in (-1.0, float("nan"), float("inf"), True, "3"):
        with pytest.raises(ValidationFailure):
            limiter.pause(bad)  # type: ignore[arg-type]


def test_the_snapshot_reports_calls_and_time_waited() -> None:
    limiter, _clock, _sleeper = _limiter()
    for _ in range(ASSUMED_CEILING + 2):
        limiter.acquire()
    snapshot = limiter.snapshot()
    assert snapshot["calls"] == ASSUMED_CEILING + 2
    assert snapshot["ceiling"] == 10 and snapshot["assumed_decay_per_second"] == "0.3"
    assert snapshot["kraken_starter_maximum"] == 15 and snapshot["kraken_starter_decay_per_second"] == "0.33"
    assert snapshot["waited_seconds"] == pytest.approx(2 * 3.333334, abs=1e-5)


def test_the_limited_transport_takes_a_slot_before_every_request_even_when_it_fails() -> None:
    order: list[str] = []
    limiter, _clock, _sleeper = _limiter()
    original = limiter.acquire
    limiter.acquire = lambda: (order.append("acquire"), original())[1]  # type: ignore[method-assign]

    class Inner:
        def get_text(self, url: str) -> str:
            order.append(f"get {url}")
            if url.endswith("bad"):
                raise RuntimeError("down")
            return "body"

    transport = LimitedTransport(Inner(), limiter)
    assert transport.get_text("u1") == "body"
    with pytest.raises(RuntimeError):
        transport.get_text("bad")
    assert order == ["acquire", "get u1", "acquire", "get bad"]
    assert limiter.calls == 2
