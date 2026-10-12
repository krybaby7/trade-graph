"""Plain words for the reasons the software refuses an order (spec §D.5)."""

import pytest

from trade_graph.api import hq_copy

MINIMUM = "the order was smaller than the exchange's minimum"
VALUATION = "the account's value couldn't be confirmed at that moment"


@pytest.mark.parametrize(("raw", "words"), [
    ("below venue minimum; size was not increased", MINIMUM),
    ("rounding would exceed authorization", MINIMUM),
    ("single asset exposure", "it would have put more than the allowed share of the money into one coin"),
    ("gross exposure", "it would have put more than the allowed share of the money into coins"),
    ("insufficient available balance", "there wasn't enough free money or coins for it"),
    ("no equity", "there wasn't enough free money or coins for it"),
    ("provisional valuation", VALUATION),
    ("native USD valuation lacks a mark or FX for BTC, ETH", VALUATION),
    ("stale native USD valuation", VALUATION),
    ("symbol is outside the active mandate", "its trading permission doesn't cover that order"),
    ("order type is outside the active mandate", "its trading permission doesn't cover that order"),
    ("venue is outside the owner policy", "its trading permission doesn't cover that order"),
    ("incomplete account reconciliation blocks new exposure", "the account's balances were still being double-checked"),
    ("unknown instrument BTC/XYZ", "the exchange's rules for that coin aren't known"),
    ("no positive reference price", hq_copy.REASON["prices"]),
    ("expired mandate allows management only", hq_copy.REASON["mandate"]),
])
def test_refusal_reasons_map_to_plain_words(raw, words):
    assert hq_copy.reason_plain(raw) == words
    assert hq_copy.reason_segment(raw) == {"t": "text", "v": words, "code": raw}
    live = hq_copy.reason_plain(raw, live=True)
    assert "practice" not in live
    assert hq_copy.reason_segment(raw, live=True) == {"t": "text", "v": live, "code": raw}


def test_refused_order_story_has_practice_and_live_wording():
    before, after = hq_copy.STORY["order_refused"]
    assert "practice" in before and after == ". No money moved."
    live_before, live_after = hq_copy.LIVE_STORY["order_refused"]
    assert "practice" not in live_before + live_after
    assert hq_copy.reason_plain("no positive reference price", live=True) == hq_copy.LIVE_REASON["prices"]


def test_error_class_names_still_read_as_a_technical_error():
    assert hq_copy.reason_plain("StaleState") == "a technical error"
    assert hq_copy.reason_plain("ValidationFailure") == "a technical error"
