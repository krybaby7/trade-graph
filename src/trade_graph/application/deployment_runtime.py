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
        self.protected = None

    def _unchanged(self) -> bool:
        manifest, source, key = _owner_bundle(self.directory)
        return (manifest == self.manifest and source == self.source
                and hashlib.sha256(key).digest() == hashlib.sha256(self._key).digest())

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
            return True
        except (OSError, ValueError, PermissionError, TradeGraphError):
            return False
