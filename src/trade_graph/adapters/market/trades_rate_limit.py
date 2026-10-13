"""A conservative request limiter for Kraken's public REST calls (docs/KRAKEN-TRADES-BACKFILL.md).

It models the documented counter: every call adds ``cost`` to one shared counter, the counter falls by
``decay_per_second`` each second, and a call that would push it over ``max_counter`` is rejected with
``EAPI:Rate limit exceeded``. Kraken's page defines the counter per API key and does not say how unauthenticated
public calls are counted, so this limiter assumes the worst case: one counter for every call this process makes.

The limiter keeps ``headroom`` below the maximum, spaces calls at least ``min_interval_seconds`` apart (the
``EService: Throttled`` error is about concurrent requests) and, after Kraken says it was exceeded anyway, waits
until the counter is back under the ceiling. Time comes from an injected clock and waiting from an injected sleeper,
so tests never sleep. Waits are Decimal and round up to the millisecond.

NOTE for the integration owner: the collector stream is writing its own ``rate_limit.py``. Both implement this same
counter model; they should be unified into one limiter shared by every REST caller in the process.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Protocol

from trade_graph.domain.clock import Clock

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MILLISECOND = Decimal("0.001")
_MICROS = Decimal(1_000_000)


class Sleeper(Protocol):
    def sleep(self, seconds: Decimal) -> None: ...


class RateLimitWaitTooLong(RuntimeError):
    """Kraken asked for a longer pause than this run will wait; the caller stops and reports it."""

    def __init__(self, wait_seconds: Decimal) -> None:
        super().__init__(f"the required wait of {wait_seconds} seconds is over the allowed bound")
        self.wait_seconds = wait_seconds


@dataclass(frozen=True, slots=True)
class LimiterSettings:
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


def _round_up(value: Decimal) -> Decimal:
    return value.quantize(_MILLISECOND, rounding=ROUND_CEILING)


class RequestLimiter:
    def __init__(self, clock: Clock, sleeper: Sleeper, settings: LimiterSettings | None = None) -> None:
        self.clock = clock
        self.sleeper = sleeper
        self.settings = settings or LimiterSettings()
        self._counter = Decimal(0)
        self._updated = self._micros()
        self._last_call: int | None = None
        self.requests_charged = 0
        self.penalties = 0
        self.waited_seconds = Decimal(0)

    def _micros(self) -> int:
        now = self.clock.now()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("the limiter clock must return aware times")
        return (now.astimezone(UTC) - _EPOCH) // timedelta(microseconds=1)

    def _decayed(self, now: int) -> Decimal:
        elapsed = Decimal(max(0, now - self._updated)) / _MICROS  # time that went backwards earns no credit
        return max(Decimal(0), self._counter - elapsed * self.settings.decay_per_second)

    def _settle(self) -> int:
        now = self._micros()
        self._counter, self._updated = self._decayed(now), now
        return now

    @property
    def counter(self) -> Decimal:
        return self._decayed(self._micros())

    def _wait(self, seconds: Decimal) -> Decimal:
        seconds = _round_up(max(Decimal(0), seconds))
        if seconds > 0:
            self.sleeper.sleep(seconds)
            self.waited_seconds += seconds
        return seconds

    def acquire(self) -> Decimal:
        """Wait until one call fits under the ceiling and the minimum spacing, charge it, return the seconds waited."""
        settings = self.settings
        now = self._settle()
        wait = Decimal(0)
        if self._counter + settings.cost > settings.ceiling:
            wait = (self._counter + settings.cost - settings.ceiling) / settings.decay_per_second
        if self._last_call is not None:
            since_last = Decimal(max(0, now - self._last_call)) / _MICROS
            wait = max(wait, settings.min_interval_seconds - since_last)
        waited = self._wait(wait)
        self._settle()
        self._counter += settings.cost
        self._last_call = self._updated
        self.requests_charged += 1
        return waited

    def penalize(self, retry_after_unix: int | None = None) -> Decimal:
        """Kraken refused a call as over the limit: assume the counter is at its maximum and wait it out.

        ``retry_after_unix`` is the time from an ``EService: Throttled`` answer. The wait is never shorter than
        the floor, never shorter than the time the counter needs to fall under the ceiling, and refused (raised as
        ``RateLimitWaitTooLong``, nothing slept) when it would exceed ``max_wait_seconds``.
        """
        settings = self.settings
        now = self._settle()
        fall = (settings.max_counter - (settings.ceiling - settings.cost)) / settings.decay_per_second
        wait = max(fall, settings.penalty_floor_seconds)
        if retry_after_unix is not None:
            wait = max(wait, Decimal(retry_after_unix) - Decimal(now) / _MICROS)
        wait = _round_up(wait)
        if wait > settings.max_wait_seconds:
            raise RateLimitWaitTooLong(wait)
        self._counter = settings.max_counter
        waited = self._wait(wait)
        self._settle()
        self.penalties += 1
        return waited
