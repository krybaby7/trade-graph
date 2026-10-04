"""Actual, bounded local paper-process restart rehearsal in new synthetic storage.

This module supplies no systemd, intended-host, paid-provider or live authority.
Only fixed trusted package entry points execute; callers cannot supply commands.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import hmac
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from trade_graph.adapters.engineering.artifact_files import ensure_directory, open_directory
from trade_graph.adapters.engineering.provenance import scrubbed_env
from trade_graph.application.operations import _read_database, financial_report, private_backup
from trade_graph.application.service_binding import private_source
from trade_graph.domain.clock import FrozenClock, SystemClock, parse_utc, utc_iso
from trade_graph.kernel.runtime_manifest import canonical_json, protected_package_sha256

MAX_PROOF_BYTES = 65_536
MAX_DATABASE_BYTES = 16_777_216
BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); "
    "from trade_graph.application.service_proof import _entry; "
    "raise SystemExit(_entry(sys.argv[1:]))"
)
REHEARSAL_CONFIG = b'{"models":null,"public_data_enabled":false,"tick_interval_seconds":0.05}\n'


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _no_duplicates(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate service proof field")
        result[name] = value
    return result


def _write(path: Path, payload: bytes) -> None:
    parent = open_directory(path.parent)
    try:
        if os.fstat(parent).st_uid != os.geteuid() or os.fstat(parent).st_mode & 0o077:
            raise ValueError("service proof output requires owner-private storage")
        descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=parent)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.fsync(parent)
    finally:
        os.close(parent)


def _log(path: Path):
    parent = open_directory(path.parent)
    try:
        if os.fstat(parent).st_uid != os.geteuid() or os.fstat(parent).st_mode & 0o077:
            raise ValueError("service log requires owner-private storage")
        descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=parent)
        return os.fdopen(descriptor, "wb")
    finally:
        os.close(parent)


def _state(path: Path) -> dict:
    private_source(path, MAX_DATABASE_BYTES)
    with _read_database(path) as database:
        deadline = time.monotonic() + 5
        database.connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        if [row[0] for row in database.execute("PRAGMA integrity_check(1)")] != ["ok"]:
            raise ValueError("restart fixture integrity unavailable")
        if database.execute("SELECT 1 FROM portfolios WHERE mode!='paper' LIMIT 1").fetchone():
            raise ValueError("restart proof requires paper-only fixture")
        portfolio = database.execute("SELECT portfolio_id FROM portfolios").fetchall()
        intents = database.execute("SELECT state FROM order_intents").fetchall()
        pauses = database.execute("SELECT profile,originator FROM pause_states").fetchall()
        if len(portfolio) != 1 or len(intents) != 1 or len(pauses) != 1:
            raise ValueError("restart fixture inventory differs from fixed rehearsal")
        return {"portfolio_id": portfolio[0][0], "intent_state": intents[0][0],
                "attempts": database.execute("SELECT count(*) FROM order_attempts WHERE kind='submit'").fetchone()[0],
                "fills": database.execute("SELECT count(*) FROM fills").fetchone()[0],
                "broker_orders": database.execute("SELECT count(*) FROM broker_orders").fetchone()[0],
                "broker_fills": sum(len(json.loads(row[0]).get("emitted", [])) for row in database.execute(
                    "SELECT document_json FROM broker_orders")),
                "owner_pause": list(pauses[0]),
                "leases": database.execute("SELECT count(*) FROM process_leases").fetchone()[0],
                "real_receipts": database.execute(
                    "SELECT count(*) FROM usage_receipts WHERE synthetic=0").fetchone()[0],
                "reservations": database.execute("SELECT count(*) FROM budget_reservations").fetchone()[0]}


def _assert_recovered(state: dict, *, leases: bool) -> None:
    if (state["intent_state"] != "FILLED" or state["attempts"] != 1 or state["fills"] != 1
            or state["broker_orders"] != 1 or state["broker_fills"] != 1
            or state["owner_pause"] != ["MANAGE_ONLY", "owner"]
            or state["leases"] != (2 if leases else 0) or state["real_receipts"] or state["reservations"]):
        raise ValueError("paper restart did not preserve the fixed financial/authority inventory")


def _projection(path: Path, as_of: str) -> str:
    report = financial_report(path, clock=FrozenClock(parse_utc(as_of)))
    # Health includes process heartbeat/task diagnostics, which legitimately
    # change across a boot. The complete financial projections stay bound.
    return _sha(canonical_json({name: report[name] for name in
                              ("portfolio_id", "overview", "costs", "positions", "orders")}).encode())


def _seed_crash(database_path: Path, config_path: Path, package_sha256: str) -> int:
    """A real process owns a service, loses an acknowledgement and exits abruptly."""
    from trade_graph.adapters.brokers.paper import DropAckBroker
    from trade_graph.application.paper_service import PaperService
    from trade_graph.cli import main
    from trade_graph.contracts.models import Decision, InstrumentRules, Observation, Quantity
    from trade_graph.paper_runtime import assemble_paper_runtime, load_runtime_config

    if package_sha256 != protected_package_sha256():
        raise ValueError("child installed package differs from pinned rehearsal source")
    if main(["init", "--database", str(database_path), "--reporting-currency", "USD"]):
        raise ValueError("synthetic paper account creation failed")
    runtime = assemble_paper_runtime(database_path, config=load_runtime_config(config_path))
    clock = SystemClock()
    # The installed service/financial paths run unchanged; only this explicitly
    # synthetic fixture broker loses its acknowledgement and retains a match.
    runtime.execution.clock = runtime.ledger.clock = clock
    runtime.ledger.observe_fx(base="USD", quote="USD", rate=Decimal("1"), source="identity", kind="identity",
                              stale=False)
    inner = runtime.execution.broker
    inner.clock = clock
    drop = DropAckBroker(inner)
    runtime.execution.broker = drop
    service = PaperService(runtime.database, runtime.execution, clock=clock, schedule_intervals={})
    runtime.execution.register_instrument(InstrumentRules(
        venue="paper", symbol="BTC/USD", base_asset="BTC", quote_asset="USD", price_increment="0.1",
        quantity_increment="0.00000001", min_quantity="0.0001", min_notional="1", synthetic=True))

    def quote(identity: str) -> Observation:
        return Observation(observation_id=identity, venue="paper", symbol="BTC/USD", event_time_utc=clock.now(),
                           available_at_utc=clock.now(), bid="99", ask="100", bid_size="1", ask_size="1",
                           kind="quote", source="synthetic-local-service-rehearsal")

    async def scenario():
        await service.start()
        runtime.execution.save_observation(quote("restart-rehearsal-entry"))
        policy = runtime.execution.authority.active_policy()
        mandate = runtime.execution.authority.active_mandate(runtime.portfolio_id)
        runtime.execution.authorize(runtime.portfolio_id, Decision(
            record_id="restart-rehearsal-decision", created_at_utc=clock.now(), run_id="synthetic-restart",
            task_id="synthetic-restart", root_task_id="synthetic-restart", portfolio_id=runtime.portfolio_id,
            mode="paper", system_version_id="installed-r1-baseline", trace_id="synthetic-restart", action="enter",
            symbol="BTC/USD", quantity=Quantity(amount="0.01", asset="BTC"),
            rationale="synthetic lost-ack restart rehearsal", invalidation="fixture only", horizon_seconds=3600,
            strategy_id="synthetic-restart", snapshot_id="synthetic-restart",
            mandate_revision=str(mandate.revision), policy_revision=policy.revision_id))
        await runtime.execution.dispatch()
        await asyncio.sleep(0.01)
        if len(inner.match(quote("restart-rehearsal-lost-fill"))) != 1 or inner.submit_count != 1:
            raise ValueError("synthetic broker did not retain one real simulated effect")
        runtime.execution.set_pause(runtime.portfolio_id, "MANAGE_ONLY", "owner", "synthetic restart owner halt")
        runtime.database.execute("PRAGMA wal_checkpoint(FULL)")

    asyncio.run(scenario())
    # Deliberately bypass orderly stop: persisted leases and UNKNOWN intent
    # survive while the OS releases the process's real flock.
    os._exit(73)


def _entry(argv: list[str]) -> int:
    if len(argv) == 4 and argv[0] == "seed-crash":
        return _seed_crash(Path(argv[1]), Path(argv[2]), argv[3])
    if argv and argv[0] == "service":
        from trade_graph.cli import main

        return main(argv[1:])
    raise ValueError("unsupported fixed rehearsal process entry point")


@dataclass(frozen=True)
class RetainedServiceProof:
    path: Path
    sha256: str


@dataclass(frozen=True)
class VerifiedServiceProof:
    path: Path
    sha256: str
    document_json: str

    @property
    def document(self) -> dict:
        return json.loads(self.document_json)


class LocalPaperRestartCollector:
    """Trusted actual local subprocess proof; only a new synthetic directory is used."""

    def __init__(self, *, signing_key: bytes) -> None:
        if type(signing_key) is not bytes or not 32 <= len(signing_key) <= 64:
            raise ValueError("protected service proof key requires 32..64 bytes")
        self._key = signing_key

    @staticmethod
    def _command(*args: str) -> list[str]:
        package_parent = Path(__file__).resolve().parents[2]
        return [sys.executable, "-I", "-c", BOOTSTRAP, str(package_parent), *args]

    @staticmethod
    def _terminate(process: subprocess.Popen) -> int:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
        try:
            return process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
            raise ValueError("paper rehearsal graceful shutdown deadline exceeded") from None

    def _run_service(self, directory: Path, database: Path, config: Path, index: int) -> dict:
        command = self._command("service", "run", "--mode", "paper", "--database", str(database),
                                "--config", str(config))
        # Process logs are bounded by the fixed service and private regular
        # files. Raw logs/arguments never enter the authenticated report.
        stdout, stderr = directory / f"service-{index}.stdout", directory / f"service-{index}.stderr"
        with _log(stdout) as output, _log(stderr) as error:
            process = subprocess.Popen(command, cwd=directory, env=scrubbed_env(), close_fds=True,
                                       start_new_session=True, stdin=subprocess.DEVNULL, stdout=output, stderr=error)
            try:
                deadline = time.monotonic() + 10
                held = False
                while time.monotonic() < deadline:
                    if os.fstat(output.fileno()).st_size + os.fstat(error.fileno()).st_size > MAX_PROOF_BYTES:
                        raise ValueError("paper rehearsal process output exceeded byte bound")
                    if process.poll() is not None:
                        raise ValueError("paper rehearsal service exited before readiness")
                    descriptor = os.open(database, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
                    try:
                        try:
                            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            held = True
                    finally:
                        os.close(descriptor)
                    if not held:
                        time.sleep(0.02)
                        continue
                    try:
                        state = _state(database)
                    except ValueError as exc:
                        # A just-starting writer may create its WAL between the
                        # diagnostic's initial idle check and snapshot. Retry
                        # only these exact operational transitions, never a
                        # financial validation or integrity failure.
                        if str(exc) not in {
                            "database changed during the idle read; retry diagnostics",
                            "SQLite recovery is pending; recover the database before reporting",
                        }:
                            raise
                        time.sleep(0.02)
                        continue
                    if held and state["intent_state"] == "FILLED" and state["leases"]:
                        _assert_recovered(state, leases=True)
                        break
                    time.sleep(0.02)
                else:
                    raise ValueError("paper rehearsal startup/reconciliation deadline exceeded")
                code = self._terminate(process)
                if code != 0:
                    raise ValueError("paper rehearsal graceful stop failed")
                _assert_recovered(_state(database), leases=False)
                return {"exit_code": code, "exclusive_flock_observed": held,
                        "leases_drained": True, "financial_inventory_verified": True,
                        "command_sha256": _sha(canonical_json(command).encode()),
                        "stdout_sha256": _sha(private_source(stdout, MAX_PROOF_BYTES)),
                        "stderr_sha256": _sha(private_source(stderr, MAX_PROOF_BYTES))}
            finally:
                if process.poll() is None:
                    self._terminate(process)

    def capture(self, evidence_path: Path | str) -> RetainedServiceProof:
        path = Path(evidence_path)
        ensure_directory(path.parent)
        # Validate before any fixture process or writes; a previous report and
        # interrupted artifact directory are deliberately never reused.
        parent = open_directory(path.parent)
        try:
            info = os.fstat(parent)
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError("service proof requires owner-private output directory")
            if path.exists() or path.is_symlink():
                raise ValueError("service proof report already exists")
            name = path.name + ".artifacts"
            os.mkdir(name, 0o700, dir_fd=parent)
        finally:
            os.close(parent)
        directory = path.parent / name
        database, config = directory / "paper.sqlite", directory / "paper-config.json"
        _write(config, REHEARSAL_CONFIG)
        started, package = utc_iso(SystemClock().now()), protected_package_sha256()
        seed = self._command("seed-crash", str(database), str(config), package)
        result = subprocess.run(seed, cwd=directory, env=scrubbed_env(), stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
        if result.returncode != 73:
            raise ValueError("synthetic service crash preparation failed")
        before = _state(database)
        if (before["intent_state"] != "UNKNOWN" or before["attempts"] != 1 or before["fills"] != 0
                or before["broker_orders"] != 1 or before["broker_fills"] != 1 or before["leases"] != 2
                or before["owner_pause"] != ["MANAGE_ONLY", "owner"]):
            raise ValueError("synthetic crash did not retain the expected uncertain effect")
        private_backup(database, directory / "crashed.sqlite", maximum_bytes=MAX_DATABASE_BYTES, wall_seconds=5)
        first = self._run_service(directory, database, config, 1)
        as_of = utc_iso(SystemClock().now())
        first_projection = _projection(database, as_of)
        private_backup(database, directory / "first-recovered.sqlite", maximum_bytes=MAX_DATABASE_BYTES,
                       wall_seconds=5)
        second = self._run_service(directory, database, config, 2)
        after = _state(database)
        if _projection(database, as_of) != first_projection or protected_package_sha256() != package:
            raise ValueError("restart financial projection or protected package changed")
        private_backup(database, directory / "restarted.sqlite", maximum_bytes=MAX_DATABASE_BYTES, wall_seconds=5)
        files = {item.name: _sha(private_source(item, MAX_DATABASE_BYTES if item.suffix == ".sqlite"
                                              else MAX_PROOF_BYTES)) for item in directory.iterdir()
                 if item.name in {"paper.sqlite", "paper-config.json", "crashed.sqlite", "crashed.sqlite.sha256",
                                  "first-recovered.sqlite",
                                  "first-recovered.sqlite.sha256", "restarted.sqlite", "restarted.sqlite.sha256",
                                  "service-1.stdout", "service-1.stderr", "service-2.stdout", "service-2.stderr"}}
        document = {"schema_version": 1, "kind": "local_paper_restart_rehearsal", "started_at": started,
                    "ended_at": utc_iso(SystemClock().now()), "protected_package_sha256": package,
                    "producer_sha256": _sha(private_source(Path(__file__), 1_048_576, private=False, root_owned=True)),
                    "config_sha256": files["paper-config.json"], "source_kind": "fixed_package_process",
                    "scope": "new_synthetic_paper_fixture", "before": before, "after": after,
                    "runs": [first, second], "projection_as_of": as_of,
                    "financial_projection_sha256": first_projection, "artifacts": files,
                    "paid_calls": False, "private_venue_calls": False, "live_enabled": False,
                    "actual_systemd_verified": False, "owner_intended_host_verified": False,
                    "funded_acceptance_verified": False}
        document["authentication"] = {"scheme": "hmac-sha256", "signature": hmac.new(
            self._key, canonical_json(document).encode(), hashlib.sha256).hexdigest()}
        payload = (canonical_json(document) + "\n").encode()
        if len(payload) > MAX_PROOF_BYTES:
            raise ValueError("service proof output exceeds byte bound")
        _write(path, payload)
        return RetainedServiceProof(path, _sha(payload))

    def verify(self, evidence_path: Path | str, expected_sha256: str) -> VerifiedServiceProof:
        path = Path(evidence_path)
        payload = private_source(path, MAX_PROOF_BYTES)
        if type(expected_sha256) is not str or not hmac.compare_digest(_sha(payload), expected_sha256):
            raise ValueError("service proof byte digest mismatch")
        try:
            document = json.loads(payload, object_pairs_hook=_no_duplicates)
            authentication = document.pop("authentication")
            if authentication != {"scheme": "hmac-sha256", "signature": hmac.new(
                    self._key, canonical_json(document).encode(), hashlib.sha256).hexdigest()}:
                raise ValueError("service proof authentication mismatch")
            fields = {"schema_version", "kind", "started_at", "ended_at", "protected_package_sha256",
                      "producer_sha256", "config_sha256", "source_kind", "scope", "before", "after", "runs",
                      "projection_as_of", "financial_projection_sha256", "artifacts", "paid_calls",
                      "private_venue_calls", "live_enabled", "actual_systemd_verified", "owner_intended_host_verified",
                      "funded_acceptance_verified"}
            if (type(document["schema_version"]) is not int or document["schema_version"] != 1
                    or document["kind"] != "local_paper_restart_rehearsal"
                    or set(document) != fields
                    or document["scope"] != "new_synthetic_paper_fixture"
                    or document["source_kind"] != "fixed_package_process"
                    or any(document[name] is not False for name in
                           ("paid_calls", "private_venue_calls", "live_enabled", "actual_systemd_verified",
                            "owner_intended_host_verified", "funded_acceptance_verified"))
                    or document["protected_package_sha256"] != protected_package_sha256()
                    or document["producer_sha256"] != _sha(private_source(
                        Path(__file__), 1_048_576, private=False, root_owned=True))):
                raise ValueError("service proof scope, authority or producer changed")
            if not (parse_utc(document["started_at"]) <= parse_utc(document["projection_as_of"])
                    <= parse_utc(document["ended_at"]) <= SystemClock().now()):
                raise ValueError("service proof chronology is invalid")
            directory = path.parent / (path.name + ".artifacts")
            names = {"paper.sqlite", "paper-config.json", "crashed.sqlite", "crashed.sqlite.sha256",
                     "first-recovered.sqlite", "first-recovered.sqlite.sha256",
                     "restarted.sqlite", "restarted.sqlite.sha256", "service-1.stdout", "service-1.stderr",
                     "service-2.stdout", "service-2.stderr"}
            if set(document["artifacts"]) != names:
                raise ValueError("service proof artifact inventory mismatch")
            for name, digest in document["artifacts"].items():
                actual = private_source(directory / name, MAX_DATABASE_BYTES if name.endswith(".sqlite")
                                        else MAX_PROOF_BYTES)
                if _sha(actual) != digest:
                    raise ValueError("retained service proof artifact changed")
            if document["config_sha256"] != document["artifacts"]["paper-config.json"]:
                raise ValueError("service proof configuration binding changed")
            if private_source(directory / "paper-config.json", MAX_PROOF_BYTES) != REHEARSAL_CONFIG:
                raise ValueError("service proof configuration is outside offline rehearsal profile")
            if canonical_json(_state(directory / "crashed.sqlite")) != canonical_json(document["before"]):
                raise ValueError("service proof crash inventory changed")
            before = document["before"]
            if (before["intent_state"] != "UNKNOWN" or before["attempts"] != 1 or before["fills"] != 0
                    or before["broker_orders"] != 1 or before["broker_fills"] != 1 or before["leases"] != 2
                    or before["owner_pause"] != ["MANAGE_ONLY", "owner"] or before["real_receipts"]
                    or before["reservations"] or before["portfolio_id"] != document["after"]["portfolio_id"]):
                raise ValueError("service proof uncertain effect inventory mismatch")
            if len(document["runs"]) != 2:
                raise ValueError("service proof requires two exact process runs")
            command = self._command("service", "run", "--mode", "paper", "--database", str(directory / "paper.sqlite"),
                                    "--config", str(directory / "paper-config.json"))
            for index, run in enumerate(document["runs"], 1):
                expected = {"exit_code": 0, "exclusive_flock_observed": True, "leases_drained": True,
                            "financial_inventory_verified": True,
                            "command_sha256": _sha(canonical_json(command).encode()),
                            "stdout_sha256": document["artifacts"][f"service-{index}.stdout"],
                            "stderr_sha256": document["artifacts"][f"service-{index}.stderr"]}
                if canonical_json(run) != canonical_json(expected):
                    raise ValueError("service proof process run differs from fixed observed profile")
            if canonical_json(_state(directory / "paper.sqlite")) != canonical_json(document["after"]):
                raise ValueError("service proof final financial inventory changed")
            _assert_recovered(document["after"], leases=False)
            for name in ("crashed.sqlite", "first-recovered.sqlite", "restarted.sqlite"):
                if (name != "crashed.sqlite" and _projection(directory / name, document["projection_as_of"])
                        != document["financial_projection_sha256"]):
                    raise ValueError("service proof restart financial continuity mismatch")
                if private_source(directory / (name + ".sha256"), 128).strip().decode() != document["artifacts"][name]:
                    raise ValueError("service proof backup checksum changed")
            document["authentication"] = authentication
            return VerifiedServiceProof(path, expected_sha256, canonical_json(document))
        except (KeyError, TypeError, json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("invalid bounded service proof document") from exc


@dataclass(frozen=True)
class LocalRestartSource:
    collector: LocalPaperRestartCollector
    path: Path
    sha256: str

    def verify(self) -> VerifiedServiceProof:
        if type(self.collector) is not LocalPaperRestartCollector or not isinstance(self.path, Path):
            raise ValueError("exact protected local service proof source required")
        return self.collector.verify(self.path, self.sha256)


def main(argv: list[str] | None = None) -> int:
    """Installed private offline proof entry; no user-provided subprocess command."""
    from trade_graph.application.operations_preflight import _key

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["capture", "verify"])
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--sha256")
    args = parser.parse_args(argv)
    try:
        if args.report.absolute() == args.key.absolute():
            raise ValueError("service proof and observation key must use separate paths")
        collector = LocalPaperRestartCollector(signing_key=_key(args.key, create=args.command == "capture"))
        retained = collector.capture(args.report) if args.command == "capture" else None
        proof = collector.verify(args.report, retained.sha256 if retained is not None else args.sha256)
        print(json.dumps({"status": "recorded", "evidence_sha256": proof.sha256,
                          "scope": proof.document["scope"], "restart_reconciliation": "observed",
                          "paid_calls": False, "live_enabled": False, "funded_acceptance_verified": False},
                         sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "refused", "failure_type": type(exc).__name__}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
