"""Restricted artifact proxy plus an independent, data-pipe-only OS-confined checker."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from trade_graph.adapters.engineering.artifact_files import (
    content_hash,
    ensure_directory,
    manifest,
    read_tree,
    write_file,
)
from trade_graph.adapters.engineering.artifact_policy import PREFIXES
from trade_graph.adapters.engineering.process import run_bounded
from trade_graph.adapters.engineering.provenance import CHECKS_FILE, checks_module_hash, scrubbed_env
from trade_graph.adapters.engineering.sandbox import (
    CONFINED_MECHANISM,
    INPUT_BYTES,
    UNCONFINED_ENV,
    UNCONFINED_VALUE,
    development_unconfined_requested,
    evidence_acceptable,
)
from trade_graph.kernel.authority import path_is_protected

ALLOWLIST_PREFIXES = PREFIXES


class EngineerRunner:
    def __init__(self, source_root: Path, *, development_unconfined: bool | None = None) -> None:
        self.source_root = source_root
        # Off unless the environment holds the exact opt-in value (sandbox.py). Where seccomp
        # exists the switch changes nothing: the checker confines itself regardless.
        self.development_unconfined = (development_unconfined_requested() if development_unconfined is None
                                       else bool(development_unconfined))

    def stage(self, destination: Path, *, snapshot: dict[str, str] | None = None) -> str:
        source_root = self.source_root.resolve()
        if (destination.is_symlink() or source_root == destination.resolve()
                or source_root.is_relative_to(destination.resolve())):
            raise PermissionError("stage cannot replace or contain the source")
        ensure_directory(destination)
        if any(destination.iterdir()):
            raise PermissionError("stage already contains retained evidence")
        files = read_tree(self.source_root, source=True) if snapshot is None else snapshot
        for name, text in files.items():
            write_file(destination, name, text)
        # No product .git, hooks, credentials, global config, templates or dependencies.
        git = shutil.which("git", path=os.defpath)
        if not git:
            raise RuntimeError("trusted git executable unavailable")
        env = {**scrubbed_env(), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_CONFIG_SYSTEM": os.devnull, "GIT_TERMINAL_PROMPT": "0"}
        prefix = [git, "-c", "core.hooksPath=/dev/null", "-c", "init.templateDir="]
        for args in (["init"], ["add", "."], ["-c", "user.email=engineer@example.invalid", "-c",
                     "user.name=Engineer", "commit", "-m", "baseline", "--allow-empty"]):
            subprocess.run([*prefix, *args], cwd=destination, env=env, check=True, capture_output=True, timeout=5)
        size = sum(path.stat().st_size for path in destination.rglob("*") if path.is_file())
        if size > 2 * 1024 * 1024:
            raise ValueError("staged repository disk limit exceeded")
        return content_hash(files)

    def apply_files(self, root: Path, files: dict[str, str]) -> list[str]:
        if not files:
            raise ValueError("advice without a file is not an artifact")
        changed = []
        for relative, content in files.items():
            normalized = relative
            if Path(normalized).is_absolute() or ".." in Path(normalized).parts:
                raise PermissionError(normalized)
            if path_is_protected(normalized) or not normalized.startswith(ALLOWLIST_PREFIXES):
                raise PermissionError(normalized)
            write_file(root, normalized, content)
            changed.append(normalized)
        read_tree(root)  # Total disk/file/depth bounds after the patch as well as before.
        return changed

    def attest(self, root: Path) -> dict:
        """Only bounded UTF-8 artifact data crosses the checker boundary, never a path/FD."""
        files = read_tree(root)
        tested_hash, tested_manifest, harness_hash = content_hash(files), manifest(files), checks_module_hash()
        payload = json.dumps({"files": files}, sort_keys=True, ensure_ascii=False).encode()
        report = {"command": f"{sys.executable} -I -S -B {CHECKS_FILE} [artifact data on stdin]",
                  "exit_code": 1, "stdout": "", "stderr": "", "content_hash": tested_hash,
                  "manifest": tested_manifest, "artifact_files": files, "checks_module_hash": harness_hash,
                  "isolation": {"verified": False}}
        if len(payload) > INPUT_BYTES:
            report["stderr"] = "checker input resource limit exceeded"
            return report
        extra_env = {UNCONFINED_ENV: UNCONFINED_VALUE} if self.development_unconfined else None
        with tempfile.TemporaryDirectory(prefix="tg-checks-") as neutral:
            report.update(run_bounded([sys.executable, "-I", "-S", "-B", str(CHECKS_FILE)], payload, cwd=neutral,
                                      extra_env=extra_env))
        try:
            result = json.loads(report["stdout"])
            isolation = result["isolation"]
            if (result["input_sha256"] != hashlib.sha256(payload).hexdigest()
                    or not evidence_acceptable(isolation, allow_development=self.development_unconfined)):
                raise ValueError("invalid checker confinement evidence")
            report["isolation"] = {**isolation, "verified": True,
                                   "confined": isolation["mechanism"] == CONFINED_MECHANISM}
            report["failures"] = result["failures"]
            if not result["passed"]:
                report["exit_code"] = 1
        except (ValueError, KeyError, TypeError):
            report["exit_code"] = 1
            report["stderr"] += " missing verified confinement result"
        if manifest(read_tree(root)) != tested_manifest or checks_module_hash() != harness_hash:
            report["exit_code"] = 1
            report["stderr"] += " artifact or trusted harness changed during checks"
        return report

    def hash_tree(self, root: Path) -> str:
        return content_hash(read_tree(root))

    def source_files(self) -> dict[str, str]:
        return read_tree(self.source_root, source=True)
