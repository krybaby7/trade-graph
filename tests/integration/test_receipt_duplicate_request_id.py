"""A provider request id that repeats an earlier receipt (W8-A; found by W1-C, D152).

``usage_receipts`` keeps one receipt per ``(provider, provider_request_id)``. A replayed or buggy answer that repeats
an id used to raise ``sqlite3.IntegrityError`` out of ``BudgetGateway.commit`` and leave the reservation ``RESERVED``.
Now the repeat is a typed refusal, the call's bill is unresolved (held at its reserved maximum, never free), the
answer is not used, and every round still ends with a recorded outcome.

Synthetic Perplexity answers in the documented shapes, played by a scripted transport with a made-up key; no network,
real key or paid call.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest
from tests.integration.test_perplexity_research import Setup, _final_without_findings, _fixture

from trade_graph.application.budget import BudgetGateway, DuplicateProviderRequest
from trade_graph.application.web_research import AGENT_CARD_ID
from trade_graph.contracts.models import ModelUsage


def _reserve(budget: BudgetGateway, task: str) -> str:
    return budget.reserve(deployment_id="deployment", role="research", task_id=task, root_task_id=task,
                          price_card_id=AGENT_CARD_ID, max_input=1000, max_output=100, max_tools=0,
                          fx_rate=Decimal("0.9"), fx_buffer=Decimal("1.02"), priority=False, synthetic=False,
                          purpose="research")


def _usage(request_id: str) -> ModelUsage:
    return ModelUsage(uncached_input_tokens=500, billed_output_tokens=20, provider_request_id=request_id)


def test_a_repeated_request_id_is_a_typed_refusal_and_never_an_integrity_error(tmp_path) -> None:
    setup = Setup(tmp_path, [])
    first, second = _reserve(setup.budget, "task-a"), _reserve(setup.budget, "task-b")
    receipt = setup.budget.commit(first, _usage("resp-repeated"), provider="perplexity",
                                  model="anthropic/claude-sonnet-5-5", fx_rate=Decimal("0.9"))
    with pytest.raises(DuplicateProviderRequest, match="already has a receipt"):
        setup.budget.commit(second, _usage("resp-repeated"), provider="perplexity",
                            model="anthropic/claude-sonnet-5-5", fx_rate=Decimal("0.9"))
    assert issubclass(DuplicateProviderRequest, ValueError)  # callers settle a ValueError as unresolved
    assert not issubclass(DuplicateProviderRequest, sqlite3.IntegrityError)
    # Nothing was written for the repeat; the earlier receipt is untouched, and the same reservation's own receipt
    # is still idempotent.
    assert [tuple(row) for row in setup.rows("SELECT reservation_id, provider_request_id FROM usage_receipts")] == [
        (first, "resp-repeated")]
    assert setup.rows("SELECT state FROM budget_reservations WHERE reservation_id = ?", second)[0][0] == "RESERVED"
    assert setup.budget.commit(first, _usage("resp-repeated"), provider="perplexity",
                               model="anthropic/claude-sonnet-5-5", fx_rate=Decimal("0.9")) == receipt


def test_an_agent_answer_repeating_an_earlier_request_id_is_unresolved_and_the_round_is_recorded(tmp_path) -> None:
    def repeated(body: dict) -> dict:
        payload = _final_without_findings(body)
        payload["id"] = "resp-replayed-answer"
        return payload

    setup = Setup(tmp_path, [repeated, repeated])
    first = setup.service.run(setup.ask(max_searches=0))
    assert first.status == "completed" and first.billing == "resolved", first
    second = setup.service.run(setup.ask(max_searches=0, task_id="task-2", root_task_id="task-2"))
    # The repeat is not used: its bill is unknown, so it is held at its maximum and the round fails visibly.
    assert (second.status, second.billing) == ("failed", "unresolved"), second
    [reservation] = second.agent_reservations
    assert second.unresolved_reservations == [reservation]
    assert setup.rows("SELECT state FROM budget_reservations WHERE reservation_id = ?", reservation)[0][0] == \
        "UNCERTAIN"
    assert len(setup.receipts()) == 1
    assert [row[0] for row in setup.rows("SELECT status FROM web_research_outcomes ORDER BY completed_at, rowid")] \
        == ["completed", "failed"]


def test_a_search_answer_repeating_an_earlier_request_id_is_unresolved_and_its_results_are_not_shown(
        tmp_path) -> None:
    def tool_calls(body: dict) -> dict:
        payload = _fixture("agent_tool_call.json")
        call = payload["output"][0]
        payload["output"] = [call, {**call, "id": "fc_synthetic_2", "call_id": "call_synthetic_2",
                                    "arguments": '{"query": "Solana validators", "recency": "week"}'}]
        return payload

    search = _fixture("search_results.json")  # the same search request id, answered twice
    setup = Setup(tmp_path, [tool_calls, search, search, _final_without_findings])
    outcome = setup.service.run(setup.ask(max_searches=2))
    assert outcome.status == "completed" and outcome.billing == "unresolved", outcome
    calls = setup.rows("SELECT c.call_id, c.reservation_id, o.outcome, o.failure, o.receipt_id FROM web_search_calls c "
                       "JOIN web_search_outcomes o ON o.call_id = c.call_id ORDER BY c.sequence")
    assert [(row["outcome"], row["failure"]) for row in calls] == [("results", None),
                                                                  ("uncertain", "duplicate_request_id")]
    assert calls[1]["receipt_id"] is None
    assert setup.rows("SELECT state FROM budget_reservations WHERE reservation_id = ?",
                      calls[1]["reservation_id"])[0][0] == "UNCERTAIN"
    assert outcome.unresolved_reservations == [calls[1]["reservation_id"]]
    # Only the first answer's results were stored and shown; the repeat's were not.
    assert len(setup.rows("SELECT * FROM web_search_sources")) == 3
    assert len(outcome.source_refs) == 3
