"""The frozen HQ snapshot contract and the synthetic fixtures the page is built against."""

import copy
import json
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from trade_graph.api import hq_contract as contract
from trade_graph.api.security import _PRIVATE_KEYS
from trade_graph.application.paper_service import PAUSED_WORK
from trade_graph.application.service_controller import ACTIVE_SERVICE
from trade_graph.contracts.models import PauseProfile

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hq"
NAMES = sorted(path.name for path in FIXTURES.glob("*.json"))


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value


def test_fixture_set_covers_the_agreed_states():
    assert set(NAMES) >= {
        "snapshot_quiet.json", "snapshot_empty.json", "snapshot_busy.json", "snapshot_allowance_paused.json",
        "snapshot_silent.json", "snapshot_safety_stop.json", "snapshot_offline.json", "snapshot_reader.json",
        "snapshot_live.json",
    }


@pytest.mark.parametrize("name", NAMES)
def test_fixture_validates_and_round_trips(name):
    data = _load(name)
    snapshot = contract.validate_snapshot(data)
    assert snapshot.model_dump(mode="json", by_alias=True, exclude_unset=True) == data


@pytest.mark.parametrize(("name", "code"), [
    ("snapshot_quiet.json", "calm"), ("snapshot_empty.json", "no_run"), ("snapshot_busy.json", "working"),
    ("snapshot_allowance_paused.json", "quota_paused"), ("snapshot_silent.json", "silent"),
    ("snapshot_safety_stop.json", "safety_stop"), ("snapshot_offline.json", "crashed"),
])
def test_fixture_shows_its_state(name, code):
    assert _load(name)["now"]["code"] == code


def test_empty_fixture_invents_nothing():
    data = _load("snapshot_empty.json")
    assert {dept["light"] for dept in data["departments"]} == {"off"}
    assert [item["kind"] for item in data["story"]["items"]] == ["account_opened"]
    assert not [item for item in data["calendar"]["items"] if item["kind"] == "dept_turn"]
    assert data["calendar"]["notes"]
    assert set(data["money"]["counts"].values()) == {0}
    assert data["money"]["pays_for_itself"]["status"] == "unknown"


def test_view_only_and_live_fixtures_offer_no_decisions():
    reader, live = _load("snapshot_reader.json"), _load("snapshot_live.json")
    assert reader["decisions"] == [] and reader["checkins"] is None and not reader["viewer"]["can_act"]
    assert live["mode"] == "live" and live["decisions"] == [] and not live["viewer"]["can_act"]
    assert not [text for text in _strings(live) if "Practice money" in text or "Paper results are simulated" in text]


def test_contract_keys_survive_redaction():
    for key in contract.contract_keys():
        normalized = key.lower().replace("-", "_")
        assert normalized not in _PRIVATE_KEYS
        assert not normalized.endswith(("_api_key", "_password", "_secret"))


def test_vocabularies_match_the_code_they_describe():
    assert contract.PAUSE_PROFILES == get_args(PauseProfile)
    assert set(contract.PAUSED_WORK_PROFILES) == PAUSED_WORK
    assert set(contract.ACTIVE_SERVICE_STATUSES) == ACTIVE_SERVICE
    assert tuple(contract.NOW_PRESENTATION) == contract.NOW_CODES
    assert tuple(contract.ATTENTION_SEVERITY) == contract.ATTENTION_CODES
    assert sorted(dept for dept, _ in contract.DEPARTMENT_ORDER) == sorted(contract.DEPT_IDS)


def test_scalar_helpers_follow_the_money_and_time_rules():
    assert contract.iso_ms("2026-10-09T13:59:10.506789+00:00") == "2026-10-09T13:59:10.506Z"
    assert contract.money_display("8939.7", "EUR") == "€8,939.70"
    assert contract.money_display("-12.405", "USD") == chr(0x2212) + "$12.40"
    assert contract.money_display("1234.5", "CHF") == "1,234.50 CHF"


