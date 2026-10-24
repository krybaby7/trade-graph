"""Explicit profile preparation never replenishes budgets or expands child rights."""

import hashlib
import json
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
from tests.security.test_deployment_image import deployment, inspected

from trade_graph.adapters.models.transport import HttpxProviderHttp
from trade_graph.application import deployment_runtime
from trade_graph.application.deployment_runtime import ProtectedDeploymentBinding
from trade_graph.domain.errors import AuthorityDenied
from trade_graph.kernel import deployment_image
from trade_graph.kernel.funded_profile import (
    PreparedFundedPaperProfile,
    ProviderCredentialPin,
    load_funded_credentials,
    load_funded_profile,
    verify_funded_network,
)
from trade_graph.kernel.runtime_manifest import ProtectedRuntimeManifest, canonical_json, protected_package_sha256
from trade_graph.paper_runtime import PaperRuntimeConfig

KEY = b"synthetic-provider-profile-fixture-only"


def profile(spec=None):
    spec = spec or deployment()
    return PreparedFundedPaperProfile(schema_version=1, profile_id="synthetic-only",
        deployment_id=spec.runtime_manifest.deployment_id, image_id=spec.image.image_id,
        manifest_sha256=spec.runtime_manifest.sha256, paper_config_sha256=hashlib.sha256(b"{}").hexdigest(),
        network_id="b" * 64, proxy_container_id="c" * 64, proxy_image_id="sha256:" + "d" * 64,
        proxy_ipv4="172.29.17.2", proxy_port=8443, proxy_review_sha256="e" * 64,
        credentials=(ProviderCredentialPin("openai", "openai.key", hashlib.sha256(KEY).hexdigest()),))


def network_pair(value):
    from trade_graph.kernel.deployment_image import image_default_masked_paths, image_default_readonly_paths
    network = {"Id": value.network_id, "Name": "synthetic-internal", "Driver": "bridge", "Internal": True,
        "EnableIPv6": False,
        "ConfigOnly": False, "Attachable": False, "Scope": "local", "Options": {},
        "Containers": {value.proxy_container_id: {"IPv4Address": value.proxy_ipv4 + "/24", "IPv6Address": ""}}}
    proxy = {"Id": value.proxy_container_id, "Image": value.proxy_image_id, "State": {"Running": True},
        "NetworkSettings": {"Networks": {"internal": {"NetworkID": value.network_id, "IPAddress": value.proxy_ipv4}}},
        "Config": {"User": "65532:65532"},
        "HostConfig": {"Privileged": False, "ReadonlyRootfs": True, "NetworkMode": value.network_id,
            "CapDrop": ["ALL"], "CapAdd": [], "SecurityOpt": ["no-new-privileges"], "PidMode": "",
            "IpcMode": "private", "UTSMode": "", "UsernsMode": "", "CgroupnsMode": "private", "Runtime": "runc",
            "Devices": [], "DeviceRequests": [], "DeviceCgroupRules": [], "GroupAdd": [], "VolumesFrom": [],
            "Links": [], "Sysctls": {}, "PublishAllPorts": False,
            "MaskedPaths": image_default_masked_paths(), "ReadonlyPaths": image_default_readonly_paths()}, "Mounts": []}
    return network, proxy


def test_default_offline_launch_is_unchanged_and_funded_launch_is_explicit():
    ordinary = deployment()
    assert ordinary.create_arguments(name="trade-graph-offline")[-1] == "boot"
    args = ordinary.create_arguments(name="trade-graph-offline")
    assert args[args.index("--network") + 1] == "none"
    with pytest.raises(ValueError):
        ordinary.create_arguments(name="trade-graph-missing-profile", action="boot-funded")
    funded = replace(ordinary, funded_profile=profile(ordinary))
    with pytest.raises(ValueError):
        funded.create_arguments(name="trade-graph-ambiguous")
    prepared = funded.create_arguments(name="trade-graph-funded-preparation", action="check-funded")
    assert prepared[prepared.index("--network") + 1] == funded.funded_profile.network_id
    assert "openai.key" not in " ".join(prepared) and KEY.decode() not in " ".join(prepared)
    assert "--read-only" in prepared and "ALL" in prepared and "/var/run/docker.sock" not in " ".join(prepared)


@pytest.mark.parametrize(("field", "value"), [
    ("image_id", "latest"), ("manifest_sha256", "A" * 64), ("network_id", "host"),
    ("proxy_ipv4", "127.0.0.1"), ("proxy_ipv4", "169.254.169.254"), ("proxy_ipv4", "8.8.8.8"),
    ("proxy_ipv4", True), ("proxy_ipv4", 2887586050),
    ("proxy_port", True), ("proxy_port", 443), ("credentials", ()), ("credentials", []),
])
def test_profile_cannot_expand_to_unpinned_identity_or_ambient_metadata_service(field, value):
    with pytest.raises(ValueError):
        replace(profile(), **{field: value})


