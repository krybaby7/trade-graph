"""HQ names a failed news check step in plain words (W9-A, D255): ``news_poll:<error>`` from the service tick."""

from __future__ import annotations

from trade_graph.api import hq_contract as contract
from trade_graph.api import hq_copy
from trade_graph.api.hq_state import PROBLEM_PREFIXES


def test_a_failed_news_check_step_has_its_own_plain_words() -> None:
    assert "news_poll:" in contract.PROBLEM_PREFIXES and contract.PROBLEM_PREFIXES[-1] == "other"
    assert "news_poll:" in PROBLEM_PREFIXES
    what, consequence = hq_copy.problem_prefix("news_poll:", live=False)
    assert "news check" in what and "Trading, protection and your records are not affected" in consequence
    assert "practice" not in (what + consequence).lower()  # a live account can use the same words
