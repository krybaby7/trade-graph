"""Perplexity Search API request and strict result parsing (T36). Synthetic fixtures; no network."""

from datetime import date
from pathlib import Path

import pytest

from trade_graph.adapters.models.transport import provider_payload
from trade_graph.adapters.research.perplexity_search import (
    SEARCH_ENDPOINT,
    MalformedSearchResponse,
    SearchQuery,
    build_search_body,
    parse_search_response,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "perplexity"


def _fixture(name: str) -> dict:
    return provider_payload((FIXTURES / name).read_bytes())


def test_query_is_bounded_and_the_body_is_minimal() -> None:
    assert SEARCH_ENDPOINT == "https://api.perplexity.ai/search"
    query = SearchQuery(query="  Solana outage  ", recency="week")
    assert query.query == "Solana outage" and query.max_results == 5
    assert build_search_body(query) == {"query": "Solana outage", "max_results": 5,
                                        "search_recency_filter": "week"}
    assert build_search_body(SearchQuery(query="BTC ETF flows", max_results=20)) == {
        "query": "BTC ETF flows", "max_results": 20}
    for bad in ({"query": ""}, {"query": "x" * 401}, {"query": "ok", "max_results": 0},
                {"query": "ok", "max_results": 21}, {"query": "ok", "recency": "hour"},
                {"query": "ok", "domains": ["example.org"]}):
        with pytest.raises(ValueError):
            SearchQuery.model_validate(bad)


def test_results_keep_title_link_dates_and_snippet_in_rank_order() -> None:
    request_id, hits = parse_search_response(_fixture("search_results.json"))
    assert request_id == "search_synthetic_1"
    assert [hit.rank for hit in hits] == [1, 2, 3]
    first = hits[0]
    assert first.title == "Solana validators report short slowdown"
    assert first.url == "https://news.example.org/solana-slowdown"
    assert first.publisher == "news.example.org"
    assert first.published_on == date(2026, 10, 14) and first.last_updated == date(2026, 10, 15)
    assert first.snippet.startswith("Block production slowed")
    assert hits[1].publisher == "markets.example.com"
    # An unreadable date is unknown, never guessed.
    assert hits[2].published_on is None and hits[2].last_updated is None


def test_injection_text_in_a_snippet_is_kept_as_data_and_flagged() -> None:
    _, hits = parse_search_response(_fixture("search_results.json"))
    assert hits[1].untrusted_text is True
    assert "Ignore previous instructions" in hits[1].snippet
    assert hits[0].untrusted_text is False


@pytest.mark.parametrize("payload", [
    {"id": "x"},
    {"id": "x", "results": {"title": "not a list"}},
    {"id": "x", "results": ["not an object"]},
    {"id": "x", "results": [{"url": "https://a.example.org", "snippet": "s"}]},
    {"id": "x", "results": [{"title": "t", "snippet": "s"}]},
    {"id": "x", "results": [{"title": "t", "url": "javascript:alert(1)", "snippet": "s"}]},
    {"id": "x", "results": [{"title": "t", "url": "https://", "snippet": "s"}]},
    {"id": "x", "results": [{"title": "t", "url": "https://a.example.org", "snippet": 7}]},
    {"id": "x", "results": [{"title": "", "url": "https://a.example.org", "snippet": "s"}]},
    {"id": "x", "results": [{"title": "t", "url": "https://a.example.org", "snippet": "s"}] * 21},
    {"id": 12, "results": []},
    ["not", "an", "object"],
])
def test_malformed_responses_are_refused_whole(payload) -> None:
    with pytest.raises(MalformedSearchResponse):
        parse_search_response(payload)


def test_the_malformed_fixture_is_refused() -> None:
    with pytest.raises(MalformedSearchResponse):
        parse_search_response(_fixture("search_malformed.json"))


def test_empty_result_list_is_valid_and_long_text_is_bounded() -> None:
    assert parse_search_response({"id": "empty", "results": []}) == ("empty", [])
    _, hits = parse_search_response({"results": [{"title": "t" * 900, "url": "https://a.example.org/p",
                                                  "snippet": "s" * 9000}]})
    assert len(hits[0].title) == 500 and len(hits[0].snippet) == 4000
