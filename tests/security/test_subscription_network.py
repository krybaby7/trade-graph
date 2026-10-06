"""Subscription-only paper launch pins networking and nested namespace confinement."""

import hashlib
import json
from copy import deepcopy
from dataclasses import replace

import pytest
from tests.security.test_deployment_image import deployment, inspected
from tests.security.test_funded_profile import network_pair
from tests.security.test_funded_profile import profile as funded_profile
from tests.security.test_live_deployment import profile as live_profile

from trade_graph.kernel import deployment_image


def seccomp_document():
    return {"defaultAction": "SCMP_ACT_ERRNO", "defaultErrnoRet": 1,
            "syscalls": [{"names": ["read", "write", "exit", "exit_group", "clone", "unshare",
                                      "mount", "umount2", "pivot_root", "sethostname"],
                          "action": "SCMP_ACT_ALLOW"}]}


def profile(spec=None, **updates):
    from trade_graph.kernel.subscription_network import PreparedSubscriptionNetworkProfile
    spec = spec or deployment()
    raw = json.dumps(seccomp_document()).encode()
    document = dict(schema_version=1, deployment_id=spec.runtime_manifest.deployment_id,
        image_id=spec.image.image_id, manifest_sha256=spec.runtime_manifest.sha256,
        protected_package_sha256=spec.image.protected_package_sha256,
        paper_config_sha256=hashlib.sha256(b"{}").hexdigest(), native_cli_sha256="a" * 64,
        boundary_sha256="f" * 64, network_id="b" * 64, proxy_container_id="c" * 64,
        proxy_image_id="sha256:" + "d" * 64, proxy_ipv4="172.29.17.2", proxy_port=8080,
        market_proxy_port=8081, proxy_review_sha256="e" * 64,
        seccomp_path=str(spec.owner_directory / "subscription-seccomp.json"),
        seccomp_sha256=hashlib.sha256(raw).hexdigest())
    return PreparedSubscriptionNetworkProfile.model_validate(document | updates)


def owner_files(value):
    return {"subscription-network-profile.json": value.model_dump_json().encode(),
            "subscription-seccomp.json": json.dumps(seccomp_document()).encode(), "paper-config.json": b"{}"}


def trust_files(monkeypatch, value, files=None):
    files = files or owner_files(value)
    def read(_directory, name, _maximum):
        if name not in files:
            raise FileNotFoundError(name)
        return files[name]
    monkeypatch.setattr(deployment_image, "read_owner_file", read)
    return files


def subscription_inspection(monkeypatch, action="check-subscription"):
    ordinary = deployment()
    value = profile(ordinary)
    spec = replace(ordinary, subscription_profile=value)
    trust_files(monkeypatch, value)
    image, container = inspected(ordinary)
    container["Config"]["Cmd"] = [action]
    container["HostConfig"]["NetworkMode"] = value.network_id
    container["HostConfig"]["SecurityOpt"] = ["no-new-privileges", "seccomp=" + json.dumps(seccomp_document())]
    container["NetworkSettings"]["Networks"] = {"synthetic-internal": {"NetworkID": ""}}
    network, proxy = network_pair(value)
    return spec, image, container, network, proxy


def test_subscription_profile_has_separate_provider_market_routes_and_no_credentials():
    value = profile()
    assert value.proxy_url == "http://172.29.17.2:8080"
    assert value.market_proxy_url == "http://172.29.17.2:8081"
    assert len(value.sha256) == 64 and "credentials" not in value.model_dump()


@pytest.mark.parametrize(("field", "value"), [
    ("schema_version", True), ("schema_version", "1"), ("image_id", "latest"),
    ("manifest_sha256", "A" * 64), ("native_cli_sha256", "changed"), ("boundary_sha256", "changed"),
    ("proxy_ipv4", "127.0.0.1"), ("proxy_ipv4", "169.254.169.254"), ("proxy_ipv4", "8.8.8.8"),
    ("proxy_port", True), ("proxy_port", "8080"), ("proxy_port", 443), ("market_proxy_port", 8080),
    ("market_proxy_port", True), ("seccomp_path", "unconfined"), ("seccomp_path", "../profile.json"),
    ("seccomp_path", "/opt/owner/../seccomp.json"), ("seccomp_path", "/tmp/profile\n.json"),
    ("credentials", []), ("api_keys", {}),
])
def test_profile_refuses_coerced_unpinned_and_api_configuration(field, value):
    with pytest.raises(ValueError):
        profile(**{field: value})


