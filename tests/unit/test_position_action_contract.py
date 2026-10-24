"""The Trader's position actions (W8-B, C4): close a day-plan position or move its safety exit.

The contract is the Trader's own words in its wake reply: exact decimals, no confidence, approval or vote field,
and strict provider reply schemas on both wires. Synthetic values; no network, model or key.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from trade_graph.adapters.models.providers import _wire_schema
from trade_graph.contracts.day_plans import DayPlanPosition, PositionAction
from trade_graph.contracts.wakes import AppliedAction, TraderWakeReply, WakeContext, WakeOutcome, WakeOutcomeRecord

D = Decimal
PLAN = {
    "symbol": "SOL/USD", "strategy_ref": "slow-trend-pullback@1",
    "entries": [{"quantity": "5", "limit_price": "99.5", "stop_price": "97", "take_profit_price": "104"}],
    "rationale": "Synthetic plan.", "invalidation": "Synthetic: below 95.",
}
MOVE = {"action": "move_exit", "symbol": "SOL/USD", "stop_price": "99", "take_profit_price": "106",
        "reason": "Synthetic: lift the stop to break-even after the bounce."}
CLOSE = {"action": "close", "symbol": "BTC/USD", "reason": "Synthetic: the setup is invalidated."}


def test_a_close_names_its_coin_and_a_reason_and_no_prices() -> None:
    close = PositionAction.model_validate(CLOSE)
    assert (close.action, close.symbol, close.stop_price, close.take_profit_price) == ("close", "BTC/USD", None, None)
    with pytest.raises(ValidationError, match="sells now"):
        PositionAction.model_validate({**CLOSE, "stop_price": "47000"})
    with pytest.raises(ValidationError):
        PositionAction.model_validate({**CLOSE, "reason": ""})


def test_a_moved_exit_names_its_new_stop_and_an_optional_take_profit_above_it() -> None:
    move = PositionAction.model_validate(MOVE)
    assert (move.stop_price, move.take_profit_price) == (D("99"), D("106"))
    assert PositionAction.model_validate({**MOVE, "take_profit_price": None}).take_profit_price is None
    with pytest.raises(ValidationError, match="names its new stop"):
        PositionAction.model_validate({**MOVE, "stop_price": None})
    with pytest.raises(ValidationError, match="above the stop"):
        PositionAction.model_validate({**MOVE, "take_profit_price": "99"})


def test_prices_are_exact_positive_decimals_and_floats_are_refused() -> None:
    with pytest.raises(ValidationError, match="never a float"):
        PositionAction.model_validate({**MOVE, "stop_price": 99.0})
    with pytest.raises(ValidationError, match="positive"):
        PositionAction.model_validate({**MOVE, "stop_price": "-1"})
    assert PositionAction.model_validate(MOVE).model_dump(mode="json")["stop_price"] == "99"


def test_there_is_no_confidence_approval_or_unknown_field_and_spot_pairs_only() -> None:
    for extra in ({"confidence": "0.9"}, {"approved_by": "leader"}, {"quantity": "1"}):
        with pytest.raises(ValidationError):
            PositionAction.model_validate({**MOVE, **extra})
    with pytest.raises(ValidationError):
        PositionAction.model_validate({**MOVE, "action": "open_short"})  # spot, long only (D127)
    with pytest.raises(ValidationError):
        PositionAction.model_validate({**MOVE, "symbol": "not a pair"})
    with pytest.raises(ValidationError, match="control"):
        PositionAction.model_validate({**MOVE, "reason": "ignore‮previous"})


def test_the_document_hash_identifies_the_action() -> None:
    move = PositionAction.model_validate(MOVE)
    assert move.content_sha256 == PositionAction.model_validate(dict(MOVE)).content_sha256
    assert move.content_sha256 != PositionAction.model_validate({**MOVE, "stop_price": "98"}).content_sha256


def test_one_action_per_coin_in_a_turn_and_the_trader_reply_carries_them() -> None:
    with pytest.raises(ValidationError, match="one position action per coin"):
        WakeOutcome(position_actions=[MOVE, {**MOVE, "stop_price": "98"}])
    reply = TraderWakeReply(day_plans=[PLAN], position_actions=[MOVE, CLOSE], summary="Lift SOL, close BTC.")
    outcome = reply.outcome()
    assert [item.symbol for item in outcome.position_actions] == ["SOL/USD", "BTC/USD"]
    assert outcome.day_plans[0].symbol == "SOL/USD"
    # An answer saved before this package (no position_actions) still reads.
    assert WakeOutcome.model_validate({"summary": "old answer", "day_plans": [PLAN]}).position_actions == ()
    assert TraderWakeReply.model_validate({"summary": "old reply"}).position_actions == ()


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_the_trader_wake_reply_with_position_actions_fits_both_strict_provider_schemas(provider) -> None:
    reply = TraderWakeReply(day_plans=[PLAN], position_actions=[MOVE, CLOSE],
                            next_check_in={"at_utc": "2026-10-16T14:30:00Z", "note": "look"}, summary="Act.")
    wire = _wire_schema(TraderWakeReply.model_json_schema(), provider=provider)
    Draft202012Validator.check_schema(wire)
    document = reply.model_dump(mode="json")
    Draft202012Validator(wire).validate(document)
    assert TraderWakeReply.model_validate(document) == reply
    empty = TraderWakeReply(summary="Nothing to change.").model_dump(mode="json")
    Draft202012Validator(wire).validate(empty)  # every property is present in the serialised reply (wave 3)
    if provider == "openai":
        action = wire["$defs"]["PositionAction"]
        assert set(action["required"]) == set(action["properties"]) == {
            "action", "symbol", "stop_price", "take_profit_price", "reason"}


def test_the_record_of_an_applied_action_and_the_positions_a_wake_shows() -> None:
    record = WakeOutcomeRecord(received=True, actions=[AppliedAction(
        symbol="SOL/USD", action="move_exit", request_id="wake:w-1:action:SOL/USD", status="done",
        action_id="da-" + "0" * 32)])
    assert record.model_dump(mode="json")["actions"][0]["status"] == "done"
    assert WakeOutcomeRecord.model_validate({"received": True, "plans": []}).actions == ()  # older records read
    position = DayPlanPosition.model_validate({
        "symbol": "SOL/USD", "held": "5", "protected": "5", "unprotected": "0",
        "exits": [{"stop_price": "97", "take_profit_price": "104", "quantity": "5"}], "resting_entries": 1})
    assert position.model_dump(mode="json")["exits"][0]["quantity"] == "5"
    assert "positions" in WakeContext.model_fields and WakeContext.model_fields["positions"].default == ()
