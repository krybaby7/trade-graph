"""Independent checker: trusted code, bounded data via stdin, zero filesystem authority."""

from __future__ import annotations

import hashlib
import json
import os
import runpy
from pathlib import Path

# Absolute trusted siblings are loaded before confinement, never from a worktree,
# PYTHONPATH, a model path or an editable candidate test suite. -I -S -B is mandatory.
HERE = Path(__file__).resolve().parent
SANDBOX = runpy.run_path(str(HERE / "sandbox.py"))
POLICY = runpy.run_path(str(HERE / "artifact_policy.py"))


def main() -> int:
    try:
        isolation = SANDBOX["confine"]()
        chunks, size = [], 0
        while True:
            chunk = os.read(0, min(65536, SANDBOX["INPUT_BYTES"] + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > SANDBOX["INPUT_BYTES"]:
                raise ValueError("checker input limit exceeded")
        raw = b"".join(chunks)
        payload = json.loads(raw)
        files = payload["files"]
        if (set(payload) != {"files"} or not isinstance(files, dict) or not 1 <= len(files) <= 32
                or any(type(k) is not str or type(v) is not str for k, v in files.items())
                or sum(len(v.encode()) for v in files.values()) > 262144):
            raise ValueError("bounded artifact snapshot required")
        failures = POLICY["validate"](files)
        result = {"isolation": isolation, "input_sha256": hashlib.sha256(raw).hexdigest(),
                  "failures": failures, "passed": not failures}
        encoded = json.dumps(result, sort_keys=True, ensure_ascii=False).encode()
        if len(encoded) > SANDBOX["OUTPUT_BYTES"]:
            raise ValueError("checker findings limit exceeded")
        os.write(1, encoded)
        return 1 if failures else 0
    except Exception as exc:
        os.write(2, f"checker failed closed: {type(exc).__name__}: {str(exc)[:500]}".encode())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
