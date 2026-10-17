"""Helpers for Perplexity inside the installed service (W8-A): the owner's protected folder, simulated.

The real folder is root-owned and read through ``kernel.deployment_image.read_owner_file``; tests replace that reader
with a dictionary of synthetic files: a network profile with the reviewed ``paid_research`` route, a made-up key and
the repository's dated price cards. No network, real key or paid call.
"""

from __future__ import annotations

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
