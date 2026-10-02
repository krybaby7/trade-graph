"""Provider-neutral broker, market and inference protocols.

Adapters live outside this package. These interfaces carry normalized DTOs only:
no vendor SDK, no graph runtime, and no withdrawal operation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from trade_graph.contracts.models import (
    AuthorizedOrderIntent,
    BalanceSnapshot,
    BrokerCapabilities,
    CancelRequest,
    CancelResult,
    FillPage,
    InstrumentRules,
    ModelCapabilities,
    ModelRequest,
    ModelResult,
    Observation,
    OrderLookup,
    OrderLookupResult,
    OrderSnapshot,
    SubmitResult,
)


@runtime_checkable
class Broker(Protocol):
    """Paper, replay and live adapters share this surface. There is no withdraw method."""

    async def capabilities(self) -> BrokerCapabilities: ...

    async def instruments(self) -> list[InstrumentRules]: ...

    async def balances(self) -> BalanceSnapshot: ...

    async def open_orders(self) -> list[OrderSnapshot]: ...

    async def fills_since(self, cursor: str | None) -> FillPage: ...

    async def submit(self, intent: AuthorizedOrderIntent) -> SubmitResult: ...

    async def cancel(self, request: CancelRequest) -> CancelResult: ...

    async def order_status(self, key: OrderLookup) -> OrderLookupResult: ...


@runtime_checkable
class MarketData(Protocol):
    """Observations and clocks. Replay must not reveal events after the requested time."""

    venue: str

    async def instrument_rules(self) -> list[InstrumentRules]: ...

    async def poll(self) -> list[Observation]: ...

    def visible(self, symbol: str, as_of: datetime) -> list[Observation]: ...


@runtime_checkable
class InferenceAdapter(Protocol):
    """Translate and interpret provider payloads. Transport and billing stay outside the adapter."""

    provider: str

    def capabilities(self, model: str) -> ModelCapabilities | None: ...

    def build_body(self, request: ModelRequest) -> dict[str, Any]: ...

    def parse(self, payload: dict[str, Any]) -> ModelResult: ...
