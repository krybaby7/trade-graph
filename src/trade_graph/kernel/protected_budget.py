"""Concrete protected reservation origins and immutable receipt commitments."""

from __future__ import annotations

import hmac
import json
from decimal import Decimal
from time import monotonic

from trade_graph.adapters.persistence.db import atomic
from trade_graph.application.budget import BudgetGateway
from trade_graph.domain.clock import parse_utc, utc_iso
from trade_graph.domain.errors import StaleState
from trade_graph.kernel.financial_checkpoint import _unique
from trade_graph.kernel.runtime_manifest import canonical_json, document_sha256

MUTABLE_RESERVATION_FIELDS = {"state", "amount", "updated_at"}


class ProtectedBudgetOrigins:
    def __init__(self, financial):
        self.financial, self.database = financial, financial.database
        self._sizes = {}

    def _source_rows(self, table, clause, params):
        """Check source field sizes in SQLite before loading private rows."""
        if table not in self._sizes:
            columns = [row["name"] for row in self.database.execute(f"PRAGMA table_info({table})")]
            self._sizes[table] = "+".join('coalesce(length(CAST("' + name.replace('"', '""')
                                        + '" AS BLOB)),0)' for name in columns)
        if self.database.execute(f"SELECT 1 FROM {table} WHERE {clause} AND ({self._sizes[table]})>65536 LIMIT 1",
                                 params).fetchone():
            raise StaleState("protected original budget source exceeds retained row bound")
        return self.database.execute(f"SELECT * FROM {table} WHERE {clause}", params)

    def task_scope(self, task_id):
        if self.database.execute("""SELECT 1 FROM tasks WHERE task_id=? AND
            (coalesce(length(CAST(portfolio_id AS BLOB)),0)+length(CAST(task_id AS BLOB))+
             coalesce(length(CAST(root_task_id AS BLOB)),0)+length(CAST(role AS BLOB)))>65536""",
            (task_id,)).fetchone():
            raise StaleState("protected original task scope exceeds retained row bound")
        return self.database.execute("SELECT portfolio_id,task_id,root_task_id,role FROM tasks WHERE task_id=?",
                                     (task_id,)).fetchone()

    def _load(self, reservation_id, kind):
        row = self._source_rows("protected_financial_budget_origins", "reservation_id=? AND event_kind=?",
                                (reservation_id, kind)).fetchone()
        if row is None:
            return None
        if len(row["origin_json"].encode()) > 65536:
            raise StaleState("protected budget original authority exceeds bound")
        try:
            document = json.loads(row["origin_json"], object_pairs_hook=_unique)
            if (type(document) is not dict
                    or not hmac.compare_digest(row["authentication"], self.financial.history._mac(document))
                    or row["origin_id"] != document_sha256([reservation_id, kind])
                    or document["reservation_id"] != reservation_id or document["event_kind"] != kind
                    or document["deployment_id"] != row["deployment_id"]
                    or document["manifest_sha256"] != row["manifest_sha256"]
                    or document["instance_id"] != row["instance_id"]
                    or document["portfolio_id"] != row["portfolio_id"]
                    or document["captured_at"] != row["created_at"]
                    or parse_utc(row["created_at"]) > self.financial.clock.now()):
                raise ValueError
            instance = self.database.execute(
                "SELECT manifest_sha256 FROM protected_runtime_instances WHERE instance_id=?",
                (row["instance_id"],)).fetchone()
            if instance is None or instance[0] != document["manifest_sha256"]:
                raise ValueError
            return document
        except (ValueError, TypeError, KeyError, RecursionError):
            raise StaleState("protected budget original authority authentication refused") from None

    def _insert(self, document):
        raw = canonical_json(document)
        if len(raw.encode()) > 65536:
            raise StaleState("protected budget original authority exceeds bound")
        self.database.execute("""INSERT INTO protected_financial_budget_origins
            (origin_id,reservation_id,event_kind,deployment_id,manifest_sha256,instance_id,portfolio_id,
             origin_json,authentication,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (document_sha256([document["reservation_id"], document["event_kind"]]), document["reservation_id"],
             document["event_kind"], document["deployment_id"], document["manifest_sha256"], document["instance_id"],
             document["portfolio_id"], raw, self.financial.history._mac(document), document["captured_at"]))

    def record_origin(self, reservation_id: str, *, instance_id: str, portfolio_id: str, initial=False):
        instance = self.financial._instance(instance_id)
        if instance["status"] != "RUNNING" and not (
            initial and instance["generation"] == 0 and instance["active_release_id"] is None
        ):
            raise StaleState("protected budget origin lacks exact admitted controller")
        row = self._source_rows("budget_reservations", "reservation_id=?", (reservation_id,)).fetchone()
        if row is None or row["deployment_id"] != self.financial.manifest.deployment_id:
            raise StaleState("protected budget origin lacks exact deployment reservation")
        existing = self._load(reservation_id, "ORIGIN")
        if existing:
            self.validate(reservation_id)
            return existing
        reservation = dict(row)
        task = self.task_scope(row["task_id"])
        if task:
            if task["role"] != row["role"] or task["root_task_id"] != row["root_task_id"]:
                raise StaleState("protected reservation task authority differs")
            portfolio_id = task["portfolio_id"]
        if not self.database.execute("SELECT 1 FROM portfolios WHERE portfolio_id=? AND mode='paper'",
                                     (portfolio_id,)).fetchone():
            raise StaleState("protected reservation original paper portfolio is absent")
        if initial:
            has_dispatch = self.database.execute("SELECT 1 FROM model_invocations WHERE reservation_id=?",
                                                 (reservation_id,)).fetchone()
            has_transport = self.database.execute("SELECT 1 FROM provider_transport_attempts WHERE reservation_id=?",
                                                  (reservation_id,)).fetchone()
            if row["state"] == "UNCERTAIN" or row["state"] == "RESERVED" and (has_dispatch or has_transport):
                raise StaleState("existing outbound or uncertain reservation has no pre-dispatch protected origin")
        elif row["state"] != "RESERVED":
            raise StaleState("new protected original authority must precede provider dispatch")
        document = {"schema_version": 1, "reservation_id": reservation_id, "event_kind": "ORIGIN",
                    "deployment_id": row["deployment_id"], "manifest_sha256": self.financial.manifest.sha256,
                    "instance_id": instance_id, "portfolio_id": portfolio_id,
                    "task_scope": dict(task) if task else None, "reservation": reservation,
                    "captured_at": utc_iso(self.financial.clock.now())}
        self._insert(document)
        if initial and row["state"] in {"COMMITTED", "CONSERVATIVE", "RECONCILED"}:
            self.record_receipt(reservation_id, initial=True)
        self.validate(reservation_id)
        return document

    def record_receipt(self, reservation_id: str, *, initial=False):
        origin = self._load(reservation_id, "ORIGIN")
        if origin is None:
            raise StaleState("protected receipt lacks pre-dispatch original authority")
        receipt = self._source_rows("usage_receipts", "reservation_id=?", (reservation_id,)).fetchone()
        reservation = self._source_rows("budget_reservations", "reservation_id=?", (reservation_id,)).fetchone()
        if receipt is None or reservation is None:
            raise StaleState("protected receipt or reservation is absent")
        document = {**{key: origin[key] for key in ("schema_version", "reservation_id", "deployment_id",
                                                   "manifest_sha256", "instance_id", "portfolio_id")},
                    "event_kind": "RECEIPT", "origin_sha256": document_sha256(origin),
                    "receipt": dict(receipt), "reservation": dict(reservation),
                    "captured_at": utc_iso(self.financial.clock.now())}
        existing_allocations = self.allocations(receipt["receipt_id"])
        document["allocations"] = (existing_allocations if initial else [{"portfolio_id": origin["portfolio_id"],
            "weight": "1", "amount": receipt["reporting_cost"]}])
        previous = self._load(reservation_id, "RECEIPT")
        if previous:
            document["captured_at"] = previous["captured_at"]
            if previous != document:
                raise StaleState("protected immutable receipt commitment differs")
            return
        self._insert(document)

    def allocations(self, receipt_id):
        rows = self._source_rows("cost_allocations", "receipt_id=?", (receipt_id,))
        result = []
        for row in rows:
            result.append({key: row[key] for key in ("portfolio_id", "weight", "amount")})
            if len(result) > 128:
                raise StaleState("protected receipt allocation exceeds portfolio bound")
        return sorted(result, key=lambda item: item["portfolio_id"])

    def validate(self, reservation_id: str, *, require_allocations=True):
        origin, sealed = self._load(reservation_id, "ORIGIN"), self._load(reservation_id, "RECEIPT")
        row = self._source_rows("budget_reservations", "reservation_id=?", (reservation_id,)).fetchone()
        if origin is None or row is None:
            raise StaleState("protected original budget reservation disappeared")
        current = dict(row)
        if (current["deployment_id"] != self.financial.manifest.deployment_id
                or any(current.get(key) != value for key, value in origin["reservation"].items()
                       if key not in MUTABLE_RESERVATION_FIELDS)):
            raise StaleState("protected original budget scope was reinterpreted")
        if origin["task_scope"] is not None:
            task = self.task_scope(current["task_id"])
            if task is None or dict(task) != origin["task_scope"]:
                raise StaleState("protected original budget task authority changed")
        receipts = self._source_rows("usage_receipts", "reservation_id=?", (reservation_id,)).fetchall()
        if len(receipts) > 1:
            raise StaleState("protected reservation has conflicting retained receipts")
        if sealed:
            if (sealed["origin_sha256"] != document_sha256(origin) or not receipts
                    or dict(receipts[0]) != sealed["receipt"] or current != sealed["reservation"]):
                raise StaleState("protected known expense or settled reservation changed or disappeared")
            receipt = receipts[0]
            try:
                amount, charge = Decimal(current["amount"]), Decimal(receipt["reporting_cost"])
                if (not amount.is_finite() or not charge.is_finite() or amount != charge or amount < 0
                        or current["currency"] != receipt["reporting_currency"]
                        or current["synthetic"] != receipt["synthetic"]
                        or current["state"] not in {"COMMITTED", "CONSERVATIVE", "RECONCILED"}):
                    raise ValueError
            except (ValueError, TypeError, ArithmeticError):
                raise StaleState("protected settled budget amount lacks matching retained receipt") from None
            if require_allocations and self.allocations(receipt["receipt_id"]) != sealed["allocations"]:
                raise StaleState("protected known expense allocation changed or disappeared")
        else:
            if (receipts or current["state"] not in {"RESERVED", "UNCERTAIN"}
                    or current["amount"] != origin["reservation"]["amount"]
                    or origin["reservation"]["state"] == "UNCERTAIN" and current["state"] != "UNCERTAIN"):
                raise StaleState("protected unresolved reservation cannot replenish its original budget")

    def verify_all(self):
        started, count = monotonic(), 0
        deployment = self.financial.manifest.deployment_id
        for row in self.database.execute("SELECT reservation_id FROM budget_reservations WHERE deployment_id=?",
                                         (deployment,)):
            count += 1
            if count > 500_000 or monotonic() - started >= 5:
                raise StaleState("protected budget continuity exceeds complete bounded verification")
            self.validate(row[0])
        if self.database.execute("""SELECT 1 FROM protected_financial_budget_origins o
            LEFT JOIN budget_reservations r ON r.reservation_id=o.reservation_id
            WHERE o.deployment_id=? AND (r.reservation_id IS NULL OR r.deployment_id!=o.deployment_id) LIMIT 1""",
            (deployment,)).fetchone():
            raise StaleState("protected historical budget authority disappeared")


class ProtectedBudgetGateway(BudgetGateway):
    def __init__(self, gateway, runtime):
        if gateway.database is not runtime.financial.database or gateway.clock is not runtime.financial.clock:
            raise ValueError("protected budget requires exact financial parent binding")
        super().__init__(gateway.database, gateway.clock)
        self.runtime, self.origins = runtime, runtime.financial.origins

    def protect_dispatch(self, reservation_id):
        origin = self.origins._load(reservation_id, "ORIGIN")
        if origin is None:
            raise StaleState("protected provider dispatch lacks original authority")
        self.origins.validate(reservation_id)
        self.runtime.financial.retain_budget_history(origin["portfolio_id"])

    def protect_settlement(self, reservation_id):
        origin = self.origins._load(reservation_id, "ORIGIN")
        if origin is None:
            raise StaleState("protected provider settlement lacks original authority")
        self.origins.validate(reservation_id)
        self.runtime.financial.retain_budget_history(origin["portfolio_id"])

    @atomic
    def reserve(self, **kwargs):
        task = self.origins.task_scope(kwargs.get("task_id"))
        if task is None or not self.runtime.financial.history.ready(task["portfolio_id"]):
            raise StaleState("protected model reservation lacks original task or independent financial witness")
        self.origins.verify_all()
        if kwargs.get("deployment_id") != self.runtime.financial.manifest.deployment_id:
            raise StaleState("protected model budget deployment changed")
        reservation = super().reserve(**kwargs)
        self.origins.record_origin(reservation, instance_id=self.runtime.controller.instance_id,
                                   portfolio_id=task["portfolio_id"])
        return reservation

    @atomic
    def commit(self, reservation_id, usage, **kwargs):
        self.origins.validate(reservation_id)
        receipt = super().commit(reservation_id, usage, **kwargs)
        self.origins.record_receipt(reservation_id)
        self.origins.validate(reservation_id, require_allocations=False)
        return receipt

    @atomic
    def conservative_charge(self, reservation_id):
        self.origins.validate(reservation_id)
        receipt = super().conservative_charge(reservation_id)
        self.origins.record_receipt(reservation_id)
        origin = self.origins._load(reservation_id, "ORIGIN")
        self.allocate(receipt, {origin["portfolio_id"]: Decimal(1)})
        self.origins.validate(reservation_id)
        return receipt

    @atomic
    def allocate(self, receipt_id, weights):
        reservation = self.database.execute("SELECT reservation_id FROM usage_receipts WHERE receipt_id=?",
                                            (receipt_id,)).fetchone()
        if reservation is None:
            raise StaleState("protected expense allocation lacks receipt")
        self.origins.validate(reservation[0], require_allocations=False)
        super().allocate(receipt_id, weights)
        self.origins.validate(reservation[0])
