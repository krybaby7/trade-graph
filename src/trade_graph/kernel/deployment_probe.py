"""Synthetic installed-image attacks and real durable paper recovery.

This bounded probe uses no provider, exchange, real budget or owner class grant.
It is run by the independent host verifier against the actual sealed container.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from trade_graph.adapters.brokers.paper import DropAckBroker, PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.authority import seed_paper_authority
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.application.protected_runtime import ProtectedPaperRuntime
from trade_graph.contracts.models import InstrumentRules, Observation
from trade_graph.domain.clock import FrozenClock
from trade_graph.kernel.deployment_image import OWNER_MOUNT, STATE_MOUNT, read_owner_file
from trade_graph.kernel.runtime_manifest import ProtectedRuntimeManifest, canonical_json, document_sha256

PROBE_ENTER = '''
def propose(context):
    return {"action":"enter", "quantity":"0.01", "rationale":"Synthetic image probe",
            "invalidation":"New quote", "horizon_seconds":3600, "strategy_id":"slow-trend"}
'''
PROBE_ATTACK = '''
import os, socket, ctypes
def propose(context):
    assert "TRADE_GRAPH_SYNTHETIC_PARENT_SECRET" not in os.environ
    blocked = []
    def check(action):
        try:
            action()
            blocked.append(False)
        except OSError as error:
            blocked.append(error.errno in (1, 9))
    check(lambda: os.open('/run/trade-graph-owner/capability.key', os.O_RDONLY))
    check(lambda: os.open('/run/trade-graph-owner/runtime-manifest.json', os.O_WRONLY))
    check(lambda: os.open('/var/lib/trade-graph/image-probe.sqlite', os.O_RDWR))
    check(lambda: os.open('/opt/trade-graph/image-seal.json', os.O_WRONLY))
    check(lambda: os.open('/tmp/candidate-escape', os.O_CREAT | os.O_WRONLY, 0o600))
    check(lambda: os.open('/proc/' + str(os.getppid()) + '/environ', os.O_RDONLY))
    check(lambda: os.open('/proc/' + str(os.getppid()) + '/mem', os.O_RDONLY))
    check(lambda: os.open('/var/run/docker.sock', os.O_RDWR))
    check(lambda: os.read(9, 1))
    check(lambda: os.write(9, b'overwrite'))
    for family in (socket.AF_INET, socket.AF_INET6, socket.AF_UNIX):
        check(lambda: socket.socket(family, socket.SOCK_STREAM))
    check(lambda: os.fork())
    check(lambda: os.execv('/bin/true', ['true']))
    check(lambda: os.kill(os.getppid(), 9))
    libc = ctypes.CDLL(None, use_errno=True)
    for number in (101, 165, 157, 160, 250, 272, 302, 308, 310, 311, 317, 321, 425, 434):
        ctypes.set_errno(0)
        blocked.append(libc.syscall(number, 0, 0, 0, 0, 0, 0) == -1 and ctypes.get_errno() == 1)
    assert len(blocked) == 30 and all(blocked), 'actual OS boundary failed'
    try:
        data = bytearray(256 * 1024 * 1024)
        raise AssertionError('memory bound failed')
    except MemoryError:
        pass
    return {"action":"hold", "rationale":"30 actual OS attacks blocked; memory bounded",
            "invalidation":"New quote", "horizon_seconds":3600, "strategy_id":"slow-trend"}
'''
PROBE_TIMEOUT = 'def propose(context):\n    while True:\n        pass\n'


def probe_source_sha256() -> tuple[str, ...]:
    return tuple(hashlib.sha256(source.encode()).hexdigest() for source in
                 (PROBE_ENTER, PROBE_ATTACK, PROBE_TIMEOUT))


def _history(database: Database) -> dict:
    return {name: [dict(row) for row in database.execute(f"SELECT * FROM {name} ORDER BY rowid")]
            for name in ("ledger_events", "fills", "order_attempts", "usage_receipts")}


def run_probe(manifest: ProtectedRuntimeManifest, *, state_directory: Path = Path(STATE_MOUNT),
              capability_key: bytes | None = None) -> dict:
    """Repeat calls restart the same durable paper DB and never issue another order."""
    if not set(probe_source_sha256()) <= set(manifest.approved_source_sha256):
        raise PermissionError("owner manifest has not admitted the exact synthetic probe sources")
    key = capability_key or read_owner_file(Path(OWNER_MOUNT), "capability.key", 128)
    loader_attacks_blocked = 0
    if capability_key is None:
        for name in ("unsafe-public.json", "unsafe-writable.json", "unsafe-owned.json", "unsafe-link.json",
                     "unsafe-fifo.json"):
            try:
                read_owner_file(Path(OWNER_MOUNT), name, 32768)
            except (OSError, PermissionError):
                loader_attacks_blocked += 1
            else:
                raise AssertionError("owner file loader admitted malicious distribution metadata")
    os.environ["TRADE_GRAPH_SYNTHETIC_PARENT_SECRET"] = "synthetic-image-parent-only-secret"
    clock = FrozenClock(datetime(2026, 10, 4, tzinfo=UTC))
    path, report_path = state_directory / "image-probe.sqlite", state_directory / "image-probe.json"
    restart = path.exists()
    db = Database(path)
    try:
        ledger = Ledger(db, clock)
        broker = DropAckBroker(PaperBroker(db, clock))
        execution = Execution(db, ledger, clock, broker)
        runtime = ProtectedPaperRuntime(database=db, clock=clock, execution=execution, manifest=manifest,
                                       capability_key=key, instance_id="synthetic-image-probe")
        if restart:
            previous = json.loads(report_path.read_bytes())
            portfolio = previous["portfolio_id"]
            result = asyncio.run(runtime.recover(portfolio))
            history = _history(db)
            if (document_sha256(history) != previous["financial_history_sha256"]
                    or len(history["order_attempts"]) != 1 or len(history["fills"]) != 1
                    or runtime.controller.status()["active_release_id"] != "synthetic-os-attack"):
                raise AssertionError("installed-image restart changed financial history or replayed execution")
            return {**previous, "phase": "restart_verified", "restart_status": result["status"],
                    "restart_dispatched": result["dispatched"]}
        portfolio = ledger.create_portfolio(reporting_currency="USD")
        ledger.deposit(portfolio, "USD", Decimal("10000"), "synthetic-virtual-only")
        seed_paper_authority(db, clock, portfolio)
        execution.register_instrument(InstrumentRules(venue="paper", symbol="BTC/USD", base_asset="BTC",
            quote_asset="USD", price_increment="0.1", quantity_increment="0.00000001", min_quantity="0.0001",
            min_notional="1", synthetic=True))
        quote = Observation(observation_id="image-probe-quote", venue="paper", symbol="BTC/USD",
            event_time_utc=clock.now(), available_at_utc=clock.now(), bid="99", ask="100", bid_size="1",
            ask_size="1", kind="quote", source="synthetic-installed-image-probe")
        execution.save_observation(quote)

        def activate(source: str, release: str):
            runtime.controller.admit_release(release_id=release, source_text=source)
            runtime.controller.activate_release(release)

        activate(PROBE_ENTER, "synthetic-entry")
        entered = asyncio.run(runtime.cycle(portfolio, "BTC/USD"))
        if entered["status"] != "APPLIED" or execution.intent_state(entered["response"]["intent_id"]) != "UNKNOWN":
            raise AssertionError("actual durable synthetic lost-ack paper order failed")
        asyncio.run(runtime.recover(portfolio))
        if db.execute("SELECT COUNT(*) FROM order_attempts").fetchone()[0] != 1:
            raise AssertionError("uncertain paper order was redispatched")
        asyncio.run(runtime.ingest_observation(quote.model_copy(update={"observation_id": "image-probe-fill"})))
        asyncio.run(runtime.recover(portfolio))
        if execution.intent_state(entered["response"]["intent_id"]) != "FILLED":
            raise AssertionError("actual installed paper fill did not reconcile")
        activate(PROBE_ATTACK, "synthetic-os-attack")
        attacked = asyncio.run(runtime.cycle(portfolio, "BTC/USD"))
        if attacked["status"] != "APPLIED":
            raise AssertionError("actual installed-image OS attacks were not all blocked")
        original_reconcile, management_calls = execution.reconcile, []

        async def observe_management():
            management_calls.append(True)
            await original_reconcile()

        execution.reconcile = observe_management
        before = document_sha256(_history(db))
        activate(PROBE_TIMEOUT, "synthetic-timeout")
        timed = asyncio.run(runtime.cycle(portfolio, "BTC/USD"))
        if (timed["status"] != "MUTABLE_REJECTED" or timed["recovery"] != "ROLLED_BACK_TO_VALIDATED_RELEASE"
                or len(management_calls) < 3 or document_sha256(_history(db)) != before
                or runtime.controller.status()["active_release_id"] != "synthetic-os-attack"):
            raise AssertionError("independent installed-image management/recovery did not preserve finance")
        report = {"schema_version": 1, "phase": "attacks_and_recovery_verified", "portfolio_id": portfolio,
                  "manifest_sha256": manifest.sha256, "os_attacks_blocked": 30, "child_memory_bounded": True,
                  "owner_loader_attacks_blocked": loader_attacks_blocked,
                  "management_calls_during_failure": len(management_calls),
                  "mutable_recovery": timed["recovery"], "financial_history_sha256": before,
                  "fills": 1, "order_attempts": 1, "unknown_ack_reconciled_without_replay": True,
                  "paid_transport_calls": 0, "live_authorization": False, "paid_authorization": False,
                  "deployed_engineer_authorization": False, "synthetic": True, "intended_host_verified": False}
        report_path.write_text(canonical_json(report) + "\n")
        return report
    finally:
        db.close()
        os.environ.pop("TRADE_GRAPH_SYNTHETIC_PARENT_SECRET", None)
