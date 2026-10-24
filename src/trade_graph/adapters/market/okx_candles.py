"""OKX public candles for the practice range (T29, D126, D168): one strict parser, one credential-free GET.

``GET /api/v5/market/history-candles`` needs no key. As read from OKX's public API documentation (the cloud build
could not open okx.com on 2026-10-16, so step L2 confirms each point against a real response):

- parameters ``instId`` (for example ``SOL-USDT``), ``bar`` (``1m``, ``5m``, ``15m``, ``1H``), ``after`` (only
  candles older than this open time in milliseconds, for paging back) and ``limit`` (at most 100);
- the answer is ``{"code": "0", "msg": "", "data": [[ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm], ...]}``,
  newest first. ``ts`` is the open time in milliseconds, prices are decimal text, ``vol`` is the base-currency volume
  for spot, and ``confirm`` is ``"1"`` for a completed candle and ``"0"`` for the one still forming;
- the documented rate limit is 20 requests per 2 seconds.

What this module relies on: a candle still forming is dropped unseen; a completed candle that closes after the
receipt time refuses the whole page; rows must be unique, on the interval grid and inside their own high-low range.
OKX reports no trade count and no volume-weighted price, so candles carry ``trade_count`` 0 (unknown for this source,
not "no trades") and no vwap. The receipt time is taken after the call, never from the exchange. No credentials,
cookies, redirects, retries or order endpoints exist here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from pydantic import ValidationError

from trade_graph.adapters.market.public import HttpxTextTransport, TextTransport
from trade_graph.contracts.candles import Candle, CandleSource, from_unix
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.money import parse_decimal

OKX_REST = "https://www.okx.com"
OKX_HISTORY_CANDLES_PATH = "/api/v5/market/history-candles"
OKX_BARS = {1: "1m", 5: "5m", 15: "15m", 60: "1H"}
OKX_PAGE_LIMIT = 100
OKX_MAXIMUM_RESPONSE_BYTES = 262_144  # a full page was about 10 KB in OKX's documented examples
OKX_TIMEOUT_SECONDS = 15.0
OKX_SOURCE: CandleSource = "okx_public_candles"
OKX_FAILURE_CATEGORIES = ("request_failed", "rate_limited", "exchange_error", "response_invalid",
                          "candle_not_closed", "clock_invalid")
RECORDING_FORMAT = "okx-history-candles-recording-v1"
RECORDING_LABELS = ("synthetic_fixture", "recorded_public_fixture")
MAX_RECORDING_PAGES = 2_000
_INST_ID = re.compile(r"([A-Z0-9]{1,16})-([A-Z]{3,5})")
_DIGITS = re.compile(r"[0-9]{1,16}")
_PAGE_NAME = re.compile(r"page-[0-9]{3,5}\.json")
_ROW_LENGTH = 9
_MAX_PRICE_TEXT = 40


class OkxFetchError(ValidationFailure):
    """A fixed category; response text never enters the message."""

    def __init__(self, category: str) -> None:
        if category not in OKX_FAILURE_CATEGORIES:
            raise ValueError("unknown OKX failure category")
        super().__init__(f"OKX candles: {category}")
        self.category = category


def okx_symbol(inst_id: str) -> str:
    """``SOL-USDT`` is stored as ``SOL/USDT`` (the series keeps OKX's own code as its venue pair code)."""
    match = _INST_ID.fullmatch(inst_id) if isinstance(inst_id, str) else None
    if match is None:
        raise ValidationFailure("an OKX spot instrument looks like SOL-USDT")
    return f"{match.group(1)}/{match.group(2)}"


def okx_bar(interval_minutes: int) -> str:
    try:
        return OKX_BARS[interval_minutes]
    except (KeyError, TypeError):
        raise ValidationFailure("OKX candles are read at 1, 5, 15 or 60 minutes") from None


def history_candles_url(inst_id: str, interval_minutes: int, *, after_ms: int | None = None,
                        limit: int = OKX_PAGE_LIMIT) -> str:
    okx_symbol(inst_id)
    if type(limit) is not int or not 1 <= limit <= OKX_PAGE_LIMIT:
        raise ValidationFailure(f"an OKX page holds 1 to {OKX_PAGE_LIMIT} candles")
    query: dict[str, Any] = {"instId": inst_id, "bar": okx_bar(interval_minutes), "limit": limit}
    if after_ms is not None:
        if type(after_ms) is not int or after_ms <= 0:
            raise ValidationFailure("after is a positive open time in milliseconds")
        query["after"] = after_ms
    return f"{OKX_REST}{OKX_HISTORY_CANDLES_PATH}?{urlencode(query)}"


@dataclass(frozen=True, slots=True)
class OkxPage:
    inst_id: str
    interval_minutes: int
    received_at: datetime
    rows_received: int
    forming_dropped: int
    candles: tuple[Candle, ...]  # oldest first
    oldest_open_ms: int | None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _refuse_constant(_name: str) -> Any:
    raise ValueError("non-finite number")


def _decimal_text(value: object) -> Decimal:
    if not isinstance(value, str) or not 1 <= len(value) <= _MAX_PRICE_TEXT:
        raise OkxFetchError("response_invalid")
    try:
        return parse_decimal(value)
    except (ValueError, ArithmeticError):
        raise OkxFetchError("response_invalid") from None


def parse_history_candles(text: object, *, inst_id: str, interval_minutes: int, received_at: datetime,
                          source: CandleSource = OKX_SOURCE) -> OkxPage:
    """One OKX page as completed candles, oldest first; anything unexpected refuses the whole page."""
    symbol = okx_symbol(inst_id)
    okx_bar(interval_minutes)
    if source not in (OKX_SOURCE, "synthetic"):
        raise ValidationFailure("OKX pages are stored as okx_public_candles (or synthetic in tests)")
    if not isinstance(received_at, datetime) or received_at.tzinfo is None:
        raise OkxFetchError("clock_invalid")
    try:
        too_large = not isinstance(text, str) or len(text.encode("utf-8")) > OKX_MAXIMUM_RESPONSE_BYTES
        payload = None if too_large else json.loads(text, parse_constant=_refuse_constant,
                                                       object_pairs_hook=_unique_object)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise OkxFetchError("response_invalid") from None
    if too_large or not isinstance(payload, dict) or set(payload) - {"code", "msg", "data"}:
        raise OkxFetchError("response_invalid")
    code = payload.get("code")
    if code == "50011":
        raise OkxFetchError("rate_limited")
    if code != "0":
        raise OkxFetchError("exchange_error" if isinstance(code, str) else "response_invalid")
    rows = payload.get("data")
    if not isinstance(rows, list) or len(rows) > OKX_PAGE_LIMIT + 1:
        raise OkxFetchError("response_invalid")
    step_ms = interval_minutes * 60_000
    candles: list[Candle] = []
    forming = 0
    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, list) or len(row) != _ROW_LENGTH or not all(isinstance(item, str) for item in row):
            raise OkxFetchError("response_invalid")
        if not _DIGITS.fullmatch(row[0]):
            raise OkxFetchError("response_invalid")
        opened_ms = int(row[0])
        if opened_ms % step_ms or opened_ms in seen:
            raise OkxFetchError("response_invalid")
        seen.add(opened_ms)
        if row[8] == "0":
            forming += 1  # still forming: dropped, its values never read
            continue
        if row[8] != "1":
            raise OkxFetchError("response_invalid")
        opening, high, low, closing, volume = (_decimal_text(row[index]) for index in (1, 2, 3, 4, 5))
        open_time = from_unix(opened_ms // 1000)
        close_time = from_unix((opened_ms + step_ms) // 1000)
        if close_time > received_at:
            raise OkxFetchError("candle_not_closed")
        try:
            candles.append(Candle(
                symbol=symbol, source_pair=inst_id, interval_minutes=interval_minutes, open_time_utc=open_time,
                close_time_utc=close_time, available_at_utc=received_at, open=opening, high=high, low=low,
                close=closing, vwap=None, volume=volume, trade_count=0, source=source))
        except ValidationError:
            raise OkxFetchError("response_invalid") from None
    if forming > 1:
        raise OkxFetchError("response_invalid")  # only the newest candle can still be forming
    candles.sort(key=lambda candle: candle.open_time_utc)
    oldest = min(seen) if seen else None
    return OkxPage(inst_id=inst_id, interval_minutes=interval_minutes, received_at=received_at,
                   rows_received=len(rows), forming_dropped=forming, candles=tuple(candles), oldest_open_ms=oldest)


@dataclass(frozen=True, slots=True)
class OkxFetch:
    url: str
    requested_at: datetime
    received_at: datetime
    text: str
    page: OkxPage


def make_okx_transport(timeout: float = OKX_TIMEOUT_SECONDS) -> HttpxTextTransport:
    return HttpxTextTransport(timeout=timeout, maximum_response_bytes=OKX_MAXIMUM_RESPONSE_BYTES)


class OkxCandleFetcher:
    """Credential-free GET only: one request per call, no retries; the caller pages and paces."""

    def __init__(self, transport: TextTransport, clock: Clock) -> None:
        self.transport, self.clock = transport, clock

    def fetch(self, inst_id: str, interval_minutes: int, *, after_ms: int | None = None) -> OkxFetch:
        url = history_candles_url(inst_id, interval_minutes, after_ms=after_ms)
        requested_at = self.clock.now().astimezone(UTC)
        try:
            text = self.transport.get_text(url)
        except Exception as error:
            status = getattr(getattr(error, "response", None), "status_code", None)
            raise OkxFetchError("rate_limited" if status == 429 else "request_failed") from None
        received_at = self.clock.now().astimezone(UTC)
        if received_at < requested_at:
            raise OkxFetchError("clock_invalid")
        page = parse_history_candles(text, inst_id=inst_id, interval_minutes=interval_minutes,
                                     received_at=received_at)
        return OkxFetch(url=url, requested_at=requested_at, received_at=received_at, text=text, page=page)


# --- recordings ------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OkxRecording:
    folder: Path
    inst_id: str
    interval_minutes: int
    evidence_label: str
    note: str
    pages: tuple[tuple[dict[str, Any], str], ...]  # manifest entry and the page's text, in recorded order


def read_recording(folder: Path | str) -> OkxRecording:
    """A folder of OKX pages as recorded (``manifest.json`` plus ``page-NNN.json``); plain files only, bounded."""
    root = Path(folder)
    if root.is_symlink() or not root.is_dir():
        raise ValidationFailure("an OKX recording is a plain folder")
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size > 1_048_576:
        raise ValidationFailure("an OKX recording needs a plain manifest.json of at most 1 MB")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError):
        raise ValidationFailure("the OKX recording manifest is not valid JSON") from None
    if not isinstance(manifest, dict) or manifest.get("format") != RECORDING_FORMAT:
        raise ValidationFailure(f"the manifest must say format {RECORDING_FORMAT}")
    label = manifest.get("evidence_label")
    if label not in RECORDING_LABELS:
        raise ValidationFailure("an OKX recording is labelled synthetic_fixture or recorded_public_fixture")
    inst_id, bar = manifest.get("inst_id"), manifest.get("bar")
    okx_symbol(inst_id)
    interval = next((minutes for minutes, name in OKX_BARS.items() if name == bar), None)
    if interval is None:
        raise ValidationFailure("the manifest's bar must be 1m, 5m, 15m or 1H")
    entries = manifest.get("pages")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_RECORDING_PAGES:
        raise ValidationFailure(f"a recording lists 1 to {MAX_RECORDING_PAGES} pages")
    pages: list[tuple[dict[str, Any], str]] = []
    for entry in entries:
        name = entry.get("file") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not _PAGE_NAME.fullmatch(name):
            raise ValidationFailure("recorded pages are named page-NNN.json")
        path = root / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > OKX_MAXIMUM_RESPONSE_BYTES:
            raise ValidationFailure(f"recorded page {name} is missing, linked or too large")
        pages.append((entry, path.read_text(encoding="utf-8")))
    return OkxRecording(folder=root, inst_id=inst_id, interval_minutes=interval, evidence_label=label,
                        note=str(manifest.get("note", ""))[:2000], pages=tuple(pages))


def write_recording(folder: Path | str, *, inst_id: str, interval_minutes: int, fetches: list[OkxFetch],
                    note: str, synthetic: bool = False) -> Path:
    """Keep the pages exactly as received, for a later import labelled ``recorded_public_fixture`` (or
    ``synthetic_fixture`` when a scripted transport produced them)."""
    root = Path(folder)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    entries = []
    for number, fetch in enumerate(fetches, start=1):
        name = f"page-{number:03d}.json"
        (root / name).write_text(fetch.text, encoding="utf-8")
        entries.append({"file": name, "url": fetch.url, "requested_at_utc": utc_iso(fetch.requested_at),
                        "received_at_utc": utc_iso(fetch.received_at), "bytes": len(fetch.text.encode("utf-8")),
                        "rows": fetch.page.rows_received})
    label = "synthetic_fixture" if synthetic else "recorded_public_fixture"
    manifest = {"format": RECORDING_FORMAT, "evidence_label": label, "inst_id": inst_id,
                "bar": okx_bar(interval_minutes), "pages": entries, "note": note}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return root


__all__ = [
    "OKX_BARS", "OKX_FAILURE_CATEGORIES", "OKX_PAGE_LIMIT", "OKX_REST", "OKX_SOURCE", "RECORDING_FORMAT",
    "OkxCandleFetcher", "OkxFetch", "OkxFetchError", "OkxPage", "OkxRecording", "history_candles_url",
    "make_okx_transport", "okx_bar", "okx_symbol", "parse_history_candles", "read_recording", "write_recording",
]
