"""Bounded Kraken REST transport; private access is read-only by default.

Credentials stay inside this protected adapter. No retry can repeat a write and
no funding, withdrawal or arbitrary endpoint is available through this surface.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlencode

import httpx

from trade_graph.domain.errors import AuthorityDenied, TradeGraphError

PUBLIC_METHODS = frozenset({"Assets", "AssetPairs"})
PRIVATE_READ_METHODS = frozenset(
    {
        "BalanceEx",
        "OpenOrders",
        "ClosedOrders",
        "QueryOrders",
        "TradesHistory",
        "QueryLedgers",
        "TradeVolume",
    }
)
ORDER_METHODS = frozenset({"AddOrder", "CancelOrder"})
MAXIMUM_JSON_DEPTH = 64


@dataclass(frozen=True)
class KrakenReadResponse:
    """Protected read capture; credentials/signature headers are never included."""

    method: str
    parameters: bytes
    request_sha256: str
    response: bytes
    started_at: datetime
    finished_at: datetime
    transport_basis: str


class KrakenApiError(TradeGraphError):
    """Safe category and outcome uncertainty; raw response bodies are never errors."""

    def __init__(self, kind: str, *, uncertain: bool = False) -> None:
        self.kind, self.uncertain = kind, uncertain
        super().__init__(
            {
                "credentials": "Kraken authentication or permission denied",
                "rate_limit": "Kraken rate limit reached",
                "temporary": "Kraken service unavailable",
                "unknown_order": "Kraken order is not visible",
                "validation": "Kraken request rejected",
                "malformed": "Kraken response could not be normalized safely",
            }.get(kind, "Kraken operation failed")
        )


def check_response(payload: object) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("error"), list):
        raise KrakenApiError("malformed")
    errors = payload["error"]
    if errors:
        if any(not isinstance(error, str) for error in errors):
            raise KrakenApiError("malformed")
        if any(
            error.startswith(("EAPI:Invalid key", "EAPI:Invalid signature", "EGeneral:Permission denied"))
            for error in errors
        ):
            raise KrakenApiError("credentials")
        if any("Rate limit" in error or "Throttled" in error for error in errors):
            raise KrakenApiError("rate_limit")
        if any(error.startswith(("EService:", "EGeneral:Temporary")) for error in errors):
            raise KrakenApiError("temporary")
        if any("Internal error" in error for error in errors):
            raise KrakenApiError("temporary")
        if any(error.startswith("EOrder:Unknown order") for error in errors):
            raise KrakenApiError("unknown_order")
        if all(error.startswith(("EOrder:", "EAPI:", "EGeneral:Invalid", "EGeneral:Permission")) for error in errors):
            raise KrakenApiError("validation")
        raise KrakenApiError("malformed")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise KrakenApiError("malformed")
    return result


def _unique_fields(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _finite_constant(value: str) -> None:
    raise ValueError("nonfinite JSON number")


def _check_json_depth(content: bytearray) -> None:
    # Bound nesting independently of the interpreter's mutable recursion limit.
    # Quotes/escapes are handled before counting structural JSON delimiters.
    depth, quoted, escaped = 0, False, False
    for value in content:
        if quoted:
            if escaped:
                escaped = False
            elif value == 92:
                escaped = True
            elif value == 34:
                quoted = False
        elif value == 34:
            quoted = True
        elif value in {91, 123}:
            depth += 1
            if depth > MAXIMUM_JSON_DEPTH:
                raise ValueError("JSON nesting exceeds the protected bound")
        elif value in {93, 125}:
            depth -= 1


class MonotonicNonce:
    """One process/key nonce source. Owners must not share keys across processes."""

    def __init__(self, clock_ns: Callable[[], int] = time.time_ns) -> None:
        self._clock_ns, self._last, self._lock = clock_ns, 0, threading.Lock()

    def __call__(self) -> int:
        with self._lock:
            self._last = max(self._last + 1, self._clock_ns() // 1000)
            return self._last


def signature(secret: str, path: str, body: str, nonce: int) -> str:
    """Kraken: HMAC-SHA512(path + SHA256(nonce + encoded POST body))."""
    try:
        key = base64.b64decode(secret, validate=True)
        if not key:
            raise ValueError("empty key")
    except (ValueError, TypeError):
        raise AuthorityDenied("invalid private Kraken credential encoding") from None
    digest = hashlib.sha256((str(nonce) + body).encode()).digest()
    return base64.b64encode(hmac.new(key, path.encode() + digest, hashlib.sha512).digest()).decode()


class KrakenRestTransport:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        allow_order_writes: bool = False,
        client: httpx.AsyncClient | None = None,
        nonce: Callable[[], int] | None = None,
        timeout_seconds: float = 10,
        maximum_response_bytes: int = 4 * 1024 * 1024,
        read_observer: Callable[[KrakenReadResponse], None] | None = None,
    ) -> None:
        if type(allow_order_writes) is not bool:
            raise ValueError("order-write authority must be an explicit boolean")
        if not 1 <= maximum_response_bytes <= 8 * 1024 * 1024 or not 0 < timeout_seconds <= 60:
            raise ValueError("transport bounds must be positive")
        self._api_key, self._api_secret = api_key, api_secret
        self._allow_order_writes = allow_order_writes
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False)
        self._timeout, self._maximum = timeout_seconds, maximum_response_bytes
        self._nonce, self._lock = nonce or MonotonicNonce(), asyncio.Lock()
        self._read_observer = read_observer

    @property
    def observation_basis(self) -> str:
        """Injected clients never establish production HTTPS origin."""
        if (self._owns_client and type(self._client) is httpx.AsyncClient
                and type(self._client._transport) is httpx.AsyncHTTPTransport
                and all(value is None or type(value) is httpx.AsyncHTTPTransport
                        for value in self._client._mounts.values())):
            return "owned_https"
        return "injected_transport"

    async def __call__(self, method: str, params: dict) -> dict:
        if method not in PUBLIC_METHODS | PRIVATE_READ_METHODS | ORDER_METHODS:
            raise AuthorityDenied("Kraken endpoint is outside the protected allowlist")
        if method in ORDER_METHODS and not self._allow_order_writes:
            raise AuthorityDenied("Kraken transport order writes are disabled")
        if method not in PUBLIC_METHODS and not (self._api_key and self._api_secret):
            raise AuthorityDenied("a private read-only Kraken credential is required")
        # The same protected key is serialized through nonce creation and dispatch.
        async with self._lock:
            started_at = datetime.now(UTC)
            try:
                headers = {"Accept-Encoding": "identity"}
                kwargs = {"headers": headers, "timeout": self._timeout, "follow_redirects": False}
                if method in PUBLIC_METHODS:
                    verb, url = "GET", "https://api.kraken.com/0/public/" + method
                    kwargs["params"] = params
                    request_sha256 = hashlib.sha256(urlencode(params).encode()).hexdigest()
                else:
                    nonce = self._nonce()
                    if not isinstance(nonce, int) or isinstance(nonce, bool) or not 0 < nonce < 2**64:
                        raise AuthorityDenied("Kraken nonce source returned an invalid value")
                    if "nonce" in params:
                        raise AuthorityDenied("Kraken request cannot override the protected nonce")
                    path = "/0/private/" + method
                    body = urlencode({"nonce": nonce, **params})
                    headers.update(
                        {
                            "API-Key": self._api_key,
                            "API-Sign": signature(self._api_secret, path, body, nonce),
                            "Content-Type": "application/x-www-form-urlencoded",
                        }
                    )
                    verb, url = "POST", "https://api.kraken.com" + path
                    kwargs["content"] = body
                    request_sha256 = hashlib.sha256(body.encode()).hexdigest()
                async with asyncio.timeout(self._timeout):
                    async with self._client.stream(verb, url, **kwargs) as response:
                        if response.status_code != 200:
                            kind = (
                                "credentials"
                                if response.status_code in {401, 403}
                                else ("rate_limit" if response.status_code == 429 else "temporary")
                            )
                            raise KrakenApiError(
                                kind, uncertain=method in ORDER_METHODS and response.status_code >= 500
                            )
                        if response.headers.get("content-encoding", "identity").lower() != "identity":
                            raise KrakenApiError("malformed", uncertain=method in ORDER_METHODS)
                        content = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=min(self._maximum + 1, 65536)):
                            if len(content) + len(chunk) > self._maximum:
                                raise KrakenApiError("malformed", uncertain=method in ORDER_METHODS)
                            content.extend(chunk)
            except (httpx.HTTPError, TimeoutError):
                raise KrakenApiError("temporary", uncertain=method in ORDER_METHODS) from None
            try:
                _check_json_depth(content)
                payload = json.loads(
                    content, parse_float=Decimal, object_pairs_hook=_unique_fields, parse_constant=_finite_constant
                )
                check_response(payload)
            except (ValueError, TypeError, RecursionError):
                raise KrakenApiError("malformed", uncertain=method in ORDER_METHODS) from None
            except KrakenApiError as exc:
                if exc.kind == "malformed" and method in ORDER_METHODS:
                    raise KrakenApiError("malformed", uncertain=True) from None
                raise
            if self._read_observer is not None and method not in ORDER_METHODS:
                parameters = json.dumps(params, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
                self._read_observer(KrakenReadResponse(
                    method=method, parameters=parameters, request_sha256=request_sha256, response=bytes(content),
                    started_at=started_at, finished_at=datetime.now(UTC), transport_basis=self.observation_basis,
                ))
            return payload

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
