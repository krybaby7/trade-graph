"""Linux x86-64, data-pipe-only checker confinement, NOT a general code sandbox.

The trusted checker loads its code before entering this boundary. Thereafter it
cannot open/stat a file, create a socket/process, execute a program, access another
process, change limits or acquire privilege. Only fd 0 can be read and fds 1/2
written. No candidate code, mounts, production paths or database handles enter it.
Unsupported kernels/architectures fail closed. The one exception is explicit and narrow: a
development machine may set TRADE_GRAPH_UNCONFINED_CHECKER=development, and the two trusted
data-only children (this checker and the ledger replay probe) then run with portable resource
limits only and record "unconfined-development-v1" as their mechanism. Children that execute
candidate code never do. Where seccomp exists the switch changes nothing.
"""

from __future__ import annotations

import ctypes
import errno
import os
import resource
import sys

MEMORY_BYTES = 128 * 1024 * 1024
CPU_SECONDS = 3
WALL_SECONDS = 10
INPUT_BYTES = 524288
OUTPUT_BYTES = 16384
CONFINED_MECHANISM = "linux-x86_64-seccomp-data-pipe-v1"
UNCONFINED_MECHANISM = "unconfined-development-v1"
UNCONFINED_ENV = "TRADE_GRAPH_UNCONFINED_CHECKER"
UNCONFINED_VALUE = "development"
CHILD_ENV = "TRADE_GRAPH_CONFINED_CHILD"  # set by run_bounded on every bounded child, never inherited
_CONFINED_SHAPE = {"no_new_privs": True, "filesystem": "denied", "network": "denied", "process_creation": "denied"}
_UNCONFINED_SHAPE = {"no_new_privs": False, "filesystem": "unrestricted", "network": "unrestricted",
                     "process_creation": "unrestricted"}


def seccomp_available() -> bool:
    """The data-pipe filter below is written for the x86-64 Linux syscall table only."""
    return sys.platform == "linux" and os.uname().machine == "x86_64"


def development_unconfined_requested(environ=None) -> bool:
    """True only for the exact opt-in value; any other spelling leaves the switch off."""
    environ = os.environ if environ is None else environ
    return environ.get(UNCONFINED_ENV) == UNCONFINED_VALUE


def _bounded_child(environ=None) -> bool:
    """True only inside a process that run_bounded started with its scrubbed environment.

    A developer's shell may export the switch; a test or tool that calls confine() in its own
    process must still fail closed rather than close its own descriptors and inherit a
    three-second CPU limit.
    """
    environ = os.environ if environ is None else environ
    return environ.get(CHILD_ENV) == "1"


def development_switch_env(environ=None) -> dict | None:
    """What a trusted parent adds to a confined child's scrubbed environment when development was asked for."""
    return {UNCONFINED_ENV: UNCONFINED_VALUE} if development_unconfined_requested(environ) else None


def evidence_acceptable(isolation: dict, *, allow_development: bool) -> bool:
    """Confined evidence is always acceptable; unconfined evidence only when development was asked for."""
    try:
        if isolation["uid"] == 0:
            return False
        shape = {key: isolation[key] for key in _CONFINED_SHAPE}
        mechanism = isolation["mechanism"]
    except (KeyError, TypeError):
        return False
    if mechanism == CONFINED_MECHANISM:
        return shape == _CONFINED_SHAPE
    if mechanism == UNCONFINED_MECHANISM:
        return allow_development and shape == _UNCONFINED_SHAPE
    return False


class Filter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32)]


class Program(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ushort), ("filters", ctypes.POINTER(Filter))]


class CapHeader(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class CapData(ctypes.Structure):
    _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32), ("inheritable", ctypes.c_uint32)]


