"""Credential-free CONNECT admission and bounded opaque TLS relay."""

import asyncio
import contextlib
import importlib.util
from dataclasses import replace

import pytest


def test_reviewed_proxy_module_exists():
    assert importlib.util.find_spec("trade_graph.kernel.subscription_proxy") is not None


def _proxy():
    from trade_graph.kernel import subscription_proxy
    return subscription_proxy


def _hello(host="api.anthropic.com", *, extra=b"", fragmented=False):
    name = host.encode("ascii")
    sni = b"\x00" + len(name).to_bytes(2, "big") + name
    sni = len(sni).to_bytes(2, "big") + sni
    extensions = b"\x00\x00" + len(sni).to_bytes(2, "big") + sni + extra
    body = b"\x03\x03" + bytes(32) + b"\x00\x00\x02\x13\x01\x01\x00"
    body += len(extensions).to_bytes(2, "big") + extensions
    message = b"\x01" + len(body).to_bytes(3, "big") + body
    parts = [message[:20], message[20:]] if fragmented else [message]
    return b"".join(b"\x16\x03\x01" + len(part).to_bytes(2, "big") + part for part in parts)


async def _run_request(request, *, scope="provider", answers=("1.1.1.1",),
                       hello=None, limits=None, responder=None):
    module = _proxy()
    calls = []
    observed = []

    async def upstream(reader, writer):
        try:
            if responder:
                await responder(reader, writer, observed)
            else:
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
        return answers

    async def dial(address, port):
        calls.append(("dial", address, port))
        return await asyncio.open_connection("127.0.0.1", upstream_port)

    proxy = module.ConnectProxy(scope, limits=limits or module.ProxyLimits(), resolver=resolve, dialer=dial)
    listener = await proxy.start("127.0.0.1", 0)
    port = listener.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
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


def _request(host="api.anthropic.com", headers=b""):
    return f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n".encode() + headers + b"\r\n"


@pytest.mark.parametrize("host", ["api.anthropic.com", "claude.ai", "platform.claude.com"])
def test_official_subscription_hosts_relay_opaque_tls_to_checked_numeric_ip(host):
    headers, body, calls, observed = asyncio.run(_run_request(_request(host), hello=_hello(host)))
    assert headers == b"HTTP/1.1 200 Connection Established\r\n\r\n"
    assert body == b"opaque-encrypted-response"
    assert calls == [("resolve", host), ("dial", "1.1.1.1", 443)]
    assert observed == [_hello(host)]


@pytest.mark.parametrize("host", ["api.kraken.com", "api.frankfurter.dev"])
def test_market_listener_admits_only_its_public_destination_domains(host):
    headers, body, calls, _ = asyncio.run(_run_request(_request(host), scope="market", hello=_hello(host)))
    assert headers.startswith(b"HTTP/1.1 200")
    assert body == b"opaque-encrypted-response"
    assert calls[-1] == ("dial", "1.1.1.1", 443)


@pytest.mark.parametrize("scope,host", [
    ("market", "api.anthropic.com"), ("provider", "api.kraken.com"),
    ("provider", "claude.com"), ("provider", "downloads.claude.ai"),
    ("provider", "api.anthropic.com.evil.example"), ("provider", "127.0.0.1"),
    ("provider", "[::1]"), ("provider", "api.openai.com"),
])
def test_rejected_domains_never_resolve_or_dial(scope, host):
    headers, _, calls, _ = asyncio.run(_run_request(_request(host), scope=scope))
    assert headers.startswith(b"HTTP/1.1 403")
    assert calls == []


@pytest.mark.parametrize("raw_request", [
    b"GET https://api.anthropic.com/ HTTP/1.1\r\n\r\n",
    b"CONNECT api.anthropic.com:80 HTTP/1.1\r\n\r\n",
    b"CONNECT api.anthropic.com:443 HTTP/1.0\r\n\r\n",
    b"CONNECT user:pass@api.anthropic.com:443 HTTP/1.1\r\n\r\n",
    _request(headers=b"Proxy-Authorization: Basic synthetic\r\n"),
    _request(headers=b"Authorization: Bearer synthetic\r\n"),
    _request(headers=b"Content-Length: 0\r\n"),
    _request(headers=b"Transfer-Encoding: chunked\r\n"),
    _request(headers=b"Host: claude.ai:443\r\n"),
    _request(headers=b" Folded: disallowed\r\n"),
    _request(headers=b"X-Secret: synthetic\r\n"),
])
def test_connect_rejects_ambiguous_requests_credentials_and_bodies(raw_request):
    headers, _, calls, _ = asyncio.run(_run_request(raw_request))
    assert headers.startswith((b"HTTP/1.1 400", b"HTTP/1.1 403"))
    assert calls == []


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.1", "169.254.169.254", "0.0.0.0", "224.0.0.1",
    "192.0.2.1", "::1", "fc00::1", "fe80::1", "ff02::1", "::ffff:127.0.0.1",
])
def test_any_non_global_dns_answer_refuses_whole_destination(address):
    headers, _, calls, _ = asyncio.run(_run_request(_request(), answers=("1.1.1.1", address)))
    assert headers.startswith(b"HTTP/1.1 403")
    assert calls == [("resolve", "api.anthropic.com")]


