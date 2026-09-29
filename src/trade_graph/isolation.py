"""Process boundary for the protected kernel and unprivileged plugins."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from multiprocessing.connection import Connection

BANNED_MODULES = {"os", "socket", "subprocess", "pathlib", "shutil", "ctypes"}
BANNED_CALLS = {"open", "exec", "eval", "__import__"}


def kernel_loop(connection: Connection, secret: str) -> None:
    while True:
        message = connection.recv()
        operation = message.get("op")
        if operation == "stop":
            connection.send({"ok": True})
            return
        if operation in {"get_secret", "set_budget", "withdraw", "replace_kernel", "read_env"}:
            connection.send({"ok": False, "error": "denied"})
            continue
        if operation == "pause":
            connection.send({"ok": True, "profile": message.get("profile")})
            continue
        if operation == "activate":
            connection.send({"ok": True, "hash": message.get("hash")})
            continue
        connection.send({"ok": False, "error": "denied"})
    # The secret stays in this process. It is intentionally unused by every reply.


def validate_plugin(source: str) -> None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in BANNED_MODULES:
                    raise PermissionError(alias.name)
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in BANNED_MODULES:
                raise PermissionError(node.module)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in BANNED_CALLS:
                raise PermissionError(node.func.id)


def run_plugin(source: str, snapshot: dict) -> dict:
    validate_plugin(source)
    wrapper = (
        "import json,sys\n"
        "from trade_graph.isolation import validate_plugin\n"
        "payload=json.loads(sys.stdin.read())\n"
        "validate_plugin(payload['source'])\n"
        "namespace={}\n"
        "exec(payload['source'], namespace, namespace)\n"
        "result=namespace['on_snapshot'](payload['snapshot'])\n"
        "json.dump(result, sys.stdout)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", wrapper],
        input=json.dumps({"source": source, "snapshot": snapshot}),
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": os.environ.get("PYTHONPATH", "")},
        preexec_fn=_limit,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr)
    return json.loads(completed.stdout)


def _limit() -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    except (ImportError, ValueError, OSError):
        return


def promote_staged(candidate: dict, *, controller_healthy: bool) -> dict:
    if not controller_healthy:
        raise RuntimeError("controller rollback does not depend on the candidate")
    if candidate.get("attestation", {}).get("runner") != "trusted-controller":
        raise RuntimeError("candidate cannot attest itself")
    return {"promoted": True, "hash": candidate["content_hash"]}
