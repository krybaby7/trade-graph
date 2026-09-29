"""Kraken spot adapter. It has no withdrawal method and refuses orders while live mode is closed."""

from __future__ import annotations

from datetime import UTC
from decimal import Decimal

from trade_graph.contracts.models import (
    AuthorizedOrderIntent,
    BalanceSnapshot,
    BrokerCapabilities,
    CancelRequest,
    CancelResult,
    FillPage,
    InstrumentRules,
    OrderLookup,
    OrderLookupResult,
    OrderSnapshot,
    SubmitResult,
)
from trade_graph.domain.errors import LiveDisabled


class KrakenLiveBroker:
    """Wire-shape adapter. Default tests inject a transport and never enable live trading."""

    def __init__(self, transport, *, live_enabled: bool = False, key_present: bool = False) -> None:
        self.transport = transport
        self.live_enabled = live_enabled
        self.key_present = key_present

    async def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            venue="kraken",
            mode="live",
            client_id_lookup=True,
            native_amend=False,
            native_stop=True,
            native_stop_tested=False,
            time_in_force=["gtc", "ioc"],
            reduce_only_flag=False,
            fills_pagination=True,
            cancel_behaviour="query-after-cancel",
            withdrawals=False,
        )

    async def instruments(self) -> list[InstrumentRules]:
        return []

    async def balances(self) -> BalanceSnapshot:
        from datetime import datetime

        payload = self.transport("Balance", {})
        return BalanceSnapshot(
            venue="kraken",
            account_id="live",
            as_of_utc=datetime.now(UTC),
            amounts={key: str(value) for key, value in payload.get("result", {}).items()},
        )

    async def open_orders(self) -> list[OrderSnapshot]:
        return []

    async def fills_since(self, cursor: str | None) -> FillPage:
        return FillPage(fills=[], next_cursor=cursor)

    async def submit(self, intent: AuthorizedOrderIntent) -> SubmitResult:
        if not self.live_enabled or not self.key_present:
            raise LiveDisabled("live trading is not enabled")
        body = {
            "pair": intent.symbol,
            "type": intent.side,
            "ordertype": intent.order_type,
            "volume": str(intent.quantity),
            "cl_ord_id": intent.client_order_id,
        }
        if intent.limit_price is not None:
            body["price"] = str(intent.limit_price)
        if "withdraw" in body or intent.order_type == "withdraw":
            raise LiveDisabled("withdrawals are not a broker operation")
        response = self.transport("AddOrder", body)
        txid = (response.get("result") or {}).get("txid", [None])[0]
        return SubmitResult(status="acknowledged", venue_order_id=txid)

    async def cancel(self, request: CancelRequest) -> CancelResult:
        if not self.live_enabled:
            raise LiveDisabled("live trading is not enabled")
        self.transport("CancelOrder", {"txid": request.venue_order_id, "cl_ord_id": request.client_order_id})
        return CancelResult(status="cancel_pending")

    async def order_status(self, key: OrderLookup) -> OrderLookupResult:
        response = self.transport("QueryOrders", {"cl_ord_id": key.client_order_id})
        result = response.get("result") or {}
        if not result:
            return OrderLookupResult(status="unknown", error="timeout_uncertain", filled_quantity=Decimal("0"))
        order = next(iter(result.values()))
        status = {"open": "open", "closed": "filled", "canceled": "cancelled"}.get(order.get("status"), "unknown")
        return OrderLookupResult(
            status=status,
            filled_quantity=Decimal(str(order.get("vol_exec", "0"))),
            venue_order_id=key.venue_order_id,
        )
