"""Private, finite operational events; never retain stream text or exception detail."""
from __future__ import annotations

import builtins
import io
import json
import logging
import os
import stat
import sys
import threading
import time
import warnings
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

EVENTS = frozenset({'worker_start', 'worker_exit', 'worker_failure', 'runtime_warning',
                    'resource_sample', 'service_degraded', 'service_recovered', 'witness_failure', 'runtime_error'})
COUNTERS = frozenset({'stdout_bytes', 'stderr_bytes', 'threads', 'rss_bytes', 'failures', 'exit_code'})
ERRORS = frozenset(name for name, value in vars(builtins).items()
                  if isinstance(value, type) and issubclass(value, BaseException)) | {
    'StaleState', 'ValidationFailure', 'AuthorityDenied', 'DatabaseOwnershipConflict',
    'CancelledError', 'OperationalError', 'IntegrityError', 'DatabaseError', 'OtherError'}
_active = None


def _error_class(value):
    name = value if isinstance(value, str) else type(value).__name__
    return name if name in ERRORS else 'OtherError'


class PrivateDiagnostics:
    """One worker writes at most three 256 KiB files, with private nofollow opens."""
    def __init__(self, directory: Path, *, max_bytes: int = 262144, backups: int = 2):
        if not 512 <= max_bytes <= 262144 or not 0 <= backups <= 2:
            raise ValueError('invalid diagnostic bounds')
        if directory.is_symlink():
            raise ValueError('diagnostic directory cannot be a symlink')
        directory.mkdir(mode=0o700, exist_ok=True)
        self.fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.max_bytes, self.backups, self.lock = max_bytes, backups, threading.Lock()
        try:
            info = os.fstat(self.fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('diagnostic directory must be owner private')
            self._validate_slots()
        except BaseException:
            os.close(self.fd)
            raise

    def _name(self, slot):
        return 'worker.jsonl' + (f'.{slot}' if slot else '')

    def _validate_slots(self):
        for slot in range(self.backups + 1):
            try:
                info = os.stat(self._name(slot), dir_fd=self.fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1):
                raise ValueError('diagnostic files must be private unlinked regular files')

    def emit(self, event: str, *, error=None, **fields):
        if event not in EVENTS:
            return
        record = {'event': event, 'at_utc': datetime.now(UTC).isoformat()}
        if error is not None:
            record['error_type'] = _error_class(error)
        for name in COUNTERS:
            value = fields.get(name)
            if type(value) is int:
                record[name] = min(max(value, -255 if name == 'exit_code' else 0), 2**63 - 1)
        raw = (json.dumps(record, separators=(',', ':')) + '\n').encode()
        with self.lock:
            self._validate_slots()
            descriptor = os.open('worker.jsonl', os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
                                 0o600, dir_fd=self.fd)
            try:
                info = os.fstat(descriptor)
                if info.st_mode & 0o077 or info.st_nlink != 1 or not stat.S_ISREG(info.st_mode):
                    raise ValueError('diagnostic file changed')
                if info.st_size + len(raw) > self.max_bytes:
                    os.close(descriptor)
                    descriptor = -1
                    for slot in range(self.backups, 0, -1):
                        try:
                            os.replace(self._name(slot - 1), self._name(slot),
                                       src_dir_fd=self.fd, dst_dir_fd=self.fd)
                        except FileNotFoundError:
                            pass
                    descriptor = os.open('worker.jsonl', os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                                         0o600, dir_fd=self.fd)
                os.write(descriptor, raw)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)

    def close(self):
        os.close(self.fd)


class _SafeLogHandler(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.WARNING:
            error = record.exc_info[0].__name__ if record.exc_info else None
            emit_event("runtime_error" if record.levelno >= logging.ERROR else "runtime_warning", error=error)


class _DiscardStream(io.TextIOBase):
    def __init__(self):
        self.byte_count = 0

    def write(self, text):
        self.byte_count = min(2**63 - 1, self.byte_count + len(text.encode('utf-8', errors='replace')))
        return len(text)

    def flush(self):
        pass


def _resources():
    result = {}
    try:
        for line in Path('/proc/self/status').read_text().splitlines():
            if line.startswith('Threads:'):
                result['threads'] = int(line.split()[1])
            elif line.startswith('VmRSS:'):
                result['rss_bytes'] = int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return result


def emit_event(event, *, error=None, **fields):
    if _active is not None:
        try:
            _active.emit(event, error=error, **fields)
        except (OSError, ValueError):
            # Diagnostic storage must not interrupt financial reconciliation.
            pass


def sample_resources():
    if _active is not None and time.monotonic() >= getattr(_active, 'next_sample', 0):
        _active.next_sample = time.monotonic() + 60
        emit_event('resource_sample', **_resources())


@contextmanager
def worker_context():
    """Enabled solely by controller launch; raw streams remain discarded."""
    global _active
    directory = os.environ.get('TRADE_GRAPH_DIAGNOSTICS_DIRECTORY')
    if not directory:
        yield
        return
    sink = PrivateDiagnostics(Path(directory))
    safe_logging = _SafeLogHandler(level=logging.WARNING)
    logging.getLogger().addHandler(safe_logging)
    old_active, _active = _active, sink
    old_stdout, old_stderr, old_warning = sys.stdout, sys.stderr, warnings.showwarning
    stdout, stderr = _DiscardStream(), _DiscardStream()
    sys.stdout, sys.stderr = stdout, stderr
    warnings.showwarning = lambda message, category, *args, **kwargs: emit_event(
        'runtime_warning', error=category.__name__)
    emit_event('worker_start', **_resources())
    exit_code = 0
    try:
        yield sink
    except BaseException as exc:
        exit_code = exc.code if isinstance(exc, SystemExit) and type(exc.code) is int else 1
        if exit_code:
            emit_event('worker_failure', error=exc)
        raise
    finally:
        exit_code = getattr(sink, 'exit_code', exit_code)
        emit_event('worker_exit', exit_code=exit_code, stdout_bytes=stdout.byte_count,
                   stderr_bytes=stderr.byte_count, **_resources())
        sys.stdout, sys.stderr, warnings.showwarning = old_stdout, old_stderr, old_warning
        logging.getLogger().removeHandler(safe_logging)
        safe_logging.close()
        _active = old_active
        sink.close()
