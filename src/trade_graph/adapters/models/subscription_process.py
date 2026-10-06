"""Native Linux subscription CLI boundary. WSL interop is never a sandbox route.

The CLI sees its pinned binary, system libraries, certificates, a fresh home and
the single owner-provisioned official login file. Financial/private host paths,
Windows mounts, host processes and inherited environment are absent. Model tools
are disabled by the official CLI flags as an additional boundary. The credential
file is read by the trusted CLI; application code never opens its contents.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from trade_graph.adapters.models.subscription import (
    MAX_OUTPUT_BYTES,
    CliOutcome,
    SubscriptionConfig,
    assess_subscription,
    claude_command,
    claude_environment,
)
from trade_graph.contracts.models import ModelRequest


@dataclass(frozen=True)
class NativeCliPin:
    binary: Path
    sha256: str
    require_root_owner: bool = True

    def verify(self) -> Path:
        binary = self.binary.resolve()
        if sys.platform != "linux" or str(binary).startswith("/mnt/") or binary.suffix.lower() == ".exe":
            raise ValueError("native Linux binary required; Windows/WSL interop is refused")
        if not binary.is_file():
            raise ValueError("native Linux CLI binary is unavailable")
        metadata = binary.stat()
        if self.require_root_owner and (metadata.st_uid != 0 or metadata.st_mode & 0o022):
            raise ValueError("CLI binary must be pinned in a root-owned non-writable installation")
        if self.require_root_owner:
            for parent in binary.parents:
                parent_metadata = parent.stat()
                if parent_metadata.st_uid != 0 or parent_metadata.st_mode & 0o022:
                    raise ValueError("CLI parent directories must be root-owned and non-writable")
        with binary.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                raise ValueError("native ELF CLI binary required")
            stream.seek(0)
            actual_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256) or actual_hash != self.sha256:
            raise ValueError("CLI binary hash differs from protected owner pin")
        return binary


class LinuxFilesystemBoundary:
    def __init__(self, pin: NativeCliPin, *, share_network: bool, credential_file: Path | None = None,
                 environment: dict[str, str] | None = None) -> None:
        self.pin, self.share_network, self.credential_file = pin, share_network, credential_file
        self.environment = dict(environment or {})
        if set(self.environment) - set(claude_environment(1)):
            raise ValueError("subscription child environment is outside the fixed allowlist")

    def command(self, arguments: list[str]) -> list[str]:
        binary = self.pin.verify()
        bwrap = shutil.which("bwrap", path="/usr/bin:/bin")
        if bwrap is None:
            raise ValueError("Linux bubblewrap is unavailable; isolation fails closed")
        boundary_metadata = Path(bwrap).stat()
        if boundary_metadata.st_uid != 0 or boundary_metadata.st_mode & 0o022:
            raise ValueError("bubblewrap must be supplied by a protected root-owned installation")
        command = [bwrap, "--unshare-all", "--die-with-parent", "--clearenv", "--cap-drop", "ALL"]
        if self.share_network:
            command.append("--share-net")
        for directory in ("/usr/lib", "/lib", "/lib64"):
            if Path(directory).exists():
                command += ["--ro-bind", directory, directory]
        command += ["--ro-bind", str(binary), "/cli/runner", "--proc", "/proc", "--dev", "/dev",
                    "--tmpfs", "/tmp", "--tmpfs", "/home", "--dir", "/home/runner", "--dir", "/workspace",
                    "--setenv", "HOME", "/home/runner", "--setenv", "PATH", "/nonexistent",
                    "--setenv", "LANG", "C.UTF-8", "--setenv", "CLAUDE_CONFIG_DIR", "/home/runner/.claude",
                    "--dir", "/home/runner/.claude", "--chdir", "/workspace"]
        if self.share_network:
            for path in ("/etc/ssl/certs", "/etc/resolv.conf", "/etc/nsswitch.conf", "/etc/hosts"):
                if Path(path).exists():
                    command += ["--ro-bind", path, path]
        if self.credential_file is not None:
            credential = self.credential_file
            if (credential.is_symlink() or not credential.is_file() or credential.name != ".credentials.json"
                    or str(credential.resolve()).startswith("/mnt/")):
                raise ValueError("one official native Claude login file is required; symlinks refused")
            metadata = credential.stat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
                raise ValueError("official subscription login file must have private permissions")
            command += ["--ro-bind", str(credential.resolve()), "/home/runner/.claude/.credentials.json"]
        for key, value in self.environment.items():
            command += ["--setenv", key, value]
        return [*command, "/cli/runner", *arguments]

    def run(self, arguments: list[str], payload: bytes, *, maximum_seconds: float,
            cancel_event: Event | None = None) -> CliOutcome:
        """Bound both pipes; kill the namespace parent on cancellation/deadline."""
        command = self.command(arguments)
        output = {"stdout": bytearray(), "stderr": bytearray()}
        stopped = ""
        with subprocess.Popen(command, cwd="/", env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                              close_fds=True, start_new_session=True, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            deadline, sent = time.monotonic() + maximum_seconds, 0
            with selectors.DefaultSelector() as selector:
                for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, name)
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE, "input")
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if cancel_event and cancel_event.is_set():
                        stopped = "cancelled"
                    elif remaining <= 0:
                        stopped = "timeout"
                    if stopped:
                        break
                    for key, _ in selector.select(min(remaining, 0.05)):
                        if key.data == "input":
                            try:
                                sent += os.write(key.fd, payload[sent:sent + 8192])
                            except BrokenPipeError:
                                sent = len(payload)
                            if sent == len(payload):
                                selector.unregister(key.fileobj)
                                key.fileobj.close()
                        else:
                            chunk = os.read(key.fd, 8192)
                            if not chunk:
                                selector.unregister(key.fileobj)
                            else:
                                output[key.data].extend(chunk)
                                if sum(map(len, output.values())) > MAX_OUTPUT_BYTES:
                                    stopped = "output_limit"
                                    break
                    if stopped:
                        break
                # Closed output streams do not mean the process exited.
                while not stopped and process.poll() is None:
                    if cancel_event and cancel_event.is_set():
                        stopped = "cancelled"
                    elif time.monotonic() >= deadline:
                        stopped = "timeout"
                    else:
                        time.sleep(0.01)
                if stopped:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait(timeout=3)
        # CLI stderr and intermediate transcripts are intentionally not retained.
        stderr = output["stderr"].decode("utf-8", errors="replace").lower()
        quota_words = ("rate_limit", "rate limit", "usage limit", "quota exhausted", "hit your limit")
        category = "quota" if process.returncode and any(word in stderr for word in quota_words) else ""
        return CliOutcome(bytes(output["stdout"][:MAX_OUTPUT_BYTES]).decode("utf-8", errors="replace"),
                          process.returncode, stopped, category)


class LinuxSubscriptionExecutor:
    def __init__(self, pin: NativeCliPin, credential_file: Path) -> None:
        if not pin.require_root_owner:
            raise ValueError("production subscription executor requires a protected root-owned CLI pin")
        pin.verify()
        self.pin, self.credential_file = pin, credential_file

    def execute(self, request: ModelRequest, *, cancel_event: Event | None = None) -> CliOutcome:
        boundary = LinuxFilesystemBoundary(self.pin, share_network=True, credential_file=self.credential_file,
                                            environment=claude_environment(request.max_output_tokens))
        arguments = claude_command("/cli/runner", request)[1:]
        return boundary.run(arguments, json.dumps(request.context, allow_nan=False).encode(),
                            maximum_seconds=request.timeout_seconds, cancel_event=cancel_event)


def _metadata_command(command: list[str], **kwargs) -> CliOutcome:
    try:
        # Status commands never start threads/turns. Do not inherit any API keys.
        outcome = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False,
                                 env={"HOME": str(Path.home()), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, **kwargs)
        return CliOutcome(outcome.stdout[:16_384], outcome.returncode)
    except (OSError, subprocess.TimeoutExpired):
        return CliOutcome("", 1)


def probe_subscription(config: SubscriptionConfig, *, executable: Path | None = None,
                       run_metadata=None, extra_usage_disabled: bool = False,
                       isolation_ready: bool = False, quota: dict | None = None) -> dict:
    """Read-only CLI metadata. Never reads a token file, starts exec or invokes -p."""
    runner = run_metadata or _metadata_command
    name = "codex" if config.provider == "codex_subscription" else "claude"
    selected = executable or (Path(found) if (found := shutil.which(name)) else None)
    native = bool(selected and sys.platform == "linux" and not str(selected.resolve()).startswith("/mnt/"))
    version, authentication = "unavailable", "none"
    if native:
        try:
            with selected.open("rb") as stream:
                native = stream.read(4) == b"\x7fELF"
        except OSError:
            native = False
    if native:
        raw_version = runner([str(selected), "--version"])
        match = re.search(r"\b\d+\.\d+\.\d+\b", raw_version.stdout)
        if match and raw_version.exit_code == 0:
            version = match[0]
        auth = runner([str(selected), "login", "status"] if name == "codex"
                      else [str(selected), "auth", "status"])
        if name == "codex":
            authentication = "chatgpt" if auth.exit_code == 0 and "Logged in using ChatGPT" in auth.stdout else "none"
        else:
            try:
                status = json.loads(auth.stdout)
                if (auth.exit_code == 0 and status.get("loggedIn") is True and status.get("authMethod") == "claude.ai"
                        and status.get("apiProvider") == "firstParty" and status.get("subscriptionType")):
                    authentication = "subscription"
            except (ValueError, TypeError, AttributeError):
                pass
    status = assess_subscription(config, cli_version=version, authentication=authentication, quota=quota or {},
        extra_usage_disabled=extra_usage_disabled, isolation_ready=isolation_ready, native_linux=native).public_status()
    status["native_cli_present"] = native
    status["inference_attempts"] = 0
    return status


def native_subscription_status(provider: str) -> dict:
    if provider not in {"codex", "claude"}:
        raise ValueError("select exactly one subscription provider: codex or claude")
    config = SubscriptionConfig(provider=f"{provider}_subscription", model="unselected")
    status = probe_subscription(config)
    status["requested_model"] = None
    return status


def probe_isolated_claude(config: SubscriptionConfig, pin: NativeCliPin, credential_file: Path, *,
                         extra_usage_disabled: bool = False, quota: dict | None = None,
                         isolation_verified: bool = False) -> dict:
    """Read version/login inside the exact executor mount, without a model request.

    The caller must separately supply protected host-isolation evidence before
    admission; metadata success alone cannot attest the whole host boundary.
    """
    if config.provider != "claude_subscription" or not pin.require_root_owner:
        raise ValueError("isolated Claude preflight requires a protected native Claude pin")
    boundary = LinuxFilesystemBoundary(pin, share_network=False, credential_file=credential_file,
                                        environment=claude_environment(1))
    version_result = boundary.run(["--version"], b"", maximum_seconds=10)
    match = re.search(r"\b\d+\.\d+\.\d+\b", version_result.stdout)
    version = match[0] if match and version_result.exit_code == 0 else "unavailable"
    auth = boundary.run(["--restricted", "--safe-mode", "--setting-sources", "", "auth", "status"], b"",
                        maximum_seconds=10)
    authentication = "none"
    try:
        metadata = json.loads(auth.stdout)
        if (auth.exit_code == 0 and metadata.get("loggedIn") is True and metadata.get("authMethod") == "claude.ai"
                and metadata.get("apiProvider") == "firstParty" and metadata.get("subscriptionType")):
            authentication = "subscription"
    except (ValueError, TypeError, AttributeError):
        pass
    status = assess_subscription(config, cli_version=version, authentication=authentication, quota=quota or {},
        extra_usage_disabled=extra_usage_disabled, isolation_ready=isolation_verified,
        native_linux=True).public_status()
    status["native_cli_present"] = True
    status["inference_attempts"] = 0
    status["credential_mount_checked"] = True
    return status
