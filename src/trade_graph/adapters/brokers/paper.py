"""Virtual broker on the shared order contract. Fills are assumptions, not venue guarantees."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

from trade_graph.adapters.brokers.matching import plan_fill
from trade_graph.adapters.persistence.db import Database
from trade_graph.contracts.models import (
    AuthorizedOrderIntent,
    BalanceSnapshot,
    BrokerCapabilities,
    CancelRequest,
    CancelResult,
    FillPage,
    FillRecord,
    InstrumentRules,
    Observation,
    OrderLookup,
    OrderLookupResult,
    OrderSnapshot,
    SubmitResult,
)
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import UncertainExternal
from trade_graph.domain.money import canonical_decimal


class PaperBroker:
    def __init__(
        self,
        database: Database,
        clock: Clock,
        *,
        maker_rate: Decimal = Decimal("0.004"),
        taker_rate: Decimal = Decimal("0.008"),
        participation: Decimal = Decimal("1"),
        latency_seconds: int = 0,
    ) -> None:
        self.database = database
        self.clock = clock
        self.maker_rate = maker_rate
        self.taker_rate = taker_rate
        self.participation = participation
        self.latency_seconds = latency_seconds
        self.submit_count = 0

    async def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            venue="paper",
            mode="paper",
            client_id_lookup=True,
            native_amend=False,
            native_stop=True,
            native_stop_tested=True,
            time_in_force=["gtc", "ioc"],
            reduce_only_flag=False,
            fills_pagination=True,
            cancel_behaviour="local-after-reconcile",
            withdrawals=False,
        )

    async def instruments(self) -> list[InstrumentRules]:
        rows = self.database.execute("SELECT document_json FROM instruments WHERE venue = 'paper'").fetchall()
        return [InstrumentRules.model_validate_json(row["document_json"]) for row in rows]

    async def balances(self) -> BalanceSnapshot:
        return BalanceSnapshot(venue="paper", account_id="paper", as_of_utc=self.clock.now(), amounts={})

    async def open_orders(self) -> list[OrderSnapshot]:
        rows = self.database.execute(
            "SELECT client_order_id, venue_order_id, document_json, status FROM broker_orders"
        ).fetchall()
        snapshots = []
        for row in rows:
            if row["status"] not in {"open", "partially_filled"}:
                continue
            document = json.loads(row["document_json"])
            snapshots.append(
                OrderSnapshot(
                    client_order_id=row["client_order_id"],
                    venue_order_id=row["venue_order_id"],
                    symbol=document["symbol"],
                    side=document["side"],
                    status=row["status"],
                    remaining_quantity=Decimal(document["remaining"]),
                )
            )
        return snapshots

    async def fills_since(self, cursor: str | None) -> FillPage:
        rows = self.database.execute(
            "SELECT document_json FROM broker_orders"
        ).fetchall()
        fills: list[FillRecord] = []
        for row in rows:
            document = json.loads(row["document_json"])
            for payload in document.get("emitted", []):
                fills.append(FillRecord.model_validate(payload))
        if cursor:
            fills = [fill for fill in fills if fill.trade_id > cursor]
        return FillPage(fills=fills, next_cursor=fills[-1].trade_id if fills else cursor)

    async def submit(self, intent: AuthorizedOrderIntent) -> SubmitResult:
        self.submit_count += 1
        venue_order_id = f"paper-{intent.client_order_id[:8]}"
        document = {
            "symbol": intent.symbol,
            "side": intent.side,
            "order_type": intent.order_type,
            "quantity": canonical_decimal(intent.quantity),
            "remaining": canonical_decimal(intent.quantity),
            "limit_price": canonical_decimal(intent.limit_price) if intent.limit_price is not None else None,
            "stop_price": canonical_decimal(intent.stop_price) if intent.stop_price is not None else None,
            "eligible_after": utc_iso(intent.eligible_after_utc),
            "excluded_observation_id": intent.excluded_observation_id,
            "emitted": [],
            "intent_id": intent.intent_id,
            "account_id": intent.account_id,
        }
        now = utc_iso(self.clock.now())
        with self.database.immediate() as conn:
            conn.execute(
                """INSERT INTO broker_orders
                (client_order_id, venue_order_id, status, document_json, updated_at)
                VALUES (?, ?, 'open', ?, ?)""",
                (intent.client_order_id, venue_order_id, json.dumps(document), now),
            )
        return SubmitResult(status="acknowledged", venue_order_id=venue_order_id)

    async def cancel(self, request: CancelRequest) -> CancelResult:
        row = self.database.execute(
            "SELECT status, document_json FROM broker_orders WHERE client_order_id = ?",
            (request.client_order_id,),
        ).fetchone()
        if row is None:
            return CancelResult(status="uncertain", error="timeout_uncertain", message="not visible yet")
        if row["status"] == "filled":
            return CancelResult(status="filled")
        with self.database.immediate() as conn:
            conn.execute(
                "UPDATE broker_orders SET status = 'cancelled', updated_at = ? WHERE client_order_id = ?",
                (utc_iso(self.clock.now()), request.client_order_id),
            )
        return CancelResult(status="cancelled")

    async def order_status(self, key: OrderLookup) -> OrderLookupResult:
        row = self.database.execute(
            "SELECT * FROM broker_orders WHERE client_order_id = ?",
            (key.client_order_id,),
        ).fetchone()
        if row is None:
            return OrderLookupResult(status="not_found", filled_quantity=Decimal("0"))
        document = json.loads(row["document_json"])
        filled = Decimal(document["quantity"]) - Decimal(document["remaining"])
        status = row["status"]
        mapped = {
            "open": "open",
            "partially_filled": "partially_filled",
            "filled": "filled",
            "cancelled": "cancelled",
        }.get(status, "unknown")
        return OrderLookupResult(
            status=mapped, filled_quantity=filled, venue_order_id=row["venue_order_id"]
        )

    def match(self, observation: Observation) -> list[FillRecord]:
        rows = self.database.execute(
            "SELECT client_order_id, document_json, status FROM broker_orders"
        ).fetchall()
        emitted: list[FillRecord] = []
        for row in rows:
            if row["status"] not in {"open", "partially_filled"}:
                continue
            document = json.loads(row["document_json"])
            planned = plan_fill(
                document,
                observation,
                participation=self.participation,
                maker_rate=self.maker_rate,
                taker_rate=self.taker_rate,
            )
            if planned is None:
                continue
            quantity, price, fee, liquidity, heuristic = planned
            sequence = len(document["emitted"]) + 1
            fill = FillRecord(
                venue="paper",
                account_id=document["account_id"],
                trade_id=f"{row['client_order_id']}:{sequence}",
                intent_id=document["intent_id"],
                symbol=document["symbol"],
                side=document["side"],
                quantity=quantity,
                price=price,
                fee_amount=fee,
                fee_asset=document["symbol"].split("/")[1],
                liquidity=liquidity,
                filled_at_utc=observation.available_at_utc,
                heuristic=heuristic,
                reference_mid=(
                    (observation.bid + observation.ask) / 2
                    if observation.bid is not None and observation.ask is not None
                    else None
                ),
            )
            document["remaining"] = canonical_decimal(Decimal(document["remaining"]) - quantity)
            document["emitted"].append(json.loads(fill.model_dump_json()))
            status = "filled" if Decimal(document["remaining"]) == 0 else "partially_filled"
            with self.database.immediate() as conn:
                conn.execute(
                    "UPDATE broker_orders SET status = ?, document_json = ?, updated_at = ? WHERE client_order_id = ?",
                    (status, json.dumps(document), utc_iso(self.clock.now()), row["client_order_id"]),
                )
            emitted.append(fill)
        return emitted


class DropAckBroker:
    """Accepts the order, then loses the acknowledgement."""

    def __init__(self, inner: PaperBroker) -> None:
        self.inner = inner

    async def capabilities(self):
        return await self.inner.capabilities()

    async def instruments(self):
        return await self.inner.instruments()

    async def balances(self):
        return await self.inner.balances()

    async def open_orders(self):
        return await self.inner.open_orders()

    async def fills_since(self, cursor):
        return await self.inner.fills_since(cursor)

    async def submit(self, intent: AuthorizedOrderIntent) -> SubmitResult:
        await self.inner.submit(intent)
        raise UncertainExternal("acknowledgement lost")

    async def cancel(self, request: CancelRequest) -> CancelResult:
        return await self.inner.cancel(request)

    async def order_status(self, key: OrderLookup) -> OrderLookupResult:
        return await self.inner.order_status(key)

    def match(self, observation: Observation) -> list[FillRecord]:
        return self.inner.match(observation)


def floor_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    if increment <= 0:
        raise ValueError("increment")
    steps = (value / increment).to_integral_value(rounding="ROUND_FLOOR")
    return steps * increment


def new_client_id() -> str:
    return uuid.uuid4().hex
