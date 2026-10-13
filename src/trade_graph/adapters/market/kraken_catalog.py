"""One bounded public Kraken AssetPairs request, parsed into listed spot pairs (no classification here).

GET /0/public/AssetPairs needs no key. Spot currency pairs of any quote are parsed generically; the caller
keeps the USD-quoted ones. A malformed pair is returned as a rejection with a fixed reason, so the catalog is
"partial", never silently shorter. A response whose shape cannot be trusted at all is refused as a whole.
Failure categories are fixed strings and never carry response text.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from trade_graph.adapters.market.public import KRAKEN_REST, HttpxTextTransport, TextTransport
from trade_graph.contracts.pair_catalog import ListedPair, engine_symbol
from trade_graph.domain.clock import Clock
from trade_graph.domain.errors import ValidationFailure

ASSET_PAIRS_URL = f"{KRAKEN_REST}/AssetPairs"
# The full response recorded on 2026-10-13 was 678,583 bytes for 1,460 pairs. 8 MiB leaves about twelve times
# that for new listings and fields, yet is half the transport's own 16 MiB ceiling, so a runaway or hostile
# response cannot exhaust memory (it is held, decoded and parsed in memory).
CATALOG_MAXIMUM_RESPONSE_BYTES = 8 * 1024 * 1024
CATALOG_TIMEOUT_SECONDS = 30.0

# Kraken's older asset codes carry an X (crypto) or Z (fiat) prefix. wsname uses the plain code. Only this
# list is translated: ZCHF (Frankencoin), ZETA and XION are different assets whose names merely start with Z or X.
_LEGACY_ASSET_CODES = {
    "XXBT": "XBT", "XETH": "ETH", "XETC": "ETC", "XLTC": "LTC", "XMLN": "MLN", "XREP": "REP", "XXDG": "XDG",
    "XXLM": "XLM", "XXMR": "XMR", "XXRP": "XRP", "XZEC": "ZEC",
    "ZUSD": "USD", "ZEUR": "EUR", "ZGBP": "GBP", "ZAUD": "AUD", "ZCAD": "CAD", "ZJPY": "JPY",
}
_SCOPE_FIELDS = ("aclass_base", "aclass_quote")
_PAIR_FIELDS = (
    "altname", "wsname", "base", "quote", "status", "ordermin", "costmin", "pair_decimals", "cost_decimals",
    "lot_decimals",
)
_RAW_NAMES = {"source_pair": "altname", "ws_name": "wsname"}
_SAFE_KEY = re.compile(r"[A-Za-z0-9._/-]{1,32}")


@dataclass(frozen=True, slots=True)
class RejectedPair:
    pair_key: str   # the exchange's result key when it is plain text, otherwise "unsafe_key"
    reason: str     # a fixed code such as missing_ordermin, invalid_status or asset_code_mismatch


@dataclass(frozen=True, slots=True)
class ParsedCatalog:
    pairs: tuple[ListedPair, ...]          # well-formed spot currency pairs of every quote, sorted by symbol
    rejected: tuple[RejectedPair, ...]
    out_of_scope: int                      # well-formed entries of another asset class, counted and not parsed
    pairs_in_response: int

    @property
    def partial(self) -> bool:
        return bool(self.rejected)


@dataclass(frozen=True, slots=True)
class FetchedCatalog:
    catalog: ParsedCatalog
    requested_at: datetime
    received_at: datetime


class CatalogFetchError(ValidationFailure):
    """The request or the response failed. ``category`` is a fixed string; no response text is carried."""

    def __init__(self, category: str) -> None:
        super().__init__(f"Kraken pair catalog {category}")
        self.category = category


def make_catalog_transport(timeout: float = CATALOG_TIMEOUT_SECONDS) -> HttpxTextTransport:
    return HttpxTextTransport(timeout, maximum_response_bytes=CATALOG_MAXIMUM_RESPONSE_BYTES)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValidationFailure("Kraken pair catalog response has duplicate JSON fields")
        result[key] = value
    return result


def _refuse_constant(_name: str) -> Any:
    raise ValueError("not a finite JSON number")


class _OutOfScope:
    pass


def _parse_pair(info: object) -> ListedPair | RejectedPair | _OutOfScope:
    """A listed pair, or the reason it is not one. The result key is added by the caller."""
    if not isinstance(info, dict):
        return RejectedPair("", "not_an_object")
    for name in _SCOPE_FIELDS:
        if name not in info:
            return RejectedPair("", f"missing_{name}")
        if not isinstance(info[name], str):
            return RejectedPair("", f"invalid_{name}")
    if info["aclass_base"] != "currency" or info["aclass_quote"] != "currency":
        return _OutOfScope()
    for name in _PAIR_FIELDS:
        if name not in info:
            return RejectedPair("", f"missing_{name}")
    ws_name = info["wsname"]
    try:
        symbol = engine_symbol(ws_name)
    except ValueError:
        return RejectedPair("", "invalid_wsname")
    try:
        listed = ListedPair(
            symbol=symbol, source_pair=info["altname"], ws_name=ws_name, base=info["base"], quote=info["quote"],
            status=info["status"], ordermin=info["ordermin"], costmin=info["costmin"], tick_size=info.get("tick_size"),
            pair_decimals=info["pair_decimals"], cost_decimals=info["cost_decimals"],
            lot_decimals=info["lot_decimals"])
    except ValidationError as error:
        location = error.errors()[0]["loc"]
        field = str(location[0]) if location else ""
        return RejectedPair("", f"invalid_{_RAW_NAMES.get(field, field)}" if field in ListedPair.model_fields
                            else "invalid_pair")
    wire_base, _, wire_quote = ws_name.partition("/")
    if (_LEGACY_ASSET_CODES.get(listed.base, listed.base) != wire_base
            or _LEGACY_ASSET_CODES.get(listed.quote, listed.quote) != wire_quote):
        return RejectedPair("", "asset_code_mismatch")
    return listed


def parse_asset_pairs(raw: str) -> ParsedCatalog:
    try:
        too_large = not isinstance(raw, str) or len(raw.encode("utf-8")) > CATALOG_MAXIMUM_RESPONSE_BYTES
    except UnicodeError:
        raise ValidationFailure("Kraken pair catalog response is not valid text") from None
    if too_large:
        raise ValidationFailure("Kraken pair catalog response exceeded its byte limit")
    try:
        payload = json.loads(raw, parse_float=Decimal, parse_constant=_refuse_constant,
                             object_pairs_hook=_unique_object)
    except (ValueError, TypeError, ArithmeticError, RecursionError):
        raise ValidationFailure("Kraken pair catalog response JSON is invalid") from None
    if not isinstance(payload, dict) or payload.get("error") != []:
        raise ValidationFailure("Kraken pair catalog response has missing or rejected error status")
    result = payload.get("result")
    if not isinstance(result, dict) or not result:
        raise ValidationFailure("Kraken pair catalog result must be a non-empty object")

    listed: list[tuple[str, ListedPair]] = []
    rejected: list[RejectedPair] = []
    out_of_scope = 0
    for key, info in result.items():
        pair_key = key if _SAFE_KEY.fullmatch(key) else "unsafe_key"
        outcome = _parse_pair(info)
        if isinstance(outcome, ListedPair):
            listed.append((pair_key, outcome))
        elif isinstance(outcome, _OutOfScope):
            out_of_scope += 1
        else:
            rejected.append(RejectedPair(pair_key, outcome.reason))

    symbols = Counter(pair.symbol for _, pair in listed)
    codes = Counter(pair.source_pair for _, pair in listed)
    valid: list[ListedPair] = []
    for pair_key, pair in listed:
        # Every pair in a collision is rejected: which one is right is not knowable from the list.
        if symbols[pair.symbol] > 1:
            rejected.append(RejectedPair(pair_key, "duplicate_symbol"))
        elif codes[pair.source_pair] > 1:
            rejected.append(RejectedPair(pair_key, "duplicate_source_pair"))
        else:
            valid.append(pair)
    if not valid:
        raise ValidationFailure("Kraken pair catalog response held no usable pair")
    # Result order is the exchange's own; sorting makes the stored list independent of it.
    return ParsedCatalog(
        pairs=tuple(sorted(valid, key=lambda pair: pair.symbol)), rejected=tuple(rejected),
        out_of_scope=out_of_scope, pairs_in_response=len(result))


def _utc_now(clock: Clock) -> datetime:
    now = clock.now()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValidationFailure("the pair catalog requires an aware receipt clock")
    return now.astimezone(UTC)


class KrakenPairCatalog:
    """Credential-free GET only: one request, no retries, no query parameters."""

    def __init__(self, transport: TextTransport, clock: Clock) -> None:
        self.transport = transport
        self.clock = clock

    def fetch(self) -> FetchedCatalog:
        requested_at = _utc_now(self.clock)
        try:
            raw = self.transport.get_text(ASSET_PAIRS_URL)
        except Exception:
            raise CatalogFetchError("request_failed") from None
        # Receipt is taken after the response, so a slow response is never dated earlier than it arrived.
        received_at = _utc_now(self.clock)
        if received_at < requested_at:
            raise CatalogFetchError("response_invalid")
        try:
            catalog = parse_asset_pairs(raw)
        except ValidationFailure:
            raise CatalogFetchError("response_invalid") from None
        return FetchedCatalog(catalog=catalog, requested_at=requested_at, received_at=received_at)
