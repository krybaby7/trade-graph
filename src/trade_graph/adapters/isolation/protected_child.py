"""Fresh-exec worker for a bounded decision proposal, confined before input."""

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

SANDBOX = runpy.run_path(str(Path(__file__).resolve().parents[1] / "engineering" / "sandbox.py"))
PRELOADED = (ctypes, decimal, resource, socket, time)


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
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
                raise ValueError("mutable input bound exceeded")
        payload = json.loads(b"".join(chunks), object_pairs_hook=no_duplicates)
        if type(payload) is not dict or set(payload) != {"source", "context"}:
            raise ValueError("strict protected worker payload required")
        namespace = {"__builtins__": __builtins__}
        exec(compile(payload["source"], "<unprivileged-decision-release>", "exec"), namespace)
        context = payload["context"]
        decision = namespace["propose"](context)
        response = {"operation": "submit_decision", "request_id": context["request_id"],
                    "capability": context["capability"], "decision": decision}
        encoded = json.dumps(response, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > SANDBOX["OUTPUT_BYTES"]:
            raise ValueError("mutable output bound exceeded")
        os.write(1, encoded)
        return 0
    except BaseException as exc:
        os.write(2, f"mutable process failed closed: {type(exc).__name__}".encode())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
