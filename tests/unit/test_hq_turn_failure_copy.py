"""Plain words for a Trader turn whose answer was set aside (owner, 2026-10-13)."""

import pytest

from trade_graph.api import hq_copy

CHECKS = "its answer didn't pass the checks"


@pytest.mark.parametrize(("raw", "words"), [
    ("subscription validation: schema mismatch", hq_copy.MODEL_FAILURE["validation"]),
    ("subscription temporary: provider error", hq_copy.MODEL_FAILURE["temporary"]),
    ("Trader cites research outside its persisted snapshot", hq_copy.REASON["outside_evidence"]),
    ("1 validation error for TraderReply\nstrategy_id\n  Field required", CHECKS),
    ("strategy template is unknown", CHECKS),
    ("trader payload action is invalid", CHECKS),
    ("order quantity must be a decimal string", CHECKS),
    ("mandate does not allow a discretionary experiment", hq_copy.REASON["not_permitted"]),
])
def test_set_aside_trader_answers_read_in_plain_words(raw, words):
    assert hq_copy.reason_plain(raw) == words
    live = hq_copy.reason_plain(raw, live=True)
    assert "practice" not in live
    assert hq_copy.reason_segment(raw) == {"t": "text", "v": words, "code": hq_copy.technical(raw)}
