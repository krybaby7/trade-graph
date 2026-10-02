"""Actual financial services behind a durable, one-use mutable capability.

The confined strategy proposes an action. This parent independently supplies all
identity/version/authority fields and owns the database, execution and budget.
There is no mutable route to credentials, budget configuration or ledger writes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import timedelta
from decimal import Decimal

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.authority import AuthorityRecord
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import Decision, Quantity
from trade_graph.domain.clock import Clock, parse_utc, utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure
from trade_graph.domain.money import Money, canonical_decimal
from trade_graph.kernel.process_boundary import _bounded_number, _no_duplicate_keys
from trade_graph.kernel.runtime_manifest import ProtectedRuntimeManifest, canonical_json, document_sha256


class ProtectedFinancialService:
    """Trusted paper decision gateway; live authority is deliberately separate."""

    def __init__(self, database: Database, clock: Clock, execution: Execution, *,
                 manifest: ProtectedRuntimeManifest, capability_key: bytes) -> None:
        if type(capability_key) is not bytes or not 32 <= len(capability_key) <= 64:
            raise ValueError("private protected capability key requires 32..64 bytes")
        if execution.database is not database or execution.clock is not clock or execution.mode != "paper":
            raise AuthorityDenied("this protected runtime requires the bound paper execution service")
        manifest.assert_current()
        self.database, self.clock, self.execution = database, clock, execution
        self.ledger: Ledger = execution.ledger
        self.authority: AuthorityRecord = execution.authority
        self.budget = BudgetGateway(database, clock)
        self.manifest = manifest
        self._capability_key = capability_key

    def _instance(self, instance_id: str):
        row = self.database.execute("SELECT * FROM protected_runtime_instances WHERE instance_id=?",
                                    (instance_id,)).fetchone()
        if row is None or row["manifest_sha256"] != self.manifest.sha256:
            raise AuthorityDenied("protected runtime manifest binding mismatch")
        return row

    def _release(self, instance):
        row = self.database.execute("SELECT * FROM protected_mutable_releases WHERE release_id=?",
                                    (instance["active_release_id"],)).fetchone()
        if (row is None or row["manifest_sha256"] != self.manifest.sha256
                or row["source_sha256"] not in self.manifest.approved_source_sha256
                or hashlib.sha256(row["source_text"].encode()).hexdigest() != row["source_sha256"]):
            raise AuthorityDenied("active immutable release does not match owner manifest")
        return row

    def _snapshot(self, portfolio_id: str, symbol: str) -> dict:
        """One protected read view, including all concurrent exposure constraints."""
        portfolio = self.database.execute("SELECT * FROM portfolios WHERE portfolio_id=?",
                                         (portfolio_id,)).fetchone()
        if portfolio is None or portfolio["mode"] != "paper":
            raise AuthorityDenied("unknown or non-paper protected portfolio")
        policy = self.authority.active_policy()
        mandate = self.authority.active_mandate(portfolio_id)
        if symbol not in policy.allowed_symbols or symbol not in mandate.symbols:
            raise AuthorityDenied("snapshot symbol outside actual owner mandate")
        if self.execution.venue not in policy.allowed_venues:
            raise AuthorityDenied("bound execution venue outside actual owner policy")
        quote = self.execution.latest_observation(symbol, utc_iso(self.clock.now()), self.execution.venue)
        if quote is None or quote.bid is None or quote.ask is None:
            raise StaleState("no protected point-in-time quote")
        rules = self.execution.instrument(self.execution.venue, symbol)
        books = self.ledger.books(portfolio_id)
        def numeric(value: Decimal) -> str:
            return _bounded_number(str(value), Decimal("1000000000000"))

        public = {"symbol": symbol, "bid": numeric(quote.bid), "ask": numeric(quote.ask),
                  "cash": numeric(books.cash_amount(rules.quote_asset)),
                  "position_quantity": numeric(self.execution.owned_quantity(portfolio_id, rules.base_asset)),
                  "quote_observed_at": utc_iso(quote.event_time_utc)}
        # Do not send account IDs, owner budget documents, private journals or
        # order payloads to the child. Their digest still invalidates stale work.
        state = {"portfolio": dict(portfolio), "policy": policy.model_dump(mode="json"),
                 "mandate": mandate.model_dump(mode="json"), "quote": quote.model_dump(mode="json"),
                 "instrument": rules.model_dump(mode="json"), "pause": self.execution.pause(portfolio_id),
                 "execution": {"venue": self.execution.venue, "account_id": self.execution.account_id,
                               "mode": self.execution.mode,
                               "fee_reserve_rate": canonical_decimal(self.execution.fee_reserve_rate)}}
        state["other_quotes"] = {}
        for other_symbol in policy.allowed_symbols:
            other = self.execution.latest_observation(other_symbol, utc_iso(self.clock.now()), self.execution.venue)
            state["other_quotes"][other_symbol] = other.model_dump(mode="json") if other else None
        queries = {
            "ledger": ("SELECT sequence,kind,payload_json,effective_at,external_ref FROM ledger_events "
                       "WHERE portfolio_id=? ORDER BY sequence", (portfolio_id,)),
            "reservations": ("SELECT intent_id,asset,amount,state FROM position_reservations "
                             "WHERE portfolio_id=? ORDER BY reservation_id", (portfolio_id,)),
            "intents": ("SELECT intent_id,state,payload_json FROM order_intents "
                        "WHERE portfolio_id=? ORDER BY intent_id", (portfolio_id,)),
            "marks": ("SELECT * FROM valuation_marks WHERE portfolio_id=? ORDER BY mark_id", (portfolio_id,)),
            "fx": ("SELECT * FROM fx_rates ORDER BY rate_id", ()),
        }
        state_digest = hashlib.sha256(canonical_json(state).encode())
        rows, total_bytes = 0, 0
        for name, (query, params) in queries.items():
            state_digest.update(name.encode() + b"\0")
            for row in self.database.execute(query, params):
                encoded = canonical_json(dict(row)).encode()
                rows, total_bytes = rows + 1, total_bytes + len(encoded)
                if rows > 10000 or total_bytes > 8388608:
                    raise StaleState("protected snapshot history exceeds bounded replay window")
                state_digest.update(encoded + b"\0")
        return {"public": public, "state_sha256": state_digest.hexdigest(),
                "policy_revision": policy.revision_id, "mandate_revision": str(mandate.revision),
                "policy_sha256": document_sha256(policy.model_dump(mode="json")),
                "mandate_sha256": document_sha256(mandate.model_dump(mode="json"))}

    def issue(self, instance_id: str, portfolio_id: str, symbol: str) -> dict:
        """Persist an exact scope before mutable work or any financial effect."""
        self.manifest.assert_current()
        with self.database.immediate() as conn:
            instance = self._instance(instance_id)
            if instance["status"] != "RUNNING":
                raise AuthorityDenied("mutable runtime is management-only")
            if self.execution.profile(portfolio_id) != "RUNNING":
                raise AuthorityDenied("owner pause blocks new mutable decisions")
            release = self._release(instance)
            snapshot = self._snapshot(portfolio_id, symbol)
            request_id = secrets.token_hex(24)
            expires_at = utc_iso(self.clock.now() + timedelta(seconds=self.manifest.capability_ttl_seconds))
            scope = {"operation": "submit_decision", "instance_id": instance_id, "portfolio_id": portfolio_id,
                     "request_id": request_id, "manifest_sha256": self.manifest.sha256,
                     "release_id": release["release_id"], "source_sha256": release["source_sha256"],
                     "build_digest": release["build_digest"], "generation": instance["generation"],
                     "symbol": symbol, "expires_at": expires_at, **snapshot}
            capability = hmac.new(self._capability_key, canonical_json(scope).encode(), hashlib.sha256).hexdigest()
            now = utc_iso(self.clock.now())
            conn.execute("""INSERT INTO protected_rpc_requests
                (request_id,instance_id,capability_sha256,scope_json,expires_at,state,created_at,updated_at)
                VALUES (?,?,?,?,?,'ISSUED',?,?)""",
                (request_id, instance_id, hashlib.sha256(capability.encode()).hexdigest(),
                 canonical_json(scope), expires_at, now, now))
        return {"request_id": request_id, "capability": capability, "snapshot": snapshot["public"],
                "snapshot_id": snapshot["state_sha256"], "release_id": release["release_id"],
                "source_sha256": release["source_sha256"], "generation": instance["generation"],
                "expires_at": expires_at}

    def _decision(self, scope: dict, payload: object) -> Decision:
        required = {"action", "rationale", "invalidation", "horizon_seconds", "strategy_id"}
        optional = {"quantity", "limit_price", "stop_price", "time_in_force", "no_action_reason"}
        if type(payload) is not dict or not required <= payload.keys() or payload.keys() - required - optional:
            raise ValidationFailure("decision proposal has invalid business fields")
        if payload["action"] not in {"enter", "exit", "hold"}:
            raise AuthorityDenied("mutable action has no protected route")
        for name in ("rationale", "invalidation", "strategy_id"):
            if type(payload[name]) is not str or not 1 <= len(payload[name]) <= 1024:
                raise ValidationFailure("bounded decision text required")
        horizon = payload["horizon_seconds"]
        if type(horizon) is not int or not 1 <= horizon <= 2678400:
            raise ValidationFailure("bounded integer horizon required")
        document = dict(payload)
        rules = self.execution.instrument(self.execution.venue, scope["symbol"])
        for name in ("quantity", "limit_price", "stop_price"):
            if name in payload:
                normalized = _bounded_number(payload[name], Decimal("1000000000000"))
                amount = Decimal(normalized)
                if amount <= 0 or len(amount.as_tuple().digits) > 28:
                    raise ValidationFailure("positive representable native financial value required")
                document[name] = (Quantity(amount=amount, asset=rules.base_asset) if name == "quantity"
                                  else Money(amount=amount, currency=rules.quote_asset))
        if payload["action"] in {"enter", "exit"} and "quantity" not in document:
            raise ValidationFailure("trade proposal requires quantity")
        if payload["action"] == "hold" and any(name in document for name in ("quantity", "limit_price", "stop_price")):
            raise ValidationFailure("hold proposal cannot carry order values")
        if "no_action_reason" in document and (type(document["no_action_reason"]) is not str
                                               or len(document["no_action_reason"]) > 1024):
            raise ValidationFailure("bounded hold reason required")
        document.update(record_id=f"protected-{scope['request_id']}", created_at_utc=self.clock.now(),
                        run_id=scope["instance_id"], portfolio_id=scope["portfolio_id"], mode="paper",
                        system_version_id=scope["release_id"], trace_id=scope["request_id"],
                        symbol=scope["symbol"], snapshot_id=scope["state_sha256"],
                        policy_revision=scope["policy_revision"], mandate_revision=scope["mandate_revision"])
        return Decision.model_validate(document)

    def dispatch(self, raw: str) -> dict:
        """Apply once atomically, or return the identical durable previous result."""
        self.manifest.assert_current()
        if type(raw) is not str or len(raw.encode()) > self.manifest.maximum_output_bytes:
            raise ValidationFailure("protected RPC output exceeds bound")
        try:
            message = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
        except (ValueError, RecursionError) as exc:
            raise ValidationFailure("invalid protected RPC JSON") from exc
        if type(message) is not dict or set(message) != {"operation", "request_id", "capability", "decision"}:
            raise AuthorityDenied("protected RPC envelope has no allowed route")
        if (message["operation"] != "submit_decision" or type(message["request_id"]) is not str
                or type(message["capability"]) is not str or len(message["capability"]) != 64):
            raise AuthorityDenied("invalid protected RPC capability")
        request_sha256 = document_sha256(message["decision"])
        with self.database.immediate() as conn:
            row = conn.execute("SELECT * FROM protected_rpc_requests WHERE request_id=?",
                               (message["request_id"],)).fetchone()
            if row is None:
                raise AuthorityDenied("unknown protected capability")
            scope = json.loads(row["scope_json"])
            if scope.get("operation") != "submit_decision":
                raise AuthorityDenied("departmental capability cannot route a financial decision")
            expected = hmac.new(self._capability_key, canonical_json(scope).encode(), hashlib.sha256).hexdigest()
            if (not hmac.compare_digest(expected, message["capability"])
                    or hashlib.sha256(message["capability"].encode()).hexdigest() != row["capability_sha256"]):
                raise AuthorityDenied("protected capability authentication failed")
            if row["state"] == "APPLIED":
                if request_sha256 != row["request_sha256"]:
                    raise AuthorityDenied("capability cannot authorize a different replay")
                return json.loads(row["response_json"])
            if row["state"] != "ISSUED" or self.clock.now() >= parse_utc(row["expires_at"]):
                raise StaleState("protected capability revoked, consumed or expired")
            instance = self._instance(scope["instance_id"])
            release = self._release(instance)
            current = self._snapshot(scope["portfolio_id"], scope["symbol"])
            if (instance["status"] != "RUNNING" or instance["generation"] != scope["generation"]
                    or release["release_id"] != scope["release_id"]
                    or release["source_sha256"] != scope["source_sha256"]
                    or release["build_digest"] != scope["build_digest"]
                    or current != {key: scope[key] for key in current}):
                raise StaleState("protected authority, version or financial snapshot changed")
            decision = self._decision(scope, message["decision"])
            self.authority.require_for_decision(decision, venue=self.execution.venue, mode="paper")
            if decision.action == "hold":
                self.execution.record_non_order(scope["portfolio_id"], decision)
                intent_id = None
            else:
                intent_id = self.execution.authorize(scope["portfolio_id"], decision)
            response = {"status": "APPLIED", "request_id": scope["request_id"], "intent_id": intent_id,
                        "decision_id": decision.record_id, "release_id": scope["release_id"],
                        "live_authorization": False, "paid_authorization": False}
            conn.execute("""UPDATE protected_rpc_requests SET state='APPLIED', request_sha256=?,
                response_json=?,updated_at=? WHERE request_id=? AND state='ISSUED'""",
                (request_sha256, canonical_json(response), utc_iso(self.clock.now()), scope["request_id"]))
            return response

    def revoke(self, request_id: str) -> None:
        with self.database.immediate() as conn:
            conn.execute("UPDATE protected_rpc_requests SET state='REVOKED',updated_at=? "
                         "WHERE request_id=? AND state='ISSUED'", (utc_iso(self.clock.now()), request_id))

    async def reconcile(self) -> None:
        """Management uses only pinned trusted code, never a candidate recovery hook."""
        self.manifest.assert_current()
        await self.execution.reconcile()

    async def dispatch_outbox(self) -> int:
        self.manifest.assert_current()
        return await self.execution.dispatch()
