"""Trusted copied runtime entry point: confinement precedes candidate parsing."""

from __future__ import annotations

import ctypes
import decimal
import json
import os
import resource
import runpy
import socket
import time
from pathlib import Path

SANDBOX = runpy.run_path(str(Path(__file__).with_name("sandbox.py")))
PRELOADED = (ctypes, decimal, resource, socket, time)


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate plugin input field")
        result[key] = value
    return result


def main() -> int:
    try:
        SANDBOX["confine"]()
        chunks, size = [], 0
        while True:
            chunk = os.read(0, min(65536, SANDBOX["INPUT_BYTES"] + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > SANDBOX["INPUT_BYTES"]:
                raise ValueError("plugin input byte bound exceeded")
        payload = json.loads(b"".join(chunks), object_pairs_hook=unique_pairs)
        if type(payload) is not dict or set(payload) != {"source", "snapshot"} or type(payload["source"]) is not str:
            raise ValueError("bounded source and numeric snapshot required")
        namespace = {"__builtins__": __builtins__}
        exec(compile(payload["source"], "<sealed-plugin>", "exec"), namespace)
        features = namespace["propose"](payload["snapshot"])
        message = {"operation": "propose_features", "snapshot_id": payload["snapshot"]["snapshot_id"],
                   "features": features}
        encoded = json.dumps(message, allow_nan=False, separators=(",", ":")).encode()
        if len(encoded) > SANDBOX["OUTPUT_BYTES"]:
            raise ValueError("plugin output byte bound exceeded")
        os.write(1, encoded)
        return 0
    except BaseException as exc:
        os.write(2, f"sealed plugin rejected: {type(exc).__name__}: {str(exc)[:200]}".encode())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
