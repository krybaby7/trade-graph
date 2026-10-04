"""Protected owner review of preserved native execution discrepancies.

An incident acknowledgement removes only its own reconciliation latch. It never
changes orders, reservations, books, pause management, pilot grants or live policy.
The HTTP owner service supplies its durable authenticated command transaction;
models and department RPCs have no route to this controller.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Context, Decimal, localcontext
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.ledger import Ledger
from trade_graph.application.venue_conformance import (
    MAX_CAPTURE_BYTES,
    STAGES,
    PinnedVenueObservation,
    _read,
)
from trade_graph.contracts.models import BalanceSnapshot, FillRecord, InstrumentRules
from trade_graph.domain.clock import Clock, parse_utc, utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState, TradeGraphError
from trade_graph.domain.money import canonical_decimal, parse_decimal
from trade_graph.live_gate import LivePilotScope, _canonical

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_KIND = "native_fill_execution_limit_discrepancy"
_DOMAIN = b"trade-graph.native-incident-owner-review.v1\0"
_UNRESOLVED = {"SUBMISSION_PENDING", "SUBMITTING", "UNKNOWN", "OPEN", "PARTIALLY_FILLED", "CANCEL_PENDING"}


class NativeIncidentResolutionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: Identifier
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    incident_id: Identifier
    expected_incident_sha256: Fingerprint
    expected_financial_sha256: Fingerprint
    expected_generation: Annotated[StrictInt, Field(ge=0)]
    acknowledgement: Literal["accept_preserved_native_effects_after_review"]
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class NativeIncidentRevocationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: Identifier
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    resolution_id: Identifier
    reason: Annotated[str, Field(min_length=1, max_length=500)]


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def incident_controller_sha256() -> str:
    package = Path(__file__).resolve().parents[1]
    files = (
        "application/incident_resolution.py",
        "application/ledger.py",
        "application/venue_conformance.py",
        "contracts/models.py",
        "kernel/books.py",
        "adapters/brokers/kraken_live.py",
    )
    return _sha({name: hashlib.sha256((package / name).read_bytes()).hexdigest() for name in files})


@dataclass(frozen=True)
class NativeProjectionVerification:
    financial_sha256: str
    observation_sha256: str
    observed_at: datetime
    local_projection_consistent: bool
    native_transport_authenticated: bool
    # Balance agreement and a complete trade scan do not authenticate all
    # deposits, withdrawals, transfers, adjustments or account ownership.
    complete_account_verified: Literal[False] = False


class ProtectedNativeIncidentResolver:
    """Concrete protected implementation, requiring independent retained facts.

    Its key and exact live scope are owner-pinned service configuration. The
    caller cannot inject a verifier, normalized summary or success boolean.
    """

    def __init__(
        self,
        database: Database,
        clock: Clock,
        *,
        scope: LivePilotScope,
        source: PinnedVenueObservation | None,
        receipt_key: bytes,
    ) -> None:
        if type(scope) is not LivePilotScope or (source is not None and type(source) is not PinnedVenueObservation):
            raise ValueError("exact protected scope and retained venue source required")
        if type(receipt_key) is not bytes or len(receipt_key) < 32:
            raise ValueError("protected incident receipt key requires at least 32 bytes")
        self.database, self.clock, self.scope, self.source = database, clock, scope, source
        self._key = receipt_key

    def _mac(self, kind: str, payload: dict) -> str:
        return hmac.new(self._key, _DOMAIN + kind.encode() + b"\0" + _canonical(payload), hashlib.sha256).hexdigest()

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        rows = self.database.execute(sql, params).fetchmany(10001)
        if len(rows) > 10000:
            raise StaleState("native incident projection exceeds bounded history")
        return [dict(row) for row in rows]

    def _financial_projection(self) -> tuple[str, dict, datetime]:
        """Reconstruct books and compare every actual journal group and fill."""
        scope = self.scope
        portfolio = self.database.execute(
            "SELECT * FROM portfolios WHERE portfolio_id=?", (scope.portfolio_id,)
        ).fetchone()
        if not portfolio or portfolio["mode"] != "live" or portfolio["status"] != "open":
            raise AuthorityDenied("exact open live incident portfolio required")
        instrument = self.database.execute(
            "SELECT * FROM instruments WHERE venue=? AND symbol=?", (scope.venue, scope.symbol)
        ).fetchone()
        if instrument is None:
            raise StaleState("native instrument is absent")
        rules = InstrumentRules.model_validate_json(instrument["document_json"])
        if (rules.venue, rules.symbol) != (scope.venue, scope.symbol):
            raise StaleState("native instrument identity differs from protected scope")
        policy = self.database.execute(
            "SELECT * FROM owner_policy_revisions WHERE revision_id=?", (scope.policy_revision,)
        ).fetchone()
        active = self.database.execute(
            "SELECT * FROM active_versions WHERE portfolio_id=?", (scope.portfolio_id,)
        ).fetchone()
        if (
            policy is None
            or hashlib.sha256(policy["document_json"].encode()).hexdigest() != scope.policy_sha256
            or active is None
            or active["artifact_hash"] != scope.system_version_sha256
        ):
            raise AuthorityDenied("current incident policy or system version differs from protected scope")
        current_policy = self.database.execute(
            "SELECT revision_id FROM owner_policy_revisions ORDER BY created_at DESC,rowid DESC LIMIT 1"
        ).fetchone()
        if not current_policy or current_policy["revision_id"] != scope.policy_revision:
            raise AuthorityDenied("incident owner policy is obsolete")
        params = (scope.portfolio_id, scope.venue, scope.account_id)
        intents = self._rows(
            "SELECT * FROM order_intents WHERE portfolio_id=? OR (json_extract(payload_json,'$.venue')=? "
            "AND json_extract(payload_json,'$.account_id')=? AND json_extract(payload_json,'$.mode')='live') "
            "ORDER BY intent_id",
            params,
        )
        for row in intents:
            payload = json.loads(row["payload_json"])
            if (
                row["portfolio_id"] != scope.portfolio_id
                or row["symbol"] != scope.symbol
                or any(
                    payload.get(name) != value
                    for name, value in {
                        "venue": scope.venue,
                        "account_id": scope.account_id,
                        "mode": "live",
                        "symbol": scope.symbol,
                        "portfolio_id": scope.portfolio_id,
                    }.items()
                )
                or row["state"] in _UNRESOLVED
            ):
                raise StaleState("unknown, open or differently scoped native orders require management")
        fills = self._rows(
            "SELECT * FROM fills WHERE portfolio_id=? OR (venue=? AND account_id=?) ORDER BY trade_id", params
        )
        events = self._rows("SELECT * FROM ledger_events WHERE portfolio_id=? ORDER BY sequence", (scope.portfolio_id,))
        if [row["sequence"] for row in events] != list(range(1, len(events) + 1)):
            raise StaleState("native financial event sequence is incomplete")
        cutoff = max(
            (
                parse_utc(row[field])
                for rows, field in ((events, "effective_at"), (fills, "created_at"), (intents, "updated_at"))
                for row in rows
            ),
            default=parse_utc(portfolio["created_at"]),
        )
        if cutoff > self.clock.now():
            raise StaleState("native financial projection contains future facts")
        ledger_fills = {}
        for row in events:
            if row["kind"] == "fill":
                document = json.loads(row["payload_json"])
                fill = FillRecord.model_validate(document["fill"])
                if (
                    fill.trade_id in ledger_fills
                    or document["base_asset"] != rules.base_asset
                    or document["quote_asset"] != rules.quote_asset
                    or row["external_ref"] != f"{scope.venue}:{scope.account_id}:{fill.trade_id}"
                ):
                    raise StaleState("native financial fills are duplicated")
                ledger_fills[fill.trade_id] = fill
        local_fills = {}
        intent_ids = {item["intent_id"] for item in intents}
        for row in fills:
            fill = FillRecord.model_validate_json(row["document_json"])
            if (
                row["portfolio_id"] != scope.portfolio_id
                or fill.venue != scope.venue
                or fill.account_id != scope.account_id
                or fill.symbol != scope.symbol
                or fill.intent_id != row["intent_id"]
                or fill.trade_id != row["trade_id"]
                or fill.intent_id not in intent_ids
            ):
                raise StaleState("native financial fill identity differs from protected scope")
            local_fills[fill.trade_id] = fill
        if local_fills != ledger_fills:
            raise StaleState("native fills differ from reconstructed financial events")
        ledger = Ledger(self.database, self.clock)
        with localcontext(Context(prec=100)):
            books = ledger.books(scope.portfolio_id)
            expected_groups = Counter(
                tuple(sorted((p.account, p.asset, canonical_decimal(p.amount)) for p in group))
                for group in books.groups
            )
            transactions = self._rows(
                "SELECT * FROM journal_transactions WHERE portfolio_id=? ORDER BY transaction_id", (scope.portfolio_id,)
            )
            postings = self._rows(
                "SELECT * FROM journal_postings WHERE portfolio_id=? ORDER BY posting_id", (scope.portfolio_id,)
            )
            transaction_ids = {tx["transaction_id"] for tx in transactions}
            grouped_postings = defaultdict(list)
            for posting in postings:
                grouped_postings[posting["transaction_id"]].append(
                    (posting["account"], posting["asset"], canonical_decimal(parse_decimal(posting["amount"])))
                )
            actual_groups = Counter(
                tuple(sorted(grouped_postings[transaction_id])) for transaction_id in transaction_ids
            )
            if actual_groups != expected_groups or any(p["transaction_id"] not in transaction_ids for p in postings):
                raise StaleState("native journal differs from the complete reconstructed financial projection")
            amounts = dict(books.cash)
            for lot in books.lots:
                amounts[lot.asset] = amounts.get(lot.asset, Decimal(0)) + lot.open_quantity()
        reservations = self._rows(
            "SELECT * FROM position_reservations WHERE portfolio_id=? ORDER BY reservation_id", (scope.portfolio_id,)
        )
        if any(row["state"] == "held" and parse_decimal(row["amount"]) != 0 for row in reservations):
            raise StaleState("unknown native financial holds cannot be acknowledged away")
        pilot_effects = self._rows(
            "SELECT e.* FROM live_pilot_effects e JOIN live_pilot_grants g USING(authorization_id) "
            "WHERE g.portfolio_id=? ORDER BY effect_id",
            (scope.portfolio_id,),
        )
        if any(row["state"] in {"PREPARED", "SUBMITTING", "UNKNOWN"} for row in pilot_effects):
            raise StaleState("unknown protected pilot effects require reconciliation")
        facts = {
            "scope": scope.model_dump(mode="json"),
            "portfolio": dict(portfolio),
            "policy": dict(policy),
            "version": dict(active),
            "instrument": dict(instrument),
            "intents": intents,
            "fills": fills,
            "events": events,
            "transactions": transactions,
            "postings": postings,
            "reservations": reservations,
            "pilot_effects": pilot_effects,
        }
        return _sha(facts), {"amounts": amounts, "fills": local_fills, "instrument": dict(instrument)}, cutoff

    def current_financial_sha256(self) -> str:
        with self.database.snapshot():
            return self._financial_projection()[0]

    def verify_current_projection(self) -> NativeProjectionVerification:
        """Replay exact retained sources, then compare real local financial facts."""
        if type(self.source) is not PinnedVenueObservation:
            raise StaleState("fresh protected native observation is unavailable")
        proof = self.source.verify(now=self.clock.now(), maximum_age_seconds=60)
        source_scope = proof.observation.scope.model_dump(mode="json")
        mode = source_scope.pop("mode")
        if (
            mode != "live"
            or source_scope != self.scope.model_dump(mode="json")
            or not proof.source_current
            or proof.observation.completed_stages != STAGES
        ):
            raise StaleState("current complete native observation scope is required")
        limited = {
            "historical_account_scope_limited",
            "order_lookup_unresolved",
            "native_stage_transport_evidence_incomplete",
        }
        if limited.intersection(proof.pending):
            raise StaleState("native observation history or order status is incomplete")
        raw = _read(self.source.path.parent / "native-summary.json", MAX_CAPTURE_BYTES)
        if hashlib.sha256(raw).hexdigest() != proof.observation.native_summary_sha256:
            raise StaleState("native observation summary changed after retained verification")
        summary = json.loads(raw)
        with self.database.snapshot():
            financial_sha256, local, cutoff = self._financial_projection()
            if proof.observation.started_at <= cutoff:
                raise StaleState("native observation must postdate every retained financial change")
            rules = InstrumentRules.model_validate_json(local["instrument"]["document_json"])
            native_rules = [InstrumentRules.model_validate(item) for item in summary["instruments"]]
            if native_rules != [rules]:
                raise StaleState("native instrument facts differ from protected local accounting")
            balance = BalanceSnapshot.model_validate(summary["balances"])
            if (balance.venue, balance.account_id) != (self.scope.venue, self.scope.account_id):
                raise StaleState("native balance identity mismatch")
            native_amounts = {asset: parse_decimal(amount) for asset, amount in balance.amounts.items()}
            if any(
                native_amounts.get(asset, Decimal(0)) != local["amounts"].get(asset, Decimal(0))
                for asset in native_amounts.keys() | local["amounts"].keys()
            ):
                raise StaleState("native balances differ from reconstructed account assets")
            if summary["open_orders"] or any(parse_decimal(value) != 0 for value in summary["held_balances"].values()):
                raise StaleState("native orders or held balances remain unresolved")
            native_fills = {}
            for item in summary["native_history"]:
                fill = FillRecord.model_validate(item)
                if fill.trade_id in native_fills:
                    raise StaleState("native history is duplicated")
                # Retained normalizer has no local resolver. Bind only the exact
                # matching native trade to its existing protected local intent.
                local_fill = local["fills"].get(fill.trade_id)
                if local_fill is None:
                    raise StaleState("native history includes an unaccounted fill")
                native_fills[fill.trade_id] = fill.model_copy(update={"intent_id": local_fill.intent_id})
            if native_fills != local["fills"]:
                raise StaleState("native history differs from current preserved fill facts")
        authenticated = {"BalanceEx", "OpenOrders", "TradesHistory"} <= set(proof.authenticated_reads) and all(
            item.transport_basis == "owned_https" for item in proof.observation.receipts
        )
        return NativeProjectionVerification(
            financial_sha256, self.source.observation_sha256, proof.observation.finished_at, True, authenticated
        )

    def _incident(self, incident_id: str) -> tuple[dict, str]:
        row = self.database.execute("SELECT * FROM activity_events WHERE event_id=?", (incident_id,)).fetchone()
        if row is None or row["kind"] != _KIND:
            raise AuthorityDenied("unknown native execution discrepancy")
        row = dict(row)
        payload = json.loads(row["payload_json"])
        if any(
            payload.get(name) != value
            for name, value in {"venue": self.scope.venue, "account_id": self.scope.account_id, "mode": "live"}.items()
        ):
            raise AuthorityDenied("native incident is outside protected account scope")
        if not Ledger(self.database, self.clock).activity_intact():
            raise StaleState("native incident activity chain is corrupt")
        return row, _sha(row)

    def _command(self, body, action: str) -> tuple[str, int]:
        if not self.database.connection.in_transaction:
            raise AuthorityDenied("native incident review requires an authenticated owner command transaction")
        command = self.database.execute(
            "SELECT * FROM dashboard_commands WHERE command_id=?", (body.request_id,)
        ).fetchone()
        scope = f"owner:{self.scope.deployment_id}"
        digest = hashlib.sha256(
            json.dumps(
                {
                    "action": action,
                    "body": body.model_dump(mode="json"),
                    "portfolio_id": self.scope.portfolio_id,
                    "deployment_id": self.scope.deployment_id,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        revision = self.database.execute(
            "SELECT revision FROM dashboard_control_state WHERE scope=?", (scope,)
        ).fetchone()
        evidence = self.database.execute(
            "SELECT * FROM dashboard_command_evidence WHERE command_id=?", (body.request_id,)
        ).fetchone()
        if (
            command is None
            or command["scope"] != scope
            or command["status"] != "PROCESSING"
            or command["request_hash"] != digest
            or revision is None
            or revision["revision"] != body.expected_revision + 1
            or evidence is None
            or evidence["action"] != action
            or evidence["revision"] != revision["revision"]
            or evidence["portfolio_id"] != self.scope.portfolio_id
            or evidence["deployment_id"] != self.scope.deployment_id
        ):
            raise AuthorityDenied("native incident review lacks its exact durable owner command binding")
        return body.request_id, revision["revision"]

    def resolve(self, body: NativeIncidentResolutionCommand) -> dict:
        if type(body) is not NativeIncidentResolutionCommand:
            raise AuthorityDenied("typed protected incident acknowledgement required")
        command_id, revision = self._command(body, "resolve-native-incident")
        incident, incident_sha256 = self._incident(body.incident_id)
        if incident_sha256 != body.expected_incident_sha256:
            raise StaleState("native incident compare-and-set pin changed")
        previous = self.database.execute(
            "SELECT * FROM native_incident_resolutions WHERE incident_id=? ORDER BY generation DESC LIMIT 1",
            (body.incident_id,),
        ).fetchone()
        generation = previous["generation"] if previous else 0
        if generation != body.expected_generation:
            raise StaleState("native incident resolution generation changed")
        pause = self.database.execute(
            "SELECT * FROM pause_states WHERE portfolio_id=?", (self.scope.portfolio_id,)
        ).fetchone()
        if pause is None or pause["profile"] == "RUNNING":
            raise AuthorityDenied("incident acknowledgement requires retained pause management")
        projection = self.verify_current_projection()
        if projection.financial_sha256 != body.expected_financial_sha256:
            raise StaleState("native financial projection compare-and-set pin changed")
        if projection.observed_at <= parse_utc(incident["created_at"]):
            raise StaleState("native observation must postdate the incident")
        receipt = {
            "schema_version": 1,
            "resolution_id": str(uuid.uuid4()),
            "incident_id": body.incident_id,
            "generation": generation + 1,
            "command_id": command_id,
            "owner_revision": revision,
            "owner_request_sha256": self.database.execute(
                "SELECT request_hash FROM dashboard_commands WHERE command_id=?", (command_id,)
            ).fetchone()[0],
            "scope": self.scope.model_dump(mode="json"),
            "incident_sha256": incident_sha256,
            "financial_sha256": projection.financial_sha256,
            "observation_sha256": projection.observation_sha256,
            "controller_sha256": incident_controller_sha256(),
            "observed_at": utc_iso(projection.observed_at),
            "created_at": utc_iso(self.clock.now()),
            "reason_sha256": hashlib.sha256(body.reason.encode()).hexdigest(),
            "acknowledgement": body.acknowledgement,
            "management_profile": pause["profile"],
            "native_transport_authenticated": projection.native_transport_authenticated,
            "clearance": "VERIFIED_NATIVE_EFFECTS"
            if projection.native_transport_authenticated
            else "PENDING_NATIVE_PROOF",
            "complete_account_verified": False,
            "execution_authority": False,
        }
        self.database.execute(
            "INSERT INTO native_incident_resolutions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                receipt["resolution_id"],
                body.incident_id,
                generation + 1,
                command_id,
                revision,
                self.scope.model_dump_json(),
                incident_sha256,
                projection.financial_sha256,
                projection.observation_sha256,
                receipt["controller_sha256"],
                _canonical(receipt).decode(),
                self._mac("resolution", receipt),
                receipt["created_at"],
            ),
        )
        return {
            "resolution_id": receipt["resolution_id"],
            "generation": generation + 1,
            "incident_acknowledged": True,
            "clearance": receipt["clearance"],
            "incident_cleared": projection.native_transport_authenticated,
            "execution_authority": False,
            "complete_account_verified": False,
            "management_continues": True,
        }

    def revoke(self, body: NativeIncidentRevocationCommand) -> dict:
        if type(body) is not NativeIncidentRevocationCommand:
            raise AuthorityDenied("typed protected incident revocation required")
        command_id, revision = self._command(body, "revoke-native-incident-resolution")
        row = self.database.execute(
            "SELECT * FROM native_incident_resolutions WHERE resolution_id=?", (body.resolution_id,)
        ).fetchone()
        if row is None or json.loads(row["scope_json"]) != self.scope.model_dump(mode="json"):
            raise AuthorityDenied("unknown protected incident resolution")
        previous = self.database.execute(
            "SELECT * FROM native_incident_resolution_revocations WHERE resolution_id=?", (body.resolution_id,)
        ).fetchone()
        if previous:
            return {"resolution_id": body.resolution_id, "revoked": True, "execution_authority": False}
        receipt = {
            "schema_version": 1,
            "revocation_id": str(uuid.uuid4()),
            "resolution_id": body.resolution_id,
            "command_id": command_id,
            "owner_revision": revision,
            "scope": self.scope.model_dump(mode="json"),
            "created_at": utc_iso(self.clock.now()),
            "reason_sha256": hashlib.sha256(body.reason.encode()).hexdigest(),
        }
        self.database.execute(
            "INSERT INTO native_incident_resolution_revocations VALUES (?,?,?,?,?,?,?)",
            (
                receipt["revocation_id"],
                body.resolution_id,
                command_id,
                revision,
                _canonical(receipt).decode(),
                self._mac("revocation", receipt),
                receipt["created_at"],
            ),
        )
        return {"resolution_id": body.resolution_id, "revoked": True, "execution_authority": False}

    def unresolved_incidents(self) -> tuple[str, ...]:
        """Restart verifies durable facts and signatures, never replays a write."""
        rows = self._rows(
            "SELECT event_id FROM activity_events WHERE kind=? "
            "AND json_extract(payload_json,'$.venue')=? AND json_extract(payload_json,'$.account_id')=? "
            "AND json_extract(payload_json,'$.mode')='live' ORDER BY rowid",
            (_KIND, self.scope.venue, self.scope.account_id),
        )
        if not rows:
            return ()
        try:
            financial_sha256 = self._financial_projection()[0]
        except (TradeGraphError, ValueError, TypeError, KeyError, ArithmeticError, sqlite3.DatabaseError):
            return tuple(row["event_id"] for row in rows)
        unresolved = []
        for item in rows:
            try:
                _, incident_sha256 = self._incident(item["event_id"])
                row = self.database.execute(
                    "SELECT * FROM native_incident_resolutions WHERE incident_id=? ORDER BY generation DESC LIMIT 1",
                    (item["event_id"],),
                ).fetchone()
                if row is None:
                    raise ValueError("unacknowledged incident")
                receipt = json.loads(row["receipt_json"])
                if (
                    not hmac.compare_digest(row["signature"], self._mac("resolution", receipt))
                    or receipt["scope"] != self.scope.model_dump(mode="json")
                    or receipt["incident_sha256"] != incident_sha256
                    or receipt["financial_sha256"] != financial_sha256
                    or receipt["controller_sha256"] != incident_controller_sha256()
                    or any(
                        row[name] != receipt[name]
                        for name in (
                            "resolution_id",
                            "incident_id",
                            "generation",
                            "command_id",
                            "owner_revision",
                            "incident_sha256",
                            "financial_sha256",
                            "observation_sha256",
                            "controller_sha256",
                            "created_at",
                        )
                    )
                    or json.loads(row["scope_json"]) != receipt["scope"]
                    or receipt["execution_authority"] is not False
                    or receipt["complete_account_verified"] is not False
                    or receipt["native_transport_authenticated"] is not True
                    or receipt["clearance"] != "VERIFIED_NATIVE_EFFECTS"
                    or self.database.execute(
                        "SELECT 1 FROM native_incident_resolution_revocations WHERE resolution_id=?",
                        (row["resolution_id"],),
                    ).fetchone()
                ):
                    raise ValueError("incident acknowledgement is no longer valid")
                command = self.database.execute(
                    "SELECT scope,status,request_hash,response_json FROM dashboard_commands WHERE command_id=?",
                    (row["command_id"],),
                ).fetchone()
                evidence = self.database.execute(
                    "SELECT action,revision,portfolio_id,deployment_id FROM dashboard_command_evidence "
                    "WHERE command_id=?",
                    (row["command_id"],),
                ).fetchone()
                if (
                    not command
                    or command["scope"] != f"owner:{self.scope.deployment_id}"
                    or command["status"] != "COMPLETE"
                    or command["request_hash"] != receipt["owner_request_sha256"]
                    or not evidence
                    or evidence["action"] != "resolve-native-incident"
                    or evidence["revision"] != row["owner_revision"]
                    or evidence["portfolio_id"] != self.scope.portfolio_id
                    or evidence["deployment_id"] != self.scope.deployment_id
                    or json.loads(command["response_json"])["resolution_id"] != row["resolution_id"]
                    or json.loads(command["response_json"])["revision"] != row["owner_revision"]
                ):
                    raise ValueError("owner acknowledgement receipt did not commit")
            except (TradeGraphError, ValueError, TypeError, KeyError, ArithmeticError, sqlite3.DatabaseError):
                unresolved.append(item["event_id"])
        return tuple(unresolved)

    def blocked(self) -> bool:
        with self.database.snapshot():
            return bool(self.unresolved_incidents())

    def review_inventory(self) -> dict:
        """Safe identifiers and CAS pins for authenticated owner review only."""
        with self.database.snapshot():
            financial = None
            try:
                financial = self._financial_projection()[0]
            except (TradeGraphError, ValueError, TypeError, KeyError, ArithmeticError):
                pass
            incidents = []
            for incident_id in self.unresolved_incidents():
                row, digest = self._incident(incident_id)
                prior = self.database.execute(
                    "SELECT MAX(generation) FROM native_incident_resolutions WHERE incident_id=?", (incident_id,)
                ).fetchone()[0]
                incidents.append(
                    {
                        "incident_id": incident_id,
                        "incident_sha256": digest,
                        "generation": prior or 0,
                        "created_at": row["created_at"],
                    }
                )
            return {"incidents": incidents, "financial_sha256": financial, "execution_authority": False}
