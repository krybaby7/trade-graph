"""Bounded pipes and wall-clock enforcement for the owner-pinned checker process."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time

from trade_graph.adapters.engineering.provenance import scrubbed_env
from trade_graph.adapters.engineering.sandbox import OUTPUT_BYTES, WALL_SECONDS


def run_bounded(command: list[str], payload: bytes, *, cwd: str, wall_seconds: float = WALL_SECONDS) -> dict:
    output = {"stdout": bytearray(), "stderr": bytearray()}
    stopped = ""
    with subprocess.Popen(command, cwd=cwd, env=scrubbed_env(), close_fds=True, start_new_session=True,
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
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
    return {"exit_code": process.returncode if not stopped else 1,
            "stdout": bytes(output["stdout"][:OUTPUT_BYTES]).decode("utf-8", errors="replace"),
            "stderr": bytes(output["stderr"][:OUTPUT_BYTES]).decode("utf-8", errors="replace") + stopped}
