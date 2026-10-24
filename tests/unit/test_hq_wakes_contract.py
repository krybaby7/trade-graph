"""The HQ wake log document (T39, D215-D219): every wake with its reasons, department and allowance reading, each
department's next check-in, today's steps, focus coins and alarms. Strict like the HQ snapshot; synthetic values only.
"""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from trade_graph.api import hq_wakes_contract as contract
from trade_graph.api.security import _PRIVATE_KEYS, redact
from trade_graph.contracts import wakes

AT = "2026-10-16T09:58:00.000Z"
TEXT = [{"t": "text", "v": "A waiting buy filled"}]


def _item(cursor: int = 7, **changes) -> dict:
    item = {
        "id": f"wake-{cursor}", "cursor": cursor, "dept": "trader", "origin": "engine", "purpose": "event",
        "status": "completed", "tone": "neutral", "at": AT, "closed_at": "2026-10-16T10:01:00.000Z",
        "headline": TEXT, "reasons": [{"kind": "entry_filled", "count": 1}],
        "events": [[{"t": "text", "v": "Waiting buy filled: 5 SOL at 99.5 USD each (SOL/USD)."}]], "more_events": 0,
        "allowance": {"status": "available", "remaining_percent": "62",
                      "observed_at": "2026-10-16T09:50:00.000Z",
                      "text": [{"t": "term", "key": "allowance", "v": "AI allowance"},
                               {"t": "text", "v": ": 62% left"}]},
        "result": [{"t": "text", "v": "Done after 3 min."}],
        "quote": {"label": "In their words", "text": "The plan stands."}, "notes": [], "link": "/organization",
        "technical": "event: entry_filled",
    }
    item.update(changes)
    return item


def _day() -> dict:
    return {
        "start": "2026-10-16T00:00:00.000Z", "end": "2026-10-17T00:00:00.000Z",
        "steps": [{"step": step, "status": "done", "text": TEXT, "at": AT} for step in wakes.CYCLE_STEPS],
        "check_ins": [{"dept": dept, "at": "2026-10-17T00:00:00.000Z", "basis": "day_start", "status": "planned",
                       "text": TEXT, "quote": None, "set_at": None, "waiting": 0} for dept in wakes.DEPARTMENTS],
        "focus": {"status": "named", "text": TEXT, "revision": 1, "recorded_at": AT, "quote": None,
                  "coins": [{"symbol": "SOL/USD", "quote": {"label": "In their words", "text": "Liquid."},
                             "evidence": 1}],
                  "shortlist": ["SOL/USD", "ADA/USD"]},
        "alarms": {"total": 1, "text": TEXT, "waiting": [{"dept": "trader", "count": 2}],
                   "items": [{"id": "alarm-1", "dept": "trader", "symbol": "SOL/USD", "text": TEXT, "armed_at": AT,
                              "quote": None, "in_day_plan": True}]},
        "counts": {"today": 3, "week": 9, "text": TEXT, "reasons_today": [{"kind": "alarm_fired", "count": 2}],
                   "departments": [{"dept": dept, "today": 3 if dept == "trader" else 0,
                                    "week": 9 if dept == "trader" else 0} for dept in wakes.DEPARTMENTS]},
    }


def _document(**changes) -> dict:
    document = {"schema": contract.SCHEMA, "as_of": AT, "available": True, "status_text": None, "day": _day(),
                "items": [_item(9), _item(7)], "total": 12, "next_before": 7}
    document.update(changes)
    return document


def test_a_complete_document_validates() -> None:
    parsed = contract.validate_wake_log(_document())
    assert parsed.items[0].cursor == 9 and parsed.day.check_ins[0].dept == "trader"


def test_an_older_page_has_no_day_block_and_an_unavailable_log_has_nothing() -> None:
    contract.validate_wake_log(_document(day=None))
    contract.validate_wake_log(_document(available=False, day=None, items=[], total=0, next_before=None,
                                         status_text=TEXT))
    with pytest.raises(ValidationError):
        contract.validate_wake_log(_document(available=False, day=None, status_text=TEXT))  # items without records


