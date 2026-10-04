"""Provider HTTP. Default tests use a scripted queue and send no credentials."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from decimal import Decimal
from time import monotonic
from typing import Any, Literal, Protocol

MAXIMUM_PROVIDER_BYTES = 1_048_576


def provider_request_bytes(body: dict[str, Any]) -> bytes:
    pending, nodes, text_size = [(body, 0)], 0, 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 64 or nodes > 10000:
            raise ValueError("provider request structural bound exceeded")
        if type(item) is dict:
            if len(item) + nodes + len(pending) > 10000 or any(type(key) is not str for key in item):
                raise ValueError("provider request object bound exceeded")
            text_size += sum(len(key) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            if len(item) + nodes + len(pending) > 10000:
                raise ValueError("provider request collection bound exceeded")
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            text_size += len(item)
        if text_size > MAXIMUM_PROVIDER_BYTES:
            raise ValueError("provider request byte bound exceeded")
    raw = json.dumps(body, allow_nan=False).encode("utf-8")
    if len(raw) > MAXIMUM_PROVIDER_BYTES:
        raise ValueError("provider request byte bound exceeded")
    return raw


def provider_payload(raw: bytes) -> Any:
    def unique(pairs):
        document = {}
        for key, value in pairs:
            if key in document:
                raise ValueError("provider duplicate JSON key")
            document[key] = value
        return document

    def finite(value):
        raise ValueError("provider nonfinite JSON value")

    return json.loads(raw.decode("utf-8"), parse_float=Decimal, object_pairs_hook=unique, parse_constant=finite)


class ProviderHttp(Protocol):
    def post_json(
        self, url: str, body: dict[str, Any], headers: Mapping[str, str], *, timeout_seconds: float | None = None,
    ) -> dict[str, Any]: ...


class ProviderHttpResponseError(RuntimeError):
    """An HTTP denial with optional billing facts, never an untrusted response message."""

    def __init__(self, status_code: int, usage_payload: dict[str, Any]) -> None:
        super().__init__(f"provider HTTP {status_code}")
        self.usage_payload = usage_payload
        self.failure: Literal["credentials", "rate_limit", "timeout_uncertain", "temporary", "validation"]
        if status_code in {401, 403}:
            self.failure = "credentials"
        elif status_code == 429:
            self.failure = "rate_limit"
        elif status_code in {408, 504}:
            self.failure = "timeout_uncertain"
        elif 500 <= status_code < 600:
            self.failure = "temporary"
        else:
            self.failure = "validation"


class ScriptedProviderHttp:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post_json(
        self, url: str, body: dict[str, Any], headers: Mapping[str, str], *, timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append({"url": url, "body": body, "headers": dict(headers)})
        if not self.responses:
            raise RuntimeError("scripted provider response exhausted")
        return self.responses.pop(0)


class HttpxProviderHttp:
    """Real provider POST. Do not construct this without an owner-funded key and paid calls enabled."""

    def __init__(self, timeout: float = 30, *, maximum_response_bytes: int = MAXIMUM_PROVIDER_BYTES,
                 proxy: str | None = None, trust_env: bool = True,
                 allowed_urls: tuple[str, ...] | None = None) -> None:
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("positive finite provider timeout required")
        self.timeout = timeout
        if type(maximum_response_bytes) is not int or not 1 <= maximum_response_bytes <= MAXIMUM_PROVIDER_BYTES:
            raise ValueError("provider response byte bound must be between 1 and 1048576")
        self.maximum_response_bytes = maximum_response_bytes
        if type(trust_env) is not bool or proxy is not None and type(proxy) is not str:
            raise ValueError("explicit trusted provider proxy/environment policy required")
        if allowed_urls is not None and (
            type(allowed_urls) is not tuple or not 1 <= len(allowed_urls) <= 2
            or len(set(allowed_urls)) != len(allowed_urls)
            or any(url not in {"https://api.openai.com/v1/responses", "https://api.anthropic.com/v1/messages"}
                   for url in allowed_urls)
        ):
            raise ValueError("fixed official provider endpoints required")
        self.proxy, self.trust_env, self.allowed_urls = proxy, trust_env, allowed_urls

    def post_json(
        self, url: str, body: dict[str, Any], headers: Mapping[str, str], *, timeout_seconds: float | None = None,
        observe: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        import httpx

        if self.allowed_urls is not None and url not in self.allowed_urls:
            raise ValueError("provider destination is outside the owner-pinned funded-paper profile")
        if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0
        ):
            raise ValueError("positive finite provider timeout required")
        timeout = min(self.timeout, timeout_seconds) if timeout_seconds is not None else self.timeout
        started = monotonic()
        raw = provider_request_bytes(body)
        status, payload_bytes = None, bytearray()

        def report(outcome, *, error=None, complete=False):
            if observe:
                observe({"outcome": outcome, "status_code": status,
                         "response_sha256": hashlib.sha256(payload_bytes).hexdigest() if complete else None,
                         "response_bytes": len(payload_bytes), "error_category": error})

        try:
            wire_headers = {name: value for name, value in headers.items()
                            if name.lower() not in {"content-type", "accept-encoding"}}
            with httpx.stream("POST", url, content=raw,
                headers={**wire_headers, "content-type": "application/json", "accept-encoding": "identity"},
                timeout=timeout, follow_redirects=False, proxy=self.proxy, trust_env=self.trust_env) as response:
                status = response.status_code
                if 300 <= status < 400:
                    raise ValueError("provider redirect refused")
                if response.headers.get("content-encoding", "").strip().lower() not in {"", "identity"}:
                    raise ValueError("provider content encoding refused")
                declared = response.headers.get("content-length")
                if declared is not None and (not declared.isdecimal() or int(declared) > self.maximum_response_bytes):
                    raise ValueError("provider declared response byte bound exceeded")
                for chunk in response.iter_bytes():
                    if monotonic() - started >= timeout:
                        raise TimeoutError("provider elapsed deadline exceeded")
                    if len(payload_bytes) + len(chunk) > self.maximum_response_bytes:
                        raise ValueError("provider response byte bound exceeded")
                    payload_bytes.extend(chunk)
                if monotonic() - started >= timeout:
                    raise TimeoutError("provider elapsed deadline exceeded")
        except httpx.RequestError:
            # A transport exception does not establish whether the provider processed
            # the request. Drop HTTPX's URL/header/body-bearing exception entirely.
            report("UNCERTAIN", error="transport")
            raise TimeoutError("provider transport outcome uncertain") from None
        except TimeoutError:
            report("UNCERTAIN", error="deadline")
            raise
        except ValueError:
            report("REFUSED_RESPONSE", error="response_bounds")
            raise
        report("HTTP_RESPONSE", complete=True)
        if not 200 <= status < 300:
            try:
                payload = provider_payload(payload_bytes)
            except (ValueError, UnicodeError):
                payload = None
            # Only actual provider billing facts cross this failure boundary. Missing
            # usage stays missing; neither an HTTP denial nor a fixture proves zero cost.
            usage_payload = {
                name: payload[name] for name in ("usage", "id", "model") if name in payload
            } if isinstance(payload, dict) else {}
            raise ProviderHttpResponseError(status, usage_payload)
        payload = provider_payload(payload_bytes)
        if not isinstance(payload, dict):
            raise ValueError("provider response was not an object")
        return payload
