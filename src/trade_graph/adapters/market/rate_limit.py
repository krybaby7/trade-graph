"""A conservative client-side mirror of Kraken's REST call counter, shared by every REST caller of a process.

Kraken (https://docs.kraken.com/api/docs/guides/spot-rest-ratelimits, read 2026-10-13) counts every REST call,
public ones included, on one counter. The starter tier allows a maximum of 15 and the counter drains by 0.33 per
second. Past the limit it answers ``EAPI:Rate limit exceeded`` or ``EService: Throttled: <unix time>`` and
restricts calls for a few seconds, longer if the calls continue.

This limiter assumes the starter tier and stays well inside it:

- ceiling 10 of 15, so five calls of headroom remain for other users of the same address (the trading
  service's own public polling);
- drain 0.30 per second, about 10% slower than the documented 0.33, so the mirror runs ahead of Kraken's own
  counter. Starting from zero it allows a burst of 10 and then one call per 3.33 seconds.

The mirror lives in one process. A process cannot know how many calls an earlier one made a moment ago, so a
command starts it at the ceiling (``initial_counter``): there is no burst, the first call waits, and a run that
begins right after another never takes Kraken's real counter above the ceiling. Two collectors running at the same
time in different processes are not coordinated and can together pass Kraken's maximum: run one at a time.

One implementation for every caller (W6-D, D221). The Trades backfill's limiter used to be a second class with the
same counter model; its two extra rules live here now, and its ``RequestLimiter`` is this class with its own
documented settings:

- ``min_interval_seconds`` spaces calls apart (``EService: Throttled`` is about concurrent requests);
- ``penalize`` handles a rate-limit or throttle answer: the counter is taken to be at Kraken's maximum and the caller
  waits until one call fits under the ceiling again (never less than ``penalty_floor_seconds``, never less than a
  stated throttle time), or is refused with ``RateLimitWaitTooLong`` when that would exceed ``max_wait_seconds``.

One instance is safe to share between threads: the counter is read and charged under a lock and nobody holds the
lock while sleeping, so a caller waiting for its slot never lets another charge past the ceiling. In the service one
instance carries the price collector, the hourly scan's ticker request and any Trades backfill (``service_limiter``).

The clock and the sleeper are injected, so tests never wait. Arithmetic uses exact fractions.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

from trade_graph.adapters.market.public import TextTransport
from trade_graph.domain.clock import Clock, SystemClock
from trade_graph.domain.errors import ValidationFailure

KRAKEN_COUNTER_MAXIMUM = 15
KRAKEN_DECAY_PER_SECOND = Fraction(33, 100)
ASSUMED_CEILING = 10
ASSUMED_DECAY_PER_SECOND = Fraction(3, 10)
DEFAULT_PENALTY_FLOOR_SECONDS = Fraction(5)
DEFAULT_MAX_WAIT_SECONDS = Fraction(120)
_WAIT_ROUNDS = 3
_MICROSECOND = timedelta(microseconds=1)
_MICROSECOND_QUANTUM = Fraction(1, 1_000_000)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _fraction_text(value: Fraction) -> str:
    return format(value.numerator / value.denominator, "g")


def _decimal(value: Fraction) -> Decimal:
    """An exact decimal for a fraction whose denominator has only the factors 2 and 5 (every value used here)."""
    return Decimal(value.numerator) / Decimal(value.denominator)


def _round_up(value: Fraction, quantum: Fraction) -> Fraction:
    return Fraction(math.ceil(value / quantum)) * quantum


def _seconds_between(later: datetime, earlier: datetime) -> Fraction:
    return Fraction(max(timedelta(0), later - earlier) // _MICROSECOND, 1_000_000)


class RateLimitWaitTooLong(RuntimeError):
    """Kraken asked for a longer pause than this run will wait; the caller stops and reports it."""

    def __init__(self, wait_seconds: Decimal) -> None:
        super().__init__(f"the required wait of {wait_seconds} seconds is over the allowed bound")
        self.wait_seconds = wait_seconds


@dataclass(frozen=True, slots=True)
class LimiterSettings:
    """The counter model in Decimal words, as the backfill records it (defaults: the backfill's own settings)."""

    max_counter: Decimal = Decimal(15)
    decay_per_second: Decimal = Decimal("0.33")
    cost: Decimal = Decimal(1)
    headroom: Decimal = Decimal(2)
    min_interval_seconds: Decimal = Decimal(1)
    penalty_floor_seconds: Decimal = Decimal(5)
    max_wait_seconds: Decimal = Decimal(120)

    def __post_init__(self) -> None:
        for name in ("max_counter", "decay_per_second", "cost", "headroom", "min_interval_seconds",
                     "penalty_floor_seconds", "max_wait_seconds"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{name} must be a finite Decimal")
        if self.max_counter <= 0 or self.decay_per_second <= 0 or self.cost <= 0 or self.max_wait_seconds <= 0:
            raise ValueError("max_counter, decay_per_second, cost and max_wait_seconds must be positive")
        if self.headroom < 0 or self.min_interval_seconds < 0 or self.penalty_floor_seconds < 0:
            raise ValueError("headroom, min_interval_seconds and penalty_floor_seconds cannot be negative")
        if self.max_counter - self.headroom < self.cost:
            raise ValueError("the ceiling (max_counter - headroom) must allow at least one call")

    @property
    def ceiling(self) -> Decimal:
        return self.max_counter - self.headroom


class KrakenRestLimiter:
    """Counts calls the way Kraken does and sleeps until the next call fits under the assumed ceiling."""

    def __init__(self, clock: Clock, sleeper: Callable[[float], None], *, ceiling: int = ASSUMED_CEILING,
                 decay_per_second: Fraction = ASSUMED_DECAY_PER_SECOND, initial_counter: int = 0,
                 min_interval_seconds: Fraction = Fraction(0),
                 penalty_floor_seconds: Fraction = DEFAULT_PENALTY_FLOOR_SECONDS,
                 max_wait_seconds: Fraction = DEFAULT_MAX_WAIT_SECONDS, wall_clock: Clock | None = None) -> None:
        if type(ceiling) is not int or not 1 <= ceiling <= KRAKEN_COUNTER_MAXIMUM:
            raise ValidationFailure("the assumed ceiling must be a whole number from 1 to Kraken's starter maximum")
        if not isinstance(decay_per_second, Fraction) or not 0 < decay_per_second <= KRAKEN_DECAY_PER_SECOND:
            raise ValidationFailure("the assumed drain must be a positive fraction no faster than Kraken's")
        if type(initial_counter) is not int or not 0 <= initial_counter <= KRAKEN_COUNTER_MAXIMUM:
            raise ValidationFailure("the starting counter must be a whole number from 0 to Kraken's starter maximum")
        for name, value, positive in (("spacing", min_interval_seconds, False),
                                      ("penalty floor", penalty_floor_seconds, False),
                                      ("longest wait", max_wait_seconds, True)):
            if not isinstance(value, Fraction) or value < 0 or (positive and value == 0):
                raise ValidationFailure(f"the {name} must be a {'positive' if positive else 'non-negative'} "
                                        "fraction of seconds")
        self._configure(clock, wall_clock or clock, ceiling=Fraction(ceiling), decay=decay_per_second,
                        initial=Fraction(initial_counter), maximum=Fraction(KRAKEN_COUNTER_MAXIMUM),
                        cost=Fraction(1), spacing=min_interval_seconds, floor=penalty_floor_seconds,
                        longest=max_wait_seconds, quantum=_MICROSECOND_QUANTUM)
        self._sleep = sleeper

    def _configure(self, clock: Clock, wall_clock: Clock, *, ceiling: Fraction, decay: Fraction, initial: Fraction,
                   maximum: Fraction, cost: Fraction, spacing: Fraction, floor: Fraction, longest: Fraction,
                   quantum: Fraction) -> None:
        self._clock = clock
        self._wall_clock = wall_clock
        self.ceiling = int(ceiling) if ceiling.denominator == 1 else ceiling
        self._ceiling = ceiling
        self.decay_per_second = decay
        self.initial_counter = int(initial) if initial.denominator == 1 else initial
        self._maximum = maximum
        self._cost = cost
        self._spacing = spacing
        self._floor = floor
        self._longest = longest
        self._quantum = quantum
        self._counter = Fraction(initial)
        self._at: datetime | None = None
        self._last_call: datetime | None = None
        self._lock = threading.Lock()
        self.calls = 0
        self.penalties = 0
        self._waited = Fraction(0)

    # --- time -------------------------------------------------------------------------------------------------

    def _now(self) -> datetime:
        now = self._clock.now()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValidationFailure("the rate limiter requires an aware clock")
        return now.astimezone(UTC)

    def _drain(self) -> datetime:
        now = self._now()
        if self._at is not None and now > self._at:
            self._counter = max(Fraction(0), self._counter - self.decay_per_second * _seconds_between(now, self._at))
        # A clock that jumped backwards earns no credit; time simply continues from the new reading, for the
        # spacing too (otherwise a step back of an hour would hold every caller for an hour).
        self._at = now
        if self._last_call is not None and now < self._last_call:
            self._last_call = now
        return now

    def _sleep_for(self, seconds: Fraction) -> None:
        self._sleep(float(seconds))

    # --- the counter ------------------------------------------------------------------------------------------

    def counter(self) -> Fraction:
        """The mirrored counter right now."""
        with self._lock:
            self._drain()
            return self._counter

    def _needed(self, now: datetime) -> Fraction:
        """Seconds until one more call fits under the ceiling and the spacing (0 when it fits now)."""
        wait = Fraction(0)
        if self._counter + self._cost > self._ceiling:
            wait = (self._counter + self._cost - self._ceiling) / self.decay_per_second
        if self._spacing and self._last_call is not None:
            wait = max(wait, self._spacing - _seconds_between(now, self._last_call))
        return _round_up(wait, self._quantum) if wait > 0 else Fraction(0)

    def _acquire(self) -> Fraction:
        """Take one call. Sleeps (outside the lock) until it fits. Returns the seconds waited."""
        waited = Fraction(0)
        stalled = 0
        while True:
            with self._lock:
                now = self._drain()
                pause = self._needed(now)
                if pause == 0:
                    self._counter += self._cost
                    self._last_call = now
                    self.calls += 1
                    self._waited += waited
                    return waited
            if stalled >= _WAIT_ROUNDS:
                with self._lock:
                    self._waited += waited
                raise ValidationFailure("the clock did not advance while the rate limiter waited")
            self._sleep_for(pause)
            waited += pause
            # Another caller may have taken the slot meanwhile; a clock that never moves must not loop forever.
            stalled = stalled + 1 if self._now() <= now else 0

    def acquire(self) -> float:
        """Take one call. Sleeps until it fits. Returns the seconds waited."""
        return float(self._acquire())

    def _penalize(self, retry_after_unix: int | None) -> Fraction:
        with self._lock:
            self._drain()
            fall = (self._maximum - (self._ceiling - self._cost)) / self.decay_per_second
            wait = max(fall, self._floor)
            if retry_after_unix is not None:
                wall = self._wall_clock.now().astimezone(UTC)
                now_unix = Fraction((wall - _EPOCH) // _MICROSECOND, 1_000_000)
                wait = max(wait, Fraction(retry_after_unix) - now_unix)
            wait = _round_up(wait, self._quantum)
            if wait > self._longest:
                raise RateLimitWaitTooLong(_decimal(wait))
            self._counter = self._maximum
        self._sleep_for(wait)
        with self._lock:
            self._drain()
            self.penalties += 1
            self._waited += wait
        return wait

    def penalize(self, retry_after_unix: int | None = None) -> float:
        """Kraken refused a call as over the limit: assume its counter is at the maximum and wait it out.

        ``retry_after_unix`` is the time from an ``EService: Throttled`` answer, read on the wall clock. The wait is
        never shorter than the floor, never shorter than the time the counter needs to fall so one call fits under the
        ceiling, and refused (``RateLimitWaitTooLong``, nothing slept) when it would exceed the longest wait.
        """
        return float(self._penalize(retry_after_unix))

    def pause(self, seconds: float) -> None:
        """Sleep through the injected sleeper, for example until a server-reported throttle ends."""
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 <= seconds < math.inf:
            raise ValidationFailure("a pause must be a finite number of seconds, zero or more")
        self._sleep(float(seconds))
        with self._lock:
            self._waited += Fraction(seconds).limit_denominator(1_000_000)

    # --- what it did ------------------------------------------------------------------------------------------

    @property
    def requests_charged(self) -> int:
        return self.calls

    @property
    def waited_seconds(self) -> Decimal:
        quantum = _decimal(self._quantum)
        return _decimal(_round_up(self._waited, self._quantum)).quantize(quantum)

    @property
    def settings(self) -> LimiterSettings:
        return LimiterSettings(
            max_counter=_decimal(self._maximum), decay_per_second=_decimal(self.decay_per_second),
            cost=_decimal(self._cost), headroom=_decimal(self._maximum - self._ceiling),
            min_interval_seconds=_decimal(self._spacing), penalty_floor_seconds=_decimal(self._floor),
            max_wait_seconds=_decimal(self._longest))

    def snapshot(self) -> dict:
        return {
            "calls": self.calls,
            "waited_seconds": float(self._waited),
            "ceiling": self.ceiling,
            "initial_counter": str(self.initial_counter),
            "assumed_decay_per_second": _fraction_text(self.decay_per_second),
            "kraken_starter_maximum": KRAKEN_COUNTER_MAXIMUM,
            "kraken_starter_decay_per_second": _fraction_text(KRAKEN_DECAY_PER_SECOND),
            "min_interval_seconds": format(_decimal(self._spacing).normalize(), "f"),
            "penalties": self.penalties,
        }


class MonotonicClock:
    """An aware clock that follows ``time.monotonic`` only, for the limiter.

    The limiter needs elapsed time, never the date. A host clock that is stepped (a time-service correction, a
    manual change) would make the wall clock read hours ahead and empty the mirrored counter, or behind and grant
    nothing. The readings here are not wall time and must not be stored as such; only differences mean anything.
    """

    _ORIGIN = datetime(1970, 1, 1, tzinfo=UTC)

    def __init__(self, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic
        self._start = monotonic()

    def now(self) -> datetime:
        return self._ORIGIN + timedelta(seconds=self._monotonic() - self._start)


class LimitedTransport:
    """Takes a limiter slot before every request, so no caller can reach Kraken around the limiter."""

    def __init__(self, transport: TextTransport, limiter: KrakenRestLimiter) -> None:
        self._transport = transport
        self._limiter = limiter

    def get_text(self, url: str) -> str:
        self._limiter.acquire()
        return self._transport.get_text(url)


def service_limiter(*, sleeper: Callable[[float], None] | None = None, clock: Clock | None = None,
                    wall_clock: Clock | None = None) -> KrakenRestLimiter:
    """The one limiter of a service process (D221): it starts at the ceiling (an earlier process may have just
    called), counts on a monotonic clock, reads throttle times on the wall clock and spaces calls a second apart."""
    return KrakenRestLimiter(clock or MonotonicClock(), sleeper or time.sleep, initial_counter=ASSUMED_CEILING,
                             min_interval_seconds=Fraction(1), wall_clock=wall_clock or SystemClock())


__all__ = [
    "ASSUMED_CEILING", "ASSUMED_DECAY_PER_SECOND", "KRAKEN_COUNTER_MAXIMUM", "KRAKEN_DECAY_PER_SECOND",
    "KrakenRestLimiter", "LimitedTransport", "LimiterSettings", "MonotonicClock", "RateLimitWaitTooLong",
    "service_limiter",
]
