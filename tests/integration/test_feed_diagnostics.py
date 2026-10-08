"""Retained bounded public feed diagnostics omit raw errors, URLs and responses."""

import asyncio
import json
from datetime import timedelta

import pytest
from tests.integration.test_execution import _stack
from tests.integration.test_paper_service import Feed

from trade_graph.adapters.market.paper_feed import PublicPaperFeed
from trade_graph.application.paper_service import PaperService
from trade_graph.domain.clock import utc_iso


def test_recurring_feed_failures_and_recovery_retain_bounded_safe_evidence(tmp_path):
    clock, ledger, execution, _broker, portfolio = _stack(tmp_path)
    first_failure = clock.now()
    feed = Feed(*(OSError("PRIVATE RAW ERROR https://private.invalid/key") for _ in range(3)), [])
    feed.diagnostic_context = {"stage": "ticker", "symbol": "BTC/USD", "url": "PRIVATE URL"}
    service = PaperService(ledger.database, execution, public_feed=feed, schedule_intervals={})

    async def scenario():
        await service.tick(wait_feed=True)
        clock.advance(1)
        await service.tick(wait_feed=True)
        clock.advance(30)
        await service.tick(wait_feed=True)
        clock.advance(1)
        await service.tick(wait_feed=True)
        await service.stop()

    asyncio.run(scenario())
    diagnostics = [json.loads(row["payload_json"]) for row in ledger.database.execute(
        "SELECT payload_json FROM activity_events WHERE kind='public_feed_health' ORDER BY rowid")]
    assert [item["status"] for item in diagnostics] == ["degraded", "degraded", "recovered"]
    assert [item["consecutive_failures"] for item in diagnostics] == [1, 3, 3]
    assert diagnostics[0]["stage"] == "ticker" and diagnostics[0]["symbol"] == "BTC/USD"
    assert diagnostics[0]["error_type"] == "OSError"
    assert diagnostics[1]["observed_at_utc"] == utc_iso(first_failure + timedelta(seconds=31))
    assert diagnostics[-1]["first_failed_at_utc"] == utc_iso(first_failure)
    assert all(item["financial_management_continues"] for item in diagnostics)
    assert [item["feed_blocks_increase"] for item in diagnostics] == [True, True, False]
    assert "PRIVATE" not in json.dumps(diagnostics) and "private.invalid" not in json.dumps(diagnostics)


@pytest.mark.parametrize("stage", ["metadata", "ticker", "reference_fx"])
def test_public_feed_identifies_failure_boundary_without_retaining_response_or_url(tmp_path, stage):
    clock, _ledger, execution, _broker, portfolio = _stack(tmp_path)
    class Transport:
        def get_text(self, url):
            actual = "metadata" if "AssetPairs" in url else "ticker" if "Ticker" in url else "reference_fx"
            if actual == stage:
                raise OSError("PRIVATE failure detail")
            if actual == "metadata":
                return json.dumps({"error": [], "result": {"XXBTZUSD": {"wsname": "XBT/USD"}}})
            if actual == "ticker":
                return json.dumps({"error": [], "result": {"XXBTZUSD": {"a": ["101"], "b": ["100"]}}})
            raise AssertionError("unexpected FX response")
    feed = PublicPaperFeed(execution, [portfolio], ["BTC/USD"], transport=Transport())
    with pytest.raises(OSError, match="PRIVATE"):
        feed.poll()
    assert feed.diagnostic_context == {"stage": stage, "symbol": "BTC/USD" if stage == "ticker" else None}
    assert feed.next_poll is None
