"""The second reviewed route, ``news_feeds`` (W9-A, D256): the five news feeds and TypeSafe's Jev endpoint.

Built the way W8-A built ``paid_research`` (D241): code fixes which hosts the route may ever name, the owner's
protected network profile lists its own port and the hosts it admits, the proxy admits a CONNECT only to those hosts on
that port, and the in-service transports send only through that port to URLs the profile admits. Local sockets and
synthetic DNS answers only: nothing here reaches a feed, TypeSafe or any other internet host.
"""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit

import pytest
from tests.security.test_reviewed_routes import _run
from tests.security.test_subscription_network import profile
from tests.security.test_subscription_proxy import _hello, _request

from trade_graph.adapters.decisions.jev import JEV_ENDPOINT
from trade_graph.contracts.news import RECOMMENDED_FEEDS
from trade_graph.kernel import subscription_proxy as proxy_module
from trade_graph.kernel.subscription_network import PreparedSubscriptionNetworkProfile
from trade_graph.kernel.subscription_proxy import (
    MARKET_HOSTS,
    NEWS_FEEDS,
    PAID_RESEARCH,
    PROVIDER_HOSTS,
    REVIEWED_ROUTE_HOSTS,
)

FEED_HOSTS = ("blog.kraken.com", "cointelegraph.com", "decrypt.co", "status.kraken.com", "www.coindesk.com")
HOSTS = tuple(sorted({*FEED_HOSTS, "api.typesafe.ai"}))
ROUTES = {"paid_research": {"port": 8082, "hosts": ["api.perplexity.ai"]},
          "news_feeds": {"port": 8083, "hosts": list(HOSTS)}}


# --- the reviewed catalogue -------------------------------------------------------------------------------------

def test_news_feeds_is_a_reviewed_route_of_exactly_the_five_feed_hosts_and_typesafe() -> None:
    assert NEWS_FEEDS == "news_feeds"
    assert REVIEWED_ROUTE_HOSTS[NEWS_FEEDS] == frozenset(HOSTS)
    # The catalogue is exactly the recommended feeds' hosts and the one official Jev endpoint's host (D142, D140).
    assert REVIEWED_ROUTE_HOSTS[NEWS_FEEDS] == frozenset(
        {urlsplit(feed.url).hostname for feed in RECOMMENDED_FEEDS} | {urlsplit(JEV_ENDPOINT).hostname})
    news = REVIEWED_ROUTE_HOSTS[NEWS_FEEDS]
    assert not news & MARKET_HOSTS and not news & PROVIDER_HOSTS
    assert not news & REVIEWED_ROUTE_HOSTS[PAID_RESEARCH]
    assert MARKET_HOSTS == frozenset({"api.kraken.com", "api.frankfurter.dev"})  # unchanged


# --- the proxy listener -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("host", HOSTS)
def test_the_news_listener_relays_each_owner_listed_host(host) -> None:
    headers, body, calls, observed = asyncio.run(_run(_request(host), scope=NEWS_FEEDS, hosts=set(HOSTS),
                                                      hello=_hello(host)))
    assert headers == b"HTTP/1.1 200 Connection Established\r\n\r\n"
    assert body == b"opaque-encrypted-response" and observed == [_hello(host)]
    assert calls == [("resolve", host), ("dial", "1.1.1.1", 443)]


@pytest.mark.parametrize("host", ["api.kraken.com", "api.frankfurter.dev", "api.perplexity.ai", "api.anthropic.com",
                                  "kraken.com", "www.kraken.com", "coindesk.com", "www.cointelegraph.com",
                                  "typesafe.ai", "console.typesafe.ai", "docs.typesafe.ai",
                                  "decrypt.co.evil.example", "status.kraken.com.evil.example", "127.0.0.1"])
def test_the_news_listener_refuses_every_other_host_before_dns(host) -> None:
    headers, _, calls, _ = asyncio.run(_run(_request(host), scope=NEWS_FEEDS, hosts=set(HOSTS)))
    assert headers.startswith(b"HTTP/1.1 403") and calls == []


def test_a_news_listener_admits_only_the_hosts_the_owner_listed_for_it() -> None:
    # The owner may list a subset (for example without TypeSafe while Jev stays off); the others are then refused.
    headers, _, calls, _ = asyncio.run(_run(_request("api.typesafe.ai"), scope=NEWS_FEEDS, hosts=set(FEED_HOSTS)))
    assert headers.startswith(b"HTTP/1.1 403") and calls == []


@pytest.mark.parametrize("scope", ["market", "provider", PAID_RESEARCH])
@pytest.mark.parametrize("host", ["status.kraken.com", "www.coindesk.com", "api.typesafe.ai"])
def test_news_hosts_are_never_admitted_on_another_listener(scope, host) -> None:
    hosts = {"api.perplexity.ai"} if scope == PAID_RESEARCH else None
    headers, _, calls, _ = asyncio.run(_run(_request(host), scope=scope, hosts=hosts))
    assert headers.startswith(b"HTTP/1.1 403") and calls == []


def test_the_news_listener_checks_the_tls_name_like_the_other_listeners() -> None:
    _, body, calls, _ = asyncio.run(_run(_request("decrypt.co"), scope=NEWS_FEEDS, hosts=set(HOSTS),
                                         hello=_hello("api.kraken.com")))
    assert body == b"" and calls == [("resolve", "decrypt.co")]  # domain fronting never dials


@pytest.mark.parametrize("hosts", [{"example.com"}, {*HOSTS, "api.kraken.com"}, {"WWW.COINDESK.COM"}, set(),
                                   "decrypt.co"])