@pytest.mark.parametrize("hello", [
    _hello("claude.ai"), b"GET / HTTP/1.1\r\n\r\n", _hello(extra=b"\xfe\x0d\x00\x00"),
    _hello(extra=b"\x00\x2a\x00\x00"),
])
def test_raw_tcp_domain_fronting_and_encrypted_client_hello_never_dial(hello):
    _, body, calls, _ = asyncio.run(_run_request(_request(), hello=hello))
    assert body == b""
    assert calls == [("resolve", "api.anthropic.com")]


def test_fragmented_tls_client_hello_is_checked_and_preserved():
    hello = _hello(fragmented=True)
    _, body, _, observed = asyncio.run(_run_request(_request(), hello=hello))
    assert body == b"opaque-encrypted-response"
    assert observed == [hello]


def test_oversized_connect_headers_refuse_without_dns():
    limits = replace(_proxy().ProxyLimits(), max_header_bytes=128)
    headers, _, calls, _ = asyncio.run(_run_request(
        _request(headers=b"User-Agent: " + b"x" * 200 + b"\r\n"), limits=limits))
    assert headers.startswith(b"HTTP/1.1 400")
    assert calls == []


def test_total_tunnel_byte_limit_includes_client_hello_and_both_directions():
    hello = _hello()
    limits = replace(_proxy().ProxyLimits(), max_tunnel_bytes=len(hello) + 5)
    _, body, _, observed = asyncio.run(_run_request(_request(), hello=hello, limits=limits))
    assert observed == [hello]
    assert body == b"opaqu"


def test_empty_dns_never_dials():
    headers, _, calls, _ = asyncio.run(_run_request(_request(), answers=()))
    assert headers.startswith(b"HTTP/1.1 502")
    assert calls == [("resolve", "api.anthropic.com")]


def test_scope_and_limits_require_bounded_reviewed_configuration():
    module = _proxy()
    with pytest.raises(ValueError):
        module.ConnectProxy("any")
    for kwargs in [{"max_connections": 0}, {"header_timeout": 0}, {"max_header_bytes": 0},
                   {"max_tunnel_bytes": 0}, {"tunnel_timeout": float("inf")}]:
        with pytest.raises(ValueError):
            module.ProxyLimits(**kwargs)


def test_real_openssl_client_hello_is_accepted_without_tls_interception():
    import ssl

    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    client = ssl.create_default_context().wrap_bio(incoming, outgoing, server_hostname="api.anthropic.com")
    with pytest.raises(ssl.SSLWantReadError):
        client.do_handshake()
    hello = outgoing.read()
    _, body, calls, observed = asyncio.run(_run_request(_request(), hello=hello))
    assert body == b"opaque-encrypted-response"
    assert observed == [hello]
    assert calls[-1] == ("dial", "1.1.1.1", 443)


