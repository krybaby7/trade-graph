"""Owner-side immutable image and Docker launch checks, outside mutable workers.

The Docker socket belongs to the human deployment controller, never a container.
An image seal describes a build; the externally approved image ID and archive
hash establish its identity. Neither a candidate nor this module grants authority.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import stat
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from trade_graph.kernel.funded_profile import PreparedFundedPaperProfile
from trade_graph.kernel.live_network import PreparedLiveNetworkProfile
from trade_graph.kernel.runtime_manifest import (
    ProtectedRuntimeManifest,
    canonical_json,
    document_sha256,
    protected_package_sha256,
)

OWNER_MOUNT = "/run/trade-graph-owner"
STATE_MOUNT = "/var/lib/trade-graph"
OWNER_MANIFEST = Path(OWNER_MOUNT) / "runtime-manifest.json"
IMAGE_SEAL = Path("/opt/trade-graph/image-seal.json")
UID = GID = 10001
PLATFORM = "linux/amd64"
ENTRYPOINT = ["python", "-I", "-B", "-m", "trade_graph.kernel.deployment_image"]
TMPFS = "rw,noexec,nosuid,nodev,size=67108864,mode=1777"
PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                   "http_proxy", "https_proxy", "all_proxy", "no_proxy")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_BASE = re.compile(r"[a-z0-9][a-z0-9./_-]*@sha256:[0-9a-f]{64}\Z")


def _sha256(value: str) -> None:
    if type(value) is not str or not _HEX.fullmatch(value):
        raise ValueError("exact SHA256 identity required")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def directory_sha256(root: Path) -> str:
    """Hash exact regular files by relative path; symlinks cannot select inputs."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("build inputs cannot contain symlinks")
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(bytes.fromhex(file_sha256(path)))
    return digest.hexdigest()


@dataclass(frozen=True)
class DeploymentImagePin:
    image_id: str
    base_image: str
    archive_sha256: str
    seal_sha256: str
    lock_sha256: str
    requirements_sha256: str
    wheelhouse_sha256: str
    source_sha256: str
    protected_package_sha256: str

    def __post_init__(self):
        if (type(self.image_id) is not str or not self.image_id.startswith("sha256:")
                or type(self.base_image) is not str or not _BASE.fullmatch(self.base_image)):
            raise ValueError("immutable image ID and digest-only base required")
        _sha256(self.image_id[7:])
        for item in fields(self):
            if item.name not in {"image_id", "base_image"}:
                _sha256(getattr(self, item.name))


def verify_image_archive(pin: DeploymentImagePin, archive: Path) -> None:
    if archive.is_symlink() or not archive.is_file() or file_sha256(archive) != pin.archive_sha256:
        raise PermissionError("distributed image archive differs from owner-pinned bytes")


def verify_image_inspection(pin: DeploymentImagePin, inspection: dict) -> None:
    """Check owner-pinned Docker image identity; never accept tags or image labels."""
    config = inspection.get("Config", {})
    if (inspection.get("Id") != pin.image_id or inspection.get("Os") != "linux"
            or inspection.get("Architecture") != "amd64"
            or config.get("User") != f"{UID}:{GID}" or config.get("Entrypoint") != ENTRYPOINT
            or config.get("Cmd") != ["boot"] or config.get("Volumes")
            or config.get("OnBuild") or config.get("WorkingDir") != STATE_MOUNT):
        raise PermissionError("owner-pinned immutable deployment image mismatch")


def _owner_path(path: Path, *, directory: bool) -> None:
    """Root controls every host ancestor; reject links and writable ancestors."""
    if not path.is_absolute() or ".." in path.parts:
        raise PermissionError("absolute owner-controlled mount path required")
    for ancestor in (*reversed(path.parents), path):
        info = ancestor.lstat()
        if (info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode)
                or not stat.S_ISDIR(info.st_mode) and ancestor != path):
            raise PermissionError("mount distribution path is not root-controlled")
    info = path.lstat()
    if (directory and not stat.S_ISDIR(info.st_mode)) or (not directory and not stat.S_ISREG(info.st_mode)):
        raise PermissionError("unexpected owner-controlled path type")