@pytest.mark.parametrize("field", ["image_id", "manifest_sha256", "protected_package_sha256", "deployment_id"])
def test_network_profile_cannot_bind_a_different_image_runtime_or_deployment(field):
    ordinary = deployment()
    value = profile()
    altered = "sha256:" + "f" * 64 if field == "image_id" else "different" if field == "deployment_id" else "f" * 64
    with pytest.raises(ValueError, match="subscription"):
        replace(ordinary, subscription_profile=value.model_copy(update={field: altered}))


def test_subscription_cannot_reuse_funded_live_or_ordinary_launch():
    ordinary = deployment()
    value = profile()
    with pytest.raises(ValueError, match="subscription"):
        ordinary.create_arguments(name="trade-graph-missing", action="boot-subscription")
    for extra in ({"funded_profile": funded_profile()}, {"live_profile": live_profile()}):
        with pytest.raises(ValueError, match="subscription"):
            replace(ordinary, subscription_profile=value, **extra)
    spec = replace(ordinary, subscription_profile=value)
    for action in ("boot", "boot-funded", "boot-live", "boot-live-dashboard"):
        with pytest.raises(ValueError):
            spec.create_arguments(name="trade-graph-wrong", action=action)


@pytest.mark.parametrize("action", ["boot-subscription", "check-subscription"])
def test_subscription_launch_keeps_unprivileged_boundary_and_exact_seccomp_path(action):
    ordinary = deployment()
    value = profile()
    spec = replace(ordinary, subscription_profile=value)
    args = spec.create_arguments(name="trade-graph-subscription", action=action)
    assert args[-2:] == [spec.image.image_id, action]
    assert args[args.index("--network") + 1] == value.network_id
    assert [args[index + 1] for index, item in enumerate(args) if item == "--security-opt"] == [
        "no-new-privileges", "seccomp=" + value.seccomp_path]
    assert "--privileged" not in args and "--cap-add" not in args and "--publish" not in args
    assert "--read-only" in args and args[args.index("--user") + 1] == "10001:10001"
    assert args.count("--mount") == 2 and "--env" in args


def test_subscription_prestart_checks_exact_reviewed_seccomp_and_network(monkeypatch):
    spec, image, container, network, proxy = subscription_inspection(monkeypatch)
    deployment_image.verify_container_inspection(spec, image, container, ["name=seccomp,profile=builtin"],
        action="check-subscription", network=network, proxy=proxy)
    before = deepcopy((image, container, network, proxy))
    deployment_image.verify_container_inspection(spec, image, container, ["name=seccomp,profile=builtin"],
        action="check-subscription", network=network, proxy=proxy)
    assert before == (image, container, network, proxy)


@pytest.mark.parametrize("attack", ["unconfined", "default_seccomp", "changed_seccomp", "privileged",
    "capability", "api_env", "extra_network", "public_network", "no_proxy", "writable_owner", "socket"])
def test_subscription_prestart_refuses_security_and_network_widening(monkeypatch, attack):
    spec, image, container, network, proxy = subscription_inspection(monkeypatch)
    if attack == "unconfined":
        container["HostConfig"]["SecurityOpt"] = ["no-new-privileges", "seccomp=unconfined"]
    elif attack == "default_seccomp":
        container["HostConfig"]["SecurityOpt"] = ["no-new-privileges"]
    elif attack == "changed_seccomp":
        altered = seccomp_document() | {"defaultAction": "SCMP_ACT_ALLOW"}
        container["HostConfig"]["SecurityOpt"][1] = "seccomp=" + json.dumps(altered)
    elif attack == "privileged":
        container["HostConfig"]["Privileged"] = True
    elif attack == "capability":
        container["HostConfig"]["CapAdd"] = ["SYS_ADMIN"]
    elif attack == "api_env":
        container["Config"]["Env"].append("ANTHROPIC_API_KEY=synthetic-refused")
    elif attack == "extra_network":
        container["NetworkSettings"]["Networks"]["bridge"] = {"NetworkID": "f" * 64}
    elif attack == "public_network":
        network["Internal"] = False
    elif attack == "no_proxy":
        proxy = None
    elif attack == "writable_owner":
        container["Mounts"][0]["RW"] = True
    else:
        container["Mounts"].append({"Type": "bind", "Source": "/var/run/docker.sock"})
    with pytest.raises(PermissionError):
        deployment_image.verify_container_inspection(spec, image, container, ["name=seccomp,profile=builtin"],
            action="check-subscription", network=network, proxy=proxy)


