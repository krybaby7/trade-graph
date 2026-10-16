"""T28 dated cost profiles: data records with sources and dates, append-only revisions (D126, D145-D149)."""

from __future__ import annotations

import copy
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trade_graph.contracts.candles import Candle
from trade_graph.contracts.fills import CostProfile, ProtectiveExit
from trade_graph.domain.errors import ValidationFailure
from trade_graph.kernel.cost_profiles import (
    KRAKEN_AFTER_10K,
    KRAKEN_STARTING,
    OKX_EEA,
    PUBLISHED_PINS,
    CostProfileBook,
    load_cost_profiles,
    packaged_document,
)
from trade_graph.kernel.fills import BAR_REPLAY_POLICY, trigger_exit_on_candle

START = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def test_the_three_packaged_profiles_match_the_build_plan_rates() -> None:
    book = load_cost_profiles()
    expected = {
        KRAKEN_STARTING: ("kraken", Decimal("0.004"), Decimal("0.008")),
        KRAKEN_AFTER_10K: ("kraken", Decimal("0.0022"), Decimal("0.0038")),
        OKX_EEA: ("okx", Decimal("0.001"), Decimal("0.002")),
    }
    assert set(book.ids()) == set(expected)
    for profile_id, (venue, maker, taker) in expected.items():
        profile = book.get(profile_id)
        assert (profile.venue, profile.market, profile.maker_rate, profile.taker_rate) == (venue, "spot", maker, taker)
        assert profile.fee_asset == "quote" and profile.basis == "published_schedule"


def test_round_trip_costs_equal_the_d126_table() -> None:
    book = load_cost_profiles()
    table = {
        KRAKEN_STARTING: (Decimal("0.008"), Decimal("0.016")),
        KRAKEN_AFTER_10K: (Decimal("0.0044"), Decimal("0.0076")),
        OKX_EEA: (Decimal("0.002"), Decimal("0.004")),
    }
    for profile_id, (limit, market) in table.items():
        profile = book.get(profile_id)
        assert (profile.round_trip_rate("maker"), profile.round_trip_rate("taker")) == (limit, market)


def test_every_published_profile_carries_official_sources_and_dates() -> None:
    for profile in load_cost_profiles().profiles:
        assert profile.sources, profile.ref
        assert profile.recorded_on == date(2026, 10, 16)
        assert profile.effective_from <= profile.recorded_on
        assert any(decision in profile.decisions for decision in ("D126", "D14"))
        for source in profile.sources:
            assert source.url.startswith("https://")
            assert source.figure_date is not None and source.figure_date <= profile.recorded_on
            assert source.checked_on == date(2026, 10, 16)
            # The cloud build could not reach the venue pages; the record says so instead of claiming a read.
            assert source.check_result in {"read_official_page", "unreachable_from_build_environment"}
            assert source.note


def test_kraken_and_okx_sources_name_the_official_pages() -> None:
    book = load_cost_profiles()
    assert book.get(KRAKEN_STARTING).sources[0].url == "https://www.kraken.com/features/fee-schedule"
    assert book.get(KRAKEN_AFTER_10K).sources[0].url == "https://www.kraken.com/features/fee-schedule"
    assert book.get(OKX_EEA).sources[0].url.startswith("https://www.okx.com/")
    assert book.get(OKX_EEA).sources[0].figure_date == date(2026, 9, 25)


def test_published_revisions_are_pinned_by_content_hash() -> None:
    book = load_cost_profiles()
    assert set(PUBLISHED_PINS) == {profile.ref for profile in book.profiles}
    for profile in book.profiles:
        assert PUBLISHED_PINS[profile.ref] == profile.content_sha256
        assert len(profile.content_sha256) == 64


def test_an_edited_published_revision_is_refused() -> None:
    document = packaged_document()
    edited = copy.deepcopy(document)
    edited["profiles"][0]["taker_rate"] = "0.0079"
    with pytest.raises(ValidationFailure, match="pinned"):
        CostProfileBook.from_document(edited)
    renamed = copy.deepcopy(document)
    renamed["profiles"][0]["sources"][0]["note"] = "quietly rewritten"
    with pytest.raises(ValidationFailure, match="pinned"):
        CostProfileBook.from_document(renamed)


def test_an_unpinned_or_removed_revision_is_refused() -> None:
    document = packaged_document()
    added = copy.deepcopy(document)
    newer = copy.deepcopy(added["profiles"][0])
    newer.update(revision=2, effective_from="2026-10-20", taker_rate="0.0075")
    added["profiles"].append(newer)
    with pytest.raises(ValidationFailure, match="not pinned"):
        CostProfileBook.from_document(added)
    removed = copy.deepcopy(document)
    removed["profiles"].pop()
    with pytest.raises(ValidationFailure, match="missing"):
        CostProfileBook.from_document(removed)


def _with_second_revision() -> tuple[CostProfileBook, dict]:
    document = packaged_document()
    newer = copy.deepcopy(document["profiles"][0])
    newer.update(revision=2, effective_from="2026-10-20", recorded_on="2026-10-20", taker_rate="0.0075")
    document["profiles"].append(newer)
    pins = dict(PUBLISHED_PINS)
    pins[f"{newer['profile_id']}@2"] = CostProfile.model_validate(newer).content_sha256
    return CostProfileBook.from_document(document, pins=pins), document


