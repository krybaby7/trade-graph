"""The additive ``trading`` part of the frozen HQ contract (W8-C): what the page may rely on.

Today's loss is measured in the account's currency and adds up, a state never claims more than its figures show,
an unavailable scorecard shows no figures, beating cash means a result above zero (D127), and the block may be left
out by an older server but is never null.
"""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from trade_graph.api import hq_contract as contract

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hq"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _usd(amount: str) -> dict:
    return {"amount": amount, "currency": "USD", "display": contract.money_display(amount, "USD")}


def test_every_fixture_carries_the_block_and_the_block_may_be_left_out_but_never_null():
    states = {}
    for path in sorted(FIXTURES.glob("snapshot_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        contract.validate_snapshot(data)
        states[path.stem] = data["trading"]["loss"]["state"]
    assert states["snapshot_live"] == "not_applied" and states["snapshot_empty"] == "not_recorded"
    assert states["snapshot_quiet"] == "within" and states["snapshot_silent"] == "unknown"
    data = _load("snapshot_busy.json")
    del data["trading"]
    contract.validate_snapshot(data)  # an older server
    data["trading"] = None
    with pytest.raises(ValidationError, match="never null"):
        contract.validate_snapshot(data)


def test_the_new_words_and_codes_are_in_the_vocabularies():
    assert {"loss_limit", "day_plan", "safety_exit", "scorecard", "price_collector"} <= set(contract.GLOSSARY_KEYS)
    assert {"money_limits:", "day_plans:", "collector:"} <= set(contract.PROBLEM_PREFIXES)
    assert contract.PROBLEM_PREFIXES[-1] == "other"
    assert "loss_limit_reached" in contract.get_args(contract.NoticeCode)


@pytest.mark.parametrize("change", [
    lambda loss: loss.update(loss=_usd("19")),                                    # doesn't add up
    lambda loss: loss.update(value={**_usd("9994.1"), "currency": "EUR",
                                    "display": contract.money_display("9994.1", "EUR")}),  # not the account's
    lambda loss: loss.update(state="reached"),                                    # below the limit
    lambda loss: loss.update(value=None),                                         # a loss without a value
    lambda loss: loss.update(used_percent=None),                                  # a known loss, no share
    lambda loss: loss.update(used_percent=120.0),                                 # more than all of the limit
    lambda loss: loss.update(day_start_basis=None),                               # a value without its basis
    lambda loss: loss.update(state="not_recorded"),                               # unrecorded, with figures
    lambda loss: loss.update(resumes_at=loss["day_start"]),                       # the day never ends
])
def test_today_s_loss_must_add_up_and_never_claim_more_than_it_shows(change):
    data = _load("snapshot_quiet.json")
    contract.validate_snapshot(copy.deepcopy(data))
    change(data["trading"]["loss"])
    with pytest.raises(ValidationError):
        contract.validate_snapshot(data)


def test_an_unknown_loss_has_no_figure_and_a_stop_needs_none():
    data = _load("snapshot_silent.json")
    loss = data["trading"]["loss"]
    assert loss["loss"] is None and loss["value"] is None and loss["day_start_value"] is not None
    stopped = copy.deepcopy(data)
    stopped["trading"]["loss"]["state"] = "stopped"
    contract.validate_snapshot(stopped)  # a stop is recorded even when the current value can't be read
    loss.update(loss=_usd("1"))
    with pytest.raises(ValidationError):
        contract.validate_snapshot(data)


@pytest.mark.parametrize("change", [
    lambda card: card.update(beats_cash=False),                                   # a positive result beats cash
    lambda card: card.update(beats_btc=None),                                     # known versus, no answer
    lambda card: card.update(versus_btc=None),                                    # an answer without a figure
    lambda card: card.update(status="provisional"),                               # provisional without a reason
    lambda card: card.update(available=False),                                    # unavailable with figures
    lambda card: card.update(unavailable_reason=[{"t": "text", "v": "x"}]),       # available with a reason
    lambda card: card.update(period_end=card["period_start"]),                    # an empty period
    lambda card: card.update(facts=card["facts"] * 3),                            # past the list bound
])
def test_the_scorecard_must_be_consistent(change):
    data = _load("snapshot_quiet.json")
    change(data["trading"]["scorecard"])
    with pytest.raises(ValidationError):
        contract.validate_snapshot(data)


def test_an_unavailable_scorecard_shows_no_figures():
    data = _load("snapshot_live.json")
    card = data["trading"]["scorecard"]
    assert card["available"] is False and card["facts"] == [] and card["result"] is None
    card["result"] = _usd("1")
    with pytest.raises(ValidationError):
        contract.validate_snapshot(data)


@pytest.mark.parametrize("change", [
    lambda item: item.update(state="ok", last_at=None, last_scan_at=None),       # working without a record
    lambda item: item.update(state="no_records"),                                # records said to be none
    lambda item: item.update(last_scan_at="2026-10-09T14:00:00.000Z"),           # a scan newer than the newest
])
def test_the_collector_view_must_be_consistent(change):
    data = _load("snapshot_quiet.json")
    change(data["trading"]["collector"])
    with pytest.raises(ValidationError):
        contract.validate_snapshot(data)


def test_amounts_are_exact_and_the_account_s_currency():
    for path in sorted(FIXTURES.glob("snapshot_*.json")):
        trading = json.loads(path.read_text(encoding="utf-8"))["trading"]
        loss = trading["loss"]
        for key in ("day_start_value", "value", "loss", "limit_amount"):
            if loss[key] is not None:
                assert loss[key]["currency"] == loss["currency"] == "USD", path.name
        card = trading["scorecard"]
        if card["available"]:
            assert card["result"]["currency"] == card["currency"], path.name
