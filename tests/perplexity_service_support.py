"""Helpers for Perplexity inside the installed service (W8-A): the owner's protected folder, simulated.

The real folder is root-owned and read through ``kernel.deployment_image.read_owner_file``; tests replace that reader
with a dictionary of synthetic files: a network profile with the reviewed ``paid_research`` route, a made-up key and
the repository's dated price cards. ``protected_runtime`` prepares the real protected runtime over a synthetic account
(W10-A: the route's reservations are bound to its protected budget origins). No network, real key or paid call.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

from tests.security.test_subscription_network import profile
from trade_graph.kernel import deployment_image

ROOT = Path(__file__).resolve().parents[1]
CARDS = (ROOT / "config" / "perplexity-price-cards.json").read_bytes()
KEY = "synthetic-test-key-not-a-real-key-0240"
ROUTE = {"paid_research": {"port": 8082, "hosts": ["api.perplexity.ai"]}}


def network_profile(*, routes: Mapping | None = ROUTE):
    return profile(reviewed_routes=dict(routes or {}))


def owner_files(*, routes: Mapping | None = ROUTE, key: str | None = KEY, cards: bytes | None = CARDS,
                **extra: bytes) -> dict[str, bytes]:
    files = {"subscription-network-profile.json": network_profile(routes=routes).model_dump_json().encode()}
    if key is not None:
        files["perplexity.key"] = key.encode() + b"\n"
    if cards is not None:
        files["perplexity-price-cards.json"] = cards
    files.update(extra)
    return files


def protected_runtime(account):
    """The protected runtime the installed service prepares at start, over a synthetic account: its financial service
    and a running controller (a confined graph release). One per account; the capability key is a labelled synthetic
    placeholder."""
    existing = getattr(account, "protected_runtime", None)
    if existing is not None:
        return existing
    from tests.integration.test_protected_department_graph import GRAPH
    from trade_graph.application.protected_runtime import ProtectedPaperRuntime
    from trade_graph.kernel.runtime_manifest import ProtectedRuntimeManifest, protected_package_sha256

    manifest = ProtectedRuntimeManifest(
        schema_version=1, protected_package_sha256=protected_package_sha256(), deployment_id="deployment",
        approved_source_sha256=(hashlib.sha256(GRAPH.encode()).hexdigest(),),
        operations=("submit_decision", "invoke_model", "apply_role_result"))
    runtime = ProtectedPaperRuntime(database=account.database, clock=account.clock, execution=account.execution,
                                    manifest=manifest, capability_key=b"synthetic-perplexity-route-key-0265",
                                    instance_id="synthetic-perplexity-route")
    runtime.controller.admit_release(release_id="synthetic-graph-v1", source_text=GRAPH)
    runtime.controller.activate_release("synthetic-graph-v1")
    account.protected_runtime = runtime
    return runtime


def trust_owner_files(monkeypatch, files: Mapping[str, bytes]) -> list[str]:
    """Serve ``files`` as the owner's protected folder; returns the names read, in order."""
    reads: list[str] = []

    def read(_directory, name, maximum):
        reads.append(name)
        if name not in files:
            raise FileNotFoundError(name)
        if len(files[name]) > maximum:
            raise ValueError("owner file exceeds retained size bound")
        return files[name]

    monkeypatch.setattr(deployment_image, "read_owner_file", read)
    return reads