def test_a_news_listener_refuses_hosts_outside_its_catalogue(hosts) -> None:
    with pytest.raises(ValueError):
        proxy_module.ConnectProxy(NEWS_FEEDS, hosts=hosts)


# --- the proxy command line -------------------------------------------------------------------------------------

def test_the_command_line_takes_the_news_route_beside_paid_research() -> None:
    value = "news_feeds=8083:" + ",".join(HOSTS)
    assert proxy_module.parse_route(value) == (NEWS_FEEDS, 8083, frozenset(HOSTS))
    listeners = proxy_module._listeners(8080, 8081, [proxy_module.parse_route("paid_research=8082:api.perplexity.ai"),
                                                    proxy_module.parse_route(value)])
    assert [(proxy.allowed, port) for proxy, port in listeners] == [
        (PROVIDER_HOSTS, 8080), (MARKET_HOSTS, 8081), (frozenset({"api.perplexity.ai"}), 8082),
        (frozenset(HOSTS), 8083)]


@pytest.mark.parametrize("args", [
    ["--route", "news_feeds=8082:decrypt.co", "--route", "paid_research=8082:api.perplexity.ai"],  # one port twice
    ["--route", "news_feeds=8081:decrypt.co"],  # the market port
    ["--route", "news_feeds=8083:decrypt.co,example.com"],
    ["--route", "news_feeds=8083:api.perplexity.ai"],
    ["--route", "paid_research=8082:decrypt.co"],
    ["--route", "news_feeds=8083:decrypt.co", "--route", "news_feeds=8084:decrypt.co"],
])
def test_the_command_line_refuses_unreviewed_or_clashing_news_routes(args, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        proxy_module.main(args)
    assert exc.value.code == 2
    assert "refused" in capsys.readouterr().err


# --- the owner's network profile --------------------------------------------------------------------------------

def test_a_profile_lists_both_reviewed_routes_on_their_own_ports() -> None:
    value = profile(reviewed_routes=ROUTES)
    assert value.route_url(NEWS_FEEDS) == "http://172.29.17.2:8083"
    assert value.route_url(PAID_RESEARCH) == "http://172.29.17.2:8082"
    for feed in RECOMMENDED_FEEDS:
        assert value.route_admits(NEWS_FEEDS, feed.url), feed.url
        assert not value.route_admits(PAID_RESEARCH, feed.url)
    assert value.route_admits(NEWS_FEEDS, JEV_ENDPOINT)
    for url in ("http://decrypt.co/feed", "https://decrypt.co:8443/feed", "https://user@decrypt.co/feed",
                "https://api.perplexity.ai/search", "https://api.kraken.com/0/public/Ticker",
                "https://feeds.example.com/rss"):
        assert not value.route_admits(NEWS_FEEDS, url), url
    loaded = PreparedSubscriptionNetworkProfile.model_validate(json.loads(value.model_dump_json()))
    assert loaded == value and loaded.sha256 == value.sha256


@pytest.mark.parametrize("routes", [
    {"news_feeds": {"port": 8083, "hosts": ["api.perplexity.ai"]}},
    {"news_feeds": {"port": 8083, "hosts": ["decrypt.co", "blog.kraken.com"]}},  # unsorted
    {"news_feeds": {"port": 8083, "hosts": ["api.kraken.com"]}},
    {"news_feeds": {"port": 8081, "hosts": ["decrypt.co"]}},
    {"news_feeds": {"port": 8082, "hosts": ["decrypt.co"]}, "paid_research": {"port": 8082,
                                                                               "hosts": ["api.perplexity.ai"]}},
])
def test_a_profile_refuses_unreviewed_news_hosts_and_clashing_ports(routes) -> None:
    with pytest.raises(ValueError):
        profile(reviewed_routes=routes)


# --- the in-service transports send a CONNECT the strict admission accepts ---------------------------------------

async def _captured_connect(send) -> bytes:
    captured = []

    async def capture(reader, writer):
        try:
            captured.append(await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2))
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")  # refuse before TLS
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(capture, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        result = await asyncio.to_thread(send, f"http://127.0.0.1:{port}")
    finally:
        server.close()
        await server.wait_closed()
    [connect] = captured
    return connect, result


def test_the_routed_feed_fetcher_sends_a_connect_the_news_listener_accepts() -> None:
    from trade_graph.adapters.news.feeds import HttpxFeedFetcher

    feed = RECOMMENDED_FEEDS[2]  # CoinDesk

    def send(proxy):
        fetcher = HttpxFeedFetcher(proxy=proxy, admits=lambda url: True, timeout=2)
        return fetcher.fetch(feed, etag=None, last_modified=None)

    connect, result = asyncio.run(_captured_connect(send))
    assert result.outcome == "transport_error" and result.body is None  # a lost feed, recorded as such
    assert proxy_module._connect_host(connect, REVIEWED_ROUTE_HOSTS[NEWS_FEEDS]) == "www.coindesk.com"


def test_the_routed_jev_transport_sends_a_connect_the_news_listener_accepts_without_the_key() -> None:
    from trade_graph.adapters.decisions.jev import PinnedJevHttp

    def send(proxy):
        transport = PinnedJevHttp(timeout=2, proxy=proxy, trust_env=False)
        with pytest.raises(TimeoutError):  # the transport's uncertain outcome; nothing reached TypeSafe
            transport.post_json(JEV_ENDPOINT, {"x": 1}, {"authorization": "Bearer synthetic-not-a-key-000000"})

    connect, _ = asyncio.run(_captured_connect(send))
    assert b"synthetic-not-a-key" not in connect  # the key never reaches the proxy
    assert proxy_module._connect_host(connect, REVIEWED_ROUTE_HOSTS[NEWS_FEEDS]) == "api.typesafe.ai"
