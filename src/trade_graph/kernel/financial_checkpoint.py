"""Bounded streaming finance commitments and an independent private highwater.

The SQLite receipt contains commitments, never a replacement ledger. Every scan
reads every retained fact. The separate witness is excluded from SQLite backups;
restoring that database cannot silently restore its financial authority.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
import sys
import threading
from contextlib import contextmanager
from decimal import Decimal, Inexact, localcontext
from pathlib import Path
from time import monotonic

from trade_graph.adapters.engineering.artifact_files import open_directory
from trade_graph.adapters.engineering.process import run_bounded
from trade_graph.domain.clock import parse_utc, utc_iso
from trade_graph.domain.errors import StaleState
from trade_graph.domain.money import canonical_decimal
from trade_graph.kernel.books import Books, Expense, Lot
from trade_graph.kernel.runtime_manifest import canonical_json, document_sha256

MAXIMUM_ROWS = 1_000_000
MAXIMUM_TOTAL_BYTES = 536_870_912
MAXIMUM_ROW_BYTES = 65_536
MAXIMUM_CORRECTION_BYTES = 1_048_576
MAXIMUM_CURRENT_ITEMS = 10_000
MAXIMUM_SECONDS = 5
MAXIMUM_WITNESS_BYTES = 65_536
MAXIMUM_BOOTSTRAP_LEDGER_ROWS = 4096
MAXIMUM_BOOTSTRAP_LEDGER_BYTES = 8_388_608


class ScanBudget:
    def __init__(self, cancelled: threading.Event | None = None):
        self.rows = self.bytes = 0
        self.started, self.cancelled = monotonic(), cancelled

    def check(self):
        if ((self.cancelled is not None and self.cancelled.is_set())
                or monotonic() - self.started >= MAXIMUM_SECONDS):
            raise StaleState("protected financial scan cancelled or deadline exceeded")

    def consume(self, row: dict, maximum_bytes: int = MAXIMUM_ROW_BYTES) -> bytes:
        self.check()
        # SQLite length checks in each query reject giant fields before Python
        # receives them. This second bound includes JSON encoding overhead.
        encoded = canonical_json(row).encode()
        self.rows += 1
        self.bytes += len(encoded)
        if (len(encoded) > maximum_bytes or self.rows > MAXIMUM_ROWS
                or self.bytes > MAXIMUM_TOTAL_BYTES):
            raise StaleState("protected financial scan exceeds complete retained bound")
        return encoded


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StaleState("duplicate protected financial receipt field")
        result[key] = value
    return result


class FinancialHistoryCheckpoint:
    def __init__(self, financial, *, witness_path: Path | None = None):
        self.financial, self.database = financial, financial.database
        self.manifest, self.key = financial.manifest, financial._capability_key
        self.path = witness_path or self.database.path.with_suffix(".protected-financial-witness.json")
        self._highwater: dict[str, tuple[int, str]] = {}
        self._witness_local = threading.local()
        self._database_highwater = self._database_identity()

    def _database_identity(self):
        """Verify no-follow identity using a handle retained beyond SQLite connections."""
        result = self.database.file_identity()
        known = getattr(self, "_database_highwater", None)
        if known and result != known:
            raise StaleState("protected financial database identity changed")
        return result

    def _mac(self, document: dict) -> str:
        return hmac.new(self.key, b"protected-financial-checkpoint-v1\0"
                        + canonical_json(document).encode(), hashlib.sha256).hexdigest()

    def _scope(self, portfolio_id: str) -> str:
        return document_sha256([self.manifest.sha256, portfolio_id])

    @contextmanager
    def witness_lock(self):
        """Serialize one controller's threads before cross-process ownership.

        Use the same local order as SQLite writers: some callers already hold a
        writer transaction, while publication must keep its witness lock beyond
        COMMIT. Nested checks reuse the securely opened directory and lock.
        """
        with self.database.serialized():
            directory = getattr(self._witness_local, "directory", None)
            if directory is not None:
                yield directory
                return
            with self._locked_witness() as directory:
                self._witness_local.directory = directory
                try:
                    yield directory
                finally:
                    self._witness_local.directory = None

    @contextmanager
    def _locked_witness(self):
        """Private parent/leaf handles, no links/FIFOs, and a cross-process lock."""
        path = self.path.absolute()
        if path == self.database.path.absolute() or ".." in path.parts:
            raise StaleState("independent protected witness path required")
        parent = path.parent
        try:
            directory = open_directory(parent)
        except OSError:
            raise StaleState("protected witness ancestor is not a real directory") from None
        info = os.fstat(directory)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            os.close(directory)
            raise StaleState("protected witness requires a private parent directory")
        lock = None
        try:
            lock = os.open(path.name + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
                           | os.O_NONBLOCK | os.O_CLOEXEC, 0o600, dir_fd=directory)
            identity = os.fstat(lock)
            if (not stat.S_ISREG(identity.st_mode) or identity.st_uid != os.geteuid()
                    or stat.S_IMODE(identity.st_mode) != 0o600 or identity.st_nlink != 1):
                raise StaleState("protected witness lock is not private")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StaleState("another protected controller owns the financial witness") from None
            second = open_directory(parent)
            try:
                current = os.fstat(second)
                if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
                    raise StaleState("protected witness directory identity changed")
            finally:
                os.close(second)
            yield directory
        finally:
            if lock is not None:
                os.close(lock)
            os.close(directory)

    def _read_witness(self, directory: int, *, recovery_source_identity: list[int] | None = None) -> dict | None:
        """Normal reads require this inode; offline recovery may authenticate an explicit old identity."""
        try:
            descriptor = os.open(self.path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                                 | os.O_CLOEXEC, dir_fd=directory)
        except FileNotFoundError:
            return None
        except OSError:
            raise StaleState("protected financial witness is unreadable") from None
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                    or info.st_size > MAXIMUM_WITNESS_BYTES):
                raise StaleState("protected financial witness identity or size refused")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = stream.read(MAXIMUM_WITNESS_BYTES + 1)
            if len(raw) > MAXIMUM_WITNESS_BYTES:
                raise StaleState("protected financial witness exceeds retained bound")
            document = json.loads(raw, object_pairs_hook=_unique)
            if type(document) is not dict or set(document) != {"payload", "authentication"}:
                raise StaleState("protected financial witness envelope refused")
            payload = document["payload"]
            expected = {"schema_version", "database_identity", "scopes"}
            if type(payload) is dict and payload.get("schema_version") in {2, 3}:
                expected.add("transitions")
            if type(payload) is dict and payload.get("schema_version") == 3:
                expected.add("recoveries")
            if (type(payload) is not dict or set(payload) != expected
                    or type(payload["schema_version"]) is not int or payload["schema_version"] not in {1, 2, 3}
                    or type(payload["scopes"]) is not dict
                    or len(payload["scopes"]) > 128 or type(document["authentication"]) is not str
                    or not hmac.compare_digest(document["authentication"], self._mac(payload))):
                raise StaleState("protected financial witness authentication refused")
            transitions = payload.get("transitions", [])
            if (type(transitions) is not list or len(transitions) > 128
                    or any(type(item) is not str or len(item) != 64 for item in transitions)
                    or len(set(transitions)) != len(transitions)):
                raise StaleState("protected financial witness transition chain refused")
            recoveries = payload.get("recoveries", [])
            if (type(recoveries) is not list or len(recoveries) > 128
                    or any(type(item) is not str or len(item) != 64 for item in recoveries)
                    or len(set(recoveries)) != len(recoveries)):
                raise StaleState("protected financial witness recovery chain refused")
            expected_identity = (self._database_identity() if recovery_source_identity is None
                                 else recovery_source_identity)
            if payload["database_identity"] != expected_identity:
                raise StaleState("protected financial database identity changed")
            if any(scope not in payload["scopes"] for scope in self._highwater):
                raise StaleState("protected financial witness omitted observed highwater")
            for scope, value in payload["scopes"].items():
                if (type(scope) is not str or len(scope) != 64 or type(value) is not dict
                        or set(value) != {"generation", "checkpoint_sha256"}
                        or type(value["generation"]) is not int or value["generation"] < 1
                        or type(value["checkpoint_sha256"]) is not str or len(value["checkpoint_sha256"]) != 64):
                    raise StaleState("protected financial witness scope refused")
                highwater = self._highwater.get(scope)
                if highwater and (value["generation"] < highwater[0]
                                  or value["generation"] == highwater[0]
                                  and value["checkpoint_sha256"] != highwater[1]):
                    raise StaleState("protected financial witness moved behind observed highwater")
            return payload
        except (ValueError, TypeError, RecursionError):
            raise StaleState("protected financial witness is malformed") from None
        finally:
            os.close(descriptor)

    def _write_witness(self, directory: int, payload: dict):
        raw = canonical_json({"payload": payload, "authentication": self._mac(payload)}).encode()
        if len(raw) > MAXIMUM_WITNESS_BYTES:
            raise StaleState("protected financial witness exceeds scope bound")
        temporary = self.path.name + "." + secrets.token_hex(12)
        descriptor = None
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                                 | os.O_CLOEXEC, 0o600, dir_fd=directory)
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(descriptor)
            os.rename(temporary, self.path.name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        except Exception as exc:
            from trade_graph.kernel.operational_diagnostics import emit_event

            emit_event("witness_failure", error=exc)
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass

    def verified_scopes(self, witness: dict | None, *, allow_pending=False,
                        allow_pending_recovery=False) -> tuple[dict, dict]:
        """Authenticate every retained scope and the complete owner transition chain."""
        scopes = {}
        if witness:
            for prior_scope, observed in witness["scopes"].items():
                header = self.database.execute("""SELECT manifest_sha256,portfolio_id,generation,
                    length(CAST(payload_json AS BLOB)) AS bytes FROM protected_financial_checkpoints
                    WHERE checkpoint_id=?""", (observed["checkpoint_sha256"],)).fetchone()
                if header is None or header["bytes"] > MAXIMUM_WITNESS_BYTES:
                    raise StaleState("independent protected witness lost a prior financial checkpoint")
                latest = self.database.execute("""SELECT max(generation) FROM protected_financial_checkpoints
                    WHERE manifest_sha256=? AND portfolio_id=?""",
                    (header["manifest_sha256"], header["portfolio_id"])).fetchone()[0]
                if (header["generation"] != observed["generation"] or latest != observed["generation"]
                        or document_sha256([header["manifest_sha256"], header["portfolio_id"]]) != prior_scope):
                    raise StaleState("independent protected witness differs from prior financial highwater")
                prior_row = self.database.execute("SELECT * FROM protected_financial_checkpoints WHERE checkpoint_id=?",
                                                 (observed["checkpoint_sha256"],)).fetchone()
                try:
                    prior_payload = json.loads(prior_row["payload_json"], object_pairs_hook=_unique)
                    if (document_sha256(prior_payload) != observed["checkpoint_sha256"]
                            or not hmac.compare_digest(prior_row["authentication"], self._mac(prior_payload))):
                        raise ValueError
                except (ValueError, TypeError, RecursionError):
                    raise StaleState("prior protected financial checkpoint authentication refused") from None
                if (prior_payload.get("manifest_sha256") != header["manifest_sha256"]
                        or prior_payload.get("portfolio_id") != header["portfolio_id"]
                        or prior_payload.get("generation") != header["generation"]):
                    raise StaleState("protected prior financial scope binding refused")
                scopes[prior_scope] = prior_payload
        from trade_graph.kernel.financial_transition import verify_transition_chain

        edges = verify_transition_chain(self, witness, scopes, allow_pending=allow_pending)
        from trade_graph.kernel.financial_recovery import verify_recovery_chain

        verify_recovery_chain(self, witness, allow_pending=allow_pending_recovery)
        return scopes, edges

    def previous(self, portfolio_id: str, directory: int) -> tuple[dict | None, dict | None]:
        witness = self._read_witness(directory)
        if witness is None and (self._highwater or self.database.execute(
                "SELECT 1 FROM protected_financial_checkpoints LIMIT 1").fetchone()):
            raise StaleState("protected financial checkpoint lacks independent witness")
        scopes, edges = self.verified_scopes(witness)
        scope = self._scope(portfolio_id)
        if scope in edges:
            raise StaleState("protected financial manifest has been retired by owner transition")
        for prior_scope, prior in scopes.items():
            if prior["portfolio_id"] != portfolio_id or prior_scope == scope:
                continue
            leaf = prior_scope
            for _ in range(129):
                if leaf not in edges:
                    break
                leaf = edges[leaf]
            if leaf != scope:
                raise StaleState("new owner manifest requires a tested protected financial continuity transition")
        if self.database.execute("""SELECT 1 FROM protected_financial_checkpoints
            WHERE manifest_sha256=? AND portfolio_id=? AND length(CAST(payload_json AS BLOB))>? LIMIT 1""",
            (self.manifest.sha256, portfolio_id, MAXIMUM_WITNESS_BYTES)).fetchone():
            raise StaleState("protected financial checkpoint exceeds retained bound")
        row = self.database.execute("""SELECT * FROM protected_financial_checkpoints
            WHERE manifest_sha256=? AND portfolio_id=? ORDER BY generation DESC LIMIT 1""",
            (self.manifest.sha256, portfolio_id)).fetchone()
        observed = witness["scopes"].get(scope) if witness else None
        if row is None:
            if observed or scope in self._highwater:
                raise StaleState("protected financial checkpoint history disappeared")
            return None, witness
        try:
            payload = json.loads(row["payload_json"], object_pairs_hook=_unique)
            if (type(payload) is not dict or payload.get("manifest_sha256") != self.manifest.sha256
                    or payload.get("portfolio_id") != portfolio_id or payload.get("generation") != row["generation"]
                    or not hmac.compare_digest(row["authentication"], self._mac(payload))):
                raise StaleState("protected financial checkpoint authentication refused")
        except (ValueError, TypeError, RecursionError):
            raise StaleState("protected financial checkpoint is malformed") from None
        digest = document_sha256(payload)
        if observed != {"generation": row["generation"], "checkpoint_sha256": digest}:
            raise StaleState("protected financial checkpoint lacks matching independent witness")
        self._highwater[scope] = row["generation"], digest
        return payload, witness

    def scan(self, portfolio_id: str, state: dict, *, previous: dict | None,
             cancelled: threading.Event | None = None) -> dict:
        budget = ScanBudget(cancelled)
        def interrupted():
            return int(monotonic() - budget.started >= MAXIMUM_SECONDS
                       or cancelled is not None and cancelled.is_set())
        connection = self.database.connection
        connection.set_progress_handler(interrupted, 1000)
        try:
            with localcontext() as context:
                context.prec = 28
                return self._scan(portfolio_id, state, previous=previous, budget=budget)
        except sqlite3.OperationalError as exc:
            if str(exc) == "interrupted":
                raise StaleState("protected financial scan cancelled or deadline exceeded") from None
            raise
        finally:
            connection.set_progress_handler(None, 0)

    def _scan(self, portfolio_id: str, state: dict, *, previous: dict | None, budget: ScanBudget) -> dict:
        db, deployment = self.database, self.manifest.deployment_id
        self.financial.origins.verify_all()
        queries = {
            "ledger": ("ledger_events", "portfolio_id=?", (portfolio_id,), "sequence"),
            "journal": ("journal_postings", "portfolio_id=?", (portfolio_id,), "rowid"),
            "journal_transactions": ("journal_transactions", "portfolio_id=?", (portfolio_id,), "rowid"),
            "reservations": ("position_reservations", "portfolio_id=?", (portfolio_id,), "reservation_id"),
            "native_fee_reservations": ("native_fee_reservations", "portfolio_id=?", (portfolio_id,), "rowid"),
            "intents": ("order_intents", "portfolio_id=?", (portfolio_id,), "intent_id"),
            "attempts": ("order_attempts", "intent_id IN (SELECT intent_id FROM order_intents WHERE portfolio_id=?)",
                         (portfolio_id,), "rowid"),
            "fills": ("fills", "portfolio_id=?", (portfolio_id,), "rowid"),
            "marks": ("valuation_marks", "portfolio_id=?", (portfolio_id,), "mark_id"),
            "fx": ("fx_rates", "1", (), "rate_id"),
            "budget": ("deployment_budget", "deployment_id=?", (deployment,), "deployment_id"),
            "role_allocations": ("role_allocations", "deployment_id=?", (deployment,), "role"),
            "budget_reservations": ("budget_reservations", "deployment_id=?", (deployment,), "rowid"),
            "budget_origins": ("protected_financial_budget_origins", "deployment_id=?", (deployment,), "rowid"),
            "usage_receipts": ("usage_receipts", "reservation_id IN (SELECT reservation_id FROM budget_reservations "
                               "WHERE deployment_id=?)", (deployment,), "rowid"),
            "cost_allocations": ("cost_allocations", "receipt_id IN (SELECT receipt_id FROM usage_receipts "
                                 "WHERE reservation_id IN (SELECT reservation_id FROM budget_reservations "
                                 "WHERE deployment_id=?))", (deployment,), "rowid"),
            "invoices": ("invoice_reconciliations", "deployment_id=?", (deployment,), "rowid"),
            "invocations": ("model_invocations", "reservation_id IN (SELECT reservation_id FROM budget_reservations "
                            "WHERE deployment_id=?)", (deployment,), "rowid"),
            "transport_attempts": ("provider_transport_attempts", "reservation_id IN (SELECT reservation_id FROM "
                                   "budget_reservations WHERE deployment_id=?)", (deployment,), "rowid"),
            "price_cards": ("price_cards", "1", (), "price_card_id"),
            "subscription_invocations": ("subscription_invocations", "1", (), "rowid"),
            "subscription_attempts": ("subscription_attempts", "1", (), "rowid"),
            "owner_expense_evidence": ("owner_expense_evidence", "deployment_id=?", (deployment,), "sequence"),
        }
        commitments = {}
        native_cash, native_inventory = {}, {}
        source_cash, source_inventory = {}, {}
        source_manifest, source_count, deferred = hashlib.sha256(), 0, None
        immutable = {"ledger", "journal", "journal_transactions", "fills", "usage_receipts", "cost_allocations",
                     "invoices", "budget_origins", "owner_expense_evidence"}
        identity_fields = {"budget_reservations": {"amount", "state", "updated_at"},
                           "attempts": {"result_json"},
                           "native_fee_reservations": {"current_amount", "state"},
                           "invocations": {"state", "result_json", "updated_at"},
                           "transport_attempts": {"outcome", "response_sha256", "status_code", "response_bytes",
                                                  "error_category", "finished_at"},
                           "subscription_invocations": {"actual_model", "state", "result_json", "quota_json",
                                                        "usage_json", "cost_status", "actual_cost_native",
                                                        "updated_at"},
                           "subscription_attempts": {"actual_model", "state", "result_json", "usage_json",
                                                     "actual_cost_native", "updated_at"}}
        previous_commitments = previous["tables"] if previous else {}
        from trade_graph.kernel.financial_recovery import append_only_commitments, recovered_prefixes

        recovery_commitments, terminal_rows, append_only = recovered_prefixes(self, portfolio_id, budget=budget)
        if append_only:
            append_only_commitments(self.database, portfolio_id, previous=append_only, budget=budget)
        retained_commitments = [previous_commitments, *recovery_commitments]
        for name, (table, condition, parameters, order) in queries.items():
            budget.check()
            # Trusted fixed schema only; these values never originate in child
            # proposals. Bound the SQLite result before allocating Python text.
            columns = [row["name"] for row in db.execute(f"PRAGMA table_info({table})")]
            size = "+".join(f"coalesce(length(CAST({column} AS BLOB)),0)" for column in columns)
            digest, count, byte_count = hashlib.sha256(), 0, 0
            identity_digest = hashlib.sha256()
            old_rows = [item[name] for item in retained_commitments if name in item]
            immutable_rows = [item for item in old_rows if name in immutable]
            identity_rows = [item for item in old_rows if name in identity_fields and "identity_sha256" in item]
            immutable_counts = {item["rows"] for item in immutable_rows}
            identity_counts = {item["rows"] for item in identity_rows}
            prefixes = {0: digest.hexdigest()}
            identity_prefixes = {0: identity_digest.hexdigest()}
            retained_terminals = terminal_rows.get(name, {})
            seen_terminals = set()
            maximum = (f"CASE WHEN kind='fill_chronological_replay' THEN {MAXIMUM_CORRECTION_BYTES} "
                       f"ELSE {MAXIMUM_ROW_BYTES} END" if name == "ledger" else str(MAXIMUM_ROW_BYTES))
            query = f"SELECT *, CASE WHEN ({size})>({maximum}) THEN 1 ELSE 0 END AS _oversized " \
                    f"FROM {table} WHERE {condition} ORDER BY {order}"
            # sqlite length alone is not enough if SELECT * includes a giant
            # value. Fetch only the oversize marker for any such row first.
            if db.execute(f"SELECT 1 FROM {table} WHERE ({condition}) AND ({size})>({maximum}) LIMIT 1",
                          parameters).fetchone():
                raise StaleState("protected financial retained row exceeds bound")
            for row in db.execute(query, parameters):
                document = dict(row)
                document.pop("_oversized")
                encoded = budget.consume(document, MAXIMUM_CORRECTION_BYTES if name == "ledger"
                                         and document["kind"] == "fill_chronological_replay" else MAXIMUM_ROW_BYTES)
                digest.update(encoded + b"\0")
                count, byte_count = count + 1, byte_count + len(encoded)
                if retained_terminals:
                    identity = document["invocation_id" if name == "subscription_invocations" else "attempt_id"]
                    if identity in retained_terminals:
                        if hashlib.sha256(encoded).hexdigest() != retained_terminals[identity]:
                            raise StaleState("protected recovered terminal subscription record changed")
                        seen_terminals.add(identity)
                if name in identity_fields:
                    identity_digest.update(canonical_json({key: value for key, value in document.items()
                        if key not in identity_fields[name]}).encode() + b"\0")
                    if count in identity_counts:
                        identity_prefixes[count] = identity_digest.hexdigest()
                if name not in {"marks", "fx", "price_cards"}:
                    for timestamp in ("created_at", "effective_at", "started_at", "finished_at"):
                        if timestamp in document and document[timestamp] is not None:
                            try:
                                if parse_utc(document[timestamp]) > parse_utc(state["snapshot_at"]):
                                    raise ValueError
                            except (ValueError, TypeError):
                                raise StaleState(
                                    "protected known financial source timestamp is invalid or future") from None
                if count in immutable_counts:
                    prefixes[count] = digest.hexdigest()
                if name == "ledger":
                    if document["sequence"] != source_count + 1:
                        raise StaleState("protected financial source sequence is incomplete")
                    deferred = self._check_ledger_row(document, source_manifest, source_count, deferred)
                    self._check_native_source(document, source_cash, source_inventory, budget)
                    if (document["kind"] == "fill_chronological_replay" and previous
                            and document["sequence"] > previous["tables"]["ledger"]["rows"]):
                        native = self._ledger_probe(portfolio_id, document["sequence"], budget=budget)
                        self._match_native_projection(native, source_cash, source_inventory)
                    source_manifest.update(canonical_json({"event_id": document["event_id"],
                        "sha256": hashlib.sha256(encoded).hexdigest()}).encode() + b"\0")
                    source_count += 1
            if name == "ledger" and deferred:
                raise StaleState("protected ledger has a dangling deferred native fill")
            if any(count < item["rows"] or prefixes.get(item["rows"]) != item["sha256"]
                   for item in immutable_rows):
                raise StaleState("protected committed financial prefix changed or disappeared")
            if any(count < item["rows"] or identity_prefixes.get(item["rows"]) != item["identity_sha256"]
                   for item in identity_rows):
                raise StaleState("protected original financial effect identity changed or disappeared")
            if seen_terminals != set(retained_terminals):
                raise StaleState("protected recovered terminal subscription record disappeared")
            commitments[name] = {"rows": count, "bytes": byte_count, "sha256": digest.hexdigest()}
            if name in identity_fields:
                commitments[name]["identity_sha256"] = identity_digest.hexdigest()
        self._native_journal(portfolio_id, state["snapshot_at"], native_cash, native_inventory, budget)
        financial = {"cash": {key: canonical_decimal(value) for key, value in sorted(native_cash.items())},
                     "inventory": {key: canonical_decimal(value) for key, value in sorted(native_inventory.items())}}
        # The observation time selects replay facts, but an advancing clock
        # without another fact must not invalidate an otherwise exact snapshot.
        state = {key: value for key, value in state.items() if key != "snapshot_at"}
        result = {"state": state, "tables": commitments, "financial": financial}
        return {**result, "state_sha256": document_sha256(result)}

    def _check_native_source(self, row, cash, inventory, budget):
        """Reuse pinned native posting helpers; no duplicate FIFO basis engine."""
        payload = json.loads(row["payload_json"], object_pairs_hook=_unique)
        transactions = self.database.execute("""SELECT transaction_id FROM journal_transactions
            WHERE portfolio_id=? AND external_ref=? ORDER BY rowid""",
            (row["portfolio_id"], row["external_ref"]))
        actual = []
        for transaction in transactions:
            group = []
            for posting in self.database.execute("""SELECT account,asset,amount FROM journal_postings
                WHERE transaction_id=? ORDER BY rowid""", (transaction["transaction_id"],)):
                document = dict(posting)
                budget.consume(document)
                group.append(document)
                if len(group) > 512:
                    raise StaleState("protected native posting group exceeds bound")
            actual.append(group)
            if len(actual) > 8:
                raise StaleState("protected native financial source has too many transaction groups")
        if row["kind"] == "fill_chronological_replay":
            expected = [payload["postings"]] if payload["postings"] else []
        elif row["kind"] == "fill" and payload.get("projection_deferred"):
            expected = []
        else:
            # Dummy lots supply current native quantities to the pinned fill
            # posting helper. Zero basis has no role in comparing native groups;
            # actual basis/realized/economic checks remain in Ledger authority.
            quote = payload.get("quote_asset", "USD")
            books = Books(cash=dict(cash), lots=[Lot("native-only-" + asset, asset, amount, Decimal(0),
                quote, "0000", "native-quantity-only") for asset, amount in inventory.items() if amount > 0])
            if row["kind"] == "expense" and payload.get("settles"):
                source = self.database.execute("""SELECT 1 FROM ledger_events WHERE portfolio_id=?
                    AND kind='expense' AND sequence<? AND json_extract(payload_json,'$.expense_id')=? LIMIT 1""",
                    (row["portfolio_id"], row["sequence"], payload["settles"])).fetchone()
                if source is None:
                    raise StaleState("protected expense settlement has no original source")
                books.expenses.append(Expense(payload["settles"], Decimal(0), "EUR", Decimal(0), "EUR",
                                              False, "native-source-only", "accrued", "0000"))
            try:
                self.financial.ledger._mutate(books, row["kind"], payload, row["effective_at"], row["external_ref"])
            except (ValueError, KeyError, ArithmeticError):
                raise StaleState("protected native financial source cannot be replayed") from None
            expected = [[{"account": item.account, "asset": item.asset, "amount": canonical_decimal(item.amount)}
                         for item in group] for group in books.groups]
        if actual != expected:
            raise StaleState("protected native journal differs from its exact financial source postings")
        for group in actual:
            for posting in group:
                account, asset, amount = posting["account"], posting["asset"], Decimal(posting["amount"])
                target = (cash if account == "cash" or account.startswith("cash:")
                          else inventory if account == "inventory" else None)
                if target is not None:
                    with localcontext() as context:
                        context.prec, context.traps[Inexact] = 28, True
                        target[asset] = target.get(asset, Decimal(0)) + amount
            if len(cash) + len(inventory) > 256:
                raise StaleState("protected native current source exceeds asset bound")

    def _check_ledger_row(self, row, source_manifest, source_count, deferred):
        known = {"deposit", "withdraw", "internal", "fill", "expense", "fill_chronological_replay"}
        if row["kind"] not in known:
            raise StaleState("protected ledger contains an unsupported financial event kind")
        try:
            payload = json.loads(row["payload_json"], object_pairs_hook=_unique)
            if type(payload) is not dict:
                raise ValueError
            if row["kind"] == "fill_chronological_replay":
                keys = {"schema_version", "projection_version", "source_manifest", "previous_projection_sha256",
                        "current_projection_sha256", "new_fill_event_id", "postings"}
                if (set(payload) != keys or type(payload["schema_version"]) is not int
                        or payload["schema_version"] != 1 or payload["projection_version"] != "chronological_replay_v1"
                        or not deferred or payload["new_fill_event_id"] != deferred["event_id"]
                        or row["effective_at"] != deferred["effective_at"]
                        or row["external_ref"] != "chronological-replay:" + deferred["event_id"]
                        or type(payload["source_manifest"]) is not list
                        or len(payload["source_manifest"]) != source_count or source_count > 4096
                        or type(payload["postings"]) is not list or len(payload["postings"]) > 512):
                    raise ValueError
                manifest = hashlib.sha256()
                for item in payload["source_manifest"]:
                    if type(item) is not dict or set(item) != {"event_id", "sha256"}:
                        raise ValueError
                    manifest.update(canonical_json(item).encode() + b"\0")
                if manifest.hexdigest() != source_manifest.hexdigest():
                    raise ValueError
                for name in ("previous_projection_sha256", "current_projection_sha256"):
                    if (type(payload[name]) is not str or len(payload[name]) != 64
                            or any(char not in "0123456789abcdef" for char in payload[name])):
                        raise ValueError
                actual = [dict(item) for item in self.database.execute("""SELECT p.account,p.asset,p.amount
                    FROM journal_postings p JOIN journal_transactions j ON j.transaction_id=p.transaction_id
                    WHERE j.portfolio_id=? AND j.external_ref=? ORDER BY p.account,p.asset""",
                    (row["portfolio_id"], row["external_ref"]))]
                if actual != payload["postings"]:
                    raise ValueError
                return None
            if deferred:
                raise ValueError
            if row["kind"] == "fill" and "projection_deferred" in payload:
                if payload["projection_deferred"] != "chronological_replay_v1":
                    raise ValueError
                return {"event_id": row["event_id"], "effective_at": row["effective_at"]}
            return None
        except (ValueError, TypeError, KeyError, RecursionError):
            raise StaleState("protected ledger native replay source linkage refused") from None

    def _native_journal(self, portfolio_id, snapshot_at, cash, inventory, budget):
        db = self.database
        if db.execute("""SELECT 1 FROM journal_postings p LEFT JOIN journal_transactions j
            ON j.transaction_id=p.transaction_id WHERE p.portfolio_id=?
            AND (j.transaction_id IS NULL OR j.portfolio_id!=p.portfolio_id) LIMIT 1""", (portfolio_id,)).fetchone():
            raise StaleState("protected native posting has no matching portfolio transaction")
        if db.execute("""SELECT 1 FROM journal_transactions j LEFT JOIN ledger_events e
            ON e.portfolio_id=j.portfolio_id AND e.external_ref=j.external_ref WHERE j.portfolio_id=?
            AND (e.event_id IS NULL OR e.kind!=j.kind OR e.effective_at!=j.created_at) LIMIT 1""",
            (portfolio_id,)).fetchone():
            raise StaleState("protected native journal has no exact financial source")
        if db.execute("""SELECT 1 FROM ledger_events e WHERE e.portfolio_id=? AND
            (e.kind IN ('deposit','withdraw','internal') OR
             (e.kind='fill' AND json_extract(e.payload_json,'$.projection_deferred') IS NULL))
            AND NOT EXISTS (SELECT 1 FROM journal_transactions j
                            WHERE j.portfolio_id=e.portfolio_id AND j.external_ref=e.external_ref) LIMIT 1""",
            (portfolio_id,)).fetchone():
            raise StaleState("protected financial source is missing its native journal")
        transaction, balances = None, {}
        query = """SELECT p.transaction_id,p.account,p.asset,p.amount,j.created_at
            FROM journal_postings p JOIN journal_transactions j ON j.transaction_id=p.transaction_id
            WHERE p.portfolio_id=? ORDER BY p.transaction_id,p.posting_id"""
        for row in db.execute(query, (portfolio_id,)):
            document = dict(row)
            budget.consume(document)
            if transaction != document["transaction_id"]:
                if any(balances.values()):
                    raise StaleState("protected native journal transaction does not balance per asset")
                transaction, balances = document["transaction_id"], {}
            asset, amount = document["asset"], Decimal(document["amount"])
            if not amount.is_finite() or not asset or len(asset) > 128:
                raise StaleState("protected native journal amount or asset refused")
            with localcontext() as context:
                context.prec, context.traps[Inexact] = 28, True
                balances[asset] = balances.get(asset, Decimal(0)) + amount
                if document["created_at"] <= snapshot_at:
                    account = document["account"]
                    target = (cash if account == "cash" or account.startswith("cash:")
                              else inventory if account == "inventory" else None)
                    if target is not None:
                        target[asset] = target.get(asset, Decimal(0)) + amount
            if len(balances) > 128 or len(cash) + len(inventory) > 256:
                raise StaleState("protected native current state exceeds asset bound")
        if any(balances.values()):
            raise StaleState("protected native journal transaction does not balance per asset")

    def verify_bootstrap(self, portfolio_id: str, scan: dict):
        """Audit existing initial native state with the pinned Ledger authority.

        The streaming checkpoint deliberately does not implement another FIFO
        engine. A first deployment over already long financial history needs a
        separately prepared continuity proof; it cannot fabricate one here.
        """
        limits = self.database.execute("""SELECT count(*) AS rows,
            coalesce(sum(length(CAST(payload_json AS BLOB))),0) AS bytes
            FROM ledger_events WHERE portfolio_id=?""", (portfolio_id,)).fetchone()
        if limits["rows"] > MAXIMUM_BOOTSTRAP_LEDGER_ROWS or limits["bytes"] > MAXIMUM_BOOTSTRAP_LEDGER_BYTES:
            raise StaleState("first protected owner admission exceeds complete native ledger audit bound")
        native = self._ledger_probe(portfolio_id, limits["rows"])
        self._match_native_projection(native,
            {key: Decimal(value) for key, value in scan["financial"]["cash"].items()},
            {key: Decimal(value) for key, value in scan["financial"]["inventory"].items()})

    def _ledger_probe(self, portfolio_id, sequence, *, budget=None):
        limits = self.database.execute("""SELECT count(*) AS rows,
            coalesce(sum(length(CAST(payload_json AS BLOB))),0) AS bytes
            FROM ledger_events WHERE portfolio_id=? AND sequence<=?""", (portfolio_id, sequence)).fetchone()
        if limits["rows"] > 4096 or limits["bytes"] > 8_388_608:
            raise StaleState("complete protected native Ledger proof exceeds replay bound")
        rows = [dict(row) for row in self.database.execute("""SELECT * FROM ledger_events
            WHERE portfolio_id=? AND sequence<=? ORDER BY sequence""", (portfolio_id, sequence))]
        raw = canonical_json(rows).encode()
        if len(raw) > 12_582_912:
            raise StaleState("complete protected native Ledger wire proof exceeds bound")
        remaining = MAXIMUM_SECONDS - (monotonic() - budget.started) if budget else MAXIMUM_SECONDS
        if remaining <= 0:
            raise StaleState("protected native Ledger verification deadline exceeded")
        helper = Path(__file__).with_name("ledger_replay_probe.py")
        self.financial.manifest.assert_current()
        result = run_bounded([sys.executable, "-I", "-B", str(helper)], raw,
                             cwd=str(helper.parent), wall_seconds=min(2, remaining))
        self.financial.manifest.assert_current()
        if result["exit_code"] != 0:
            raise StaleState("owner-pinned complete native Ledger verification refused")
        try:
            native = json.loads(result["stdout"], object_pairs_hook=_unique)
            if type(native) is not dict or set(native) != {"cash", "inventory", "projection_sha256"}:
                raise ValueError
            if any(type(native[name]) is not dict or len(native[name]) > 256 for name in ("cash", "inventory")):
                raise ValueError
            return native
        except (ValueError, TypeError, RecursionError):
            raise StaleState("protected native Ledger projection receipt refused") from None

    @staticmethod
    def _match_native_projection(native, cash, inventory):
        if (any(cash.get(asset, Decimal(0)) != Decimal(native["cash"].get(asset, "0"))
                for asset in set(cash) | set(native["cash"]))
                or any(inventory.get(asset, Decimal(0)) != Decimal(native["inventory"].get(asset, "0"))
                       for asset in set(inventory) | set(native["inventory"]))):
            raise StaleState("protected native journal disagrees with owner-pinned Ledger authority")

    def retain(self, portfolio_id: str, scan: dict, previous: dict | None) -> dict:
        same_manifest = previous and previous["manifest_sha256"] == self.manifest.sha256
        if same_manifest and previous["state_sha256"] == scan["state_sha256"]:
            return previous
        generation = previous["generation"] + 1 if same_manifest else 1
        if generation > 1_000_000:
            raise StaleState("protected financial checkpoint generation bound exceeded")
        payload = {"schema_version": 1, "manifest_sha256": self.manifest.sha256,
                   "portfolio_id": portfolio_id, "generation": generation,
                   "previous_sha256": document_sha256(previous) if previous else None, **scan}
        raw = canonical_json(payload)
        if len(raw.encode()) > MAXIMUM_WITNESS_BYTES:
            raise StaleState("protected financial checkpoint payload exceeds bound")
        self.database.execute("""INSERT INTO protected_financial_checkpoints
            (checkpoint_id,manifest_sha256,portfolio_id,generation,payload_json,authentication,created_at)
            VALUES (?,?,?,?,?,?,?)""", (document_sha256(payload), self.manifest.sha256, portfolio_id,
            generation, raw, self._mac(payload), self.financial.ledger.now()))
        return payload

    def publish(self, portfolio_id: str, receipt: dict, witness: dict | None, directory: int):
        identity = self._database_identity()
        payload = witness or {"schema_version": 1, "database_identity": identity,
                              "scopes": {}}
        scope, digest = self._scope(portfolio_id), document_sha256(receipt)
        payload["scopes"][scope] = {"generation": receipt["generation"], "checkpoint_sha256": digest}
        self._write_witness(directory, payload)
        self._highwater[scope] = receipt["generation"], digest

    def ready(self, portfolio_id: str) -> bool:
        try:
            with self.witness_lock() as directory:
                previous, _ = self.previous(portfolio_id, directory)
                self.financial.origins.verify_all()
                if previous is not None and self.database.execute(
                        "SELECT 1 FROM protected_financial_recoveries LIMIT 1").fetchone():
                    portfolio = self.database.execute("SELECT * FROM portfolios WHERE portfolio_id=?",
                                                      (portfolio_id,)).fetchone()
                    state = {"portfolio": dict(portfolio),
                        "policy": self.financial.authority.active_policy().model_dump(mode="json"),
                        "mandate": self.financial.authority.active_mandate(portfolio_id).model_dump(mode="json"),
                        "snapshot_at": utc_iso(self.financial.clock.now())}
                    self.scan(portfolio_id, state, previous=previous)
                return previous is not None
        except (OSError, StaleState, ValueError):
            return False