@pytest.mark.parametrize("path, value", [
    (("schema",), "trade-graph.hq.v1"),
    (("items", 0, "dept"), "engineer"),               # the five woken departments only
    (("items", 0, "status"), "approved"),
    (("items", 0, "reasons", 0, "kind"), "confidence"),
    (("items", 0, "reasons", 0, "count"), 0),
    (("items", 0, "allowance", "status"), "plenty"),
    (("items", 0, "allowance", "remaining_percent"), 62.0),  # exact decimal text, never a float
    (("items", 0, "allowance", "remaining_percent"), "1e2"),
    (("items", 0, "at"), "2026-10-16T09:58:00Z"),
    (("items", 0, "link"), "https://example.com/"),
    (("items", 0, "closed_at"), None),                # a closed wake says when it closed
    (("items", 0, "quote", "label"), "Summary"),
    (("day", "check_ins", 0, "basis"), "timer"),
    (("day", "focus", "coins", 0, "symbol"), "sol/usd"),
    (("day", "alarms", "items", 0, "symbol"), "SOLUSD"),
    (("day", "steps", 0, "status"), "maybe"),
    (("total",), -1),
])
def test_refused_values(path, value) -> None:
    document = _document()
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        contract.validate_wake_log(document)


def test_open_wakes_have_no_close_time_and_items_are_newest_first_and_unique() -> None:
    contract.validate_wake_log(_document(items=[_item(9, status="queued", closed_at=None, result=TEXT)],
                                         next_before=None))
    with pytest.raises(ValidationError):
        contract.validate_wake_log(_document(items=[_item(9, status="applying")]))
    with pytest.raises(ValidationError):
        contract.validate_wake_log(_document(items=[_item(7), _item(9)]))  # oldest first
    with pytest.raises(ValidationError):
        contract.validate_wake_log(_document(items=[_item(9), _item(9)]))
    with pytest.raises(ValidationError):
        contract.validate_wake_log(_document(next_before=9))  # the next page starts below this one
    with pytest.raises(ValidationError):
        contract.validate_wake_log(_document(items=[_item(9, reasons=[{"kind": "alarm_fired", "count": 1},
                                                                      {"kind": "alarm_fired", "count": 2}])]))


def test_display_bounds_are_document_bounds_not_activity_caps() -> None:
    # A wake may carry any number of triggers (D116): the document lists a few and counts the rest.
    contract.validate_wake_log(_document(items=[_item(9, events=[TEXT] * contract.EVENT_LINES_MAX,
                                                      more_events=500, reasons=[{"kind": "alarm_fired",
                                                                                 "count": 506}])]))
    with pytest.raises(ValidationError):
        contract.validate_wake_log(_document(items=[_item(9, events=[TEXT] * (contract.EVENT_LINES_MAX + 1))]))
    with pytest.raises(ValidationError):
        contract.validate_wake_log(_document(items=[_item(i) for i in range(contract.PAGE_MAX + 1, 0, -1)],
                                             next_before=None))
    assert contract.PAGE_DEFAULT <= contract.PAGE_MAX
    assert contract.PAYLOAD_BUDGET_BYTES == 80 * 1024


def test_vocabularies_are_the_event_engines() -> None:
    assert contract.DEPARTMENTS == wakes.DEPARTMENTS
    assert contract.TRIGGER_KINDS == wakes.TRIGGER_KINDS
    assert contract.WAKE_STATUSES == wakes.WAKE_STATUSES
    assert contract.CYCLE_STEPS == wakes.CYCLE_STEPS


def test_no_key_of_the_document_is_redacted() -> None:
    keys = contract.contract_keys()
    assert {"items", "allowance", "check_ins", "focus", "alarms", "counts", "next_before"} <= keys
    assert not {key for key in keys if key.lower() in _PRIVATE_KEYS}
    document = _document()
    assert redact(copy.deepcopy(document)) == document
