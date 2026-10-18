"""The feed fetcher through the reviewed ``news_feeds`` route (W9-A, D256). No network: the HTTP client is replaced
by a recorder, and refusals happen before any client exists."""

from __future__ import annotations

import httpx
import pytest

from trade_graph.adapters.news import feeds
from trade_graph.adapters.news.feeds import HttpxFeedFetcher
from trade_graph.contracts.news import RECOMMENDED_FEEDS

ROUTE = "http://172.29.17.2:8083"
FEED = RECOMMENDED_FEEDS[0]


def _no_dns(host):
    raise AssertionError(f"a routed fetch never resolves {host} locally")


class Recorder:
    """Stands in for ``httpx.Client``: records how it was built, then fails like an unreachable proxy."""

    built: list[dict] = []

    def __init__(self, **kwargs) -> None:
        Recorder.built.append(kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def stream(self, method, url, headers=None):
        raise httpx.ConnectError("synthetic: the proxy is not reachable in this test")


@pytest.fixture
def recorder(monkeypatch):
    Recorder.built = []
    monkeypatch.setattr(httpx, "Client", Recorder)
    return Recorder


def test_a_feed_the_route_does_not_admit_is_refused_before_any_request(recorder) -> None:
    fetcher = HttpxFeedFetcher(proxy=ROUTE, admits=lambda url: False, resolver=_no_dns)
    result = fetcher.fetch(FEED, etag=None, last_modified=None)
    assert (result.outcome, result.error_category, result.body) == ("refused", "route_not_listed", None)
    assert recorder.built == []


def test_an_admission_check_that_fails_never_opens_the_route(recorder) -> None:
    def broken(url):
        raise RuntimeError("synthetic")

    result = HttpxFeedFetcher(proxy=ROUTE, admits=broken, resolver=_no_dns).fetch(FEED, etag=None, last_modified=None)
    assert result.outcome == "refused" and recorder.built == []


def test_a_routed_fetch_uses_only_the_routes_proxy_without_environment_or_local_dns(recorder) -> None:
    fetcher = HttpxFeedFetcher(proxy=ROUTE, admits=lambda url: url == FEED.url, resolver=_no_dns, trust_env=True)
    result = fetcher.fetch(FEED, etag="synthetic-etag", last_modified=None)
    assert result.outcome == "transport_error" and result.error_category == "transport"  # a lost feed, reported
    [options] = recorder.built
    assert options["proxy"] == ROUTE and options["trust_env"] is False and options["follow_redirects"] is False


def test_a_routed_fetcher_needs_both_its_proxy_and_its_admission_check() -> None:
    with pytest.raises(ValueError):
        HttpxFeedFetcher(proxy=ROUTE)
    with pytest.raises(ValueError):
        HttpxFeedFetcher(admits=lambda url: True)


def test_the_plain_fetcher_still_checks_public_addresses_itself(recorder) -> None:
    fetcher = HttpxFeedFetcher(resolver=lambda host: ["10.0.0.5"])
    result = fetcher.fetch(FEED, etag=None, last_modified=None)
    assert (result.outcome, result.error_category) == ("refused", "unsafe_target") and recorder.built == []
    assert feeds.HttpxFeedFetcher().proxy is None
