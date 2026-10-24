"""Quotes for the day's coins (W6-D, D223): with the event engine on, the paper feed also quotes the coins the Trader
plans, holds or watches, in one extra Ticker request per poll, so a day plan on a coin other than BTC or ETH can
rest, fill and be protected. Synthetic answers in Kraken's format; no network."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

from trade_graph.adapters.market.paper_feed import PublicPaperFeed
from trade_graph.cli import main
from trade_graph.domain.clock import FrozenClock
from trade_graph.paper_runtime import assemble_paper_runtime

PAIRS = {"XBTUSD": ("XXBTZUSD", "XBT/USD", "100"), "SOLUSD": ("SOLUSD", "SOL/USD", "150"),
         "ADAUSD": ("ADAUSD", "ADA/USD", "0.5")}


class Responses:
    """AssetPairs and Ticker for the requested pairs, Frankfurter for the reference rate; every URL remembered."""

    def __init__(self, clock) -> None:
        self.clock, self.calls, self.fail_extra = clock, [], False

    def get_text(self, url: str) -> str:
        self.calls.append(url)
        parsed = urlparse(url)
        pairs = parse_qs(parsed.query).get("pair", [""])[0].split(",")
        if parsed.path.endswith("/AssetPairs"):
            return json.dumps({"error": [], "result": {PAIRS[pair][0]: {
                "wsname": PAIRS[pair][1], "pair_decimals": 4, "lot_decimals": 8, "ordermin": "0.0001",
                "costmin": "1"} for pair in pairs}})
        if parsed.path.endswith("/Ticker"):
            if self.fail_extra and pairs != ["XBTUSD"]:
                raise TimeoutError("synthetic outage of the extra request")
            self.clock.advance(1)
            return json.dumps({"error": [], "result": {PAIRS[pair][0]: {
                "a": [PAIRS[pair][2], "2", "2"], "b": [PAIRS[pair][2], "2", "2"], "c": [PAIRS[pair][2], "1"],
                "v": ["2", "5"]} for pair in pairs}})
        assert url.endswith("/rate/usd/eur")
        return json.dumps({"base": "USD", "quote": "EUR", "rate": "0.9", "date": "2026-10-17"})

    def tickers(self) -> list[list[str]]:
        return [parse_qs(urlparse(url).query)["pair"][0].split(",") for url in self.calls if "/Ticker" in url]


def _feed(tmp_path, capsys, monkeypatch, coins):
    path = tmp_path / "paper.sqlite"
    clock = FrozenClock(datetime(2026, 10, 17, 9, 0, tzinfo=UTC))
    monkeypatch.setattr("trade_graph.domain.clock.SystemClock", lambda: clock)
    main(["init", "--database", str(path)])
    capsys.readouterr()
    runtime = assemble_paper_runtime(path, clock=clock)
    transport = Responses(clock)
    feed = PublicPaperFeed(runtime.execution, [runtime.portfolio_id], ["BTC/USD"], transport=transport,
                           extra_symbols=coins)
    return runtime, feed, transport


def test_the_days_coins_are_quoted_in_one_extra_request_per_poll(tmp_path, capsys, monkeypatch) -> None:
    runtime, feed, transport = _feed(tmp_path, capsys, monkeypatch, lambda: ["SOL/USD", "ADA/USD", "BTC/USD"])
    try:
        quotes = feed.poll()
        assert [quote.symbol for quote in quotes] == ["BTC/USD", "SOL/USD", "ADA/USD"]
        assert all(quote.venue == "paper" for quote in quotes)
        assert transport.tickers() == [["XBTUSD"], ["SOLUSD", "ADAUSD"]]  # BTC as before, the day's coins together
        stored = runtime.execution.latest_observation("SOL/USD", "2026-10-17T09:00:05.000000Z", "kraken")
        assert stored is not None and str(stored.ask) == "150"
        assert runtime.execution.instrument("paper", "SOL/USD").symbol == "SOL/USD"  # rules for a resting entry
        feed.clock.advance(11)
        feed.poll()
        assert sum(1 for url in transport.calls if "/AssetPairs" in url) == 2  # the new coins' rules asked once
    finally:
        runtime.database.close()


def test_a_failed_extra_request_never_fails_the_feed_and_is_recorded(tmp_path, capsys, monkeypatch) -> None:
    runtime, feed, transport = _feed(tmp_path, capsys, monkeypatch, lambda: ["SOL/USD"])
    transport.fail_extra = True
    try:
        quotes = feed.poll()
        assert [quote.symbol for quote in quotes] == ["BTC/USD"] and feed.extra_error == "TimeoutError"
        transport.fail_extra = False
        feed.clock.advance(11)
        assert [quote.symbol for quote in feed.poll()] == ["BTC/USD", "SOL/USD"] and feed.extra_error is None
        kinds = [row[0] for row in runtime.database.execute(
            "SELECT json_extract(payload_json, '$.status') FROM activity_events WHERE kind = 'public_feed_day_coins' "
            "ORDER BY rowid")]
        assert kinds == ["degraded", "recovered"]
    finally:
        runtime.database.close()


def test_without_a_provider_the_feed_asks_exactly_what_it_asked_before(tmp_path, capsys, monkeypatch) -> None:
    runtime, feed, transport = _feed(tmp_path, capsys, monkeypatch, None)
    try:
        feed.poll()
        assert transport.tickers() == [["XBTUSD"]]
    finally:
        runtime.database.close()