def _broken(mutate):
    data = _load("snapshot_quiet.json")
    mutate(data)
    with pytest.raises(ValidationError):
        contract.validate_snapshot(data)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(extra=True),
    lambda d: d["now"]["headline"][0].update(code=None),
    lambda d: d["now"].update(attention_count=True),
    lambda d: d["now"].update(attention_count=3),
    lambda d: d["now"].update(tone="busy"),
    lambda d: d.update(as_of="2026-10-09T13:59:10Z"),
    lambda d: d["money"]["practice_value"].update(amount="8939.70"),
    lambda d: d["money"]["practice_value"].update(display="€8939.70"),
    lambda d: d["money"]["change"].update(total=None),
    lambda d: d["notices"][0].update(link="https://example.invalid/"),
    lambda d: d["departments"].reverse(),
    lambda d: d["calendar"]["up_next"].append("turn:s-trader:2026-10-09T21:56:26.000Z"),
    lambda d: d["attention"].reverse(),
    lambda d: d["viewer"].update(role="reader"),
    lambda d: d["viewer"].update(auth="bearer"),
    lambda d: d.update(mode="live"),
    # The three additive keys may be absent but are never null (review fix plan section 1).
    lambda d: d["team"]["allowance"].update(stop_line_state=None),
    lambda d: _trader_next(d).update(status=None),
    lambda d: d["calendar"].update(estimates=None),
    # ... and each one agrees with the fields it describes.
    lambda d: d["team"]["allowance"].update(stop_line_state="maybe"),
    lambda d: d["team"]["allowance"].update(stop_line_percent=None),
    lambda d: d["team"]["allowance"].update(stop_line_state="off"),
    lambda d: _trader_next(d).update(overdue=True),
    lambda d: _trader_next(d).update(status="due_now"),
    lambda d: d["calendar"]["estimates"][0].update(count=31),
    lambda d: d["calendar"]["estimates"][0].update(count=0),
    lambda d: d["calendar"]["estimates"][0].update(dept="accounts"),
    lambda d: d["calendar"]["estimates"].append(dict(d["calendar"]["estimates"][0])),
])
def test_contract_rejects_drift(mutate):
    _broken(mutate)


def _trader_next(data: dict) -> dict:
    return next(dept for dept in data["departments"] if dept["id"] == "trader")["next"]


def _group(data: dict) -> dict:
    return next(item for item in data["calendar"]["items"] if item.get("count"))


@pytest.mark.parametrize("mutate", [
    lambda item: item.update(count=None),
    lambda item: item.update(until=None),
    lambda item: item.pop("until"),
    lambda item: item.update(count=1),
    lambda item: item.update(count=True),
    lambda item: item.update(kind="trial_end"),
    lambda item: item.update(until="2026-10-10T09:29:59.999Z"),  # before the group's first deadline
    lambda item: item.update(until="2026-10-10T11:30:00.001Z"),  # beyond the two-hour group window
])
def test_grouped_calendar_items_carry_a_count_and_a_range(mutate):
    data = _load("snapshot_busy.json")
    item = _group(data)
    assert item["count"] == 2 and item["kind"] in contract.CALENDAR_GROUPED_KINDS
    contract.validate_snapshot(data)
    mutate(item)
    with pytest.raises(ValidationError):
        contract.validate_snapshot(data)
    single = _load("snapshot_busy.json")
    group = _group(single)
    del group["count"], group["until"]
    contract.validate_snapshot(single)  # an older server's single items carry neither key


def test_new_keys_are_emitted_in_every_fixture():
    for name in NAMES:
        data = _load(name)
        assert data["team"]["allowance"]["stop_line_state"] in ("on", "off", "unknown"), name
        assert all(dept["next"]["status"] for dept in data["departments"] if dept["next"]), name
        assert isinstance(data["calendar"]["estimates"], list), name
    quiet = _load("snapshot_quiet.json")["calendar"]
    assert [series["schedule_id"] for series in quiet["estimates"]] == ["s-trader"]


def test_omittable_keys_may_be_absent():
    data = copy.deepcopy(_load("snapshot_quiet.json"))
    data["decisions"][3]["facts"] = {}
    contract.validate_snapshot(data)
    # An older server or fixture may omit the additive keys entirely.
    del data["team"]["allowance"]["stop_line_state"]
    for dept in data["departments"]:
        if dept["next"]:
            del dept["next"]["status"]
    del data["calendar"]["estimates"]
    contract.validate_snapshot(data)
    unknown = copy.deepcopy(_load("snapshot_quiet.json"))
    unknown["team"]["allowance"].update(stop_line_percent=None, stop_line_state="unknown")
    contract.validate_snapshot(unknown)
