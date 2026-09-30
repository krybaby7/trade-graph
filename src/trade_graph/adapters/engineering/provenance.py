"""Trusted-runner identity. A caller-supplied label is not this hash."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

CHECKS_FILE = Path(__file__).resolve().parent / "checks.py"
ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL", "SYSTEMROOT")


def checks_module_hash() -> str:
    return hashlib.sha256(CHECKS_FILE.read_bytes()).hexdigest()


def scrubbed_env() -> dict[str, str]:
    """Pass only the interpreter path and locale. Drop keys, homes and PYTHONPATH."""
    env = {key: os.environ[key] for key in ENV_ALLOWLIST if key in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    return env
