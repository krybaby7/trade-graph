"""The public OHLC fetcher against recorded Kraken responses and table-built ones. No network."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.candle_support import (
    FIXTURES,
    QueueTransport,
    error_wire,
    fixture_text,
    grid_rows,
    listed_pairs,
    manifest,
    recorded_clock,
    row,
    unix,
    wire,
)
from tests.network_guard import block_network

from trade_graph.adapters.market.kraken_candles import (
    OHLC_MAXIMUM_ROWS,
    KrakenCandleFetcher,
    OhlcFetchError,
    make_ohlc_transport,
    ohlc_url,
)
from trade_graph.adapters.market.public import HttpxTextTransport
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import ValidationFailure

NOW = datetime(2026, 10, 13, 15, 30, 20, tzinfo=UTC)
PAIRS = listed_pairs()
BTC = PAIRS["BTC/USD"]
BY_CODE = {pair.source_pair: pair for pair in PAIRS.values()}
RECORDED = sorted(name for name, entry in manifest().items() if entry["stored_as"])


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


def _fetch(responses: list, *, interval: int = 15, delay: float = 0, since: int | None = None, pair=BTC,
           clock: FrozenClock | None = None):
    clock = clock or FrozenClock(NOW)
    transport = QueueTransport(responses, clock, delay_seconds=delay)
    return KrakenCandleFetcher(transport, clock).fetch(pair, interval, since=since), transport, clock


def _table(interval: int = 15, committed: int = 4, *, key: str = "XXBTZUSD", **row_options) -> tuple[str, list]:
    """Committed candles ending at the last closed boundary before NOW, then the forming candle."""
    step = interval * 60
    forming = unix(NOW) - unix(NOW) % step
    rows = grid_rows(interval, forming - committed * step, committed + 1, **row_options)
    return wire(key, rows), rows


def _refused(raw, *, interval: int = 15, **options) -> OhlcFetchError:
    with pytest.raises(OhlcFetchError) as failure:
        _fetch([raw], interval=interval, **options)
    return failure.value


# --- the recorded responses -----------------------------------------------------------------------------------------


def test_the_manifest_lists_every_recorded_response_within_the_budget() -> None:
    entries = manifest()
    assert len(entries) <= 20
    assert {entry["interval_minutes"] for entry in entries.values()} == {1, 5, 15, 60}
    assert {entry["http_status"] for entry in entries.values()} == {200}
    intervals_by_pair: dict[str, set[int]] = {}
    for entry in entries.values():
        intervals_by_pair.setdefault(entry["pair"], set()).add(entry["interval_minutes"])
    assert intervals_by_pair["XBTUSD"] == {1, 5, 15, 60} and intervals_by_pair["SOLUSD"] == {1, 5, 15, 60}
    assert len(intervals_by_pair) >= 6
    for entry in entries.values():
        if entry["stored_as"]:
            assert (FIXTURES / entry["stored_as"]).is_file()


@pytest.mark.parametrize("name", RECORDED)
def test_a_recorded_response_gives_its_committed_rows_and_never_the_forming_one(name: str) -> None:
    entry = manifest()[name]
    raw = fixture_text(name)
    rows = next(value for key, value in json.loads(raw)["result"].items() if key != "last")
    clock = recorded_clock(name)
    transport = QueueTransport([raw], clock, delay_seconds=2)
    fetched = KrakenCandleFetcher(transport, clock).fetch(
        BY_CODE[entry["pair"]], entry["interval_minutes"], since=entry["since"])
    opens = [candle.open_unix for candle in fetched.candles]
    assert opens == [item[0] for item in rows[:-1]]
    assert rows[-1][0] not in opens
    assert fetched.rows_received == len(rows)
    assert fetched.cursor == entry["last"]
    assert all(candle.symbol == BY_CODE[entry["pair"]].symbol for candle in fetched.candles)
    assert all(candle.source == "kraken_public_ohlc" and candle.source_pair == entry["pair"]
               for candle in fetched.candles)
    assert all(candle.available_at_utc == fetched.received_at >= candle.close_time_utc for candle in fetched.candles)
    assert fetched.received_at == clock.now() == recorded_clock(name).now() + timedelta(seconds=2)
    if rows[:-1]:
        assert opens[-1] == entry["last"]  # Kraken's own cursor is the last committed candle


def test_exact_decimals_survive_from_the_recorded_bitcoin_response() -> None:
    fetched, _transport, _clock = _fetch_recorded("XBTUSD_15")
    last = fetched.candles[-1]
    assert (last.open, last.high, last.low, last.close) == (
        Decimal("83424.0"), Decimal("83594.2"), Decimal("83378.1"), Decimal("83568.1"))
    assert (last.vwap, last.volume, last.trade_count) == (Decimal("83464.6"), Decimal("25.82701009"), 1838)
    assert last.symbol == "BTC/USD" and last.source_pair == "XBTUSD" and last.interval_minutes == 15


def _fetch_recorded(name: str, delay: float = 2):
    entry = manifest()[name]
    clock = recorded_clock(name)
    transport = QueueTransport([fixture_text(name)], clock, delay_seconds=delay)
    fetched = KrakenCandleFetcher(transport, clock).fetch(
        BY_CODE[entry["pair"]], entry["interval_minutes"], since=entry["since"])
    return fetched, transport, clock


def test_kraken_fills_quiet_intervals_and_they_are_kept_as_quiet_candles() -> None:
    fetched, _transport, _clock = _fetch_recorded("USDUCUSD_1")
    rows = next(value for key, value in json.loads(fixture_text("USDUCUSD_1"))["result"].items() if key != "last")
    expected_quiet = sum(1 for item in rows[:-1] if item[7] == 0)
    quiet = [candle for candle in fetched.candles if candle.trade_count == 0]
    assert expected_quiet == len(quiet) == fetched.no_trade_candles >= 200
    assert all(candle.volume == 0 and candle.vwap == 0 for candle in quiet)
    assert all(candle.open == candle.high == candle.low == candle.close for candle in quiet)
    assert any(candle.trade_count > 0 for candle in fetched.candles)  # the thin coin did trade now and then
    # No slot is omitted: the series is continuous at one-minute spacing.
    opens = [candle.open_unix for candle in fetched.candles]
    assert opens == list(range(opens[0], opens[-1] + 1, 60))


def test_a_pair_with_no_data_returns_only_the_time_zero_forming_row_and_no_candles() -> None:
    fetched, _transport, _clock = _fetch_recorded("ACAUSD_1")
    assert fetched.candles == () and fetched.rows_received == 1 and fetched.cursor == 0


def test_since_is_inclusive_in_the_recorded_responses() -> None:
    entry = manifest()["XBTUSD_15_since1791903600"]
    fetched, transport, _clock = _fetch_recorded("XBTUSD_15_since1791903600")
    assert [candle.open_unix for candle in fetched.candles] == [entry["since"]]
    assert transport.calls == [f"https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=15&since={entry['since']}"]
    two, _transport, _clock = _fetch_recorded("XBTUSD_15_since1791902700")
    assert [candle.open_unix for candle in two.candles] == [1791902700, 1791903600]


def test_the_requests_are_plain_public_gets_with_only_pair_interval_and_since() -> None:
    assert ohlc_url("XBTUSD", 15, None) == "https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=15"
    assert ohlc_url("SOLUSD", 1, 1791904440) == "https://api.kraken.com/0/public/OHLC?pair=SOLUSD&interval=1&since=1791904440"
    raw, _rows = _table()
    _fetched, transport, _clock = _fetch([raw], interval=15)
    assert transport.calls == ["https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=15"]


# --- completed candles only, and receipt time -----------------------------------------------------------------------


def test_the_final_row_is_dropped_even_when_it_looks_like_a_finished_old_candle() -> None:
    rows = grid_rows(15, unix(NOW) - unix(NOW) % 900 - 4 * 900, 3)
    old_looking = row(unix(NOW) - 30 * 86400 - (unix(NOW) - 30 * 86400) % 900)
    fetched, _transport, _clock = _fetch([wire("XXBTZUSD", [*rows, old_looking])])
    assert [candle.open_unix for candle in fetched.candles] == [item[0] for item in rows]


def test_the_final_row_is_not_inspected_beyond_its_shape() -> None:
    rows = grid_rows(15, unix(NOW) - unix(NOW) % 900 - 3 * 900, 3)
    for final in ([0, "0.00000", "0.00000", "0.00000", "0.00000", "0.00000", "0.00000000", 0],
                  [1, "x", "y", "z", "w", "v", "q", "r"],
                  [unix(NOW) + 10**6, "-1", "-2", "-3", "-4", "-5", "-6", -7]):
        fetched, _transport, _clock = _fetch([wire("XXBTZUSD", [*rows, final])])
        assert len(fetched.candles) == 3


def test_a_final_row_with_the_wrong_shape_refuses_the_response() -> None:
    rows = grid_rows(15, unix(NOW) - unix(NOW) % 900 - 3 * 900, 3)
    for final in ([1, 2, 3], "x", None, {"a": 1}):
        assert _refused(wire("XXBTZUSD", [*rows, final])).category == "response_invalid"


def test_receipt_time_is_taken_after_the_network_call() -> None:
    raw, rows = _table()
    fetched, transport, clock = _fetch([raw], delay=7)
    assert fetched.requested_at == NOW and fetched.received_at == NOW + timedelta(seconds=7) == clock.now()
    assert {candle.available_at_utc for candle in fetched.candles} == {NOW + timedelta(seconds=7)}
    assert len(transport.calls) == 1


def test_a_candle_that_closes_after_the_receipt_time_refuses_the_response() -> None:
    step = 900
    forming = unix(NOW) - unix(NOW) % step
    rows = grid_rows(15, forming - 3 * step, 3)  # the last one closes exactly at the forming open, 15:30:00
    rows.append(row(forming))  # an extra committed-looking row that closes at 15:45, after the receipt
    rows.append(row(forming + step))
    failure = _refused(wire("XXBTZUSD", rows))
    assert failure.category == "candle_not_closed"


def test_a_candle_closing_exactly_at_the_receipt_time_is_complete() -> None:
    forming = unix(NOW) - unix(NOW) % 900
    rows = [*grid_rows(15, forming - 2 * 900, 2), row(forming)]
    clock = FrozenClock(datetime.fromtimestamp(forming, UTC))
    fetched, _transport, _clock = _fetch([wire("XXBTZUSD", rows)], clock=clock)
    assert fetched.candles[-1].close_time_utc == fetched.received_at


def test_a_clock_that_moves_backwards_during_the_request_is_refused() -> None:
    raw, _rows = _table()
    assert _refused(raw, delay=-3).category == "clock_invalid"


def test_a_naive_clock_is_refused_before_any_request() -> None:
    class Naive:
        def now(self) -> datetime:
            return datetime(2026, 10, 13, 15, 30)

    transport = QueueTransport([_table()[0]], FrozenClock(NOW))
    with pytest.raises(ValidationFailure):
        KrakenCandleFetcher(transport, Naive()).fetch(BTC, 15)
    assert transport.calls == []


# --- row validation -------------------------------------------------------------------------------------------------


def _mutated(index: int, **changes) -> str:
    raw, rows = _table(committed=4)
    target = list(rows[index])
    for position, value in changes.items():
        target[int(position[1:])] = value
    rows[index] = target
    return wire("XXBTZUSD", rows)


@pytest.mark.parametrize(("changes", "label"), [
    ({"p0": 1791904501}, "off the grid"),
    ({"p0": "1791904500"}, "timestamp as text"),
    ({"p0": 1791904500.0}, "timestamp as float"),
    ({"p0": True}, "timestamp as bool"),
    ({"p0": None}, "timestamp missing"),
    ({"p0": -900}, "negative timestamp"),
    ({"p1": "abc"}, "open not a number"),
    ({"p1": "NaN"}, "open NaN"),
    ({"p2": "Infinity"}, "high infinite"),
    ({"p1": None}, "open missing"),
    ({"p3": "1E+999"}, "decimal too long to store"),
    ({"p1": "0"}, "open zero"),
    ({"p1": "-5"}, "open negative"),
    ({"p4": "0"}, "close zero"),
    ({"p2": "99"}, "high below the open"),
    ({"p3": "100.25"}, "low above the open"),
    ({"p5": "500"}, "vwap above the high"),
    ({"p5": "0"}, "vwap zero on a traded candle"),
    ({"p6": "-1"}, "negative volume"),
    ({"p6": "abc"}, "volume not a number"),
    ({"p7": -1}, "negative count"),
    ({"p7": "7"}, "count as text"),
    ({"p7": True}, "count as bool"),
    ({"p7": 7.0}, "count as float"),
    ({"p7": 0}, "volume without trades"),
    ({"p7": 10**12 + 1}, "count beyond any plausible number of trades"),
    ({"p7": 2**63}, "count beyond the storable integer"),
    ({"p7": 10**30}, "absurd count"),
])
def test_an_invalid_committed_row_refuses_the_whole_response(changes: dict, label: str) -> None:
    failure = _refused(_mutated(1, **changes))
    assert failure.category == "response_invalid", label
    assert "SECRET" not in str(failure)


def test_a_committed_row_with_the_wrong_length_or_type_is_refused() -> None:
    raw, rows = _table()
    for bad in (rows[1][:7], [*rows[1], 1], "row", None, {"t": 1}):
        copy = list(rows)
        copy[1] = bad
        assert _refused(wire("XXBTZUSD", copy)).category == "response_invalid"


def test_duplicate_or_unordered_timestamps_are_refused_and_a_hole_is_just_a_missing_slot() -> None:
    raw, rows = _table(committed=4)
    duplicate = [rows[0], rows[0], *rows[2:]]
    assert _refused(wire("XXBTZUSD", duplicate)).category == "response_invalid"
    swapped = [rows[1], rows[0], *rows[2:]]
    assert _refused(wire("XXBTZUSD", swapped)).category == "response_invalid"
    # A hole between committed rows is not invalid: it is simply a missing slot, kept missing.
    holey = [rows[0], rows[2], rows[3], rows[4]]
    fetched, _transport, _clock = _fetch([wire("XXBTZUSD", holey)])
    assert [candle.open_unix for candle in fetched.candles] == [rows[0][0], rows[2][0], rows[3][0]]


def test_a_traded_candle_and_a_quiet_candle_with_exact_boundaries_are_accepted() -> None:
    raw, rows = _table(committed=3)
    rows[1] = row(rows[1][0], price="100.0", quiet=True)
    rows[2] = [rows[2][0], "100.0", "100.0", "100.0", "100.0", "100.0", "0.00000001", 1]
    fetched, _transport, _clock = _fetch([wire("XXBTZUSD", rows)])
    assert [candle.trade_count for candle in fetched.candles] == [7, 0, 1]
    assert fetched.no_trade_candles == 1
    assert fetched.candles[2].volume == Decimal("1E-8")


def test_the_largest_accepted_trade_count_is_stored_as_sent() -> None:
    raw, rows = _table(committed=2)
    rows[1] = [*rows[1][:7], 10**12]
    fetched, _transport, _clock = _fetch([wire("XXBTZUSD", rows)])
    assert fetched.candles[1].trade_count == 10**12


def test_numeric_json_values_are_read_exactly_and_equal_the_text_form() -> None:
    raw, rows = _table(committed=2)
    text = _fetch([raw])[0].candles[0]
    numeric = raw.replace('"100.0"', "100.000").replace('"101.5"', "101.500", 1)
    assert numeric != raw
    number = _fetch([numeric])[0].candles[0]
    assert (number.open, number.high) == (Decimal("100.0"), Decimal("101.5"))
    assert number.content_hash == text.content_hash


def test_a_response_with_more_than_the_documented_number_of_rows_is_refused() -> None:
    assert OHLC_MAXIMUM_ROWS == 721  # 720 committed candles plus the one still forming
    step = 900
    forming = unix(NOW) - unix(NOW) % step
    full = grid_rows(15, forming - 720 * step, 721)
    fetched, _transport, _clock = _fetch([wire("XXBTZUSD", full)])
    assert len(fetched.candles) == 720 and fetched.rows_received == 721
    too_many = grid_rows(15, forming - 721 * step, 722)
    assert _refused(wire("XXBTZUSD", too_many)).category == "response_invalid"


# --- shape of the whole response ------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [
    "",
    "not json",
    "[]",
    "null",
    '{"result": {}}',
    '{"error": [], "result": []}',
    '{"error": [], "result": {"XXBTZUSD": []}}',
    '{"error": [], "result": {"last": 5}}',
    '{"error": [], "result": {"XXBTZUSD": [], "last": 5}}',
    '{"error": [], "result": {"XXBTZUSD": "x", "last": 5}}',
    '{"error": "none", "result": {}}',
    '{"error": [], "result": {"XXBTZUSD": [[1,2,3,4,5,6,7,8]], "last": "5"}}',
    '{"error": [], "result": {"XXBTZUSD": [[1,2,3,4,5,6,7,8]], "last": -1}}',
    '{"error": [], "result": {"XXBTZUSD": [[1,2,3,4,5,6,7,8]], "last": true}}',
    '{"error": [], "result": {"XXBTZUSD": [[1,2,3,4,5,6,7,8]], "last": NaN}}',
    '{"error": [], "error": [], "result": {"XXBTZUSD": [[1,2,3,4,5,6,7,8]], "last": 5}}',
    '{"error": [], "result": {"XXBTZUSD": [[1,2,3,4,5,6,7,8]], "last": 5, "last": 6}}',
    '{"error": [], "result": {"XXBTZUSD": [[1,2,3,4,5,6,7,8]], "XETHZUSD": [[1,2,3,4,5,6,7,8]], "last": 5}}',
    "[" * 200_000,
])
def test_an_untrustworthy_response_is_refused_with_a_fixed_category(raw: str) -> None:
    assert _refused(raw).category == "response_invalid"


def test_a_response_for_another_pair_is_refused() -> None:
    raw, _rows = _table(key="XETHZUSD")
    assert _refused(raw).category == "pair_mismatch"


@pytest.mark.parametrize("key", ["XBTUSD", "XXBTZUSD", "XBT/USD"])
def test_the_response_key_may_be_any_of_the_pairs_own_names(key: str) -> None:
    raw, _rows = _table(key=key)
    assert len(_fetch([raw])[0].candles) == 4


def test_a_single_unknown_key_for_a_catalog_pair_is_refused_not_guessed() -> None:
    raw, _rows = _table(key="SOMETHINGELSE")
    assert _refused(raw).category == "pair_mismatch"


def test_a_response_larger_than_the_byte_limit_is_refused() -> None:
    from trade_graph.adapters.market.kraken_candles import OHLC_MAXIMUM_RESPONSE_BYTES

    raw, _rows = _table()
    assert OHLC_MAXIMUM_RESPONSE_BYTES == 1_048_576
    assert _refused(raw + " " * OHLC_MAXIMUM_RESPONSE_BYTES).category == "response_invalid"
    assert _refused(raw + "é" * (OHLC_MAXIMUM_RESPONSE_BYTES // 2 + 1)).category == "response_invalid"


def test_a_transport_that_returns_something_other_than_text_is_refused() -> None:
    assert _refused(None).category == "response_invalid"
    assert _refused(b"bytes").category == "response_invalid"


def test_the_real_transport_is_bounded_and_has_a_deadline() -> None:
    transport = make_ohlc_transport()
    assert isinstance(transport, HttpxTextTransport)
    assert transport.maximum_response_bytes == 1_048_576 and 5 <= transport.timeout <= 30


# --- exchange errors, rate limits and failures ----------------------------------------------------------------------


def test_a_transport_failure_is_a_request_failure_with_no_detail_and_no_retry() -> None:
    clock = FrozenClock(NOW)
    transport = QueueTransport([RuntimeError("SECRET-BODY connection reset"), _table()[0]], clock)
    with pytest.raises(OhlcFetchError) as failure:
        KrakenCandleFetcher(transport, clock).fetch(BTC, 15)
    assert failure.value.category == "request_failed" and "SECRET" not in str(failure.value)
    assert len(transport.calls) == 1


def test_an_http_429_is_a_rate_limit() -> None:
    class Response:
        status_code = 429

    class Rejected(Exception):
        response = Response()

    assert _refused(Rejected("SECRET 429 Too Many Requests")).category == "rate_limited"

    class Other(Exception):
        response = type("R", (), {"status_code": 503})()

    assert _refused(Other("SECRET")).category == "request_failed"


@pytest.mark.parametrize(("message", "category"), [
    ("EAPI:Rate limit exceeded", "rate_limited"),
    ("EQuery:Unknown asset pair", "unknown_pair"),
    ("EService:Unavailable", "service_unavailable"),
    ("EService:Busy", "service_unavailable"),
    ("EGeneral:Internal error", "exchange_error"),
    ("something unexpected SECRET", "exchange_error"),
])
def test_exchange_error_strings_map_to_fixed_categories_without_echoing_the_text(message: str, category: str) -> None:
    failure = _refused(error_wire(message))
    assert failure.category == category
    assert "SECRET" not in str(failure) and "SECRET" not in repr(failure.__dict__)


def test_the_error_class_of_an_unrecognised_error_is_kept_only_when_it_is_plain() -> None:
    assert _refused(error_wire("EGeneral:Internal error")).error_class == "EGeneral"
    assert _refused(error_wire("weird SECRET text")).error_class is None


def test_a_throttle_carries_its_unix_time_and_nothing_else() -> None:
    failure = _refused(error_wire("EService: Throttled: 1791905000"))
    assert failure.category == "throttled" and failure.retry_at_unix == 1791905000
    assert _refused(error_wire("EService:Throttled")).retry_at_unix is None
    assert _refused(error_wire("EService: Throttled: 99999999999999999999999")).retry_at_unix is None


def test_an_error_alongside_candles_is_still_an_error() -> None:
    _raw, rows = _table()
    failure = _refused(wire("XXBTZUSD", rows, error=["EService:Unavailable"]))
    assert failure.category == "service_unavailable"


# --- arguments are checked before any request -----------------------------------------------------------------------


@pytest.mark.parametrize("interval", [0, 2, 30, 240, 720, 1440, True, "15", 15.0, None])
def test_only_the_four_collected_intervals_are_requested(interval) -> None:
    clock = FrozenClock(NOW)
    transport = QueueTransport([], clock)
    with pytest.raises(ValidationFailure):
        KrakenCandleFetcher(transport, clock).fetch(BTC, interval)
    assert transport.calls == []


@pytest.mark.parametrize("since", [-1, True, 1.5, "100", 10**13])
def test_since_must_be_a_plausible_unix_time(since) -> None:
    clock = FrozenClock(NOW)
    transport = QueueTransport([], clock)
    with pytest.raises(ValidationFailure):
        KrakenCandleFetcher(transport, clock).fetch(BTC, 15, since=since)
    assert transport.calls == []


def test_an_unsafe_pair_code_never_reaches_the_url() -> None:
    clock = FrozenClock(NOW)
    transport = QueueTransport([], clock)
    for code in ("../Balance", "XBT USD", "XBTUSD&since=0", "xbtusd", "", "A" * 40):
        with pytest.raises(ValidationFailure):
            ohlc_url(code, 15, None)
    unsafe = BTC.model_copy(update={"source_pair": "XBTUSD&x=1"})
    with pytest.raises(ValidationFailure):
        KrakenCandleFetcher(transport, clock).fetch(unsafe, 15)
    assert transport.calls == []


def test_a_synthetic_fetcher_labels_its_candles_synthetic() -> None:
    raw, _rows = _table()
    clock = FrozenClock(NOW)
    fetcher = KrakenCandleFetcher(QueueTransport([raw], clock), clock, source="synthetic")
    assert {candle.source for candle in fetcher.fetch(BTC, 15).candles} == {"synthetic"}
    with pytest.raises(ValidationFailure):
        KrakenCandleFetcher(QueueTransport([], clock), clock, source="kraken_ohlcvt_archive")
