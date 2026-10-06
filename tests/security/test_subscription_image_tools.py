"""Optional native tools are reviewed, hash-pinned offline image inputs."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def builder():
    spec = importlib.util.spec_from_file_location("protected_image_builder", ROOT / "scripts/build_protected_image.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def api():
    module = builder()
    assert hasattr(module, "stage_subscription_tools"), "optional native image input staging is missing"
    assert hasattr(module, "subscription_dockerfile"), "optional offline Dockerfile assembly is missing"
    return module


def reviewed_inputs(tmp_path, monkeypatch):
    module = api()
    trusted = tmp_path / "trusted"
    (trusted / "deploy").mkdir(parents=True)
    policy = {
        "schema_version": 1,
        "platform": "linux/amd64",
        "base_image": "python@sha256:" + "1" * 64,
        "claude": {"version": "2.1.292", "sha256": hashlib.sha256(b"native-claude").hexdigest(),
                   "signing_fingerprint": "31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE"},
        "allowed_debian_packages": ["bubblewrap", "libcap2", "libseccomp2", "libselinux1", "libpcre2-8-0"],
    }
    (trusted / "deploy/subscription-native-inputs.json").write_text(json.dumps(policy))
    monkeypatch.setattr(module, "ROOT", trusted)
    directory = tmp_path / "native"
    directory.mkdir()
    (directory / "claude").write_bytes(b"native-claude")
    filename = "bubblewrap_0.11.0-2_amd64.deb"
    (directory / filename).write_bytes(b"reviewed-base-matching-deb")
    manifest = {
        "schema_version": 1,
        "platform": policy["platform"],
        "base_image": policy["base_image"],
        "claude_sha256": policy["claude"]["sha256"],
        "packages": [{"filename": filename, "package": "bubblewrap", "version": "0.11.0-2",
                      "architecture": "amd64",
                      "sha256": hashlib.sha256((directory / filename).read_bytes()).hexdigest()}],
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    context = tmp_path / "context"
    context.mkdir()
    # Only package inspection is external; a fixture cannot embed third-party
    # executables. Production invokes dpkg-deb against the hash-checked file.
    commands = []
    def inspect(arguments, *, output=False):
        commands.append(arguments)
        assert arguments[:2] == ["dpkg-deb", "--field"]
        return "Package: bubblewrap\nVersion: 0.11.0-2\nArchitecture: amd64"
    monkeypatch.setattr(module, "run", inspect)
    return module, directory, context, manifest, commands


def save_manifest(directory, manifest):
    (directory / "manifest.json").write_text(json.dumps(manifest))


def test_optional_inputs_copy_only_reviewed_binaries_and_record_complete_identity(tmp_path, monkeypatch):
    module, directory, context, manifest, commands = reviewed_inputs(tmp_path, monkeypatch)
    (directory / ".credentials.json").write_text("private-auth-must-never-enter-image")
    result = module.stage_subscription_tools(directory, context, {"platform": "linux/amd64",
                                                                "base_image": manifest["base_image"]})
    assert set(path.name for path in (context / "subscription-tools").iterdir()) == {
        "claude", manifest["packages"][0]["filename"], "manifest.json"}
    assert result["subscription_tools"]["claude"] == {
        "version": "2.1.292", "sha256": manifest["claude_sha256"],
        "signing_fingerprint": "31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE"}
    assert result["subscription_tools"]["packages"] == manifest["packages"]
    assert result["subscription_tools"]["manifest_sha256"] == hashlib.sha256(
        (directory / "manifest.json").read_bytes()).hexdigest()
    assert result["subscription_tools"]["inputs_sha256"] == module.directory_sha256(context / "subscription-tools")
    assert len(commands) == 1


@pytest.mark.parametrize("attack", ["claude_hash", "deb_hash", "platform", "base", "package", "version",
                                   "architecture", "absolute_file", "traversal_file", "duplicate_package",
                                   "no_bwrap", "extra_field", "linked_binary", "linked_directory"])
def test_changed_or_unreviewed_native_inputs_are_rejected_before_staging(tmp_path, monkeypatch, attack):
    module, directory, context, manifest, commands = reviewed_inputs(tmp_path, monkeypatch)
    package = manifest["packages"][0]
    if attack == "claude_hash":
        (directory / "claude").write_bytes(b"replacement")
    elif attack == "deb_hash":
        (directory / package["filename"]).write_bytes(b"replacement")
    elif attack == "platform":
        manifest["platform"] = "linux/arm64"
    elif attack == "base":
        manifest["base_image"] = "python@sha256:" + "2" * 64
    elif attack == "package":
        package["package"] = "libc6"
    elif attack == "version":
        package["version"] = "not-the-inspected-version"
    elif attack == "architecture":
        package["architecture"] = "arm64"
    elif attack == "absolute_file":
        package["filename"] = str(directory / package["filename"])
    elif attack == "traversal_file":
        package["filename"] = "../" + package["filename"]
    elif attack == "duplicate_package":
        manifest["packages"].append(dict(package))
    elif attack == "no_bwrap":
        manifest["packages"] = []
    elif attack == "extra_field":
        manifest["private_auth"] = "never-accepted"
    elif attack == "linked_binary":
        target = directory / "replacement"
        (directory / "claude").rename(target)
        (directory / "claude").symlink_to(target)
    elif attack == "linked_directory":
        target = tmp_path / "link"
        target.symlink_to(directory, target_is_directory=True)
        directory = target
    save_manifest(directory, manifest)
    with pytest.raises((ValueError, PermissionError)):
        module.stage_subscription_tools(directory, context, {"platform": "linux/amd64",
                                                            "base_image": "python@sha256:" + "1" * 64})
    assert not (context / "subscription-tools").exists()


def test_debian_inspection_must_match_hashed_manifest(tmp_path, monkeypatch):
    module, directory, context, manifest, commands = reviewed_inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "run", lambda *args, **kwargs: "Package: libc6\nVersion: 2.99\nArchitecture: amd64")
    with pytest.raises(ValueError):
        module.stage_subscription_tools(directory, context, {"platform": manifest["platform"],
                                                            "base_image": manifest["base_image"]})


def test_default_dockerfile_bytes_are_unchanged_and_optional_tools_precede_seal():
    module = api()
    original = (ROOT / "deploy/Dockerfile.protected").read_bytes()
    assert module.subscription_dockerfile(original, enabled=False) == original
    optional = module.subscription_dockerfile(original, enabled=True).decode()
    assert "COPY subscription-tools /subscription-tools" in optional
    assert "dpkg -i /subscription-tools/*.deb" in optional
    assert "--force-depends" not in optional and "apt-get" not in optional and "curl " not in optional
    assert "/opt/trade-graph/claude" in optional and "/usr/bin/bwrap --version" in optional
    assert optional.index("COPY subscription-tools") < optional.index("deployment_image seal")
    assert optional.index("rm -rf /subscription-tools") < optional.index("deployment_image seal")
    assert optional.count("USER 10001:10001") == 1


def test_reviewed_native_policy_pins_verified_claude_and_cannot_replace_base_libc():
    policy_path = ROOT / "deploy/subscription-native-inputs.json"
    assert policy_path.exists(), "reviewed subscription native policy is missing"
    policy = json.loads(policy_path.read_bytes())
    base = json.loads((ROOT / "deploy/protected-image-inputs.json").read_bytes())
    assert policy["base_image"] == base["base_image"] and policy["platform"] == base["platform"]
    assert policy["claude"] == {
        "version": "2.1.292",
        "sha256": "a967e7b1d8b4e47ee421d5433027880347952b0c0857abf880e2c942a4ec93b3",
        "signing_fingerprint": "31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE",
    }
    assert "bubblewrap" in policy["allowed_debian_packages"]
    assert "libc6" not in policy["allowed_debian_packages"]


def test_invalid_native_inputs_fail_before_any_network_package_acquisition(tmp_path, monkeypatch):
    module, directory, context, manifest, commands = reviewed_inputs(tmp_path, monkeypatch)
    inputs = {
        "schema_version": 1, "platform": manifest["platform"], "base_image": manifest["base_image"],
        "build_backend_requirement": "setuptools==80.9.0 --hash=sha256:" + "2" * 64,
        "source_date_epoch": 1780000000,
    }
    (module.ROOT / "deploy/protected-image-inputs.json").write_text(json.dumps(inputs))
    (directory / "claude").write_bytes(b"tampered-native")
    def no_commands(*args, **kwargs):
        pytest.fail("invalid native inputs reached external acquisition")
    monkeypatch.setattr(module, "run", no_commands)
    with pytest.raises(ValueError, match="Claude SHA256"):
        module.build(tmp_path / "fresh-output", subscription_tools=directory)


def test_duplicated_manifest_fields_are_rejected(tmp_path, monkeypatch):
    module, directory, context, manifest, commands = reviewed_inputs(tmp_path, monkeypatch)
    raw = (directory / "manifest.json").read_text()
    (directory / "manifest.json").write_text(raw[:-1] + ', "schema_version": 1}')
    with pytest.raises(ValueError, match="duplicate fields"):
        module.stage_subscription_tools(directory, context, {"platform": manifest["platform"],
                                                            "base_image": manifest["base_image"]})
