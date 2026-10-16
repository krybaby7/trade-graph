"""Virtual broker on the shared order contract. Fills are assumptions, not venue guarantees."""

from __future__ import annotations

import json
from decimal import Decimal

from trade_graph.adapters.persistence.db import Database
from trade_graph.contracts.fills import CostProfile, FillPolicy, ProtectiveExit, SimFill, SimOrder
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
from trade_graph.domain.clock import Clock, parse_utc, utc_iso
from trade_graph.domain.errors import UncertainExternal, ValidationFailure
from trade_graph.domain.money import canonical_decimal
from trade_graph.domain.precision import floor_to_increment, new_client_id
from trade_graph.kernel.cost_profiles import KRAKEN_STARTING, load_cost_profiles
from trade_graph.kernel.fills import PAPER_QUOTE_POLICY, fill_order_on_observation, trigger_exit_on_observation

_DEFAULT_MAKER = Decimal("0.004")
_DEFAULT_TAKER = Decimal("0.008")


class PaperBroker:
    """Fills come from the shared fill engine (kernel/fills.py, T28) under a dated cost profile.

    The default profile is Kraken's starting spot tier (the D14 rates as a dated record). Explicit
    ``maker_rate``/``taker_rate`` remain supported and are labelled as explicit configuration.
    """

    def __init__(
        self,
        database: Database,
        clock: Clock,
        *,
        maker_rate: Decimal | None = None,
        taker_rate: Decimal | None = None,
        cost_profile: CostProfile | None = None,
        fill_policy: FillPolicy | None = None,
        participation: Decimal | None = None,
        latency_seconds: int = 0,
    ) -> None:
        if cost_profile is not None and (maker_rate is not None or taker_rate is not None):
            raise ValidationFailure("configure either a cost profile or explicit fee rates, not both")
        if fill_policy is not None and participation is not None:
            raise ValidationFailure("configure either a fill policy or a participation share, not both")
        if cost_profile is None:
            if maker_rate is None and taker_rate is None:
                cost_profile = load_cost_profiles().get(KRAKEN_STARTING)
            else:
                cost_profile = CostProfile.explicit(
                    maker_rate=_DEFAULT_MAKER if maker_rate is None else maker_rate,
                    taker_rate=_DEFAULT_TAKER if taker_rate is None else taker_rate,
                    on=clock.now().date(),
                )
        policy = fill_policy or PAPER_QUOTE_POLICY
        if participation is not None and participation != policy.participation:
            policy = FillPolicy.model_validate(
                policy.model_dump() | {"policy_id": "paper-quote-configured", "participation": participation})
        if policy.entry_slippage_rate != 0:
            raise ValidationFailure(
                "paper buy reservations are sized at the quote, so the paper fill policy adds no entry slippage")
        self.database = database
        self.clock = clock
        self.cost_profile = cost_profile
        self.fill_policy = policy
        self.latency_seconds = latency_seconds
        self.submit_count = 0

    @property
    def maker_rate(self) -> Decimal:
        return self.cost_profile.maker_rate

    @maker_rate.setter
    def maker_rate(self, value: Decimal) -> None:
        """A changed fee is a new, explicitly configured profile; fills already made keep theirs."""
        self.cost_profile = CostProfile.explicit(maker_rate=value, taker_rate=self.taker_rate,
                                                 on=self.clock.now().date())

    @property
    def taker_rate(self) -> Decimal:
        return self.cost_profile.taker_rate

    @taker_rate.setter
    def taker_rate(self, value: Decimal) -> None:
        self.cost_profile = CostProfile.explicit(maker_rate=self.maker_rate, taker_rate=value,
                                                 on=self.clock.now().date())

    @property
    def participation(self) -> Decimal:
        return self.fill_policy.participation

    @property
    def fee_reserve_rate(self) -> Decimal:
        """Worst configured fill fee; rebates cannot reduce the funds reserved for an order."""
        return max(Decimal("0"), self.maker_rate, self.taker_rate)

    async def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            venue="paper",
            mode="paper",
            client_id_lookup=True,
            native_amend=False,
            native_stop=True,
            native_stop_tested=True,
            time_in_force=["gtc"],
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
        if not fills:
            return FillPage(fills=[], next_cursor=None)
        return FillPage(fills=fills, next_cursor=fills[-1].trade_id)

    async def submit(self, intent: AuthorizedOrderIntent) -> SubmitResult:
        self.submit_count += 1
        capabilities = await self.capabilities()
        if intent.venue != capabilities.venue or intent.mode != capabilities.mode:
            return SubmitResult(status="rejected", error="rejected", message="paper broker binding mismatch")
        if intent.time_in_force not in capabilities.time_in_force:
            return SubmitResult(status="rejected", error="rejected", message="unsupported time in force")
        if intent.order_type == "stop" and not (capabilities.native_stop and capabilities.native_stop_tested):
            return SubmitResult(status="rejected", error="rejected", message="unsupported or untested native stop")
        if intent.stop_price is not None and intent.order_type != "stop":
            return SubmitResult(status="rejected", error="rejected", message="unsupported combined stop order")
        if intent.order_type == "stop" and intent.side != "sell":
            return SubmitResult(status="rejected", error="rejected", message="unsupported buy stop")
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

    def _plan(self, client_order_id: str, document: dict, observation: Observation) -> SimFill | None:
        """One shared-engine evaluation of a stored paper order against one observation."""
        if observation.symbol != document["symbol"] or Decimal(document["remaining"]) <= 0:
            return None
        common = {
            "symbol": document["symbol"],
            "eligible_after_utc": parse_utc(document["eligible_after"]),
            "excluded_event_ref": (
                f"observation:{document['excluded_observation_id']}"
                if document.get("excluded_observation_id") else None
            ),
        }
        increment = self._price_increment(document["symbol"])
        if document["order_type"] == "stop":
            if document["side"] != "sell":
                return None
            exit_ = ProtectiveExit(exit_id=client_order_id, quantity=Decimal(document["remaining"]),
                                   stop_price=Decimal(document["stop_price"]), **common)
            return trigger_exit_on_observation(exit_, observation, profile=self.cost_profile,
                                               policy=self.fill_policy, price_increment=increment)
        if document["order_type"] not in {"market", "limit"}:
            return None
        order = SimOrder(
            order_id=client_order_id, side=document["side"], kind=document["order_type"],
            quantity=Decimal(document["quantity"]), remaining=Decimal(document["remaining"]),
            limit_price=None if document.get("limit_price") is None else Decimal(document["limit_price"]),
            **common,
        )
        return fill_order_on_observation(order, observation, profile=self.cost_profile, policy=self.fill_policy,
                                         price_increment=increment)

    def _price_increment(self, symbol: str) -> Decimal | None:
        row = self.database.execute(
            "SELECT document_json FROM instruments WHERE venue = 'paper' AND symbol = ?", (symbol,),
        ).fetchone()
        return None if row is None else InstrumentRules.model_validate_json(row["document_json"]).price_increment

    def match(self, observation: Observation) -> list[FillRecord]:
        rows = self.database.execute(
            "SELECT client_order_id, document_json, status FROM broker_orders"
        ).fetchall()
        emitted: list[FillRecord] = []
        for row in rows:
            if row["status"] not in {"open", "partially_filled"}:
                continue
            document = json.loads(row["document_json"])
            planned = self._plan(row["client_order_id"], document, observation)
            if planned is None:
                continue
            sequence = len(document["emitted"]) + 1
            fill = FillRecord(
                venue="paper",
                account_id=document["account_id"],
                trade_id=f"{row['client_order_id']}:{sequence}",
                intent_id=document["intent_id"],
                symbol=document["symbol"],
                side=document["side"],
                quantity=planned.quantity,
                price=planned.price,
                fee_amount=planned.fee,
                fee_asset=planned.fee_asset,
                liquidity=planned.liquidity,
                filled_at_utc=observation.available_at_utc,
                heuristic=planned.heuristic,
                reference_mid=(
                    (observation.bid + observation.ask) / 2
                    if observation.bid is not None and observation.ask is not None
                    else None
                ),
            )
            document["remaining"] = canonical_decimal(Decimal(document["remaining"]) - planned.quantity)
            document["emitted"].append(json.loads(fill.model_dump_json()))
            # The terms each fill was computed under; a later profile or policy change never rewrites them.
            document.setdefault("fill_terms", []).append({
                "trade_id": fill.trade_id, "reason": planned.reason,
                "reference_price": canonical_decimal(planned.reference_price),
                "cost_profile_ref": planned.cost_profile_ref, "cost_profile_sha256": planned.cost_profile_sha256,
                "fill_policy_ref": planned.fill_policy_ref, "fill_policy_sha256": planned.fill_policy_sha256,
            })
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

    @property
    def fee_reserve_rate(self) -> Decimal:
        return self.inner.fee_reserve_rate

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


__all__ = ["DropAckBroker", "PaperBroker", "floor_to_increment", "new_client_id"]
