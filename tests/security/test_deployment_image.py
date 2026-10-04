"""Owner-image identities and pre-start configuration cannot be widened."""

import hashlib
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from trade_graph.kernel.deployment_image import (
    ENTRYPOINT,
    OWNER_MOUNT,
    PROXY_VARIABLES,
    STATE_MOUNT,
    TMPFS,
    DeploymentImagePin,
    ProtectedDeploymentSpec,
    image_default_masked_paths,
    image_default_readonly_paths,
    read_owner_file,
    verify_container_inspection,
    verify_image_archive,
    verify_image_inspection,
)
from trade_graph.kernel.deployment_probe import probe_source_sha256, run_probe
from trade_graph.kernel.runtime_manifest import ProtectedRuntimeManifest, protected_package_sha256


def deployment():
    pin = DeploymentImagePin(image_id="sha256:" + "1" * 64, base_image="python@sha256:" + "2" * 64,
        archive_sha256="3" * 64, seal_sha256="4" * 64, lock_sha256="5" * 64, requirements_sha256="6" * 64,
        wheelhouse_sha256="7" * 64, source_sha256="8" * 64, protected_package_sha256="9" * 64)
    manifest = ProtectedRuntimeManifest(schema_version=1, protected_package_sha256="9" * 64,
        deployment_id="owner-image", approved_source_sha256=("a" * 64,))
    return ProtectedDeploymentSpec(pin, manifest, Path("/opt/owner/deployment"), Path("/opt/owner/state"))


def inspected(spec):
    config = {"User": "10001:10001", "Entrypoint": ENTRYPOINT, "Cmd": ["boot"], "WorkingDir": STATE_MOUNT,
              "Env": ["PATH=/usr/local/bin:/usr/bin:/bin"]}
    image = {"Id": spec.image.image_id, "Os": "linux", "Architecture": "amd64", "Config": deepcopy(config)}
    container = {"Image": spec.image.image_id, "State": {"Status": "created", "Running": False},
        "Config": {**config, "Image": spec.image.image_id,
                   "Env": [*config["Env"], *(name + "=" for name in PROXY_VARIABLES)]}, "HostConfig": {
            "ReadonlyRootfs": True, "Privileged": False, "NetworkMode": "none", "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"], "IpcMode": "private", "CgroupnsMode": "private",
            "PidsLimit": 64, "Memory": 536870912, "MemorySwap": 536870912, "NanoCpus": 1000000000,
            "Tmpfs": {"/tmp": TMPFS}, "Init": True, "MaskedPaths": image_default_masked_paths(),
            "Runtime": "runc", "AutoRemove": False, "OomKillDisable": False, "PublishAllPorts": False,
            "ReadonlyPaths": image_default_readonly_paths(), "Ulimits": [{"Name": "core", "Hard": 0, "Soft": 0}]},
        "Mounts": [{"Type": "bind", "Source": str(spec.owner_directory), "Destination": OWNER_MOUNT,
                    "RW": False, "Propagation": "rprivate"},
                   {"Type": "bind", "Source": str(spec.state_directory), "Destination": STATE_MOUNT,
                    "RW": True, "Propagation": "rprivate"}]}
    return image, container


@pytest.mark.parametrize("identity", ["python:3.12", "sha256:" + "1" * 63, "sha256:" + "A" * 64, "latest"])
def test_mutable_image_identifiers_cannot_be_approved(identity):
    with pytest.raises(ValueError):
        replace(deployment().image, image_id=identity)


def test_exact_archive_pin_detects_replacement_and_link_substitution(tmp_path):
    archive = tmp_path / "image.tar"
    archive.write_bytes(b"synthetic-image-archive")
    pin = replace(deployment().image, archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    verify_image_archive(pin, archive)
    link = tmp_path / "mutable-link.tar"
    link.symlink_to(archive)
    with pytest.raises(PermissionError):
        verify_image_archive(pin, link)
    archive.write_bytes(b"replacement")
    with pytest.raises(PermissionError):
        verify_image_archive(pin, archive)


@pytest.mark.parametrize(("field", "value"), [
    ("ReadonlyRootfs", False), ("Privileged", True), ("NetworkMode", "host"), ("CapAdd", ["SYS_ADMIN"]),
    ("CapDrop", []), ("SecurityOpt", ["seccomp=unconfined"]), ("PidMode", "host"), ("IpcMode", "host"),
    ("UsernsMode", "host"), ("CgroupnsMode", "host"), ("PidsLimit", -1), ("Memory", 0), ("MemorySwap", -1),
    ("NanoCpus", 0), ("Tmpfs", {}), ("Init", False), ("Devices", [{"PathOnHost": "/dev/mem"}]),
    ("PortBindings", {"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8000"}]}),
    ("Sysctls", {"kernel.yama.ptrace_scope": "0"}), ("MaskedPaths", []), ("ReadonlyPaths", []),
    ("GroupAdd", ["0"]), ("DeviceCgroupRules", ["a *:* rwm"]), ("Runtime", "custom-privileged-runtime"),
    ("OomKillDisable", True), ("PublishAllPorts", True), ("AutoRemove", True),
])
def test_container_cannot_widen_resources_capabilities_egress_or_host_access(field, value):
    spec = deployment()
    image, container = inspected(spec)
    verify_container_inspection(spec, image, container, ["name=seccomp,profile=builtin"])
    container["HostConfig"][field] = value
    with pytest.raises(PermissionError):
        verify_container_inspection(spec, image, container, ["name=seccomp,profile=builtin"])


