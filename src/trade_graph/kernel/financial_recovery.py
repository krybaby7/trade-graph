"""Offline authenticated paper recovery into a separate, explicitly approved inode.

No data salvage, reseal, fresh authority, model task replay or resume is provided.
The owner must independently substantiate backup cutoff/incident continuity before
installing the exact inspection proposal in the protected owner mount.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import json
import re
import sqlite3
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

from trade_graph.adapters.persistence.db import exclusive_database_path
from trade_graph.domain.clock import parse_utc, utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState
from trade_graph.kernel.financial_checkpoint import MAXIMUM_WITNESS_BYTES, ScanBudget, _unique
from trade_graph.kernel.financial_transition import FinancialManifestTransition, _checkpoint, historical_manifest
from trade_graph.kernel.runtime_manifest import canonical_json, document_sha256

MAXIMUM_RECOVERY_BYTES = 1_048_576
APPROVAL_FIELDS = {"schema_version", "operation_id", "source_database_identity", "candidate_database_identity",
                   "deployment_id", "operator_manifest", "source_manifests", "source_witness_sha256",
                   "source_witness_authentication",
                   "source_scopes", "complete_state", "portfolio_states", "witness_differences", "proof_limitations",
                   "incident_evidence_sha256", "authority_fences", "recovered_financial_commitments",
                   "terminal_subscription_rows", "append_only_rows", "expires_at"}
PARTIAL_APPROVAL_FIELDS = APPROVAL_FIELDS | {"history_incident"}


def _approval_shape(approval):
    return (type(approval) is dict and type(approval.get("schema_version")) is int
        and ((approval["schema_version"] == 1 and set(approval) == APPROVAL_FIELDS)
             or (approval["schema_version"] == 2 and set(approval) == PARTIAL_APPROVAL_FIELDS)))


def _identity(value):
    return type(value) is list and len(value) == 2 and all(type(item) is int and item >= 0 for item in value)


def recovery_mac(history, payload):
    return hmac.new(history.key, b"protected-financial-recovery-v1\0"
                    + canonical_json(payload).encode(), hashlib.sha256).hexdigest()


def _row(history, operation_id):
    header = history.database.execute("""SELECT length(CAST(payload_json AS BLOB)) AS bytes
        FROM protected_financial_recoveries WHERE operation_id=?""", (operation_id,)).fetchone()
    if header is None:
        return None
    if header["bytes"] > MAXIMUM_RECOVERY_BYTES:
        raise StaleState("protected financial recovery receipt exceeds bound")
    return history.database.execute("""SELECT * FROM protected_financial_recoveries WHERE operation_id=?
        AND length(CAST(payload_json AS BLOB))<=?""", (operation_id, MAXIMUM_RECOVERY_BYTES)).fetchone()


def load_recovery(history, row):
    try:
        if row is None or len(row["payload_json"].encode()) > MAXIMUM_RECOVERY_BYTES:
            raise ValueError
        payload = json.loads(row["payload_json"], object_pairs_hook=_unique)
        approval = payload["approval"]
        source = payload["source_witness"]
        manifest = historical_manifest(approval["operator_manifest"])
        originals = [historical_manifest(item) for item in approval["source_manifests"]]
        if (type(payload) is not dict or set(payload) != {
                "schema_version", "operation_id", "approval", "approval_sha256", "source_witness",
                "committed_state", "created_at"}
                or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
                or not _approval_shape(approval)
                or payload["operation_id"] != row["operation_id"]
                or payload["operation_id"] != approval["operation_id"]
                or payload["approval_sha256"] != row["approval_sha256"]
                or payload["approval_sha256"] != document_sha256(approval)
                or document_sha256(payload) != row["recovery_sha256"]
                or payload["created_at"] != row["created_at"]
                or parse_utc(payload["created_at"]) > history.financial.clock.now()
                or parse_utc(payload["created_at"]) >= parse_utc(approval["expires_at"])
                or not _identity(approval["source_database_identity"])
                or not _identity(approval["candidate_database_identity"])
                or approval["source_database_identity"] == approval["candidate_database_identity"]
                or source["database_identity"] != approval["source_database_identity"]
                or source["scopes"] != approval["source_scopes"]
                or document_sha256(source) != approval["source_witness_sha256"]
                or not hmac.compare_digest(approval["source_witness_authentication"], history._mac(source))
                or not originals or len(originals) > 128
                or len({item.sha256 for item in originals}) != len(originals)
                or any(item.deployment_id != manifest.deployment_id for item in originals)
                or approval["deployment_id"] != manifest.deployment_id
                or not hmac.compare_digest(row["authentication"], recovery_mac(history, payload))):
            raise ValueError
        if approval["schema_version"] == 2:
            from trade_graph.kernel.recovery_history import validate_history_incident

            validate_history_incident(approval["history_incident"], now=parse_utc(payload["created_at"]))
        return payload
    except (ValueError, TypeError, KeyError, RecursionError, AuthorityDenied):
        raise StaleState("protected financial recovery authentication refused") from None


def destination_witness(payload):
    result = copy.deepcopy(payload["source_witness"])
    result["schema_version"] = 3
    result.setdefault("transitions", [])
    result["database_identity"] = payload["approval"]["candidate_database_identity"]
    result["recoveries"] = [*result.get("recoveries", []), document_sha256(payload)]
    return result


def verify_recovery_chain(history, witness, *, allow_pending=False):
    rows = history.database.execute("""SELECT operation_id,recovery_sha256,
        length(CAST(payload_json AS BLOB)) AS bytes FROM protected_financial_recoveries LIMIT 129""").fetchall()
    if len(rows) > 128 or any(row["bytes"] > MAXIMUM_RECOVERY_BYTES for row in rows):
        raise StaleState("protected financial recovery history exceeds bound")
    known = witness.get("recoveries", []) if witness else []
    by_hash = {row["recovery_sha256"]: row for row in rows}
    extra = set(by_hash) - set(known)
    if set(known) - set(by_hash) or extra and (not allow_pending or len(extra) != 1):
        raise StaleState("protected financial recovery lacks matching independent witness publication")
    previous_identity = None
    for index, digest in enumerate(known):
        payload = load_recovery(history, _row(history, by_hash[digest]["operation_id"]))
        source, approval = payload["source_witness"], payload["approval"]
        if (source.get("recoveries", []) != known[:index]
                or previous_identity is not None and source["database_identity"] != previous_identity
                or source.get("transitions", [])
                != witness.get("transitions", [])[:len(source.get("transitions", []))]):
            raise StaleState("protected financial recovery source chain refused")
        for scope, observed in source["scopes"].items():
            retained = _checkpoint(history, observed["checkpoint_sha256"])
            if (retained["generation"] != observed["generation"]
                    or scope != document_sha256([retained["manifest_sha256"], retained["portfolio_id"]])):
                raise StaleState("protected financial recovery source checkpoint changed")
        previous_identity = approval["candidate_database_identity"]
    if previous_identity is not None and (not witness or witness["database_identity"] != previous_identity):
        raise StaleState("protected financial recovery destination identity refused")


def recovered_prefixes(history, portfolio_id, *, budget=None):
    """Recovery establishes new retention proof without replacing historic proof."""
    budget = budget or ScanBudget()
    rows = history.database.execute("""SELECT operation_id,length(CAST(payload_json AS BLOB)) AS bytes
        FROM protected_financial_recoveries LIMIT 129""").fetchall()
    if len(rows) > 128 or any(row["bytes"] > MAXIMUM_RECOVERY_BYTES for row in rows):
        raise StaleState("protected financial recovery history exceeds bound")
    baselines, append_only = [], []
    terminals = {"subscription_invocations": {}, "subscription_attempts": {}}
    for row in rows:
        budget.check()
        payload = load_recovery(history, _row(history, row["operation_id"]))
        approval = payload["approval"]
        if approval["deployment_id"] != history.manifest.deployment_id:
            raise StaleState("protected recovery budget deployment changed")
        if portfolio_id in approval["recovered_financial_commitments"]:
            baselines.append(approval["recovered_financial_commitments"][portfolio_id])
            append_only.append(approval["append_only_rows"][portfolio_id])
        for table, records in approval["terminal_subscription_rows"].items():
            if table not in terminals or type(records) is not dict or len(records) > 10000:
                raise StaleState("protected terminal subscription recovery commitment refused")
            for identity, digest in records.items():
                if (type(identity) is not str or len(identity) > 512 or type(digest) is not str or len(digest) != 64
                        or identity in terminals[table] and terminals[table][identity] != digest):
                    raise StaleState("protected terminal subscription recovery commitments conflict")
                terminals[table][identity] = digest
                if len(terminals[table]) > 10000:
                    raise StaleState("protected terminal subscription retention exceeds complete bound")
    return baselines, terminals, append_only


def append_only_commitments(database, portfolio_id, *, previous=(), budget=None):
    """Point-in-time marks, FX observations and dated price cards append by rowid."""
    external_budget = budget is not None
    budget, result = budget or ScanBudget(), {}
    with nullcontext() if external_budget else _bounded_sql(database, budget):
        for table, condition, params in (("valuation_marks", "portfolio_id=?", (portfolio_id,)),
                                         ("fx_rates", "1", ()), ("price_cards", "1", ())):
            columns = [row["name"] for row in database.execute(f"PRAGMA table_info({table})")]
            size = "+".join(f"coalesce(length(CAST({column} AS BLOB)),0)" for column in columns)
            if database.execute(f"SELECT 1 FROM {table} WHERE ({condition}) AND ({size})>65536 LIMIT 1",
                                params).fetchone():
                raise StaleState("recovered append-only financial row exceeds bound")
            retained = [item[table] for item in previous]
            counts = {item["rows"] for item in retained}
            digest, count = hashlib.sha256(), 0
            prefixes = {0: digest.hexdigest()}
            query = f"SELECT rowid AS _recovery_rowid,* FROM {table} WHERE {condition} ORDER BY rowid"
            for row in database.execute(query, params):
                digest.update(budget.consume(dict(row)) + b"\0")
                count += 1
                if count in counts:
                    prefixes[count] = digest.hexdigest()
            if any(count < item["rows"] or prefixes.get(item["rows"]) != item["sha256"] for item in retained):
                raise StaleState("protected recovered append-only financial prefix changed or disappeared")
            result[table] = {"rows": count, "sha256": digest.hexdigest()}
    return result


def terminal_subscription_rows(database):
    budget, result = ScanBudget(), {}
    with _bounded_sql(database, budget):
        for table, identity in (("subscription_invocations", "invocation_id"), ("subscription_attempts", "attempt_id")):
            columns = [row["name"] for row in database.execute(f"PRAGMA table_info({table})")]
            size = "+".join(f"coalesce(length(CAST({column} AS BLOB)),0)" for column in columns)
            if database.execute(f"SELECT 1 FROM {table} WHERE ({size})>65536 LIMIT 1").fetchone():
                raise StaleState("recovered terminal subscription row exceeds bound")
            records = {}
            for row in database.execute(f"SELECT * FROM {table} WHERE state!='DISPATCHED' ORDER BY rowid"):
                document = dict(row)
                records[document[identity]] = hashlib.sha256(budget.consume(document)).hexdigest()
                if len(records) > 10000:
                    raise StaleState("recovered terminal subscription count exceeds bound")
            result[table] = records
    return result


@contextmanager
def _bounded_sql(database, budget):
    def interrupted():
        try:
            budget.check()
            return 0
        except StaleState:
            return 1
    database.connection.set_progress_handler(interrupted, 1000)
    try:
        yield
    except sqlite3.OperationalError as exc:
        if str(exc) == "interrupted":
            raise StaleState("recovery SQLite verification deadline exceeded") from None
        raise
    finally:
        database.connection.set_progress_handler(None, 0)


def complete_state(database):
    budget = ScanBudget()
    with _bounded_sql(database, budget):
        return _complete_state(database, budget)


def _complete_state(database, budget):
    """Bound every retained logical row and schema, including failed/unknown attempts.

    Only recovery receipts themselves are excluded, so an interrupted receipt can
    be verified against exactly the precommit state. Later normal activity need
    not match this historical receipt; normal financial highwaters still apply.
    """
    tables = {}
    if database.execute("""SELECT 1 FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'
        AND (length(CAST(sql AS BLOB))>65536 OR length(name)>128 OR length(tbl_name)>128) LIMIT 1""").fetchone():
        raise StaleState("recovery schema item exceeds retained bound")
    schema = [dict(row) for row in database.execute("""SELECT type,name,tbl_name,sql FROM sqlite_schema
        WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name LIMIT 2049""")]
    if len(schema) > 2048:
        raise StaleState("recovery schema exceeds retained bound")
    for item in schema:
        budget.consume(item)
    for item in schema:
        if item["type"] != "table" or item["name"] == "protected_financial_recoveries":
            continue
        name = item["name"]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise StaleState("recovery requires the fixed application schema")
        columns = [row["name"] for row in database.execute(f'PRAGMA table_info("{name}")')]
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column) for column in columns):
            raise StaleState("recovery schema column refused")
        size = "+".join(f'coalesce(length(CAST("{column}" AS BLOB)),0)' for column in columns)
        if database.execute(f'SELECT 1 FROM "{name}" WHERE ({size})>? LIMIT 1',
                            (MAXIMUM_RECOVERY_BYTES,)).fetchone():
            raise StaleState("recovery retained row exceeds bound")
        digest, count = hashlib.sha256(), 0
        for row in database.execute(f'SELECT rowid AS _recovery_rowid,* FROM "{name}" ORDER BY rowid'):
            digest.update(budget.consume(dict(row), MAXIMUM_RECOVERY_BYTES) + b"\0")
            count += 1
        tables[name] = {"rows": count, "sha256": digest.hexdigest()}
    return {"schema_sha256": document_sha256(schema), "tables": tables,
            "sha256": document_sha256({"schema": schema, "tables": tables})}


class FinancialDatabaseRecovery:
    """Trusted offline identity transition; no mutable or HTTP recovery endpoint."""

    def __init__(self, financial):
        self.financial, self.history, self.database = financial, financial.history, financial.database
        self.transition = FinancialManifestTransition(financial)

    def inspect(self, **kwargs):
        with self.database.exclusive_lock("financial-database-recovery"):
            with self.history.witness_lock() as directory:
                return self._inspect_locked(directory=directory, **kwargs)

    def _inspect_locked(self, *, source_database_identity, source_manifests, operation_id, incident_evidence_sha256,
                        expires_at, directory, allow_pending=False, history_incident=None):
        self.financial.manifest.assert_current()
        if (self.database.connection.in_transaction or not _identity(source_database_identity)
                or source_database_identity == self.history._database_identity()
                or type(operation_id) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", operation_id)
                or type(incident_evidence_sha256) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", incident_evidence_sha256)
                or not allow_pending and not self.financial.clock.now() < parse_utc(expires_at)
                <= self.financial.clock.now() + timedelta(hours=1)):
            raise AuthorityDenied("explicit recovery identities, evidence and fresh bounded approval required")
        originals = [historical_manifest(item) for item in source_manifests]
        if (not originals or len(originals) > 128 or len({item.sha256 for item in originals}) != len(originals)
                or any(item.deployment_id != self.financial.manifest.deployment_id for item in originals)):
            raise AuthorityDenied("recovery requires exact original manifests for the unchanged deployment")
        with _bounded_sql(self.database, ScanBudget()):
            integrity = self.database.execute("PRAGMA integrity_check").fetchmany(2)
            if (len(integrity) != 1 or integrity[0][0] != "ok"
                    or self.database.execute("PRAGMA foreign_key_check").fetchone()):
                raise StaleState("recovery SQLite integrity or foreign keys refused")
        with self.database.immediate():
            portfolios = self.transition._pause_and_portfolios()
            witness = self.history._read_witness(directory, recovery_source_identity=source_database_identity)
            if not witness or not witness["scopes"]:
                raise StaleState("recovery requires the existing authenticated financial witness")
            scopes, edges = self.history.verified_scopes(witness, allow_pending_recovery=allow_pending)
            active = {item["portfolio_id"]: item for scope, item in scopes.items() if scope not in edges}
            if (set(active) != {row["portfolio_id"] for row in portfolios}
                    or len(active) != len(scopes) - len(edges)
                    or {item["manifest_sha256"] for item in active.values()} != {item.sha256 for item in originals}):
                raise StaleState("recovery requires all original financial scopes and portfolios")
            retained = self.database.execute("""SELECT DISTINCT manifest_sha256,portfolio_id
                FROM protected_financial_checkpoints LIMIT 129""").fetchall()
            if {document_sha256([row[0], row[1]]) for row in retained} != set(scopes):
                raise StaleState("recovery contains a financial scope absent from the original witness")
            states, differences, limitations, recovered, append_only = {}, {}, {}, {}, {}
            for portfolio in portfolios:
                pid, previous = portfolio["portfolio_id"], active[portfolio["portfolio_id"]]
                scan = self.transition._scan(portfolio, previous)
                self.history.verify_bootstrap(pid, scan)
                changes = {}
                for table, commitment in previous["tables"].items():
                    current = scan["tables"].get(table)
                    if current is None or current["rows"] < commitment["rows"]:
                        raise StaleState("recovery lost a witnessed financial or attempt record")
                    if current != commitment:
                        changes[table] = {"witnessed": commitment, "candidate": current}
                limitations[pid] = {
                    "unwitnessed_tables": sorted(set(scan["tables"]) - set(previous["tables"])),
                    "unwitnessed_identity_prefixes": sorted(name for name, current in scan["tables"].items()
                        if "identity_sha256" in current and "identity_sha256" not in previous["tables"].get(name, {}))}
                differences[pid], states[pid] = changes, scan["state_sha256"]
                recovered[pid] = scan["tables"]
                append_only[pid] = append_only_commitments(self.database, pid)
            incident = None
            if history_incident is not None:
                from trade_graph.kernel.recovery_history import prior_period, validate_history_incident

                incident = validate_history_incident(history_incident, now=self.financial.clock.now())
                incident = validate_history_incident(incident, now=self.financial.clock.now(),
                    database=self.database, expected_previous_period=prior_period(self.history,
                        incident["portfolio_id"], witness=witness, exclude_operation=operation_id,
                        proposed_incident=incident))
            snapshot = complete_state(self.database)
            proposal = {"schema_version": 2 if incident else 1, "operation_id": operation_id,
                "source_database_identity": source_database_identity,
                "candidate_database_identity": self.history._database_identity(),
                "deployment_id": self.financial.manifest.deployment_id,
                "operator_manifest": asdict(self.financial.manifest),
                "source_manifests": [asdict(item) for item in sorted(originals, key=lambda item: item.sha256)],
                "source_witness_sha256": document_sha256(witness),
                "source_witness_authentication": self.history._mac(witness), "source_scopes": witness["scopes"],
                "complete_state": snapshot, "portfolio_states": states, "witness_differences": differences,
                "proof_limitations": limitations,
                "incident_evidence_sha256": incident_evidence_sha256, "authority_fences": self._authority_fences(),
                "recovered_financial_commitments": recovered,
                "terminal_subscription_rows": terminal_subscription_rows(self.database),
                "append_only_rows": append_only, "expires_at": expires_at}
            if incident:
                proposal["history_incident"] = incident
            return json.loads(canonical_json(proposal))

    def _authority_fences(self):
        instances = [dict(row) for row in self.database.execute("""SELECT instance_id,generation
            FROM protected_runtime_instances ORDER BY instance_id LIMIT 10001""")]
        issued = [row[0] for row in self.database.execute("""SELECT request_id FROM protected_rpc_requests
            WHERE state='ISSUED' ORDER BY request_id LIMIT 10001""")]
        if len(instances) > 10000 or len(issued) > 10000:
            raise StaleState("recovery controller fence exceeds retained bound")
        return {"instances": instances, "issued_request_ids": issued}

    def apply(self, approval, *, history_incident=None):
        with self.database.exclusive_lock("financial-database-recovery"):
            with self.history.witness_lock() as directory:
                return self._apply_locked(approval, directory, history_incident=history_incident)

    def _apply_locked(self, approval, directory, *, history_incident=None):
        self.financial.manifest.assert_current()
        if (self.database.connection.in_transaction or not _approval_shape(approval)
                or approval["candidate_database_identity"] != self.history._database_identity()
                or approval["operator_manifest"] != json.loads(canonical_json(asdict(self.financial.manifest)))
                or approval["deployment_id"] != self.financial.manifest.deployment_id):
            raise AuthorityDenied("owner recovery approval binding refused")
        if ((approval["schema_version"] == 2 and (history_incident is None
                or approval["history_incident"] != history_incident))
                or approval["schema_version"] == 1 and history_incident is not None):
            raise AuthorityDenied("recovery approval requires the identical independent owner history incident")
        row = _row(self.history, approval["operation_id"])
        if row is not None:
            payload = load_recovery(self.history, row)
            if payload["approval_sha256"] != document_sha256(approval):
                raise AuthorityDenied("committed recovery requires identical owner approval")
            self._recover(payload, directory)
            return self._result(payload)
        current = self._inspect_locked(directory=directory, history_incident=history_incident,
            **{key: approval[key] for key in (
            "source_database_identity", "source_manifests", "operation_id", "incident_evidence_sha256", "expires_at")})
        if current != approval:
            raise StaleState("owner recovery approval became stale or candidate state changed")
        with self.database.immediate() as connection:
            self.transition._pause_and_portfolios()
            if complete_state(self.database) != approval["complete_state"]:
                raise StaleState("recovery candidate state changed before commit")
            witness = self.history._read_witness(directory,
                recovery_source_identity=approval["source_database_identity"])
            if document_sha256(witness) != approval["source_witness_sha256"]:
                raise StaleState("recovery source witness changed before commit")
            sealed_at = self.financial.clock.now()
            if sealed_at >= parse_utc(approval["expires_at"]):
                raise AuthorityDenied("owner recovery approval expired before commit")
            if self._authority_fences() != approval["authority_fences"]:
                raise StaleState("recovery controller authority changed before commit")
            connection.execute("""UPDATE protected_runtime_instances
                SET status='MANAGE_ONLY',generation=generation+1,updated_at=?""", (utc_iso(sealed_at),))
            connection.execute("""UPDATE protected_rpc_requests SET state='REVOKED',updated_at=?
                WHERE state='ISSUED'""", (utc_iso(sealed_at),))
            committed = complete_state(self.database)
            payload = {"schema_version": 1, "operation_id": approval["operation_id"],
                "approval": copy.deepcopy(approval), "approval_sha256": document_sha256(approval),
                "source_witness": witness, "committed_state": committed, "created_at": utc_iso(sealed_at)}
            raw, destination = canonical_json(payload), destination_witness(payload)
            if (len(raw.encode()) > MAXIMUM_RECOVERY_BYTES or len(destination["recoveries"]) > 128
                    or len(canonical_json({"payload": destination,
                        "authentication": self.history._mac(destination)}).encode()) > MAXIMUM_WITNESS_BYTES):
                raise StaleState("complete recovery receipt or witness exceeds bound")
            connection.execute("""INSERT INTO protected_financial_recoveries
                (operation_id,recovery_sha256,approval_sha256,payload_json,authentication,created_at)
                VALUES (?,?,?,?,?,?)""", (payload["operation_id"], document_sha256(payload),
                payload["approval_sha256"], raw, recovery_mac(self.history, payload), payload["created_at"]))
            if self.financial.clock.now() >= parse_utc(approval["expires_at"]):
                raise AuthorityDenied("owner recovery approval expired before commit")
        self.history._write_witness(directory, destination)
        return self._result(payload)

    def _recover(self, payload, directory):
        approval = payload["approval"]
        # Authenticate only one of the two exact historical identities; never relax
        # the normal reader or infer permission from a missing witness.
        try:
            witness = self.history._read_witness(directory)
        except StaleState:
            witness = self.history._read_witness(directory,
                recovery_source_identity=approval["source_database_identity"])
        if witness and document_sha256(payload) in witness.get("recoveries", []):
            self.history.verified_scopes(witness)
            self.transition._pause_and_portfolios()
            return
        if witness != payload["source_witness"]:
            raise StaleState("interrupted recovery lost its exact original witness")
        current = self._inspect_locked(directory=directory, allow_pending=True,
            history_incident=approval.get("history_incident"), **{key: approval[key] for key in (
            "source_database_identity", "source_manifests", "operation_id", "incident_evidence_sha256", "expires_at")})
        if current["complete_state"] != payload["committed_state"]:
            raise StaleState("interrupted recovery candidate state changed; publication refused")
        current["complete_state"] = approval["complete_state"]
        current["authority_fences"] = approval["authority_fences"]
        if current != approval:
            raise StaleState("interrupted recovery financial or approval state changed; publication refused")
        self.history._write_witness(directory, destination_witness(payload))

    @staticmethod
    def _result(payload):
        result = {"status": "APPLIED", "operation_id": payload["operation_id"],
            "recovery_sha256": document_sha256(payload),
            "database_identity": payload["approval"]["candidate_database_identity"],
            "management_profile": "MANAGE_ONLY", "live_authorization": False, "paid_authorization": False}
        if payload["approval"]["schema_version"] == 2:
            result.update(history_status="PARTIAL_HISTORY",
                evaluation_period=payload["approval"]["history_incident"]["new_evaluation_period"])
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["inspect", "apply"])
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--protected-owner", required=True, type=Path)
    parser.add_argument("--source-manifest", action="append", type=Path, default=[])
    parser.add_argument("--source-device", type=int)
    parser.add_argument("--source-inode", type=int)
    parser.add_argument("--operation-id")
    parser.add_argument("--incident-evidence-sha256")
    parser.add_argument("--expires-at")
    parser.add_argument("--partial-price-history", action="store_true")
    args = parser.parse_args(argv)
    from trade_graph.adapters.brokers.paper import PaperBroker
    from trade_graph.adapters.persistence.db import Database
    from trade_graph.application.execution import Execution
    from trade_graph.application.ledger import Ledger
    from trade_graph.domain.clock import SystemClock
    from trade_graph.kernel.deployment_image import load_owner_runtime_manifest, read_owner_file
    from trade_graph.kernel.financial_service import ProtectedFinancialService

    try:
        with ExitStack() as lifetime:
            lifetime.enter_context(exclusive_database_path(args.database, "financial-database-recovery"))
            manifest = load_owner_runtime_manifest(args.protected_owner / "runtime-manifest.json")
            key = read_owner_file(args.protected_owner, "capability.key", 4096)
            clock, database = SystemClock(), Database(args.database)
            lifetime.callback(database.close)
            execution = Execution(database, Ledger(database, clock), clock, PaperBroker(database, clock))
            operator = FinancialDatabaseRecovery(ProtectedFinancialService(database, clock, execution,
                manifest=manifest, capability_key=key))
            with operator.history.witness_lock() as directory:
                if args.operation == "inspect":
                    if not args.source_manifest or None in (args.source_device, args.source_inode, args.operation_id,
                                args.incident_evidence_sha256, args.expires_at):
                        raise AuthorityDenied("inspection requires original inode, evidence, operation ID and expiry")
                    sources = [json.loads(read_owner_file(path.parent, path.name, 32768), object_pairs_hook=_unique)
                               for path in args.source_manifest]
                    incident = None
                    if args.partial_price_history:
                        from trade_graph.kernel.recovery_history import MAXIMUM_INCIDENT_BYTES

                        incident = json.loads(read_owner_file(args.protected_owner,
                            "financial-history-incident.json", MAXIMUM_INCIDENT_BYTES), object_pairs_hook=_unique)
                    result = operator._inspect_locked(directory=directory, source_manifests=sources,
                        history_incident=incident,
                        source_database_identity=[args.source_device, args.source_inode],
                        operation_id=args.operation_id, incident_evidence_sha256=args.incident_evidence_sha256,
                        expires_at=args.expires_at)
                else:
                    if args.partial_price_history or args.source_manifest or any(value is not None for value in (
                            args.source_device, args.source_inode, args.operation_id,
                                                          args.incident_evidence_sha256, args.expires_at)):
                        raise AuthorityDenied("apply accepts only fixed root-distributed recovery approval")
                    approval = json.loads(read_owner_file(args.protected_owner,
                        "financial-recovery-approval.json", MAXIMUM_RECOVERY_BYTES), object_pairs_hook=_unique)
                    incident = None
                    if type(approval) is dict and approval.get("schema_version") == 2:
                        from trade_graph.kernel.recovery_history import MAXIMUM_INCIDENT_BYTES

                        incident = json.loads(read_owner_file(args.protected_owner,
                            "financial-history-incident.json", MAXIMUM_INCIDENT_BYTES), object_pairs_hook=_unique)
                    result = operator._apply_locked(approval, directory, history_incident=incident)
            print(canonical_json(result))
            return 0
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, AuthorityDenied, StaleState) as exc:
        print(canonical_json({"status": "REFUSED", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
