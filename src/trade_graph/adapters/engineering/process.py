"""Bounded pipes and deadlines for owner-pinned checker and confined prototype processes."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time

from trade_graph.adapters.engineering.provenance import scrubbed_env
from trade_graph.adapters.engineering.sandbox import CHILD_ENV, OUTPUT_BYTES, UNCONFINED_ENV, WALL_SECONDS


def run_bounded(command: list[str], payload: bytes, *, cwd: str, wall_seconds: float = WALL_SECONDS,
                extra_env: dict[str, str] | None = None) -> dict:
    environment = {**scrubbed_env(), CHILD_ENV: "1"}  # marks a bounded child; confine() trusts the switch only here
    if extra_env:
        # The scrubbed environment admits exactly one addition: the checker's development switch.
        if set(extra_env) - {UNCONFINED_ENV}:
            raise ValueError("only the checker's development switch may join the scrubbed environment")
        environment.update(extra_env)
    output = {"stdout": bytearray(), "stderr": bytearray()}
    stopped = ""
    with subprocess.Popen(command, cwd=cwd, env=environment, close_fds=True, start_new_session=True,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        deadline, sent = time.monotonic() + wall_seconds, 0
        with selectors.DefaultSelector() as selector:
            for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, "input")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    stopped = "checker wall-clock limit exceeded"
                    break
                for key, _ in selector.select(min(remaining, 0.1)):
                    if key.data == "input":
                        try:
                            sent += os.write(key.fd, payload[sent:sent + 65536])
                        except BrokenPipeError:
                            sent = len(payload)
                        if sent == len(payload):
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                    else:
                        data = os.read(key.fd, 8192)
                        if not data:
                            selector.unregister(key.fileobj)
                            continue
                        output[key.data].extend(data)
                        if sum(len(value) for value in output.values()) > OUTPUT_BYTES:
                            stopped = "checker output limit exceeded"
                            break
                if stopped:
                    break
            if stopped:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=3)
            else:
                # Hostile mutable code can close both output pipes and continue
                # running. Empty selector state is not process completion.
                try:
                    process.wait(timeout=max(0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    stopped = "checker wall-clock limit exceeded"
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=3)
    return {"exit_code": process.returncode if not stopped else 1,
            "stdout": bytes(output["stdout"][:OUTPUT_BYTES]).decode("utf-8", errors="replace"),
            "stderr": bytes(output["stderr"][:OUTPUT_BYTES]).decode("utf-8", errors="replace") + stopped}
