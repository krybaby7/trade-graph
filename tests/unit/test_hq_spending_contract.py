"""The additive ``ai_spending`` part of the frozen HQ contract (T40): what the page may rely on.

Amounts are USD, a part with no call is null (never a zero cost), unresolved calls are never free, the lines add
up to the total, and the block may be left out by an older server but is never null.
"""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from trade_graph.api import hq_contract as contract

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hq"


def _busy() -> dict:
    return json.loads((FIXTURES / "snapshot_busy.json").read_text(encoding="utf-8"))


def _service(data: dict, name: str) -> dict:
    return next(item for item in data["ai_spending"]["services"] if item["service"] == name)


def _usd(amount: str) -> dict:
    return {"amount": amount, "currency": "USD", "display": contract.money_display(amount, "USD")}


def test_every_fixture_carries_the_block_and_the_block_may_be_left_out_but_never_null():
    for path in sorted(FIXTURES.glob("snapshot_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert [item["service"] for item in data["ai_spending"]["services"]] == ["perplexity", "jev"], path.name
        contract.validate_snapshot(data)
    data = _busy()
    del data["ai_spending"]
    contract.validate_snapshot(data)  # an older server
    data["ai_spending"] = None
    with pytest.raises(ValidationError, match="never null"):
        contract.validate_snapshot(data)


def test_the_alert_is_a_watch_card_and_the_new_words_have_glossary_keys():
    assert contract.ATTENTION_SEVERITY["spending_alert"] == "watch"
    assert {"estimated_cost", "unresolved_cost", "testing_alert", "prepaid_credit"} <= set(contract.GLOSSARY_KEYS)
    assert contract.SPENDING_SERVICES == ("perplexity", "jev")


@pytest.mark.parametrize("change", [
    lambda line: line.update(unresolved=_usd("0.01"), unresolved_calls=0),       # held without an open call
    lambda line: line.update(unresolved=_usd("0.5")),                            # total no longer adds up
    lambda line: line.update(actual={**_usd("0.15"), "currency": "EUR",
                                     "display": contract.money_display("0.15", "EUR")}),  # USD only
    lambda line: line.update(unresolved_calls=99),                               # more open calls than calls
    lambda line: line.update(actual=None, estimated=None),                       # settled calls without amounts
    lambda line: line.update(unresolved=None),                                   # unknown hold, total still known
])
def test_a_line_must_be_honest_about_unknown_costs(change):
    data = _busy()
    research = next(item for item in _service(data, "perplexity")["lines"] if item["id"] == "research")
    change(research["amounts"])
    with pytest.raises(ValidationError):
        contract.validate_snapshot(data)


def test_an_unknown_hold_makes_the_line_and_the_service_total_unknown():
    data = _busy()
    perplexity = _service(data, "perplexity")
    research = next(item for item in perplexity["lines"] if item["id"] == "research")
    research["amounts"].update(unresolved=None, total=None)
    with pytest.raises(ValidationError, match="unknown"):
        contract.validate_snapshot(data)  # the service total still claims a known hold
    perplexity["total"].update(unresolved=None, total=None)
    contract.validate_snapshot(data)


def test_lines_add_up_to_the_service_total():
    data = _busy()
    perplexity = _service(data, "perplexity")
    perplexity["total"]["calls"] += 1
    with pytest.raises(ValidationError, match="add up"):
        contract.validate_snapshot(data)
    data = _busy()
    _service(data, "perplexity")["total"]["actual"] = _usd("99")
    with pytest.raises(ValidationError):
        contract.validate_snapshot(data)


def test_jev_has_only_the_news_alarm_line_and_perplexity_never_has_it():
    data = _busy()
    jev, perplexity = _service(data, "jev"), _service(data, "perplexity")
    moved = copy.deepcopy(jev["lines"][0])
    perplexity["lines"].append(moved)
    with pytest.raises(ValidationError, match="news alarm"):
        contract.validate_snapshot(data)


def test_the_testing_alert_gives_way_to_a_monthly_limit():
    data = _busy()
    perplexity = _service(data, "perplexity")
    perplexity["status"] = "monthly_limit"
    with pytest.raises(ValidationError, match="monthly"):
        contract.validate_snapshot(data)
    perplexity["monthly_limit"] = {"limit": _usd("45"), "month_start": "2026-10-01T00:00:00.000Z",
                                   "month": copy.deepcopy(perplexity["total"]),
                                   "text": [{"t": "text", "v": "This month so far."}]}
    with pytest.raises(ValidationError, match="testing alert"):
        contract.validate_snapshot(data)
    perplexity["alert"] = None
    contract.validate_snapshot(data)


def test_services_are_listed_once_in_order():
    data = _busy()
    data["ai_spending"]["services"].reverse()
    with pytest.raises(ValidationError, match="Perplexity, then Jev"):
        contract.validate_snapshot(data)


def test_codex_lines_add_up_and_a_share_is_a_percentage():
    data = _busy()
    codex = data["ai_spending"]["codex"]
    codex["turns"] += 1
    with pytest.raises(ValidationError, match="add up"):
        contract.validate_snapshot(data)
    data = _busy()
    data["ai_spending"]["codex"]["lines"][0]["share_percent"] = 120.0
    with pytest.raises(ValidationError):
        contract.validate_snapshot(data)
