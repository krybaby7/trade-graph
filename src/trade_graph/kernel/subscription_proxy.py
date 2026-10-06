"""Credential-free, non-intercepting HTTPS CONNECT admission for protected roles.

Provider authentication/refresh hosts follow the official network documentation:
https://code.claude.com/docs/en/network-config (reviewed 2026-10-07).
Only the native, previously authenticated Claude CLI belongs on the provider port.
The market port admits Kraken/Frankfurter domains, never inference domains.

Encrypted HTTP paths and billing cannot be inspected here. The protected caller
must separately enforce public market paths, official subscription authentication,
disabled extra usage, immutable CLI/environment settings and spending authority.
No request, authentication material, TLS payload or exception is logged/persisted.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ipaddress
import math
import signal
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

PROVIDER_HOSTS = frozenset({"api.anthropic.com", "claude.ai", "platform.claude.com"})
MARKET_HOSTS = frozenset({"api.kraken.com", "api.frankfurter.dev"})

Resolver = Callable[[str], Awaitable[Sequence[str]]]
Dialer = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]

_RESPONSES = {
    200: b"HTTP/1.1 200 Connection Established\r\n\r\n",
    400: b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
    403: b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
    502: b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
    503: b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
}


class _Refused(Exception):
    """Only a fixed status leaves the boundary, never rejected input or errors."""

    def __init__(self, status: int = 403):
        self.status = status


@dataclass(frozen=True)
class ProxyLimits:
    max_header_bytes: int = 8192
    max_hello_bytes: int = 65536
    max_connections: int = 16
    max_tunnel_bytes: int = 64 * 1024 * 1024
    header_timeout: float = 10
    connect_timeout: float = 10
    hello_timeout: float = 10
    idle_timeout: float = 180
    tunnel_timeout: float = 900

    def __post_init__(self):
        for name, maximum in (("max_header_bytes", 65536), ("max_hello_bytes", 262144),
                              ("max_connections", 256), ("max_tunnel_bytes", 1024 * 1024 * 1024)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("bounded integer proxy limit required")
        for name in ("header_timeout", "connect_timeout", "hello_timeout", "idle_timeout", "tunnel_timeout"):
            value = getattr(self, name)
            if (type(value) not in {int, float} or not math.isfinite(value)
                    or not 0 < value <= 3600):
                raise ValueError("finite bounded proxy timeout required")


def _connect_host(payload: bytes, allowed: frozenset[str]) -> str:
    try:
        text = payload.decode("ascii")
        lines = text.split("\r\n")
        if lines[-2:] != ["", ""] or any(ord(c) < 32 and c not in "\r\n" for c in text):
            raise _Refused(400)
        method, target, version = lines[0].split(" ")
        if method != "CONNECT" or version != "HTTP/1.1":
            raise _Refused(400)
        host, port = target.rsplit(":", 1)
        host = host.lower()
        if port != "443" or host not in allowed or target.lower() != host + ":443":
            raise _Refused()
        headers = {}
        for line in lines[1:-2]:
            name, value = line.split(":", 1)
            if not name or name != name.strip() or not name.isascii():
                raise _Refused(400)
            name = name.lower()
            if name not in {"host", "connection", "proxy-connection", "user-agent", "accept"} or name in headers:
                raise _Refused(400)
            value = value.strip(" ")
            if not value or len(value) > 1024 or any(ord(c) < 32 or ord(c) > 126 for c in value):
                raise _Refused(400)
            if name == "accept" and value != "*/*":
                raise _Refused(400)
            headers[name] = value
        if headers.get("host", "").lower() != target.lower():
            raise _Refused(400)
        return host
    except (UnicodeError, ValueError):
        raise _Refused(400) from None


async def _resolve(host: str) -> Sequence[str]:
    records = await asyncio.get_running_loop().getaddrinfo(
        host, 443, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
    )
    return tuple(dict.fromkeys(record[4][0] for record in records))


def _checked_addresses(answers: Sequence[str]) -> tuple[str, ...]:
    if not answers or len(answers) > 32:
        raise _Refused(502)
    checked = []
    for value in answers:
        try:
            if type(value) is not str or "%" in value:
                raise ValueError
            address = ipaddress.ip_address(value)
        except ValueError:
            raise _Refused() from None
        if (not address.is_global or address.is_multicast or address.is_reserved
                or address.is_unspecified or address.is_loopback or address.is_link_local
                or (isinstance(address, ipaddress.IPv6Address)
                    and (address.ipv4_mapped or address.sixtofour or address.teredo))):
            raise _Refused()
        checked.append(str(address))
    return tuple(dict.fromkeys(checked))


async def _dial(address: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Use the already checked literal address; neither API resolves a hostname."""
    family = socket.AF_INET6 if ipaddress.ip_address(address).version == 6 else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setblocking(False)
    try:
        await asyncio.get_running_loop().sock_connect(sock, (address, port))
        return await asyncio.open_connection(sock=sock)
    except BaseException:
        sock.close()
        raise


