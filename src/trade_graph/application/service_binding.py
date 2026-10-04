"""Exact local systemd/unit/process observations, never deployment approval."""

from __future__ import annotations

import hashlib
import os
import pwd
import shlex
import stat
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path

from trade_graph.adapters.engineering.artifact_files import open_directory
from trade_graph.adapters.engineering.process import run_bounded
from trade_graph.kernel.runtime_manifest import canonical_json, protected_package_sha256

MAX_UNIT_BYTES = 65_536


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _fingerprint(value: str) -> bool:
    return type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def private_source(path: Path, limit: int, *, private: bool = True, root_owned: bool = False) -> bytes:
    """Check the complete ancestor chain and descriptor identity before reading."""
    parent = open_directory(path.parent)
    descriptor = -1
    try:
        directory = os.fstat(parent)
        if private and (directory.st_uid != os.geteuid() or directory.st_mode & 0o077):
            raise ValueError("service proof requires owner-private storage")
        descriptor = os.open(path.name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=parent)
        info = os.fstat(descriptor)
        owners = {os.geteuid(), 0} if root_owned else {os.geteuid()}
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in owners or info.st_nlink != 1
                or info.st_size > limit or (private and info.st_mode & 0o077)
                or (not private and info.st_mode & 0o022)):
            raise ValueError("service proof requires a bounded single-link protected regular file")
        chunks, total = [], 0
        while chunk := os.read(descriptor, min(65_536, limit + 1 - total)):
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise ValueError("service proof source exceeds byte bound")

        def identity(value):
            return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_nlink,
                    value.st_size, value.st_mtime_ns, value.st_ctime_ns)

        if (identity(info) != identity(os.fstat(descriptor))
                or identity(info) != identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False))):
            raise ValueError("service proof source changed during read")
        return b"".join(chunks)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def _unit(payload: bytes) -> dict[tuple[str, str], str]:
    """Support the simple supplied unit only; expansions/includes are refused."""
    document, section = {}, ""
    for line in payload.decode("utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith(("#", ";")):
            continue
        if value.startswith("[") and value.endswith("]"):
            section = value[1:-1]
            if section not in {"Unit", "Service", "Install"}:
                raise ValueError("unsupported service unit section")
            continue
        if not section or "=" not in value or value.endswith("\\"):
            raise ValueError("unsupported service unit syntax")
        name, text = value.split("=", 1)
        key = (section, name)
        if key in document or any(char in text for char in ("%", "$", "\x00")):
            raise ValueError("ambiguous or expanded service directive")
        document[key] = text
    return document


def _loader_environment(payload: bytes) -> None:
    """Inspect only names, never decode or retain credential/proxy values."""
    if len(payload) > 65_536:
        raise ValueError("running service environment exceeds observation bound")
    forbidden = {b"PYTHONPATH", b"PYTHONHOME", b"PYTHONSTARTUP", b"PYTHONINSPECT", b"PYTHONUSERBASE",
                 b"PYTHONPLATLIBDIR", b"PYTHONEXECUTABLE", b"LD_PRELOAD", b"LD_LIBRARY_PATH", b"LD_AUDIT"}
    for item in payload.rstrip(b"\0").split(b"\0") if payload else ():
        name, separator, _ = item.partition(b"=")
        if not separator or not name or len(name) > 128 or name in forbidden:
            raise ValueError("running service has unsupported interpreter loader environment")


def _proc_bytes(directory: int, name: str, limit: int) -> bytes:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise ValueError("service process source has an unexpected owner or type")
        chunks, total = [], 0
        while chunk := os.read(descriptor, min(8192, limit + 1 - total)):
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise ValueError("service process source exceeds observation bound")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class ServiceUnitBinding:
    """Owner-supplied exact expected bytes; creation supplies no owner authority."""

    unit_path: Path
    executable_path: Path
    database_path: Path
    working_directory: Path
    unit_sha256: str
    executable_sha256: str
    protected_package_sha256: str
    config_path: Path | None = None
    config_sha256: str | None = None
    user: str = "trade-graph"

    def __post_init__(self) -> None:
        paths = (self.unit_path, self.executable_path, self.database_path, self.working_directory)
        if (any(not isinstance(path, Path) or not path.is_absolute() or any(char.isspace() for char in str(path))
                for path in paths)
                or any(not _fingerprint(value) for value in
                       (self.unit_sha256, self.executable_sha256, self.protected_package_sha256))
                or type(self.user) is not str or not self.user or any(char.isspace() for char in self.user)
                or (self.config_path is None) != (self.config_sha256 is None)
                or (self.config_path is not None and (not isinstance(self.config_path, Path)
                    or not self.config_path.is_absolute() or not _fingerprint(self.config_sha256)))):
            raise ValueError("exact protected service binding required")

    @property
    def command(self) -> tuple[str, ...]:
        values = (str(self.executable_path), "run", "--mode", "paper", "--database", str(self.database_path))
        return values if self.config_path is None else values + ("--config", str(self.config_path))

    def verify_unit(self) -> dict:
        """Validate exact files and all authority-relevant unit directives locally."""
        if protected_package_sha256() != self.protected_package_sha256:
            raise ValueError("protected package/interpreter pin changed")
        payload = private_source(self.unit_path, MAX_UNIT_BYTES, private=False, root_owned=True)
        executable = private_source(self.executable_path, 1_048_576, private=False, root_owned=True)
        if _digest(payload) != self.unit_sha256 or _digest(executable) != self.executable_sha256:
            raise ValueError("service unit or executable byte pin mismatch")
        # An executable label does not prove what Python package it imports. The
        # supplied console script must be the fixed Trade Graph entry point and
        # must select this exact interpreter. Alternate wrappers are unsupported.
        text = executable.decode("utf-8")
        if (not text.startswith("#!" + sys.executable + "\n")
                or "from trade_graph.cli import main" not in text
                or "sys.exit(main())" not in text):
            raise ValueError("service console entry point is not the current protected interpreter")
        if self.config_path is not None:
            from trade_graph.paper_runtime import PaperRuntimeConfig

            configuration = private_source(self.config_path, 262_144)
            if _digest(configuration) != self.config_sha256:
                raise ValueError("service private configuration byte pin mismatch")
            PaperRuntimeConfig.model_validate_json(configuration)
        document = _unit(payload)
        required = {
            "Type": "simple", "User": self.user, "Group": self.user,
            "WorkingDirectory": str(self.working_directory), "UMask": "0077",
            "StateDirectory": "trade-graph", "StateDirectoryMode": "0700",
            "Restart": "on-failure", "RestartSec": "5", "TimeoutStopSec": "60", "KillSignal": "SIGTERM",
            "NoNewPrivileges": "yes", "PrivateTmp": "yes", "ProtectSystem": "strict", "ProtectHome": "yes",
            "ReadWritePaths": str(self.database_path.parent),
        }
        if ({name: value for (section, name), value in document.items() if section == "Service"}
                != dict(required, ExecStart=document.get(("Service", "ExecStart"), ""))
                or shlex.split(document.get(("Service", "ExecStart"), "")) != list(self.command)
                or {key: value for key, value in document.items() if key[0] != "Service"}
                != {("Unit", "Description"): "Trade Graph local paper service",
                    ("Unit", "After"): "network-online.target", ("Install", "WantedBy"): "multi-user.target"}):
            raise ValueError("service unit differs from fixed paper service profile")
        private_source(self.database_path, 16_777_216)
        return {"unit_sha256": self.unit_sha256, "executable_sha256": self.executable_sha256,
                "protected_package_sha256": self.protected_package_sha256, "config_sha256": self.config_sha256,
                "command_sha256": _digest(canonical_json(self.command).encode()),
                "unit_configuration_verified": True, "paid_permission_verified": False,
                "live_enabled": False, "owner_intended_host_verified": False}

    def observe(self) -> dict:
        """Probe the fixed systemd unit and exact live process without mutation."""
        facts = self.verify_unit()
        if not Path("/run/systemd/system").is_dir() or not Path("/usr/bin/systemctl").is_file():
            return {"status": "pending", "facts": dict(facts, actual_unit_verified=False,
                                                          unit_configuration_verified=False)}
        properties = ("LoadState", "ActiveState", "SubState", "MainPID", "FragmentPath", "DropInPaths",
                      "User", "Group", "WorkingDirectory", "Environment", "EnvironmentFiles", "ExecStart",
                      "NeedDaemonReload", "NoNewPrivileges", "PrivateTmp", "ProtectSystem", "ProtectHome", "UMask")
        result = run_bounded(["/usr/bin/systemctl", "show", "trade-graph-paper.service", "--no-pager",
                              "--property=" + ",".join(properties)], b"", cwd="/", wall_seconds=5)
        if result["exit_code"] or len(result["stdout"]) > 16_384 or len(result["stderr"]) > 4096:
            raise ValueError("bounded exact service probe failed")
        values = {}
        for line in result["stdout"].splitlines():
            if "=" not in line:
                raise ValueError("invalid systemd property response")
            name, value = line.split("=", 1)
            if name not in properties or name in values:
                raise ValueError("ambiguous systemd property response")
            values[name] = value
        if set(values) != set(properties):
            raise ValueError("incomplete systemd property response")
        if values["LoadState"] != "loaded" or values["ActiveState"] != "active" or values["SubState"] != "running":
            return {"status": "pending", "facts": dict(facts, actual_unit_verified=False,
                                                          unit_configuration_verified=False)}
        # Drop-ins, environment wrappers and commands beyond the pinned unit are
        # unsupported. Never retain environment values or raw process arguments.
        if (values["FragmentPath"] != str(self.unit_path) or values["DropInPaths"] or values["Environment"]
                or values["EnvironmentFiles"] or values["User"] != self.user or values["Group"] != self.user
                or values["WorkingDirectory"] != str(self.working_directory)
                or {name: values[name] for name in ("NeedDaemonReload", "NoNewPrivileges", "PrivateTmp",
                                                    "ProtectSystem", "ProtectHome", "UMask")}
                != {"NeedDaemonReload": "no", "NoNewPrivileges": "yes", "PrivateTmp": "yes",
                    "ProtectSystem": "strict", "ProtectHome": "yes", "UMask": "0077"}):
            raise ValueError("effective service configuration differs from pinned unit")
        expected_start = "{ path=" + str(self.executable_path) + " ; argv[]=" + " ".join(self.command) + " ;"
        if not values["ExecStart"].startswith(expected_start):
            raise ValueError("effective service command differs from pinned command")
        pid = values["MainPID"]
        if not pid.isdecimal() or not 1 <= int(pid) <= 4_194_304:
            raise ValueError("running service pid unavailable")
        installed = Path(sysconfig.get_path("purelib")) / "trade_graph"
        if Path(__file__).resolve().parents[1] != installed.resolve():
            raise ValueError("service observation requires the exact installed package origin")
        directory = open_directory(Path("/proc") / pid)
        try:
            info = os.fstat(directory)
            if info.st_uid != os.geteuid() or info.st_uid != pwd.getpwnam(self.user).pw_uid:
                raise ValueError("service process has an unexpected owner")
            # procfs is kernel-owned; exe is an intentional kernel symlink and
            # its target must be the protected Python interpreter. All process
            # reads use this opened directory, preventing PID-path substitution.
            if os.readlink("exe", dir_fd=directory) != str(Path(sys.executable).resolve()):
                raise ValueError("service process interpreter differs from protected interpreter")
            command = _proc_bytes(directory, "cmdline", 8192)
            _loader_environment(_proc_bytes(directory, "environ", 65_536))
        finally:
            os.close(directory)
        expected = [sys.executable, *self.command]
        if len(command) > 8192 or command.rstrip(b"\0").split(b"\0") != [value.encode() for value in expected]:
            raise ValueError("service process arguments differ from protected service command")
        return {"status": "observed", "facts": dict(facts, actual_unit_verified=True,
                                                        installed_package_origin_verified=True,
                                                        loader_environment_verified=True,
                                                        unit_loaded=True, unit_active=True,
                                                        unit="trade-graph-paper.service")}
