"""Durable bounded-pilot preparation and management, with no external-effect route.

Only a protected service may instantiate this controller. Signed declarations
can be staged, but the current readiness implementation prevents activation and
submission. This module neither changes live policy nor calls a broker/provider.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from decimal import Context, Decimal, localcontext

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import InstrumentRules
from trade_graph.domain.clock import Clock, parse_utc, utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure
from trade_graph.domain.money import Money, canonical_decimal, parse_decimal
from trade_graph.live_evidence import LiveUpstreamSources
from trade_graph.live_gate import (
    LivePilotScope,
    OwnerPilotAuthorization,
    PinnedReadinessSource,
    _canonical,
    evaluate_live_readiness,
)

_HELD_EFFECTS = {"PREPARED", "SUBMITTING", "UNKNOWN", "COMMITTED"}
_OPEN_ORDERS = {"SUBMISSION_PENDING", "SUBMITTING", "UNKNOWN", "OPEN", "PARTIALLY_FILLED", "CANCEL_PENDING"}


def _amount(value) -> Decimal:
    amount = parse_decimal(value)
    if amount < 0 or not -18 <= amount.as_tuple().exponent <= 18 or len(amount.as_tuple().digits) > 36:
        raise ValidationFailure("bounded nonnegative native amount required")
    return amount


def _digest(value: dict) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def validate_pilot_envelope(
    grant: OwnerPilotAuthorization, *, native_commitments: tuple[Money, ...],
    proposed_native_cost: Money, actual_expenses: tuple[tuple[Money, datetime], ...], now: datetime,
) -> dict[str, Money]:
    """Conservative full-loss envelope; sales and profits never replenish it.

    Native commitments are worst-case acquisition costs including quote fees,
    retained even after fills/sales. A price stop does not guarantee a loss cap.
    Expenses contain all actual deployment reservations, including unknown usage.
    Synthetic receipts are excluded by the protected database reader, not here.
    """
    grant = OwnerPilotAuthorization.model_validate(grant.model_dump())
    if now.tzinfo is None or not grant.verified_at <= now < grant.expires_at:
        raise AuthorityDenied("pilot authorization is expired or not yet valid")
    if len(native_commitments) > 10000 or len(actual_expenses) > 10000:
        raise ValidationFailure("pilot envelope history exceeds bounded window")
    with localcontext(Context(prec=100)):
        if proposed_native_cost.currency != grant.allocation.currency or _amount(proposed_native_cost.amount) <= 0:
            raise ValidationFailure("positive cost in allocated native currency required")
        used = Decimal(0)
        for commitment in native_commitments:
            if commitment.currency != grant.allocation.currency:
                raise ValidationFailure("native commitment currency mismatch")
            used += _amount(commitment.amount)
        cap = min(grant.allocation.amount, grant.maximum_loss.amount)
        if used + proposed_native_cost.amount > cap:
            raise AuthorityDenied("pilot native allocation or full-loss envelope exhausted")
        expenses = daily = Decimal(0)
        day = utc_iso(now)[:10]
        for amount, observed_at in actual_expenses:
            if amount.currency != "EUR" or observed_at.tzinfo is None or observed_at > now:
                raise ValidationFailure("actual expenses require dated EUR reservations")
            expenses += _amount(amount.amount)
            if utc_iso(observed_at)[:10] == day:
                daily += amount.amount
        if expenses > grant.operating_allowance.amount or daily > grant.daily_expense_limit.amount:
            raise AuthorityDenied("pilot real operating allowance or daily limit exhausted")
        return {
            "native_remaining": Money(amount=cap - used - proposed_native_cost.amount,
                                      currency=grant.allocation.currency),
            "operating_remaining": Money(amount=grant.operating_allowance.amount - expenses, currency="EUR"),
            "daily_remaining": Money(amount=grant.daily_expense_limit.amount - daily, currency="EUR"),
        }


class ProtectedPilotLifecycle:
    """Private lifecycle service; no HTTP/model interface or injectable verifier.

    Startup must call recover for a previously running grant before dispatch.
    This code is preparation until an independently authorized protected broker
    adopts its prepare/begin/reconcile protocol. Current readiness stays closed.
    """

    def __init__(self, database: Database, clock: Clock, *, scope: LivePilotScope,
                 source: PinnedReadinessSource, upstream: LiveUpstreamSources | None = None) -> None:
        if type(scope) is not LivePilotScope or type(source) is not PinnedReadinessSource:
            raise ValueError("exact protected scope and source implementations required")
        self.database, self.clock, self.scope, self.source, self.upstream = database, clock, scope, source, upstream

    def _now(self) -> str:
        return utc_iso(self.clock.now())

    def _event(self, authorization_id: str, generation: int, kind: str, payload: dict) -> None:
        self.database.execute(
            "INSERT INTO live_pilot_events VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), authorization_id, generation, kind, _canonical(payload).decode(), self._now()),
        )

    def _grant(self, authorization_id: str):
        row = self.database.execute("SELECT * FROM live_pilot_grants WHERE authorization_id=?",
                                    (authorization_id,)).fetchone()
        if row is None:
            raise AuthorityDenied("unknown protected pilot authorization")
        grant = OwnerPilotAuthorization.model_validate_json(row["authorization_json"])
        if (grant.scope != self.scope or grant.authorization_id != authorization_id
                or _digest(grant.model_dump(mode="json")) != row["authorization_sha256"]
                or grant.scope.model_dump_json() != row["scope_json"]
                or row["deployment_id"] != self.scope.deployment_id or row["portfolio_id"] != self.scope.portfolio_id
                or row["stop_profile"] != grant.stop_profile):
            raise AuthorityDenied("persisted pilot identity or authorization changed")
        return row, grant

    def _current_bundle(self, row, grant: OwnerPilotAuthorization) -> None:
        bundle = self.source.load()
        if (row["bundle_sha256"] != self.source.bundle_sha256
                or bundle.owner_authorization.payload != grant
                or not grant.verified_at <= self.clock.now() < grant.expires_at):
            raise AuthorityDenied("pilot authorization pin or freshness changed")

    def _ready(self, row, grant: OwnerPilotAuthorization) -> bool:
        self._current_bundle(row, grant)
        result = evaluate_live_readiness(self.database, self.clock, scope=self.scope,
                                        source=self.source, upstream=self.upstream)
        return (result.get("ready") is True
                and result.get("upstream_verification", {}).get("authoritative_external_verification") is True)

    def prepare(self) -> str:
        """Retain a signed owner's envelope as PENDING; this grants no effects."""
        bundle = self.source.load()
        grant = bundle.owner_authorization.payload
        if grant.scope != self.scope or not grant.verified_at <= self.clock.now() < grant.expires_at:
            raise AuthorityDenied("owner authorization scope or freshness mismatch")
        with self.database.immediate():
            portfolio = self.database.execute("SELECT mode,status FROM portfolios WHERE portfolio_id=?",
                                              (self.scope.portfolio_id,)).fetchone()
            if portfolio is None or portfolio["mode"] != "live" or portfolio["status"] != "open":
                raise AuthorityDenied("separate open live portfolio required")
            prior = self.database.execute("SELECT * FROM live_pilot_grants WHERE authorization_id=?",
                                          (grant.authorization_id,)).fetchone()
            if prior is not None:
                row, existing = self._grant(grant.authorization_id)
                if existing != grant or row["bundle_sha256"] != self.source.bundle_sha256:
                    raise AuthorityDenied("authorization IDs are immutable")
                return grant.authorization_id
            other = self.database.execute(
                "SELECT 1 FROM live_pilot_grants WHERE portfolio_id=? "
                "AND state IN ('PENDING','ACTIVE','RECOVERY_REQUIRED','STOPPING')",
                (self.scope.portfolio_id,),
            ).fetchone()
            if other is not None:
                raise AuthorityDenied("a pilot lifecycle already requires management")
            books = Ledger(self.database, self.clock).books(self.scope.portfolio_id)
            if (any(lot.open_quantity() != 0 for lot in books.lots)
                    or any(value != 0 for asset, value in books.cash.items() if asset != grant.allocation.currency)):
                raise AuthorityDenied("a pilot grant requires a separate initially flat portfolio")
            # Revocation closes permission, not exposure. A new grant cannot
            # replace unresolved effects or a different portfolio on this account.
            siblings = self.database.execute(
                "SELECT authorization_id,portfolio_id,state FROM live_pilot_grants "
                "WHERE json_extract(scope_json,'$.venue')=? AND json_extract(scope_json,'$.account_id')=?",
                (self.scope.venue, self.scope.account_id),
            ).fetchmany(10001)
            if len(siblings) > 10000:
                raise StaleState("pilot account history exceeds bounded window")
            if siblings and not self._complete_account_history():
                raise StaleState("fresh complete account history required for a replacement pilot grant")
            for sibling in siblings:
                unresolved = self.database.execute(
                    "SELECT 1 FROM live_pilot_effects WHERE authorization_id=? "
                    "AND state IN ('PREPARED','SUBMITTING','UNKNOWN') LIMIT 1", (sibling["authorization_id"],),
                ).fetchone()
                prior_books = Ledger(self.database, self.clock).books(sibling["portfolio_id"])
                inventory = (any(lot.open_quantity() != 0 for lot in prior_books.lots)
                             or any(value != 0 for asset, value in prior_books.cash.items()
                                    if asset != grant.allocation.currency))
                if sibling["state"] not in {"STOPPED", "REVOKED"} or unresolved is not None or inventory:
                    raise AuthorityDenied("prior pilot account exposure still requires management")
            existing_orders = self.database.execute(
                "SELECT state FROM order_intents WHERE portfolio_id=? OR "
                "(json_extract(payload_json,'$.venue')=? AND json_extract(payload_json,'$.account_id')=? "
                "AND json_extract(payload_json,'$.mode')='live')",
                (self.scope.portfolio_id, self.scope.venue, self.scope.account_id),
            ).fetchmany(10001)
            if len(existing_orders) > 10000 or any(item["state"] in _OPEN_ORDERS for item in existing_orders):
                raise AuthorityDenied("unresolved account orders block a replacement pilot grant")
            now = self._now()
            self.database.execute(
                """INSERT INTO live_pilot_grants
                (authorization_id,deployment_id,portfolio_id,scope_json,authorization_json,authorization_sha256,
                 bundle_sha256,state,generation,stop_profile,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,'PENDING',0,?,?,?)""",
                (grant.authorization_id, self.scope.deployment_id, self.scope.portfolio_id,
                 self.scope.model_dump_json(), grant.model_dump_json(), _digest(grant.model_dump(mode="json")),
                 self.source.bundle_sha256, grant.stop_profile, now, now),
            )
            self._event(grant.authorization_id, 0, "prepared", {"execution_authority": False})
        return grant.authorization_id

    def activate(self, authorization_id: str) -> dict:
        """Request activation through the concrete closed readiness evaluator."""
        with self.database.immediate():
            row, grant = self._grant(authorization_id)
            if row["state"] != "PENDING":
                raise AuthorityDenied("activation requires a new pending owner authorization")
            ready = self._ready(row, grant)
            if not ready:
                self._event(authorization_id, row["generation"], "activation_refused",
                            {"code": "protected_prerequisites_incomplete"})
                return {"activated": False, "state": "PENDING", "code": "protected_prerequisites_incomplete"}
            self.database.execute("UPDATE live_pilot_grants SET state='ACTIVE',generation=generation+1,updated_at=? "
                                  "WHERE authorization_id=?", (self._now(), authorization_id))
            self._event(authorization_id, row["generation"] + 1, "activated", {})
            return {"activated": True, "state": "ACTIVE"}

    def _intent(self, intent_id: str, grant: OwnerPilotAuthorization):
        row = self.database.execute("SELECT * FROM order_intents WHERE intent_id=?", (intent_id,)).fetchone()
        if row is None:
            raise AuthorityDenied("unknown native pilot order intent")
        payload = json.loads(row["payload_json"])
        if (row["portfolio_id"] != self.scope.portfolio_id or row["symbol"] != self.scope.symbol
                or any(payload.get(key) != value for key, value in {
                    "portfolio_id": self.scope.portfolio_id, "account_id": self.scope.account_id,
                    "venue": self.scope.venue, "symbol": self.scope.symbol, "mode": "live", "side": "buy",
                    "order_type": "limit", "policy_revision": self.scope.policy_revision,
                }.items()) or payload.get("reduce_only") is not False):
            raise AuthorityDenied("pilot increase requires an exactly scoped bounded limit order")
        rule_row = self.database.execute("SELECT document_json FROM instruments WHERE venue=? AND symbol=?",
                                         (self.scope.venue, self.scope.symbol)).fetchone()
        if rule_row is None:
            raise StaleState("pilot instrument rules unavailable")
        rules = InstrumentRules.model_validate_json(rule_row["document_json"])
        if (rules.venue != self.scope.venue or rules.symbol != self.scope.symbol
                or rules.quote_asset != grant.allocation.currency):
            raise AuthorityDenied("pilot allocation must use native instrument quote currency")
        price, quantity = _amount(payload.get("limit_price")), _amount(payload.get("quantity"))
        if price <= 0 or quantity <= 0:
            raise ValidationFailure("positive bounded native price and quantity required")
        reservation = self.database.execute(
            "SELECT asset,amount,state FROM position_reservations WHERE intent_id=? AND portfolio_id=?",
            (intent_id, self.scope.portfolio_id),
        ).fetchall()
        with localcontext(Context(prec=100)):
            if (len(reservation) != 1 or reservation[0]["asset"] != rules.quote_asset
                    or reservation[0]["state"] != "held"
                    or _amount(reservation[0]["amount"]) < price * quantity
                    or payload.get("reserve_asset") != rules.quote_asset
                    or _amount(payload.get("reserve_amount")) != _amount(reservation[0]["amount"])):
                raise AuthorityDenied("pilot native cost requires the complete held execution reservation")
        decision = self.database.execute("SELECT policy_revision,system_version_id FROM decisions WHERE decision_id=?",
                                         (payload.get("decision_id"),)).fetchone()
        active = self.database.execute("SELECT version_id,artifact_hash FROM active_versions WHERE portfolio_id=?",
                                       (self.scope.portfolio_id,)).fetchone()
        if (decision is None or active is None or decision["policy_revision"] != self.scope.policy_revision
                or decision["system_version_id"] != active["version_id"]
                or active["artifact_hash"] != self.scope.system_version_sha256):
            raise AuthorityDenied("pilot decision version or owner-policy binding changed")
        request_digest = _digest({"intent": dict(row), "reservation": dict(reservation[0])})
        return row, Money(amount=_amount(reservation[0]["amount"]), currency=rules.quote_asset), request_digest

    def _envelope(self, authorization_id: str, grant: OwnerPilotAuthorization, proposed: Money,
                  *, excluding_effect: str | None = None) -> None:
        effects = self.database.execute("SELECT * FROM live_pilot_effects WHERE authorization_id=?",
                                        (authorization_id,)).fetchmany(10001)
        if len(effects) > 10000:
            raise ValidationFailure("pilot envelope history exceeds bounded window")
        commitments = tuple(Money(amount=_amount(item["maximum_native_cost"]), currency=item["native_currency"])
                            for item in effects if item["state"] in _HELD_EFFECTS
                            and item["effect_id"] != excluding_effect)
        expenses = self.database.execute(
            "SELECT amount,currency,created_at FROM budget_reservations WHERE deployment_id=? AND synthetic=0 "
            "AND state IN ('RESERVED','UNCERTAIN','COMMITTED','CONSERVATIVE','RECONCILED')",
            (self.scope.deployment_id,),
        ).fetchmany(10001)
        validate_pilot_envelope(grant, native_commitments=commitments, proposed_native_cost=proposed,
                                actual_expenses=tuple((Money(amount=_amount(item["amount"]),
                                                            currency=item["currency"]), parse_utc(item["created_at"]))
                                                      for item in expenses), now=self.clock.now())

    def reserve_increase(self, authorization_id: str, intent_id: str) -> str:
        """Persist a full-loss hold before a future protected dispatcher acts."""
        with self.database.immediate():
            row, grant = self._grant(authorization_id)
            if row["state"] != "ACTIVE" or not self._ready(row, grant):
                raise AuthorityDenied("active independently verified pilot authority required")
            intent, native_cost, digest = self._intent(intent_id, grant)
            existing = self.database.execute("SELECT * FROM live_pilot_effects WHERE intent_id=?",
                                             (intent_id,)).fetchone()
            if existing is not None:
                if (existing["authorization_id"] != authorization_id or existing["request_sha256"] != digest
                        or existing["generation"] != row["generation"] or existing["state"] != "PREPARED"):
                    raise AuthorityDenied("pilot intent cannot be replayed or replaced")
                self._envelope(authorization_id, grant, native_cost, excluding_effect=existing["effect_id"])
                return existing["effect_id"]
            if intent["state"] != "SUBMISSION_PENDING":
                raise AuthorityDenied("pilot intent must be unsent before reserving authority")
            self._envelope(authorization_id, grant, native_cost)
            effect_id, now = str(uuid.uuid4()), self._now()
            self.database.execute(
                "INSERT INTO live_pilot_effects VALUES (?,?,?,?,?,?,?,'PREPARED',?,?)",
                (effect_id, authorization_id, intent_id, row["generation"], digest, native_cost.currency,
                 canonical_decimal(native_cost.amount), now, now),
            )
            self._event(authorization_id, row["generation"], "effect_prepared", {"effect_id": effect_id})
            return effect_id

    def begin_submission(self, effect_id: str) -> None:
        """Durably consume one prepared generation before an external request."""
        with self.database.immediate():
            effect = self.database.execute("SELECT * FROM live_pilot_effects WHERE effect_id=?",
                                           (effect_id,)).fetchone()
            if effect is None:
                raise AuthorityDenied("unknown pilot effect")
            row, grant = self._grant(effect["authorization_id"])
            if (row["state"] != "ACTIVE" or effect["state"] != "PREPARED"
                    or effect["generation"] != row["generation"] or not self._ready(row, grant)):
                raise AuthorityDenied("pilot effect authority is revoked, consumed, or stale")
            intent, cost, digest = self._intent(effect["intent_id"], grant)
            if intent["state"] != "SUBMISSION_PENDING" or digest != effect["request_sha256"]:
                raise AuthorityDenied("pilot order changed after preparation")
            self._envelope(row["authorization_id"], grant, cost, excluding_effect=effect_id)
            self.database.execute("UPDATE live_pilot_effects SET state='SUBMITTING',updated_at=? WHERE effect_id=?",
                                  (self._now(), effect_id))
            self._event(row["authorization_id"], row["generation"], "effect_submitting", {"effect_id": effect_id})

    def _management_latch(self, grant: OwnerPilotAuthorization, reason: str) -> None:
        pause = self.database.execute("SELECT * FROM pause_states WHERE portfolio_id=?",
                                      (self.scope.portfolio_id,)).fetchone()
        # A grant may not weaken an already stronger explicit owner halt.
        profile = grant.stop_profile
        if pause is not None and pause["originator"] == "owner" and pause["profile"] in {"FLATTEN", "STOPPED"}:
            profile = pause["profile"]
        self.database.execute(
            """INSERT INTO pause_states
            (portfolio_id,profile,originator,reason,scope,requested_at,achieved,details_json)
            VALUES (?,?,'owner',?,'portfolio',?,'requested','{}')
            ON CONFLICT(portfolio_id) DO UPDATE SET profile=excluded.profile,originator='owner',
            reason=excluded.reason,requested_at=excluded.requested_at,achieved='requested'""",
            (self.scope.portfolio_id, profile, reason, self._now()),
        )

    def _halt(self, authorization_id: str, state: str, reason: str) -> None:
        if type(reason) is not str or not 1 <= len(reason) <= 512:
            raise ValidationFailure("bounded management reason required")
        with self.database.immediate():
            row, grant = self._grant(authorization_id)
            if (row["state"] in {state, "REVOKED"}
                    or (row["state"] == "STOPPED" and state != "REVOKED")):
                return
            self.database.execute("UPDATE live_pilot_grants SET state=?,generation=generation+1,updated_at=? "
                                  "WHERE authorization_id=?", (state, self._now(), authorization_id))
            self._management_latch(grant, reason)
            self._event(authorization_id, row["generation"] + 1, state.lower(), {"stop_profile": grant.stop_profile})

    def stop(self, authorization_id: str, *, reason: str) -> None:
        """Latch order/position management; retain every unresolved effect hold."""
        self._halt(authorization_id, "STOPPING", reason)

    def revoke(self, authorization_id: str, *, reason: str) -> None:
        """Irreversible for this authorization ID, with management still active."""
        self._halt(authorization_id, "REVOKED", reason)

    def recover(self, authorization_id: str) -> None:
        """Restart closes authority and retains unknown effects for reconciliation."""
        with self.database.immediate():
            row, grant = self._grant(authorization_id)
            if row["state"] in {"STOPPED", "REVOKED"}:
                return
            if row["state"] == "PENDING":
                return
            target = "STOPPING" if row["state"] == "STOPPING" else "RECOVERY_REQUIRED"
            self.database.execute("UPDATE live_pilot_grants SET state=?,generation=generation+1,updated_at=? "
                                  "WHERE authorization_id=?", (target, self._now(), authorization_id))
            self.database.execute("UPDATE live_pilot_effects SET state='UNKNOWN',updated_at=? "
                                  "WHERE authorization_id=? AND state IN ('PREPARED','SUBMITTING')",
                                  (self._now(), authorization_id))
            self._management_latch(grant, "pilot restart requires reconciliation and a new owner authorization")
            self._event(authorization_id, row["generation"] + 1, "recovered_closed", {})

    def _complete_account_history(self) -> bool:
        row = self.database.execute(
            "SELECT payload_json,created_at FROM activity_events WHERE kind='execution_reconciliation_health' "
            "AND json_extract(payload_json,'$.venue')=? AND json_extract(payload_json,'$.account_id')=? "
            "AND json_extract(payload_json,'$.mode')='live' ORDER BY rowid DESC LIMIT 1",
            (self.scope.venue, self.scope.account_id),
        ).fetchone()
        return bool(row and json.loads(row["payload_json"]).get("state") == "complete"
                    and timedelta(0) <= self.clock.now() - parse_utc(row["created_at"]) <= timedelta(seconds=60))

    def reconcile_effect(self, effect_id: str) -> str:
        """Resolve from durable native order/fill state; never accept an outcome flag."""
        with self.database.immediate():
            effect = self.database.execute("SELECT * FROM live_pilot_effects WHERE effect_id=?",
                                           (effect_id,)).fetchone()
            if effect is None:
                raise AuthorityDenied("unknown pilot effect")
            row, _ = self._grant(effect["authorization_id"])
            intent = self.database.execute(
                "SELECT state,payload_json,portfolio_id FROM order_intents WHERE intent_id=?",
                (effect["intent_id"],),
            ).fetchone()
            payload = json.loads(intent["payload_json"]) if intent else {}
            if (intent is None or intent["portfolio_id"] != self.scope.portfolio_id
                    or any(payload.get(key) != value for key, value in {
                        "venue": self.scope.venue, "account_id": self.scope.account_id, "mode": "live",
                        "symbol": self.scope.symbol,
                    }.items())):
                raise AuthorityDenied("pilot reconciliation account binding changed")
            fills = self.database.execute("SELECT 1 FROM fills WHERE intent_id=? LIMIT 1",
                                          (effect["intent_id"],)).fetchone()
            state = effect["state"]
            if intent["state"] in {"SUBMITTING", "UNKNOWN", "CANCEL_PENDING"}:
                state = "UNKNOWN"
            elif fills is not None or intent["state"] in {"OPEN", "PARTIALLY_FILLED", "FILLED"}:
                state = "COMMITTED"
            elif intent["state"] in {"REJECTED", "CANCELLED"}:
                if not self._complete_account_history():
                    raise StaleState("complete fresh account history required to release a pilot hold")
                # Once external exposure was acknowledged, conservatively retain
                # its full-loss commitment even if a later order label changes.
                state = "COMMITTED" if effect["state"] == "COMMITTED" else "RELEASED"
            self.database.execute("UPDATE live_pilot_effects SET state=?,updated_at=? WHERE effect_id=?",
                                  (state, self._now(), effect_id))
            self._event(row["authorization_id"], row["generation"], "effect_reconciled",
                        {"effect_id": effect_id, "state": state})
            return state

    def complete_stop(self, authorization_id: str) -> None:
        """Finish lifecycle only after fresh reconciliation proves no order/position."""
        with self.database.immediate():
            row, _ = self._grant(authorization_id)
            if row["state"] == "STOPPED":
                return
            if row["state"] not in {"STOPPING", "RECOVERY_REQUIRED"}:
                raise AuthorityDenied("stop completion requires a management lifecycle")
            unresolved = self.database.execute(
                "SELECT 1 FROM live_pilot_effects WHERE authorization_id=? "
                "AND state IN ('PREPARED','SUBMITTING','UNKNOWN') LIMIT 1", (authorization_id,),
            ).fetchone()
            intents = self.database.execute(
                "SELECT state FROM order_intents WHERE portfolio_id=? OR "
                "(json_extract(payload_json,'$.venue')=? AND json_extract(payload_json,'$.account_id')=? "
                "AND json_extract(payload_json,'$.mode')='live')",
                (self.scope.portfolio_id, self.scope.venue, self.scope.account_id),
            ).fetchmany(10001)
            books = Ledger(self.database, self.clock).books(self.scope.portfolio_id)
            instrument = self.database.execute("SELECT document_json FROM instruments WHERE venue=? AND symbol=?",
                                               (self.scope.venue, self.scope.symbol)).fetchone()
            if instrument is None:
                raise StaleState("native instrument required to verify a flat pilot")
            rules = InstrumentRules.model_validate_json(instrument["document_json"])
            inventory = (any(lot.open_quantity() != 0 for lot in books.lots)
                         or any(amount != 0 for asset, amount in books.cash.items() if asset != rules.quote_asset))
            if (unresolved is not None or len(intents) > 10000 or any(item["state"] in _OPEN_ORDERS for item in intents)
                    or inventory or not self._complete_account_history()):
                raise StaleState("pilot stop is incomplete until reconciled orders and positions are flat")
            self.database.execute("UPDATE live_pilot_grants SET state='STOPPED',generation=generation+1,updated_at=? "
                                  "WHERE authorization_id=?", (self._now(), authorization_id))
            self._event(authorization_id, row["generation"] + 1, "stopped_flat", {})
