"""Overlap reconciliation and source precedence on constructed archive / Trades / collector candles."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.reconcile_candles import (
    COMPARED_FIELDS,
    DEFAULT_SOURCE_PRECEDENCE,
    precedence_window,
    reconcile_candles,
)
from trade_graph.contracts.candles import Candle, from_unix
from trade_graph.domain.errors import ValidationFailure

T0 = 1_791_892_800  # 2026-10-13T12:00:00Z
NOW = datetime(2026, 10, 14, 6, 0, tzinfo=UTC)
COLLECTOR, ARCHIVE, TRADES = "kraken_public_ohlc", "kraken_ohlcvt_archive", "kraken_public_trades"
RUN_KINDS = {COLLECTOR: ("public_collect", "synthetic_fixture"), ARCHIVE: ("archive_import", "local_archive_file"),
             TRADES: ("trades_backfill", "synthetic_fixture")}


@pytest.fixture
def store(tmp_path):
    database = MarketDataDatabase(tmp_path / "market" / "market_data.sqlite")
    yield CandleStore(database)
    database.close()


def make(source: str, slot: int, *, interval: int = 15, receipt: datetime = NOW, **overrides) -> Candle:
    values = dict(open="100", high="101", low="99", close="100.5", vwap=None if source == ARCHIVE else "100.2",
                  volume="5", trade_count=3)
    values.update(overrides)
    opened = T0 + slot * interval * 60
    return Candle(
        symbol="BTC/USD", source_pair="XBTUSD", interval_minutes=interval, open_time_utc=from_unix(opened),
        close_time_utc=from_unix(opened + interval * 60), available_at_utc=receipt, source=source,
        **{key: (Decimal(value) if isinstance(value, str) else value) for key, value in values.items()})


def put(store: CandleStore, source: str, candles: list[Candle]) -> None:
    kind, label = RUN_KINDS[source]
    run = store.begin_run(kind=kind, source=source, evidence_label=label, started_at=NOW, parameters={})
    store.save(candles, run_id=run)


def constructed(store: CandleStore) -> None:
    """Nine 15 minute slots (0 to 8). Archive: 0-5. Collector: 2-7. Trades-built: 3-7. Slot 8 is in no source."""
    put(store, ARCHIVE, [make(ARCHIVE, slot) for slot in range(6)])
    put(store, COLLECTOR, [make(COLLECTOR, 2), make(COLLECTOR, 3), make(COLLECTOR, 4, close="100.6"),
                           make(COLLECTOR, 5), make(COLLECTOR, 6), make(COLLECTOR, 7)])
    put(store, TRADES, [make(TRADES, 3), make(TRADES, 4), make(TRADES, 5, volume="5.1"),
                        make(TRADES, 6, trade_count=4), make(TRADES, 7)])


def end_of(slot_count: int = 9, interval: int = 15) -> datetime:
    return from_unix(T0 + slot_count * interval * 60)


def report(store: CandleStore, **kwargs):
    arguments = dict(sources=(COLLECTOR, ARCHIVE, TRADES), end_close_time=end_of(), count=9, mode="replay")
    arguments.update(kwargs)
    return reconcile_candles(store, "BTC/USD", 15, **arguments)


# --- reconciliation ------------------------------------------------------------------------------------------------


def test_the_default_precedence_is_collector_then_archive_then_trades() -> None:
    assert DEFAULT_SOURCE_PRECEDENCE == (COLLECTOR, ARCHIVE, TRADES)
    assert COMPARED_FIELDS == ("open", "high", "low", "close", "volume", "vwap", "trade_count")


def test_every_overlap_category_is_counted_on_constructed_data(store) -> None:
    constructed(store)
    result = report(store)
    counts = result.to_document()["counts"]
    # slot 0, 1: archive only; slot 2: archive + collector (same); slot 3: all three (same); slot 4: close differs;
    # slot 5: trades volume differs; slot 6: trade count differs (collector vs trades); slot 7: collector + trades same
    assert counts == {
        "slots": 9, "identical": 3, "differing": 3, "single_source": 2,
        "single_source_by_source": {COLLECTOR: 0, ARCHIVE: 2, TRADES: 0}, "absent_in_all": 1,
    }
    assert list(result.slot_statuses) == [
        "single_source", "single_source", "identical", "identical", "differing", "differing", "differing",
        "identical", "absent_in_all"]


def test_differences_name_the_fields_the_decimal_differences_and_the_relative_size(store) -> None:
    constructed(store)
    document = report(store).to_document()
    close_pair = document["pairwise"][f"{COLLECTOR}|{ARCHIVE}"]
    assert close_pair["both_present"] == 4 and close_pair["identical"] == 3 and close_pair["differing"] == 1
    close = close_pair["fields"]["close"]
    assert close["compared"] == 4 and close["differing"] == 1
    assert close["max_absolute"] == "0.1" and close["max_relative"] == "0.000994035785"
    assert close_pair["fields"]["open"]["differing"] == 0
    volume = document["pairwise"][f"{ARCHIVE}|{TRADES}"]["fields"]["volume"]
    assert volume["differing"] == 1 and volume["max_absolute"] == "0.1" and volume["max_relative"] == "0.019607843137"
    count = document["pairwise"][f"{COLLECTOR}|{TRADES}"]["fields"]["trade_count"]
    assert count["differing"] == 1 and count["max_absolute"] == "1" and count["max_relative"] == "0.25"


def test_a_missing_vwap_is_not_comparable_and_is_not_a_difference(store) -> None:
    constructed(store)
    pairwise = report(store).to_document()["pairwise"]
    vwap = pairwise[f"{COLLECTOR}|{ARCHIVE}"]["fields"]["vwap"]
    assert vwap["compared"] == 0 and vwap["differing"] == 0 and vwap["not_comparable"] == 4
    assert pairwise[f"{COLLECTOR}|{TRADES}"]["fields"]["vwap"]["compared"] == 5


def test_pairwise_presence_counts(store) -> None:
    constructed(store)
    pairwise = report(store).to_document()["pairwise"]
    assert pairwise[f"{COLLECTOR}|{ARCHIVE}"] == {
        "both_present": 4, "identical": 3, "differing": 1, "only_first": 2, "only_second": 2, "absent_in_both": 1,
        "fields": pairwise[f"{COLLECTOR}|{ARCHIVE}"]["fields"]}
    trades_pair = pairwise[f"{ARCHIVE}|{TRADES}"]
    assert (trades_pair["both_present"], trades_pair["only_first"], trades_pair["only_second"]) == (3, 3, 2)


def test_the_worst_slots_are_ranked_by_relative_size_then_time(store) -> None:
    constructed(store)
    result = report(store, worst=2)
    worst = result.to_document()["worst_slots"]
    assert [entry["open_time_utc"] for entry in worst] == ["2026-10-13T13:30:00.000000Z", "2026-10-13T13:15:00.000000Z"]
    assert [entry["relative_size"] for entry in worst] == ["0.25", "0.019607843137"]
    top = worst[0]["differences"][0]
    assert (top["field"], top["left_source"], top["left"], top["right_source"], top["right"]) == (
        "trade_count", COLLECTOR, "3", TRADES, "4")
    assert (top["absolute"], top["relative"]) == ("1", "0.25")
    assert worst[0]["sources_present"] == [COLLECTOR, TRADES]
    assert len(worst) == 2 and len(report(store, worst=10).to_document()["worst_slots"]) == 3


def test_equal_relative_sizes_are_listed_earliest_first(store) -> None:
    put(store, ARCHIVE, [make(ARCHIVE, n) for n in range(5)])
    put(store, COLLECTOR, [make(COLLECTOR, 0), make(COLLECTOR, 1, close="100.6"), make(COLLECTOR, 2),
                           make(COLLECTOR, 3, close="100.6"), make(COLLECTOR, 4, close="100.7")])
    worst = report(store, sources=(COLLECTOR, ARCHIVE), end_close_time=end_of(5), count=5).to_document()["worst_slots"]
    # slot 4 differs most; slots 1 and 3 differ by the same relative amount, so the earlier one comes first
    assert [entry["open_time_utc"] for entry in worst] == [
        "2026-10-13T13:00:00.000000Z", "2026-10-13T12:15:00.000000Z", "2026-10-13T12:45:00.000000Z"]


def test_identical_data_reports_no_differences(store) -> None:
    put(store, ARCHIVE, [make(ARCHIVE, n) for n in range(3)])
    put(store, COLLECTOR, [make(COLLECTOR, n) for n in range(3)])
    result = report(store, sources=(COLLECTOR, ARCHIVE), end_close_time=end_of(3), count=3)
    document = result.to_document()
    assert document["counts"]["identical"] == 3 and document["counts"]["differing"] == 0
    assert document["worst_slots"] == []


def test_the_report_is_plain_json_with_decimals_as_text(store) -> None:
    constructed(store)
    document = report(store).to_document()
    assert json.loads(json.dumps(document, sort_keys=True)) == document
    assert document["symbol"] == "BTC/USD" and document["interval_minutes"] == 15
    assert document["availability_basis"] == "event_time_only" and document["mode"] == "replay"
    assert document["sources"] == [COLLECTOR, ARCHIVE, TRADES]
    assert document["window"]["slots"] == 9 and document["window"]["first_open_utc"] == "2026-10-13T12:00:00.000000Z"
    assert any("not merged" in note or "never merged" in note for note in document["notes"])


def test_a_source_with_no_series_is_named(store) -> None:
    put(store, ARCHIVE, [make(ARCHIVE, n) for n in range(3)])
    document = report(store, sources=(COLLECTOR, ARCHIVE), end_close_time=end_of(3), count=3).to_document()
    assert document["series_present"] == {COLLECTOR: False, ARCHIVE: True}
    assert document["counts"]["single_source"] == 3 and document["counts"]["single_source_by_source"][ARCHIVE] == 3


def test_point_in_time_mode_hides_candles_received_after_as_of(store) -> None:
    early = NOW - timedelta(hours=10)
    put(store, ARCHIVE, [make(ARCHIVE, n, receipt=early) for n in range(3)])
    put(store, COLLECTOR, [make(COLLECTOR, n, receipt=NOW) for n in range(3)])
    visible = report(store, sources=(COLLECTOR, ARCHIVE), end_close_time=end_of(3), count=3,
                     mode="point_in_time", as_of=NOW - timedelta(hours=1))
    assert visible.to_document()["counts"]["single_source"] == 3
    assert visible.to_document()["availability_basis"] == "receipt_time"
    later = report(store, sources=(COLLECTOR, ARCHIVE), end_close_time=end_of(3), count=3, mode="point_in_time",
                   as_of=NOW + timedelta(hours=1))
    assert later.to_document()["counts"]["identical"] == 3


def test_a_long_window_is_read_in_chunks_with_the_same_result(store) -> None:
    constructed(store)
    whole = report(store).to_document()
    chunked = report(store, chunk_slots=2).to_document()
    assert chunked == whole


def test_a_changed_candle_shows_the_newest_revision_in_replay_mode(store) -> None:
    put(store, ARCHIVE, [make(ARCHIVE, 0)])
    put(store, COLLECTOR, [make(COLLECTOR, 0)])
    assert report(store, sources=(COLLECTOR, ARCHIVE), end_close_time=end_of(1), count=1).to_document()[
        "counts"]["identical"] == 1
    put(store, COLLECTOR, [make(COLLECTOR, 0, close="100.7", receipt=NOW + timedelta(hours=1))])
    after = report(store, sources=(COLLECTOR, ARCHIVE), end_close_time=end_of(1), count=1).to_document()
    assert after["counts"]["differing"] == 1


@pytest.mark.parametrize("kwargs", [
    {"sources": (COLLECTOR,)}, {"sources": ()}, {"sources": (COLLECTOR, COLLECTOR)}, {"sources": (COLLECTOR, "other")},
    {"count": 0}, {"count": 3_000_000}, {"worst": 0}, {"worst": 10_000}, {"chunk_slots": 0},
    {"mode": "live"}, {"mode": "point_in_time", "as_of": None},
])
def test_bad_requests_are_refused(store, kwargs) -> None:
    with pytest.raises(ValidationFailure):
        report(store, **kwargs)


def test_reconciling_changes_nothing_in_the_store(store) -> None:
    constructed(store)
    before = [(s.symbol, s.source, s.interval_minutes, s.candle_count) for s in store.series()]
    runs_before = len(store.runs())
    report(store)
    assert [(s.symbol, s.source, s.interval_minutes, s.candle_count) for s in store.series()] == before
    assert len(store.runs()) == runs_before


# --- precedence ----------------------------------------------------------------------------------------------------


def test_each_slot_takes_the_first_source_that_has_it_and_says_which(store) -> None:
    constructed(store)
    window = precedence_window(store, "BTC/USD", 15, end_close_time=end_of(), count=9, mode="replay")
    assert window.sources == DEFAULT_SOURCE_PRECEDENCE
    chosen = [slot.source for slot in window.slots]
    assert chosen == [ARCHIVE, ARCHIVE, COLLECTOR, COLLECTOR, COLLECTOR, COLLECTOR, COLLECTOR, COLLECTOR, None]
    assert window.source_counts == {COLLECTOR: 6, ARCHIVE: 2, TRADES: 0}
    assert window.missing_close_times == (from_unix(T0 + 9 * 900),) and not window.complete
    assert window.availability_basis == "event_time_only"


def test_the_chosen_candle_is_the_stored_candle_unchanged_and_never_a_blend(store) -> None:
    constructed(store)
    window = precedence_window(store, "BTC/USD", 15, end_close_time=end_of(), count=9, mode="replay")
    slot4 = window.slots[4]
    assert slot4.stored.candle.close == Decimal("100.6") and slot4.stored.candle.source == COLLECTOR
    slot0 = window.slots[0]
    assert slot0.stored.candle.vwap is None and slot0.stored.candle.source == ARCHIVE  # nothing borrowed from others
    for slot in window.slots:
        if slot.stored is not None:
            assert slot.stored.candle.source == slot.source
            assert slot.stored == store.revisions("BTC/USD", 15, slot.source, slot.stored.candle.open_time_utc)[-1]


def test_a_disagreement_with_a_lower_source_is_flagged_not_merged(store) -> None:
    constructed(store)
    window = precedence_window(store, "BTC/USD", 15, end_close_time=end_of(), count=9, mode="replay")
    by_open = {slot.close_time_utc - timedelta(minutes=15): slot for slot in window.slots}
    slot4 = by_open[from_unix(T0 + 4 * 900)]
    assert slot4.source == COLLECTOR and slot4.disagreeing_sources == (ARCHIVE, TRADES)
    assert slot4.disagreement_fields == {ARCHIVE: ("close",), TRADES: ("close",)}
    slot5, slot6 = by_open[from_unix(T0 + 5 * 900)], by_open[from_unix(T0 + 6 * 900)]
    assert slot5.disagreeing_sources == (TRADES,) and slot5.disagreement_fields == {TRADES: ("volume",)}
    assert slot6.disagreeing_sources == (TRADES,) and slot6.disagreement_fields == {TRADES: ("trade_count",)}
    agreeing = by_open[from_unix(T0 + 3 * 900)]
    assert agreeing.sources_present == (COLLECTOR, ARCHIVE, TRADES) and agreeing.disagreeing_sources == ()
    assert window.disagreement_count == 3


def test_a_missing_vwap_is_not_a_disagreement(store) -> None:
    constructed(store)
    window = precedence_window(store, "BTC/USD", 15, end_close_time=end_of(), count=9, mode="replay")
    slot2 = window.slots[2]  # collector has a vwap, the archive has none
    assert slot2.sources_present == (COLLECTOR, ARCHIVE) and slot2.disagreeing_sources == ()


def test_the_order_is_a_choice_and_changing_it_changes_the_label_and_the_candle(store) -> None:
    constructed(store)
    window = precedence_window(store, "BTC/USD", 15, (ARCHIVE, COLLECTOR, TRADES), end_close_time=end_of(),
                               count=9, mode="replay")
    assert window.slots[4].source == ARCHIVE and window.slots[4].stored.candle.close == Decimal("100.5")
    assert window.slots[4].disagreeing_sources == (COLLECTOR,)
    assert window.slots[7].source == COLLECTOR and window.slots[6].source == COLLECTOR


def test_point_in_time_precedence_ignores_candles_not_yet_received(store) -> None:
    early = NOW - timedelta(hours=10)
    put(store, ARCHIVE, [make(ARCHIVE, n, receipt=early) for n in range(3)])
    put(store, COLLECTOR, [make(COLLECTOR, n, receipt=NOW, close="100.9") for n in range(3)])
    window = precedence_window(store, "BTC/USD", 15, end_close_time=end_of(3), count=3, as_of=NOW - timedelta(hours=1))
    assert [slot.source for slot in window.slots] == [ARCHIVE] * 3 and window.availability_basis == "receipt_time"
    later = precedence_window(store, "BTC/USD", 15, end_close_time=end_of(3), count=3, as_of=NOW + timedelta(hours=1))
    assert [slot.source for slot in later.slots] == [COLLECTOR] * 3 and later.disagreement_count == 3


def test_precedence_documents_are_json(store) -> None:
    constructed(store)
    document = precedence_window(store, "BTC/USD", 15, end_close_time=end_of(), count=9, mode="replay").to_document()
    assert json.loads(json.dumps(document)) == document
    assert document["source_counts"] == {COLLECTOR: 6, ARCHIVE: 2, TRADES: 0} and document["missing_slots"] == 1
    assert document["sources"] == [COLLECTOR, ARCHIVE, TRADES] and document["disagreement_count"] == 3
    assert document["slots"][4]["source"] == COLLECTOR
    assert document["slots"][4]["disagreeing_sources"] == [ARCHIVE, TRADES]
    assert document["slots"][8]["source"] is None


@pytest.mark.parametrize("sources", [(), (COLLECTOR, COLLECTOR), ("nope",), "kraken_public_ohlc"])
def test_bad_precedence_orders_are_refused(store, sources) -> None:
    with pytest.raises(ValidationFailure):
        precedence_window(store, "BTC/USD", 15, sources, end_close_time=end_of(), count=2, mode="replay")


def test_one_source_is_a_valid_precedence_order(store) -> None:
    put(store, ARCHIVE, [make(ARCHIVE, n) for n in range(2)])
    window = precedence_window(store, "BTC/USD", 15, (ARCHIVE,), end_close_time=end_of(2), count=2, mode="replay")
    assert window.complete and window.disagreement_count == 0
