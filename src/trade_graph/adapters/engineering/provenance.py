"""Trusted-runner identity. A caller-supplied label is not this hash."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

CHECKS_FILE = Path(__file__).resolve().parent / "checks.py"
ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL", "SYSTEMROOT")


def checks_module_hash() -> str:
    digest = hashlib.sha256()
    for name in ("checks.py", "sandbox.py", "artifact_policy.py", "artifact_files.py", "process.py", "runner.py"):
        digest.update(name.encode())
        digest.update((CHECKS_FILE.parent / name).read_bytes())
    return digest.hexdigest()


def scrubbed_env() -> dict[str, str]:
    """Pass only the interpreter path and locale. Drop keys, homes and PYTHONPATH."""
    env = {"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    return env
