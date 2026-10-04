"""Intent, outbox, reservation and reconciliation. Unknown is not rejection."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from decimal import ROUND_CEILING, Context, Decimal, Inexact, localcontext

from trade_graph.adapters.persistence.db import Database, atomic
from trade_graph.application.authority import AuthorityRecord
from trade_graph.application.incident_resolution import ProtectedNativeIncidentResolver
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import (
    AuthorizedOrderIntent,
    BrokerCapabilities,
    CancelRequest,
    Decision,
    FillRecord,
    InstrumentRules,
    Mode,
    Observation,
    OrderLookup,
    PauseProfile,
    Quantity,
)
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import (
    AuthorityDenied,
    DuplicateRecord,
    StaleState,
    TradeGraphError,
    UncertainExternal,
    ValidationFailure,
)
from trade_graph.domain.money import canonical_decimal, parse_decimal
from trade_graph.domain.precision import floor_to_increment, new_client_id
from trade_graph.domain.protocols import Broker
from trade_graph.kernel.authority import pause_allows_increase, pause_allows_reduction
from trade_graph.live_pilot import ProtectedPilotLifecycle

TAKER = Decimal("0.008")


def _fill_reservation_debits(fill: FillRecord) -> dict[str, Decimal]:
    """Gross debits; same-fill proceeds can pay quote fees, rebates never refill holds."""
    base, quote = fill.symbol.split("/")
    with localcontext(Context(prec=128)):
        charges = {}
        for fee in fill.fee_legs():
            charges[fee.asset] = charges.get(fee.asset, Decimal("0")) + max(Decimal("0"), fee.amount)
        if fill.side == "buy":
            charges[quote] = fill.quote_principal + charges.get(quote, Decimal("0"))
            charges[base] = max(Decimal("0"), charges.get(base, Decimal("0")) - fill.quantity)
        else:
            charges[base] = fill.quantity + charges.get(base, Decimal("0"))
            charges[quote] = max(Decimal("0"), charges.get(quote, Decimal("0")) - fill.quote_principal)
        return {asset: amount for asset, amount in charges.items() if amount > 0}


class Execution:
    def __init__(
        self,
        database: Database,
        ledger: Ledger,
        clock: Clock,
        broker: Broker,
        *,
        venue: str = "paper",
        account_id: str = "paper",
        mode: Mode = "paper",
        blocks_increase: Callable[[str], bool] | None = None,
        fee_reserve_rate: Decimal = TAKER,
        pilot_lifecycle: ProtectedPilotLifecycle | None = None,
        pilot_authorization_id: str | None = None,
        native_incident_resolver: ProtectedNativeIncidentResolver | None = None,
    ) -> None:
        self.database = database
        self.ledger = ledger
        self.clock = clock
        self.broker = broker
        self.venue = venue
        self.account_id = account_id
        self.mode = mode
        if (pilot_lifecycle is None) != (pilot_authorization_id is None):
            raise ValueError("pilot lifecycle and immutable authorization must be supplied together")
        if pilot_lifecycle is not None and (
            type(pilot_lifecycle) is not ProtectedPilotLifecycle or mode != "live"
            or pilot_lifecycle.database is not database or pilot_lifecycle.clock is not clock
            or pilot_lifecycle.scope.venue != venue or pilot_lifecycle.scope.account_id != account_id
            or type(pilot_authorization_id) is not str or not 1 <= len(pilot_authorization_id) <= 128
        ):
            raise ValueError("exact protected pilot execution binding required")
        self._pilot_lifecycle, self._pilot_authorization_id = pilot_lifecycle, pilot_authorization_id
        self._native_incident_resolver = native_incident_resolver
        if native_incident_resolver is not None and not self._incident_resolver_bound():
            raise ValueError("exact protected native incident execution binding required")
        self.blocks_increase = blocks_increase
        self.fee_reserve_rate = parse_decimal(fee_reserve_rate)
        if self.fee_reserve_rate < 0:
            raise ValidationFailure("fee reserve rate must be nonnegative")
        # A cold live adapter must still support read-only crash reconciliation.
        # Readiness is mandatory before an intent/reservation or submission.
        self._validate_fee_reserve_rate(require_ready=False)
        self.authority = AuthorityRecord(database, clock)

    def now(self) -> str:
        return utc_iso(self.clock.now())

    def _assert_pilot_increase(self, portfolio_id: str, symbol: str | None) -> None:
        if self.mode != "live":
            return
        pilot = self._pilot_lifecycle
        if (type(pilot) is not ProtectedPilotLifecycle or pilot.database is not self.database
                or pilot.clock is not self.clock or pilot.scope.venue != self.venue
                or pilot.scope.account_id != self.account_id or self._pilot_authorization_id is None):
            raise AuthorityDenied("live increases require a concrete protected pilot lifecycle")
        pilot.assert_increase_authority(self._pilot_authorization_id, portfolio_id=portfolio_id, symbol=symbol)

    def _sync_pilot_effect(self, intent_id: str) -> None:
        if self.mode != "live" or self._pilot_lifecycle is None:
            return
        effect = self.database.execute("SELECT effect_id FROM live_pilot_effects "
                                       "WHERE authorization_id=? AND intent_id=?",
                                       (self._pilot_authorization_id, intent_id)).fetchone()
        if effect is not None:
            try:
                self._pilot_lifecycle.reconcile_effect(effect["effect_id"])
            except StaleState:
                # A terminal label is insufficient to release the pilot hold.
                # Keep it until a real later scoped history observation exists.
                pass
            except AuthorityDenied:
                self._pilot_management_incident("reconcile_effect")

    def _pilot_management_incident(self, operation: str) -> None:
        # Fixed codes retain the failed authority boundary without exposing
        # signed documents, private source paths or exception text.
        self._incident("pilot_management_authority_unavailable", {
            "operation": operation, "reason": "pilot_authority_invalid", "mode": "live",
        })

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
    def authorize(self, portfolio_id: str, decision: Decision) -> str:
        if self.mode == "live":
            self._validate_fee_reserve_rate()
        if decision.action not in {"enter", "exit"}:
            raise ValidationFailure("authorize requires enter/exit; resize and adjustment need explicit semantics")
        if decision.action == "enter" and self._reconciliation_blocked():
            raise StaleState("incomplete account reconciliation blocks new exposure")
        if decision.action == "enter":
            self._assert_pilot_increase(portfolio_id, decision.symbol)
        portfolio = self.database.execute(
            "SELECT mode FROM portfolios WHERE portfolio_id = ?", (portfolio_id,)
        ).fetchone()
        if portfolio is None or decision.portfolio_id != portfolio_id:
            raise AuthorityDenied("decision portfolio mismatch")
        if portfolio["mode"] != self.mode:
            raise AuthorityDenied("decision mode mismatch")
        policy, mandate = self.authority.require_for_decision(
            decision, venue=self.venue, mode=self.mode
        )
        gross_cap, asset_cap, max_quote_age_seconds = self.authority.limits(policy, mandate)
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
        venue = self.venue
        if increase and self.blocks_increase is not None and self.blocks_increase(decision.symbol):
            raise StaleState("market feed blocks new exposure")
        if increase and self._uncertain_exposure(decision.symbol):
            raise StaleState("unresolved execution blocks new exposure")
        if increase:
            self._validate_fee_reserve_rate()
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
        if increase:
            self._assert_increase_exposure(
                portfolio_id,
                decision.symbol,
                quantity,
                price,
                quote,
                base,
                gross_cap,
                asset_cap,
            )
        fee_reserve = notional * self.fee_reserve_rate
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
            account_id=self.account_id,
            venue=venue,
            mode=self.mode,
            client_order_id=client_order_id,
            symbol=decision.symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            limit_price=price if order_type == "limit" else None,
            stop_price=decision.stop_price.amount if decision.stop_price else None,
            time_in_force=decision.time_in_force,
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
        payload["policy_revision"] = policy.revision_id
        payload["mandate_revision"] = str(mandate.revision)
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
            if self.mode == "live" and increase:
                # Join the native intent/reservation/outbox transaction. Failure
                # to reserve pilot authority rolls every prospective effect back.
                self._pilot_lifecycle.reserve_increase(self._pilot_authorization_id, intent_id)
        return intent_id

    async def dispatch(self) -> int:
        rows = self.database.execute(
            """SELECT o.payload_ref FROM outbox o JOIN order_intents i ON i.intent_id = o.payload_ref
            WHERE o.kind = 'submit' AND o.status = 'pending'
            AND json_extract(i.payload_json, '$.venue') = ?
            AND json_extract(i.payload_json, '$.account_id') = ?
            AND json_extract(i.payload_json, '$.mode') = ?""",
            (self.venue, self.account_id, self.mode),
        ).fetchall()
        if self.mode == "live":
            eligible = []
            for row in rows:
                intent = self._intent_model(row["payload_ref"])
                if intent.side == "buy":
                    try:
                        self._assert_pilot_increase(intent.portfolio_id, intent.symbol)
                    except (AuthorityDenied, ValueError, TypeError, OSError, ArithmeticError):
                        # Unseen requests stay pending; refusal does not imply
                        # an exchange rejection or resolve an unknown effect.
                        continue
                eligible.append(row)
            rows = eligible
            if not rows:
                return 0
        capabilities = await self._require_broker_binding()
        sent = 0
        for row in rows:
            intent_id = row["payload_ref"]
            if self.intent_state(intent_id) != "SUBMISSION_PENDING":
                continue
            intent = self._intent_model(intent_id)
            unsupported = self._unsupported_feature(intent, capabilities)
            if unsupported is not None:
                self._abandon_unsent(intent_id, "rejected", message=unsupported)
                continue
            if intent.side == "buy" or self.mode == "live":
                try:
                    self._validate_fee_reserve_rate()
                except ValidationFailure as exc:
                    self._abandon_unsent(intent_id, "rejected", message=str(exc))
                    continue
            if intent.side == "buy":
                block = self._unsent_increase_block(intent)
                if block == "reject":
                    self._abandon_unsent(intent_id, "authority")
                    continue
                if block == "hold":
                    continue
            try:
                started = self._mark_submitting(intent_id)
            except AuthorityDenied:
                if self.mode != "live" or intent.side != "buy":
                    raise
                self._pilot_management_incident("begin_submission")
                continue
            if not started:
                continue
            intent = self._intent_model(intent_id)
            try:
                result = await self.broker.submit(intent)
            except UncertainExternal:
                self._complete_submission(intent_id, "UNKNOWN", {"error": "timeout"})
                self._sync_pilot_effect(intent_id)
                continue
            if result.status == "acknowledged":
                self._complete_submission(intent_id, "OPEN", {"venue_order_id": result.venue_order_id})
            elif result.status == "uncertain":
                self._complete_submission(intent_id, "UNKNOWN", {"error": result.error})
            else:
                self._complete_submission(
                    intent_id, "REJECTED", {"error": result.error, "message": result.message}, release=True,
                )
            self._sync_pilot_effect(intent_id)
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
    def record_fill(self, fill: FillRecord, *, _allow_chronological_replay: bool = False) -> bool:
        existing = self.database.execute(
            "SELECT document_json FROM fills WHERE venue = ? AND account_id = ? AND trade_id = ?",
            (fill.venue, fill.account_id, fill.trade_id),
        ).fetchone()
        if existing is not None:
            if existing["document_json"] != fill.model_dump_json():
                self._incident("fill_discrepancy", {"trade_id": fill.trade_id})
            else:
                self._record_native_cost_limits(fill)
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
            previous = self.database.execute(
                "SELECT document_json FROM fills WHERE portfolio_id=?", (portfolio_id,),
            ).fetchall()
            late = any(FillRecord.model_validate_json(row["document_json"]).filled_at_utc > fill.filled_at_utc
                       for row in previous)
            if late:
                if not _allow_chronological_replay:
                    raise ValidationFailure("late fill requires chronological ledger replay")
                self.ledger.apply_late_fill(portfolio_id, fill, base_asset=base, quote_asset=quote)
            else:
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
            self._record_native_cost_limits(fill)
            self._consume_reservation(fill)
            self._refresh_order_state(fill.intent_id)
        return True

    async def reconcile(self) -> None:
        rows = self.database.execute(
            """SELECT intent_id, portfolio_id, payload_json, state FROM order_intents
            WHERE json_extract(payload_json, '$.venue') = ?
            AND json_extract(payload_json, '$.account_id') = ?
            AND json_extract(payload_json, '$.mode') = ?""",
            (self.venue, self.account_id, self.mode),
        ).fetchall()
        if not rows and self.mode != "live":
            return
        owned = {row["intent_id"]: row for row in rows}
        active = [row for row in rows if row["state"] in {
            "UNKNOWN", "SUBMITTING", "OPEN", "PARTIALLY_FILLED", "CANCEL_PENDING",
        }]
        statuses = {}
        # Resolve every recoverable identity before querying account-wide trades.
        # An adapter may need these lookups to link a venue order to its client ID.
        for row in active:
            payload = json.loads(row["payload_json"])
            if row["state"] == "SUBMITTING":
                self._set_state(row["intent_id"], "UNKNOWN", {})
            statuses[row["intent_id"]] = await self.broker.order_status(
                OrderLookup(client_order_id=payload["client_order_id"],
                            venue_order_id=payload.get("venue_order_id"), symbol=payload["symbol"])
            )

        fills = []
        cursor = None
        seen_cursors: set[str] = set()
        while True:
            page = await self.broker.fills_since(cursor)
            fills.extend(page.fills)
            if page.next_cursor is None:
                break
            if page.next_cursor in seen_cursors:
                raise UncertainExternal("fill pagination did not advance")
            seen_cursors.add(page.next_cursor)
            cursor = page.next_cursor
        # Sort globally, not per intent: FIFO/cash must observe the same chronology
        # across interleaved orders. Stable ties preserve the adapter's precise
        # wire ordering when distinct timestamps truncate to one DTO microsecond.
        fills.sort(key=lambda fill: fill.filled_at_utc)
        unowned = {}
        owned_portfolios = {row["portfolio_id"] for row in rows}
        recorded_identities = set()
        for row in self.database.execute("SELECT portfolio_id, document_json FROM fills"):
            if row["portfolio_id"] not in owned_portfolios:
                continue
            fill = FillRecord.model_validate_json(row["document_json"])
            recorded_identities.add((fill.venue, fill.account_id, fill.trade_id))
        new_at_same_time = set()
        try:
            with self.database.immediate():
                for fill in fills:
                    if fill.intent_id in owned:
                        if fill.filled_at_utc > self.clock.now():
                            raise ValidationFailure("broker fill is later than reconciliation time")
                        existing = self.database.execute(
                            "SELECT document_json FROM fills WHERE venue = ? AND account_id = ? AND trade_id = ?",
                            (fill.venue, fill.account_id, fill.trade_id),
                        ).fetchone()
                        if existing is not None and existing["document_json"] != fill.model_dump_json():
                            raise ValidationFailure("broker fill conflicts with recorded history")
                        portfolio_id = owned[fill.intent_id]["portfolio_id"]
                        identity = (fill.venue, fill.account_id, fill.trade_id)
                        tie = (portfolio_id, fill.filled_at_utc)
                        if existing is None:
                            new_at_same_time.add(tie)
                        elif identity in recorded_identities and tie in new_at_same_time:
                            raise ValidationFailure("late fill requires chronological ledger replay")
                        self.record_fill(fill, _allow_chronological_replay=True)
                    elif (fill.venue, fill.account_id) == (self.venue, self.account_id):
                        referenced = self.database.execute(
                            "SELECT 1 FROM order_intents WHERE intent_id = ?", (fill.intent_id,),
                        ).fetchone() if fill.intent_id is not None else None
                        if referenced:
                            raise ValidationFailure("broker fill references another execution scope")
                        unowned[fill.trade_id] = fill
                for fill in unowned.values():
                    previous = self.database.execute(
                        """SELECT 1 FROM activity_events WHERE kind = 'unreconciled_broker_fill'
                        AND json_extract(payload_json, '$.venue') = ?
                        AND json_extract(payload_json, '$.account_id') = ?
                        AND json_extract(payload_json, '$.trade_id') = ? LIMIT 1""",
                        (fill.venue, fill.account_id, fill.trade_id),
                    ).fetchone()
                    if previous is None:
                        self._incident("unreconciled_broker_fill", {
                            "venue": fill.venue, "account_id": fill.account_id, "trade_id": fill.trade_id,
                            "reason": "fill has no owned durable intent; account reconciliation is incomplete",
                        })
                if unowned:
                    self._set_reconciliation_health(incomplete=True, reason="unowned broker fills")
        except ValidationFailure:
            # The fill batch rolls back, but readiness must remain blocked across
            # restart until this binding's full history can be validated again.
            self._set_reconciliation_health(incomplete=True, reason="invalid owned broker fills")
            raise

        missing_terminal_fills = False
        for row in active:
            payload = json.loads(row["payload_json"])
            status = statuses[row["intent_id"]]
            recorded = self._filled_quantity(row["intent_id"])
            expected = Decimal(payload["quantity"]) if status.status == "filled" else status.filled_quantity
            if status.status in {"filled", "cancelled"} and recorded != expected:
                missing_terminal_fills = True
                self._set_state(row["intent_id"], "UNKNOWN", {})
                self._incident("terminal_order_missing_fills", {"intent_id": row["intent_id"]})
                continue
            if status.status == "not_found" and row["state"] in {"SUBMITTING", "UNKNOWN"}:
                self._incident("unknown_order_not_visible", {"intent_id": row["intent_id"]})
                continue
            if status.status == "filled":
                self._set_state(row["intent_id"], "FILLED", {"venue_order_id": status.venue_order_id}
                                if status.venue_order_id else {})
                self._release(row["intent_id"])
            elif status.status == "cancelled":
                self._set_state(row["intent_id"], "CANCELLED", {"venue_order_id": status.venue_order_id}
                                if status.venue_order_id else {})
                self._release(row["intent_id"])
        if unowned:
            raise UncertainExternal("broker history includes unowned fills; account reconciliation is incomplete")
        with self.database.immediate():
            # Owned fill history alone cannot resolve two conflicting native
            # acknowledgements. Keep this durable uncertainty through restart.
            conflicting_identity = bool(self.database.execute(
                """SELECT 1 FROM order_intents
                WHERE json_extract(payload_json, '$.venue') = ?
                  AND json_extract(payload_json, '$.account_id') = ?
                  AND json_extract(payload_json, '$.mode') = ?
                  AND json_extract(payload_json, '$.late_submission.venue_order_id') IS NOT NULL
                  AND json_extract(payload_json, '$.venue_order_id') IS NOT NULL
                  AND json_extract(payload_json, '$.late_submission.venue_order_id')
                      != json_extract(payload_json, '$.venue_order_id') LIMIT 1""",
                (self.venue, self.account_id, self.mode),
            ).fetchone())
            self._set_reconciliation_health(
                incomplete=missing_terminal_fills or conflicting_identity,
                reason=("conflicting submission order identity" if conflicting_identity else
                        "terminal order missing fills" if missing_terminal_fills else "resolved full broker history"),
            )
        for row in rows:
            self._sync_pilot_effect(row["intent_id"])

    async def startup(self) -> None:
        if self.mode == "live" and self._pilot_lifecycle is not None:
            try:
                self._pilot_lifecycle.recover(self._pilot_authorization_id)
            except AuthorityDenied:
                self._pilot_management_incident("recover")
        await self.reconcile()
        await self.dispatch()

    async def advance_pause(self, portfolio_id: str) -> str:
        """Apply the persisted pause profile and record only a verified achieved state."""
        current = self.pause(portfolio_id)
        profile = current["profile"] if current else "RUNNING"

        def achieved_state(achieved: str) -> str:
            if self._set_achieved(portfolio_id, achieved, expected_pause=current):
                return achieved
            # An awaited cancellation/reconciliation may outlive a newer owner
            # request. Return its actual state without relabelling that pause.
            latest = self.pause(portfolio_id)
            return latest["achieved"] if latest else "requested"

        if profile == "RUNNING":
            if current:
                return achieved_state("running")
            return "running"
        if profile == "PAUSE_DECISIONS":
            return achieved_state("decisions-paused")
        if profile == "MANAGE_ONLY":
            return achieved_state("managing")
        if profile == "NO_NEW_EXPOSURE":
            await self._cancel_for_pause(portfolio_id, sides={"buy"}, keep_flatten_exits=False)
            buy_left = self._has_outstanding(portfolio_id, sides={"buy"})
            achieved = "cancelling-increases" if buy_left else "increases-cleared"
            return achieved_state(achieved)
        if profile == "CANCEL_ALL":
            await self._cancel_for_pause(portfolio_id, sides=None, keep_flatten_exits=False)
            achieved = "cancelling-orders" if self._has_outstanding(portfolio_id) else "orders-cleared"
            return achieved_state(achieved)
        if profile == "FLATTEN":
            await self._cancel_for_pause(portfolio_id, sides=None, keep_flatten_exits=True)
            if self._flatten_ready(portfolio_id):
                return achieved_state("flat-verified")
            if not self._has_blocking(portfolio_id) and self._has_inventory(portfolio_id):
                self._queue_flatten_exits(portfolio_id)
                await self.dispatch()
            return achieved_state("flattening")
        if profile == "STOPPED":
            achieved = "stopped" if self._flatten_ready(portfolio_id) else "blocked-until-flat"
            return achieved_state(achieved)
        raise ValidationFailure("unsupported pause profile")

    async def cancel(self, intent_id: str) -> None:
        payload = self._assert_current_scope(intent_id)
        self._set_state(intent_id, "CANCEL_PENDING", {})
        await self.broker.cancel(
            CancelRequest(
                intent_id=intent_id,
                client_order_id=payload["client_order_id"],
                venue_order_id=payload.get("venue_order_id"),
                symbol=payload["symbol"],
            )
        )
        await self.reconcile()

    async def replace(self, intent_id: str, decision: Decision) -> str:
        self._assert_current_scope(intent_id)
        await self.cancel(intent_id)
        if self.intent_state(intent_id) not in {"CANCELLED", "FILLED"}:
            raise UncertainExternal("replacement requires reconciled cancellation of the original")
        payload = self._payload(intent_id)
        filled = self._filled_quantity(intent_id)
        remaining = Decimal(payload["quantity"]) - filled
        if decision.quantity is None or decision.quantity.amount > remaining:
            raise ValidationFailure("replacement exceeds remaining quantity")
        return self.authorize(payload["portfolio_id"], decision)

    @atomic
    def place_protection(
        self,
        portfolio_id: str,
        symbol: str,
        quantity: Decimal,
        stop_price: Decimal,
        snapshot_id: str,
    ) -> str:
        if (self.venue, self.account_id, self.mode) != ("paper", "paper", "paper"):
            raise AuthorityDenied("paper protection requires the default paper execution binding")
        portfolio = self.database.execute(
            "SELECT mode FROM portfolios WHERE portfolio_id = ?", (portfolio_id,),
        ).fetchone()
        if portfolio is None or portfolio["mode"] != "paper":
            raise AuthorityDenied("paper protection requires a paper portfolio")
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

    _OUTSTANDING = frozenset({
        "SUBMISSION_PENDING",
        "SUBMITTING",
        "OPEN",
        "PARTIALLY_FILLED",
        "UNKNOWN",
        "CANCEL_PENDING",
    })

    def _set_achieved(self, portfolio_id: str, achieved: str, *, expected_pause: dict | None = None) -> bool:
        with self.database.immediate() as conn:
            if expected_pause is None:
                result = conn.execute(
                    "UPDATE pause_states SET achieved = ? WHERE portfolio_id = ?",
                    (achieved, portfolio_id),
                )
            else:
                result = conn.execute(
                    """UPDATE pause_states SET achieved = ? WHERE portfolio_id = ?
                    AND profile = ? AND originator = ? AND requested_at = ? AND reason = ? AND achieved = ?""",
                    (achieved, portfolio_id, *(expected_pause[name] for name in (
                        "profile", "originator", "requested_at", "reason", "achieved",
                    ))),
                )
            return result.rowcount == 1

    def _order_rows(self, portfolio_id: str) -> list:
        return self.database.execute(
            "SELECT intent_id, state, payload_json FROM order_intents WHERE portfolio_id = ?",
            (portfolio_id,),
        ).fetchall()

    def _has_outstanding(self, portfolio_id: str, *, sides: set[str] | None = None) -> bool:
        for row in self._order_rows(portfolio_id):
            if row["state"] not in self._OUTSTANDING:
                continue
            if sides is not None and json.loads(row["payload_json"]).get("side") not in sides:
                continue
            return True
        return False

    def _has_blocking(self, portfolio_id: str) -> bool:
        for row in self._order_rows(portfolio_id):
            if row["state"] not in self._OUTSTANDING:
                continue
            if json.loads(row["payload_json"]).get("flatten_exit"):
                continue
            return True
        return False

    def _flatten_ready(self, portfolio_id: str) -> bool:
        return not self._has_outstanding(portfolio_id) and not self._has_inventory(portfolio_id)

    def _has_inventory(self, portfolio_id: str) -> bool:
        return any(quantity > 0 for _, quantity, _rules in self._inventory(portfolio_id))

    def _inventory(self, portfolio_id: str) -> list[tuple[str, Decimal, InstrumentRules]]:
        rows = self.database.execute("SELECT document_json FROM instruments").fetchall()
        held: list[tuple[str, Decimal, InstrumentRules]] = []
        for row in rows:
            rules = InstrumentRules.model_validate_json(row["document_json"])
            quantity = self.owned_quantity(portfolio_id, rules.base_asset)
            if quantity > 0:
                held.append((rules.symbol, quantity, rules))
        return held

    async def _cancel_for_pause(
        self,
        portfolio_id: str,
        *,
        sides: set[str] | None,
        keep_flatten_exits: bool,
    ) -> None:
        for row in self._order_rows(portfolio_id):
            if row["state"] not in self._OUTSTANDING:
                continue
            payload = json.loads(row["payload_json"])
            if sides is not None and payload.get("side") not in sides:
                continue
            if keep_flatten_exits and payload.get("flatten_exit"):
                continue
            if row["state"] == "SUBMISSION_PENDING":
                self._abandon_unsent(row["intent_id"], "pause")
                continue
            if row["state"] == "CANCEL_PENDING":
                continue
            await self.cancel(row["intent_id"])
        await self.reconcile()

    def _queue_flatten_exits(self, portfolio_id: str) -> None:
        policy = self.authority.active_policy()
        mandate = self.authority.active_mandate(portfolio_id)
        for symbol, quantity, rules in self._inventory(portfolio_id):
            if self._flatten_exit_outstanding(portfolio_id, symbol):
                continue
            decision_id = f"flatten-{uuid.uuid4()}"
            decision = Decision(
                record_id=decision_id,
                created_at_utc=self.clock.now(),
                run_id="pause",
                task_id="flatten",
                root_task_id="flatten",
                portfolio_id=portfolio_id,
                mode=self.mode,
                system_version_id="pause",
                trace_id=decision_id,
                action="exit",
                symbol=symbol,
                quantity=Quantity(amount=quantity, asset=rules.base_asset),
                rationale="close verified remaining inventory",
                invalidation="owner flatten",
                horizon_seconds=3600,
                strategy_id="slow-trend",
                snapshot_id="pause",
                mandate_revision=str(mandate.revision),
                policy_revision=policy.revision_id,
            )
            try:
                intent_id = self.authorize(portfolio_id, decision)
            except (ValidationFailure, AuthorityDenied, StaleState) as exc:
                self._incident("flatten_exit_not_submitted", {"symbol": symbol, "reason": str(exc)})
                continue
            self._mark_flatten_exit(intent_id)

    def _flatten_exit_outstanding(self, portfolio_id: str, symbol: str) -> bool:
        for row in self._order_rows(portfolio_id):
            if row["state"] not in self._OUTSTANDING:
                continue
            payload = json.loads(row["payload_json"])
            if payload.get("flatten_exit") and payload.get("symbol") == symbol:
                return True
        return False

    def _mark_flatten_exit(self, intent_id: str) -> None:
        payload = self._payload(intent_id)
        payload["flatten_exit"] = True
        with self.database.immediate() as conn:
            conn.execute(
                "UPDATE order_intents SET payload_json = ? WHERE intent_id = ?",
                (json.dumps(payload), intent_id),
            )

    def intent_state(self, intent_id: str) -> str:
        row = self.database.execute(
            "SELECT state FROM order_intents WHERE intent_id = ?",
            (intent_id,),
        ).fetchone()
        return row["state"]

    async def _require_broker_binding(self) -> BrokerCapabilities:
        caps = await self.broker.capabilities()
        if caps.withdrawals:
            raise AuthorityDenied("withdrawals are not a broker capability")
        if caps.venue != self.venue or caps.mode != self.mode:
            raise AuthorityDenied("broker binding does not match execution venue and mode")
        if not caps.client_id_lookup:
            raise AuthorityDenied("broker cannot look up orders by client id")
        return caps

    @staticmethod
    def _unsupported_feature(intent: AuthorizedOrderIntent, caps: BrokerCapabilities) -> str | None:
        if intent.time_in_force not in caps.time_in_force:
            return "unsupported time in force"
        if intent.order_type == "stop" and not (caps.native_stop and caps.native_stop_tested):
            return "unsupported or untested native stop"
        if intent.stop_price is not None and intent.order_type != "stop":
            return "unsupported combined stop order"
        return None

    def _unsent_increase_block(self, intent: AuthorizedOrderIntent) -> str:
        if self._reconciliation_blocked():
            return "hold"
        if self.blocks_increase is not None and self.blocks_increase(intent.symbol):
            return "hold"
        if self._uncertain_exposure(intent.symbol):
            return "hold"
        try:
            policy = self.authority.active_policy()
            mandate = self.authority.active_mandate(intent.portfolio_id)
        except AuthorityDenied:
            return "reject"
        if self.authority.mandate_expired(mandate):
            return "reject"
        if intent.symbol not in mandate.symbols or intent.symbol not in policy.allowed_symbols:
            return "reject"
        if intent.venue not in policy.allowed_venues or intent.mode != self.mode:
            return "reject"
        if intent.mode == "live" and not policy.live_enabled:
            return "reject"
        if intent.order_type not in mandate.allowed_order_types:
            return "reject"
        gross_cap, asset_cap, max_age = self.authority.limits(policy, mandate)
        if not self.quote_fresh(intent.symbol, max_age, intent.venue):
            return "hold"
        price = intent.limit_price or self._reference_price(intent.symbol, "buy", intent.venue)
        if price is None or price <= 0:
            return "hold"
        rules = self.instrument(intent.venue, intent.symbol)
        try:
            self._assert_increase_exposure(
                intent.portfolio_id,
                intent.symbol,
                intent.quantity,
                price,
                rules.quote_asset,
                rules.base_asset,
                gross_cap,
                asset_cap,
                exclude_intent_id=intent.intent_id,
            )
        except AuthorityDenied:
            return "reject"
        except StaleState:
            return "hold"
        return "submit"

    def _uncertain_exposure(self, symbol: str) -> bool:
        rows = self.database.execute(
            """SELECT payload_json FROM order_intents WHERE symbol = ?
            AND state IN ('UNKNOWN', 'SUBMITTING', 'CANCEL_PENDING')""", (symbol,),
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            if (payload["venue"], payload["account_id"]) == (self.venue, self.account_id):
                return True
        return False

    def _validate_fee_reserve_rate(self, *, require_ready: bool = True) -> None:
        """Operator's worst anticipated rate must cover any fee bound the broker exposes."""
        required = getattr(self.broker, "fee_reserve_rate", None)
        if required is None:
            if require_ready and self.mode == "live":
                raise ValidationFailure("live broker fee readiness is unavailable")
            return
        try:
            bound = parse_decimal(required)
        except (ValueError, TypeError) as exc:
            raise ValidationFailure("broker fee bound must be a finite Decimal rate") from exc
        if bound < 0:
            raise ValidationFailure("broker fee bound must be nonnegative")
        if self.fee_reserve_rate < bound:
            raise ValidationFailure("fee reserve rate is below the broker's configured fees")

    def _abandon_unsent(self, intent_id: str, reason: str, *, message: str | None = None) -> None:
        payload = self._payload(intent_id)
        payload["error"] = reason
        if message is not None:
            payload["message"] = message
        now = self.now()
        with self.database.immediate() as conn:
            cursor = conn.execute(
                """UPDATE order_intents
                SET state = 'REJECTED', payload_json = ?, updated_at = ?
                WHERE intent_id = ? AND state = 'SUBMISSION_PENDING'""",
                (json.dumps(payload), now, intent_id),
            )
            if cursor.rowcount != 1:
                return
            self._release(intent_id)
            conn.execute(
                """UPDATE outbox SET status = 'cancelled'
                WHERE kind = 'submit' AND payload_ref = ? AND status = 'pending'""",
                (intent_id,),
            )

    def _assert_increase_exposure(
        self,
        portfolio_id: str,
        symbol: str,
        quantity: Decimal,
        price: Decimal,
        quote_asset: str,
        base_asset: str,
        gross_cap: Decimal,
        asset_cap: Decimal,
        *,
        exclude_intent_id: str | None = None,
    ) -> None:
        books = self.ledger.books(portfolio_id)
        equity = self.ledger.equity(portfolio_id)
        if equity.equity is None or equity.provisional:
            raise StaleState("provisional valuation")
        if equity.equity <= 0:
            raise AuthorityDenied("no equity")

        def reporting(amount: Decimal) -> Decimal:
            return self.ledger.reporting_value(portfolio_id, amount, quote_asset)

        pending_gross, pending_asset = self._pending_exposure(
            portfolio_id, base_asset, exclude_intent_id=exclude_intent_id
        )
        proposed = reporting(price * quantity)
        projected_asset = reporting(self._position_value(books, symbol, price)) + pending_asset + proposed
        projected_gross = (equity.inventory_reporting or Decimal("0")) + pending_gross + proposed
        if projected_asset / equity.equity > asset_cap:
            raise AuthorityDenied("single asset exposure")
        if projected_gross / equity.equity > gross_cap:
            raise AuthorityDenied("gross exposure")

    @atomic
    def _mark_submitting(self, intent_id: str) -> bool:
        self._assert_current_scope(intent_id)
        intent = self._intent_model(intent_id)
        profile = self.profile(intent.portfolio_id)
        allowed = pause_allows_reduction(profile) if intent.side == "sell" else pause_allows_increase(profile)
        if not allowed:
            return False
        now = self.now()
        with self.database.immediate() as conn:
            pilot_effect = None
            if self.mode == "live" and intent.side == "buy":
                self._assert_pilot_increase(intent.portfolio_id, intent.symbol)
                pilot_effect = self._pilot_lifecycle.begin_intent_submission(self._pilot_authorization_id, intent_id)
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
            attempt_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO order_attempts (attempt_id, intent_id, kind, created_at, result_json)
                VALUES (?, ?, 'submit', ?, '{}')""",
                (attempt_id, intent_id, now),
            )
            if pilot_effect is not None:
                self._pilot_lifecycle.record_submission_attempt(pilot_effect, attempt_id)
        return True

    @atomic
    def _complete_submission(self, intent_id: str, state: str, extra: dict, *, release: bool = False) -> None:
        """A delayed reply cannot erase newer fill, cancellation or recovery facts.

        The completion and any reservation release share a writer transaction.
        Once management or a fill has moved the intent beyond SUBMITTING, retain
        that state and record the delayed reply only as evidence. A newly learned
        consistent native order ID remains useful for cold-start reconciliation.
        """
        current = self.intent_state(intent_id)
        payload = self._payload(intent_id)
        reported_id = extra.get("venue_order_id")
        known_id = payload.get("venue_order_id")
        if reported_id and known_id and reported_id != known_id:
            self._incident("submission_identity_discrepancy", {"intent_id": intent_id})
            self._set_reconciliation_health(incomplete=True, reason="conflicting submission order identity")
            self._set_state(intent_id, current, {"late_submission": {"state": state, **extra}})
            return
        if current != "SUBMITTING":
            evidence = {"late_submission": {"state": state, **extra}}
            if reported_id:
                evidence["venue_order_id"] = reported_id
            self._set_state(intent_id, current, evidence)
            return
        self._set_state(intent_id, state, extra)
        if release:
            self._release(intent_id)

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
            conn.execute(
                "UPDATE native_fee_reservations SET state='released' WHERE intent_id=? AND state='held'", (intent_id,),
            )

    def _reserved(self, portfolio_id: str, asset: str) -> Decimal:
        rows = self.database.execute(
            """SELECT amount FROM position_reservations
            WHERE portfolio_id = ? AND asset = ? AND state = 'held'""",
            (portfolio_id, asset),
        ).fetchall()
        fees = self.database.execute(
            """SELECT current_amount AS amount,original_amount FROM native_fee_reservations
            WHERE portfolio_id=? AND asset=? AND state='held'""",
            (portfolio_id, asset),
        ).fetchall()
        with localcontext(Context(prec=28)) as context:
            context.traps[Inexact] = True
            for row in fees:
                current, original = parse_decimal(row["amount"]), parse_decimal(row["original_amount"])
                if not 0 <= current <= original or original <= 0:
                    raise StaleState("invalid original/current native fee reservation")
            return sum((Decimal(row["amount"]) for row in [*rows, *fees]), Decimal("0"))

    def _owned(self, books, asset: str) -> Decimal:
        return sum((lot.open_quantity() for lot in books.lots if lot.asset == asset), Decimal("0"))

    def _pending_exposure(
        self,
        portfolio_id: str,
        asset: str,
        *,
        exclude_intent_id: str | None = None,
    ) -> tuple[Decimal, Decimal]:
        gross = single = Decimal("0")
        rows = self.database.execute(
            """SELECT intent_id, payload_json FROM order_intents WHERE portfolio_id = ?
            AND state NOT IN ('FILLED', 'CANCELLED', 'REJECTED')""", (portfolio_id,)
        ).fetchall()
        for row in rows:
            if row["intent_id"] == exclude_intent_id:
                continue
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
        rows = self.database.execute(
            "SELECT reservation_id, asset, amount FROM position_reservations WHERE intent_id = ? AND state = 'held'",
            (fill.intent_id,),
        ).fetchall()
        debits = _fill_reservation_debits(fill)
        fee_rows = self.database.execute(
            """SELECT reservation_id,asset,current_amount AS amount FROM native_fee_reservations
            WHERE intent_id=? AND state='held'""", (fill.intent_id,),
        ).fetchall()
        with localcontext() as context:
            context.prec = 28
            context.traps[Inexact] = True
            primary_updates, auxiliary_updates = [], []
            for group, updates in ((rows, primary_updates), (fee_rows, auxiliary_updates)):
                for row in group:
                    used = debits.get(row["asset"], Decimal("0"))
                    remaining = Decimal("0") if used >= Decimal(row["amount"]) else Decimal(row["amount"]) - used
                    updates.append((canonical_decimal(max(remaining, Decimal("0"))),
                                    "released" if remaining <= 0 else "held", row["reservation_id"]))
            with self.database.immediate() as conn:
                conn.executemany(
                    "UPDATE position_reservations SET amount = ?, state = ? WHERE reservation_id = ?", primary_updates,
                )
                conn.executemany(
                    "UPDATE native_fee_reservations SET current_amount=?,state=? WHERE reservation_id=?",
                    auxiliary_updates,
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

    def _incident(self, kind: str, payload: dict, *, created_at: str | None = None) -> None:
        self.ledger._activity(None, kind, payload, created_at=created_at)

    def _assert_current_scope(self, intent_id: str) -> dict:
        payload = self._payload(intent_id)
        if (payload.get("venue"), payload.get("account_id"), payload.get("mode")) != (
            self.venue, self.account_id, self.mode,
        ):
            raise AuthorityDenied("intent belongs to another execution scope")
        return payload

    def _reconciliation_health(self) -> dict | None:
        row = self.database.execute(
            """SELECT payload_json FROM activity_events WHERE kind = 'execution_reconciliation_health'
            AND json_extract(payload_json, '$.venue') = ?
            AND json_extract(payload_json, '$.account_id') = ?
            AND json_extract(payload_json, '$.mode') = ? ORDER BY rowid DESC LIMIT 1""",
            (self.venue, self.account_id, self.mode),
        ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def _reconciliation_blocked(self) -> bool:
        health = self._reconciliation_health()
        return (health is not None and health["state"] == "incomplete") or self._native_cost_limits_blocked()

    def _native_cost_limits_blocked(self) -> bool:
        if self._native_incident_resolver is not None:
            if not self._incident_resolver_bound():
                return True
            try:
                return self._native_incident_resolver.blocked()
            except (TradeGraphError, ValueError, TypeError, KeyError, ArithmeticError, sqlite3.DatabaseError):
                # Failed review cannot lose an already executed native fact when
                # reconciliation updates its sticky health record.
                return True
        return bool(self.database.execute(
            """SELECT 1 FROM activity_events WHERE kind='native_fill_execution_limit_discrepancy'
            AND json_extract(payload_json,'$.venue')=? AND json_extract(payload_json,'$.account_id')=?
            AND json_extract(payload_json,'$.mode')=? LIMIT 1""", (self.venue, self.account_id, self.mode),
        ).fetchone())

    def _incident_resolver_bound(self) -> bool:
        resolver = self._native_incident_resolver
        return (
            type(resolver) is ProtectedNativeIncidentResolver
            and resolver.database is self.database and resolver.clock is self.clock
            and self.mode == "live" and resolver.scope.venue == self.venue
            and resolver.scope.account_id == self.account_id
            and (self._pilot_lifecycle is None or resolver.scope == self._pilot_lifecycle.scope)
        )

    def _record_native_cost_limits(self, fill: FillRecord) -> None:
        if fill.intent_id is None:
            return
        if (fill.quote_cost is None and fill.fee_components is None and fill.fee_amount == 0
                and self.database.execute(
                    "SELECT 1 FROM native_fee_reservations WHERE intent_id=? LIMIT 1", (fill.intent_id,),
                ).fetchone() is None):
            return
        payload = self._payload(fill.intent_id)
        reasons = []
        if payload.get("limit_price") is not None:
            with localcontext() as context:
                context.prec = 128
                limit = Decimal(payload["limit_price"]) * fill.quantity
                if ((fill.side == "buy" and fill.quote_principal > limit)
                        or (fill.side == "sell" and fill.quote_principal < limit)):
                    reasons.append("native_principal_exceeds_limit")
        asset = payload.get("reserve_asset")
        originals = {}
        if asset is not None and payload.get("reserve_amount") is not None:
            originals[asset] = parse_decimal(payload["reserve_amount"])
        originals.update({row["asset"]: parse_decimal(row["original_amount"]) for row in self.database.execute(
            "SELECT asset,original_amount FROM native_fee_reservations WHERE intent_id=?", (fill.intent_id,),
        )})
        cumulative = {}
        with localcontext(Context(prec=128)):
            for row in self.database.execute("SELECT document_json FROM fills WHERE intent_id=?", (fill.intent_id,)):
                recorded = FillRecord.model_validate_json(row["document_json"])
                for name, amount in _fill_reservation_debits(recorded).items():
                    cumulative[name] = cumulative.get(name, Decimal("0")) + amount
        excess = [{"asset": name, "used": canonical_decimal(amount), "original": canonical_decimal(originals[name])}
                  for name, amount in sorted(cumulative.items()) if name in originals and amount > originals[name]]
        if excess:
            reasons.append("native_fills_exceed_original_reservation")
        unreserved_fee_assets = sorted(name for name, amount in _fill_reservation_debits(fill).items()
                                       if amount > 0 and name not in originals and name != asset)
        if unreserved_fee_assets:
            reasons.append("native_fee_asset_has_no_original_reservation")
        if not reasons or self.database.execute(
            """SELECT 1 FROM activity_events WHERE kind='native_fill_execution_limit_discrepancy'
            AND json_extract(payload_json,'$.intent_id')=? AND json_extract(payload_json,'$.trade_id')=? LIMIT 1""",
            (fill.intent_id, fill.trade_id),
        ).fetchone():
            return
        self._incident("native_fill_execution_limit_discrepancy", {
            "venue": self.venue, "account_id": self.account_id, "mode": self.mode,
            "intent_id": fill.intent_id, "trade_id": fill.trade_id,
            "quote_principal": canonical_decimal(fill.quote_principal), "reasons": reasons,
            "unreserved_fee_assets": unreserved_fee_assets, "reservation_excess": excess,
            "financial_facts_preserved": True, "owner_review_required": True,
        })
        self._set_reconciliation_health(incomplete=True, reason="native fill execution limits require review")

    def _set_reconciliation_health(self, *, incomplete: bool, reason: str) -> None:
        if self._native_cost_limits_blocked():
            incomplete, reason = True, "native fill execution limits require review"
        state = "incomplete" if incomplete else "complete"
        health = self._reconciliation_health()
        # Every actually completed live history scan retains a fresh observation.
        # A prior complete label cannot resolve a subsequently changed order.
        # This covers owned intents/fill history, not account balances, key
        # permissions, owner eligibility or authority to dispatch a live order.
        fresh_live_scan = self.mode == "live" and not incomplete
        if health is None and not incomplete and not fresh_live_scan:
            return
        if (not fresh_live_scan and health is not None
                and health["state"] == state and health["reason"] == reason):
            return
        observed_at = self.now()
        self._incident("execution_reconciliation_health", {
            "venue": self.venue, "account_id": self.account_id, "mode": self.mode,
            "state": state, "reason": reason,
            "observed_at": observed_at, "observation_scope": "owned_intent_fill_history",
        }, created_at=observed_at)
