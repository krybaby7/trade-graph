"""Research's focus-coin reply and the owner's Perplexity switch as contracts (T37; D120, D124, D125, D205-D209).

Synthetic values only. The reply is what Research's Perplexity round must return: a selection from the day's
candidates with sourced reasons, news findings that each cite one stored source, and its next check-in. It holds no
confidence, approval, order or wake field: web content is data, never authority.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from trade_graph.adapters.models.providers import _wire_schema
from trade_graph.contracts.focus_research import FocusFinding, FocusPick, FocusResearchReply
from trade_graph.contracts.perplexity_switch import PERPLEXITY_DEPARTMENTS, PerplexityDepartments
from trade_graph.contracts.wakes import FocusSelection
from trade_graph.contracts.web_research import PerplexitySpendPolicy, WebResearchReply
from trade_graph.kernel.authority import LEADER_FORBIDDEN, path_is_protected

TESTING = PerplexitySpendPolicy(revision_id="spend-1", mode="unlimited_testing", decision_ref="D125")


def _reply(**changes) -> FocusResearchReply:
    document = {
        "summary": "SOL has fresh news and the best swing for its cost; BTC is quiet.",
        "findings": [{"question": "Any Solana news today?", "claim": "A synthetic outlet reports a SOL upgrade.",
                      "source_ref": "web:abc:1", "instruments": ["SOL/USD"],
                      "counterevidence": "One source only.", "invalidation": "The upgrade is postponed."}],
        "focus": [{"symbol": "SOL/USD", "reason": "Synthetic: liquid, rising, an upgrade this week.",
                   "evidence_refs": ["web:abc:1", "scan-1"]}],
        "next_check_in": {"at_utc": "2026-10-16T14:30:00Z", "note": "after the US open"},
    }
    document.update(changes)
    return FocusResearchReply.model_validate(document)


def test_the_reply_is_a_web_research_reply_with_a_selection_and_a_next_check_in() -> None:
    reply = _reply()
    assert isinstance(reply, WebResearchReply)  # the Perplexity round accepts it as its reply type
    assert reply.keep_current_focus is False
    assert [pick.symbol for pick in reply.focus] == ["SOL/USD"]
    assert reply.next_check_in.at_utc == datetime(2026, 10, 16, 14, 30, tzinfo=UTC)
    selection = reply.selection()
    assert isinstance(selection, FocusSelection) and selection.symbols() == ("SOL/USD",)
    assert selection.coins[0].note == "Synthetic: liquid, rising, an upgrade this week."
    # Findings carry no lifetime chosen by the model: software sets it to the trading day's end (D207).
    assert "expires_after_seconds" not in FocusFinding.model_fields
    # Picking nothing is a valid answer, and so is keeping today's focus at a refresh.
    assert _reply(focus=[]).selection().symbols() == ()
    assert _reply(focus=[], keep_current_focus=True).selection() is None


def test_a_reply_cannot_carry_authority_or_break_its_own_rules() -> None:
    for extra in ({"confidence": "0.9"}, {"day_plans": []}, {"wake_requests": [{"department": "trader",
                                                                                 "note": "buy now"}]},
                  {"approve": True}):
        with pytest.raises(ValidationError):
            _reply(**extra)
    pick = {"symbol": "SOL/USD", "reason": "Synthetic.", "evidence_refs": ["scan-1"]}
    with pytest.raises(ValidationError):
        _reply(focus=[pick, pick])  # each coin once
    with pytest.raises(ValidationError):
        _reply(keep_current_focus=True)  # keeping the focus names no new coins
    with pytest.raises(ValidationError):
        FocusPick(symbol="SOL/USD", reason="Synthetic.", evidence_refs=[])  # a reason is sourced
    with pytest.raises(ValidationError):
        FocusPick(symbol="sol", reason="Synthetic.", evidence_refs=["scan-1"])
    with pytest.raises(ValidationError):
        FocusPick(symbol="SOL/USD", reason="Hidden‮text", evidence_refs=["scan-1"])
    with pytest.raises(ValidationError):
        FocusPick(symbol="SOL/USD", reason="Synthetic.", evidence_refs=["not a reference"])
    with pytest.raises(ValidationError):
        _reply(findings=[{"question": "q", "claim": "c", "source_ref": "web:abc:1", "instruments": [],
                          "counterevidence": "x", "invalidation": "y"}])  # a finding names what it is about
    with pytest.raises(ValidationError):
        _reply(next_check_in={"at_utc": "2026-10-16T14:30:00"})  # an aware time or none


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_the_reply_fits_strict_provider_reply_schemas(provider) -> None:
    reply = _reply()
    wire = _wire_schema(FocusResearchReply.model_json_schema(), provider=provider)
    Draft202012Validator.check_schema(wire)
    document = reply.model_dump(mode="json")
    Draft202012Validator(wire).validate(document)
    assert FocusResearchReply.model_validate(document) == reply


def test_the_switch_is_off_until_the_owner_records_it_and_on_needs_a_spend_rule() -> None:
    assert PERPLEXITY_DEPARTMENTS == ("research", "leader")
    off = PerplexityDepartments.off()
    assert (off.research, off.leader, off.spend_policy, off.revision_id) == (False, False, None, None)
    assert not off.enabled_for("research") and not off.enabled_for("leader")
    on = PerplexityDepartments(revision_id="switch-1", research=True, leader=False, spend_policy=TESTING)
    assert on.enabled_for("research") and not on.enabled_for("leader")
    with pytest.raises(ValidationError):
        PerplexityDepartments(revision_id="switch-2", research=True, leader=True, spend_policy=None)
    with pytest.raises(ValidationError):
        PerplexityDepartments(revision_id="switch-3", research=False, leader=False, spend_policy=TESTING,
                              set_by="leader")
    with pytest.raises(ValueError):
        on.enabled_for("trader")  # the Trader stays on its subscription (D124)


def test_the_switch_the_workflow_and_the_spend_rule_are_protected_paths() -> None:
    for path in ("src/trade_graph/contracts/focus_research.py", "src/trade_graph/contracts/perplexity_switch.py",
                 "src/trade_graph/application/focus_research.py", "src/trade_graph/application/focus_scope.py",
                 "src/trade_graph/application/perplexity_switch.py",
                 "src/trade_graph/market_data_cli/perplexity_departments.py",
                 "src/trade_graph/application/web_research.py", "src/trade_graph/contracts/web_research.py"):
        assert path_is_protected(path), path
    assert "switch_perplexity_departments" in LEADER_FORBIDDEN
