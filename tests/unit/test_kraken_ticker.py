"""One public Kraken Ticker request for every pair: bid and ask for the coin scanner's spread (T31, D170).

The fixture is synthetic (Kraken's format, made-up prices): Kraken was unreachable from the cloud build session.
No test touches the network.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.candle_support import pair_records

from trade_graph.adapters.market.kraken_ticker import (
    TICKER_MAXIMUM_RESPONSE_BYTES,
    TICKER_URL,
    KrakenTickerFetcher,
    TickerFetchError,
    load_ticker_fixture,
    parse_ticker,
    quotes_by_symbol,
)
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import ValidationFailure

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "coin_scanner" / "ticker_synthetic.json"
NOW = datetime(2026, 10, 13, 15, 25, tzinfo=UTC)


def _response() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["response"]


class Scripted:
    def __init__(self, text: str | None = None, error: BaseException | None = None, clock: FrozenClock | None = None,
                 delay: float = 0) -> None:
        self.text, self.error, self.clock, self.delay, self.urls = text, error, clock, delay, []

    def get_text(self, url: str) -> str:
        self.urls.append(url)
        if self.clock is not None:
            self.clock.advance(self.delay)
        if self.error is not None:
            raise self.error
        return self.text


def test_every_well_formed_entry_is_read_as_exact_decimals() -> None:
    parsed = parse_ticker(json.dumps(_response()))
    assert set(parsed.entries) == {"XXBTZUSD", "XETHZUSD", "SOLUSD", "LINKUSD", "USDUCUSD", "XXBTZEUR"}
    btc = parsed.entries["XXBTZUSD"]
    assert (btc.bid, btc.ask, btc.last) == (Decimal("83536.90000"), Decimal("83537.00000"), Decimal("83537.10000"))
    assert all(type(value) is Decimal for value in (btc.bid, btc.ask, btc.last))
    assert [(item.key, item.reason) for item in parsed.rejected] == [("XDGUSD", "invalid_bid")]
    assert parsed.entries_in_response == 7


def test_a_price_that_is_not_kraken_text_is_refused_for_that_entry_only() -> None:
    response = _response()
    response["result"]["SOLUSD"]["a"][0] = 201.15  # a JSON number, which Kraken does not send
    response["result"]["LINKUSD"]["b"][0] = "-1"
    response["result"]["USDUCUSD"]["c"] = []
    parsed = parse_ticker(json.dumps(response))
    reasons = {item.key: item.reason for item in parsed.rejected}
    assert reasons["SOLUSD"] == "invalid_ask" and reasons["LINKUSD"] == "invalid_bid"
    assert reasons["USDUCUSD"] == "invalid_last"
    assert "XXBTZUSD" in parsed.entries


def test_a_response_that_cannot_be_trusted_is_refused_as_a_whole() -> None:
    with pytest.raises(ValidationFailure):
        parse_ticker('{"error": [], "result": {}}')
    with pytest.raises(ValidationFailure):
        parse_ticker('{"error": ["EGeneral:Invalid arguments"], "result": {}}')
    with pytest.raises(ValidationFailure):
        parse_ticker('{"error": [], "result": {"A": {}, "A": {}}}')
    with pytest.raises(ValidationFailure):
        parse_ticker('{"error": [], "result": {"A": {"a": [NaN]}}}')
    with pytest.raises(ValidationFailure):
        parse_ticker(" " * (TICKER_MAXIMUM_RESPONSE_BYTES + 1))


def test_a_rate_limit_answer_is_its_own_failure_so_the_caller_stops() -> None:
    clock = FrozenClock(NOW)
    transport = Scripted('{"error": ["EAPI:Rate limit exceeded"]}')
    with pytest.raises(TickerFetchError) as caught:
        KrakenTickerFetcher(transport, clock).fetch()
    assert caught.value.category == "rate_limited"


def test_one_plain_request_for_all_pairs_is_made_and_dated_after_it_arrives() -> None:
    clock = FrozenClock(NOW)
    transport = Scripted(json.dumps(_response()), clock=clock, delay=2.5)
    snapshot = KrakenTickerFetcher(transport, clock).fetch()
    assert transport.urls == [TICKER_URL] and TICKER_URL == "https://api.kraken.com/0/public/Ticker"
    assert snapshot.requested_at == NOW and snapshot.received_at == NOW + timedelta(seconds=2.5)
    assert snapshot.evidence_label == "live_public"
    assert len(snapshot.entries) == 6


def test_a_failed_request_names_a_fixed_category_and_never_the_response_text() -> None:
    class Refused(Exception):
        response = type("Response", (), {"status_code": 429})()

    for error, category in ((OSError("secret detail"), "request_failed"), (Refused(), "rate_limited")):
        with pytest.raises(TickerFetchError) as caught:
            KrakenTickerFetcher(Scripted(error=error), FrozenClock(NOW)).fetch()
        assert caught.value.category == category and "secret" not in str(caught.value)
    with pytest.raises(TickerFetchError) as caught:
        KrakenTickerFetcher(Scripted("not json"), FrozenClock(NOW)).fetch()
    assert caught.value.category == "response_invalid"


def test_entries_are_matched_to_catalog_pairs_by_kraken_names_and_ambiguity_gives_no_quote() -> None:
    records = [record for record in pair_records().values() if record.usd_quoted]
    parsed = parse_ticker(json.dumps(_response()))
    matched = quotes_by_symbol(parsed.entries, records)
    assert set(matched.quotes) == {"BTC/USD", "ETH/USD", "SOL/USD", "LINK/USD", "USDUC/USD"}
    assert matched.quotes["BTC/USD"].key == "XXBTZUSD"  # base XXBT + quote ZUSD
    assert matched.quotes["SOL/USD"].key == "SOLUSD"    # the pair's own altname
    assert matched.unmatched_keys == 1                    # XXBTZEUR is not a USD pair
    response = _response()
    response["result"]["XBTUSD"] = response["result"]["XXBTZUSD"]  # a second key naming the same pair
    twice = quotes_by_symbol(parse_ticker(json.dumps(response)).entries, records)
    assert "BTC/USD" not in twice.quotes and twice.ambiguous == ("BTC/USD",)


def test_the_fixture_loader_keeps_the_fixture_label_and_refuses_a_live_one(tmp_path) -> None:
    snapshot = load_ticker_fixture(FIXTURE)
    assert snapshot.evidence_label == "synthetic_fixture"
    assert snapshot.received_at == datetime(2026, 10, 13, 15, 25, tzinfo=UTC)
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    document["evidence_label"] = "live_public"
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValidationFailure, match="fixture"):
        load_ticker_fixture(forged)