def load_owner_runtime_manifest(path: Path = OWNER_MANIFEST) -> ProtectedRuntimeManifest:
    document = json.loads(read_owner_file(path.parent, path.name, 32768), object_pairs_hook=_unique_fields)
    names = {item.name for item in fields(ProtectedRuntimeManifest)}
    if type(document) is not dict or set(document) != names:
        raise ValueError("exact complete protected runtime manifest required")
    for name in ("approved_source_sha256", "operations"):
        if type(document[name]) is not list:
            raise ValueError("protected manifest arrays required")
        document[name] = tuple(document[name])
    result = ProtectedRuntimeManifest(**document)
    result.assert_current()
    return result


def read_owner_file(directory: Path, name: str, maximum_bytes: int) -> bytes:
    """Read a bounded root-distributed file through a verified directory handle."""
    if (type(name) is not str or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,127}", name)
            or type(maximum_bytes) is not int or not 1 <= maximum_bytes <= 1048576):
        raise ValueError("bounded single owner filename required")
    _owner_path(directory, directory=True)
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    descriptor = None
    try:
        directory_info = os.fstat(directory_fd)
        if directory_info.st_uid != 0 or directory_info.st_mode & 0o022:
            raise PermissionError("owner distribution directory identity changed")
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory_fd)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o027
                or info.st_size > maximum_bytes):
            raise PermissionError("owner file identity, mode or size is not protected")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read(maximum_bytes + 1)
        if len(payload) > maximum_bytes:
            raise ValueError("owner file exceeds retained size bound")
        return payload
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory_fd)


