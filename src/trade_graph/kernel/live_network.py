"""Separate owner-pinned Kraken/public-FX proxy and immutable live launch profile.

No provider billing endpoint is admitted. The host-side controller inspects the
created container and dedicated internal network before allowing startup.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trade_graph.kernel.runtime_manifest import document_sha256

Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ImageId = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class PreparedLiveNetworkProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    deployment_id: str = Field(min_length=1, max_length=128)
    authorization_id: str = Field(min_length=1, max_length=128)
    image_id: ImageId
    manifest_sha256: Fingerprint
    protected_package_sha256: Fingerprint
    scope_sha256: Fingerprint
    live_config_sha256: Fingerprint
    network_id: Fingerprint
    proxy_container_id: Fingerprint
    proxy_image_id: ImageId
    proxy_ipv4: str
    proxy_port: int = Field(ge=1024, le=65535)
    proxy_review_sha256: Fingerprint

    @field_validator("proxy_ipv4")
    @classmethod
    def fixed_private_proxy(cls, value):
        address = ipaddress.IPv4Address(value)
        if (str(address) != value or not address.is_private or address.is_loopback
                or address.is_link_local or address.is_multicast or address.is_unspecified):
            raise ValueError("exact private live proxy IPv4 required")
        return value

    @property
    def sha256(self):
        return document_sha256(self.model_dump(mode="json"))


def load_live_network_profile(directory: Path) -> PreparedLiveNetworkProfile:
    from trade_graph.kernel.deployment_image import read_owner_file

    return PreparedLiveNetworkProfile.model_validate_json(
        read_owner_file(directory, "live-network-profile.json", 32768),
    )


def verify_live_network(profile: PreparedLiveNetworkProfile, network: dict, proxy: dict, *,
                        daemon_security_options: list[str]):
    if type(profile) is not PreparedLiveNetworkProfile:
        raise PermissionError("exact reviewed live network profile required")
    from trade_graph.kernel.funded_profile import verify_funded_network

    # Network/OS restrictions are identical; routing/credential semantics are
    # separately attested in the venue/operations live commission, never reused
    # from a model API funded-paper profile.
    verify_funded_network(profile, network, proxy, daemon_security_options=daemon_security_options)
