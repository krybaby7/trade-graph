"""Protocol fixtures only; no OS sandbox or trusted promotion authority exists yet.

An AST denylist and a same-UID child process are not security isolation. Broader
executable plugins stay disabled; the R1 allowlisted data-artifact path is separate.
"""

from __future__ import annotations

import ast
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
    raise PermissionError("executable plugins disabled: independently enforced OS sandbox is not implemented")


def promote_staged(candidate: dict, *, controller_healthy: bool) -> dict:
    raise RuntimeError("staged code promotion disabled: caller-supplied attestation is not trusted evidence")