def _unique_fields(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate protected manifest field")
        document[key] = value
    return document


@dataclass(frozen=True)
class ProtectedDeploymentSpec:
    image: DeploymentImagePin
    runtime_manifest: ProtectedRuntimeManifest
    owner_directory: Path
    state_directory: Path
    funded_profile: PreparedFundedPaperProfile | None = None
    live_profile: PreparedLiveNetworkProfile | None = None

    def __post_init__(self):
        if (not isinstance(self.image, DeploymentImagePin)
                or not isinstance(self.runtime_manifest, ProtectedRuntimeManifest)
                or self.image.protected_package_sha256 != self.runtime_manifest.protected_package_sha256
                or not isinstance(self.owner_directory, Path) or not isinstance(self.state_directory, Path)
                or self.owner_directory == self.state_directory
                or self.owner_directory in self.state_directory.parents
                or self.state_directory in self.owner_directory.parents):
            raise ValueError("separate owner mounts and image-bound runtime manifest required")
        if self.funded_profile is not None and (
            type(self.funded_profile) is not PreparedFundedPaperProfile
            or self.funded_profile.image_id != self.image.image_id
            or self.funded_profile.manifest_sha256 != self.runtime_manifest.sha256
            or self.funded_profile.deployment_id != self.runtime_manifest.deployment_id
        ):
            raise ValueError("funded-paper profile requires exact image/runtime/deployment pins")
        if self.live_profile is not None and (
            self.funded_profile is not None or type(self.live_profile) is not PreparedLiveNetworkProfile
            or self.live_profile.image_id != self.image.image_id
            or self.live_profile.manifest_sha256 != self.runtime_manifest.sha256
            or self.live_profile.protected_package_sha256 != self.image.protected_package_sha256
            or self.live_profile.deployment_id != self.runtime_manifest.deployment_id
        ):
            raise ValueError("live profile requires separate exact image/runtime/deployment pins")

    def verify_host_paths(self) -> None:
        _owner_path(self.owner_directory, directory=True)
        # The state leaf belongs to the credentialed parent, while its ancestors
        # and mount distribution remain owner-controlled. No mutable child mounts it.
        _owner_path(self.state_directory.parent, directory=True)
        state = self.state_directory.lstat()
        if (not stat.S_ISDIR(state.st_mode) or state.st_uid != UID or state.st_gid != GID
                or stat.S_IMODE(state.st_mode) != 0o700):
            raise PermissionError("private state mount must belong to protected container identity")
        installed = load_owner_runtime_manifest_document(self.owner_directory / "runtime-manifest.json")
        if installed != asdict(self.runtime_manifest):
            raise PermissionError("distributed owner manifest does not match approved release")
        if self.funded_profile is not None:
            from trade_graph.kernel.funded_profile import load_funded_credentials, load_funded_profile

            if load_funded_profile(self.owner_directory) != self.funded_profile:
                raise PermissionError("distributed funded-paper profile differs from owner pin")
            raw = read_owner_file(self.owner_directory, "paper-config.json", 262144)
            if hashlib.sha256(raw).hexdigest() != self.funded_profile.paper_config_sha256:
                raise PermissionError("distributed funded-paper configuration differs from owner pin")
            load_funded_credentials(self.owner_directory, self.funded_profile)
        if self.live_profile is not None:
            from trade_graph.kernel.live_network import load_live_network_profile
            from trade_graph.live_runtime import LiveRuntimeConfig, live_configuration_digest

            if load_live_network_profile(self.owner_directory) != self.live_profile:
                raise PermissionError("distributed live network profile differs from owner pin")
            configured = LiveRuntimeConfig.model_validate_json(
                read_owner_file(self.owner_directory, "live-config.json", 262144),
            )
            if live_configuration_digest(configured) != self.live_profile.live_config_sha256:
                raise PermissionError("distributed live configuration differs from reviewed network pin")

    def create_arguments(self, *, name: str, action: str = "boot") -> list[str]:
        if type(name) is not str or not re.fullmatch(r"trade-graph-[a-z0-9-]{1,64}", name):
            raise ValueError("bounded deployment container name required")
        if action not in {"boot", "probe", "check-boot", "boot-funded", "check-funded",
                          "boot-live", "check-live", "boot-live-dashboard"}:
            raise ValueError("deployment command must be a fixed protected entrypoint")
        funded = action in {"boot-funded", "check-funded"}
        if funded != (self.funded_profile is not None):
            raise ValueError("funded-paper launch requires an explicit separately pinned profile")
        live = action in {"boot-live", "check-live", "boot-live-dashboard"}
        if live != (self.live_profile is not None):
            raise ValueError("live launch requires an explicit separately pinned network profile")
        dashboard = action == "boot-live-dashboard"
        if dashboard and self.live_profile.dashboard_host_port is None:
            raise ValueError("live dashboard publication requires its explicit owner profile pin")
        publication = ["--publish", f"127.0.0.1:{self.live_profile.dashboard_host_port}:8000/tcp"] if dashboard else []
        for path in (self.owner_directory, self.state_directory):
            if not path.is_absolute() or ".." in path.parts or "," in str(path) or "\n" in str(path):
                raise ValueError("unambiguous absolute mount paths required")
        environment = [argument for name in PROXY_VARIABLES for argument in ("--env", name + "=")]
        profile = self.live_profile if live else self.funded_profile
        network = profile.network_id if profile is not None else "none"
        return ["docker", "create", "--name", name, "--platform", PLATFORM, *environment,
                "--user", f"{UID}:{GID}", "--read-only", "--network", network, *publication, "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", "64", "--memory", "512m",
                "--memory-swap", "512m", "--cpus", "1", "--ulimit", "core=0:0", "--init",
                "--tmpfs", f"/tmp:{TMPFS}", "--mount",
                f"type=bind,src={self.owner_directory},dst={OWNER_MOUNT},readonly,bind-propagation=rprivate",
                "--mount", f"type=bind,src={self.state_directory},dst={STATE_MOUNT},bind-propagation=rprivate",
                self.image.image_id, action]


def load_owner_runtime_manifest_document(path: Path) -> dict:
    """Host-side manifest reader; host Python/package need not match the image."""
    _owner_path(path, directory=False)
    if path.stat().st_size > 32768:
        raise ValueError("owner manifest exceeds file bound")
    document = json.loads(path.read_bytes(), object_pairs_hook=_unique_fields)
    if type(document) is not dict or set(document) != {item.name for item in fields(ProtectedRuntimeManifest)}:
        raise ValueError("complete protected manifest required")
    for name in ("approved_source_sha256", "operations"):
        if type(document[name]) is not list:
            raise ValueError("protected manifest array required")
        document[name] = tuple(document[name])
    return asdict(ProtectedRuntimeManifest(**document))


def verify_container_inspection(spec: ProtectedDeploymentSpec, image: dict, container: dict,
                                daemon_security_options: list[str], *, action: str = "boot",
                                network: dict | None = None, proxy: dict | None = None) -> None:
    """Independently inspect the CREATED container before allowing it to start."""
    verify_image_inspection(spec.image, image)
    host, config = container.get("HostConfig", {}), container.get("Config", {})
    funded = action in {"boot-funded", "check-funded"}
    if funded != (spec.funded_profile is not None):
        raise PermissionError("funded-paper container requires exact separate profile")
    live = action in {"boot-live", "check-live", "boot-live-dashboard"}
    if live != (spec.live_profile is not None):
        raise PermissionError("live container requires exact separate network profile")
    dashboard = action == "boot-live-dashboard"
    if dashboard and spec.live_profile.dashboard_host_port is None:
        raise PermissionError("live dashboard publication is not explicitly approved")
    expected_ports = ({"8000/tcp": [{"HostIp": "127.0.0.1",
                                      "HostPort": str(spec.live_profile.dashboard_host_port)}]} if dashboard else {})
    expected_exposed = {"8000/tcp": {}} if dashboard else image["Config"].get("ExposedPorts", {})
    profile = spec.live_profile if live else spec.funded_profile
    expected_network = profile.network_id if profile is not None else "none"
    if funded:
        from trade_graph.kernel.funded_profile import verify_funded_network

        if network is None or proxy is None:
            raise PermissionError("funded-paper launch requires independently inspected network/proxy")
        verify_funded_network(spec.funded_profile, network, proxy, daemon_security_options=daemon_security_options)
    if live:
        from trade_graph.kernel.live_network import verify_live_network

        if network is None or proxy is None:
            raise PermissionError("live launch requires independently inspected private network/proxy")
        verify_live_network(spec.live_profile, network, proxy, daemon_security_options=daemon_security_options)
    attached = container.get("NetworkSettings", {}).get("Networks")
    network_name = network.get("Name") if funded or live else "none"
    if (type(network_name) is not str or not network_name or type(attached) is not dict
            or set(attached) != {network_name}):
        raise PermissionError("protected container has an extra or substituted network attachment")
    attachment = attached[network_name]
    if (type(attachment) is not dict or attachment.get("NetworkID") not in {"", expected_network}
            or attachment.get("IPAMConfig") or attachment.get("Links") or attachment.get("Aliases")
            or attachment.get("DriverOpts") or attachment.get("GwPriority", 0) != 0
            or attachment.get("IPAddress") or attachment.get("Gateway") or attachment.get("MacAddress")
            or attachment.get("EndpointID") or attachment.get("GlobalIPv6Address")
            or attachment.get("IPv6Gateway")):
        raise PermissionError("created protected container network configuration is not the exact approved profile")
    expected_environment = {**_environment(image["Config"].get("Env")),
                            **{name: "" for name in PROXY_VARIABLES}}
    if (action not in {"boot", "probe", "check-boot", "boot-funded", "check-funded",
                       "boot-live", "check-live", "boot-live-dashboard"}
            or container.get("Image") != spec.image.image_id
            or container.get("State", {}).get("Running") is not False
            or container.get("State", {}).get("Status") != "created"
            or config.get("Image") != spec.image.image_id or config.get("User") != f"{UID}:{GID}"
            or config.get("Entrypoint") != ENTRYPOINT or config.get("Cmd") != [action]
            or _environment(config.get("Env")) != expected_environment or config.get("Volumes")
            or config.get("WorkingDir") != STATE_MOUNT
            or config.get("Healthcheck") != image["Config"].get("Healthcheck")
            or host.get("ReadonlyRootfs") is not True or host.get("Privileged") is not False
            or host.get("NetworkMode") != expected_network or host.get("CapDrop") != ["ALL"] or host.get("CapAdd")
            or host.get("SecurityOpt") != ["no-new-privileges"]
            or not any(item.startswith("name=seccomp,profile=builtin") for item in daemon_security_options)
            or host.get("PidMode") or host.get("IpcMode") not in {"private", ""} or host.get("UTSMode")
            or host.get("UsernsMode") or host.get("CgroupnsMode") != "private"
            or host.get("PidsLimit") != 64 or host.get("Memory") != 536870912
            or host.get("MemorySwap") != 536870912 or host.get("NanoCpus") != 1000000000
            or host.get("Tmpfs") != {"/tmp": TMPFS} or host.get("Init") is not True
            or host.get("Devices") or host.get("DeviceRequests") or host.get("ExtraHosts")
            or host.get("DeviceCgroupRules") or host.get("GroupAdd") or host.get("Runtime") != "runc"
            or host.get("AutoRemove") is not False or host.get("OomKillDisable") is not False
            or host.get("PublishAllPorts") is not False
            or (host.get("PortBindings") or {}) != expected_ports
            or (config.get("ExposedPorts") or {}) != expected_exposed
            or host.get("Links") or host.get("VolumesFrom")
            or host.get("Sysctls") or host.get("MaskedPaths") != image_default_masked_paths()
            or host.get("ReadonlyPaths") != image_default_readonly_paths()
            or host.get("Ulimits") != [{"Name": "core", "Hard": 0, "Soft": 0}]):
        raise PermissionError("protected container resource/capability/egress configuration mismatch")
    mounts = container.get("Mounts", [])
    expected = {OWNER_MOUNT: (str(spec.owner_directory), False), STATE_MOUNT: (str(spec.state_directory), True)}
    if (len(mounts) != 2 or {item.get("Destination") for item in mounts} != set(expected)
            or any(item.get("Type") != "bind" or item.get("Propagation") != "rprivate"
                   or (item.get("Source"), item.get("RW")) != expected[item["Destination"]] for item in mounts)):
        raise PermissionError("unexpected or mutable protected host mount")


def _environment(values) -> dict[str, str]:
    if type(values) is not list or any(type(value) is not str or "=" not in value for value in values):
        raise PermissionError("exact image environment required")
    result = {}
    for value in values:
        name, content = value.split("=", 1)
        if name in result:
            raise PermissionError("duplicate image environment variable")
        result[name] = content
    return result


def image_default_masked_paths() -> list[str]:
    return ["/proc/asound", "/proc/acpi", "/proc/interrupts", "/proc/kcore", "/proc/keys", "/proc/latency_stats",
            "/proc/timer_list", "/proc/timer_stats", "/proc/sched_debug", "/proc/scsi", "/sys/firmware",
            "/sys/devices/virtual/powercap"]


def image_default_readonly_paths() -> list[str]:
    return ["/proc/bus", "/proc/fs", "/proc/irq", "/proc/sys", "/proc/sysrq-trigger"]


def build_seal(inputs: dict) -> dict:
    distributions = sorted((item.metadata["Name"], item.version) for item in importlib.metadata.distributions())
    return {**inputs, "protected_package_sha256": protected_package_sha256(),
            "installed_distributions": distributions, "installed_distributions_sha256": document_sha256(distributions)}


def assert_boot_environment(*, mountinfo: Path = Path("/proc/self/mountinfo")) -> ProtectedRuntimeManifest:
    if os.getuid() != UID or os.getgid() != GID:
        raise PermissionError("protected image must run under its unprivileged identity")
    mounts = {}
    for line in mountinfo.read_text().splitlines():
        parts = line.split()
        mounts[parts[4]] = set(parts[5].split(","))
    if "ro" not in mounts.get("/", set()) or "ro" not in mounts.get(OWNER_MOUNT, set()):
        raise PermissionError("protected image and owner mount must be read-only")
    state = Path(STATE_MOUNT).stat(follow_symlinks=False)
    if state.st_uid != UID or state.st_gid != GID or stat.S_IMODE(state.st_mode) != 0o700:
        raise PermissionError("protected state volume identity mismatch")
    _owner_path(IMAGE_SEAL, directory=False)
    seal = json.loads(IMAGE_SEAL.read_bytes(), object_pairs_hook=_unique_fields)
    if seal.get("protected_package_sha256") != protected_package_sha256():
        raise PermissionError("installed protected image seal mismatch")
    return load_owner_runtime_manifest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seal", "boot", "check-boot", "probe", "boot-funded", "check-funded",
                                         "boot-live", "check-live", "boot-live-dashboard"))
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.action == "seal":
        if args.inputs is None or args.output is None:
            parser.error("seal requires exact build inputs and output")
        args.output.write_text(canonical_json(build_seal(json.loads(args.inputs.read_bytes()))) + "\n")
        return 0
    manifest = assert_boot_environment()
    if args.action in {"boot-live", "check-live", "boot-live-dashboard"}:
        from trade_graph.kernel.live_network import load_live_network_profile
        from trade_graph.live_runtime import (
            live_configuration_digest,
            live_startup_prerequisites,
            load_live_runtime_config,
        )

        directory = Path(OWNER_MOUNT)
        profile = load_live_network_profile(directory)
        configured = load_live_runtime_config(directory / "live-config.json")
        if (profile.manifest_sha256 != manifest.sha256 or profile.deployment_id != manifest.deployment_id
                or profile.protected_package_sha256 != manifest.protected_package_sha256
                or profile.live_config_sha256 != live_configuration_digest(configured)):
            raise PermissionError("protected live launch configuration/profile mismatch")
        if args.action == "check-live":
            print(canonical_json(live_startup_prerequisites(Path(STATE_MOUNT) / "trade_graph.sqlite",
                                                           protected_owner=directory)))
            return 0
        from trade_graph.cli import main as cli_main

        if args.action == "boot-live-dashboard":
            if profile.dashboard_host_port is None:
                raise PermissionError("live dashboard publication is not explicitly approved")
            return cli_main(["dashboard", "--mode", "live", "--database", f"{STATE_MOUNT}/trade_graph.sqlite",
                             "--protected-owner", OWNER_MOUNT, "--session-file", f"{STATE_MOUNT}/owner-session.json",
                             "--protected-network-bind", "--port", "8000"])
        return cli_main(["run", "--mode", "live", "--database", f"{STATE_MOUNT}/trade_graph.sqlite",
                         "--protected-owner", OWNER_MOUNT])
    if args.action in {"boot-funded", "check-funded"}:
        from trade_graph.kernel.funded_profile import load_funded_credentials, load_funded_profile
        from trade_graph.paper_runtime import PaperRuntimeConfig

        profile = load_funded_profile(Path(OWNER_MOUNT))
        config = read_owner_file(Path(OWNER_MOUNT), "paper-config.json", 262144)
        configured = PaperRuntimeConfig.model_validate_json(config)
        if (profile.manifest_sha256 != manifest.sha256 or profile.deployment_id != manifest.deployment_id
                or hashlib.sha256(config).hexdigest() != profile.paper_config_sha256
                or configured.public_data_enabled):
            raise PermissionError("protected funded-paper configuration/profile mismatch")
        load_funded_credentials(Path(OWNER_MOUNT), profile)
        if args.action == "check-funded":
            print(canonical_json({"status": "funded_paper_profile_prepared", "profile_sha256": profile.sha256,
                                  "intended_host_verified": False, "live_authorization": False,
                                  "paid_authorization": False, "public_feed_profile_supported": False}))
            return 0
    else:
        try:
            read_owner_file(Path(OWNER_MOUNT), "funded-paper-profile.json", 32768)
        except FileNotFoundError:
            pass
        else:
            raise PermissionError("funded-paper owner bundle requires its separate launch profile")
    if args.action == "check-boot":
        print(canonical_json({"status": "protected_boot_verified", "manifest_sha256": manifest.sha256,
                              "live_authorization": False, "paid_authorization": False}))
        return 0
    if args.action == "probe":
        from trade_graph.kernel.deployment_probe import run_probe

        print(canonical_json(run_probe(manifest)))
        return 0
    from trade_graph.cli import main as cli_main

    return cli_main(["run", "--mode", "paper", "--database", f"{STATE_MOUNT}/trade_graph.sqlite",
                     "--protected-owner", OWNER_MOUNT])


if __name__ == "__main__":
    raise SystemExit(main())
