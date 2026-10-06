"""Protected live composition using the same confined, one-use graph RPC.

Native effects remain in the trusted parent and always traverse Execution's
durable owner/lifecycle/envelope checks. Mutable departments cannot choose mode,
venue, account, credential, policy or financial state.
"""

from __future__ import annotations

import asyncio

from trade_graph.application.deployment_runtime import ProtectedDeploymentBinding, _owner_bundle
from trade_graph.application.protected_departments import assemble_protected_handlers
from trade_graph.application.protected_runtime import ProtectedPaperRuntime
from trade_graph.domain.errors import AuthorityDenied, TradeGraphError
from trade_graph.kernel.financial_service import ProtectedFinancialService
from trade_graph.kernel.live_commission import PinnedLiveCommission
from trade_graph.kernel.runtime_controller import ProtectedRuntimeController
from trade_graph.live_gate import evaluate_live_readiness
from trade_graph.live_pilot import ProtectedPilotLifecycle


class ProtectedLiveFinancialService(ProtectedFinancialService):
    _execution_mode = "live"

    def __init__(self, database, clock, execution, *, manifest, capability_key, commission):
        pilot = execution._pilot_lifecycle
        if (type(commission) is not PinnedLiveCommission or type(pilot) is not ProtectedPilotLifecycle
                or pilot.upstream is None or pilot.upstream.commission != commission
                or execution.venue != "kraken" or pilot.scope.deployment_id != manifest.deployment_id):
            raise AuthorityDenied("exact commissioned protected live financial binding required")
        commission.verify(pilot.scope, pilot.source.load(), clock, database=database,
                          management_only=commission.admitted(database, pilot.scope, execution._pilot_authorization_id))
        self.commission = commission
        super().__init__(database, clock, execution, manifest=manifest, capability_key=capability_key)

    def issue(self, instance_id, portfolio_id, symbol):
        pilot = self.execution._pilot_lifecycle
        if not evaluate_live_readiness(self.database, self.clock, scope=pilot.scope, source=pilot.source,
                                       upstream=pilot.upstream)["ready"]:
            raise AuthorityDenied("current live authority does not admit new graph work")
        return super().issue(instance_id, portfolio_id, symbol)


class ProtectedLiveRuntime(ProtectedPaperRuntime):
    def __init__(self, *, database, clock, execution, manifest, capability_key, instance_id, commission):
        self.financial = ProtectedLiveFinancialService(database, clock, execution, manifest=manifest,
                                                       capability_key=capability_key, commission=commission)
        self.controller = ProtectedRuntimeController(self.financial, instance_id=instance_id)
        self._execution_lock = asyncio.Lock()
        self._owns_database = False


class ProtectedLiveDeploymentBinding(ProtectedDeploymentBinding):
    """No direct model/API-key construction outside the protected department path."""

    def __init__(self, runtime, directory, *, commission):
        self.runtime, self.directory = runtime, directory
        self.manifest, self.source, self._key = _owner_bundle(directory)
        self.manifest.assert_current()
        if self.manifest.deployment_id != runtime.deployment_id or type(commission) is not PinnedLiveCommission:
            raise AuthorityDenied("live runtime differs from owner manifest and commission")
        self.commission = commission
        self.instance_id = "live-deployed-" + self.manifest.sha256[:32]
        self.release_id = "live-owner-" + self.manifest.sha256[:32]
        self.funded_profile = None
        self.protected = None
        self.api_keys, self.transport = {}, None
        self._initialise_subscription()

    def _funded_bundle(self):
        return None

    def prepare(self):
        if not self._unchanged():
            raise AuthorityDenied("protected live owner deployment changed during startup")
        runtime = self.runtime
        existing = runtime.database.execute(
            "SELECT * FROM protected_runtime_instances WHERE instance_id=?", (self.instance_id,),
        ).fetchone()
        self.protected = ProtectedLiveRuntime(
            database=runtime.database, clock=runtime.clock, execution=runtime.execution,
            manifest=self.manifest, capability_key=self._key, instance_id=self.instance_id,
            commission=self.commission,
        )
        controller = self.protected.controller
        controller.admit_release(release_id=self.release_id, source_text=self.source)
        if existing is None:
            controller.activate_release(self.release_id)
        elif controller.status()["status"] == "RUNNING":
            status = controller.status()
            try:
                self.protected.financial._release(status)
            except (TradeGraphError, ValueError):
                controller._recover_mutable(status["active_release_id"], status["generation"])
        if not self._subscription_current():
            runtime.handlers = {}
            return {}
        if self.subscription_declared:
            return self._subscription_handlers()
        if runtime.model_config is None:
            return {}
        runtime.model_handlers = assemble_protected_handlers(
            runtime.office, runtime.secretary, runtime.engineer, runtime.artifact_runtime, runtime.model_config,
            protected_runtime=self.protected, workspace_root=runtime.database.path.parent / "engineering",
            api_keys={}, transport=None,
        )
        runtime.handlers = runtime.model_handlers.handlers
        return runtime.handlers

    def ready(self):
        try:
            self.manifest.assert_current()
            if not self.protected or not self._unchanged() or not self._subscription_ready():
                return False
            status = self.protected.controller.status()
            if status["status"] != "RUNNING":
                return False
            self.protected.financial._release(status)
            pilot = self.runtime.execution._pilot_lifecycle
            readiness = evaluate_live_readiness(self.runtime.database, self.runtime.clock, scope=pilot.scope,
                                               source=pilot.source, upstream=pilot.upstream)
            return readiness["ready"] is True and self.protected.financial.history.ready(self.runtime.portfolio_id)
        except (OSError, ValueError, PermissionError, TradeGraphError):
            return False
