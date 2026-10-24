"""Confine the entire mutable departmental reasoning step before input parsing."""

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
        data, size = [], 0
        while True:
            chunk = os.read(0, min(65536, SANDBOX["INPUT_BYTES"] + 1 - size))
            if not chunk:
                break
            data.append(chunk)
            size += len(chunk)
            if size > SANDBOX["INPUT_BYTES"]:
                raise ValueError("bounded departmental input exceeded")
        payload = json.loads(b"".join(data), object_pairs_hook=no_duplicates)
        if type(payload) is not dict or set(payload) != {"source", "context"}:
            raise ValueError("strict departmental payload required")
        namespace = {"__builtins__": __builtins__}
        exec(compile(payload["source"], "<unprivileged-departmental-graph>", "exec"), namespace)
        context = payload["context"]
        proposal = namespace["graph"](context)
        response = {"request_id": context["request_id"], "capability": context["capability"],
                    "operation": context["operation"], "proposal": proposal}
        encoded = json.dumps(response, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > SANDBOX["OUTPUT_BYTES"]:
            raise ValueError("bounded departmental output exceeded")
        os.write(1, encoded)
        return 0
    except BaseException as exc:
        os.write(2, f"departmental process failed closed: {type(exc).__name__}".encode())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