@pytest.mark.parametrize("attack", ["socket", "owner_write", "unexpected_source", "shared_propagation",
                                    "env_injection", "root_user", "entrypoint", "previously_started"])
def test_prestart_checks_reject_mount_and_command_substitution(attack):
    spec = deployment()
    image, container = inspected(spec)
    if attack == "socket":
        container["Mounts"].append({"Type": "bind", "Source": "/var/run/docker.sock",
                                    "Destination": "/var/run/docker.sock", "RW": True})
    elif attack == "owner_write":
        container["Mounts"][0]["RW"] = True
    elif attack == "unexpected_source":
        container["Mounts"][0]["Source"] = "/tmp/candidate-owner"
    elif attack == "shared_propagation":
        container["Mounts"][0]["Propagation"] = "rshared"
    elif attack == "env_injection":
        container["Config"]["Env"] = [*image["Config"]["Env"], "LD_PRELOAD=/tmp/attack.so"]
    elif attack == "root_user":
        container["Config"]["User"] = "0:0"
    elif attack == "entrypoint":
        container["Config"]["Entrypoint"] = ["python", "/tmp/candidate.py"]
    else:
        container["State"]["Status"] = "exited"
    with pytest.raises(PermissionError):
        verify_container_inspection(spec, image, container, ["name=seccomp,profile=builtin"])


def test_missing_seccomp_and_image_platform_change_fail_closed():
    spec = deployment()
    image, container = inspected(spec)
    with pytest.raises(PermissionError):
        verify_container_inspection(spec, image, container, [])
    image["Architecture"] = "arm64"
    with pytest.raises(PermissionError):
        verify_image_inspection(spec.image, image)


def test_owner_state_overlap_and_candidate_defined_commands_fail_closed():
    spec = deployment()
    with pytest.raises(ValueError):
        replace(spec, state_directory=spec.owner_directory / "state")
    with pytest.raises(ValueError):
        spec.create_arguments(name="trade-graph-local", action="candidate.py")
    args = spec.create_arguments(name="trade-graph-local")
    assert args[-2:] == [spec.image.image_id, "boot"]
    assert "/var/run/docker.sock" not in " ".join(args)


@pytest.mark.parametrize("name", ["../capability.key", "/etc/passwd", "foo\nbar", "..", "graph.py/secret"])
def test_owner_reader_cannot_expand_to_other_files(tmp_path, name):
    with pytest.raises(ValueError):
        read_owner_file(tmp_path, name, 32768)


def test_synthetic_installed_probe_requires_manifest_sources_and_recovery_preserves_history(tmp_path):
    manifest = ProtectedRuntimeManifest(schema_version=1, protected_package_sha256=protected_package_sha256(),
        deployment_id="deployment", approved_source_sha256=probe_source_sha256())
    key = b"synthetic-parent-probe-key-only-32-bytes-41"
    first = run_probe(manifest, state_directory=tmp_path, capability_key=key)
    second = run_probe(manifest, state_directory=tmp_path, capability_key=key)
    assert first["os_attacks_blocked"] == 30 and first["management_calls_during_failure"] >= 3
    assert second["phase"] == "restart_verified" and second["restart_dispatched"] == 0
    assert first["financial_history_sha256"] == second["financial_history_sha256"]
    assert first["deployed_engineer_authorization"] is False and first["intended_host_verified"] is False
    with pytest.raises(PermissionError):
        run_probe(replace(manifest, approved_source_sha256=("0" * 64,)), state_directory=tmp_path, capability_key=key)
