"""A euro reference-rate outage labels EUR reporting only; quotes flow and buys stay allowed (owner, 2026-10-13)."""

import asyncio
import json
from datetime import timedelta

from tests.integration.test_execution import _stack

from trade_graph.adapters.market.live_feed import PublicLiveFeed
from trade_graph.adapters.market.paper_feed import FX_RETRY_SECONDS, PublicPaperFeed
from trade_graph.application.paper_service import PaperService


class Transport:
    """Kraken public metadata and ticker answer; the Frankfurter reference rate is down until restored."""

    def __init__(self) -> None:
        self.fx_down = True
        self.fx_calls = 0

    def get_text(self, url: str) -> str:
        if "AssetPairs" in url:
            return json.dumps({"error": [], "result": {"XXBTZUSD": {"wsname": "XBT/USD"}}})
        if "Ticker" in url:
            return json.dumps({"error": [], "result": {"XXBTZUSD": {"a": ["101"], "b": ["100"]}}})
        self.fx_calls += 1
        if self.fx_down:
            raise OSError("PRIVATE outage detail https://private.invalid")
        return json.dumps({"date": "2026-01-01", "base": "USD", "quote": "EUR", "rate": "0.86"})


def test_reference_fx_outage_returns_quotes_and_retries_later(tmp_path):
    clock, _ledger, execution, _broker, portfolio = _stack(tmp_path)
    transport = Transport()
    feed = PublicPaperFeed(execution, [portfolio], ["BTC/USD"], transport=transport)
    observations = feed.poll()
    assert [item.symbol for item in observations] == ["BTC/USD"]
    assert feed.reference_fx_error == "OSError"
    assert feed.next_fx == clock.now() + timedelta(seconds=FX_RETRY_SECONDS)
    assert feed.next_poll is not None
    assert "PRIVATE" not in json.dumps(feed.diagnostic_context)
    transport.fx_down = False
    clock.advance(FX_RETRY_SECONDS)
    feed.poll()
    assert feed.reference_fx_error is None and transport.fx_calls == 2


def test_service_keeps_buying_allowed_and_reports_the_reference_fx_outage(tmp_path):
    clock, ledger, execution, _broker, portfolio = _stack(tmp_path)
    transport = Transport()
    feed = PublicPaperFeed(execution, [portfolio], ["BTC/USD"], transport=transport)
    service = PaperService(ledger.database, execution, public_feed=feed, schedule_intervals={})
    gate = []

    async def scenario():
        await service.tick(wait_feed=True)
        gate.append(execution.blocks_increase("BTC/USD"))
        transport.fx_down = False
        clock.advance(FX_RETRY_SECONDS)
        await service.tick(wait_feed=True)
        await service.stop()

    asyncio.run(scenario())
    assert gate == [False]
    assert not ledger.database.execute(
        "SELECT 1 FROM activity_events WHERE kind = 'public_feed_health'").fetchone()
    events = [json.loads(row[0]) for row in ledger.database.execute(
        "SELECT payload_json FROM activity_events WHERE kind = 'reference_fx_health' ORDER BY rowid")]
    assert [event["status"] for event in events] == ["degraded", "recovered"]
    assert events[0]["error_type"] == "OSError" and events[0]["feed_blocks_increase"] is False
    assert events[0]["affects"] == "EUR reporting labels only"
    assert "PRIVATE" not in json.dumps(events) and "private.invalid" not in json.dumps(events)


def test_live_feed_also_keeps_quotes_during_a_reference_fx_outage(tmp_path):
    clock, _ledger, execution, _broker, portfolio = _stack(tmp_path)
    feed = PublicLiveFeed(execution, [portfolio], ["BTC/USD"], transport=Transport())
    assert [item.symbol for item in feed.poll()] == ["BTC/USD"]
    assert feed.reference_fx_error == "OSError"
    assert feed.next_fx == clock.now() + timedelta(seconds=FX_RETRY_SECONDS)
