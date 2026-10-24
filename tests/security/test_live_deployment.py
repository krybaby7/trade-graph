"""Synthetic inspections verify supported live launch; no container/venue calls."""

from copy import deepcopy
from dataclasses import replace

import pytest
from tests.security.test_deployment_image import deployment, inspected
from tests.security.test_funded_profile import network_pair

from trade_graph.kernel import deployment_image, live_network
from trade_graph.kernel.live_network import PreparedLiveNetworkProfile
from trade_graph.live_runtime import LiveRuntimeConfig, live_configuration_digest


def profile(spec=None):
    spec = spec or deployment()
    return PreparedLiveNetworkProfile(
        schema_version=1, deployment_id=spec.runtime_manifest.deployment_id,
        authorization_id="synthetic-live-authorization", image_id=spec.image.image_id,
        manifest_sha256=spec.runtime_manifest.sha256,
        protected_package_sha256=spec.image.protected_package_sha256,
        scope_sha256="a" * 64, live_config_sha256=live_configuration_digest(LiveRuntimeConfig()),
        network_id="b" * 64, proxy_container_id="c" * 64, proxy_image_id="sha256:" + "d" * 64,
        proxy_ipv4="172.29.17.2", proxy_port=8443, proxy_review_sha256="e" * 64,
    )


def live_inspection(action="boot-live"):
    ordinary = deployment()
    value = profile(ordinary)
    spec = replace(ordinary, live_profile=value)
    image, container = inspected(ordinary)
    container["Config"]["Cmd"] = [action]
    container["HostConfig"]["NetworkMode"] = value.network_id
    container["NetworkSettings"]["Networks"] = {"synthetic-internal": {"NetworkID": ""}}
    network, proxy = network_pair(value)
    return spec, image, container, network, proxy


@pytest.mark.parametrize("action", ["boot-live", "check-live"])
def test_live_supported_entrypoint_preserves_os_boundary_and_exact_proxy(action):
    spec, image, container, network, proxy = live_inspection(action)
    args = spec.create_arguments(name="trade-graph-live-synthetic", action=action)
    assert args[-1] == action
    assert args[args.index("--network") + 1] == spec.live_profile.network_id
    assert "--read-only" in args and "ALL" in args and "no-new-privileges" in args
    assert "kraken.key" not in " ".join(args) and "OPENAI_API_KEY" not in " ".join(args)
    deployment_image.verify_container_inspection(spec, image, container, ["name=seccomp,profile=builtin"],
                                                action=action, network=network, proxy=proxy)


def test_ordinary_paper_launch_and_unreviewed_live_launch_cannot_share_profile():
    ordinary = deployment()
    with pytest.raises(ValueError, match="live launch"):
        ordinary.create_arguments(name="trade-graph-uncommissioned", action="boot-live")
    commissioned = replace(ordinary, live_profile=profile(ordinary))
    with pytest.raises(ValueError, match="live launch"):
        commissioned.create_arguments(name="trade-graph-downgrade", action="boot")


@pytest.mark.parametrize("attack", ["no_inspection", "external_network", "extra_network", "proxy_changed",
                                    "owner_writable", "socket", "seccomp", "wrong_mode"])
def test_live_container_cannot_expand_network_mount_or_execution_permissions(attack):
    spec, image, container, network, proxy = live_inspection()
    daemon = ["name=seccomp,profile=builtin"]
    if attack == "no_inspection":
        proxy = None
    elif attack == "external_network":
        network["Internal"] = False
    elif attack == "extra_network":
        container["NetworkSettings"]["Networks"]["public"] = {"NetworkID": "f" * 64}
    elif attack == "proxy_changed":
        proxy["Image"] = "sha256:" + "a" * 64
    elif attack == "owner_writable":
        container["Mounts"][0]["RW"] = True
    elif attack == "socket":
        container["Mounts"].append({"Type": "bind", "Source": "/var/run/docker.sock",
                                    "Destination": "/var/run/docker.sock", "RW": True})
    elif attack == "seccomp":
        daemon = []
    else:
        container["Config"]["Cmd"] = ["boot"]
    with pytest.raises(PermissionError):
        deployment_image.verify_container_inspection(spec, image, container, daemon, action="boot-live",
                                                    network=network, proxy=proxy)


def test_boot_live_routes_the_fixed_cli_mode_and_check_live_never_starts_worker(monkeypatch, capsys):
    from trade_graph import cli, live_runtime

    value = profile()
    config = LiveRuntimeConfig()
    calls = []
    monkeypatch.setattr(deployment_image, "assert_boot_environment", lambda: deployment().runtime_manifest)
    monkeypatch.setattr(live_network, "load_live_network_profile", lambda _directory: value)
    monkeypatch.setattr(live_runtime, "load_live_runtime_config", lambda _path: config)
    monkeypatch.setattr(live_runtime, "live_startup_prerequisites", lambda *_a, **_k: {
        "ready": False, "mode": "live", "status": "blocked", "reason": "synthetic uncommissioned"})
    monkeypatch.setattr(cli, "main", lambda args: calls.append(args) or 0)
    assert deployment_image.main(["check-live"]) == 0
    assert calls == [] and '"ready":false' in capsys.readouterr().out
    assert deployment_image.main(["boot-live"]) == 0
    assert calls == [["run", "--mode", "live", "--database", "/var/lib/trade-graph/trade_graph.sqlite",
                      "--protected-owner", "/run/trade-graph-owner"]]
    # Source/config binding is checked before delegating any worker startup.
    monkeypatch.setattr(live_network, "load_live_network_profile", lambda _directory:
                        value.model_copy(update={"live_config_sha256": "f" * 64}))
    with pytest.raises(PermissionError, match="configuration/profile mismatch"):
        deployment_image.main(["boot-live"])
    assert len(calls) == 1


