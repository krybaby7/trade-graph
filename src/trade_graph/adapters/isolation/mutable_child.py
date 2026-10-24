"""Pinned standalone child. Untrusted Python is compiled only after confinement.

The parent launches this with -I -S -B, scrubbed environment and closed FDs.
Everything loaded before confine is trusted standard-library or pinned code.
"""

from __future__ import annotations

import ctypes  # Preload for actual-host adversarial probes; no parent address space is inherited.
import decimal
import json
import os
import resource
import runpy
import socket
import time
from pathlib import Path

SANDBOX = runpy.run_path(str(Path(__file__).resolve().parents[1] / "engineering" / "sandbox.py"))
# Keep these loaded so untrusted tests can exercise syscalls after filesystem access closes.
PRELOADED = (ctypes, decimal, resource, socket, time)


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate RPC JSON field")
        result[key] = value
    return result


def main() -> int:
    try:
        SANDBOX["confine"]()  # Irreversible OS boundary precedes every untrusted byte/statement.
        chunks, size = [], 0
        while True:
            chunk = os.read(0, min(65536, SANDBOX["INPUT_BYTES"] + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > SANDBOX["INPUT_BYTES"]:
                raise ValueError("mutable input limit exceeded")
        payload = json.loads(b"".join(chunks), object_pairs_hook=no_duplicate_keys)
        if set(payload) != {"source", "snapshot"} or type(payload["source"]) is not str:
            raise ValueError("bounded source and numeric snapshot required")
        namespace = {"__builtins__": __builtins__}
        exec(compile(payload["source"], "<unprivileged-candidate>", "exec"), namespace)
        features = namespace["propose"](payload["snapshot"])
        reply = {"operation": "propose_features", "snapshot_id": payload["snapshot"]["snapshot_id"],
                 "features": features}
        encoded = json.dumps(reply, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > SANDBOX["OUTPUT_BYTES"]:
            raise ValueError("mutable result limit exceeded")
        os.write(1, encoded)
        return 0
    except BaseException as exc:
        os.write(2, f"mutable process failed closed: {type(exc).__name__}: {str(exc)[:300]}".encode())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
