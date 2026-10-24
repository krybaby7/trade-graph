"""HQ decision evidence shows the D114 candle-wait fact and the refusals the Trader was shown."""

from trade_graph.api import evidence

WAIT = {
    "status": "missing_after_wait", "expected_close_time_utc": "2026-10-10T01:00:00.000000Z",
    "latest_stored_close_time_utc": "2026-10-10T00:00:00.000000Z", "waited_seconds": 295,
    "wait_deadline_utc": "2026-10-10T01:05:00.000000Z",
    "note": "The hourly candle that closed at 01:00 UTC was still not stored after waiting 295 seconds; "
            "hourly values use candles through 00:00 UTC.",
}
REFUSAL = {
    "decision_id": "d-1", "refused_at": "2026-10-10T01:05:01.000000Z", "action": "enter", "symbol": "BTC/USD",
    "quantity": "0.00001", "order_type": "market", "limit_price": None, "code": "validation",
    "reason": "below venue minimum; size was not increased",
}


def _payload(history: dict, portfolio: dict) -> dict:
    return {"as_of": "2026-10-10T01:05:01.000000Z", "market": {"BTC/USD": {"history": history}},
            "portfolio": portfolio}


def test_evidence_keeps_expected_candle_and_recent_refusals_as_scalars():
    history = {"expected_candle": {**WAIT, "prompt": {"role": "system"}}}
    portfolio = {"recent_order_refusals": [{**REFUSAL, "messages": [{"content": "x"}]}, "not a row"]}
    retained = evidence._retained_inputs(_payload(history, portfolio), None)
    assert retained["market"]["BTC/USD"]["history"]["expected_candle"] == WAIT
    assert retained["portfolio"]["recent_order_refusals"] == [REFUSAL]


def test_snapshots_before_d114_do_not_appear_to_have_empty_values():
    retained = evidence._retained_inputs(_payload({}, {}), None)
    assert "expected_candle" not in retained["market"]["BTC/USD"]["history"]
    assert "recent_order_refusals" not in retained["portfolio"]
