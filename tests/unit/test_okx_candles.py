"""OKX public candles (T29): the strict page parser, the credential-free fetcher and recorded folders.

Every page here is synthetic (``tests/okx_support.py``, labelled so); nothing reaches the network.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.okx_support import FIXTURE, START, fixture_files, pages

from trade_graph.adapters.market.okx_candles import (
    OkxCandleFetcher,
    OkxFetchError,
    history_candles_url,
    okx_symbol,
    parse_history_candles,
    read_recording,
)
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import ValidationFailure

LATER = datetime(2026, 7, 10, tzinfo=UTC)


def _page(rows: list[list[str]], code: str = "0") -> str:
    return json.dumps({"code": code, "msg": "", "data": rows})


def _row(minutes: int, o: str = "150", h: str = "151", low: str = "149", c: str = "150.5", confirm: str = "1"):
    opened = int((START + timedelta(minutes=minutes)).timestamp() * 1000)
    return [str(opened), o, h, low, c, "12.5", "1881.25", "1881.25", confirm]


def test_the_committed_fixture_is_exactly_what_the_generator_writes() -> None:
    files = fixture_files()
    assert sorted(path.name for path in FIXTURE.iterdir()) == sorted(files)
    for name, text in files.items():
        assert (FIXTURE / name).read_text(encoding="utf-8") == text, name


def test_a_page_becomes_completed_candles_oldest_first_and_the_forming_one_is_dropped() -> None:
    newest = pages()[0]
    page = parse_history_candles(newest, inst_id="SOL-USDT", interval_minutes=15, received_at=LATER)
    assert page.rows_received == 100 and page.forming_dropped == 1 and len(page.candles) == 99
    assert [c.open_time_utc for c in page.candles] == sorted(c.open_time_utc for c in page.candles)
    first = page.candles[0]
    assert (first.symbol, first.source_pair, first.source) == ("SOL/USDT", "SOL-USDT", "okx_public_candles")
    assert first.trade_count == 0 and first.vwap is None and first.available_at_utc == LATER
    assert isinstance(first.close, Decimal)
    assert page.oldest_open_ms == int(json.loads(newest)["data"][-1][0])


@pytest.mark.parametrize(("rows", "code", "category"), [
    ([_row(0)], "50011", "rate_limited"),
    ([_row(0)], "51001", "exchange_error"),
    ([_row(0)[:8]], "0", "response_invalid"),  # a field missing
    ([[_row(0)[0], 150, *_row(0)[2:]]], "0", "response_invalid"),  # a number, not decimal text
    ([_row(7)], "0", "response_invalid"),  # off the 15-minute grid
    ([_row(0), _row(0)], "0", "response_invalid"),  # the same candle twice
    ([_row(0, confirm="2")], "0", "response_invalid"),
    ([_row(0, h="150.2")], "0", "response_invalid"),  # the close is above the high
    ([_row(0, confirm="0"), _row(15, confirm="0")], "0", "response_invalid"),  # two still forming
    ([_row(60 * 24 * 30)], "0", "candle_not_closed"),  # completed, yet closing after it was received
])
def test_anything_unexpected_refuses_the_whole_page(rows, code, category) -> None:
    with pytest.raises(OkxFetchError) as caught:
        parse_history_candles(_page(rows, code), inst_id="SOL-USDT", interval_minutes=15, received_at=LATER)
    assert caught.value.category == category


def test_text_that_is_not_a_page_is_refused() -> None:
    for text in ("not json", '{"code": "0", "data": [], "extra": 1}', '{"code": "0", "data": {}}', "x" * 300_000,
                 '{"code": "0", "code": "0", "data": []}'):
        with pytest.raises(OkxFetchError):
            parse_history_candles(text, inst_id="SOL-USDT", interval_minutes=15, received_at=LATER)


def test_the_request_is_one_public_get_with_documented_parameters() -> None:
    url = history_candles_url("SOL-USDT", 15, after_ms=1783314900000)
    assert url == ("https://www.okx.com/api/v5/market/history-candles?instId=SOL-USDT&bar=15m&limit=100"
                   "&after=1783314900000")
    assert okx_symbol("PEPE-USDC") == "PEPE/USDC"
    for bad in ("SOL/USDT", "sol-usdt", "SOL-USDT-SWAP", ""):
        with pytest.raises(ValidationFailure):
            okx_symbol(bad)
    with pytest.raises(ValidationFailure):
        history_candles_url("SOL-USDT", 30)


class _Scripted:
    synthetic = True

    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []

    def get_text(self, url: str) -> str:
        self.urls.append(url)
        answer = self.responses.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class _HttpError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__("http")
        self.response = type("Response", (), {"status_code": status})()


def test_the_fetcher_takes_the_receipt_after_the_call_and_maps_failures() -> None:
    clock = FrozenClock(LATER)
    transport = _Scripted([pages()[0], _HttpError(429), RuntimeError("boom")])
    fetched = OkxCandleFetcher(transport, clock).fetch("SOL-USDT", 15)
    assert fetched.received_at == LATER and fetched.page.forming_dropped == 1
    with pytest.raises(OkxFetchError) as limited:
        OkxCandleFetcher(transport, clock).fetch("SOL-USDT", 15)
    assert limited.value.category == "rate_limited"
    with pytest.raises(OkxFetchError) as failed:
        OkxCandleFetcher(transport, clock).fetch("SOL-USDT", 15)
    assert failed.value.category == "request_failed" and "boom" not in str(failed.value)


def test_a_recording_is_a_plain_labelled_folder(tmp_path) -> None:
    recording = read_recording(FIXTURE)
    assert (recording.inst_id, recording.interval_minutes, recording.evidence_label) == ("SOL-USDT", 15,
                                                                                         "synthetic_fixture")
    assert len(recording.pages) == 7
    copy = tmp_path / "copy"
    copy.mkdir()
    for name, text in fixture_files().items():
        (copy / name).write_text(text)
    manifest = json.loads((copy / "manifest.json").read_text())
    manifest["evidence_label"] = "live_public"
    (copy / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValidationFailure, match="labelled"):
        read_recording(copy)
    manifest["evidence_label"] = "synthetic_fixture"
    manifest["pages"][0]["file"] = "../page-001.json"
    (copy / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValidationFailure, match="named"):
        read_recording(copy)
    linked = tmp_path / "linked"
    linked.symlink_to(FIXTURE)
    with pytest.raises(ValidationFailure, match="plain folder"):
        read_recording(linked)