@pytest.mark.parametrize("attack", ["external", "host", "second_peer", "proxy_image", "proxy_stopped",
                                  "proxy_privileged", "proxy_writable", "published", "socket", "socket_alias",
                                  "run_directory", "arbitrary_mount", "missing_mounts", "proxy_ip",
                                  "ipv6", "network_driver"])
def test_unreviewed_network_and_proxy_configuration_refused(attack):
    value = profile()
    network, proxy = network_pair(value)
    verify_funded_network(value, network, proxy, daemon_security_options=["name=seccomp,profile=builtin"])
    if attack == "external":
        network["Internal"] = False
    elif attack == "host":
        proxy["HostConfig"]["NetworkMode"] = "host"
    elif attack == "second_peer":
        network["Containers"]["f" * 64] = {"IPv4Address": "172.29.17.3/24"}
    elif attack == "proxy_image":
        proxy["Image"] = "sha256:" + "a" * 64
    elif attack == "proxy_stopped":
        proxy["State"]["Running"] = False
    elif attack == "proxy_privileged":
        proxy["HostConfig"]["Privileged"] = True
    elif attack == "proxy_writable":
        proxy["HostConfig"]["ReadonlyRootfs"] = False
    elif attack == "published":
        proxy["HostConfig"]["PortBindings"] = {"8443/tcp": [{"HostPort": "8443"}]}
    elif attack == "socket":
        proxy["Mounts"] = [{"Source": "/var/run/docker.sock"}]
    elif attack == "socket_alias":
        proxy["Mounts"] = [{"Type": "bind", "Source": "/run/docker.sock", "Destination": "/var/run/docker.sock"}]
    elif attack == "run_directory":
        proxy["Mounts"] = [{"Type": "bind", "Source": "/run", "Destination": "/run"}]
    elif attack == "arbitrary_mount":
        proxy["Mounts"] = [{"Type": "volume", "Source": "unreviewed-volume", "Destination": "/config"}]
    elif attack == "missing_mounts":
        del proxy["Mounts"]
    elif attack == "proxy_ip":
        network["Containers"][value.proxy_container_id]["IPv4Address"] = "172.29.17.3/24"
    elif attack == "ipv6":
        network["EnableIPv6"] = True
    else:
        network["Driver"] = "overlay"
    with pytest.raises(PermissionError):
        verify_funded_network(value, network, proxy, daemon_security_options=["name=seccomp,profile=builtin"])


@pytest.mark.parametrize(("field", "value"), [
    ("CapDrop", []), ("CapAdd", ["SYS_PTRACE", "SYS_ADMIN"]),
    ("SecurityOpt", ["no-new-privileges", "seccomp=unconfined"]),
    ("SecurityOpt", ["no-new-privileges", "apparmor=unconfined"]),
    ("PidMode", "host"), ("IpcMode", "host"), ("UTSMode", "host"), ("UsernsMode", "host"),
    ("CgroupnsMode", "host"), ("Runtime", "unreviewed"), ("Devices", [{"PathOnHost": "/dev/mem"}]),
    ("DeviceRequests", [{"Count": -1}]), ("DeviceCgroupRules", ["a *:* rwm"]), ("GroupAdd", ["docker"]),
    ("VolumesFrom", ["unreviewed"]), ("Links", ["host-proxy"]), ("Sysctls", {"kernel.yama.ptrace_scope": "0"}),
    ("PublishAllPorts", True), ("MaskedPaths", []), ("ReadonlyPaths", []),
])
def test_proxy_cannot_expand_host_namespace_capability_or_device_authority(field, value):
    prepared = profile()
    network, proxy = network_pair(prepared)
    proxy["HostConfig"][field] = value
    with pytest.raises(PermissionError):
        verify_funded_network(prepared, network, proxy, daemon_security_options=["name=seccomp,profile=builtin"])


@pytest.mark.parametrize("user", ["", "root", "0:0", "65532:0", "app", True])
def test_proxy_requires_explicit_numeric_unprivileged_user(user):
    prepared = profile()
    network, proxy = network_pair(prepared)
    proxy["Config"]["User"] = user
    with pytest.raises(PermissionError):
        verify_funded_network(prepared, network, proxy, daemon_security_options=["name=seccomp,profile=builtin"])


def test_proxy_requires_independently_inspected_builtin_daemon_seccomp():
    prepared = profile()
    network, proxy = network_pair(prepared)
    with pytest.raises(PermissionError):
        verify_funded_network(prepared, network, proxy, daemon_security_options=[])


def test_prestart_funded_inspection_requires_separate_network_and_exact_action():
    ordinary = deployment()
    value = profile(ordinary)
    funded = replace(ordinary, funded_profile=value)
    image, container = inspected(ordinary)
    container["Config"]["Cmd"] = ["check-funded"]
    container["HostConfig"]["NetworkMode"] = value.network_id
    container["NetworkSettings"]["Networks"] = {"synthetic-internal": {"NetworkID": ""}}
    network, proxy = network_pair(value)
    with pytest.raises(PermissionError):
        deployment_image.verify_container_inspection(funded, image, container,
            ["name=seccomp,profile=builtin"], action="check-funded")
    deployment_image.verify_container_inspection(funded, image, container,
        ["name=seccomp,profile=builtin"], action="check-funded", network=network, proxy=proxy)
    container["HostConfig"]["NetworkMode"] = "bridge"
    with pytest.raises(PermissionError):
        deployment_image.verify_container_inspection(funded, image, container,
            ["name=seccomp,profile=builtin"], action="check-funded", network=network, proxy=proxy)


