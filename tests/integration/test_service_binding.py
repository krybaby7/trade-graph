"""Exact fixed unit and byte binding; mocked systemd never proves actual deployment."""

import hashlib
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from trade_graph.application.operations_evidence import HostObservationCollector
from trade_graph.application.service_binding import ServiceUnitBinding, _loader_environment, _proc_bytes, private_source
from trade_graph.kernel.runtime_manifest import protected_package_sha256


def binding(tmp_path, *, config=False):
    tmp_path.chmod(0o700)
    executable, database, unit = tmp_path / "trade-graph", tmp_path / "paper.sqlite", tmp_path / "paper.service"
    executable.write_text(f"#!{sys.executable}\nimport sys\nfrom trade_graph.cli import main\nsys.exit(main())\n")
    database.write_bytes(b"synthetic bounded service binding fixture")
    database.chmod(0o600)
    configuration = tmp_path / "paper-config.json" if config else None
    if configuration is not None:
        configuration.write_text('{"models":null,"public_data_enabled":false}')
        configuration.chmod(0o600)
    text = Path("deploy/trade-graph-paper.service").read_text()
    text = text.replace("/opt/trade-graph/.venv/bin/trade-graph", str(executable))
    text = text.replace("/var/lib/trade-graph/trade_graph.sqlite", str(database))
    text = text.replace("WorkingDirectory=/opt/trade-graph", f"WorkingDirectory={tmp_path}")
    text = text.replace("ReadWritePaths=/var/lib/trade-graph", f"ReadWritePaths={tmp_path}")
    if config:
        text = text.replace(str(database) + "\n", str(database) + " --config " + str(configuration) + "\n")
    unit.write_text(text)
    return ServiceUnitBinding(unit, executable, database, tmp_path, hashlib.sha256(unit.read_bytes()).hexdigest(),
                              hashlib.sha256(executable.read_bytes()).hexdigest(), protected_package_sha256(),
                              configuration, None if configuration is None else
                              hashlib.sha256(configuration.read_bytes()).hexdigest())


def test_exact_template_configuration_and_console_bytes_can_be_inspected_without_host_authority(tmp_path):
    source = binding(tmp_path, config=True)
    result = source.verify_unit()
    assert result["unit_configuration_verified"] is True
    assert result["config_sha256"] == source.config_sha256
    assert result["paid_permission_verified"] is result["owner_intended_host_verified"] is False
    if not Path("/run/systemd/system").is_dir():
        observed = source.observe()
        assert observed["status"] == "pending" and observed["facts"]["unit_configuration_verified"] is False


@pytest.mark.parametrize("change", ["unit", "executable", "configuration", "package"])
def test_byte_and_package_pin_drift_is_refused(tmp_path, change):
    source = binding(tmp_path, config=True)
    if change == "package":
        source = replace(source, protected_package_sha256="0" * 64)
    else:
        path = {"unit": source.unit_path, "executable": source.executable_path,
                "configuration": source.config_path}[change]
        path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="pin"):
        source.verify_unit()


@pytest.mark.parametrize("directive", ["Environment=OPENAI_API_KEY=private-value", "ExecStartPost=/bin/true",
                                      "ProtectSystem=no", "User=root", "NoNewPrivileges=no",
                                      "ExecStart=/bin/sh -c true", "LoadCredential=key:/tmp/private-value",
                                      "OnFailure=unreviewed.service"])
def test_even_re_pinned_unsupported_unit_authority_is_refused(tmp_path, directive):
    source = binding(tmp_path)
    payload = source.unit_path.read_text().replace("[Install]", directive + "\n\n[Install]")
    source.unit_path.write_text(payload)
    source = replace(source, unit_sha256=hashlib.sha256(payload.encode()).hexdigest())
    with pytest.raises(ValueError):
        source.verify_unit()


@pytest.mark.parametrize("attack", ["symlink", "fifo", "hardlink", "public-config", "writable-unit"])
def test_regular_owner_file_boundaries_are_checked_before_any_read(tmp_path, attack):
    source = binding(tmp_path, config=True)
    if attack == "public-config":
        source.config_path.chmod(0o644)
    elif attack == "writable-unit":
        source.unit_path.chmod(0o666)
    else:
        raw = source.config_path.read_bytes()
        source.config_path.unlink()
        if attack == "fifo":
            os.mkfifo(source.config_path)
        else:
            target = tmp_path / "other-config"
            target.write_bytes(raw)
            target.chmod(0o600)
            if attack == "symlink":
                source.config_path.symlink_to(target)
            else:
                os.link(target, source.config_path)
    with pytest.raises((OSError, ValueError)):
        source.verify_unit()


