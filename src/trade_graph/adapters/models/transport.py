"""Provider HTTP. Default tests use a scripted queue and send no credentials."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any, Literal, Protocol

from trade_graph.adapters.market.public import loads


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

    def __init__(self, timeout: float = 30) -> None:
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("positive finite provider timeout required")
        self.timeout = timeout

    def post_json(
        self, url: str, body: dict[str, Any], headers: Mapping[str, str], *, timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        import httpx

        if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0
        ):
            raise ValueError("positive finite provider timeout required")
        timeout = min(self.timeout, timeout_seconds) if timeout_seconds is not None else self.timeout
        try:
            response = httpx.post(
                url,
                content=json.dumps(body),
                headers={"content-type": "application/json", **dict(headers)},
                timeout=timeout,
            )
        except httpx.RequestError:
            # A transport exception does not establish whether the provider processed
            # the request. Drop HTTPX's URL/header/body-bearing exception entirely.
            raise TimeoutError("provider transport outcome uncertain") from None
        if not 200 <= response.status_code < 300:
            try:
                payload = loads(response.text)
            except (ValueError, UnicodeError):
                payload = None
            # Only actual provider billing facts cross this failure boundary. Missing
            # usage stays missing; neither an HTTP denial nor a fixture proves zero cost.
            usage_payload = {
                name: payload[name] for name in ("usage", "id", "model") if name in payload
            } if isinstance(payload, dict) else {}
            raise ProviderHttpResponseError(response.status_code, usage_payload)
        payload = loads(response.text)
        if not isinstance(payload, dict):
            raise ValueError("provider response was not an object")
        return payload