def test_protected_network_profile_and_seccomp_loading_are_hash_bound(monkeypatch, tmp_path):
    from trade_graph.kernel.subscription_network import load_subscription_network_profile, load_subscription_seccomp
    value = profile()
    files = trust_files(monkeypatch, value)
    assert load_subscription_network_profile(tmp_path) == value
    assert load_subscription_seccomp(tmp_path, value) == seccomp_document()
    files["subscription-seccomp.json"] += b" "
    with pytest.raises(PermissionError, match="seccomp"):
        load_subscription_seccomp(tmp_path, value)


@pytest.mark.parametrize("document", [
    {"defaultAction": "SCMP_ACT_ALLOW", "syscalls": []},
    {"defaultAction": "SCMP_ACT_ERRNO", "syscalls": [{"names": ["*"], "action": "SCMP_ACT_ALLOW"}]},
    {"defaultAction": "SCMP_ACT_ERRNO", "syscalls": [{"names": ["mount"], "action": "SCMP_ACT_NOTIFY"}]},
])
def test_pinned_seccomp_cannot_disable_filtering_or_delegate_policy(monkeypatch, tmp_path, document):
    from trade_graph.kernel.subscription_network import load_subscription_seccomp
    raw = json.dumps(document).encode()
    value = profile(seccomp_sha256=hashlib.sha256(raw).hexdigest())
    files = owner_files(value) | {"subscription-seccomp.json": raw}
    trust_files(monkeypatch, value, files)
    with pytest.raises(PermissionError, match="seccomp"):
        load_subscription_seccomp(tmp_path, value)


def test_network_profile_loader_rejects_duplicate_fields(monkeypatch, tmp_path):
    from trade_graph.kernel.subscription_network import load_subscription_network_profile
    value = profile()
    files = owner_files(value)
    files["subscription-network-profile.json"] = b'{"schema_version":1,"schema_version":1}'
    trust_files(monkeypatch, value, files)
    with pytest.raises(ValueError):
        load_subscription_network_profile(tmp_path)


def test_subscription_boot_is_one_paper_tick_and_metadata_check_never_launches_worker(monkeypatch, capsys):
    from trade_graph import cli
    from trade_graph.kernel import subscription_network
    value = profile()
    calls = []
    trust_files(monkeypatch, value)
    monkeypatch.setattr(deployment_image, "assert_boot_environment", lambda: deployment().runtime_manifest)
    monkeypatch.setattr(subscription_network, "load_subscription_network_profile", lambda _directory: value)
    monkeypatch.setattr(cli, "main", lambda args: calls.append(args) or 0)
    assert deployment_image.main(["check-subscription"]) == 0
    assert calls == [] and '"live_authorization":false' in capsys.readouterr().out
    assert deployment_image.main(["boot-subscription"]) == 0
    assert calls == [["run", "--mode", "paper", "--database", "/var/lib/trade-graph/trade_graph.sqlite",
                      "--protected-owner", "/run/trade-graph-owner", "--once"]]
    monkeypatch.setattr(subscription_network, "load_subscription_network_profile", lambda _directory:
                        value.model_copy(update={"paper_config_sha256": "f" * 64}))
    with pytest.raises(PermissionError, match="configuration/profile"):
        deployment_image.main(["boot-subscription"])
    assert len(calls) == 1


