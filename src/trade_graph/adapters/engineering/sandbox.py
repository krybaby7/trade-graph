"""Linux x86-64, data-pipe-only checker confinement, NOT a general code sandbox.

The trusted checker loads its code before entering this boundary. Thereafter it
cannot open/stat a file, create a socket/process, execute a program, access another
process, change limits or acquire privilege. Only fd 0 can be read and fds 1/2
written. No candidate code, mounts, production paths or database handles enter it.
Unsupported kernels/architectures fail closed; there is no scrub-only fallback.
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


class Filter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32)]


class Program(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ushort), ("filters", ctypes.POINTER(Filter))]


class CapHeader(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class CapData(ctypes.Structure):
    _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32), ("inheritable", ctypes.c_uint32)]


def confine() -> dict:
    """Enter once, before reading any untrusted input; all failure paths abort."""
    if sys.platform != "linux" or os.uname().machine != "x86_64":
        raise RuntimeError("checker requires Linux x86_64 seccomp-bpf")
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
    return {"mechanism": "linux-x86_64-seccomp-data-pipe-v1", "uid": uid,
            "no_new_privs": True, "filesystem": "denied", "network": "denied",
            "process_creation": "denied", "memory_bytes": MEMORY_BYTES, "cpu_seconds": CPU_SECONDS,
            "wall_seconds": WALL_SECONDS, "input_bytes": INPUT_BYTES, "output_bytes": OUTPUT_BYTES}
