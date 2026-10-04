"""Explicit owner-pinned funded PAPER deployment preparation.

This profile grants no budget or paid permission. It binds the credentialed
parent to a separately reviewed dedicated internal network and proxy. Mutable
children continue to have neither filesystem nor network syscalls.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from trade_graph.kernel.runtime_manifest import document_sha256

PROVIDER_ENDPOINTS = {
    "openai": "https://api.openai.com/v1/responses",
    "anthropic": "https://api.anthropic.com/v1/messages",
}
_SHA = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class ProviderCredentialPin:
    provider: str
    filename: str
    sha256: str

    def __post_init__(self):
        if (self.provider not in PROVIDER_ENDPOINTS or self.filename != self.provider + ".key"
                or type(self.sha256) is not str or not _SHA.fullmatch(self.sha256)):
            raise ValueError("exact supported provider credential pin required")


@dataclass(frozen=True)
class PreparedFundedPaperProfile:
    schema_version: int
    profile_id: str
    deployment_id: str
    image_id: str
    manifest_sha256: str
    paper_config_sha256: str
    network_id: str
    proxy_container_id: str
    proxy_image_id: str
    proxy_ipv4: str
    proxy_port: int
    proxy_review_sha256: str
    credentials: tuple[ProviderCredentialPin, ...]

    def __post_init__(self):
        try:
            address = ipaddress.IPv4Address(self.proxy_ipv4)
        except (ValueError, TypeError):
            raise ValueError("fixed private IPv4 proxy required") from None
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.proxy_ipv4) is not str or str(address) != self.proxy_ipv4
                or type(self.profile_id) is not str or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", self.profile_id)
                or type(self.deployment_id) is not str or not 1 <= len(self.deployment_id) <= 128
                or not address.is_private or address.is_loopback or address.is_link_local
                or address.is_multicast or address.is_unspecified
                or type(self.proxy_port) is not int or not 1024 <= self.proxy_port <= 65535
                or type(self.credentials) is not tuple or not 1 <= len(self.credentials) <= 2
                or any(type(pin) is not ProviderCredentialPin for pin in self.credentials)
                or len({pin.provider for pin in self.credentials}) != len(self.credentials)):
            raise ValueError("bounded separately pinned funded-paper profile required")
        for name in ("manifest_sha256", "paper_config_sha256", "network_id", "proxy_container_id",
                     "proxy_review_sha256"):
            if type(getattr(self, name)) is not str or not _SHA.fullmatch(getattr(self, name)):
                raise ValueError("exact owner-funded profile SHA256 required")
        for name in ("image_id", "proxy_image_id"):
            value = getattr(self, name)
            if type(value) is not str or not value.startswith("sha256:") or not _SHA.fullmatch(value[7:]):
                raise ValueError("immutable funded-paper image identities required")

    @property
    def sha256(self):
        return document_sha256(asdict(self))

    @property
    def proxy_url(self):
        return f"http://{self.proxy_ipv4}:{self.proxy_port}"

    @property
    def endpoints(self):
        return tuple(PROVIDER_ENDPOINTS[pin.provider] for pin in self.credentials)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate funded-paper profile field")
        result[key] = value
    return result


def load_funded_profile(directory: Path) -> PreparedFundedPaperProfile:
    from trade_graph.kernel.deployment_image import read_owner_file

    try:
        value = json.loads(read_owner_file(directory, "funded-paper-profile.json", 32768), object_pairs_hook=_unique)
        if type(value) is not dict or set(value) != {field.name for field in fields(PreparedFundedPaperProfile)}:
            raise ValueError
        if type(value["credentials"]) is not list:
            raise ValueError
        value["credentials"] = tuple(ProviderCredentialPin(**pin) for pin in value["credentials"])
        return PreparedFundedPaperProfile(**value)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError("invalid protected funded-paper profile") from None


def load_funded_credentials(directory: Path, profile: PreparedFundedPaperProfile) -> dict[str, str]:
    from trade_graph.kernel.deployment_image import read_owner_file

    result = {}
    for pin in profile.credentials:
        raw = read_owner_file(directory, pin.filename, 4096)
        if hashlib.sha256(raw).hexdigest() != pin.sha256:
            raise PermissionError("protected funded-paper credential differs from owner pin")
        try:
            key = raw.decode("ascii")
        except UnicodeError:
            raise PermissionError("protected funded-paper credential encoding refused") from None
        if (not 16 <= len(key) <= 4096 or not all(33 <= ord(char) <= 126 for char in key)):
            raise PermissionError("protected funded-paper credential format refused")
        result[pin.provider] = key
    return result


def verify_funded_network(profile: PreparedFundedPaperProfile, network: dict, proxy: dict, *,
                          daemon_security_options: list[str]) -> None:
    """Check exact reviewed proxy and dedicated internal bridge before create.

    Inspecting Docker proves these configured identities, not the proxy's routing
    policy or intended-host admission. The owner retains that separate review.
    """
    containers = network.get("Containers", {})
    peer = containers.get(profile.proxy_container_id, {})
    networks = proxy.get("NetworkSettings", {}).get("Networks", {})
    matching = [value for value in networks.values() if value.get("NetworkID") == profile.network_id]
    from trade_graph.kernel.deployment_image import image_default_masked_paths, image_default_readonly_paths

    host = proxy.get("HostConfig", {})
    user = proxy.get("Config", {}).get("User")
    if (network.get("Id") != profile.network_id or network.get("Driver") != "bridge"
            or network.get("Internal") is not True or network.get("EnableIPv6") is not False
            or network.get("ConfigOnly") is not False or network.get("Attachable") is not False
            or network.get("Scope") != "local" or network.get("Options")
            or type(containers) is not dict or set(containers) != {profile.proxy_container_id}
            or peer.get("IPv4Address", "").split("/", 1)[0] != profile.proxy_ipv4
            or peer.get("IPv6Address") or proxy.get("Id") != profile.proxy_container_id
            or proxy.get("Image") != profile.proxy_image_id or proxy.get("State", {}).get("Running") is not True
            or len(matching) != 1 or matching[0].get("IPAddress") != profile.proxy_ipv4
            or proxy.get("HostConfig", {}).get("Privileged") is not False
            or proxy.get("HostConfig", {}).get("ReadonlyRootfs") is not True
            or proxy.get("HostConfig", {}).get("NetworkMode") in {"host", "none"}
            or proxy.get("HostConfig", {}).get("PortBindings")
            or proxy.get("Mounts") != [] or type(user) is not str or not re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", user)
            or host.get("CapDrop") != ["ALL"] or host.get("CapAdd")
            or host.get("SecurityOpt") != ["no-new-privileges"]
            or not any(item.startswith("name=seccomp,profile=builtin") for item in daemon_security_options)
            or host.get("PidMode") or host.get("IpcMode") not in {"private", ""} or host.get("UTSMode")
            or host.get("UsernsMode") or host.get("CgroupnsMode") != "private" or host.get("Runtime") != "runc"
            or host.get("Devices") or host.get("DeviceRequests") or host.get("DeviceCgroupRules")
            or host.get("GroupAdd") or host.get("VolumesFrom") or host.get("Links") or host.get("Sysctls")
            or host.get("PublishAllPorts") is not False
            or host.get("MaskedPaths") != image_default_masked_paths()
            or host.get("ReadonlyPaths") != image_default_readonly_paths()):
        raise PermissionError("funded-paper network/proxy differs from separately reviewed owner pin")