class _Cursor:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def take(self, length: int) -> bytes:
        if length < 0 or self.offset + length > len(self.data):
            raise _Refused()
        result = self.data[self.offset:self.offset + length]
        self.offset += length
        return result

    def integer(self, length: int) -> int:
        return int.from_bytes(self.take(length), "big")

    def done(self) -> bool:
        return self.offset == len(self.data)


def _check_hello(body: bytes, host: str) -> None:
    cursor = _Cursor(body)
    if cursor.take(2) != b"\x03\x03":
        raise _Refused()  # TLS 1.2/1.3 legacy ClientHello version.
    cursor.take(32)
    session_size = cursor.integer(1)
    if session_size > 32:
        raise _Refused()
    cursor.take(session_size)
    cipher_size = cursor.integer(2)
    if cipher_size < 2 or cipher_size % 2:
        raise _Refused()
    cursor.take(cipher_size)
    if cursor.take(cursor.integer(1)) != b"\x00":
        raise _Refused()
    extensions = _Cursor(cursor.take(cursor.integer(2)))
    if not cursor.done():
        raise _Refused()
    seen = set()
    sni = None
    while not extensions.done():
        kind = extensions.integer(2)
        value = extensions.take(extensions.integer(2))
        if kind in seen or kind in {0xFE0D, 42}:  # ECH and early data are inadmissible.
            raise _Refused()
        seen.add(kind)
        if kind == 0:
            names = _Cursor(value)
            entries = _Cursor(names.take(names.integer(2)))
            if not names.done() or entries.integer(1) != 0:
                raise _Refused()
            try:
                sni = entries.take(entries.integer(2)).decode("ascii").lower()
            except UnicodeError:
                raise _Refused() from None
            if not entries.done():
                raise _Refused()
    if sni != host:
        raise _Refused()


async def _client_hello(reader: asyncio.StreamReader, host: str, maximum: int) -> bytes:
    wire = bytearray()
    handshake = bytearray()
    expected = None
    while expected is None or len(handshake) < expected:
        header = await reader.readexactly(5)
        size = int.from_bytes(header[3:], "big")
        if (header[:3] not in {b"\x16\x03\x01", b"\x16\x03\x02", b"\x16\x03\x03"}
                or not 1 <= size <= 16384 or len(wire) + 5 + size > maximum):
            raise _Refused()
        record = await reader.readexactly(size)
        wire.extend(header + record)
        handshake.extend(record)
        if len(handshake) >= 4:
            if handshake[0] != 1:
                raise _Refused()
            expected = 4 + int.from_bytes(handshake[1:4], "big")
            if expected > maximum:
                raise _Refused()
    if len(handshake) != expected:
        raise _Refused()
    _check_hello(bytes(handshake[4:]), host)
    return bytes(wire)


