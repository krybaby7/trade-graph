"""Native Linux subscription CLI boundary. WSL interop is never a sandbox route.

The CLI sees its pinned binary, system libraries, certificates, a fresh home and
the single owner-provisioned official login file. Financial/private host paths,
Windows mounts, host processes and inherited environment are absent. Model tools
are disabled by the official CLI flags as an additional boundary. The credential
file is read by the trusted CLI; application code never opens its contents.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from urllib.parse import urlsplit

from trade_graph.adapters.models.providers import _wire_schema
from trade_graph.adapters.models.subscription import (
    MAX_OUTPUT_BYTES,
    CliOutcome,
    SubscriptionConfig,
    assess_subscription,
    claude_command,
    claude_environment,
    codex_command,
    sanitize_quota,
    subscription_failure_diagnostic,
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
                 environment: dict[str, str] | None = None, proxy_url: str | None = None,
                 provider: str = "claude_subscription", schema_file: Path | None = None,
                 model_catalog_file: Path | None = None, credential_writable: bool = False) -> None:
        self.pin, self.share_network, self.credential_file = pin, share_network, credential_file
        self.provider, self.schema_file, self.model_catalog_file = provider, schema_file, model_catalog_file
        self.credential_writable = credential_writable
        if provider not in {"claude_subscription", "codex_subscription"}:
            raise ValueError("unsupported subscription boundary provider")
        self.proxy_url = validate_proxy_url(proxy_url) if proxy_url is not None else None
        if self.proxy_url is not None and not share_network:
            raise ValueError("offline subscription boundary cannot enable a proxy")
        self.environment = dict(environment or {})
        allowed_environment = (set(claude_environment(1)) if provider == "claude_subscription"
                               else {"CODEX_DISABLE_UPDATE_CHECK"})
        if set(self.environment) - allowed_environment:
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
        if self.provider == "codex_subscription":
            command += ["--dir", "/home/runner/.codex", "--setenv", "CODEX_HOME", "/home/runner/.codex"]
        if self.schema_file is not None:
            schema = self.schema_file
            if schema.is_symlink() or not schema.is_file() or schema.stat().st_mode & 0o077:
                raise ValueError("private controller schema file required")
            command += ["--dir", "/request", "--ro-bind", str(schema.resolve()), "/request/schema.json"]
        if self.model_catalog_file is not None:
            catalog = self.model_catalog_file
            if catalog.is_symlink() or not catalog.is_file() or catalog.stat().st_mode & 0o022:
                raise ValueError("non-writable controller model catalog required")
            command += ["--dir", "/request", "--ro-bind", str(catalog.resolve()), "/request/model-catalog.json"]
        if self.share_network:
            for path in ("/etc/ssl/certs", "/etc/resolv.conf", "/etc/nsswitch.conf", "/etc/hosts"):
                if Path(path).exists():
                    command += ["--ro-bind", path, path]
        if self.credential_file is not None:
            credential = self.credential_file
            if (credential.is_symlink() or not credential.is_file()
                    or credential.name != ("auth.json" if self.provider == "codex_subscription"
                                            else ".credentials.json")
                    or str(credential.resolve()).startswith("/mnt/")):
                raise ValueError("one official native subscription login file is required; symlinks refused")
            metadata = credential.stat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
                raise ValueError("official subscription login file must have private permissions")
            destination = ("/home/runner/.codex/auth.json" if self.provider == "codex_subscription"
                           else "/home/runner/.claude/.credentials.json")
            if self.credential_writable:
                directory = credential.parent
                metadata_directory = directory.stat()
                if (directory.is_symlink() or metadata_directory.st_mode & 0o077
                        or metadata_directory.st_uid != os.geteuid()):
                    raise ValueError("refreshable official auth cache requires a dedicated private directory")
                command += ["--bind", str(directory.resolve()), str(Path(destination).parent)]
            else:
                command += ["--ro-bind", str(credential.resolve()), destination]
        for key, value in self.environment.items():
            command += ["--setenv", key, value]
        if self.proxy_url is not None:
            command += ["--setenv", "HTTPS_PROXY", self.proxy_url]
        return [*command, "/cli/runner", *arguments]

    def run(self, arguments: list[str], payload: bytes, *, maximum_seconds: float,
            cancel_event: Event | None = None, maximum_output_bytes: int = MAX_OUTPUT_BYTES,
            metadata: bool = False) -> CliOutcome:
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
                                if sum(map(len, output.values())) > maximum_output_bytes:
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
        # Raw stderr and intermediate transcripts are never retained. Failure
        # diagnostics contain only fixed codes/categories and a verified public
        # output-schema path, not provider messages, URLs or identifiers.
        stderr = output["stderr"].decode("utf-8", errors="replace")
        category, code, path = "", "", ""
        if process.returncode and self.provider == "codex_subscription":
            try:
                schema = json.loads(self.schema_file.read_text()) if self.schema_file else {}
            except (OSError, ValueError):
                schema = {}
            category, code, path = subscription_failure_diagnostic(
                output["stdout"].decode("utf-8", errors="replace"), stderr, schema=schema)
        quota_words = ("rate_limit", "rate limit", "usage limit", "quota exhausted", "hit your limit")
        if category != "invalid_schema" and process.returncode and any(word in stderr.lower() for word in quota_words):
            category = "quota"
        raw = output["stdout"] + output["stderr"] if metadata else output["stdout"]
        return CliOutcome(bytes(raw[:maximum_output_bytes]).decode("utf-8", errors="replace"),
                          process.returncode, stopped, category, process_terminated=True,
                          error_code=code, schema_path=path)


def validate_proxy_url(value: str) -> str:
    """No ambient proxy, credentials, DNS, loopback or metadata destinations."""
    try:
        parsed = urlsplit(value)
        address = ipaddress.IPv4Address(parsed.hostname or "")
        if (parsed.scheme != "http" or parsed.username or parsed.password or parsed.path
                or parsed.query or parsed.fragment or parsed.port is None or not 1024 <= parsed.port <= 65535
                or not address.is_private or address.is_loopback or address.is_link_local
                or address.is_multicast or address.is_unspecified
                or value != f"http://{address}:{parsed.port}"):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValueError("exact protected private subscription proxy required") from None
    return value


class LinuxSubscriptionExecutor:
    def __init__(self, pin: NativeCliPin, credential_file: Path, *, proxy_url: str,
                 config: SubscriptionConfig | None = None, model_catalog_file: Path | None = None,
                 credential_writable: bool = False) -> None:
        if not pin.require_root_owner:
            raise ValueError("production subscription executor requires a protected root-owned CLI pin")
        pin.verify()
        self.pin, self.credential_file = pin, credential_file
        selected = config or SubscriptionConfig(provider="claude_subscription", model="unselected")
        self.config = selected.model_copy(deep=True)
        self.model_catalog_file = model_catalog_file
        self.credential_writable = credential_writable
        if self.config.provider == "codex_subscription" and model_catalog_file is None:
            raise ValueError("Codex requires a protected model catalog removing filesystem-capable tools")
        self.proxy_url = validate_proxy_url(proxy_url)

    def execute(self, request: ModelRequest, *, cancel_event: Event | None = None) -> CliOutcome:
        if self.config.provider == "codex_subscription":
            with tempfile.TemporaryDirectory(prefix="trade-graph-schema-") as directory:
                schema = Path(directory) / "schema.json"
                schema.write_text(json.dumps(_wire_schema(request.output_schema, provider="openai")), encoding="utf-8")
                schema.chmod(0o600)
                boundary = LinuxFilesystemBoundary(self.pin, share_network=True, credential_file=self.credential_file,
                    proxy_url=self.proxy_url, provider=self.config.provider, schema_file=schema,
                    model_catalog_file=self.model_catalog_file, credential_writable=self.credential_writable)
                payload = (request.instructions + "\nPublic departmental context:\n" +
                           json.dumps(request.context, allow_nan=False)).encode()
                return boundary.run(codex_command("/cli/runner", request)[1:], payload,
                    maximum_seconds=request.timeout_seconds,
                            maximum_output_bytes=self.config.maximum_output_bytes,
                    cancel_event=cancel_event)
        boundary = LinuxFilesystemBoundary(self.pin, share_network=True, credential_file=self.credential_file,
                                            environment=claude_environment(request.max_output_tokens, self.config),
                                            proxy_url=self.proxy_url, credential_writable=self.credential_writable)
        arguments = claude_command("/cli/runner", request, self.config)[1:]
        return boundary.run(arguments, json.dumps(request.context, allow_nan=False).encode(),
                            maximum_seconds=request.timeout_seconds,
                            maximum_output_bytes=self.config.maximum_output_bytes,
                            cancel_event=cancel_event)


def _metadata_command(command: list[str], **kwargs) -> CliOutcome:
    try:
        # Status commands never start threads/turns. Do not inherit any API keys.
        outcome = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False,
                                 env={"HOME": str(Path.home()), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, **kwargs)
        return CliOutcome((outcome.stdout + outcome.stderr)[:16_384], outcome.returncode)
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


def probe_isolated_codex(config: SubscriptionConfig, pin: NativeCliPin, credential_file: Path, *,
                         extra_usage_disabled: bool = False, quota: dict | None = None,
                         isolation_verified: bool = False, proxy_url: str | None = None,
                         credential_writable: bool = False) -> dict:
    """Official version/login metadata inside the same opaque native auth mount."""
    if config.provider != "codex_subscription" or not pin.require_root_owner:
        raise ValueError("isolated Codex preflight requires a protected native Codex pin")
    boundary = LinuxFilesystemBoundary(pin, share_network=False, credential_file=credential_file,
                                        provider="codex_subscription", credential_writable=credential_writable)
    version_result = boundary.run(["--version"], b"", maximum_seconds=10, metadata=True)
    match = re.search(r"\b\d+\.\d+\.\d+\b", version_result.stdout)
    version = match[0] if match and version_result.exit_code == 0 else "unavailable"
    auth = boundary.run(["-c", 'forced_login_method="chatgpt"', "login", "status"],
                        b"", maximum_seconds=10, metadata=True)
    authentication = "chatgpt" if auth.exit_code == 0 and "Logged in using ChatGPT" in auth.stdout else "none"
    fresh_quota = {}
    if authentication == "chatgpt" and proxy_url is not None:
        network_boundary = LinuxFilesystemBoundary(pin, share_network=True, credential_file=credential_file,
            provider="codex_subscription", proxy_url=proxy_url, credential_writable=credential_writable)
        fresh_quota = probe_codex_account_quota(network_boundary)
    status = assess_subscription(config, cli_version=version, authentication=authentication,
        quota=fresh_quota or quota or {},
        extra_usage_disabled=extra_usage_disabled, isolation_ready=isolation_verified,
        native_linux=True).public_status()
    status["native_cli_present"] = True
    status["inference_attempts"] = 0
    status["credential_mount_checked"] = True
    return status


def codex_quota_metadata(result: dict) -> dict:
    """Preserve all reported bucket/window readings without account or bucket identifiers."""
    if not isinstance(result, dict):
        return {}
    legacy = result.get("rateLimits")
    by_limit = result.get("rateLimitsByLimitId")
    if by_limit is not None and not isinstance(by_limit, dict):
        return {"metadata_error": True}
    if isinstance(by_limit, dict) and by_limit:
        if len(by_limit) > 32 or any(value is not None and not isinstance(value, dict)
                                     for value in by_limit.values()):
            return {"metadata_error": True}
        buckets = [value if value is not None else {} for value in by_limit.values()]
        prefix = True
    elif isinstance(legacy, dict):
        buckets, prefix = [legacy], False
    else:
        return {}
    quota = {"source": "codex-app-server", "observed_at": datetime.now(UTC).isoformat(),
             "windows": {}, "unavailable_windows": []}
    ordinary = result.get("ordinaryUsageAllowed")
    if type(ordinary) is bool:
        quota["ordinary_usage_allowed"] = ordinary
    credits = legacy.get("credits") if isinstance(legacy, dict) else None
    if isinstance(credits, dict) and isinstance(credits.get("balance"), str):
        quota["credits_balance"] = credits["balance"]
    for index, limits in enumerate(buckets, 1):
        credits = limits.get("credits")
        if isinstance(credits, dict) and isinstance(credits.get("balance"), str):
            if "credits_balance" in quota and credits["balance"] != quota["credits_balance"]:
                quota["metadata_error"] = True
            else:
                quota["credits_balance"] = credits["balance"]
        for key in ("primary", "secondary"):
            name = f"limit_{index}_{key}" if prefix else key
            window = limits.get(key)
            if window is None:
                quota["windows"][name] = None
                quota["unavailable_windows"].append(name)
                continue
            if not isinstance(window, dict):
                quota["metadata_error"] = True
                continue
            used, duration = window.get("usedPercent"), window.get("windowDurationMins")
            if (type(used) not in (int, float) or not math.isfinite(used) or not 0 <= used <= 100
                    or type(duration) is not int or not 1 <= duration <= 525600):
                quota["metadata_error"] = True
                continue
            reading = {"used_percent": used, "remaining_percent": 100 - used,
                       "window_duration_mins": duration, "resets_at": window.get("resetsAt")}
            quota["windows"][name] = reading
            alias = "weekly" if duration == 10080 else "five_hour" if duration == 300 else None
            if alias and alias not in quota:
                quota[alias] = reading
    return sanitize_quota(quota)


def probe_codex_account_quota(boundary: LinuxFilesystemBoundary, *, maximum_seconds: float = 20) -> dict:
    """Read official app-server account/rate-limit metadata; never issue a thread or turn method.

    JSON-RPC must remain interactive until the explicit response IDs arrive.
    Sending all messages then EOF drops asynchronous provider responses.
    """
    command = boundary.command(["-c", 'forced_login_method="chatgpt"', "-c", "analytics.enabled=false",
        "-c", "feedback.enabled=false", "-c", "features.apps=false", "app-server", "--listen", "stdio://"])
    requests = [
        {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "trade-graph", "version": "1"},
                                                     "capabilities": {}}},
        {"id": 2, "method": "account/read", "params": {"refreshToken": False}},
        {"id": 3, "method": "account/rateLimits/read", "params": {}},
    ]
    quota, total, pending, buffer = {}, 0, 0, bytearray()
    process = None
    try:
        process = subprocess.Popen(command, cwd="/", env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                close_fds=True, start_new_session=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE)
        def send(document):
            process.stdin.write(json.dumps(document).encode() + b"\n")
            process.stdin.flush()
        send(requests[0])
        deadline = time.monotonic() + maximum_seconds
        with selectors.DefaultSelector() as selector:
            for stream in (process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, stream == process.stdout)
            while pending < len(requests) and selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fd, 8192)
                    total += len(chunk)
                    if total > 262144:
                        raise ValueError("subscription quota metadata bound exceeded")
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if not key.data:
                        continue  # Never retain raw CLI stderr/account identifiers.
                    buffer.extend(chunk)
                    while b"\n" in buffer:
                        if pending >= len(requests):
                            break
                        line, _, tail = buffer.partition(b"\n")
                        buffer = bytearray(tail)
                        event = json.loads(line)
                        if not isinstance(event, dict) or event.get("id") != requests[pending]["id"]:
                            continue
                        if "error" in event:
                            raise ValueError("subscription quota metadata refused")
                        if pending == 1:
                            account = event.get("result", {}).get("account", {})
                            if not isinstance(account, dict) or account.get("type") != "chatgpt":
                                raise ValueError("official ChatGPT account required")
                        if pending == 2:
                            quota = codex_quota_metadata(event.get("result", {}))
                        pending += 1
                        if pending == 1:
                            send({"method": "initialized"})
                        if pending < len(requests):
                            send(requests[pending])
        process.stdin.close()
        if process.poll() is None and pending < len(requests):
            os.killpg(process.pid, signal.SIGKILL)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
        return quota if pending == len(requests) else {}
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        return {}
    finally:
        if process is not None:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
