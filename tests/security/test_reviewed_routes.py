"""Separately reviewed network routes (W8-A, D241): owner-listed hosts beside the provider and market listeners.

Code fixes which hosts a reviewed route may ever name (``REVIEWED_ROUTE_HOSTS``); the owner's protected network profile
names the route, its own listener port and the hosts it admits; the proxy admits a CONNECT only to those hosts on that
port. Paid research (Perplexity) is the first route; W9-A adds the news feeds and TypeSafe as a second one the same way.
Local sockets and synthetic DNS answers only: nothing here reaches the internet.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json

import pytest
from tests.security.test_subscription_network import profile
from tests.security.test_subscription_proxy import _hello, _request

from trade_graph.kernel import subscription_proxy as proxy_module
from trade_graph.kernel.runtime_manifest import document_sha256
from trade_graph.kernel.subscription_network import PreparedSubscriptionNetworkProfile
from trade_graph.kernel.subscription_proxy import (
    MARKET_HOSTS,
    PAID_RESEARCH,
    PROVIDER_HOSTS,
    REVIEWED_ROUTE_HOSTS,
    ConnectProxy,
    ProxyLimits,
)

PERPLEXITY = "api.perplexity.ai"
ROUTE = {"paid_research": {"port": 8082, "hosts": [PERPLEXITY]}}


async def _run(request: bytes, *, scope: str, hosts=None, hello: bytes | None = None):
    calls, observed = [], []

    async def upstream(reader, writer):
        try:
            observed.append(await reader.readexactly(len(hello)))
            writer.write(b"opaque-encrypted-response")
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    upstream_port = server.sockets[0].getsockname()[1]

    async def resolve(host):
        calls.append(("resolve", host))
        return ("1.1.1.1",)

    async def dial(address, port):
        calls.append(("dial", address, port))
        return await asyncio.open_connection("127.0.0.1", upstream_port)

    proxy = ConnectProxy(scope, hosts=hosts, limits=ProxyLimits(), resolver=resolve, dialer=dial)
    listener = await proxy.start("127.0.0.1", 0)
    reader, writer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
    try:
        writer.write(request)
        await writer.drain()
        headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
        if hello is not None and headers.startswith(b"HTTP/1.1 200"):
            writer.write(hello)
            await writer.drain()
        body = await asyncio.wait_for(reader.read(), 2)
        return headers, body, calls, observed
    finally:
        writer.close()
        await writer.wait_closed()
        await proxy.close()
        server.close()
        await server.wait_closed()


# --- the reviewed catalogue -------------------------------------------------------------------------------------

def test_paid_research_is_a_reviewed_route_of_its_own_apart_from_market_and_provider_hosts() -> None:
    assert PAID_RESEARCH == "paid_research"
    assert REVIEWED_ROUTE_HOSTS[PAID_RESEARCH] == frozenset({PERPLEXITY})
    assert MARKET_HOSTS == frozenset({"api.kraken.com", "api.frankfurter.dev"})  # unchanged: Kraken and Frankfurter
    for hosts in REVIEWED_ROUTE_HOSTS.values():
        assert not hosts & MARKET_HOSTS and not hosts & PROVIDER_HOSTS
    with pytest.raises(TypeError):
        REVIEWED_ROUTE_HOSTS["anything"] = frozenset({"example.com"})  # type: ignore[index]


# --- the proxy listener -----------------------------------------------------------------------------------------

def test_the_research_listener_relays_only_the_owner_listed_perplexity_host() -> None:
    headers, body, calls, observed = asyncio.run(_run(_request(PERPLEXITY), scope=PAID_RESEARCH,
                                                      hosts={PERPLEXITY}, hello=_hello(PERPLEXITY)))
    assert headers == b"HTTP/1.1 200 Connection Established\r\n\r\n"
    assert body == b"opaque-encrypted-response" and observed == [_hello(PERPLEXITY)]
    assert calls == [("resolve", PERPLEXITY), ("dial", "1.1.1.1", 443)]


@pytest.mark.parametrize("host", ["api.kraken.com", "api.anthropic.com", "perplexity.ai", "www.perplexity.ai",
                                  "docs.perplexity.ai", "api.perplexity.ai.evil.example", "127.0.0.1",
                                  "api.typesafe.ai"])
def test_the_research_listener_refuses_every_other_host_before_dns(host) -> None:
    headers, _, calls, _ = asyncio.run(_run(_request(host), scope=PAID_RESEARCH, hosts={PERPLEXITY}))
    assert headers.startswith(b"HTTP/1.1 403") and calls == []


@pytest.mark.parametrize("scope", ["market", "provider"])
def test_perplexity_is_never_admitted_on_the_market_or_provider_listener(scope) -> None:
    headers, _, calls, _ = asyncio.run(_run(_request(PERPLEXITY), scope=scope))
    assert headers.startswith(b"HTTP/1.1 403") and calls == []


def test_the_research_listener_checks_the_tls_name_like_the_other_listeners() -> None:
    _, body, calls, _ = asyncio.run(_run(_request(PERPLEXITY), scope=PAID_RESEARCH, hosts={PERPLEXITY},
                                         hello=_hello("api.kraken.com")))
    assert body == b"" and calls == [("resolve", PERPLEXITY)]  # domain fronting never dials


@pytest.mark.parametrize(("scope", "hosts"), [
    (PAID_RESEARCH, None), (PAID_RESEARCH, set()), (PAID_RESEARCH, {"example.com"}),
    (PAID_RESEARCH, {PERPLEXITY, "api.kraken.com"}), (PAID_RESEARCH, {"API.PERPLEXITY.AI"}),
    (PAID_RESEARCH, PERPLEXITY), ("news_feeds", {PERPLEXITY}), ("market", {PERPLEXITY}), ("provider", {PERPLEXITY}),
])
def test_a_listener_admits_only_hosts_its_reviewed_catalogue_names(scope, hosts) -> None:
    with pytest.raises(ValueError):
        ConnectProxy(scope, hosts=hosts)


def test_the_real_provider_transport_sends_a_connect_the_strict_admission_accepts() -> None:
    """HTTPX's CONNECT for the Agent API, as the installed transport sends it, passes the research listener."""
    from trade_graph.adapters.models.transport import HttpxProviderHttp

    async def scenario():
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
        transport = HttpxProviderHttp(timeout=2, proxy=f"http://127.0.0.1:{port}", trust_env=False,
                                      allowed_urls=("https://api.perplexity.ai/v1/agent",))
        try:
            with pytest.raises(TimeoutError):  # the transport's uncertain outcome; nothing was sent upstream
                await asyncio.to_thread(transport.post_json, "https://api.perplexity.ai/v1/agent", {"x": 1},
                                        {"Authorization": "Bearer synthetic-not-a-key-000000"})
            [connect] = captured
            assert b"synthetic-not-a-key" not in connect  # the key never reaches the proxy
            assert proxy_module._connect_host(connect, REVIEWED_ROUTE_HOSTS[PAID_RESEARCH]) == PERPLEXITY
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