class ConnectProxy:
    """One fixed-scope listener; production supplies no custom resolver/dialer."""

    def __init__(self, scope: str, *, limits: ProxyLimits | None = None,
                 resolver: Resolver = _resolve, dialer: Dialer = _dial):
        if scope not in {"provider", "market"}:
            raise ValueError("exact provider or market proxy scope required")
        self.allowed = PROVIDER_HOSTS if scope == "provider" else MARKET_HOSTS
        self.limits = limits or ProxyLimits()
        if type(self.limits) is not ProxyLimits:
            raise ValueError("exact reviewed proxy limits required")
        self._resolver = resolver
        self._dialer = dialer
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task] = set()
        self._closing = False

    async def start(self, host: str, port: int) -> asyncio.Server:
        address = ipaddress.ip_address(host)
        if (not (address.is_private or address.is_loopback) or address.is_unspecified
                or address.is_multicast or address.is_link_local or self._server is not None):
            raise ValueError("one exact internal or loopback listener required")
        self._server = await asyncio.start_server(
            self.handle, host, port, limit=self.limits.max_header_bytes, backlog=self.limits.max_connections,
        )
        return self._server

    async def close(self) -> None:
        self._closing = True
        if self._server:
            self._server.close()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._server:
            await self._server.wait_closed()

    async def _reply(self, writer: asyncio.StreamWriter, status: int) -> None:
        writer.write(_RESPONSES[status])
        async with asyncio.timeout(self.limits.header_timeout):
            await writer.drain()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        admitted = not self._closing and len(self._tasks) < self.limits.max_connections
        if admitted:
            self._tasks.add(task)
        upstream_writer = None
        established = False
        try:
            if not admitted:
                await self._reply(writer, 503)
                return
            async with asyncio.timeout(self.limits.header_timeout):
                header = await reader.readuntil(b"\r\n\r\n")
            if len(header) > self.limits.max_header_bytes:
                raise _Refused(400)
            host = _connect_host(header, self.allowed)
            async with asyncio.timeout(self.limits.connect_timeout):
                addresses = _checked_addresses(await self._resolver(host))
            # Check ClientHello before any upstream effect. CONNECT clients only send
            # that hello after the fixed 200 response. Admission failure closes TLS.
            await self._reply(writer, 200)
            established = True
            async with asyncio.timeout(self.limits.hello_timeout):
                hello = await _client_hello(reader, host, self.limits.max_hello_bytes)
            if len(hello) >= self.limits.max_tunnel_bytes:
                raise _Refused()
            async with asyncio.timeout(self.limits.connect_timeout):
                for address in addresses:
                    try:
                        upstream_reader, upstream_writer = await self._dialer(address, 443)
                        break
                    except OSError:
                        continue
                else:
                    raise _Refused(502)
            async with asyncio.timeout(self.limits.tunnel_timeout):
                upstream_writer.write(hello)
                async with asyncio.timeout(self.limits.idle_timeout):
                    await upstream_writer.drain()
                await self._relay(reader, writer, upstream_reader, upstream_writer, len(hello))
        except _Refused as exc:
            if not established:
                with contextlib.suppress(Exception):
                    await self._reply(writer, exc.status)
        except (asyncio.LimitOverrunError, asyncio.IncompleteReadError):
            if not established:
                with contextlib.suppress(Exception):
                    await self._reply(writer, 400)
        except Exception:
            # Do not expose raw errors (which can include payloads or DNS input).
            if not established:
                with contextlib.suppress(Exception):
                    await self._reply(writer, 502)
        finally:
            for stream in (upstream_writer, writer):
                if stream is not None:
                    stream.close()
            for stream in (upstream_writer, writer):
                if stream is not None:
                    with contextlib.suppress(Exception):
                        async with asyncio.timeout(1):
                            await stream.wait_closed()
            if admitted:
                self._tasks.discard(task)

    async def _relay(self, client_reader, client_writer, upstream_reader, upstream_writer, used: int) -> None:
        remaining = self.limits.max_tunnel_bytes - used
        loop = asyncio.get_running_loop()
        last_activity = loop.time()

        async def pump(source, target):
            nonlocal remaining, last_activity
            while remaining:
                data = await source.read(min(16384, remaining))
                if not data:
                    return
                # Both pumps share the event loop: reserve before yielding in drain.
                data = data[:remaining]
                remaining -= len(data)
                last_activity = loop.time()
                target.write(data)
                async with asyncio.timeout(self.limits.idle_timeout):
                    await target.drain()
                if not remaining:
                    return

        async def idle_watchdog():
            while True:
                wait = self.limits.idle_timeout - (loop.time() - last_activity)
                if wait <= 0:
                    return
                await asyncio.sleep(wait)

        pumps = [asyncio.create_task(pump(client_reader, upstream_writer)),
                 asyncio.create_task(pump(upstream_reader, client_writer)),
                 asyncio.create_task(idle_watchdog())]
        try:
            done, pending = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in pumps:
                task.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)


async def _serve(host: str, provider_port: int, market_port: int) -> None:
    if provider_port == market_port or not all(1024 <= port <= 65535 for port in (provider_port, market_port)):
        raise ValueError("two distinct unprivileged proxy ports required")
    proxies = (ConnectProxy("provider"), ConnectProxy("market"))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    try:
        await proxies[0].start(host, provider_port)
        await proxies[1].start(host, market_port)
        await stop.wait()
    finally:
        await asyncio.gather(*(proxy.close() for proxy in proxies))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--provider-port", type=int, default=8080)
    parser.add_argument("--market-port", type=int, default=8081)
    args = parser.parse_args(argv)
    try:
        asyncio.run(_serve(args.listen_host, args.provider_port, args.market_port))
    except (ValueError, OSError):
        parser.exit(2, "reviewed proxy listener configuration/startup refused\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
