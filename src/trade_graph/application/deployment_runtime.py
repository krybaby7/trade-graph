"""Bind the installed paper service to an independently pinned owner deployment.

Preparation runs after the service owns its financial database and reconciles.
The mutable graph never receives the owner files, key or database handle.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from trade_graph.application.protected_departments import assemble_protected_handlers
from trade_graph.application.protected_runtime import ProtectedPaperRuntime
from trade_graph.domain.errors import AuthorityDenied, TradeGraphError


def _owner_bundle(directory: Path):
    from trade_graph.kernel.deployment_image import load_owner_runtime_manifest, read_owner_file

    manifest = load_owner_runtime_manifest(directory / "runtime-manifest.json")
    source = read_owner_file(directory, "graph.py", manifest.maximum_source_bytes).decode("utf-8")
    key = read_owner_file(directory, "capability.key", 4096)
    if len(key) < 32:
        raise AuthorityDenied("protected capability key requires at least 32 bytes")
    digest = hashlib.sha256(source.encode()).hexdigest()
    if (manifest.operations != ("submit_decision", "invoke_model", "apply_role_result")
            or digest not in manifest.approved_source_sha256):
        raise AuthorityDenied("deployment lacks an approved six-role graph")
    return manifest, source, key


class ProtectedDeploymentBinding:
    def __init__(self, runtime, directory: Path, *, api_keys=None, transport=None) -> None:
        self.runtime, self.directory = runtime, directory
        self.manifest, self.source, self._key = _owner_bundle(directory)
        self.manifest.assert_current()
        if self.manifest.deployment_id != runtime.deployment_id:
            raise AuthorityDenied("owner deployment does not match the runtime budget scope")
        self.instance_id = "deployed-" + self.manifest.sha256[:32]
        source_id = hashlib.sha256(self.source.encode()).hexdigest()[:16]
        self.release_id = "owner-" + self.manifest.sha256[:16] + "-" + source_id
        self.api_keys, self.transport = api_keys, transport
        self.funded_profile = self._funded_bundle()
        if self.funded_profile is not None:
            from trade_graph.adapters.models.transport import HttpxProviderHttp
            from trade_graph.kernel.funded_profile import load_funded_credentials

            if api_keys or transport is not None:
                raise AuthorityDenied("funded-paper deployment rejects injected credentials or transport")
            self.api_keys = load_funded_credentials(self.directory, self.funded_profile)
            self.transport = HttpxProviderHttp(proxy=self.funded_profile.proxy_url, trust_env=False,
                                               allowed_urls=self.funded_profile.endpoints)
        self.protected = None

    def _funded_bundle(self):
        from trade_graph.kernel.deployment_image import read_owner_file
        from trade_graph.kernel.funded_profile import load_funded_profile
        from trade_graph.paper_runtime import PaperRuntimeConfig

        try:
            (self.directory / "funded-paper-profile.json").lstat()
        except FileNotFoundError:
            return None
        profile = load_funded_profile(self.directory)
        raw = read_owner_file(self.directory, "paper-config.json", 262144)
        configured = PaperRuntimeConfig.model_validate_json(raw)
        if (profile.manifest_sha256 != self.manifest.sha256
                or profile.deployment_id != self.runtime.deployment_id
                or hashlib.sha256(raw).hexdigest() != profile.paper_config_sha256
                or configured != self.runtime.config or configured.public_data_enabled):
            raise AuthorityDenied("funded-paper runtime/configuration differs from owner profile")
        return profile

    def _unchanged(self) -> bool:
        manifest, source, key = _owner_bundle(self.directory)
        if (manifest != self.manifest or source != self.source
                or hashlib.sha256(key).digest() != hashlib.sha256(self._key).digest()
                or self._funded_bundle() != self.funded_profile):
            return False
        if self.funded_profile is not None:
            from trade_graph.kernel.funded_profile import load_funded_credentials

            if load_funded_credentials(self.directory, self.funded_profile) != self.api_keys:
                return False
        return True

    def prepare(self) -> dict:
        """Admit once; restarting a failed release cannot lift management-only recovery."""
        if not self._unchanged():
            raise AuthorityDenied("protected owner deployment changed during service startup")
        runtime = self.runtime
        existing = runtime.database.execute(
            "SELECT * FROM protected_runtime_instances WHERE instance_id=?", (self.instance_id,),
        ).fetchone()
        self.protected = ProtectedPaperRuntime(
            database=runtime.database, clock=runtime.clock, execution=runtime.execution,
            manifest=self.manifest, capability_key=self._key, instance_id=self.instance_id,
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
        if runtime.model_config is None:
            return {}
        runtime.model_handlers = assemble_protected_handlers(
            runtime.office, runtime.secretary, runtime.engineer, runtime.artifact_runtime, runtime.model_config,
            protected_runtime=self.protected, workspace_root=runtime.database.path.parent / "engineering",
            api_keys=self.api_keys, transport=self.transport,
        )
        runtime.handlers = runtime.model_handlers.handlers
        return runtime.handlers

    def ready(self) -> bool:
        try:
            self.manifest.assert_current()
            if not self.protected or not self._unchanged():
                return False
            status = self.protected.controller.status()
            if status["status"] != "RUNNING":
                return False
            self.protected.financial._release(status)
            portfolios = self.runtime.database.execute("SELECT portfolio_id FROM portfolios WHERE mode='paper'")
            if not all(self.protected.financial.history.ready(row["portfolio_id"]) for row in portfolios):
                return False
            return True
        except (OSError, ValueError, PermissionError, TradeGraphError):
            return False