def test_ancestor_symlink_and_changed_file_during_read_are_refused(tmp_path, monkeypatch):
    directory = tmp_path / "actual"
    directory.mkdir(mode=0o700)
    file = directory / "private.json"
    file.write_bytes(b"test")
    file.chmod(0o600)
    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    with pytest.raises(OSError):
        private_source(alias / file.name, 100)
    read = os.read

    def change(descriptor, amount):
        payload = read(descriptor, amount)
        if payload:
            file.write_bytes(b"different")
        return payload

    monkeypatch.setattr(os, "read", change)
    with pytest.raises(ValueError, match="changed during read"):
        private_source(file, 100)


def test_host_observation_binding_requires_same_database_and_configuration(tmp_path):
    source = binding(tmp_path, config=True)
    with pytest.raises(ValueError, match="same database"):
        HostObservationCollector(None, signing_key=b"synthetic-local-host-binding-key", service_binding=source)
    with pytest.raises(ValueError, match="exact protected"):
        HostObservationCollector(None, signing_key=b"synthetic-local-host-binding-key", service_binding=object())


@pytest.mark.parametrize("changed", ["FragmentPath", "DropInPaths", "Environment", "EnvironmentFiles", "User",
                                     "Group", "WorkingDirectory", "ExecStart", "NeedDaemonReload",
                                     "NoNewPrivileges", "PrivateTmp", "ProtectSystem", "ProtectHome", "UMask"])
def test_effective_systemd_configuration_drift_is_refused_without_retaining_values(tmp_path, monkeypatch, changed):
    source = binding(tmp_path)
    values = {"LoadState": "loaded", "ActiveState": "active", "SubState": "running", "MainPID": "1",
              "FragmentPath": str(source.unit_path), "DropInPaths": "", "User": "trade-graph", "Group": "trade-graph",
              "WorkingDirectory": str(source.working_directory), "Environment": "", "EnvironmentFiles": "",
              "ExecStart": "{ path=" + str(source.executable_path) + " ; argv[]=" + " ".join(source.command) + " ; }",
              "NeedDaemonReload": "no", "NoNewPrivileges": "yes", "PrivateTmp": "yes", "ProtectSystem": "strict",
              "ProtectHome": "yes", "UMask": "0077"}
    values[changed] = "private-proxy-credential-sentinel"
    is_dir = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda path: True if str(path) == "/run/systemd/system" else is_dir(path))
    monkeypatch.setattr("trade_graph.application.service_binding.run_bounded", lambda *args, **kwargs: {
        "exit_code": 0, "stdout": "\n".join(f"{name}={value}" for name, value in values.items()), "stderr": ""})
    with pytest.raises(ValueError) as caught:
        source.observe()
    assert "sentinel" not in str(caught.value)


@pytest.mark.parametrize("response", ["LoadState=loaded\nLoadState=loaded", "LoadState=loaded",
                                     "unparseable", "x" * 20000])
def test_ambiguous_missing_or_oversized_systemd_response_is_refused(tmp_path, monkeypatch, response):
    source = binding(tmp_path)
    is_dir = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda path: True if str(path) == "/run/systemd/system" else is_dir(path))
    monkeypatch.setattr("trade_graph.application.service_binding.run_bounded", lambda *args, **kwargs: {
        "exit_code": 0, "stdout": response, "stderr": ""})
    with pytest.raises(ValueError):
        source.observe()


@pytest.mark.parametrize("name", ["PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "LD_PRELOAD", "LD_LIBRARY_PATH"])
def test_running_service_loader_substitution_is_refused_without_value_disclosure(name):
    with pytest.raises(ValueError) as caught:
        _loader_environment((name + "=private-value\0OPENAI_API_KEY=private-secret\0").encode())
    assert "private" not in str(caught.value)


def test_credential_and_proxy_values_are_opaque_during_loader_name_inspection():
    assert _loader_environment(b"PATH=/usr/bin\0OPENAI_API_KEY=\xff\xfe\0HTTPS_PROXY=private-value\0") is None
    with pytest.raises(ValueError):
        _loader_environment(b"malformed")
    with pytest.raises(ValueError):
        _loader_environment(b"OPENAI_API_KEY=" + b"x" * 65536)


def test_partial_process_reads_cannot_hide_a_later_loader_substitution(tmp_path, monkeypatch):
    payload = b"PATH=/usr/bin\0OPENAI_API_KEY=opaque\0PYTHONPATH=unapproved\0"
    (tmp_path / "environ").write_bytes(payload)
    read = os.read
    monkeypatch.setattr(os, "read", lambda descriptor, amount: read(descriptor, min(3, amount)))
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        actual = _proc_bytes(descriptor, "environ", 100)
        assert actual == payload
        with pytest.raises(ValueError):
            _loader_environment(actual)
        with pytest.raises(ValueError, match="bound"):
            _proc_bytes(descriptor, "environ", 5)
    finally:
        os.close(descriptor)
