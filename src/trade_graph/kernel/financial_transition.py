"""Offline owner-approved manifest continuity; no mutable reset or repair route.

The approval is read from the root-distributed owner mount by the operator CLI.
All committed transition receipts and original financial sources remain retained.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, fields
from datetime import timedelta
from pathlib import Path

from trade_graph.adapters.engineering.artifact_files import open_directory
from trade_graph.domain.clock import parse_utc, utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState
from trade_graph.kernel.financial_checkpoint import MAXIMUM_WITNESS_BYTES, _unique
from trade_graph.kernel.runtime_manifest import ProtectedRuntimeManifest, canonical_json, document_sha256

MAXIMUM_TRANSITION_BYTES = 262144
MAXIMUM_WITNESS_SCOPES = 128
APPROVAL_FIELDS = {"schema_version", "operation_id", "database_identity", "deployment_id",
                   "target_manifest", "source_manifests", "source_witness_sha256", "source_scopes",
                   "portfolio_states", "expires_at"}


def _document(value):
    return json.loads(canonical_json(value))


def historical_manifest(document):
    """Validate exact old approval bytes without asserting the old executable pin."""
    if type(document) is not dict or set(document) != {item.name for item in fields(ProtectedRuntimeManifest)}:
        raise AuthorityDenied("exact complete owner source manifest approval required")
    value = dict(document)
    for name in ("approved_source_sha256", "operations"):
        if type(value[name]) not in {tuple, list}:
            raise AuthorityDenied("owner source manifest arrays required")
        value[name] = tuple(value[name])
    return ProtectedRuntimeManifest(**value)


def transition_mac(history, payload):
    return hmac.new(history.key, b"protected-financial-transition-v1\0"
                    + canonical_json(payload).encode(), hashlib.sha256).hexdigest()


def load_transition(history, row):
    try:
        if row is None or len(row["payload_json"].encode()) > MAXIMUM_TRANSITION_BYTES:
            raise ValueError
        payload = json.loads(row["payload_json"], object_pairs_hook=_unique)
        if (type(payload) is not dict or set(payload) != {
                "schema_version", "operation_id", "approval", "approval_sha256",
                "source_witness", "links", "created_at"}
                or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
                or payload["operation_id"] != row["operation_id"]
                or payload["approval_sha256"] != row["approval_sha256"]
                or payload["approval_sha256"] != document_sha256(payload["approval"])
                or document_sha256(payload) != row["transition_sha256"]
                or payload["created_at"] != row["created_at"]
                or parse_utc(payload["created_at"]) > history.financial.clock.now()
                or not hmac.compare_digest(row["authentication"], transition_mac(history, payload))):
            raise ValueError
        return payload
    except (ValueError, TypeError, KeyError, RecursionError):
        raise StaleState("protected financial transition authentication refused") from None


def _transition_row(history, operation_id):
    header = history.database.execute("""SELECT length(CAST(payload_json AS BLOB)) AS bytes
        FROM protected_financial_transitions WHERE operation_id=?""", (operation_id,)).fetchone()
    if header is None:
        return None
    if header["bytes"] > MAXIMUM_TRANSITION_BYTES:
        raise StaleState("protected financial transition history exceeds bound")
    return history.database.execute("""SELECT * FROM protected_financial_transitions
        WHERE operation_id=? AND length(CAST(payload_json AS BLOB))<=?""",
        (operation_id, MAXIMUM_TRANSITION_BYTES)).fetchone()


def _checkpoint(history, checkpoint_id):
    header = history.database.execute("""SELECT length(CAST(payload_json AS BLOB)) AS bytes
        FROM protected_financial_checkpoints WHERE checkpoint_id=?""", (checkpoint_id,)).fetchone()
    if header is None or header["bytes"] > MAXIMUM_WITNESS_BYTES:
        raise StaleState("protected transition checkpoint disappeared or exceeds bound")
    row = history.database.execute("""SELECT * FROM protected_financial_checkpoints
        WHERE checkpoint_id=? AND length(CAST(payload_json AS BLOB))<=?""",
        (checkpoint_id, MAXIMUM_WITNESS_BYTES)).fetchone()
    try:
        if row is None:
            raise ValueError
        payload = json.loads(row["payload_json"], object_pairs_hook=_unique)
        if (document_sha256(payload) != checkpoint_id
                or payload["manifest_sha256"] != row["manifest_sha256"]
                or payload["portfolio_id"] != row["portfolio_id"]
                or payload["generation"] != row["generation"]
                or not hmac.compare_digest(row["authentication"], history._mac(payload))):
            raise ValueError
        return payload
    except (ValueError, TypeError, KeyError, RecursionError):
        raise StaleState("protected transition checkpoint authentication refused") from None


def destination_witness(payload):
    witness = copy.deepcopy(payload["source_witness"])
    witness["schema_version"] = 2
    witness["transitions"] = [*witness.get("transitions", []), document_sha256(payload)]
    for link in payload["links"]:
        witness["scopes"][link["to_scope"]] = {
            "generation": 1, "checkpoint_sha256": link["to_checkpoint_sha256"]}
    return witness


def verify_transition_chain(history, witness, scopes, *, allow_pending=False):
    """Reject retired scopes, removed receipts and a committed unpublished transition."""
    rows = history.database.execute("""SELECT operation_id,transition_sha256,
        length(CAST(payload_json AS BLOB)) AS bytes
        FROM protected_financial_transitions LIMIT 129""").fetchall()
    if len(rows) > 128 or any(row["bytes"] > MAXIMUM_TRANSITION_BYTES for row in rows):
        raise StaleState("protected financial transition history exceeds bound")
    known = witness.get("transitions", []) if witness else []
    by_hash = {row["transition_sha256"]: row for row in rows}
    extra = set(by_hash) - set(known)
    if set(known) - set(by_hash) or extra and (not allow_pending or len(extra) != 1):
        raise StaleState("protected financial transition lacks matching independent witness publication")
    edges, destinations = {}, set()
    for index, digest in enumerate(known):
        payload = load_transition(history, _transition_row(history, by_hash[digest]["operation_id"]))
        approval, source = payload["approval"], payload["source_witness"]
        try:
            target = historical_manifest(approval["target_manifest"])
            originals = {historical_manifest(item).sha256: historical_manifest(item)
                         for item in approval["source_manifests"]}
            if (set(approval) != APPROVAL_FIELDS or approval["source_witness_sha256"] != document_sha256(source)
                    or approval["source_scopes"] != source["scopes"]
                    or source.get("transitions", []) != known[:index]
                    or approval["database_identity"] != source["database_identity"]
                    or approval["deployment_id"] != target.deployment_id
                    or any(item.deployment_id != target.deployment_id for item in originals.values())
                    or type(payload["links"]) is not list or not 1 <= len(payload["links"]) <= 128):
                raise ValueError
            source_manifests, portfolios = set(), set()
            for link in payload["links"]:
                if type(link) is not dict or set(link) != {
                        "portfolio_id", "from_scope", "from_checkpoint_sha256", "to_scope", "to_checkpoint_sha256"}:
                    raise ValueError
                before = scopes[link["from_scope"]]
                after = _checkpoint(history, link["to_checkpoint_sha256"])
                if (link["from_scope"] in edges or link["to_scope"] in destinations
                        or link["to_scope"] in source["scopes"]
                        or before["manifest_sha256"] not in originals
                        or before["portfolio_id"] != link["portfolio_id"]
                        or after["portfolio_id"] != link["portfolio_id"]
                        or after["manifest_sha256"] != target.sha256 or after["generation"] != 1
                        or after["previous_sha256"] != link["from_checkpoint_sha256"]
                        or document_sha256(before) != link["from_checkpoint_sha256"]
                        or source["scopes"][link["from_scope"]] != {
                            "generation": before["generation"], "checkpoint_sha256": document_sha256(before)}
                        or link["to_scope"] != document_sha256([target.sha256, link["portfolio_id"]])
                        or link["to_scope"] not in scopes
                        or after["state_sha256"] != approval["portfolio_states"][link["portfolio_id"]]):
                    raise ValueError
                source_manifests.add(before["manifest_sha256"])
                portfolios.add(link["portfolio_id"])
                edges[link["from_scope"]] = link["to_scope"]
                destinations.add(link["to_scope"])
            if source_manifests != set(originals) or portfolios != set(approval["portfolio_states"]):
                raise ValueError
        except (ValueError, TypeError, KeyError, RecursionError):
            raise StaleState("protected financial transition source chain refused") from None
    for origin in edges:
        leaf, seen = origin, set()
        while leaf in edges:
            if leaf in seen:
                raise StaleState("protected financial transition cycle refused")
            seen.add(leaf)
            leaf = edges[leaf]
    return edges


@contextmanager
def exclusive_financial_database(path: Path):
    """The same native flock used by the persistent paper service and controller."""
    directory = descriptor = None
    try:
        directory = open_directory(path.absolute().parent)
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             dir_fd=directory)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise StaleState("protected financial transition requires a single regular database")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StaleState("another service or protected controller owns the financial database") from None
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


class FinancialManifestTransition:
    """Trusted offline operator; never exposed in mutable model RPC or tool context."""

    def __init__(self, financial):
        self.financial, self.history, self.database = financial, financial.history, financial.database

    def _pause_and_portfolios(self):
        rows = self.database.execute("SELECT * FROM portfolios ORDER BY portfolio_id").fetchall()
        if not 1 <= len(rows) <= 128 or any(row["mode"] != "paper" for row in rows):
            raise AuthorityDenied("financial transition requires a bounded paper-only database")
        for row in rows:
            pause = self.financial.execution.pause(row["portfolio_id"])
            if not pause or pause["profile"] != "MANAGE_ONLY" or pause["originator"] != "owner":
                raise AuthorityDenied("financial transition requires owner MANAGE_ONLY for every portfolio")
        return rows

    def _scan(self, portfolio, previous):
        state = {"portfolio": dict(portfolio),
                 "policy": self.financial.authority.active_policy().model_dump(mode="json"),
                 "mandate": self.financial.authority.active_mandate(portfolio["portfolio_id"]).model_dump(mode="json"),
                 "snapshot_at": utc_iso(self.financial.clock.now())}
        return self.history.scan(portfolio["portfolio_id"], state, previous=previous)

    def inspect(self, *, source_manifests, operation_id, expires_at):
        with exclusive_financial_database(self.database.path):
            return self._inspect_locked(source_manifests=source_manifests, operation_id=operation_id,
                                        expires_at=expires_at)

    def _inspect_locked(self, *, source_manifests, operation_id, expires_at):
        self.financial.manifest.assert_current()
        if self.database.connection.in_transaction:
            raise StaleState("financial transition requires an independent committed operation")
        if (type(operation_id) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", operation_id)
                or not self.financial.clock.now() < parse_utc(expires_at)
                <= self.financial.clock.now() + timedelta(hours=1)):
            raise AuthorityDenied("bounded operation identity and fresh owner approval expiry required")
        originals = [historical_manifest(item) for item in source_manifests]
        if not originals or len(originals) > 128 or len({item.sha256 for item in originals}) != len(originals):
            raise AuthorityDenied("complete unique owner source manifest approvals required")
        with self.history.witness_lock() as directory:
            with self.database.immediate():
                portfolios = self._pause_and_portfolios()
                witness = self.history._read_witness(directory)
                if not witness or not witness["scopes"]:
                    raise StaleState("owner transition requires existing authenticated financial witness")
                scopes, edges = self.history.verified_scopes(witness)
                active = {payload["portfolio_id"]: payload for scope, payload in scopes.items() if scope not in edges}
                if (set(active) != {row["portfolio_id"] for row in portfolios}
                        or len(active) != len(scopes) - len(edges)
                        or {value["manifest_sha256"] for value in active.values()}
                        != {item.sha256 for item in originals}
                        or any(item.deployment_id != self.financial.manifest.deployment_id for item in originals)
                        or any(self.history._scope(row["portfolio_id"]) in scopes for row in portfolios)):
                    raise AuthorityDenied("owner transition source manifest set or target deployment refused")
                # No orphan scope can be admitted by a transition or stale inspection.
                retained = self.database.execute("""SELECT DISTINCT manifest_sha256,portfolio_id
                    FROM protected_financial_checkpoints LIMIT 129""").fetchall()
                if {document_sha256([row[0], row[1]]) for row in retained} != set(scopes):
                    raise StaleState("financial checkpoint scope lacks retained independent witness")
                states = {row["portfolio_id"]: self._scan(row, active[row["portfolio_id"]])["state_sha256"]
                          for row in portfolios}
                approval = {"schema_version": 1, "operation_id": operation_id,
                    "database_identity": self.history._database_identity(),
                    "deployment_id": self.financial.manifest.deployment_id,
                    "target_manifest": asdict(self.financial.manifest),
                    "source_manifests": [asdict(item) for item in sorted(originals, key=lambda item: item.sha256)],
                    "source_witness_sha256": document_sha256(witness), "source_scopes": witness["scopes"],
                    "portfolio_states": states, "expires_at": expires_at}
                return _document(approval)

    def apply(self, approval):
        with exclusive_financial_database(self.database.path):
            return self._apply_locked(approval)

    def _apply_locked(self, approval):
        self.financial.manifest.assert_current()
        if (self.database.connection.in_transaction or type(approval) is not dict
                or set(approval) != APPROVAL_FIELDS or approval["schema_version"] != 1
                or type(approval["schema_version"]) is not int
                or approval["target_manifest"] != _document(asdict(self.financial.manifest))
                or approval["deployment_id"] != self.financial.manifest.deployment_id
                or approval["database_identity"] != self.history._database_identity()):
            raise AuthorityDenied("owner financial continuity approval binding refused")
        with self.history.witness_lock() as directory:
            row = _transition_row(self.history, approval["operation_id"])
            if row is not None:
                payload = load_transition(self.history, row)
                if payload["approval_sha256"] != document_sha256(approval):
                    raise AuthorityDenied("committed financial transition requires identical owner approval")
                self._recover(payload, directory)
                return self._result(payload)
            # Recreate the exact proposal from the current witness, complete retained
            # prefixes and owner pause before writing any new checkpoint or receipt.
            current = self._inspect_locked(source_manifests=approval["source_manifests"],
                operation_id=approval["operation_id"], expires_at=approval["expires_at"])
            if current != approval:
                raise StaleState("owner financial continuity approval became stale or state changed")
            with self.database.immediate() as connection:
                portfolios = self._pause_and_portfolios()
                witness = self.history._read_witness(directory)
                scopes, edges = self.history.verified_scopes(witness)
                links = []
                for portfolio in portfolios:
                    pid = portfolio["portfolio_id"]
                    before_scope, before = next((scope, item) for scope, item in scopes.items()
                                                if item["portfolio_id"] == pid and scope not in edges)
                    scan = self._scan(portfolio, before)
                    if scan["state_sha256"] != approval["portfolio_states"][pid]:
                        raise StaleState("financial transition inspected state changed")
                    after = self.history.retain(pid, scan, before)
                    links.append({"portfolio_id": pid, "from_scope": before_scope,
                        "from_checkpoint_sha256": document_sha256(before), "to_scope": self.history._scope(pid),
                        "to_checkpoint_sha256": document_sha256(after)})
                payload = {"schema_version": 1, "operation_id": approval["operation_id"],
                    "approval": copy.deepcopy(approval), "approval_sha256": document_sha256(approval),
                    "source_witness": witness, "links": links, "created_at": utc_iso(self.financial.clock.now())}
                raw = canonical_json(payload)
                if len(raw.encode()) > MAXIMUM_TRANSITION_BYTES:
                    raise StaleState("complete financial transition receipt exceeds bound")
                destination = destination_witness(payload)
                encoded_witness = canonical_json({"payload": destination,
                                                  "authentication": self.history._mac(destination)})
                if (len(destination["scopes"]) > MAXIMUM_WITNESS_SCOPES
                        or len(destination["transitions"]) > 128
                        or len(encoded_witness.encode()) > MAXIMUM_WITNESS_BYTES):
                    raise StaleState("complete financial transition witness exceeds retained bound")
                connection.execute("""INSERT INTO protected_financial_transitions
                    (operation_id,transition_sha256,approval_sha256,payload_json,authentication,created_at)
                    VALUES (?,?,?,?,?,?)""", (payload["operation_id"], document_sha256(payload),
                    payload["approval_sha256"], raw, transition_mac(self.history, payload), payload["created_at"]))
                # Retain original instance bindings for historical reservation origins.
                source_hashes = tuple(historical_manifest(item).sha256 for item in approval["source_manifests"])
                for digest in source_hashes:
                    connection.execute("""UPDATE protected_runtime_instances
                        SET status='MANAGE_ONLY',generation=generation+1,updated_at=? WHERE manifest_sha256=?""",
                        (payload["created_at"], digest))
                    connection.execute("""UPDATE protected_rpc_requests SET state='REVOKED',updated_at=?
                        WHERE state='ISSUED' AND json_extract(scope_json,'$.manifest_sha256')=?""",
                        (payload["created_at"], digest))
            self.history._write_witness(directory, destination_witness(payload))
            return self._result(payload)

    def _recover(self, payload, directory):
        witness = self.history._read_witness(directory)
        digest = document_sha256(payload)
        if witness and digest in witness.get("transitions", []):
            self.history.verified_scopes(witness)
            return
        if witness != payload["source_witness"]:
            raise StaleState("interrupted financial transition lost its exact source witness")
        with self.database.immediate():
            self._pause_and_portfolios()
            self.history.verified_scopes(witness, allow_pending=True)
            for link in payload["links"]:
                after = _checkpoint(self.history, link["to_checkpoint_sha256"])
                portfolio = self.database.execute("SELECT * FROM portfolios WHERE portfolio_id=?",
                                                  (link["portfolio_id"],)).fetchone()
                scan = self._scan(portfolio, after)
                if scan["state_sha256"] != after["state_sha256"]:
                    raise StaleState("interrupted financial transition state changed; publication refused")
                latest = self.database.execute("""SELECT max(generation) FROM protected_financial_checkpoints
                    WHERE manifest_sha256=? AND portfolio_id=?""",
                    (after["manifest_sha256"], link["portfolio_id"])).fetchone()[0]
                if latest != 1:
                    raise StaleState("interrupted financial transition checkpoint changed")
        self.history._write_witness(directory, destination_witness(payload))

    @staticmethod
    def _result(payload):
        return {"status": "APPLIED", "operation_id": payload["operation_id"],
                "transition_sha256": document_sha256(payload),
                "target_manifest_sha256": historical_manifest(payload["approval"]["target_manifest"]).sha256,
                "portfolio_ids": [link["portfolio_id"] for link in payload["links"]],
                "management_profile": "MANAGE_ONLY", "live_authorization": False, "paid_authorization": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["inspect", "apply"])
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--protected-owner", required=True, type=Path)
    parser.add_argument("--source-manifest", action="append", type=Path, default=[])
    parser.add_argument("--operation-id")
    parser.add_argument("--expires-at")
    args = parser.parse_args(argv)
    from trade_graph.adapters.brokers.paper import PaperBroker
    from trade_graph.adapters.persistence.db import Database
    from trade_graph.application.execution import Execution
    from trade_graph.application.ledger import Ledger
    from trade_graph.domain.clock import SystemClock
    from trade_graph.kernel.deployment_image import load_owner_runtime_manifest, read_owner_file
    from trade_graph.kernel.financial_service import ProtectedFinancialService

    try:
        # Lock before Database opens/migrates; a live worker never shares ownership.
        with ExitStack() as lifetime:
            lifetime.enter_context(exclusive_financial_database(args.database))
            manifest = load_owner_runtime_manifest(args.protected_owner / "runtime-manifest.json")
            key = read_owner_file(args.protected_owner, "capability.key", 4096)
            clock = SystemClock()
            database = Database(args.database)
            lifetime.callback(database.close)
            execution = Execution(database, Ledger(database, clock), clock, PaperBroker(database, clock))
            financial = ProtectedFinancialService(database, clock, execution, manifest=manifest, capability_key=key)
            operator = FinancialManifestTransition(financial)
            if args.operation == "inspect":
                if not args.source_manifest or not args.operation_id or not args.expires_at:
                    raise AuthorityDenied("inspection requires exact old owner manifests, operation ID and expiry")
                sources = [json.loads(read_owner_file(path.parent, path.name, 32768), object_pairs_hook=_unique)
                           for path in args.source_manifest]
                result = operator._inspect_locked(source_manifests=sources,
                    operation_id=args.operation_id, expires_at=args.expires_at)
            else:
                if args.source_manifest or args.operation_id or args.expires_at:
                    raise AuthorityDenied("apply reads only the exact root-owned financial continuity approval")
                approval = json.loads(read_owner_file(args.protected_owner,
                    "financial-continuity-approval.json", MAXIMUM_TRANSITION_BYTES), object_pairs_hook=_unique)
                result = operator._apply_locked(approval)
            print(canonical_json(result))
            return 0
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, AuthorityDenied, StaleState) as exc:
        print(canonical_json({"status": "REFUSED", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