def test_slow_headers_and_missing_tls_hello_release_connection_slots():
    async def scenario():
        module = _proxy()
        calls = []

        async def resolve(host):
            calls.append(host)
            return ("1.1.1.1",)

        limits = replace(module.ProxyLimits(), header_timeout=0.03, hello_timeout=0.03)
        proxy = module.ConnectProxy("provider", limits=limits, resolver=resolve)
        listener = await proxy.start("127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        writers = []
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writers.append(writer)
            writer.write(b"CONNECT ")
            await writer.drain()
            result = await asyncio.wait_for(reader.read(), 1)
            assert result.startswith(b"HTTP/1.1 502")
            assert calls == []
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writers.append(writer)
            writer.write(_request())
            await writer.drain()
            assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")
            assert await asyncio.wait_for(reader.read(), 1) == b""
            assert calls == ["api.anthropic.com"]
        finally:
            for writer in writers:
                writer.close()
                await writer.wait_closed()
            await proxy.close()
        assert proxy._tasks == set()

    asyncio.run(scenario())


def test_maximum_connections_rejects_overflow_without_dns_and_recovers():
    async def scenario():
        module = _proxy()
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def resolve(host):
            calls.append(host)
            entered.set()
            await release.wait()
            return ("1.1.1.1",)

        proxy = module.ConnectProxy(
            "provider", limits=replace(module.ProxyLimits(), max_connections=1), resolver=resolve)
        listener = await proxy.start("127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        first_reader, first_writer = await asyncio.open_connection("127.0.0.1", port)
        second_writer = None
        try:
            first_writer.write(_request())
            await first_writer.drain()
            await asyncio.wait_for(entered.wait(), 1)
            reader, second_writer = await asyncio.open_connection("127.0.0.1", port)
            assert (await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 1)).startswith(b"HTTP/1.1 503")
            assert calls == ["api.anthropic.com"]
        finally:
            await proxy.close()
            assert await asyncio.wait_for(first_reader.read(), 1) == b""
            for writer in (first_writer, second_writer):
                if writer:
                    writer.close()
                    await writer.wait_closed()
        assert proxy._tasks == set()

    asyncio.run(scenario())


def test_resolver_deadline_refuses_without_leaking_raw_errors(capsys):
    async def scenario():
        module = _proxy()

        async def resolve(host):
            await asyncio.Event().wait()

        proxy = module.ConnectProxy(
            "provider", limits=replace(module.ProxyLimits(), connect_timeout=0.03), resolver=resolve)
        listener = await proxy.start("127.0.0.1", 0)
        reader, writer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
        try:
            writer.write(_request())
            await writer.drain()
            assert (await asyncio.wait_for(reader.read(), 1)).startswith(b"HTTP/1.1 502")
        finally:
            writer.close()
            await writer.wait_closed()
            await proxy.close()

    asyncio.run(scenario())
    assert capsys.readouterr() == ("", "")


def test_idle_and_absolute_tunnel_deadlines_close_opaque_connections():
    async def scenario(*, continuous):
        module = _proxy()
        observed = []

        async def upstream(reader, writer, captured):
            captured.append(await reader.readexactly(len(_hello())))
            try:
                while continuous:
                    writer.write(b"x")
                    await writer.drain()
                    await asyncio.sleep(0.005)
                await reader.read()
            except (ConnectionError, asyncio.CancelledError):
                pass

        limits = replace(module.ProxyLimits(), idle_timeout=0.04, tunnel_timeout=0.16)
        _, body, calls, captured = await _run_request(
            _request(), hello=_hello(), limits=limits, responder=upstream)
        observed.extend(captured)
        assert calls[-1] == ("dial", "1.1.1.1", 443)
        assert observed == [_hello()]
        assert bool(body) is continuous
        if continuous:
            assert len(body) >= 20  # Activity survives the idle deadline until absolute timeout.

    asyncio.run(scenario(continuous=False))
    asyncio.run(scenario(continuous=True))


def test_oversized_tls_hello_and_connection_failure_never_expose_payload(capsys):
    limits = replace(_proxy().ProxyLimits(), max_hello_bytes=40, hello_timeout=0.03)
    _, body, calls, _ = asyncio.run(_run_request(_request(), hello=_hello(), limits=limits))
    assert body == b""
    assert calls == [("resolve", "api.anthropic.com")]
    assert capsys.readouterr() == ("", "")


def test_numeric_production_dial_does_not_resolve_the_hostname_again(monkeypatch):
    async def scenario():
        module = _proxy()
        server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        loop = asyncio.get_running_loop()
        real_connect = loop.sock_connect
        calls = []

        async def connect(sock, destination):
            calls.append(destination)
            await real_connect(sock, ("127.0.0.1", port))

        async def forbidden_dns(*args, **kwargs):
            pytest.fail("dial performed a second DNS resolution")

        monkeypatch.setattr(loop, "sock_connect", connect)
        monkeypatch.setattr(loop, "getaddrinfo", forbidden_dns)
        try:
            _, writer = await module._dial("1.1.1.1", 443)
            writer.close()
            await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()
        assert calls == [("1.1.1.1", 443)]

    asyncio.run(scenario())


def test_cli_rejects_wildcard_public_and_identical_listener_configuration(capsys):
    for args in [
        ["--listen-host", "0.0.0.0"], ["--listen-host", "1.1.1.1"],
        ["--provider-port", "8080", "--market-port", "8080"],
    ]:
        with pytest.raises(SystemExit) as exc:
            _proxy().main(args)
        assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "refused" in captured.err


def test_checked_address_fallback_handles_unreachable_ipv6_without_new_dns_or_hostnames():
    async def scenario():
        module = _proxy()
        calls = []
        hello = _hello()

        async def upstream(reader, writer):
            assert await reader.readexactly(len(hello)) == hello
            writer.write(b"ok")
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        async def resolve(host):
            calls.append(("resolve", host))
            return ("2606:4700:4700::1111", "1.1.1.1")

        async def dial(address, target_port):
            calls.append(("dial", address, target_port))
            if ":" in address:
                raise OSError("synthetic unreachable IPv6")
            return await asyncio.open_connection("127.0.0.1", port)

        proxy = module.ConnectProxy("provider", resolver=resolve, dialer=dial)
        listener = await proxy.start("127.0.0.1", 0)
        reader, writer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
        try:
            writer.write(_request())
            await writer.drain()
            assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")
            writer.write(hello)
            await writer.drain()
            assert await asyncio.wait_for(reader.read(), 1) == b"ok"
            assert calls == [("resolve", "api.anthropic.com"),
                             ("dial", "2606:4700:4700::1111", 443), ("dial", "1.1.1.1", 443)]
        finally:
            writer.close()
            await writer.wait_closed()
            await proxy.close()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())