def confine(*, allow_development: bool = False) -> dict:
    """Enter once, before reading any untrusted input; all failure paths abort.

    `allow_development` is passed only by the trusted, data-only children (the artifact checker
    and the ledger replay probe). A child that executes candidate code never passes it: the
    installed-image attack probe proves confinement by trying to kill its parent, and without
    seccomp that attempt would succeed against the host.
    """
    if not seccomp_available():
        if allow_development and development_unconfined_requested() and _bounded_child():
            return _development_unconfined()
        raise RuntimeError("checker requires Linux x86_64 seccomp-bpf; a development machine without it may set "
                           f"{UNCONFINED_ENV}={UNCONFINED_VALUE} to run the checker unconfined")
    libc = ctypes.CDLL(None, use_errno=True)
    if os.geteuid() == 0:
        os.setgroups([])
        os.setgid(65534)
        os.setuid(65534)
    if os.geteuid() == 0:
        raise RuntimeError("checker must be non-root")
    uid = os.geteuid()
    # Drop inherited capabilities too, including in a non-root capability-bearing installation.
    header, caps = CapHeader(0x20080522, 0), (CapData * 2)()
    if libc.capset(ctypes.byref(header), ctypes.byref(caps)) != 0:
        raise OSError(ctypes.get_errno(), "cannot clear checker capabilities")
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), "no_new_privs unavailable")
    # Parent uses close_fds as well. Close any descriptors opened by trusted startup.
    limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    os.closerange(3, min(limit, 1048576))
    for kind, value in ((resource.RLIMIT_AS, MEMORY_BYTES), (resource.RLIMIT_CPU, CPU_SECONDS),
                        (resource.RLIMIT_FSIZE, 0), (resource.RLIMIT_NOFILE, 16),
                        (resource.RLIMIT_NPROC, 0), (resource.RLIMIT_CORE, 0)):
        resource.setrlimit(kind, (value, value))
    allow, deny = 0x7FFF0000, 0x00050000 | errno.EPERM
    # seccomp_data: nr@0, arch@4, args[0]@16. Reject other ABIs (including x32).
    code = [Filter(0x20, 0, 0, 4), Filter(0x15, 1, 0, 0xC000003E),
            Filter(0x06, 0, 0, 0x80000000), Filter(0x20, 0, 0, 0)]
    # read(0, ...), write(1|2, ...); no other inherited FD can be used.
    code += [Filter(0x15, 0, 4, 0), Filter(0x20, 0, 0, 16), Filter(0x15, 0, 1, 0),
             Filter(0x06, 0, 0, allow), Filter(0x06, 0, 0, deny)]
    code += [Filter(0x15, 0, 5, 1), Filter(0x20, 0, 0, 16), Filter(0x15, 1, 0, 1),
             Filter(0x15, 0, 1, 2), Filter(0x06, 0, 0, allow), Filter(0x06, 0, 0, deny)]
    # Memory, signal return/handlers, self identity, time and exit only. In particular:
    # NO open/openat/stat, socket/connect, clone/exec, kill/ptrace/process_vm, prctl,
    # mount, bpf, io_uring, keyctl, chmod/chown, fcntl, dup or resource-limit changes.
    allowed = (3, 9, 10, 11, 12, 13, 14, 15, 24, 25, 28, 35, 39, 60, 97, 102, 104,
               107, 108, 186, 202, 228, 230, 231, 318)
    for number in allowed:
        code += [Filter(0x15, 0, 1, number), Filter(0x06, 0, 0, allow)]
    code.append(Filter(0x06, 0, 0, deny))
    filters = (Filter * len(code))(*code)
    program = Program(len(code), filters)
    if libc.prctl(22, 2, ctypes.byref(program), 0, 0) != 0:  # PR_SET_SECCOMP/FILTER
        raise OSError(ctypes.get_errno(), "seccomp filter unavailable")
    return {"mechanism": CONFINED_MECHANISM, "uid": uid,
            "no_new_privs": True, "filesystem": "denied", "network": "denied",
            "process_creation": "denied", "memory_bytes": MEMORY_BYTES, "cpu_seconds": CPU_SECONDS,
            "wall_seconds": WALL_SECONDS, "input_bytes": INPUT_BYTES, "output_bytes": OUTPUT_BYTES}


def _development_unconfined() -> dict:
    """Portable bounds only: inherited descriptors closed, CPU/file-size/core limits set.

    Files, sockets and processes stay reachable, and the record says so in every field. This
    exists so a developer without seccomp can exercise the loop; it is not confinement.
    """
    if os.geteuid() == 0:
        raise RuntimeError("checker must be non-root")
    limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    os.closerange(3, min(limit, 1048576))
    enforced = []
    for name, kind, value in (("cpu_seconds", resource.RLIMIT_CPU, CPU_SECONDS),
                              ("file_size", resource.RLIMIT_FSIZE, 0), ("core", resource.RLIMIT_CORE, 0)):
        try:
            resource.setrlimit(kind, (value, value))
            enforced.append(name)
        except (ValueError, OSError):
            pass
    return {"mechanism": UNCONFINED_MECHANISM, "uid": os.geteuid(), **_UNCONFINED_SHAPE,
            "memory_bytes": MEMORY_BYTES, "cpu_seconds": CPU_SECONDS, "wall_seconds": WALL_SECONDS,
            "input_bytes": INPUT_BYTES, "output_bytes": OUTPUT_BYTES,
            "enforced_limits": enforced + ["wall_seconds", "input_bytes", "output_bytes"]}
