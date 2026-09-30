"""Intent, outbox, reservation and reconciliation. Unknown is not rejection."""

from __future__ import annotations

import json
import uuid
from decimal import ROUND_CEILING, Decimal

from trade_graph.adapters.brokers.paper import floor_to_increment, new_client_id
from trade_graph.adapters.persistence.db import Database, atomic
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import (
    AuthorizedOrderIntent,
    Decision,
    FillRecord,
    InstrumentRules,
    Observation,
    OrderLookup,
    PauseProfile,
)
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import (
    AuthorityDenied,
    DuplicateRecord,
    StaleState,
    UncertainExternal,
    ValidationFailure,
)
from trade_graph.domain.money import canonical_decimal
from trade_graph.kernel.authority import pause_allows_increase, pause_allows_reduction

TAKER = Decimal("0.008")


class Execution:
    def __init__(self, database: Database, ledger: Ledger, clock: Clock, broker) -> None:
        self.database = database
        self.ledger = ledger
        self.clock = clock
        self.broker = broker

    def now(self) -> str:
        return utc_iso(self.clock.now())

    def register_instrument(self, rules: InstrumentRules) -> None:
        with self.database.immediate() as conn:
            conn.execute(
                """INSERT INTO instruments (venue, symbol, document_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(venue, symbol) DO UPDATE SET
                    document_json=excluded.document_json,
                    updated_at=excluded.updated_at""",
                (rules.venue, rules.symbol, rules.model_dump_json(), self.now()),
            )

    def instrument(self, venue: str, symbol: str) -> InstrumentRules:
        row = self.database.execute(
            "SELECT document_json FROM instruments WHERE venue = ? AND symbol = ?",
            (venue, symbol),
        ).fetchone()
        if row is None:
            raise ValidationFailure(f"unknown instrument {symbol}")
        return InstrumentRules.model_validate_json(row["document_json"])

    @atomic
    def set_pause(self, portfolio_id: str, profile: PauseProfile, originator: str, reason: str) -> None:
        profiles = {"RUNNING", "PAUSE_DECISIONS", "NO_NEW_EXPOSURE", "MANAGE_ONLY", "CANCEL_ALL", "FLATTEN", "STOPPED"}
        if profile not in profiles or originator not in {"owner", "leader", "system"}:
            raise ValidationFailure("invalid pause profile or originator")
        current = self.pause(portfolio_id)
        if current and current["originator"] == "owner" and current["profile"] != "RUNNING" and originator != "owner":
            # Preserve the owner latch through intermediate pause transitions.
            raise AuthorityDenied("only the owner may replace an owner halt")
        achieved = "requested"
        if profile == "STOPPED":
            achieved = "blocked-until-flat"
        with self.database.immediate() as conn:
            conn.execute(
                """INSERT INTO pause_states
                (portfolio_id, profile, originator, reason, scope, requested_at, achieved, details_json)
                VALUES (?, ?, ?, ?, 'portfolio', ?, ?, '{}')
                ON CONFLICT(portfolio_id) DO UPDATE SET
                  profile=excluded.profile, originator=excluded.originator, reason=excluded.reason,
                  requested_at=excluded.requested_at, achieved=excluded.achieved""",
                (portfolio_id, profile, originator, reason, self.now(), achieved),
            )

    def pause(self, portfolio_id: str) -> dict | None:
        row = self.database.execute(
            "SELECT * FROM pause_states WHERE portfolio_id = ?",
            (portfolio_id,),
        ).fetchone()
        return dict(row) if row else None

    def profile(self, portfolio_id: str) -> str:
        current = self.pause(portfolio_id)
        return current["profile"] if current else "RUNNING"

    def save_observation(self, observation: Observation) -> None:
        with self.database.immediate() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO observations
                (observation_id, venue, symbol, event_time, available_at, document_json, sequence)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    observation.observation_id,
                    observation.venue,
                    observation.symbol,
                    utc_iso(observation.event_time_utc),
                    utc_iso(observation.available_at_utc),
                    observation.model_dump_json(),
                    0,
                ),
            )

    def latest_observation(self, symbol: str, as_of: str, venue: str | None = None) -> Observation | None:
        row = self.database.execute(
            """SELECT document_json FROM observations
            WHERE symbol = ? AND available_at <= ? AND event_time <= ?
              AND (? IS NULL OR venue = ?)
            ORDER BY event_time DESC, available_at DESC, rowid DESC LIMIT 1""",
            (symbol, as_of, as_of, venue, venue),
        ).fetchone()
        if row is None:
            return None
        return Observation.model_validate_json(row["document_json"])

    def quote_fresh(self, symbol: str, max_age_seconds: int, venue: str | None = None) -> bool:
        latest = self.latest_observation(symbol, self.now(), venue)
        if latest is None:
            return False
        age = self.clock.now() - latest.event_time_utc
        return 0 <= age.total_seconds() <= max_age_seconds

    def observations_available(self, symbol: str, as_of: str) -> list[Observation]:
        rows = self.database.execute(
            """SELECT document_json FROM observations
            WHERE symbol = ? AND available_at <= ? ORDER BY available_at""",
            (symbol, as_of),
        ).fetchall()
        return [Observation.model_validate_json(row["document_json"]) for row in rows]

    @atomic
    def authorize(
        self,
        portfolio_id: str,
        decision: Decision,
        *,
        venue: str,
        account_id: str,
        mode: str,
        max_quote_age_seconds: int,
        gross_cap: Decimal,
        asset_cap: Decimal,
    ) -> str:
        if decision.action not in {"enter", "exit"}:
            raise ValidationFailure("authorize requires enter/exit; resize and adjustment need explicit semantics")
        portfolio = self.database.execute(
            "SELECT mode FROM portfolios WHERE portfolio_id = ?", (portfolio_id,)
        ).fetchone()
        if portfolio is None or decision.portfolio_id != portfolio_id:
            raise AuthorityDenied("decision portfolio mismatch")
        if portfolio["mode"] != mode or decision.mode != mode:
            raise AuthorityDenied("decision mode mismatch")
        if not (Decimal("0") < asset_cap <= gross_cap <= Decimal("1")):
            raise ValidationFailure("spot exposure caps must satisfy 0 < asset <= gross <= 1")
        if decision.symbol is None or decision.quantity is None:
            raise ValidationFailure("order needs a symbol and quantity")
        profile = self.profile(portfolio_id)
        increase = decision.action == "enter"
        side = "buy" if increase else "sell"
        if increase and not pause_allows_increase(profile):  # type: ignore[arg-type]
            raise AuthorityDenied(f"pause {profile} blocks new exposure")
        if not increase and not pause_allows_reduction(profile):  # type: ignore[arg-type]
            raise AuthorityDenied(f"pause {profile} blocks reduction")
        if increase and not self.quote_fresh(decision.symbol, max_quote_age_seconds, venue):
            raise StaleState("stale or missing quote")
        rules = self.instrument(venue, decision.symbol)
        if decision.quantity.asset != rules.base_asset:
            raise ValidationFailure("quantity asset does not match instrument")
        for value in (decision.limit_price, decision.stop_price):
            if value is not None and (value.currency != rules.quote_asset or value.amount <= 0):
                raise ValidationFailure("price must be positive and in the instrument quote currency")
        quantity = floor_to_increment(decision.quantity.amount, rules.quantity_increment)
        if quantity <= 0 or quantity > decision.quantity.amount:
            raise ValidationFailure("rounding would exceed authorization")
        price = (decision.limit_price.amount if decision.limit_price
                 else self._reference_price(decision.symbol, side, venue))
        if price is None or price <= 0:
            raise StaleState("no positive reference price")
        if decision.limit_price:
            if side == "buy":
                price = floor_to_increment(price, rules.price_increment)
            else:
                units = (price / rules.price_increment).to_integral_value(rounding=ROUND_CEILING)
                price = units * rules.price_increment
        notional = price * quantity
        if quantity < rules.min_quantity or notional < rules.min_notional:
            raise ValidationFailure("below venue minimum; size was not increased")
        books = self.ledger.books(portfolio_id)
        base, quote = rules.base_asset, rules.quote_asset
        equity = self.ledger.equity(portfolio_id)
        if increase:
            if equity.equity is None or equity.provisional:
                raise StaleState("provisional valuation")
            if equity.equity <= 0:
                raise AuthorityDenied("no equity")
            def reporting(amount: Decimal) -> Decimal:
                return self.ledger.reporting_value(portfolio_id, amount, quote)
            pending_gross, pending_asset = self._pending_exposure(portfolio_id, base)
            proposed = reporting(notional)
            projected_asset = reporting(self._position_value(books, decision.symbol, price)) + pending_asset + proposed
            projected_gross = (equity.inventory_reporting or Decimal("0")) + pending_gross + proposed
            if projected_asset / equity.equity > asset_cap:
                raise AuthorityDenied("single asset exposure")
            if projected_gross / equity.equity > gross_cap:
                raise AuthorityDenied("gross exposure")
        fee_reserve = notional * TAKER
        if side == "buy":
            need_asset, need_amount = quote, notional + fee_reserve
            available = books.cash_amount(quote) - self._reserved(portfolio_id, quote)
        else:
            owned = self._owned(books, base)
            need_asset, need_amount = base, quantity
            available = owned - self._reserved(portfolio_id, base)
        if need_amount > available:
            raise ValidationFailure("insufficient available balance")
        intent_id = str(uuid.uuid4())
        client_order_id = new_client_id()
        order_type = "limit" if decision.limit_price else "market"
        intent = AuthorizedOrderIntent(
            intent_id=intent_id,
            portfolio_id=portfolio_id,
            account_id=account_id,
            venue=venue,
            mode=mode,  # type: ignore[arg-type]
            client_order_id=client_order_id,
            symbol=decision.symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            limit_price=price if order_type == "limit" else None,
            stop_price=decision.stop_price.amount if decision.stop_price else None,
            decision_id=decision.record_id,
            snapshot_id=decision.snapshot_id,
            eligible_after_utc=self.clock.now(),
            excluded_observation_id=(
                decision.snapshot_id.removeprefix("obs:")
                if decision.snapshot_id.startswith("obs:")
                else None
            ),
            reduce_only=side == "sell",
        )
        payload = json.loads(intent.model_dump_json())
        payload["reserve_asset"] = need_asset
        payload["reserve_amount"] = canonical_decimal(need_amount)
        now = self.now()
        with self.database.immediate() as conn:
            conn.execute(
                """INSERT INTO order_intents
                (intent_id, portfolio_id, client_order_id, state, symbol, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, 'SUBMISSION_PENDING', ?, ?, ?, ?)""",
                (intent_id, portfolio_id, client_order_id, decision.symbol, json.dumps(payload), now, now),
            )
            conn.execute(
                """INSERT INTO position_reservations
                (reservation_id, portfolio_id, intent_id, asset, amount, state, created_at)
                VALUES (?, ?, ?, ?, ?, 'held', ?)""",
                (str(uuid.uuid4()), portfolio_id, intent_id, need_asset, canonical_decimal(need_amount), now),
            )
            conn.execute(
                """INSERT INTO outbox (outbox_id, portfolio_id, kind, payload_ref, payload_json, status, created_at)
                VALUES (?, ?, 'submit', ?, ?, 'pending', ?)""",
                (str(uuid.uuid4()), portfolio_id, intent_id, json.dumps({"intent_id": intent_id}), now),
            )
            conn.execute(
                """INSERT INTO decisions
                (decision_id, portfolio_id, action, payload_json, mandate_revision, policy_revision,
                 snapshot_id, system_version_id, created_at, task_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision.record_id,
                    portfolio_id,
                    decision.action,
                    decision.model_dump_json(),
                    decision.mandate_revision,
                    decision.policy_revision,
                    decision.snapshot_id,
                    decision.system_version_id,
                    now,
                    decision.task_id,
                ),
            )
        return intent_id

    async def dispatch(self) -> int:
        rows = self.database.execute(
            """SELECT payload_ref FROM outbox WHERE kind = 'submit' AND status = 'pending'"""
        ).fetchall()
        sent = 0
        for row in rows:
            intent_id = row["payload_ref"]
            if not self._mark_submitting(intent_id):
                continue
            intent = self._intent_model(intent_id)
            try:
                result = await self.broker.submit(intent)
            except UncertainExternal:
                self._set_state(intent_id, "UNKNOWN", {"error": "timeout"})
                continue
            if result.status == "acknowledged":
                self._set_state(intent_id, "OPEN", {"venue_order_id": result.venue_order_id})
            elif result.status == "uncertain":
                self._set_state(intent_id, "UNKNOWN", {"error": result.error})
            else:
                self._set_state(intent_id, "REJECTED", {"error": result.error})
                self._release(intent_id)
            sent += 1
        return sent

    def on_observation(self, observation: Observation) -> list[str]:
        self.save_observation(observation)
        fills = self.broker.match(observation) if hasattr(self.broker, "match") else []
        recorded = []
        for fill in fills:
            if self.record_fill(fill):
                recorded.append(fill.trade_id)
        return recorded

    @atomic
    def record_fill(self, fill: FillRecord) -> bool:
        existing = self.database.execute(
            "SELECT document_json FROM fills WHERE venue = ? AND account_id = ? AND trade_id = ?",
            (fill.venue, fill.account_id, fill.trade_id),
        ).fetchone()
        if existing is not None:
            if existing["document_json"] != fill.model_dump_json():
                self._incident("fill_discrepancy", {"trade_id": fill.trade_id})
            return False
        base, quote = fill.symbol.split("/")
        portfolio_id = self._portfolio_for_intent(fill.intent_id) if fill.intent_id else None
        if portfolio_id is None:
            raise ValidationFailure("fill without portfolio")
        intent = self._intent_model(fill.intent_id)
        if (fill.venue, fill.account_id, fill.symbol, fill.side) != (
            intent.venue, intent.account_id, intent.symbol, intent.side
        ):
            raise ValidationFailure("fill does not match its authorized intent")
        if self._filled_quantity(fill.intent_id) + fill.quantity > intent.quantity:
            raise ValidationFailure("fill exceeds authorized quantity")
        try:
            self.ledger.apply_fill(portfolio_id, fill, base_asset=base, quote_asset=quote)
        except DuplicateRecord:
            ref = f"{fill.venue}:{fill.account_id}:{fill.trade_id}"
            prior = self.database.execute(
                "SELECT payload_json FROM ledger_events WHERE portfolio_id = ? AND external_ref = ? AND kind = 'fill'",
                (portfolio_id, ref),
            ).fetchone()
            if prior is None or FillRecord.model_validate(json.loads(prior["payload_json"])["fill"]) != fill:
                raise ValidationFailure("fill conflicts with existing ledger event") from None
        with self.database.immediate() as conn:
            conn.execute(
                """INSERT INTO fills
                (fill_id, venue, account_id, trade_id, portfolio_id, intent_id, document_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    fill.venue,
                    fill.account_id,
                    fill.trade_id,
                    portfolio_id,
                    fill.intent_id,
                    fill.model_dump_json(),
                    self.now(),
                ),
            )
        if fill.intent_id:
            self._consume_reservation(fill)
            self._refresh_order_state(fill.intent_id)
        return True

    async def reconcile(self) -> None:
        rows = self.database.execute(
            """SELECT intent_id, payload_json, state FROM order_intents
            WHERE state IN ('UNKNOWN', 'SUBMITTING', 'OPEN', 'PARTIALLY_FILLED', 'CANCEL_PENDING')"""
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            if row["state"] == "SUBMITTING":
                self._set_state(row["intent_id"], "UNKNOWN", {})
            status = await self.broker.order_status(
                OrderLookup(client_order_id=payload["client_order_id"], symbol=payload["symbol"])
            )
            cursor = None
            seen_cursors: set[str] = set()
            while True:
                page = await self.broker.fills_since(cursor)
                for fill in page.fills:
                    if fill.intent_id == row["intent_id"]:
                        self.record_fill(fill)
                if page.next_cursor is None:
                    break
                if page.next_cursor in seen_cursors:
                    raise UncertainExternal("fill pagination did not advance")
                seen_cursors.add(page.next_cursor)
                cursor = page.next_cursor
            recorded = self._filled_quantity(row["intent_id"])
            expected = Decimal(payload["quantity"]) if status.status == "filled" else status.filled_quantity
            if status.status in {"filled", "cancelled"} and recorded != expected:
                self._set_state(row["intent_id"], "UNKNOWN", {})
                self._incident("terminal_order_missing_fills", {"intent_id": row["intent_id"]})
                continue
            if status.status == "not_found" and row["state"] in {"SUBMITTING", "UNKNOWN"}:
                self._incident("unknown_order_not_visible", {"intent_id": row["intent_id"]})
                continue
            if status.status == "filled":
                self._set_state(row["intent_id"], "FILLED", {})
                self._release(row["intent_id"])
            elif status.status == "cancelled":
                self._set_state(row["intent_id"], "CANCELLED", {})
                self._release(row["intent_id"])

    async def startup(self) -> None:
        await self.reconcile()
        await self.dispatch()

    async def cancel(self, intent_id: str) -> None:
        payload = self._payload(intent_id)
        self._set_state(intent_id, "CANCEL_PENDING", {})
        await self.broker.cancel(
            __import__("trade_graph.contracts.models", fromlist=["CancelRequest"]).CancelRequest(
                intent_id=intent_id,
                client_order_id=payload["client_order_id"],
                venue_order_id=payload.get("venue_order_id"),
                symbol=payload["symbol"],
            )
        )
        await self.reconcile()

    async def replace(self, intent_id: str, decision: Decision, **authorize_kwargs) -> str:
        await self.cancel(intent_id)
        if self.intent_state(intent_id) not in {"CANCELLED", "FILLED"}:
            raise UncertainExternal("replacement requires reconciled cancellation of the original")
        payload = self._payload(intent_id)
        filled = self._filled_quantity(intent_id)
        remaining = Decimal(payload["quantity"]) - filled
        if decision.quantity is None or decision.quantity.amount > remaining:
            raise ValidationFailure("replacement exceeds remaining quantity")
        return self.authorize(payload["portfolio_id"], decision, **authorize_kwargs)

    @atomic
    def place_protection(
        self,
        portfolio_id: str,
        symbol: str,
        quantity: Decimal,
        stop_price: Decimal,
        snapshot_id: str,
    ) -> str:
        if not self.quote_fresh(symbol, 10**9):
            raise StaleState("no quote to invent a protective price from")
        intent_id = str(uuid.uuid4())
        client_order_id = new_client_id()
        rules = self.instrument("paper", symbol)
        if quantity <= 0 or stop_price <= 0:
            raise ValidationFailure("protective quantity and stop must be positive")
        if quantity != floor_to_increment(quantity, rules.quantity_increment):
            raise ValidationFailure("protective quantity precision")
        available = self.owned_quantity(portfolio_id, rules.base_asset) - self._reserved(portfolio_id, rules.base_asset)
        if quantity > available:
            raise ValidationFailure("protective order exceeds unreserved inventory")
        intent = AuthorizedOrderIntent(
            intent_id=intent_id,
            portfolio_id=portfolio_id,
            account_id="paper",
            venue="paper",
            mode="paper",
            client_order_id=client_order_id,
            symbol=symbol,
            side="sell",
            order_type="stop",
            quantity=quantity,
            stop_price=stop_price,
            snapshot_id=snapshot_id,
            eligible_after_utc=self.clock.now(),
            reduce_only=True,
        )
        payload = json.loads(intent.model_dump_json())
        payload["reserve_asset"] = rules.base_asset
        payload["reserve_amount"] = canonical_decimal(quantity)
        now = self.now()
        with self.database.immediate() as conn:
            conn.execute(
                """INSERT INTO order_intents
                (intent_id, portfolio_id, client_order_id, state, symbol, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, 'SUBMISSION_PENDING', ?, ?, ?, ?)""",
                (intent_id, portfolio_id, client_order_id, symbol, json.dumps(payload), now, now),
            )
            conn.execute(
                """INSERT INTO position_reservations
                (reservation_id, portfolio_id, intent_id, asset, amount, state, created_at)
                VALUES (?, ?, ?, ?, ?, 'held', ?)""",
                (str(uuid.uuid4()), portfolio_id, intent_id, rules.base_asset, canonical_decimal(quantity), now),
            )
            conn.execute(
                """INSERT INTO outbox (outbox_id, portfolio_id, kind, payload_ref, payload_json, status, created_at)
                VALUES (?, ?, 'submit', ?, ?, 'pending', ?)""",
                (str(uuid.uuid4()), portfolio_id, intent_id, "{}", now),
            )
        return intent_id

    def owned_quantity(self, portfolio_id: str, asset: str) -> Decimal:
        return self._owned(self.ledger.books(portfolio_id), asset)

    def record_non_order(self, portfolio_id: str, decision: Decision) -> None:
        if decision.action not in {"hold", "no_action"}:
            raise ValidationFailure("only non-order decisions are recorded here")
        with self.database.immediate() as conn:
            conn.execute(
                """INSERT INTO decisions
                (decision_id, portfolio_id, action, payload_json, mandate_revision, policy_revision,
                 snapshot_id, system_version_id, created_at, task_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision.record_id,
                    portfolio_id,
                    decision.action,
                    decision.model_dump_json(),
                    decision.mandate_revision,
                    decision.policy_revision,
                    decision.snapshot_id,
                    decision.system_version_id,
                    self.now(),
                    decision.task_id,
                ),
            )

    def intent_state(self, intent_id: str) -> str:
        row = self.database.execute(
            "SELECT state FROM order_intents WHERE intent_id = ?",
            (intent_id,),
        ).fetchone()
        return row["state"]

    @atomic
    def _mark_submitting(self, intent_id: str) -> bool:
        intent = self._intent_model(intent_id)
        profile = self.profile(intent.portfolio_id)
        allowed = pause_allows_reduction(profile) if intent.side == "sell" else pause_allows_increase(profile)
        if not allowed:
            return False
        now = self.now()
        with self.database.immediate() as conn:
            cursor = conn.execute(
                """UPDATE order_intents SET state = 'SUBMITTING', updated_at = ?
                WHERE intent_id = ? AND state = 'SUBMISSION_PENDING'""",
                (now, intent_id),
            )
            if cursor.rowcount != 1:
                return False
            conn.execute(
                """UPDATE outbox SET status = 'started' WHERE kind = 'submit' AND payload_ref = ?""",
                (intent_id,),
            )
            conn.execute(
                """INSERT INTO order_attempts (attempt_id, intent_id, kind, created_at, result_json)
                VALUES (?, ?, 'submit', ?, '{}')""",
                (str(uuid.uuid4()), intent_id, now),
            )
        return True

    def _set_state(self, intent_id: str, state: str, extra: dict) -> None:
        payload = self._payload(intent_id)
        payload.update(extra)
        with self.database.immediate() as conn:
            conn.execute(
                "UPDATE order_intents SET state = ?, payload_json = ?, updated_at = ? WHERE intent_id = ?",
                (state, json.dumps(payload), self.now(), intent_id),
            )

    def _payload(self, intent_id: str) -> dict:
        row = self.database.execute(
            "SELECT payload_json FROM order_intents WHERE intent_id = ?",
            (intent_id,),
        ).fetchone()
        return json.loads(row["payload_json"])

    def _intent_model(self, intent_id: str) -> AuthorizedOrderIntent:
        payload = self._payload(intent_id)
        allowed = set(AuthorizedOrderIntent.model_fields)
        return AuthorizedOrderIntent.model_validate({key: payload[key] for key in allowed if key in payload})

    def _release(self, intent_id: str) -> None:
        with self.database.immediate() as conn:
            conn.execute(
                "UPDATE position_reservations SET state = 'released' WHERE intent_id = ? AND state = 'held'",
                (intent_id,),
            )

    def _reserved(self, portfolio_id: str, asset: str) -> Decimal:
        rows = self.database.execute(
            """SELECT amount FROM position_reservations
            WHERE portfolio_id = ? AND asset = ? AND state = 'held'""",
            (portfolio_id, asset),
        ).fetchall()
        return sum((Decimal(row["amount"]) for row in rows), Decimal("0"))

    def _owned(self, books, asset: str) -> Decimal:
        return sum((lot.open_quantity() for lot in books.lots if lot.asset == asset), Decimal("0"))

    def _pending_exposure(self, portfolio_id: str, asset: str) -> tuple[Decimal, Decimal]:
        gross = single = Decimal("0")
        rows = self.database.execute(
            """SELECT intent_id, payload_json FROM order_intents WHERE portfolio_id = ?
            AND state NOT IN ('FILLED', 'CANCELLED', 'REJECTED')""", (portfolio_id,)
        ).fetchall()
        for row in rows:
            intent = json.loads(row["payload_json"])
            if intent["side"] != "buy":
                continue
            rules = self.instrument(intent["venue"], intent["symbol"])
            remaining = max(Decimal("0"), Decimal(intent["quantity"]) - self._filled_quantity(row["intent_id"]))
            if not remaining:
                continue
            price = self._reference_price(intent["symbol"], "buy", intent["venue"])
            if intent.get("limit_price") is not None:
                price = max(price or Decimal("0"), Decimal(intent["limit_price"]))
            if price is None:
                raise StaleState("pending order has no price for exposure valuation")
            value = self.ledger.reporting_value(portfolio_id, remaining * price, rules.quote_asset)
            gross += value
            if rules.base_asset == asset:
                single += value
        return gross, single

    def _position_value(self, books, symbol: str, price: Decimal) -> Decimal:
        base = symbol.split("/")[0]
        return self._owned(books, base) * price

    def _reference_price(self, symbol: str, side: str, venue: str | None = None) -> Decimal | None:
        latest = self.latest_observation(symbol, self.now(), venue)
        if latest is None:
            return None
        return latest.ask if side == "buy" else latest.bid

    def _portfolio_for_intent(self, intent_id: str) -> str:
        row = self.database.execute(
            "SELECT portfolio_id FROM order_intents WHERE intent_id = ?",
            (intent_id,),
        ).fetchone()
        if row is None:
            raise ValidationFailure("unknown fill intent")
        return row["portfolio_id"]

    def _consume_reservation(self, fill: FillRecord) -> None:
        if fill.intent_id is None:
            return
        payload = self._payload(fill.intent_id)
        asset = payload.get("reserve_asset")
        if asset is None:
            return
        if fill.side == "buy":
            used = fill.price * fill.quantity
        else:
            used = fill.quantity
        if fill.fee_asset == asset:
            used += fill.fee_amount
        row = self.database.execute(
            "SELECT reservation_id, amount FROM position_reservations WHERE intent_id = ? AND state = 'held'",
            (fill.intent_id,),
        ).fetchone()
        if row is None:
            return
        remaining = Decimal(row["amount"]) - used
        state = "released" if remaining <= 0 else "held"
        with self.database.immediate() as conn:
            conn.execute(
                "UPDATE position_reservations SET amount = ?, state = ? WHERE reservation_id = ?",
                (canonical_decimal(max(remaining, Decimal("0"))), state, row["reservation_id"]),
            )

    def _refresh_order_state(self, intent_id: str) -> None:
        payload = self._payload(intent_id)
        recorded = self._filled_quantity(intent_id)
        if recorded == Decimal(payload["quantity"]):
            self._set_state(intent_id, "FILLED", {})
            self._release(intent_id)
        elif recorded > 0:
            self._set_state(intent_id, "PARTIALLY_FILLED", {})

    def _filled_quantity(self, intent_id: str) -> Decimal:
        rows = self.database.execute(
            "SELECT document_json FROM fills WHERE intent_id = ?",
            (intent_id,),
        ).fetchall()
        return sum((FillRecord.model_validate_json(row["document_json"]).quantity for row in rows), Decimal("0"))

    def _incident(self, kind: str, payload: dict) -> None:
        self.ledger._activity(None, kind, payload)
