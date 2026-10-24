"""The HQ day plans document (W8-C, ``trade-graph.hq.plans.v1``): what the page may rely on.

A synthetic document only; no database, network or order.
"""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from trade_graph.api import hq_plans_contract as contract
from trade_graph.api.security import _PRIVATE_KEYS, redact

AT = "2026-10-16T08:00:00.000Z"
words = lambda text: [{"t": "text", "v": text}]  # noqa: E731


def _entry(**changes) -> dict:
    return {"id": "dp-1-0", "status": "partly_filled", "tone": "neutral", "text": words("Buy 5 SOL at 99.5 USD."),
            "quantity": "5", "filled": "2", "unprotected": "0", "limit_price": "99.5", "stop_price": "97",
            "take_profit_price": "104", "expires_at": "2026-10-17T00:00:00.000Z",
            "fills": [words("Bought 2 SOL at 99.5 USD.")], "more_fills": 0,
            "exits": [{"id": "x-1", "status": "waiting", "tone": "neutral", "text": words("Safety exit for 2 SOL."),
                       "fills": [], "more_fills": 0}], "more_exits": 0, **changes}


def _document(**changes) -> dict:
    plan = {"id": "dp-1", "symbol": "SOL/USD", "installed_at": AT, "day_start": "2026-10-16T00:00:00.000Z",
            "day_end": "2026-10-17T00:00:00.000Z", "current": True, "replaced_today": 0,
            "headline": words("1 waiting buy."), "strategy": words("Strategy slow-trend-pullback, version 1."),
            "watching": words("Watching RSI."), "quote": {"label": "In their words", "text": "Synthetic plan."},
            "invalidation": None, "entries": [_entry()], "events": [words("Put in place.")], "more_events": 0}
    return {"schema": contract.SCHEMA, "as_of": AT, "available": True, "status_text": None,
            "day": {"start": "2026-10-16T00:00:00.000Z", "end": "2026-10-17T00:00:00.000Z"},
            "limits_recorded": True, "headline": words("1 day plan today."), "plans": [plan], "more_plans": 0,
            "refusals": [{"at": AT, "symbol": "SOL/USD", "text": words("A plan was refused."), "technical": "x"}],
            "refusals_total": 1, **changes}


def test_a_document_validates_and_no_key_is_redacted() -> None:
    document = _document()
    contract.validate_plans(document)
    keys = contract.contract_keys()
    assert {"plans", "entries", "exits", "refusals", "unprotected", "limits_recorded"} <= keys
    assert not {key for key in keys if key.lower() in _PRIVATE_KEYS}
    assert redact(copy.deepcopy(document)) == document


@pytest.mark.parametrize("change", [
    lambda entry: entry.update(filled="6"),                                   # more than the quantity
    lambda entry: entry.update(unprotected="3"),                              # more unprotected than filled
    lambda entry: entry.update(unprotected="1"),                              # unprotected without attention
    lambda entry: entry.update(status="filled"),                              # filled, not all of it
    lambda entry: entry.update(stop_price="100"),                             # a stop above the buy
    lambda entry: entry.update(take_profit_price="99"),                       # a target below the buy
    lambda entry: entry.update(status="lost"),                                # not a status
    lambda entry: entry.update(quantity="5.0"),                               # not exact canonical text
    lambda entry: entry.update(fills=entry["fills"] * (contract.FILLS_MAX + 1)),  # past the bound
])
def test_an_entry_must_add_up(change) -> None:
    document = _document()
    change(document["plans"][0]["entries"][0])
    with pytest.raises(ValidationError):
        contract.validate_plans(document)


@pytest.mark.parametrize("change", [
    lambda document: document.update(available=False),                         # unavailable with plans
    lambda document: document.update(day=None),                                # available without a day
    lambda document: document.update(refusals_total=0),                        # fewer in total than listed
    lambda document: document["plans"].append(copy.deepcopy(document["plans"][0])),  # one plan twice
    lambda document: document["plans"][0].update(installed_at="2026-10-15T23:00:00.000Z"),  # outside its day
    lambda document: document["refusals"].append({**document["refusals"][0], "at": "2026-10-16T09:00:00.000Z"}),
    lambda document: document["plans"][0].update(symbol="SOLUSD"),             # not a pair
])
def test_the_document_must_be_consistent(change) -> None:
    document = _document()
    change(document)
    with pytest.raises(ValidationError):
        contract.validate_plans(document)


def test_an_unavailable_document_lists_nothing() -> None:
    contract.validate_plans(_document(available=False, day=None, plans=[], refusals=[], refusals_total=0,
                                      limits_recorded=None, status_text=words("No day-plan records yet.")))
