"""A conservative client-side mirror of Kraken's REST call counter.

Kraken (https://docs.kraken.com/api/docs/guides/spot-rest-ratelimits, read 2026-10-13) counts every REST call,
public ones included, on one counter. The starter tier allows a maximum of 15 and the counter drains by 0.33 per
second. Past the limit it answers ``EAPI:Rate limit exceeded`` or ``EService: Throttled: <unix time>`` and
restricts calls for a few seconds, longer if the calls continue.

This limiter assumes the starter tier and stays well inside it:

- ceiling 10 of 15, so five calls of headroom remain for other users of the same address (the trading
  service's own public polling) and for a counter that started above zero;
- drain 0.30 per second, about 10% slower than the documented 0.33, so the mirror runs ahead of Kraken's own
  counter. Starting from zero it allows a burst of 10 and then one call per 3.33 seconds.

The clock and the sleeper are injected, so tests never wait. Arithmetic uses exact fractions.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from fractions import Fraction

from trade_graph.adapters.market.public import TextTransport
from trade_graph.domain.clock import Clock
from trade_graph.domain.errors import ValidationFailure

KRAKEN_COUNTER_MAXIMUM = 15
KRAKEN_DECAY_PER_SECOND = Fraction(33, 100)
ASSUMED_CEILING = 10
ASSUMED_DECAY_PER_SECOND = Fraction(3, 10)
_WAIT_ROUNDS = 3
_MICROSECOND = timedelta(microseconds=1)


def _fraction_text(value: Fraction) -> str:
    return format(value.numerator / value.denominator, "g")


class KrakenRestLimiter:
    """Counts calls the way Kraken does and sleeps until the next call fits under the assumed ceiling."""

    def __init__(self, clock: Clock, sleeper: Callable[[float], None], *, ceiling: int = ASSUMED_CEILING,
                 decay_per_second: Fraction = ASSUMED_DECAY_PER_SECOND) -> None:
        if type(ceiling) is not int or not 1 <= ceiling <= KRAKEN_COUNTER_MAXIMUM:
            raise ValidationFailure("the assumed ceiling must be a whole number from 1 to Kraken's starter maximum")
        if not isinstance(decay_per_second, Fraction) or not 0 < decay_per_second <= KRAKEN_DECAY_PER_SECOND:
            raise ValidationFailure("the assumed drain must be a positive fraction no faster than Kraken's")
        self._clock = clock
        self._sleep = sleeper
        self.ceiling = ceiling
        self.decay_per_second = decay_per_second
        self._counter = Fraction(0)
        self._at: datetime | None = None
        self.calls = 0
        self._waited = Fraction(0)

    def _now(self) -> datetime:
        now = self._clock.now()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValidationFailure("the rate limiter requires an aware clock")
        return now.astimezone(UTC)

    def _drain(self) -> None:
        now = self._now()
        if self._at is not None and now > self._at:
            elapsed = Fraction((now - self._at) // _MICROSECOND, 1_000_000)
            self._counter = max(Fraction(0), self._counter - self.decay_per_second * elapsed)
        # A clock that jumped backwards earns no credit; time simply continues from the new reading.
        self._at = now

    def counter(self) -> Fraction:
        """The mirrored counter right now."""
        self._drain()
        return self._counter

    def acquire(self) -> float:
        """Take one call. Sleeps until it fits. Returns the seconds waited."""
        waited = Fraction(0)
        for _ in range(_WAIT_ROUNDS):
            self._drain()
            if self._counter + 1 <= self.ceiling:
                self._counter += 1
                self.calls += 1
                self._waited += waited
                return float(waited)
            needed = (self._counter + 1 - self.ceiling) / self.decay_per_second
            pause = Fraction(math.ceil(needed * 1_000_000), 1_000_000)
            self._sleep(float(pause))
            waited += pause
        self._waited += waited
        raise ValidationFailure("the clock did not advance while the rate limiter waited")

    def snapshot(self) -> dict:
        return {
            "calls": self.calls,
            "waited_seconds": float(self._waited),
            "ceiling": self.ceiling,
            "assumed_decay_per_second": _fraction_text(self.decay_per_second),
            "kraken_starter_maximum": KRAKEN_COUNTER_MAXIMUM,
            "kraken_starter_decay_per_second": _fraction_text(KRAKEN_DECAY_PER_SECOND),
        }


class LimitedTransport:
    """Takes a limiter slot before every request, so no caller can reach Kraken around the limiter."""

    def __init__(self, transport: TextTransport, limiter: KrakenRestLimiter) -> None:
        self._transport = transport
        self._limiter = limiter

    def get_text(self, url: str) -> str:
        self._limiter.acquire()
        return self._transport.get_text(url)
