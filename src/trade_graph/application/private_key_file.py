"""Read a provider key from the owner's private file (T36, D125). Never from Git, chat or a model.

The file holds one plain key and nothing else. It must sit outside any Git checkout,
be a single-link regular file owned by this user with no group or other access, in a
folder others cannot write. The value comes back as a ``SecretStr`` whose text form is
masked, and no error message ever includes the file's content.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

from pydantic import SecretStr

MAXIMUM_KEY_BYTES = 4096


def read_private_key(path: Path) -> SecretStr:
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("the key file path must be absolute")
    for ancestor in path.parents:
        if (ancestor / ".git").exists():
            raise PermissionError("the key file must be outside any Git checkout")
    folder = os.stat(path.parent)
    if folder.st_mode & 0o022:
        raise PermissionError("the key file's folder must not be writable by others")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise PermissionError("the key file must not be a symbolic link") from None
        raise
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise PermissionError("the key file must be a regular file")
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise PermissionError("the key file must be owner-only (mode 600 or 400)")
        if info.st_nlink != 1:
            raise PermissionError("the key file must have a single name (no hard links)")
        if info.st_size > MAXIMUM_KEY_BYTES:
            raise ValueError("the key file is too large to hold one key")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(MAXIMUM_KEY_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(raw) > MAXIMUM_KEY_BYTES:
        raise ValueError("the key file is too large to hold one key")
    text = raw[:-2] if raw.endswith(b"\r\n") else raw[:-1] if raw.endswith(b"\n") else raw
    if not 16 <= len(text) <= MAXIMUM_KEY_BYTES or any(byte < 33 or byte > 126 for byte in text):
        raise ValueError("the key file must hold one key of 16 or more visible ASCII characters")
    return SecretStr(text.decode("ascii"))
