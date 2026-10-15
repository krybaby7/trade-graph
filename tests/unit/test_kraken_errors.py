"""How Kraken's rate-limit wordings are read, for the pair list and the candles alike."""

from __future__ import annotations

import pytest

from trade_graph.adapters.market.kraken_errors import (
    MAXIMUM_UNIX_TIME,
    RATE_LIMITED,
    THROTTLED,
    RateLimitSignal,
    rate_limit_in,
    rate_limit_signal,
)


@pytest.mark.parametrize("text", [
    "EAPI:Rate limit exceeded", "EAPI: Rate  limit exceeded", "eapi:rate limit exceeded",
    "EOrder:Rate limit exceeded", "EGeneral:Too many requests", "EAuth:Too many requests",
    "EGeneral:Temporary lockout", "EGeneral:TemporaryLockout",
])
def test_every_known_wording_of_a_rate_limit_is_recognised(text: str) -> None:
    assert rate_limit_signal(text) == RateLimitSignal(RATE_LIMITED)


@pytest.mark.parametrize(("text", "retry_at"), [
    ("EService: Throttled: 1791905000", 1791905000), ("EService:Throttled:1791905000", 1791905000),
    ("EService:Throttled", None), ("EService: Throttled: 99999999999999999999999", None),
    (f"EService: Throttled: {MAXIMUM_UNIX_TIME + 1}", None), (f"EService: Throttled: {MAXIMUM_UNIX_TIME}",
                                                               MAXIMUM_UNIX_TIME),
])
def test_a_throttle_carries_its_time_only_when_it_is_plausible(text: str, retry_at: int | None) -> None:
    assert rate_limit_signal(text) == RateLimitSignal(THROTTLED, retry_at)


@pytest.mark.parametrize("text", [
    "EQuery:Unknown asset pair", "EService:Unavailable", "EService:Busy", "EGeneral:Internal error", "", "SECRET",
    "EService: Throttled: 12 and more",
])
def test_other_exchange_errors_are_not_rate_limits(text: str) -> None:
    assert rate_limit_signal(text) is None


def test_the_first_rate_limit_in_an_error_list_is_returned() -> None:
    assert rate_limit_in(["EGeneral:Internal error", "EService: Throttled: 1791905000", "EAPI:Rate limit exceeded"]) \
        == RateLimitSignal(THROTTLED, 1791905000)
    assert rate_limit_in(["SECRET", 7, None, "EAPI:Rate limit exceeded"]) == RateLimitSignal(RATE_LIMITED)
    for not_a_list in (None, "EAPI:Rate limit exceeded", {"error": []}, 3):
        assert rate_limit_in(not_a_list) is None
    assert rate_limit_in([]) is None
