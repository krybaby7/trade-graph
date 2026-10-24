"""The Trades backfill's request limiter (docs/KRAKEN-TRADES-BACKFILL.md): the one Kraken limiter, with its settings.

It models the documented counter: every call adds ``cost`` to one shared counter, the counter falls by
``decay_per_second`` each second, and a call that would push it over ``max_counter`` is rejected with
``EAPI:Rate limit exceeded``. Kraken's page defines the counter per API key and does not say how unauthenticated
public calls are counted, so this limiter assumes the worst case: one counter for every call this process makes.

The limiter keeps ``headroom`` below the maximum, spaces calls at least ``min_interval_seconds`` apart (the
``EService: Throttled`` error is about concurrent requests) and, after Kraken says it was exceeded anyway, waits
until the counter is back under the ceiling. Time comes from an injected clock and waiting from an injected sleeper,
so tests never sleep. Waits are Decimal and round up to the millisecond.

Since W6-D (D221) there is one limiter implementation: ``RequestLimiter`` is ``rate_limit.KrakenRestLimiter`` built
from these Decimal settings, so the standalone backfill command keeps its documented numbers, and in the service one
``KrakenRestLimiter`` instance carries the collector and any backfill together (``backfill_trades`` takes either).
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from typing import Protocol

from trade_graph.adapters.market.rate_limit import KrakenRestLimiter, LimiterSettings, RateLimitWaitTooLong
from trade_graph.domain.clock import Clock

_MILLISECOND = Fraction(1, 1000)


class Sleeper(Protocol):
    def sleep(self, seconds: Decimal) -> None: ...


class RequestLimiter(KrakenRestLimiter):
    """The backfill's settings on the shared implementation; waits and counters are reported as Decimal."""

    def __init__(self, clock: Clock, sleeper: Sleeper, settings: LimiterSettings | None = None) -> None:
        self.sleeper = sleeper
        settings = settings or LimiterSettings()
        if not isinstance(settings, LimiterSettings):
            raise ValueError("backfill limiter settings are required")
        self._configure(clock, clock, ceiling=Fraction(settings.ceiling), decay=Fraction(settings.decay_per_second),
                        initial=Fraction(0), maximum=Fraction(settings.max_counter), cost=Fraction(settings.cost),
                        spacing=Fraction(settings.min_interval_seconds),
                        floor=Fraction(settings.penalty_floor_seconds), longest=Fraction(settings.max_wait_seconds),
                        quantum=_MILLISECOND)
        self._settings = settings
        self._sleep = lambda seconds: sleeper.sleep(Decimal(str(seconds)))

    def _sleep_for(self, seconds: Fraction) -> None:
        self.sleeper.sleep(Decimal(seconds.numerator) / Decimal(seconds.denominator))

    @property
    def settings(self) -> LimiterSettings:
        return self._settings

    @property
    def counter(self) -> Decimal:  # type: ignore[override]
        value = KrakenRestLimiter.counter(self)
        return Decimal(value.numerator) / Decimal(value.denominator)

    def acquire(self) -> Decimal:  # type: ignore[override]
        """Wait until one call fits under the ceiling and the minimum spacing, charge it, return the seconds waited."""
        waited = self._acquire()
        return (Decimal(waited.numerator) / Decimal(waited.denominator)).quantize(Decimal("0.001"))

    def penalize(self, retry_after_unix: int | None = None) -> Decimal:  # type: ignore[override]
        """Kraken refused a call as over the limit: assume the counter is at its maximum and wait it out.

        ``retry_after_unix`` is the time from an ``EService: Throttled`` answer. The wait is never shorter than
        the floor, never shorter than the time the counter needs to fall under the ceiling, and refused (raised as
        ``RateLimitWaitTooLong``, nothing slept) when it would exceed ``max_wait_seconds``.
        """
        waited = self._penalize(retry_after_unix)
        return (Decimal(waited.numerator) / Decimal(waited.denominator)).quantize(Decimal("0.001"))

    @property
    def waited_seconds(self) -> Decimal:
        return KrakenRestLimiter.waited_seconds.fget(self)


__all__ = ["LimiterSettings", "RateLimitWaitTooLong", "RequestLimiter", "Sleeper"]
