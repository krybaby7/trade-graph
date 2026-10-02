"""Opt-in protected production-paper composition; legacy paper mode stays default.

Only this trusted parent receives the financial database, broker, owner manifest
and private capability key. Mutable strategy source lives in confined fresh exec
children. This slice supports decision proposals, not broader Engineer deployment.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import stat
import threading
from contextlib import contextmanager

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.execution import Execution
from trade_graph.contracts.models import Observation
from trade_graph.domain.clock import Clock
from trade_graph.domain.errors import StaleState
from trade_graph.kernel.financial_service import ProtectedFinancialService
from trade_graph.kernel.runtime_controller import ProtectedRuntimeController
from trade_graph.kernel.runtime_manifest import ProtectedRuntimeManifest


class ProtectedPaperRuntime:
    def __init__(self, *, database: Database, clock: Clock, execution: Execution,
                 manifest: ProtectedRuntimeManifest, capability_key: bytes, instance_id: str) -> None:
        self.financial = ProtectedFinancialService(database, clock, execution,
                                                  manifest=manifest, capability_key=capability_key)
        self.controller = ProtectedRuntimeController(self.financial, instance_id=instance_id)
        self._execution_lock = asyncio.Lock()
        self._owns_database = False

    @contextmanager
    def _exclusive_controller(self):
        path = self.financial.database.path
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        acquired = False
        try:
            info = os.fstat(descriptor)
            current = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
                raise StaleState("protected database file identity changed")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise StaleState("another service or protected controller owns the financial database") from exc
            acquired = True
            self._owns_database = True
            yield
        finally:
            if acquired:
                self._owns_database = False
            os.close(descriptor)

    async def _manage(self, portfolio_id: str) -> int:
        async with self._execution_lock:
            await self.financial.reconcile()
            await self.financial.execution.advance_pause(portfolio_id)
            return await self.financial.dispatch_outbox()

    async def ingest_observation(self, observation: Observation) -> list[str]:
        """Trusted feed matching shares the same exclusion as submit/reconcile."""
        async with self._execution_lock:
            if self._owns_database:
                return self.financial.execution.on_observation(observation)
            with self._exclusive_controller():
                return self.financial.execution.on_observation(observation)

    async def recover(self, portfolio_id: str) -> dict:
        with self._exclusive_controller():
            async with self._execution_lock:
                result = await self.controller.recover()
            result["dispatched"] = await self._manage(portfolio_id)
            return result

    async def cycle(self, portfolio_id: str, symbol: str) -> dict:
        with self._exclusive_controller():
            return await self._cycle(portfolio_id, symbol)

    async def _cycle(self, portfolio_id: str, symbol: str) -> dict:
        """Management runs independently, before mutable work and before effects."""
        dispatched = await self._manage(portfolio_id)
        status = self.controller.status()
        if status["status"] != "RUNNING" or self.financial.execution.profile(portfolio_id) != "RUNNING":
            return {"status": "MANAGE_ONLY", "dispatched": dispatched,
                    "live_authorization": False, "paid_authorization": False}
        cancelled = threading.Event()

        def run_mutable():
            with self.financial.database.thread_connection():
                return self.controller.run_active(portfolio_id, symbol, cancelled=cancelled)

        worker = asyncio.create_task(asyncio.to_thread(run_mutable))
        try:
            while not worker.done():
                done, _ = await asyncio.wait({worker}, timeout=0.25)
                if done:
                    break
                dispatched += await self._manage(portfolio_id)
            result = await worker
        except BaseException:
            # The synchronous subprocess supervisor still kills/reaps by its
            # bounded deadline. Drain it before propagating cancellation/errors.
            cancelled.set()
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    cancelled.set()
                except BaseException:
                    break
            raise
        if result["status"] == "APPLIED":
            dispatched += await self._manage(portfolio_id)
        result["dispatched"] = dispatched
        return result
