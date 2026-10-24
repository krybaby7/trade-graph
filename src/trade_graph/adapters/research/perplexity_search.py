"""Perplexity Search API wire format (T36, D150). Results are data and cannot authorize anything.

``POST https://api.perplexity.ai/search`` takes a query and returns ranked results with
title, url, snippet and optional ``date`` and ``last_updated``. The shape follows
Perplexity's published documentation; the first real response is recorded at step L2.
A response that breaks the shape is refused whole, so partial results are never stored.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from trade_graph.contracts.web_research import SearchHit, SearchQuery, looks_like_instruction

SEARCH_ENDPOINT = "https://api.perplexity.ai/search"
MAXIMUM_RESULTS = 20
_TITLE_LIMIT = 500
_SNIPPET_LIMIT = 4000

__all__ = ["SEARCH_ENDPOINT", "MalformedSearchResponse", "SearchQuery", "build_search_body", "parse_search_response"]


class MalformedSearchResponse(ValueError):
    """The provider answered, but not in the documented result shape."""


def build_search_body(query: SearchQuery) -> dict[str, Any]:
    body: dict[str, Any] = {"query": query.query, "max_results": query.max_results}
    if query.recency is not None:
        body["search_recency_filter"] = query.recency
    return body


def parse_search_response(payload: Any) -> tuple[str | None, list[SearchHit]]:
    if not isinstance(payload, dict):
        raise MalformedSearchResponse("search response must be an object")
    request_id = payload.get("id")
    if request_id is not None and (not isinstance(request_id, str) or not 0 < len(request_id) <= 128):
        raise MalformedSearchResponse("search request id must be a short string")
    results = payload.get("results")
    if not isinstance(results, list) or len(results) > MAXIMUM_RESULTS:
        raise MalformedSearchResponse("search results must be a list of at most 20 items")
    hits = []
    for rank, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            raise MalformedSearchResponse("each search result must be an object")
        title, url, snippet = item.get("title"), item.get("url"), item.get("snippet", "")
        if not isinstance(title, str) or not title.strip() or not isinstance(snippet, str):
            raise MalformedSearchResponse("search results need a title and a text snippet")
        publisher = _publisher(url)
        title, snippet = " ".join(title.split())[:_TITLE_LIMIT], " ".join(snippet.split())[:_SNIPPET_LIMIT]
        try:
            hits.append(SearchHit(
                rank=rank, title=title, url=url, publisher=publisher, snippet=snippet,
                published_on=_date(item.get("date")), last_updated=_date(item.get("last_updated")),
                untrusted_text=looks_like_instruction(title) or looks_like_instruction(snippet),
            ))
        except ValidationError as exc:
            raise MalformedSearchResponse("search result is outside its bounds") from exc
    return request_id, hits


def _publisher(url: Any) -> str:
    if not isinstance(url, str) or len(url) > 2048:
        raise MalformedSearchResponse("search results need a link")
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"https", "http"} or not host or "." not in host:
        raise MalformedSearchResponse("search result links must be web addresses")
    return host[4:] if host.startswith("www.") else host


def _date(value: Any) -> date | None:
    """ISO dates only. Anything else is an unknown date, never a guess."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
