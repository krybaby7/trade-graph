"""Bounded fetch. Pages are data and cannot authorize an action."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from urllib.parse import urlparse

from trade_graph.domain.errors import UnsafeTarget

Resolver = Callable[[str], list[str]]


def assert_public_url(url: str, resolver: Resolver, *, redirects: list[str] | None = None) -> None:
    hops = [url, *(redirects or [])]
    if len(hops) > 3:
        raise UnsafeTarget("too many redirects")
    for hop in hops:
        parsed = urlparse(hop)
        if parsed.scheme not in {"https"}:
            raise UnsafeTarget("only https")
        host = parsed.hostname
        if host is None:
            raise UnsafeTarget("missing host")
        addresses = resolver(host)
        if not addresses:
            raise UnsafeTarget("unresolved")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            ):
                raise UnsafeTarget(address)


def strip_active_content(html: str, limit: int = 100_000) -> str:
    clipped = html[:limit]
    clipped = re.sub(r"(?is)<script.*?>.*?</script>", "", clipped)
    clipped = re.sub(r"(?is)<style.*?>.*?</style>", "", clipped)
    return re.sub(r"(?is)<[^>]+>", " ", clipped)
