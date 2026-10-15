"""How the public Kraken endpoints say that too many requests were made (shared by the pair list and the candles).

Kraken words this several ways: ``EAPI:Rate limit exceeded`` and ``EService: Throttled: <unix time>`` for a call
counter that ran over (https://docs.kraken.com/api/docs/guides/spot-rest-ratelimits), and further codes such as
``EGeneral:Too many requests`` or ``EGeneral:Temporary lockout`` in its error list. Which of them a keyless public
call receives is not confirmed, so every wording stops a run: the next call would only lengthen the restriction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAXIMUM_UNIX_TIME = 4_102_444_800  # 2100-01-01; anything later is not a plausible candle, cursor or retry time
RATE_LIMITED = "rate_limited"
THROTTLED = "throttled"
_THROTTLED = re.compile(r"EService:\s*Throttled(?::\s*(\d+))?")
_RATE_LIMIT_MARKERS = ("ratelimit", "toomanyrequests", "templockout", "temporarylockout")


@dataclass(frozen=True, slots=True)
class RateLimitSignal:
    category: str                   # RATE_LIMITED or THROTTLED
    retry_at_unix: int | None = None  # a throttle states when to try again; None when absent or implausible


def rate_limit_signal(text: str) -> RateLimitSignal | None:
    """The rate-limit meaning of one exchange error string, or None when it says something else."""
    throttled = _THROTTLED.fullmatch(text.strip())
    if throttled is not None:
        digits = throttled.group(1)
        stamp = int(digits) if digits is not None and len(digits) <= 12 else None
        return RateLimitSignal(THROTTLED, stamp if stamp is not None and stamp <= MAXIMUM_UNIX_TIME else None)
    squashed = re.sub(r"\s+", "", text).lower()
    if any(marker in squashed for marker in _RATE_LIMIT_MARKERS):
        return RateLimitSignal(RATE_LIMITED)
    return None


def rate_limit_in(errors: object) -> RateLimitSignal | None:
    """The first rate-limit signal in an exchange ``error`` list, or None."""
    if not isinstance(errors, list):
        return None
    for text in errors:
        if isinstance(text, str):
            signal = rate_limit_signal(text)
            if signal is not None:
                return signal
    return None
