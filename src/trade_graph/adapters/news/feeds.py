"""Read free public RSS and Atom feeds: bounded, keyless, no redirects, no entity declarations (T44).

A feed is fetched with one plain GET carrying no cookie or credential, and with the previous ``ETag`` and
``Last-Modified`` so an unchanged feed is not downloaded again. Documents that declare a DTD or entities are
refused outright, which removes entity-expansion and external-entity attacks without a new dependency.
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

from trade_graph.adapters.research.fetch import assert_public_url
from trade_graph.contracts.news import EvidenceLabel, FeedSource
from trade_graph.domain.errors import UnsafeTarget

MAX_FEED_BYTES = 2_000_000
MAX_ITEMS = 500
MAX_TITLE = 1000
MAX_LINK = 2000
USER_AGENT = "trade-graph-news-alarm/1 (paper trading research; one request per feed every 5 minutes)"
ATOM = "{http://www.w3.org/2005/Atom}"
RSS1 = "{http://purl.org/rss/1.0/}"
DC_DATE = "{http://purl.org/dc/elements/1.1/}date"
_DECLARATION = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)

FetchOutcome = Literal["fetched", "not_modified", "http_error", "transport_error", "refused"]


class FeedRefused(ValueError):
    """A feed body that is not read: too large, broken, or declaring a DTD or entities."""


@dataclass(frozen=True)
class FeedItem:
    title: str
    link: str
    published_at_utc: datetime | None
    item_sha256: str


@dataclass(frozen=True)
class ParsedFeed:
    items: tuple[FeedItem, ...]
    rejected: int


@dataclass(frozen=True)
class FetchResult:
    outcome: FetchOutcome
    http_status: int | None
    body: bytes | None
    etag: str | None
    last_modified: str | None
    evidence: EvidenceLabel
    error_category: str | None


class FeedFetcher(Protocol):
    def fetch(self, feed: FeedSource, *, etag: str | None, last_modified: str | None) -> FetchResult: ...


def canonical_link(url: str) -> str:
    """The link used to detect repeats: lower-case scheme and host, no fragment, no ``utm_`` parameters."""
    if not isinstance(url, str):
        raise ValueError("a link is text")
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("a headline link must be a plain web address")
    netloc = parts.hostname.lower() + (f":{parts.port}" if parts.port else "")
    query = "&".join(item for item in parts.query.split("&")
                     if item and not item.split("=", 1)[0].lower().startswith("utm_"))
    link = urlunsplit((scheme, netloc, parts.path, query, ""))
    if len(link) > MAX_LINK:
        raise ValueError("a headline link is too long")
    return link


def _date(text: str | None) -> datetime | None:
    if not text or not text.strip():
        return None
    value = text.strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
    if parsed is None:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def _atom_link(entry: ElementTree.Element) -> str | None:
    for link in entry.findall(f"{ATOM}link"):
        if link.get("rel", "alternate") == "alternate" and link.get("href"):
            return link.get("href")
    return None


def _rss_link(item: ElementTree.Element) -> str | None:
    link = item.findtext("link") or item.findtext(f"{RSS1}link")
    if link and link.strip():
        return link
    guid = item.find("guid")
    if guid is not None and guid.get("isPermaLink", "true") != "false" and guid.text:
        return guid.text
    return None


def parse_feed(raw: bytes, format: Literal["rss", "atom"] | str = "rss") -> ParsedFeed:
    """Items as published. ``format`` is the expected kind; the document's own root decides how it is read."""
    if not raw or len(raw) > MAX_FEED_BYTES:
        raise FeedRefused("feed body is empty or too large")
    if _DECLARATION.search(raw):
        raise FeedRefused("feeds that declare a DTD or entities are not read")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        raise FeedRefused("feed is not well-formed XML") from None
    atom = root.tag == f"{ATOM}feed"
    elements = (root.findall(f"{ATOM}entry") if atom
                else root.findall("./channel/item") + root.findall(f"{RSS1}item"))
    items, rejected = [], max(0, len(elements) - MAX_ITEMS)
    for element in elements[:MAX_ITEMS]:
        if atom:
            title, link = element.findtext(f"{ATOM}title"), _atom_link(element)
            published = element.findtext(f"{ATOM}published") or element.findtext(f"{ATOM}updated")
        else:
            title, link = element.findtext("title") or element.findtext(f"{RSS1}title"), _rss_link(element)
            published = element.findtext("pubDate") or element.findtext(DC_DATE)
        title = " ".join((title or "").split())
        try:
            link = canonical_link(link) if link else None
        except ValueError:
            link = None
        if not title or len(title) > MAX_TITLE or link is None:
            rejected += 1
            continue
        items.append(FeedItem(title=title, link=link, published_at_utc=_date(published),
                              item_sha256=hashlib.sha256(ElementTree.tostring(element)).hexdigest()))
    return ParsedFeed(items=tuple(items), rejected=rejected)