# --- the proxy command line -------------------------------------------------------------------------------------

def test_the_command_line_takes_reviewed_routes_on_their_own_ports() -> None:
    assert proxy_module.parse_route("paid_research=8082:api.perplexity.ai") == (
        PAID_RESEARCH, 8082, frozenset({PERPLEXITY}))


@pytest.mark.parametrize("args", [
    ["--route", "paid_research=8080:api.perplexity.ai"],  # the provider port
    ["--route", "paid_research=8081:api.perplexity.ai"],  # the market port
    ["--route", "paid_research=443:api.perplexity.ai"],
    ["--route", "paid_research=8082:example.com"],
    ["--route", "paid_research=8082:"],
    ["--route", "unknown=8082:api.perplexity.ai"],
    ["--route", "paid_research=8082:api.perplexity.ai", "--route", "paid_research=8083:api.perplexity.ai"],
    ["--route", "paid_research"],
])
def test_the_command_line_refuses_unreviewed_or_clashing_routes(args, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        proxy_module.main(args)
    assert exc.value.code == 2
    assert "refused" in capsys.readouterr().err


# --- the owner's network profile --------------------------------------------------------------------------------

def test_an_older_profile_without_routes_still_loads_and_hashes_as_before() -> None:
    value = profile()
    assert value.reviewed_routes == {}
    document = value.model_dump(mode="json")
    assert "reviewed_routes" not in document  # an older owner file re-serialises byte for byte
    assert value.sha256 == document_sha256(document)
    with pytest.raises(KeyError):
        value.route_url(PAID_RESEARCH)


def test_a_profile_lists_the_paid_research_route_with_its_port_and_hosts() -> None:
    value = profile(reviewed_routes=ROUTE)
    assert value.route_url(PAID_RESEARCH) == "http://172.29.17.2:8082"
    assert value.route_hosts(PAID_RESEARCH) == frozenset({PERPLEXITY})
    assert value.route_admits(PAID_RESEARCH, "https://api.perplexity.ai/v1/agent")
    assert value.route_admits(PAID_RESEARCH, "https://api.perplexity.ai/search")
    for url in ("http://api.perplexity.ai/search", "https://api.kraken.com/0/public/Ticker",
                "https://api.perplexity.ai:8443/search", "https://user@api.perplexity.ai/search"):
        assert not value.route_admits(PAID_RESEARCH, url), url
    loaded = PreparedSubscriptionNetworkProfile.model_validate(json.loads(value.model_dump_json()))
    assert loaded == value and loaded.sha256 == value.sha256 != profile().sha256


@pytest.mark.parametrize("routes", [
    {"unknown": {"port": 8082, "hosts": [PERPLEXITY]}},
    {"paid_research": {"port": 8082, "hosts": ["example.com"]}},
    {"paid_research": {"port": 8082, "hosts": []}},
    {"paid_research": {"port": 8082, "hosts": [PERPLEXITY, PERPLEXITY]}},
    {"paid_research": {"port": 8082, "hosts": ["API.PERPLEXITY.AI"]}},
    {"paid_research": {"port": 8080, "hosts": [PERPLEXITY]}},
    {"paid_research": {"port": 8081, "hosts": [PERPLEXITY]}},
    {"paid_research": {"port": 80, "hosts": [PERPLEXITY]}},
    {"paid_research": {"port": "8082", "hosts": [PERPLEXITY]}},
    {"paid_research": {"port": 8082, "hosts": [PERPLEXITY], "credentials": []}},
    {"paid_research": {"port": 8082, "hosts": PERPLEXITY}},
])
def test_a_profile_refuses_unreviewed_hosts_clashing_ports_and_coerced_values(routes) -> None:
    with pytest.raises(ValueError):
        profile(reviewed_routes=routes)


def test_the_profile_route_is_hash_bound_like_every_other_field() -> None:
    value = profile(reviewed_routes=ROUTE)
    raw = value.model_dump_json().encode()
    changed = json.loads(raw)
    changed["reviewed_routes"]["paid_research"]["port"] = 8083
    other = PreparedSubscriptionNetworkProfile.model_validate(changed)
    assert other != value and other.sha256 != value.sha256
    assert hashlib.sha256(raw).hexdigest() != hashlib.sha256(other.model_dump_json().encode()).hexdigest()
