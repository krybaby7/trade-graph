"""T45 contracts: the owner's news wake-up switch in the owner policy and the note a woken department reads.

Synthetic values only. The note is software's fixed words around the headline as quoted data; it always fits the
wake contract's 500 characters and never carries control or text-direction characters.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from trade_graph.application.authority import paper_owner_policy
from trade_graph.contracts.models import OwnerPolicy
from trade_graph.contracts.news_wakes import (
    DEFAULT_MODE,
    NEWS_WAKE_MODES,
    NOTE_PREFIX,
    compose_note,
    route_source_ref,
)
from trade_graph.contracts.wakes import MAX_NOTE_LENGTH, WakeTrigger

HEADLINE_ID = "0123456789abcdef0123456789abcdef"


def _note(**changes) -> str:
    values = {"headline_id": HEADLINE_ID, "publisher": "Kraken blog",
              "title": "Kraken will delist SOL/USD on 30 October (synthetic)",
              "link": "https://blog.kraken.com/product/asset-listings/synthetic-sol-delisting",
              "subjects": ("SOL",), "event_type": "delisting", "holder_yes": "0.93"}
    values.update(changes)
    return compose_note(**values)


def test_the_switch_ships_in_shadow_mode() -> None:
    assert NEWS_WAKE_MODES == ("shadow", "live") and DEFAULT_MODE == "shadow"


def test_an_older_owner_policy_revision_serialises_exactly_as_before() -> None:
    policy = paper_owner_policy(revision_id="synthetic-1")
    document = policy.model_dump(mode="json")
    assert "news_wakes" not in document
    assert policy.news_wakes is None
    stored = policy.model_dump_json()
    assert OwnerPolicy.model_validate_json(stored).model_dump_json() == stored


def test_the_owner_policy_names_shadow_or_live_and_nothing_else() -> None:
    policy = paper_owner_policy(revision_id="synthetic-2").model_copy(update={"news_wakes": "live"})
    again = OwnerPolicy.model_validate_json(policy.model_dump_json())
    assert again.news_wakes == "live" and json.loads(policy.model_dump_json())["news_wakes"] == "live"
    document = paper_owner_policy(revision_id="synthetic-3").model_dump(mode="json")
    for value in ("on", "LIVE", True, "wake_everyone"):
        with pytest.raises(ValidationError):
            OwnerPolicy.model_validate({**document, "news_wakes": value})


def test_the_note_names_the_source_the_headline_and_jevs_answers() -> None:
    note = _note()
    assert note.startswith(f"{NOTE_PREFIX}: Kraken blog reported \"Kraken will delist SOL/USD on 30 October")
    assert "https://blog.kraken.com/product/asset-listings/synthetic-sol-delisting" in note
    assert "about SOL" in note and "delisting" in note and "yes 0.93" in note
    assert HEADLINE_ID in note
    assert "quoted data" in note
    assert len(note) <= MAX_NOTE_LENGTH
    trigger = WakeTrigger(trigger_id="t-1", kind="news_alarm", department="leader", portfolio_id="p-1",
                          source_ref="software:news-alarm:r-1", occurred_at="2026-10-16T12:00:00Z",
                          requested_by="software", note=note)
    assert trigger.note == note


def test_a_long_headline_is_cut_and_a_long_link_left_out_so_the_note_always_fits() -> None:
    note = _note(title="Delisting " * 200, link="https://example.org/" + "a" * 1900,
                 subjects=tuple(f"C{number}" for number in range(40)), publisher="P" * 80)
    assert len(note) <= MAX_NOTE_LENGTH
    assert note.startswith(f"{NOTE_PREFIX}: {'P' * 80} reported \"Delisting")
    assert "…" in note and "example.org" not in note
    assert HEADLINE_ID in note and "yes 0.93" in note and "and 32 more" in note


def test_control_and_text_direction_characters_never_reach_the_note() -> None:
    note = _note(title="Kraken‮ will\x00 delist⁦ SOL\n(synthetic)", publisher="Kraken\x07 blog")
    assert "‮" not in note and "\x00" not in note and "⁦" not in note and "\x07" not in note
    WakeTrigger(trigger_id="t-1", kind="news_alarm", department="trader", portfolio_id="p-1",
                source_ref="software:news-alarm:r-1", occurred_at="2026-10-16T12:00:00Z",
                requested_by="software", note=note)


def test_kraken_itself_and_ties_read_as_plain_subjects() -> None:
    assert "about Kraken itself" in _note(subjects=("kraken",))
    assert "about ETH and Kraken itself" in _note(subjects=("ETH", "kraken"))


def test_each_route_has_its_own_trigger_source() -> None:
    assert route_source_ref("abc") == "news-alarm:abc"
    with pytest.raises(ValueError):
        route_source_ref("not a reference")
