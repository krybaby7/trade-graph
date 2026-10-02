"""Protected runtime evidence collection, separate from external authentication.

Read the actual authoritative database, not candidate-provided export documents.
All declarations remain unverified as external facts: a receipt ID, synthetic
flag, source label or signing key cannot establish a credentialed provider run.
"""

from __future__ import annotations

import json
import stat
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Context, Decimal, localcontext
from hashlib import sha256
from typing import Literal

from pydantic import Field, field_validator, model_validator

from trade_graph.adapters.models.providers import AnthropicAdapter, OpenAIAdapter
from trade_graph.adapters.models.transport import provider_request_bytes
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import (
    Decision,
    FillRecord,
    ModelRequest,
    ModelResult,
    ModelUsage,
    Observation,
    PriceCard,
)
from trade_graph.domain.clock import Clock, parse_utc
from trade_graph.evaluation_contracts import (
    EvaluationContract,
    EvaluationSnapshot,
    ExpenseEvidence,
    Fingerprint,
    Reference,
    fixed_decimal,
)
from trade_graph.evaluation_registry import TrialRegistry, document_hash
from trade_graph.kernel.books import Books
from trade_graph.kernel.pricing import usage_cost

MAX_ROWS = 10000
MAX_TOTAL_ROWS = 20000
MAX_CELL_BYTES = 131072
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_CAPTURE_BYTES = 48 * 1024 * 1024
MAX_HISTORY_CAPTURES = 256
MAX_HISTORY_BYTES = 128 * 1024 * 1024
TABLES = (
    "schema_migrations", "portfolios", "ledger_events", "journal_transactions", "journal_postings",
    "fx_rates", "valuation_marks", "instruments", "observations", "decisions", "snapshots",
    "order_intents", "order_attempts", "fills", "position_reservations", "deployment_budget",
    "role_allocations", "budget_reservations", "usage_receipts", "cost_allocations", "price_cards",
    "invoice_reconciliations", "model_invocations", "tasks", "change_tasks", "engineering_jobs",
    "engineering_attempts", "engineering_commissions", "active_versions", "version_history",
    "component_fingerprints", "activity_events", "provider_transport_attempts",
    "owner_policy_revisions", "mandates", "pause_states", "outbox", "broker_orders",
    "protected_runtime_instances", "protected_mutable_releases", "protected_rpc_requests",
    "version_events", "artifact_bundles", "version_rollouts", "consumer_loads", "version_observations",
    "dashboard_commands", "dashboard_command_evidence", "dashboard_control_state",
)
IMMUTABLE_IDENTITIES = {
    "ledger_events": ("event_id",), "journal_transactions": ("transaction_id",),
    "journal_postings": ("posting_id",), "observations": ("observation_id",),
    "decisions": ("decision_id",), "snapshots": ("snapshot_id",), "fills": ("fill_id",),
    "usage_receipts": ("receipt_id",), "price_cards": ("price_card_id",),
    "valuation_marks": ("mark_id",), "fx_rates": ("rate_id",),
    "invoice_reconciliations": ("reconciliation_id",), "cost_allocations": ("receipt_id", "portfolio_id"),
    "version_history": ("portfolio_id", "version_id"), "activity_events": ("event_id",),
}


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _card_time(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if len(value) == 10:
        return result.replace(tzinfo=UTC)
    if result.tzinfo is None:
        raise ValueError("price timestamps need an explicit timezone or UTC calendar date")
    return result.astimezone(UTC)


class RuntimeCollectionBinding(EvaluationContract):
    schema_version: Literal[1] = 1
    trial_id: Reference
    deployment_id: Reference
    portfolio_id: Reference
    market_stream_id: Reference
    protocol_sha256: Fingerprint
    database_identity: tuple[int, int]
    bound_at: datetime
    initial_source_sha256: Fingerprint
    initial_source_json: str = Field(max_length=MAX_SOURCE_BYTES)

    @field_validator("bound_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("collection time requires a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def source_bound(self):
        if (len(self.initial_source_json.encode()) > MAX_SOURCE_BYTES
                or document_hash(self.initial_source_json) != self.initial_source_sha256):
            raise ValueError("runtime initial inventory digest mismatch")
        initial = json.loads(self.initial_source_json)
        if (not isinstance(initial, dict) or set(initial) != set(TABLES)
                or _canonical(initial) != self.initial_source_json):
            raise ValueError("runtime initial inventory must retain every required table canonically")
        return self


class RuntimeEvidenceCapture(EvaluationContract):
    schema_version: Literal[1] = 1
    binding: RuntimeCollectionBinding
    captured_at: datetime
    source_sha256: Fingerprint
    source_json: str = Field(max_length=MAX_SOURCE_BYTES)
    evaluation_snapshot: EvaluationSnapshot
    evaluation_snapshot_sha256: Fingerprint

    @field_validator("captured_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("capture time requires a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def consistent(self):
        if len(self.source_json.encode()) > MAX_SOURCE_BYTES:
            raise ValueError("runtime source inventory exceeds its byte bound")
        source = json.loads(self.source_json)
        if _canonical(source) != self.source_json or document_hash(self.source_json) != self.source_sha256:
            raise ValueError("runtime source inventory digest mismatch")
        if not isinstance(source, dict) or set(source) != set(TABLES):
            raise ValueError("runtime source inventory must retain every required table")
        snapshot = self.evaluation_snapshot
        if (snapshot.trial_id, snapshot.portfolio_id, snapshot.market_stream_id, snapshot.protocol_sha256) != (
            self.binding.trial_id, self.binding.portfolio_id, self.binding.market_stream_id,
            self.binding.protocol_sha256,
        ):
            raise ValueError("runtime capture and evaluation scope disagree")
        if document_hash(snapshot.model_dump_json()) != self.evaluation_snapshot_sha256:
            raise ValueError("runtime capture evaluation snapshot digest mismatch")
        if not self.binding.bound_at <= snapshot.collected_as_of <= self.captured_at:
            raise ValueError("runtime capture chronology is invalid")
        return self


class RuntimeVerification(EvaluationContract):
    runtime_source_sha256: Fingerprint
    evaluation_snapshot_sha256: Fingerprint
    protocol_sha256: Fingerprint
    report_sha256: Fingerprint
    inventory_sha256: Fingerprint | None
    source_manifest_sha256: Fingerprint
    database_consistent: bool = Field(strict=True)
    actual_external_provenance_verified: Literal[False] = False
    reasons: tuple[Reference, ...]
    receipt_ids: tuple[Reference, ...]
    synthetic_receipt_ids: tuple[Reference, ...]
    unresolved_reservation_ids: tuple[Reference, ...]


class RuntimeEvidenceCollector:
    """Operator-owned collector; no external calls, financial writes or trust flags.

    Retained captures include private requests/journals. Keep the registry outside
    mutable snapshots. Binding must precede collection, not be added retrospectively.
    """

    def __init__(self, database: Database, registry: TrialRegistry, clock: Clock, *, deployment_id: str):
        self.database, self.registry, self.clock = database, registry, clock
        self.deployment_id = deployment_id

    def _identity(self) -> tuple[int, int]:
        metadata = self.database.path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("runtime database must be a regular nonsymlink file")
        return metadata.st_dev, metadata.st_ino

    def _read(self) -> str:
        data, count = {}, 0
        size = len(_canonical({table: [] for table in TABLES}).encode())
        for table in TABLES:
            # Table identifiers are a fixed protected allowlist; LIMIT is not
            # pagination. Hitting a limit refuses the whole inventory.
            columns = self.database.execute(f"PRAGMA table_info({table})").fetchall()
            if not columns:
                raise ValueError("runtime required source table is missing")
            row_count = self.database.execute(
                f"SELECT COUNT(*) FROM (SELECT 1 FROM {table} LIMIT ?)", (MAX_ROWS + 1,),
            ).fetchone()[0]
            if row_count > MAX_ROWS or count + row_count > MAX_TOTAL_ROWS:
                raise ValueError("runtime inventory exceeds complete collection bounds")
            # Reject oversized cells in SQLite before fetching any payload bytes.
            # Identifiers come from the protected table schema and are quoted.
            names = ['"' + column["name"].replace('"', '""') + '"' for column in columns]
            oversized = " OR ".join(f"length(CAST({name} AS BLOB)) > ?" for name in names)
            unsupported = " OR ".join(f"typeof({name}) IN ('real','blob')" for name in names)
            if self.database.execute(f"SELECT 1 FROM {table} WHERE {oversized} LIMIT 1",
                                     (MAX_CELL_BYTES,) * len(names)).fetchone():
                raise ValueError("runtime source cell exceeds collection bounds")
            if self.database.execute(f"SELECT 1 FROM {table} WHERE {unsupported} LIMIT 1").fetchone():
                raise ValueError("runtime source contains unsupported binary or floating values")
            retained = []
            for row in self.database.execute(f"SELECT * FROM {table} ORDER BY rowid LIMIT ?", (MAX_ROWS + 1,)):
                count += 1
                if len(retained) >= MAX_ROWS or count > MAX_TOTAL_ROWS:
                    raise ValueError("runtime inventory exceeds complete collection bounds")
                for value in row:
                    if isinstance(value, float) or isinstance(value, bytes):
                        raise ValueError("runtime source contains unsupported binary or floating values")
                    if isinstance(value, str) and len(value.encode()) > MAX_CELL_BYTES:
                        raise ValueError("runtime source cell exceeds collection bounds")
                record = dict(row)
                size += len(_canonical(record).encode()) + int(bool(retained))
                if size > MAX_SOURCE_BYTES:
                    raise ValueError("runtime source exceeds complete byte collection bounds")
                retained.append(record)
            data[table] = retained
        raw = _canonical(data)
        if len(raw.encode()) > MAX_SOURCE_BYTES:
            raise ValueError("runtime source exceeds complete byte collection bounds")
        return raw

    def _binding(self, trial_id: str) -> RuntimeCollectionBinding:
        rows = self.registry._rows("runtime_binding", trial_id)
        if len(rows) != 1:
            raise ValueError("runtime collector must be bound before the forward horizon")
        binding = RuntimeCollectionBinding.model_validate_json(rows[0]["document_json"])
        protocol = self.registry.protocol(trial_id)
        registered_at = parse_utc(self.registry._rows("protocol", trial_id)[0]["collected_at"])
        collected_at = parse_utc(rows[0]["collected_at"])
        if (binding.deployment_id != self.deployment_id or binding.database_identity != self._identity()
                or binding.protocol_sha256 != document_hash(protocol.model_dump_json())
                or not registered_at <= binding.bound_at <= collected_at < protocol.forward_blocks[0].start):
            raise ValueError("runtime collection binding no longer matches its source")
        row = self.database.execute("SELECT mode, status FROM portfolios WHERE portfolio_id = ?",
                                    (binding.portfolio_id,)).fetchone()
        if row is None or row["mode"] != "paper" or row["status"] != "open":
            raise ValueError("runtime collection requires its open paper portfolio")
        return binding

    def _retained_capture(self, key: str, trial_id: str):
        # Read only this capture, never all historical multi-megabyte snapshots.
        size = self.registry.connection.execute(
            "SELECT length(CAST(document_json AS BLOB)) FROM evaluation_records "
            "WHERE kind='runtime_capture' AND record_key=? AND trial_id=?", (key, trial_id),
        ).fetchone()
        if size is not None and size[0] > MAX_CAPTURE_BYTES:
            raise ValueError("retained runtime capture exceeds verification byte bounds")
        row = self.registry.connection.execute(
            "SELECT document_json,document_sha256,collected_at FROM evaluation_records "
            "WHERE kind='runtime_capture' AND record_key=? AND trial_id=?", (key, trial_id),
        ).fetchone()
        if row is not None and document_hash(row["document_json"]) != row["document_sha256"]:
            raise ValueError("retained runtime capture bytes changed")
        return row

    def _history_bounds(self, *, extra_bytes: int = 0, extra_count: int = 0):
        count = self.registry.connection.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM evaluation_records WHERE kind='runtime_capture' "
            "LIMIT ?)", (MAX_HISTORY_CAPTURES + 1,),
        ).fetchone()[0]
        if count + extra_count > MAX_HISTORY_CAPTURES:
            raise ValueError("runtime retained capture history exceeds verification bounds")
        sizes = self.registry.connection.execute(
            "SELECT COALESCE(SUM(length(CAST(document_json AS BLOB))),0),"
            "COALESCE(MAX(length(CAST(document_json AS BLOB))),0) FROM evaluation_records "
            "WHERE kind='runtime_capture'",
        ).fetchone()
        if (sizes[0] + extra_bytes > MAX_HISTORY_BYTES or sizes[1] > MAX_CAPTURE_BYTES
                or extra_bytes > MAX_CAPTURE_BYTES):
            raise ValueError("runtime retained capture history exceeds verification byte bounds")

    @staticmethod
    def _source_facts(data):
        facts = {}

        def retain(kind, identity, record):
            key = kind, document_hash(_canonical(identity))
            value = document_hash(_canonical(record))
            if key in facts and facts[key] != value:
                raise ValueError("ambiguous immutable runtime source identity")
            facts[key] = value

        for table, columns in IMMUTABLE_IDENTITIES.items():
            for row in data[table]:
                retain(table, [row[column] for column in columns], row)
        for table, identity, mutable in (
            ("budget_reservations", "reservation_id", {"amount", "state", "updated_at"}),
            ("model_invocations", "invocation_id", {"state", "result_json", "updated_at"}),
            ("provider_transport_attempts", "attempt_id", {
                "outcome", "response_sha256", "status_code", "response_bytes", "error_category", "finished_at",
            }),
        ):
            for row in data[table]:
                retain(table + "_identity", [row[identity]], {key: value for key, value in row.items()
                                                           if key not in mutable})
                if table == "provider_transport_attempts" and row["outcome"] == "HTTP_RESPONSE":
                    retain("complete_provider_response", [row[identity]], row)
                if table == "model_invocations" and row["result_json"] is not None:
                    retain("retained_provider_result", [row[identity]], {key: value for key, value in row.items()
                                                                      if key not in {"state", "updated_at"}})
                if table == "budget_reservations" and row["state"] in {"COMMITTED", "CONSERVATIVE", "RECONCILED"}:
                    retain("retained_paid_obligation", [row[identity]], {key: value for key, value in row.items()
                                                                      if key not in {"state", "updated_at"}})
        return facts

    def _history_consistency(self, data, binding, reasons):
        self._history_bounds()
        current = self._source_facts(data)
        initial = self._source_facts(json.loads(binding.initial_source_json))
        if any(current.get(key) != value for key, value in initial.items()):
            reasons.append("invalid:captured_runtime_history_changed")
        rows = self.registry.connection.execute(
            "SELECT record_key,document_json,document_sha256,collected_at FROM evaluation_records "
            "WHERE kind='runtime_capture' ORDER BY collected_at,record_key",
        )
        for row in rows:
            raw = row["document_json"]
            if document_hash(raw) != row["document_sha256"] or row["record_key"] != document_hash(raw):
                raise ValueError("retained runtime capture history bytes changed")
            previous = RuntimeEvidenceCapture.model_validate_json(raw)
            retained_bindings = self.registry._rows("runtime_binding", previous.binding.trial_id)
            if (len(retained_bindings) != 1
                    or RuntimeCollectionBinding.model_validate_json(retained_bindings[0]["document_json"])
                    != previous.binding or previous.binding.deployment_id != binding.deployment_id
                    or previous.binding.database_identity != binding.database_identity
                    or not previous.captured_at <= parse_utc(row["collected_at"]) <= self.clock.now()):
                raise ValueError("retained runtime capture history scope or chronology changed")
            historical = self._source_facts(json.loads(previous.source_json))
            if any(current.get(key) != value for key, value in historical.items()):
                reasons.append("invalid:captured_runtime_history_changed")

    @staticmethod
    def _initial_chronology(data, at):
        timestamps = {
            "portfolios": ("created_at",), "ledger_events": ("effective_at",),
            "journal_transactions": ("created_at",), "observations": ("event_time", "available_at"),
            "decisions": ("created_at",), "snapshots": ("as_of", "created_at"), "fills": ("created_at",),
            "usage_receipts": ("created_at",), "budget_reservations": ("created_at", "updated_at"),
            "model_invocations": ("created_at", "updated_at"),
            "provider_transport_attempts": ("started_at", "finished_at"),
            "invoice_reconciliations": ("created_at",), "activity_events": ("created_at",),
            "valuation_marks": ("observed_at",), "fx_rates": ("observed_at", "valid_as_of", "retrieved_at"),
            "price_cards": ("created_at",),
        }
        for table, columns in timestamps.items():
            for row in data[table]:
                if any(row[column] is not None and parse_utc(row[column]) > at for column in columns):
                    return False
        for row in data["decisions"]:
            if Decision.model_validate_json(row["payload_json"]).created_at_utc > at:
                return False
        for row in data["observations"]:
            value = Observation.model_validate_json(row["document_json"])
            if max(value.event_time_utc, value.available_at_utc) > at:
                return False
        for row in data["snapshots"]:
            for market in json.loads(row["payload_json"]).get("market", {}).values():
                if market.get("observation") is not None:
                    value = Observation.model_validate(market["observation"])
                    if max(value.event_time_utc, value.available_at_utc) > at:
                        return False
        for row in data["fills"]:
            if FillRecord.model_validate_json(row["document_json"]).filled_at_utc > at:
                return False
        for row in data["ledger_events"]:
            if (row["kind"] == "fill"
                    and FillRecord.model_validate(json.loads(row["payload_json"])["fill"]).filled_at_utc > at):
                return False
        return True

    def bind(self, trial_id: str) -> RuntimeCollectionBinding:
        with self.registry._atomic(), self.database.snapshot():
            if self.registry._rows("runtime_binding", trial_id):
                return self._binding(trial_id)
            protocol = self.registry.protocol(trial_id)
            if self.registry.observations(trial_id) or self.clock.now() >= protocol.forward_blocks[0].start:
                raise ValueError("runtime collection must be bound before untouched forward data")
            if self.clock.now() < parse_utc(self.registry._rows("protocol", trial_id)[0]["collected_at"]):
                raise ValueError("runtime collector clock precedes preregistration")
            row = self.database.execute("SELECT mode, status FROM portfolios WHERE portfolio_id = ?",
                                        (protocol.portfolio_id,)).fetchone()
            if row is None or row["mode"] != "paper" or row["status"] != "open":
                raise ValueError("runtime collection requires the registered paper portfolio")
            raw = self._read()
            if not self._initial_chronology(json.loads(raw), self.clock.now()):
                raise ValueError("runtime binding contains future collected facts")
            binding = RuntimeCollectionBinding(
                trial_id=trial_id, deployment_id=self.deployment_id, portfolio_id=protocol.portfolio_id,
                market_stream_id=protocol.market_stream_id, protocol_sha256=document_hash(protocol.model_dump_json()),
                database_identity=self._identity(), bound_at=self.clock.now(), initial_source_sha256=document_hash(raw),
                initial_source_json=raw,
            )
            self.registry._append("runtime_binding", trial_id, trial_id, binding)
            return binding

    def capture(self, trial_id: str) -> RuntimeEvidenceCapture:
        # EvaluationSnapshot has its own consistent registry transaction. Source
        # changes between it and capture are refused by verify_snapshot below.
        with self.registry._atomic(), self.database.snapshot():
            self._binding(trial_id)
        snapshot = self.registry.snapshot(trial_id)
        with self.registry._atomic(), self.database.snapshot():
            binding = self._binding(trial_id)
            if snapshot.source_records != self.registry._source_bindings():
                raise ValueError("evaluation source changed during runtime capture")
            raw = self._read()
            capture = RuntimeEvidenceCapture(
                binding=binding, captured_at=self.clock.now(), source_json=raw, source_sha256=document_hash(raw),
                evaluation_snapshot=snapshot, evaluation_snapshot_sha256=document_hash(snapshot.model_dump_json()),
            )
            key = document_hash(capture.model_dump_json())
            retained = self._retained_capture(key, trial_id)
            if retained is not None and retained["document_json"] != capture.model_dump_json():
                raise ValueError("retained runtime capture bytes changed")
            if retained is None:
                self._history_bounds(extra_bytes=len(capture.model_dump_json().encode()), extra_count=1)
                self.registry._append("runtime_capture", key, trial_id, capture)
            return capture

    def verify(self, capture: RuntimeEvidenceCapture) -> RuntimeVerification:
        capture = RuntimeEvidenceCapture.model_validate(capture.model_dump())
        self.registry.verify_snapshot(capture.evaluation_snapshot)
        with self.registry._atomic(), self.database.snapshot(), localcontext(Context(prec=100)):
            binding = self._binding(capture.binding.trial_id)
            if binding != capture.binding or capture.captured_at > self.clock.now():
                raise ValueError("runtime capture scope or availability changed")
            key = document_hash(capture.model_dump_json())
            retained = self._retained_capture(key, binding.trial_id)
            if (retained is None or retained["document_json"] != capture.model_dump_json()
                    or not capture.captured_at <= parse_utc(retained["collected_at"]) <= self.clock.now()):
                raise ValueError("runtime capture is not retained by this collector")
            if capture.evaluation_snapshot.source_records != self.registry._source_bindings():
                raise ValueError("evaluation evidence changed during runtime verification")
            if self._read() != capture.source_json:
                raise ValueError("runtime evidence is stale; capture the complete current source again")
            reasons, receipts, synthetic, unresolved = self._audit(json.loads(capture.source_json), binding)
            derived = {item.receipt_id: item for item in self._derived_expenses(json.loads(capture.source_json))}
            for imported in self.registry.expenses():
                if imported.receipt_id in derived and imported != derived[imported.receipt_id]:
                    reasons.append("invalid:runtime_registry_expense_link")
            snapshot = capture.evaluation_snapshot
            return RuntimeVerification(
                runtime_source_sha256=capture.source_sha256,
                evaluation_snapshot_sha256=capture.evaluation_snapshot_sha256,
                protocol_sha256=snapshot.protocol_sha256, report_sha256=snapshot.report_sha256,
                inventory_sha256=snapshot.inventory_sha256, source_manifest_sha256=snapshot.source_manifest_sha256,
                database_consistent=not any(item.startswith("invalid:") for item in reasons),
                reasons=tuple(sorted(set(reasons))), receipt_ids=tuple(sorted(receipts)),
                synthetic_receipt_ids=tuple(sorted(synthetic)), unresolved_reservation_ids=tuple(sorted(unresolved)),
            )

    def _audit(self, data: dict, binding: RuntimeCollectionBinding):
        reasons = ["external_transport_provenance_missing", "complete_provider_invoice_export_missing",
                   "baseline_runtime_collection_missing", "independence_and_regime_source_verification_pending"]
        ledger = Ledger(self.database, self.clock)
        initial = json.loads(binding.initial_source_json)
        if not self._initial_chronology(initial, binding.bound_at):
            reasons.append("invalid:future_native_facts_at_preregistration")
        self._history_consistency(data, binding, reasons)
        for table in ("ledger_events", "journal_transactions", "journal_postings", "observations",
                      "decisions", "snapshots", "fills", "usage_receipts", "price_cards", "valuation_marks"):
            if data[table][:len(initial[table])] != initial[table]:
                reasons.append("invalid:preregistered_runtime_history_changed")
        if not ledger.activity_intact():
            reasons.append("invalid:runtime_activity_chain")
        postings = defaultdict(Decimal)
        portfolios = {row["portfolio_id"] for row in data["portfolios"]}
        transactions = {row["transaction_id"]: row for row in data["journal_transactions"]}
        events = {(row["portfolio_id"], row["external_ref"]): row for row in data["ledger_events"]}
        if len(events) != len(data["ledger_events"]):
            reasons.append("invalid:ambiguous_native_event_identity")
        posting_groups = defaultdict(list)
        for row in data["journal_postings"]:
            postings[(row["transaction_id"], row["asset"])] += fixed_decimal(row["amount"])
            transaction = transactions.get(row["transaction_id"])
            if (transaction is None or row["portfolio_id"] not in portfolios
                    or row["portfolio_id"] != transaction["portfolio_id"]):
                reasons.append("invalid:native_posting_transaction_ownership")
            posting_groups[row["transaction_id"]].append((row["account"], row["asset"], fixed_decimal(row["amount"])))
        if any(amount != 0 for amount in postings.values()):
            reasons.append("invalid:unbalanced_native_journal")
        event_transactions = defaultdict(list)
        for transaction in transactions.values():
            identity = transaction["portfolio_id"], transaction["external_ref"]
            event = events.get(identity)
            if (transaction["portfolio_id"] not in portfolios or event is None
                    or transaction["kind"] != event["kind"]
                    or parse_utc(transaction["created_at"]) != parse_utc(event["effective_at"])):
                reasons.append("invalid:native_transaction_event_link")
            event_transactions[identity].append(sorted(posting_groups[transaction["transaction_id"]]))
        if any(row["portfolio_id"] not in portfolios for row in data["ledger_events"]):
            reasons.append("invalid:native_event_portfolio_ownership")
        for portfolio in data["portfolios"]:
            identity = portfolio["portfolio_id"]
            # Replay the protected native ledger and compare every posting group,
            # with its exact event metadata, rather than matching global zero sums.
            rows = sorted((row for row in data["ledger_events"] if row["portfolio_id"] == identity),
                          key=lambda row: row["sequence"])
            if [row["sequence"] for row in rows] != list(range(1, len(rows) + 1)):
                reasons.append("invalid:native_event_sequence")
            books = Books()
            with localcontext(Context(prec=28)):
                for row in rows:
                    before = len(books.groups)
                    ledger._mutate(books, row["kind"], json.loads(row["payload_json"]),
                                   row["effective_at"], row["external_ref"])
                    derived = [sorted((item.account, item.asset, item.amount) for item in group)
                               for group in books.groups[before:]]
                    if event_transactions[(identity, row["external_ref"])] != derived:
                        reasons.append("invalid:native_journal_does_not_match_event_replay")
        if any(parse_utc(row["effective_at"]) > self.clock.now() for row in data["ledger_events"]):
            reasons.append("invalid:future_native_ledger_event")
        fills = {(row["venue"], row["account_id"], row["trade_id"]): row for row in data["fills"]}
        linked = set()
        for row in data["ledger_events"]:
            if row["kind"] == "fill":
                fill = FillRecord.model_validate(json.loads(row["payload_json"])["fill"])
                identity = (fill.venue, fill.account_id, fill.trade_id)
                retained = fills.get(identity)
                if (retained is None or retained["portfolio_id"] != row["portfolio_id"]
                        or FillRecord.model_validate_json(retained["document_json"]) != fill):
                    reasons.append("invalid:fill_native_ledger_link")
                linked.add(identity)
            if row["kind"] == "expense":
                reasons.append("embedded_or_external_ledger_expense_receipt_link_requires_verification")
        if set(fills) != linked:
            reasons.append("invalid:fill_without_native_ledger_event")
        for row in data["observations"]:
            value = Observation.model_validate_json(row["document_json"])
            if (value.observation_id != row["observation_id"] or value.venue != row["venue"]
                    or value.symbol != row["symbol"] or value.available_at_utc > self.clock.now()
                    or value.event_time_utc > value.available_at_utc
                    or parse_utc(row["available_at"]) != value.available_at_utc
                    or parse_utc(row["event_time"]) != value.event_time_utc):
                reasons.append("invalid:point_in_time_market_observation")
            if value.source.startswith("synthetic") or value.venue in {"paper", "replay"}:
                reasons.append("synthetic_or_paper_market_reference")
        snapshots = {row["snapshot_id"]: row for row in data["snapshots"]}
        versions = {(row["portfolio_id"], row["version_id"]): row["artifact_hash"] for row in data["version_history"]}
        observations = {row["observation_id"]: Observation.model_validate_json(row["document_json"])
                        for row in data["observations"]}
        protocol = self.registry.protocol(binding.trial_id)
        for row in data["decisions"]:
            if row["portfolio_id"] != binding.portfolio_id:
                continue
            source = snapshots.get(row["snapshot_id"])
            decision = Decision.model_validate_json(row["payload_json"])
            if (decision.record_id != row["decision_id"] or decision.snapshot_id != row["snapshot_id"]
                    or decision.system_version_id != row["system_version_id"] or decision.action != row["action"]
                    or decision.mode != "paper" or decision.portfolio_id != binding.portfolio_id
                    or decision.task_id != row["task_id"] or decision.mandate_revision != row["mandate_revision"]
                    or decision.policy_revision != row["policy_revision"]
                    or not decision.created_at_utc <= parse_utc(row["created_at"]) <= self.clock.now()):
                reasons.append("invalid:decision_native_record_link")
            if source is None or source["portfolio_id"] != row["portfolio_id"]:
                reasons.append("invalid:decision_snapshot_link")
            else:
                context = json.loads(source["payload_json"])
                as_of = parse_utc(source["as_of"])
                if (not as_of <= parse_utc(source["created_at"]) <= decision.created_at_utc
                        or context.get("system_version_id") != row["system_version_id"]):
                    reasons.append("invalid:decision_point_in_time_snapshot")
                for market in context.get("market", {}).values():
                    quote = market.get("observation")
                    if quote is None:
                        reasons.append("decision_market_reference_missing")
                        continue
                    quote = Observation.model_validate(quote)
                    if observations.get(quote.observation_id) != quote or quote.available_at_utc > as_of:
                        reasons.append("invalid:decision_point_in_time_market_link")
            version = versions.get((row["portfolio_id"], row["system_version_id"]))
            if version is None:
                reasons.append("invalid:decision_version_link")
            elif version not in protocol.allowed_versions_sha256:
                reasons.append("invalid:decision_version_outside_preregistration")
        reservations = {row["reservation_id"]: row for row in data["budget_reservations"]
                        if row["deployment_id"] == self.deployment_id}
        invocations = {row["reservation_id"]: row for row in data["model_invocations"]}
        attempts = {row["reservation_id"]: row for row in data["provider_transport_attempts"]}
        cards = {row["price_card_id"]: row for row in data["price_cards"]}
        receipts, synthetic, unresolved = [], [], []
        receipt_reservations = set()
        for row in data["usage_receipts"]:
            reservation = reservations.get(row["reservation_id"])
            if reservation is None:
                if not any(item["reservation_id"] == row["reservation_id"] for item in data["budget_reservations"]):
                    reasons.append("invalid:orphan_usage_receipt")
                continue
            receipts.append(row["receipt_id"])
            if row["reservation_id"] in receipt_reservations:
                reasons.append("invalid:duplicate_attempt_receipts")
            receipt_reservations.add(row["reservation_id"])
            if row["synthetic"] or reservation["synthetic"]:
                synthetic.append(row["receipt_id"])
                reasons.append("synthetic_runtime_receipts")
            if row["synthetic"] != reservation["synthetic"]:
                reasons.append("invalid:receipt_namespace_mismatch")
            if row["status"] != "committed" or reservation["state"] not in {"COMMITTED", "RECONCILED"}:
                unresolved.append(row["reservation_id"])
                reasons.append("unresolved_usage_or_invoice_cost")
                continue
            card_row = cards.get(reservation["price_card_id"])
            if card_row is None:
                reasons.append("invalid:missing_receipt_price_card")
                continue
            card = PriceCard.model_validate_json(card_row["document_json"])
            if card.source_id.startswith(("synthetic", "fixture")):
                reasons.append("synthetic_price_source")
            if (max(_card_time(card.effective_at), _card_time(card.verified_at))
                    > parse_utc(reservation["created_at"])
                    or parse_utc(card_row["created_at"]) > parse_utc(reservation["created_at"])):
                reasons.append("invalid:receipt_price_unavailable_at_dispatch")
            for name in ("input_per_million", "output_per_million", "cache_read_per_million",
                         "cache_write_per_million", "search_per_call"):
                if getattr(card, name) is not None:
                    fixed_decimal(getattr(card, name))
            usage = ModelUsage.model_validate_json(row["usage_json"])
            native = usage_cost(card, usage)
            if (row["reporting_currency"] != "EUR"
                    or row["provider_request_id"] != usage.provider_request_id
                    or parse_utc(row["created_at"]) > self.clock.now()
                    or parse_utc(row["created_at"]) < parse_utc(reservation["created_at"])):
                reasons.append("invalid:receipt_currency_or_availability")
            if not row["synthetic"] and (row["provider"] != card.provider or row["model"] != card.model):
                reasons.append("invalid:receipt_price_card_identity")
            if native != fixed_decimal(row["native_cost"]) or row["native_currency"] != card.currency:
                reasons.append("invalid:receipt_native_price_arithmetic")
            if fixed_decimal(row["reporting_cost"]) != fixed_decimal(reservation["amount"]):
                reasons.append("invalid:receipt_reporting_reservation_link")
            if card.currency == "EUR":
                if fixed_decimal(row["reporting_cost"]) != native:
                    reasons.append("invalid:receipt_identity_conversion")
            else:
                self._fx_link(row, reservation, data, reasons, native)
            invocation = invocations.get(row["reservation_id"])
            if invocation is None:
                reasons.append("durable_provider_invocation_missing")
            else:
                request = ModelRequest.model_validate_json(invocation["request_json"])
                result = (ModelResult.model_validate_json(invocation["result_json"])
                          if invocation["result_json"] else None)
                if (request.task_id != reservation["task_id"] or request.role != reservation["role"]
                        or request.root_task_id != reservation["root_task_id"]
                        or request.system_version_id != reservation["system_version_id"]
                        or invocation["created_at"] > row["created_at"]
                        or any(getattr(request, name) != invocation[name]
                               for name in ("task_id", "root_task_id", "run_id", "system_version_id"))
                        or result is None or result.usage != usage):
                    reasons.append("invalid:provider_attempt_receipt_link")
                if (invocation["portfolio_id"], request.system_version_id) not in versions:
                    reasons.append("provider_invocation_version_link_missing")
                if request.provider == "scripted" or "http_fixture" in request.context:
                    reasons.append("synthetic_provider_invocation")
            if row["reservation_id"] not in attempts:
                reasons.append("provider_transport_attempt_missing")
            if not row["provider_request_id"]:
                reasons.append("provider_response_identifier_missing")
        for identity, row in reservations.items():
            if row["state"] in {"RESERVED", "UNCERTAIN", "CONSERVATIVE"} or (
                row["state"] != "RELEASED" and identity not in receipt_reservations
            ):
                unresolved.append(identity)
                reasons.append("unresolved_or_unreceipted_attempt")
        self._transport_links(data, reservations, invocations, reasons, unresolved)
        self._invoice_links(data, reservations, reasons)
        receipt_map = {row["receipt_id"]: row for row in data["usage_receipts"]}
        weights = defaultdict(Decimal)
        for row in data["cost_allocations"]:
            source = receipt_map.get(row["receipt_id"])
            weight = fixed_decimal(row["weight"])
            weights[row["receipt_id"]] += weight
            if (source is None or not 0 <= weight <= 1 or source["reporting_cost"] is None
                    or fixed_decimal(row["amount"]) != fixed_decimal(source["reporting_cost"]) * weight):
                reasons.append("invalid:shared_runtime_expense_allocation")
        if any(value != 1 for value in weights.values()):
            reasons.append("invalid:incomplete_or_duplicate_shared_allocation")
        return reasons, receipts, synthetic, set(unresolved)

    def _invoice_links(self, data, reservations, reasons):
        for row in data["invoice_reconciliations"]:
            if row["deployment_id"] != self.deployment_id:
                continue
            total, recorded, difference = (fixed_decimal(row[key])
                                           for key in ("invoice_total", "recorded_total", "unexplained"))
            cutoff = parse_utc(row["created_at"])
            if (total < 0 or recorded < 0 or total - recorded != difference or row["currency"] != "EUR"
                    or cutoff > self.clock.now()):
                reasons.append("invalid:provider_invoice_arithmetic_currency_or_availability")
            before, at = Decimal("0"), Decimal("0")
            for receipt in data["usage_receipts"]:
                if (receipt["reservation_id"] not in reservations or receipt["synthetic"]
                        or receipt["status"] == "uncertain"):
                    continue
                observed = parse_utc(receipt["created_at"])
                if observed > cutoff:
                    continue
                if receipt["reporting_cost"] is None or receipt["reporting_currency"] != "EUR":
                    reasons.append("invalid:provider_invoice_receipt_currency_or_cost")
                    continue
                amount = fixed_decimal(receipt["reporting_cost"])
                if observed < cutoff:
                    before += amount
                else:
                    at += amount
            if not before <= recorded <= before + at:
                reasons.append("invalid:provider_invoice_recorded_cost_cutoff")
            elif at != 0:
                # Equal timestamps do not prove which receipts preceded this
                # historical reconciliation. A future sequence/cutoff receipt link
                # can resolve this; current arithmetic only bounds the total.
                reasons.append("provider_invoice_receipt_cutoff_ambiguous")
            if difference != 0:
                reasons.append("unexplained_provider_invoice_difference")

    def _transport_links(self, data, reservations, invocations, reasons, unresolved):
        all_reservations = {row["reservation_id"] for row in data["budget_reservations"]}
        receipt_times = {row["reservation_id"]: parse_utc(row["created_at"]) for row in data["usage_receipts"]}
        outcomes = {"DISPATCH_POSSIBLE", "HTTP_RESPONSE", "UNCERTAIN", "REFUSED_RESPONSE", "UNVERIFIED_RESPONSE"}
        for row in data["provider_transport_attempts"]:
            if row["reservation_id"] not in all_reservations:
                reasons.append("invalid:orphan_provider_transport_attempt")
            reservation = reservations.get(row["reservation_id"])
            if reservation is None:
                continue
            started = parse_utc(row["started_at"])
            finished = parse_utc(row["finished_at"]) if row["finished_at"] else None
            if (row["attempt_id"] != row["reservation_id"] or row["synthetic"] != reservation["synthetic"]
                    or row["transport_basis"] not in {"protected_httpx_observation", "unverified_transport"}
                    or row["outcome"] not in outcomes
                    or not parse_utc(reservation["created_at"]) <= started <= self.clock.now()
                    or finished is not None and not started <= finished <= self.clock.now()
                    or row["reservation_id"] in receipt_times and started > receipt_times[row["reservation_id"]]):
                reasons.append("invalid:provider_transport_scope_or_chronology")
            for name in ("endpoint_sha256", "request_sha256", "response_sha256"):
                value = row[name]
                if (value is None and name != "response_sha256") or (value is not None and (
                    not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
                )):
                    reasons.append("invalid:provider_transport_digest")
            if ((row["status_code"] is not None and not 100 <= row["status_code"] <= 599)
                    or row["response_bytes"] is not None and not 0 <= row["response_bytes"] <= 1048576
                    or row["error_category"] not in {None, "transport", "deadline", "response_bounds"}):
                reasons.append("invalid:provider_transport_response_bounds")
            if row["outcome"] == "HTTP_RESPONSE":
                if (finished is None or row["response_sha256"] is None or row["status_code"] is None
                        or row["response_bytes"] is None or row["error_category"] is not None
                        or row["reservation_id"] in receipt_times and finished > receipt_times[row["reservation_id"]]):
                    reasons.append("invalid:provider_transport_complete_response")
            else:
                unresolved.append(row["reservation_id"])
                reasons.append("provider_transport_outcome_unresolved")
                if row["outcome"] != "DISPATCH_POSSIBLE" and finished is None:
                    reasons.append("invalid:provider_transport_missing_finish")
            if row["transport_basis"] == "unverified_transport" or row["synthetic"]:
                reasons.append("unverified_or_synthetic_provider_transport")
            invocation = invocations.get(row["reservation_id"])
            if invocation is None:
                reasons.append("durable_provider_invocation_missing")
                continue
            request = ModelRequest.model_validate_json(invocation["request_json"])
            if (row["invocation_id"] != invocation["invocation_id"] or request.provider != row["provider"]
                    or parse_utc(invocation["created_at"]) > started):
                reasons.append("invalid:provider_transport_invocation_link")
            adapter = {"openai": OpenAIAdapter, "anthropic": AnthropicAdapter}.get(request.provider)
            if adapter is None or "http_fixture" in request.context:
                reasons.append("invalid:provider_transport_fixture_invocation")
            else:
                protected = adapter()
                if (sha256(protected.endpoint.encode()).hexdigest() != row["endpoint_sha256"]
                        or sha256(provider_request_bytes(protected.build_body(request))).hexdigest()
                        != row["request_sha256"]):
                    reasons.append("invalid:provider_transport_request_link")

    def _fx_link(self, receipt, reservation, data, reasons, native):
        identity, frozen, rate = (receipt.get(key) for key in ("fx_rate_id", "fx_source_json", "fx_rate_value"))
        if not identity or frozen is None or rate is None:
            reasons.append("receipt_fx_source_link_missing")
            return
        if any(receipt.get(key) != reservation.get(key) for key in ("fx_rate_id", "fx_source_json", "fx_rate_value")):
            reasons.append("invalid:receipt_reservation_fx_link")
        sources = [row for row in data["fx_rates"] if row["rate_id"] == identity]
        if len(sources) != 1 or json.loads(frozen) != sources[0]:
            reasons.append("invalid:receipt_fx_source_changed")
            return
        source = sources[0]
        incurred = parse_utc(reservation["created_at"])
        if (source["base"] != receipt["native_currency"] or source["quote"] != "EUR" or source["stale"]
                or fixed_decimal(source["rate"]) != fixed_decimal(rate)
                or any(parse_utc(source[key]) > incurred for key in ("observed_at", "valid_as_of", "retrieved_at"))
                or native * fixed_decimal(rate) != fixed_decimal(receipt["reporting_cost"])):
            reasons.append("invalid:receipt_fx_time_or_arithmetic")

    def _derived_expenses(self, data: dict) -> tuple[ExpenseEvidence, ...]:
        reservations = {row["reservation_id"]: row for row in data["budget_reservations"]}
        invocations = {row["reservation_id"]: row for row in data["model_invocations"]}
        versions = {(row["portfolio_id"], row["version_id"]): row["artifact_hash"] for row in data["version_history"]}
        derived = []
        for row in data["usage_receipts"]:
            reservation = reservations.get(row["reservation_id"])
            if reservation is None or reservation["deployment_id"] != self.deployment_id:
                continue
            invocation = invocations.get(row["reservation_id"])
            result = (ModelResult.model_validate_json(invocation["result_json"])
                      if invocation and invocation["result_json"] else None)
            known = (row["status"] == "committed" and reservation["state"] in {"COMMITTED", "RECONCILED"}
                     and (row["native_currency"] == "EUR" or all(
                         row.get(name) is not None for name in ("fx_rate_id", "fx_rate_value", "fx_source_json")
                     )))
            amount = fixed_decimal(row["reporting_cost"]) if known else None
            version = (versions.get((invocation["portfolio_id"], reservation["system_version_id"]))
                       if invocation and reservation["role"] != "engineer" else None)
            derived.append(ExpenseEvidence(
                receipt_id=row["receipt_id"], source_ref=f"runtime-receipt:{document_hash(_canonical({
                    'receipt': row, 'reservation': reservation, 'invocation': invocation,
                }))}",
                conversion_ref=(f"runtime-fx:{document_hash(row['fx_source_json'])}" if row.get("fx_source_json")
                                else "runtime:EUR-identity" if row["native_currency"] == "EUR"
                                else "runtime:unresolved-FX-source"),
                incurred_at=parse_utc(reservation["created_at"]), amount_eur=amount,
                evidence_kind="synthetic" if row["synthetic"] else "actual",
                cost_class="setup_engineering" if reservation["role"] == "engineer" else "recurring",
                outcome="unresolved" if not known or result is None else "failed" if not result.ok else "succeeded",
                variant_sha256=version,
            ))
        return tuple(derived)

    def import_expenses(self, capture: RuntimeEvidenceCapture) -> tuple[str, ...]:
        """Derive receipts from retained current runtime rows, never supplied amounts.

        This creates auditable financial imports, not an authentication verdict.
        Unknown attempts stay unresolved and require their missing receipts. Shared
        allocation weights remain a separate protected operator decision.
        """
        verified = self.verify(capture)
        if not verified.database_consistent:
            raise ValueError("invalid runtime source cannot become expense evidence")
        data = json.loads(capture.source_json)
        imported = []
        with self.registry._atomic(), self.database.snapshot():
            if capture.evaluation_snapshot.source_records != self.registry._source_bindings():
                raise ValueError("evaluation evidence changed before expense import")
            if self._read() != capture.source_json:
                raise ValueError("runtime evidence changed before expense import")
            existing = {row["receipt_id"]: row for row in (
                json.loads(item["document_json"]) for item in self.registry._rows("expense")
            )}
            for evidence in self._derived_expenses(data):
                if evidence.receipt_id in existing:
                    if ExpenseEvidence.model_validate(existing[evidence.receipt_id]) != evidence:
                        raise ValueError("runtime receipt conflicts with retained expense evidence")
                else:
                    self.registry._append("expense", evidence.receipt_id, None, evidence)
                imported.append(evidence.receipt_id)
        return tuple(sorted(imported))