def test_secure_profile_credentials_require_exact_bytes_and_never_accept_exchange_key(monkeypatch, tmp_path):
    value = profile()
    files = {"funded-paper-profile.json": canonical_json(asdict(value)).encode(), "openai.key": KEY}
    monkeypatch.setattr(deployment_image, "read_owner_file", lambda _directory, name, _maximum: files[name])
    assert load_funded_profile(tmp_path) == value
    assert load_funded_credentials(tmp_path, value) == {"openai": KEY.decode()}
    files["openai.key"] = KEY + b"changed"
    with pytest.raises(PermissionError):
        load_funded_credentials(tmp_path, value)
    with pytest.raises(ValueError):
        ProviderCredentialPin("kraken", "kraken.key", "1" * 64)
    duplicate = json.loads(files["funded-paper-profile.json"])
    duplicate["credentials"].append(duplicate["credentials"][0])
    files["funded-paper-profile.json"] = json.dumps(duplicate).encode()
    with pytest.raises(ValueError):
        load_funded_profile(tmp_path)


def test_provider_transport_uses_only_fixed_profile_endpoint_and_ignores_proxy_environment(monkeypatch):
    import httpx

    calls = []
    class Response:
        status_code, headers = 200, {}
        def iter_bytes(self):
            yield b'{"id":"synthetic-only"}'
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
    def stream(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()
    monkeypatch.setattr(httpx, "stream", stream)
    monkeypatch.setenv("HTTPS_PROXY", "http://unapproved.invalid:8080")
    value = profile()
    transport = HttpxProviderHttp(proxy=value.proxy_url, trust_env=False, allowed_urls=value.endpoints)
    assert transport.post_json(value.endpoints[0], {}, {}) == {"id": "synthetic-only"}
    assert calls[0][1]["trust_env"] is False and calls[0][1]["proxy"] == value.proxy_url
    assert calls[0][1]["follow_redirects"] is False
    for target in ("http://api.openai.com/v1/responses", "https://api.openai.com@evil.invalid/v1/responses",
                   "https://api.openai.com/v1/responses?key=leak", "http://169.254.169.254/latest/meta-data/"):
        with pytest.raises(ValueError):
            transport.post_json(target, {}, {"authorization": "Bearer " + KEY.decode()})
    assert len(calls) == 1


def test_profile_binding_retains_owner_paid_gate_and_refuses_configuration_and_credential_drift(tmp_path, monkeypatch):
    source = 'def graph(context):\n    return {"node":"invoke_model","guidance":"synthetic"}\n'
    manifest = ProtectedRuntimeManifest(schema_version=1, protected_package_sha256=protected_package_sha256(),
        deployment_id="owner-image", approved_source_sha256=(hashlib.sha256(source.encode()).hexdigest(),),
        operations=("submit_decision", "invoke_model", "apply_role_result"))
    value = replace(profile(), manifest_sha256=manifest.sha256)
    runtime = SimpleNamespace(deployment_id="owner-image", config=PaperRuntimeConfig(), model_config=None,
                              paid_calls_enabled=False, live_enabled=False)
    files = {"funded-paper-profile.json": canonical_json(asdict(value)).encode(), "paper-config.json": b"{}",
             "openai.key": KEY}
    (tmp_path / "funded-paper-profile.json").write_bytes(files["funded-paper-profile.json"])
    monkeypatch.setattr(deployment_runtime, "_owner_bundle", lambda _directory: (manifest, source, b"k" * 32))
    monkeypatch.setattr(deployment_image, "read_owner_file", lambda _directory, name, _maximum: files[name])
    binding = ProtectedDeploymentBinding(runtime, tmp_path)
    assert binding._unchanged() and binding.api_keys == {"openai": KEY.decode()}
    assert runtime.paid_calls_enabled is False and runtime.live_enabled is False and runtime.model_config is None
    assert binding.transport.trust_env is False
    with pytest.raises(AuthorityDenied):
        ProtectedDeploymentBinding(runtime, tmp_path, api_keys={"openai": KEY.decode()})
    with pytest.raises(AuthorityDenied):
        ProtectedDeploymentBinding(runtime, tmp_path, transport=HttpxProviderHttp())
    files["openai.key"] = b"synthetic-but-different-provider-key"
    with pytest.raises(PermissionError):
        binding._unchanged()
    files["openai.key"] = KEY
    files["paper-config.json"] = b'{"public_data_enabled":true}'
    with pytest.raises(AuthorityDenied):
        binding._unchanged()