def test_a_new_revision_is_appended_and_earlier_revisions_still_resolve() -> None:
    book, _ = _with_second_revision()
    assert book.get(KRAKEN_STARTING).revision == 2
    assert book.get(KRAKEN_STARTING, revision=1) == load_cost_profiles().get(KRAKEN_STARTING)
    assert book.resolve("kraken-spot-starting-tier@1").taker_rate == Decimal("0.008")
    assert book.effective(KRAKEN_STARTING, date(2026, 10, 19)).revision == 1
    assert book.effective(KRAKEN_STARTING, date(2026, 10, 20)).revision == 2
    with pytest.raises(ValidationFailure):
        book.resolve("kraken-spot-starting-tier@3")


def test_revisions_must_be_contiguous_and_move_forward_in_time() -> None:
    document = packaged_document()
    newer = copy.deepcopy(document["profiles"][0])
    newer.update(revision=3, effective_from="2026-10-20")
    document["profiles"].append(newer)
    pins = dict(PUBLISHED_PINS) | {f"{newer['profile_id']}@3": CostProfile.model_validate(newer).content_sha256}
    with pytest.raises(ValidationFailure, match="contiguous"):
        CostProfileBook.from_document(document, pins=pins)
    document["profiles"][-1].update(revision=2, effective_from="2026-09-01")
    pins = dict(PUBLISHED_PINS) | {
        f"{newer['profile_id']}@2": CostProfile.model_validate(document["profiles"][-1]).content_sha256}
    with pytest.raises(ValidationFailure, match="later"):
        CostProfileBook.from_document(document, pins=pins)


def test_changing_a_profile_never_rewrites_an_earlier_result() -> None:
    """A fill keeps the fee, reference and hash it was computed with; a rerun under that revision reproduces it."""
    original = load_cost_profiles().get(KRAKEN_STARTING)
    exit_ = ProtectiveExit(exit_id="x", symbol="BTC/USD", quantity=Decimal("1"), stop_price=Decimal("95"),
                           eligible_after_utc=START)
    bar = Candle(symbol="BTC/USD", source_pair="XBTUSD", interval_minutes=15, open_time_utc=START,
                 close_time_utc=START + timedelta(minutes=15), available_at_utc=START + timedelta(minutes=15),
                 open=Decimal("100"), high=Decimal("101"), low=Decimal("94"), close=Decimal("96"),
                 volume=Decimal("5"), trade_count=2, source="synthetic")
    before = trigger_exit_on_candle(exit_, bar, armed_in_this_candle=False, profile=original,
                                    policy=BAR_REPLAY_POLICY)
    stored = before.model_dump_json()
    book, _ = _with_second_revision()
    after_change = trigger_exit_on_candle(exit_, bar, armed_in_this_candle=False, profile=book.get(KRAKEN_STARTING),
                                          policy=BAR_REPLAY_POLICY)
    rerun = trigger_exit_on_candle(exit_, bar, armed_in_this_candle=False,
                                   profile=book.resolve(before.cost_profile_ref), policy=BAR_REPLAY_POLICY)
    assert after_change.fee != before.fee and after_change.cost_profile_ref == "kraken-spot-starting-tier@2"
    assert rerun.model_dump_json() == stored
    assert json.loads(stored)["fee"] == "0.75924"  # 94.905 * 1 * 0.008


def test_profile_contract_refuses_floats_rebates_and_unsourced_published_profiles() -> None:
    record = packaged_document()["profiles"][0]
    with pytest.raises(ValidationError):
        CostProfile.model_validate(record | {"maker_rate": 0.004})
    with pytest.raises(ValidationError):
        CostProfile.model_validate(record | {"maker_rate": "-0.0001"})
    with pytest.raises(ValidationError):
        CostProfile.model_validate(record | {"taker_rate": "0.5"})
    with pytest.raises(ValidationError):
        CostProfile.model_validate(record | {"sources": []})
    with pytest.raises(ValidationError):
        CostProfile.model_validate(record | {"sources": [record["sources"][0] | {"url": "http://example.com"}]})
    explicit = CostProfile.explicit(maker_rate=Decimal("0.001"), taker_rate=Decimal("0.003"), venue="paper")
    assert explicit.basis == "explicit_configuration" and explicit.sources == ()
    assert explicit.ref.startswith("explicit-paper-")
    with pytest.raises(ValidationError):
        CostProfile.model_validate(record | {"basis": "explicit_configuration"})  # explicit rates cite no source


def test_the_fill_engine_document_lists_every_published_profile_with_its_rates() -> None:
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "docs" / "FILL-ENGINE.md").read_text(encoding="utf-8")
    for profile in load_cost_profiles().profiles:
        maker, taker = (f"{rate * 100:.2f}%" for rate in (profile.maker_rate, profile.taker_rate))
        assert f"| `{profile.ref}` | {maker} / {taker} |" in text, profile.ref