@pytest.mark.parametrize("config", [b'{"models":{}}', b'{"price_cards":[{}]}'])
def test_subscription_entrypoint_refuses_api_configuration_before_worker(monkeypatch, config):
    from trade_graph import cli
    from trade_graph.kernel import subscription_network
    value = profile(paper_config_sha256=hashlib.sha256(config).hexdigest())
    files = owner_files(value) | {"paper-config.json": config}
    trust_files(monkeypatch, value, files)
    monkeypatch.setattr(deployment_image, "assert_boot_environment", lambda: deployment().runtime_manifest)
    monkeypatch.setattr(subscription_network, "load_subscription_network_profile", lambda _directory: value)
    calls = []
    monkeypatch.setattr(cli, "main", lambda args: calls.append(args) or 0)
    with pytest.raises((ValueError, PermissionError)):
        deployment_image.main(["boot-subscription"])
    assert not calls



def test_seccomp_parser_refuses_nonfinite_json_even_with_matching_owner_pin(monkeypatch, tmp_path):
    from trade_graph.kernel.subscription_network import load_subscription_seccomp
    raw = b'{"defaultAction":"SCMP_ACT_ERRNO","defaultErrnoRet":NaN,' \
          b'"syscalls":[{"names":["read"],"action":"SCMP_ACT_ALLOW"}]}'
    value = profile(seccomp_sha256=hashlib.sha256(raw).hexdigest())
    files = owner_files(value) | {"subscription-seccomp.json": raw}
    trust_files(monkeypatch, value, files)
    with pytest.raises(PermissionError, match="seccomp"):
        load_subscription_seccomp(tmp_path, value)


def test_runtime_cannot_start_subscription_bundle_through_another_launch_action(monkeypatch):
    value = profile()
    trust_files(monkeypatch, value)
    monkeypatch.setattr(deployment_image, "assert_boot_environment", lambda: deployment().runtime_manifest)
    for action in ("boot", "boot-funded", "boot-live", "check-boot"):
        with pytest.raises(PermissionError, match="separate paper launch"):
            deployment_image.main([action])


@pytest.mark.parametrize("name", ["funded-paper-profile.json", "live-network-profile.json"])
def test_subscription_entrypoint_rejects_other_runtime_profiles_before_worker(monkeypatch, name):
    from trade_graph import cli
    from trade_graph.kernel import subscription_network
    value = profile()
    files = owner_files(value) | {name: b"{}"}
    trust_files(monkeypatch, value, files)
    monkeypatch.setattr(deployment_image, "assert_boot_environment", lambda: deployment().runtime_manifest)
    monkeypatch.setattr(subscription_network, "load_subscription_network_profile", lambda _directory: value)
    calls = []
    monkeypatch.setattr(cli, "main", lambda args: calls.append(args) or 0)
    with pytest.raises(PermissionError, match="refuses funded API and live"):
        deployment_image.main(["boot-subscription"])
    assert not calls


def test_host_distribution_checks_profile_and_seccomp_before_create(monkeypatch, tmp_path):
    import os
    from dataclasses import asdict
    ordinary = replace(deployment(), owner_directory=tmp_path / "owner", state_directory=tmp_path / "state")
    ordinary.owner_directory.mkdir(mode=0o700)
    ordinary.state_directory.mkdir(mode=0o700)
    value = profile(ordinary)
    spec = replace(ordinary, subscription_profile=value)
    files = trust_files(monkeypatch, value)
    monkeypatch.setattr(deployment_image, "_owner_path", lambda *_a, **_k: None)
    monkeypatch.setattr(deployment_image, "UID", os.getuid())
    monkeypatch.setattr(deployment_image, "GID", os.getgid())
    monkeypatch.setattr(deployment_image, "load_owner_runtime_manifest_document",
                        lambda _path: asdict(ordinary.runtime_manifest))
    spec.verify_host_paths()
    files["subscription-seccomp.json"] += b" "
    with pytest.raises(PermissionError, match="seccomp"):
        spec.verify_host_paths()
    files["subscription-seccomp.json"] = json.dumps(seccomp_document()).encode()
    changed = value.model_copy(update={"proxy_port": 8082})
    files["subscription-network-profile.json"] = changed.model_dump_json().encode()
    with pytest.raises(PermissionError, match="network profile differs"):
        spec.verify_host_paths()
