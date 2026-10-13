"""Kraken pair catalog from the recorded public fixture: parsing, classification and the catalog run record.

Everything here reads tests/fixtures/kraken/asset_pairs_2026-10-13.json or a scripted transport. A recorded
fixture is evidence of the response format, not a live-network result.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.network_guard import block_network

from trade_graph.adapters.market.kraken_catalog import (
    ASSET_PAIRS_URL,
    CATALOG_MAXIMUM_RESPONSE_BYTES,
    CatalogFetchError,
    KrakenPairCatalog,
    make_catalog_transport,
    parse_asset_pairs,
)
from trade_graph.adapters.market.public import HttpxTextTransport
from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.collect_candles import refresh_catalog, stored_catalog
from trade_graph.application.pair_classification import CLASSIFICATION_RULES_VERSION
from trade_graph.contracts.pair_catalog import PairRecord
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import ValidationFailure

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "kraken" / "asset_pairs_2026-10-13.json"
RAW = FIXTURE.read_text(encoding="utf-8")
NOW = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


class ScriptedTransport:
    def __init__(self, responses: list, clock: FrozenClock, *, delay_seconds: int = 2, before=None) -> None:
        self.responses = list(responses)
        self.clock = clock
        self.delay_seconds = delay_seconds
        self.before = before
        self.calls: list[str] = []

    def get_text(self, url: str) -> str:
        self.calls.append(url)
        if self.before is not None:
            self.before()
        self.clock.advance(self.delay_seconds)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _document() -> dict:
    return json.loads(RAW)


def _wire(document: dict) -> str:
    return json.dumps(document)


@pytest.fixture
def store(tmp_path):
    database = MarketDataDatabase(tmp_path / "market" / "market_data.sqlite")
    yield CandleStore(database)
    database.close()


def _pair(parsed, symbol: str):
    return next(pair for pair in parsed.pairs if pair.symbol == symbol)


# --- the recorded fixture -------------------------------------------------------------------------------------


def test_the_fixture_is_a_small_recorded_excerpt_with_the_required_cases() -> None:
    result = _document()["result"]
    assert 40 <= len(result) <= 60 and len(RAW.encode()) < 100_000
    assert {value["status"] for value in result.values()} >= {"online", "cancel_only", "post_only"}
    assert {"XXBTZUSD", "XDGUSD", "XETHZUSD", "SOLUSD", "USDTZUSD", "USDCUSD", "LSETHUSD", "MSOLUSD"} <= set(result)
    assert any(value["quote"] != "ZUSD" for value in result.values())
    assert (FIXTURE.parent / "README.md").read_text(encoding="utf-8").startswith("# Kraken recorded public fixtures")


def test_every_fixture_pair_parses_and_nothing_is_rejected() -> None:
    parsed = parse_asset_pairs(RAW)
    assert parsed.pairs_in_response == 53 and len(parsed.pairs) == 53
    assert parsed.rejected == () and parsed.out_of_scope == 0 and not parsed.partial
    symbols = [pair.symbol for pair in parsed.pairs]
    assert symbols == sorted(symbols) and len(set(symbols)) == len(symbols)


def test_xbt_becomes_btc_and_xdg_becomes_doge_with_exact_numbers() -> None:
    parsed = parse_asset_pairs(RAW)
    btc = _pair(parsed, "BTC/USD")
    assert (btc.source_pair, btc.ws_name, btc.base, btc.quote, btc.status) == (
        "XBTUSD", "XBT/USD", "XXBT", "ZUSD", "online")
    assert (btc.ordermin, btc.costmin, btc.tick_size) == (Decimal("0.00005"), Decimal("0.5"), Decimal("0.1"))
    assert (btc.pair_decimals, btc.cost_decimals, btc.lot_decimals) == (1, 5, 8)
    doge = _pair(parsed, "DOGE/USD")
    assert (doge.source_pair, doge.ws_name, doge.base) == ("XDGUSD", "XDG/USD", "XXDG")
    assert doge.tick_size == Decimal("0.0000001") and doge.ordermin == Decimal("50")
    eth = _pair(parsed, "ETH/USD")
    assert (eth.source_pair, eth.base) == ("ETHUSD", "XETH")
    assert _pair(parsed, "ETH/BTC").source_pair == "ETHXBT"
    assert not any(pair.symbol.startswith(("XBT", "XDG")) for pair in parsed.pairs)
    assert _pair(parsed, "DOGE/EUR").quote == "ZEUR"


def test_legacy_prefixed_codes_are_cross_checked_but_modern_z_names_are_kept() -> None:
    parsed = parse_asset_pairs(RAW)
    assert _pair(parsed, "ZCHF/USD").base == "ZCHF"
    assert _pair(parsed, "EUR/USD").base == "ZEUR" and _pair(parsed, "AUD/USD").base == "ZAUD"
    assert _pair(parsed, "LINK/USD").base == "LINK"


def test_pairs_with_every_trading_status_are_parsed_and_kept() -> None:
    parsed = parse_asset_pairs(RAW)
    by_symbol = {pair.symbol: pair for pair in parsed.pairs}
    assert by_symbol["ACA/USD"].status == "cancel_only" and by_symbol["EURR/USD"].status == "post_only"
    assert by_symbol["BTC/USD"].status == "online"


def test_every_quote_currency_is_parsed_generically_and_usd_is_identified() -> None:
    parsed = parse_asset_pairs(RAW)
    quotes = {pair.quote for pair in parsed.pairs}
    assert {"ZUSD", "ZEUR", "ZGBP", "USDT", "USDC", "PYUSD", "XXBT"} <= quotes
    assert sum(pair.usd_quoted for pair in parsed.pairs) == 44
    assert _pair(parsed, "BTC/USD").usd_quoted
    assert not _pair(parsed, "ETH/PYUSD").usd_quoted and not _pair(parsed, "BTC/USDT").usd_quoted


def test_a_missing_or_null_tick_size_stays_unknown() -> None:
    document = _document()
    del document["result"]["XXBTZUSD"]["tick_size"]
    document["result"]["XETHZUSD"]["tick_size"] = None
    parsed = parse_asset_pairs(_wire(document))
    assert _pair(parsed, "BTC/USD").tick_size is None and _pair(parsed, "ETH/USD").tick_size is None
    assert not parsed.partial


# --- malformed pairs are reported, never skipped silently ------------------------------------------------------


def _mutated(key: str, **changes) -> str:
    document = _document()
    document["result"][key].update(changes)
    return _wire(document)


@pytest.mark.parametrize(("changes", "reason"), [
    ({"status": "maintenance"}, "invalid_status"),
    ({"status": None}, "invalid_status"),
    ({"ordermin": "abc"}, "invalid_ordermin"),
    ({"ordermin": "0"}, "invalid_ordermin"),
    ({"ordermin": "-1"}, "invalid_ordermin"),
    ({"ordermin": "1E+999"}, "invalid_ordermin"),
    ({"ordermin": True}, "invalid_ordermin"),
    ({"costmin": "NaN"}, "invalid_costmin"),
    ({"tick_size": "0"}, "invalid_tick_size"),
    ({"tick_size": "x"}, "invalid_tick_size"),
    ({"pair_decimals": 5.5}, "invalid_pair_decimals"),
    ({"cost_decimals": "5"}, "invalid_cost_decimals"),
    ({"lot_decimals": -1}, "invalid_lot_decimals"),
    ({"wsname": "xbt/usd"}, "invalid_wsname"),
    ({"wsname": "BTCUSD"}, "invalid_wsname"),
    ({"wsname": "ETH/USD"}, "asset_code_mismatch"),
    ({"quote": "ZEUR"}, "asset_code_mismatch"),
    ({"base": "XETH"}, "asset_code_mismatch"),
    ({"altname": "../XBTUSD"}, "invalid_altname"),
    ({"altname": "XBT USD"}, "invalid_altname"),
    ({"base": "x y"}, "invalid_base"),
])
def test_a_malformed_pair_is_rejected_with_a_reason_and_the_rest_still_parse(changes: dict, reason: str) -> None:
    parsed = parse_asset_pairs(_mutated("XXBTZUSD", **changes))
    assert [(item.pair_key, item.reason) for item in parsed.rejected] == [("XXBTZUSD", reason)]
    assert parsed.partial and len(parsed.pairs) == 52
    assert "BTC/USD" not in {pair.symbol for pair in parsed.pairs}


@pytest.mark.parametrize("field", [
    "altname", "wsname", "base", "quote", "status", "ordermin", "costmin", "pair_decimals", "cost_decimals",
    "lot_decimals", "aclass_base", "aclass_quote",
])
def test_a_missing_required_field_is_named_in_the_rejection(field: str) -> None:
    document = _document()
    del document["result"]["SOLUSD"][field]
    parsed = parse_asset_pairs(_wire(document))
    assert [(item.pair_key, item.reason) for item in parsed.rejected] == [("SOLUSD", f"missing_{field}")]
    assert parsed.partial


def test_an_entry_that_is_not_an_object_is_rejected() -> None:
    document = _document()
    document["result"]["BROKEN"] = "not-an-object"
    document["result"]["ALSO"] = [1, 2]
    parsed = parse_asset_pairs(_wire(document))
    assert sorted((item.pair_key, item.reason) for item in parsed.rejected) == [
        ("ALSO", "not_an_object"), ("BROKEN", "not_an_object")]


def test_an_unsafe_result_key_is_reported_without_echoing_it() -> None:
    document = _document()
    document["result"]["SECRET\nkey with spaces"] = 7
    parsed = parse_asset_pairs(_wire(document))
    assert [(item.pair_key, item.reason) for item in parsed.rejected] == [("unsafe_key", "not_an_object")]
    assert "SECRET" not in repr(parsed.rejected)


def test_pairs_that_claim_the_same_symbol_or_pair_code_are_all_rejected() -> None:
    document = _document()
    clone = copy.deepcopy(document["result"]["XXBTZUSD"])
    clone["altname"] = "XBTUSDX"
    document["result"]["COPYBTC"] = clone
    other = copy.deepcopy(document["result"]["SOLUSD"])
    other["wsname"], other["base"] = "SOLX/USD", "SOLX"
    document["result"]["COPYSOL"] = other
    parsed = parse_asset_pairs(_wire(document))
    assert sorted((item.pair_key, item.reason) for item in parsed.rejected) == [
        ("COPYBTC", "duplicate_symbol"), ("COPYSOL", "duplicate_source_pair"),
        ("SOLUSD", "duplicate_source_pair"), ("XXBTZUSD", "duplicate_symbol"),
    ]
    assert parsed.partial
    assert "BTC/USD" not in {pair.symbol for pair in parsed.pairs}


def test_a_pair_in_another_asset_class_is_counted_as_out_of_scope_not_rejected() -> None:
    document = _document()
    document["result"]["AAPLXUSD"] = {**document["result"]["SOLUSD"], "altname": "AAPLXUSD", "wsname": "AAPLX/USD",
                                      "base": "AAPLX", "aclass_base": "tokenized_asset"}
    parsed = parse_asset_pairs(_wire(document))
    assert parsed.out_of_scope == 1 and parsed.rejected == () and not parsed.partial
    assert parsed.pairs_in_response == 54 and len(parsed.pairs) == 53


def test_every_rejection_is_kept_in_the_parse_result_however_many_there_are() -> None:
    document = {"error": [], "result": {"SOLUSD": _document()["result"]["SOLUSD"]}}
    for index in range(300):
        document["result"][f"BAD{index}"] = {"altname": f"BAD{index}"}
    parsed = parse_asset_pairs(_wire(document))
    assert len(parsed.rejected) == 300 and parsed.partial
    assert len(parsed.pairs) == 1


# --- the whole response is refused when its shape cannot be trusted ----------------------------------------------


def test_a_non_empty_error_list_refuses_the_response_without_echoing_it() -> None:
    document = _document()
    document["error"] = ["EGeneral:SECRET-DETAIL"]
    with pytest.raises(ValidationFailure) as failure:
        parse_asset_pairs(_wire(document))
    assert "SECRET" not in str(failure.value)


@pytest.mark.parametrize("raw", [
    "",
    "not json",
    "[]",
    '{"result": {}}',
    '{"error": [], "result": {}}',
    '{"error": [], "result": []}',
    '{"error": [], "result": "x"}',
    '{"error": "none", "result": {"A": {}}}',
    '{"error": [], "result": {"A": NaN}}',
    '{"error": [], "result": {"A": Infinity}}',
    '{"error": [], "error": [], "result": {"A": {}}}',
    '{"error": [], "result": {"A": {"status": "online", "status": "cancel_only"}}}',
    '{"error": [], "result": {"A": {}, "A": {}}}',
    "[" * 200_000,
    '{"error": [], "result": {"A": {"altname": "A"}}}',
])
def test_an_unusable_response_is_refused(raw: str) -> None:
    with pytest.raises(ValidationFailure):
        parse_asset_pairs(raw)


def test_a_duplicate_field_inside_a_pair_is_refused_even_when_the_rest_is_fine() -> None:
    raw = RAW.replace('"altname":"XBTUSD"', '"altname":"XBTUSD","altname":"XBTUSD"', 1)
    assert raw != RAW
    with pytest.raises(ValidationFailure):
        parse_asset_pairs(raw)


def test_the_response_byte_limit_is_deliberate_and_enforced() -> None:
    # The recorded full response was 678,583 bytes; the limit leaves about twelve times that, below the
    # transport's own 16 MiB ceiling.
    assert 10 * 678_583 <= CATALOG_MAXIMUM_RESPONSE_BYTES <= 16_777_216
    with pytest.raises(ValidationFailure, match="byte limit"):
        parse_asset_pairs(" " * (CATALOG_MAXIMUM_RESPONSE_BYTES + 1) + RAW)
    parse_asset_pairs(RAW + " " * 1_000)


def test_a_multibyte_response_is_measured_in_bytes_not_characters() -> None:
    padding = "é" * (CATALOG_MAXIMUM_RESPONSE_BYTES // 2 + 1)
    assert len(padding) < CATALOG_MAXIMUM_RESPONSE_BYTES
    with pytest.raises(ValidationFailure, match="byte limit"):
        parse_asset_pairs(RAW + " " + padding)


def test_the_real_transport_is_bounded_by_the_catalog_limit() -> None:
    transport = make_catalog_transport()
    assert isinstance(transport, HttpxTextTransport)
    assert transport.maximum_response_bytes == CATALOG_MAXIMUM_RESPONSE_BYTES
    assert transport.timeout >= 10


# --- the fetch --------------------------------------------------------------------------------------------------


def test_one_plain_get_is_made_and_receipt_is_taken_after_the_response() -> None:
    clock = FrozenClock(NOW)
    transport = ScriptedTransport([RAW], clock, delay_seconds=3)
    fetched = KrakenPairCatalog(transport, clock).fetch()
    assert transport.calls == [ASSET_PAIRS_URL] == ["https://api.kraken.com/0/public/AssetPairs"]
    assert fetched.received_at == NOW + timedelta(seconds=3) and fetched.requested_at == NOW
    assert len(fetched.catalog.pairs) == 53


def test_a_transport_failure_is_a_request_failure_with_no_detail() -> None:
    clock = FrozenClock(NOW)
    transport = ScriptedTransport([RuntimeError("SECRET-BODY connection reset")], clock)
    with pytest.raises(CatalogFetchError) as failure:
        KrakenPairCatalog(transport, clock).fetch()
    assert failure.value.category == "request_failed" and "SECRET" not in str(failure.value)


def test_a_transport_that_refuses_the_response_is_still_a_request_failure() -> None:
    clock = FrozenClock(NOW)
    transport = ScriptedTransport([ValidationFailure("public response exceeded its byte limit")], clock)
    with pytest.raises(CatalogFetchError) as failure:
        KrakenPairCatalog(transport, clock).fetch()
    assert failure.value.category == "request_failed"


def test_a_response_that_cannot_be_trusted_is_a_response_failure() -> None:
    clock = FrozenClock(NOW)
    with pytest.raises(CatalogFetchError) as failure:
        KrakenPairCatalog(ScriptedTransport(["SECRET-BODY not json"], clock), clock).fetch()
    assert failure.value.category == "response_invalid" and "SECRET" not in str(failure.value)
    with pytest.raises(CatalogFetchError) as failure:
        KrakenPairCatalog(ScriptedTransport([None], clock), clock).fetch()
    assert failure.value.category == "response_invalid"


def test_a_clock_that_moves_backwards_during_the_request_is_refused() -> None:
    clock = FrozenClock(NOW)
    transport = ScriptedTransport([RAW], clock, delay_seconds=-5)
    with pytest.raises(CatalogFetchError) as failure:
        KrakenPairCatalog(transport, clock).fetch()
    assert failure.value.category == "response_invalid"


def test_a_naive_clock_is_refused_before_any_request() -> None:
    class NaiveClock:
        def now(self) -> datetime:
            return datetime(2026, 10, 13, 15, 0)

    transport = ScriptedTransport([RAW], FrozenClock(NOW))
    with pytest.raises(ValidationFailure):
        KrakenPairCatalog(transport, NaiveClock()).fetch()
    assert transport.calls == []


# --- refresh_catalog: run record, classification, deduplication ----------------------------------------------------


def _refresh(store: CandleStore, raw: str | Exception = RAW, *, at: datetime = NOW,
             label: str = "recorded_public_fixture", delay: int = 2, before=None) -> dict:
    clock = FrozenClock(at)
    transport = ScriptedTransport([raw], clock, delay_seconds=delay, before=before)
    return refresh_catalog(store, clock, transport, label)


def test_a_refresh_records_a_catalog_run_with_its_parameters_and_report(store: CandleStore) -> None:
    report = _refresh(store)
    runs = store.runs()
    assert len(runs) == 1
    run = runs[0]
    assert (run.kind, run.source, run.evidence_label, run.status) == (
        "catalog", "kraken_public_ohlc", "recorded_public_fixture", "ok")
    assert run.started_at_utc == NOW and run.finished_at_utc == NOW + timedelta(seconds=2)
    assert run.parameters["endpoint"] == ASSET_PAIRS_URL and run.parameters["quote"] == "USD"
    assert run.parameters["rules_version"] == CLASSIFICATION_RULES_VERSION
    assert run.parameters["maximum_response_bytes"] == CATALOG_MAXIMUM_RESPONSE_BYTES
    assert run.parameters["paid_calls_enabled"] is False and run.parameters["live_trading_enabled"] is False
    assert run.report == json.loads(json.dumps(report))


def test_the_report_counts_statuses_classes_and_what_was_not_reviewed(store: CandleStore) -> None:
    report = _refresh(store)
    assert report["status"] == "ok" and report["error"] is None
    assert report["request_count"] == 1
    assert report["paid_calls_enabled"] is False and report["live_trading_enabled"] is False
    assert report["rules_version"] == CLASSIFICATION_RULES_VERSION
    assert report["evidence_label"] == "recorded_public_fixture"
    assert report["observed_at_utc"] == "2026-10-13T15:00:02.000000Z"
    assert (report["pairs_in_response"], report["spot_currency_pairs"], report["usd_pairs"]) == (53, 53, 44)
    assert report["other_quote_pairs"] == 9 and report["out_of_scope_pairs"] == 0
    assert report["other_quotes"] == {"PYUSD": 1, "USDC": 1, "USDT": 1, "XXBT": 1, "ZEUR": 4, "ZGBP": 1}
    assert report["by_status"] == {"online": 39, "cancel_only": 3, "post_only": 2, "limit_only": 0, "reduce_only": 0}
    assert report["by_class"] == {
        "stablecoin": 18, "yield_token": 6, "stablecoin_and_yield_token": 1, "fiat_currency": 3,
        "reviewed_not_marked": 13, "not_individually_reviewed": 3,
    }
    assert report["excluded_pairs"] == 28 and report["eligible_for_collection"] == 13
    assert report["not_normal_status"] == 5
    assert report["unreviewed_assets"] == ["ACA", "KIN", "MAT"] and report["unreviewed_asset_count"] == 3
    assert report["reviewed_asset_count"] == 41
    assert report["rejected_count"] == 0 and report["rejected_pairs"] == []
    assert report["catalog_row_added"] is True
    assert report["price_peg_check"] == "not_applied"
    assert "not individually reviewed" in report["verification_note"]
    assert len(json.dumps(report)) < 50_000


def test_the_saved_catalog_holds_the_usd_pairs_with_their_marks(store: CandleStore) -> None:
    _refresh(store)
    observation = store.latest_catalog(as_of=NOW + timedelta(minutes=1))
    assert observation.rules_version == CLASSIFICATION_RULES_VERSION and len(observation.pairs) == 44
    assert all(pair["symbol"].endswith("/USD") for pair in observation.pairs)
    assert [pair["symbol"] for pair in observation.pairs] == sorted(pair["symbol"] for pair in observation.pairs)
    by_symbol = {pair["symbol"]: pair for pair in observation.pairs}
    assert by_symbol["USDT/USD"]["stablecoin"] is True and by_symbol["USDT/USD"]["exclusion_reasons"] == ["stablecoin"]
    assert by_symbol["MSOL/USD"]["yield_token"] is True
    assert by_symbol["BTC/USD"]["exclusion_reasons"] == [] and by_symbol["BTC/USD"]["tick_size"] == "0.1"
    assert by_symbol["ACA/USD"]["base_asset_review"] == "not_individually_reviewed"
    assert by_symbol["ACA/USD"]["status"] == "cancel_only"
    records = stored_catalog(store, as_of=NOW + timedelta(minutes=1))
    assert [type(record) for record in records] == [PairRecord] * 44
    assert [record.symbol for record in records if record.eligible_for_collection] == sorted(
        record.symbol for record in records if record.eligible_for_collection)
    assert len([record for record in records if record.eligible_for_collection]) == 13


def test_no_catalog_observed_yet_is_unknown_not_empty(store: CandleStore) -> None:
    assert stored_catalog(store, as_of=NOW) is None
    _refresh(store)
    assert stored_catalog(store, as_of=NOW - timedelta(days=1)) is None


def test_an_unchanged_list_adds_no_row_but_each_refresh_is_a_run(store: CandleStore) -> None:
    first = _refresh(store)
    second = _refresh(store, at=NOW + timedelta(hours=1))
    assert (first["catalog_row_added"], second["catalog_row_added"]) == (True, False)
    assert len(store.runs()) == 2 and all(run.status == "ok" for run in store.runs())
    assert store.database.query("SELECT COUNT(*) AS n FROM pair_catalog_observations")[0]["n"] == 1


def test_a_changed_status_adds_a_dated_row_and_keeps_the_earlier_one(store: CandleStore) -> None:
    _refresh(store)
    changed = _mutated("SOLUSD", status="cancel_only")
    report = _refresh(store, changed, at=NOW + timedelta(hours=2))
    assert report["catalog_row_added"] is True
    assert report["by_status"]["cancel_only"] == 4
    latest = store.latest_catalog(as_of=NOW + timedelta(hours=3))
    earlier = store.latest_catalog(as_of=NOW + timedelta(hours=1))
    assert {pair["symbol"]: pair["status"] for pair in latest.pairs}["SOL/USD"] == "cancel_only"
    assert {pair["symbol"]: pair["status"] for pair in earlier.pairs}["SOL/USD"] == "online"
    assert store.database.query("SELECT COUNT(*) AS n FROM pair_catalog_observations")[0]["n"] == 2


def test_the_order_of_pairs_in_the_response_does_not_change_the_saved_list(store: CandleStore) -> None:
    _refresh(store)
    document = _document()
    document["result"] = dict(reversed(list(document["result"].items())))
    report = _refresh(store, _wire(document), at=NOW + timedelta(hours=1))
    assert report["catalog_row_added"] is False


def test_a_malformed_pair_makes_the_run_partial_and_the_report_names_it(store: CandleStore) -> None:
    report = _refresh(store, _mutated("SOLUSD", ordermin="abc"))
    assert report["status"] == "partial" and report["error"] is None
    assert report["rejected_count"] == 1
    assert report["rejected_pairs"] == [{"pair_key": "SOLUSD", "reason": "invalid_ordermin"}]
    assert report["rejected_by_reason"] == {"invalid_ordermin": 1}
    assert report["usd_pairs"] == 43
    assert store.runs()[0].status == "partial"
    saved = store.latest_catalog(as_of=NOW + timedelta(minutes=1))
    assert "SOL/USD" not in {pair["symbol"] for pair in saved.pairs}


def test_a_huge_rejection_list_is_cut_in_the_report_but_counted_in_full(store: CandleStore) -> None:
    document = {"error": [], "result": {"SOLUSD": _document()["result"]["SOLUSD"]}}
    for index in range(300):
        document["result"][f"BAD{index}"] = {"altname": f"BAD{index}"}
    report = _refresh(store, _wire(document))
    assert report["status"] == "partial" and report["rejected_count"] == 300
    assert len(report["rejected_pairs"]) == 200 and report["rejected_truncated"] is True
    assert report["rejected_by_reason"] == {"missing_aclass_base": 300}


def test_a_failed_request_is_a_failed_run_with_a_fixed_category_and_no_body(store: CandleStore) -> None:
    report = _refresh(store, RuntimeError("SECRET-BODY reset by peer"))
    assert report["status"] == "failed" and report["error"] == "public_catalog_request_failed"
    assert report["request_count"] == 1 and report["catalog_row_added"] is False
    run = store.runs()[0]
    assert run.status == "failed" and run.report == json.loads(json.dumps(report))
    assert "SECRET" not in json.dumps(run.report) + json.dumps(run.parameters)
    assert store.latest_catalog(as_of=NOW + timedelta(days=1)) is None


@pytest.mark.parametrize("raw", [
    "SECRET-BODY not json",
    '{"error": ["SECRET-BODY"], "result": {}}',
    '{"error": [], "result": {}}',
    json.dumps({"error": [], "result": {"XXBTZEUR": json.loads(RAW)["result"]["XXBTZEUR"]}}),
])
def test_an_untrusted_or_useless_response_is_a_failed_run(store: CandleStore, raw: str) -> None:
    report = _refresh(store, raw)
    assert report["status"] == "failed" and report["error"] == "public_catalog_invalid"
    assert "SECRET" not in json.dumps(report)
    assert store.runs()[0].status == "failed" and store.latest_catalog(as_of=NOW + timedelta(days=1)) is None


def test_a_persistence_failure_is_a_failed_run_not_a_silent_success(store: CandleStore, monkeypatch) -> None:
    def refuse(*_args, **_kwargs):
        raise ValidationFailure("SECRET-DB-DETAIL")

    monkeypatch.setattr(store, "save_catalog", refuse)
    report = _refresh(store)
    assert report["status"] == "failed" and report["error"] == "catalog_persistence_failed"
    assert "SECRET" not in json.dumps(report) and store.runs()[0].status == "failed"


def test_the_run_is_recorded_before_the_request_is_made(store: CandleStore) -> None:
    seen: list = []
    _refresh(store, before=lambda: seen.extend(store.runs()))
    assert len(seen) == 1
    assert seen[0].kind == "catalog" and seen[0].status == "interrupted" and seen[0].report is None


def test_a_request_that_never_completes_leaves_a_visible_interrupted_run(store: CandleStore) -> None:
    class Crash(BaseException):
        pass

    def crash() -> None:
        raise Crash

    with pytest.raises(Crash):
        _refresh(store, before=crash)
    assert [run.status for run in store.runs()] == ["interrupted"]


def test_only_catalog_evidence_labels_are_accepted_and_nothing_is_recorded_otherwise(store: CandleStore) -> None:
    for label in ("local_archive_file", "invented", ""):
        with pytest.raises(ValidationFailure):
            _refresh(store, label=label)
    assert store.runs() == []


def test_a_synthetic_label_uses_the_synthetic_source_and_live_labels_use_kraken(store: CandleStore) -> None:
    _refresh(store, label="synthetic_fixture")
    _refresh(store, label="live_public_smoke", at=NOW + timedelta(hours=1))
    assert [(run.source, run.evidence_label) for run in store.runs()] == [
        ("synthetic", "synthetic_fixture"), ("kraken_public_ohlc", "live_public_smoke")]


def test_the_report_never_enables_paid_calls_or_live_trading(store: CandleStore) -> None:
    for raw in (RAW, RuntimeError("x"), "not json"):
        report = _refresh(store, raw, at=NOW + timedelta(hours=len(store.runs())))
        assert report["paid_calls_enabled"] is False and report["live_trading_enabled"] is False