class FixtureFeedFetcher:
    """Feeds from a local fixture folder with a ``manifest.json``; never touches the network."""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        manifest = json.loads((self.directory / "manifest.json").read_text())
        if manifest.get("evidence") not in {"synthetic_fixture", "recorded_public_fixture"}:
            raise ValueError("a feed fixture folder must label its evidence")
        self.evidence: EvidenceLabel = manifest["evidence"]
        self.feeds: dict = manifest.get("feeds", {})

    def fetch(self, feed: FeedSource, *, etag: str | None, last_modified: str | None) -> FetchResult:
        entry = self.feeds.get(feed.feed_id)
        if not isinstance(entry, dict) or entry.get("url") != feed.url:
            return FetchResult("refused", None, None, None, None, self.evidence, "no_fixture_for_feed")
        path = self.directory / Path(entry["file"]).name
        body = path.read_bytes()
        if len(body) > MAX_FEED_BYTES:
            return FetchResult("refused", None, None, None, None, self.evidence, "too_large")
        return FetchResult("fetched", 200, body, None, None, self.evidence, None)


def _resolve(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})


class HttpxFeedFetcher:
    """One bounded HTTPS GET per feed. Public data only: no key, cookie or account."""

    def __init__(self, *, resolver: Callable[[str], list[str]] = _resolve, transport=None, timeout: float = 10.0,
                 max_bytes: int = MAX_FEED_BYTES, user_agent: str = USER_AGENT, trust_env: bool = True) -> None:
        self.resolver, self.transport, self.timeout = resolver, transport, timeout
        self.max_bytes, self.user_agent, self.trust_env = max_bytes, user_agent, trust_env

    def fetch(self, feed: FeedSource, *, etag: str | None, last_modified: str | None) -> FetchResult:
        import httpx

        def result(outcome: FetchOutcome, status: int | None = None, category: str | None = None) -> FetchResult:
            return FetchResult(outcome, status, None, None, None, "live_public", category)

        try:
            assert_public_url(feed.url, self.resolver)
        except (UnsafeTarget, OSError, ValueError):
            return result("refused", category="unsafe_target")
        headers = {"user-agent": self.user_agent,
                   "accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8"}
        if etag:
            headers["if-none-match"] = etag
        if last_modified:
            headers["if-modified-since"] = last_modified
        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout, follow_redirects=False,
                              trust_env=self.trust_env) as client:
                with client.stream("GET", feed.url, headers=headers) as response:
                    status = response.status_code
                    if status == 304:
                        return result("not_modified", status)
                    if not 200 <= status < 300:
                        return result("http_error", status, "redirect" if 300 <= status < 400 else "status")
                    declared = response.headers.get("content-length")
                    if declared is not None and declared.isdecimal() and int(declared) > self.max_bytes:
                        return result("refused", status, "too_large")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > self.max_bytes:
                            return result("refused", status, "too_large")
                    return FetchResult("fetched", status, bytes(body), response.headers.get("etag"),
                                       response.headers.get("last-modified"), "live_public", None)
        except httpx.HTTPError:
            # The exception text can carry the address and headers; only the category is kept.
            return result("transport_error", category="transport")
