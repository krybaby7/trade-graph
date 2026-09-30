"""Provider HTTP. Default tests use a scripted queue and send no credentials."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol

from trade_graph.adapters.market.public import loads


class ProviderHttp(Protocol):
    def post_json(self, url: str, body: dict[str, Any], headers: Mapping[str, str]) -> dict[str, Any]: ...


class ScriptedProviderHttp:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post_json(self, url: str, body: dict[str, Any], headers: Mapping[str, str]) -> dict[str, Any]:
        self.calls.append({"url": url, "body": body, "headers": dict(headers)})
        if not self.responses:
            raise RuntimeError("scripted provider response exhausted")
        return self.responses.pop(0)


class HttpxProviderHttp:
    """Real provider POST. Do not construct this without an owner-funded key and paid calls enabled."""

    def __init__(self, timeout: float = 30) -> None:
        self.timeout = timeout

    def post_json(self, url: str, body: dict[str, Any], headers: Mapping[str, str]) -> dict[str, Any]:
        import httpx

        response = httpx.post(
            url,
            content=json.dumps(body),
            headers={"content-type": "application/json", **dict(headers)},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = loads(response.text)
        if not isinstance(payload, dict):
            raise ValueError("provider response was not an object")
        return payload