def test_fixed_public_live_fetch_origin_rejects_private_or_arbitrary_hosts():
    from trade_graph.adapters.market.live_feed import LivePublicTextTransport
    from trade_graph.domain.errors import AuthorityDenied

    transport = LivePublicTextTransport("http://172.29.17.2:8443")
    for url in ("http://api.kraken.com/0/public/Ticker", "https://api.kraken.com/0/private/AddOrder",
                "https://api.kraken.com.attacker.test/0/public/Ticker", "https://169.254.169.254/latest",
                "https://user:password@api.kraken.com/0/public/Ticker"):
        with pytest.raises(AuthorityDenied):
            transport.get_text(url)


def test_network_inspection_is_not_mutated_by_verification():
    spec, image, container, network, proxy = live_inspection()
    before = deepcopy((image, container, network, proxy))
    deployment_image.verify_container_inspection(spec, image, container, ["name=seccomp,profile=builtin"],
                                                action="boot-live", network=network, proxy=proxy)
    assert before == (image, container, network, proxy)


def test_live_dashboard_launch_requires_explicit_loopback_publication_pin():
    ordinary = deployment()
    unapproved = replace(ordinary, live_profile=profile(ordinary))
    with pytest.raises(ValueError, match="dashboard publication"):
        unapproved.create_arguments(name="trade-graph-live-dashboard", action="boot-live-dashboard")
    value = profile(ordinary).model_copy(update={"dashboard_host_port": 18000})
    approved = replace(ordinary, live_profile=value)
    args = approved.create_arguments(name="trade-graph-live-dashboard", action="boot-live-dashboard")
    assert args[args.index("--publish") + 1] == "127.0.0.1:18000:8000/tcp"
    assert args[-1] == "boot-live-dashboard"
    assert "--pid" not in args and "--privileged" not in args
    assert "--publish" not in approved.create_arguments(name="trade-graph-live-worker", action="boot-live")


@pytest.mark.parametrize("port", [True, "8000", 80, 65536, -1, 1.5])
def test_live_dashboard_profile_rejects_coerced_or_unbounded_host_ports(port):
    with pytest.raises(ValueError):
        PreparedLiveNetworkProfile.model_validate(profile().model_dump() | {"dashboard_host_port": port})


def test_live_dashboard_prestart_requires_exact_loopback_and_no_other_ports():
    spec, image, container, network, proxy = live_inspection("boot-live-dashboard")
    spec = replace(spec, live_profile=spec.live_profile.model_copy(update={"dashboard_host_port": 18000}))
    container["HostConfig"]["PortBindings"] = {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18000"}]}
    container["Config"]["ExposedPorts"] = {"8000/tcp": {}}
    deployment_image.verify_container_inspection(spec, image, container, ["name=seccomp,profile=builtin"],
        action="boot-live-dashboard", network=network, proxy=proxy)
    for attack in ("public", "wrong_port", "second_host", "udp", "extra_exposed", "worker_publish"):
        candidate = deepcopy(container)
        action = "boot-live-dashboard"
        if attack == "public":
            candidate["HostConfig"]["PortBindings"]["8000/tcp"][0]["HostIp"] = "0.0.0.0"
        elif attack == "wrong_port":
            candidate["HostConfig"]["PortBindings"]["8000/tcp"][0]["HostPort"] = "8000"
        elif attack == "second_host":
            candidate["HostConfig"]["PortBindings"]["8000/tcp"].append({"HostIp": "::1", "HostPort": "18000"})
        elif attack == "udp":
            candidate["HostConfig"]["PortBindings"]["8000/udp"] = [{"HostIp": "127.0.0.1", "HostPort": "18000"}]
        elif attack == "extra_exposed":
            candidate["Config"]["ExposedPorts"]["22/tcp"] = {}
        else:
            candidate["Config"]["Cmd"] = ["boot-live"]
            action = "boot-live"
        with pytest.raises(PermissionError):
            deployment_image.verify_container_inspection(spec, image, candidate, ["name=seccomp,profile=builtin"],
                action=action, network=network, proxy=proxy)


def test_boot_live_dashboard_routes_view_only_fixed_cli_without_any_worker(monkeypatch):
    from trade_graph import cli, live_runtime

    value = profile().model_copy(update={"dashboard_host_port": 18000})
    calls = []
    monkeypatch.setattr(deployment_image, "assert_boot_environment", lambda: deployment().runtime_manifest)
    monkeypatch.setattr(live_network, "load_live_network_profile", lambda _directory: value)
    monkeypatch.setattr(live_runtime, "load_live_runtime_config", lambda _path: LiveRuntimeConfig())
    monkeypatch.setattr(cli, "main", lambda args: calls.append(args) or 0)
    assert deployment_image.main(["boot-live-dashboard"]) == 0
    assert calls == [["dashboard", "--mode", "live", "--database", "/var/lib/trade-graph/trade_graph.sqlite",
        "--protected-owner", "/run/trade-graph-owner", "--session-file", "/var/lib/trade-graph/owner-session.json",
        "--protected-network-bind", "--port", "8000"]]
    monkeypatch.setattr(live_network, "load_live_network_profile", lambda _directory: profile())
    with pytest.raises(PermissionError, match="dashboard publication"):
        deployment_image.main(["boot-live-dashboard"])
    assert len(calls) == 1
