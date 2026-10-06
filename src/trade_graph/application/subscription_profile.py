"""Read-only root-distributed subscription admission; no model or private broker requests."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field, StrictBool, field_validator

from trade_graph.adapters.models.subscription import SubscriptionAdapter, assess_subscription, sanitize_quota
from trade_graph.adapters.models.subscription_process import (
    LinuxSubscriptionExecutor,
    NativeCliPin,
    probe_isolated_claude,
    probe_isolated_codex,
)
from trade_graph.application.subscription_runtime import SubscriptionRuntimeConfig
from trade_graph.contracts.models import ContractModel
from trade_graph.domain.errors import TradeGraphError
from trade_graph.kernel.deployment_image import assert_boot_environment, read_owner_file
from trade_graph.kernel.runtime_manifest import protected_package_sha256

_CHECKS = frozenset({"private_files_denied", "windows_mounts_denied", "host_proc_denied",
                     "api_environment_denied", "descendants_killed", "tools_disabled",
                     "direct_egress_denied", "provider_route_restricted", "market_route_restricted"})


def _profile_digest(directory: Path, raw: bytes, depth: int = 0) -> str:
    digest = hashlib.sha256(raw)
    for name in ("subscription-network-profile.json", "subscription-isolation.json", "subscription-seccomp.json",
                 "paper-config.json"):
        digest.update(name.encode() + b"\0")
        try:
            digest.update(read_owner_file(directory, name, 262144))
        except FileNotFoundError:
            digest.update(b"missing")
    profile = json.loads(raw, object_pairs_hook=_unique)
    if depth > 3:
        raise ValueError("subscription fallback nesting exceeds supported bounds")
    catalog_name = profile.get("model_catalog_file")
    if catalog_name:
        if not isinstance(catalog_name, str) or not re.fullmatch(r"[a-z0-9-]+\.json", catalog_name):
            raise ValueError("invalid protected model catalog filename")
        digest.update(catalog_name.encode() + read_owner_file(directory, catalog_name, 262144))
    quota_name = profile.get("quota_evidence_file")
    if quota_name:
        if not isinstance(quota_name, str) or not re.fullmatch(r"[a-z0-9-]+\.json", quota_name):
            raise ValueError("invalid protected quota evidence filename")
        digest.update(quota_name.encode() + read_owner_file(directory, quota_name, 32768))
    for name in profile.get("fallback_profiles", []):
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name):
            raise ValueError("invalid protected fallback directory")
        digest.update(name.encode() + b"\0")
        try:
            fallback_raw = read_owner_file(directory / name, "subscription-profile.json", 32768)
            digest.update(_profile_digest(directory / name, fallback_raw, depth + 1).encode())
        except FileNotFoundError:
            digest.update(b"missing")
    return digest.hexdigest()


def verify_subscription_egress(directory: Path, profile, manifest):
    from trade_graph.kernel.subscription_network import (
        load_subscription_network_profile,
        load_subscription_seccomp,
        validate_subscription_configuration,
    )

    network = load_subscription_network_profile(directory)
    validate_subscription_configuration(network, manifest, read_owner_file(directory, "paper-config.json", 262144))
    load_subscription_seccomp(directory, network)
    boundary = Path("/usr/bin/bwrap")
    metadata = boundary.stat()
    if (network.native_cli_sha256 != profile.native_sha256 or metadata.st_uid != 0
            or metadata.st_mode & 0o022
            or hashlib.sha256(boundary.read_bytes()).hexdigest() != network.boundary_sha256):
        raise PermissionError("subscription CLI/boundary differs from protected network assembly")
    return network


class PreparedSubscriptionProfile(ContractModel):
    schema_version: int = Field(ge=1, le=1)
    runtime: SubscriptionRuntimeConfig
    native_binary: str = Field(min_length=1, max_length=512)
    native_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    official_login_file: str = Field(min_length=1, max_length=512)
    extra_usage_disabled: StrictBool = False
    isolation_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_catalog_file: str | None = Field(default=None, pattern=r"^[a-z0-9-]+\.json$")
    quota_evidence_file: str | None = Field(default=None, pattern=r"^[a-z0-9-]+\.json$")
    fallback_profiles: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("fallback_profiles")
    @classmethod
    def protected_fallback_names(cls, value):
        if len(value) != len(set(value)) or any(not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name) for name in value):
            raise ValueError("unique protected fallback directory names required")
        return value


@dataclass(frozen=True)
class SubscriptionAdmission:
    config: SubscriptionRuntimeConfig | None
    adapter: SubscriptionAdapter | None
    status: dict
    profile_sha256: str | None = None


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate subscription profile field")
        value[key] = item
    return value


def _blocked(config=None, *reasons, profile_sha256=None):
    provider = config.subscription.provider if config else None
    status = {"ready": False, "selected_provider": provider, "provider": provider,
              "blockers": list(reasons), "quota": {}, "billing_kind": "subscription",
              "actual_cost_native": None, "cost_status": "unknown", "inference_attempts": 0,
              "application_automatic_retry": False, "application_automatic_fallback": False}
    return SubscriptionAdmission(config, None, status, profile_sha256)


def load_subscription_profile(protected_owner: Path, *, _depth: int = 0) -> SubscriptionAdmission:
    """Missing/refused admission leaves deterministic management available.

    Callers must recheck the returned profile hash on startup/readiness. Profile
    files and independent host evidence are root-distributed; runtime never
    fabricates them. Native login metadata is read only inside the exact mounted
    executor boundary, without reading the login file in application code.
    """
    config = None
    digest = None
    try:
        if _depth > 3:
            raise ValueError("subscription fallback nesting exceeds supported bounds")
        raw = read_owner_file(protected_owner, "subscription-profile.json", 32768)
        digest = _profile_digest(protected_owner, raw)
        profile = PreparedSubscriptionProfile.model_validate(json.loads(raw, object_pairs_hook=_unique))
        config = profile.runtime
        # Read-only owner files alone do not prove confinement of a credentialed
        # application process: require the established image boot boundary too.
        manifest = assert_boot_environment()
        network = verify_subscription_egress(protected_owner, profile, manifest)
        proof_raw = read_owner_file(protected_owner, "subscription-isolation.json", 32768)
        if hashlib.sha256(proof_raw).hexdigest() != profile.isolation_evidence_sha256:
            raise PermissionError("independent isolation proof changed")
        proof = json.loads(proof_raw, object_pairs_hook=_unique)
        if (type(proof) is not dict or set(proof) != {"schema_version", "kind", "native_cli_sha256",
                "protected_package_sha256", "checks"} or type(proof["schema_version"]) is not int
                or proof["schema_version"] != 1 or proof["kind"] != "actual-linux-boundary"
                or proof["native_cli_sha256"] != profile.native_sha256
                or proof["protected_package_sha256"] != protected_package_sha256()
                or type(proof["checks"]) is not list or any(type(item) is not str for item in proof["checks"])
                or len(proof["checks"]) != len(_CHECKS) or set(proof["checks"]) != _CHECKS):
            raise PermissionError("exact independent isolation evidence required")
        binary, login = Path(profile.native_binary), Path(profile.official_login_file)
        login_name = "auth.json" if config.subscription.provider == "codex_subscription" else ".credentials.json"
        if not binary.is_absolute() or not login.is_absolute() or login.name != login_name:
            raise PermissionError("exact native binary and official login mount required")
        catalog = None
        if config.subscription.provider == "codex_subscription":
            if profile.model_catalog_file is None:
                raise PermissionError("Codex requires a protected model catalog removing filesystem-capable tools")
            catalog = protected_owner / profile.model_catalog_file
            metadata_catalog = json.loads(read_owner_file(protected_owner, profile.model_catalog_file, 262144),
                                          object_pairs_hook=_unique)
            models = metadata_catalog.get("models") if isinstance(metadata_catalog, dict) else None
            if (not isinstance(models, list) or len(models) != 1 or not isinstance(models[0], dict)
                    or models[0].get("slug") != config.subscription.model
                    or models[0].get("shell_type") != "disabled"
                    or "apply_patch_tool_type" not in models[0] or models[0]["apply_patch_tool_type"] is not None
                    or models[0].get("experimental_supported_tools") != []):
                raise PermissionError("Codex model catalog retains unsupported filesystem tools")
        pin = NativeCliPin(binary, profile.native_sha256)
        pin.verify()
        probe = probe_isolated_codex if config.subscription.provider == "codex_subscription" else probe_isolated_claude
        metadata = probe(config.subscription, pin, login,
            extra_usage_disabled=profile.extra_usage_disabled, isolation_verified=True)
        # Recompute supported-version/login/extra-usage admission from sanitized
        # metadata; a claimed Boolean cannot admit an obsolete or unsigned CLI.
        quota = (sanitize_quota(json.loads(read_owner_file(protected_owner, profile.quota_evidence_file, 32768),
                                          object_pairs_hook=_unique))
                 if profile.quota_evidence_file else metadata["quota"])
        readiness = assess_subscription(config.subscription, cli_version=metadata["cli_version"],
            authentication=metadata["authentication"], quota=quota,
            extra_usage_disabled=profile.extra_usage_disabled, isolation_ready=True, native_linux=True)
        status = {**readiness.public_status(), "selected_provider": config.subscription.provider,
                  "inference_attempts": 0, "profile_sha256": digest}
        if not readiness.ready:
            return SubscriptionAdmission(config, None, status, digest)
        fallback_admissions = [load_subscription_profile(protected_owner / name, _depth=_depth + 1)
                               for name in profile.fallback_profiles]
        fallbacks = tuple(item.adapter for item in fallback_admissions if item.adapter is not None
                          and item.config.deployment_id == config.deployment_id)
        status["fallback_routes"] = [{"provider": item.status.get("provider"),
                                      "ready": item.adapter is not None,
                                      "blockers": item.status.get("blockers", [])}
                                     for item in fallback_admissions]
        status["application_automatic_fallback"] = bool(fallbacks)
        adapter = SubscriptionAdapter(config.subscription, readiness,
            LinuxSubscriptionExecutor(pin, login, proxy_url=network.proxy_url, config=config.subscription,
                                      model_catalog_file=catalog),
            fallbacks=fallbacks)
        return SubscriptionAdmission(config, adapter, status, digest)
    except FileNotFoundError:
        return _blocked(config, "protected subscription profile/native login/isolation evidence is not provisioned",
                        profile_sha256=digest)
    except (OSError, ValueError, TypeError, KeyError, RecursionError, TradeGraphError):
        return _blocked(config, "protected subscription profile, pinned native CLI or host isolation admission refused",
                        profile_sha256=digest)


def subscription_profile_unchanged(protected_owner: Path, profile_sha256: str | None) -> bool:
    """Recheck immutable owner bytes without another auth probe or inference."""
    try:
        (protected_owner / "subscription-profile.json").lstat()
    except FileNotFoundError:
        return profile_sha256 is None
    except OSError:
        return False
    try:
        raw = read_owner_file(protected_owner, "subscription-profile.json", 32768)
        return _profile_digest(protected_owner, raw) == profile_sha256
    except FileNotFoundError:
        return False
    except (OSError, ValueError, TradeGraphError):
        return False
