"""Owner-pinned subscription PAPER network and nested-namespace launch preparation.

The profile contains no provider/exchange secret or spending authority. Proxy
routing is independently reviewed: provider and public-market listeners are
separate, while the application has only one dedicated internal network.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from pathlib import Path, PurePosixPath
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from trade_graph.kernel.runtime_manifest import canonical_json, document_sha256

Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ImageId = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
SECCOMP_FILENAME = "subscription-seccomp.json"


class PreparedSubscriptionNetworkProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: StrictInt = Field(ge=1, le=1)
    deployment_id: str = Field(min_length=1, max_length=128)
    image_id: ImageId
    manifest_sha256: Fingerprint
    protected_package_sha256: Fingerprint
    paper_config_sha256: Fingerprint
    native_cli_sha256: Fingerprint
    boundary_sha256: Fingerprint
    network_id: Fingerprint
    proxy_container_id: Fingerprint
    proxy_image_id: ImageId
    proxy_ipv4: str
    proxy_port: StrictInt = Field(ge=1024, le=65535)
    market_proxy_port: StrictInt = Field(ge=1024, le=65535)
    proxy_review_sha256: Fingerprint
    seccomp_path: str = Field(min_length=1, max_length=512)
    seccomp_sha256: Fingerprint

    @field_validator("proxy_ipv4")
    @classmethod
    def private_proxy(cls, value):
        address = ipaddress.IPv4Address(value)
        if (str(address) != value or not address.is_private or address.is_loopback
                or address.is_link_local or address.is_multicast or address.is_unspecified):
            raise ValueError("exact private subscription proxy IPv4 required")
        return value

    @field_validator("seccomp_path")
    @classmethod
    def exact_seccomp_path(cls, value):
        path = PurePosixPath(value)
        if (not path.is_absolute() or ".." in path.parts or path.name != SECCOMP_FILENAME
                or str(path) != value or any(ord(char) < 32 for char in value)):
            raise ValueError("absolute protected subscription seccomp file required")
        return value

    @model_validator(mode="after")
    def distinct_routes(self):
        if self.proxy_port == self.market_proxy_port:
            raise ValueError("subscription provider and public market listeners must be separate")
        return self

    @property
    def sha256(self):
        return document_sha256(self.model_dump(mode="json"))

    @property
    def proxy_url(self):
        return f"http://{self.proxy_ipv4}:{self.proxy_port}"

    @property
    def market_proxy_url(self):
        return f"http://{self.proxy_ipv4}:{self.market_proxy_port}"


def _unique(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate protected subscription field")
        document[key] = value
    return document


def _reject_constant(_value):
    raise ValueError("nonfinite protected JSON value")


def load_subscription_network_profile(directory: Path) -> PreparedSubscriptionNetworkProfile:
    from trade_graph.kernel.deployment_image import read_owner_file

    raw = read_owner_file(directory, "subscription-network-profile.json", 32768)
    document = json.loads(raw, object_pairs_hook=_unique, parse_constant=_reject_constant)
    return PreparedSubscriptionNetworkProfile.model_validate(document)


def load_subscription_seccomp(directory: Path, profile: PreparedSubscriptionNetworkProfile) -> dict:
    """Require the separately reviewed exact root-distributed deny-default filter.

    The owner review and actual image probe establish the individual syscall
    policy. This bounded reader also rejects unconfined/default-allow filters,
    wildcard syscall rules and notification/tracing delegation. No syscalls are
    added by the application. The daemon consumes the pinned host file directly.
    """
    from trade_graph.kernel.deployment_image import read_owner_file

    raw = read_owner_file(directory, SECCOMP_FILENAME, 262144)
    if hashlib.sha256(raw).hexdigest() != profile.seccomp_sha256:
        raise PermissionError("subscription seccomp file differs from its owner pin")
    try:
        document = json.loads(raw, object_pairs_hook=_unique, parse_constant=_reject_constant)
        rules = document.get("syscalls") if type(document) is dict else None
        if (document.get("defaultAction") != "SCMP_ACT_ERRNO" or type(rules) is not list
                or not 1 <= len(rules) <= 2048):
            raise ValueError("deny-default seccomp filter required")
        for rule in rules:
            if (type(rule) is not dict or type(rule.get("names")) is not list
                    or not 1 <= len(rule["names"]) <= 512
                    or any(type(name) is not str or not re.fullmatch(r"[a-z_][a-z0-9_]{0,63}", name)
                           for name in rule["names"])
                    or rule.get("action") not in {"SCMP_ACT_ALLOW", "SCMP_ACT_ERRNO", "SCMP_ACT_KILL",
                                                  "SCMP_ACT_KILL_THREAD", "SCMP_ACT_KILL_PROCESS"}):
                raise ValueError("bounded named seccomp rules required")
        return document
    except (ValueError, TypeError, AttributeError, RecursionError) as exc:
        raise PermissionError("reviewed subscription seccomp policy refused") from exc


def verify_subscription_security_options(profile: PreparedSubscriptionNetworkProfile, directory: Path,
                                         options) -> None:
    """Docker CLI expands a seccomp pathname to compact JSON before create.

    Compare its complete document against the exact hash-pinned owner file;
    neither a path string nor an optimistic daemon status establishes the filter.
    """
    expected = load_subscription_seccomp(directory, profile)
    if (type(options) is not list or len(options) != 2 or options[0] != "no-new-privileges"
            or type(options[1]) is not str or not options[1].startswith("seccomp=")
            or len(options[1]) > 262144):
        raise PermissionError("exact subscription seccomp security options required")
    try:
        actual = json.loads(options[1][len("seccomp="):], object_pairs_hook=_unique, parse_constant=_reject_constant)
        if canonical_json(actual) != canonical_json(expected):
            raise ValueError("changed seccomp policy")
    except (ValueError, TypeError, RecursionError) as exc:
        raise PermissionError("created subscription seccomp differs from owner pin") from exc


def verify_subscription_network(profile: PreparedSubscriptionNetworkProfile, network: dict, proxy: dict, *,
                                daemon_security_options: list[str]) -> None:
    if type(profile) is not PreparedSubscriptionNetworkProfile:
        raise PermissionError("exact reviewed subscription network profile required")
    from trade_graph.kernel.funded_profile import verify_funded_network

    # Reuse the strict dedicated internal bridge/pinned unprivileged proxy checks.
    # Provider/API credentials and funded permissions are never loaded here.
    verify_funded_network(profile, network, proxy, daemon_security_options=daemon_security_options)


def validate_subscription_configuration(profile: PreparedSubscriptionNetworkProfile, manifest, raw: bytes):
    from trade_graph.paper_runtime import PaperRuntimeConfig

    configured = PaperRuntimeConfig.model_validate_json(raw)
    if (profile.manifest_sha256 != manifest.sha256 or profile.deployment_id != manifest.deployment_id
            or profile.protected_package_sha256 != manifest.protected_package_sha256
            or hashlib.sha256(raw).hexdigest() != profile.paper_config_sha256
            or configured.models is not None or configured.price_cards):
        raise PermissionError("protected subscription configuration/profile mismatch")
    return configured
