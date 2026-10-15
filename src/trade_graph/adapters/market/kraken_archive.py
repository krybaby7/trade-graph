"""Read a locally supplied Kraken OHLCVT archive: a ZIP, or a directory of CSV files. Never the network.

Kraken's support page does not state the file layout, the timestamp unit or whether candles are complete
(docs/KRAKEN-ARCHIVE-IMPORT.md lists what is confirmed and what is not), so this reader is strict and loud:
member names are checked against an allowlist and never used as paths, nothing is extracted to disk, every size
is limited while streaming, and each rejected row is counted under a stable reason.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import zipfile
import zlib
from collections import Counter
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import BinaryIO, NamedTuple, Protocol

from trade_graph.domain.errors import ValidationFailure

ARCHIVE_SOURCE = "kraken_ohlcvt_archive"
MIB = 1024 * 1024
GIB = 1024 * MIB
CHUNK_BYTES = 64 * 1024
RATIO_GRACE_BYTES = MIB
# Plausible Unix seconds: 2010-01-01 up to 2100-01-01. Milliseconds (about 1.7e12) land far outside.
MIN_TIMESTAMP = 1_262_304_000
MAX_TIMESTAMP = 4_102_444_800

_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_DATA_NAME = re.compile(r"([A-Z0-9]{2,24})_([0-9]{1,4})\.csv")
_MULTIPART = re.compile(r".*\.zip\.part[0-9]+", re.IGNORECASE)
_INTEGER = re.compile(r"-?[0-9]{1,18}")
# At most 21 digits and a two-digit exponent keep the fixed-point width under the Candle contract's 128.
_DECIMAL = re.compile(r"-?[0-9]{1,20}(?:\.[0-9]{1,20})?(?:[eE][+-]?[0-9]{1,2})?")
_PAIR_ALIASES = {"XBT": "BTC", "XDG": "DOGE"}
# National currencies that Kraken also quotes against the dollar (EURUSD, GBPUSD, ...). They are not coins, so they are
# never stored as one. Stablecoins are deliberately not listed: excluding them (D131) is the scanner's decision.
FIAT_BASES = frozenset({"AED", "AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD", "USD"})
_LEGACY_USD = re.compile(r"X([A-Z]{3})ZUSD")
_BASE = re.compile(r"[A-Z0-9]{1,16}")
_ALLOWED_COMPRESSION = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)

REJECTION_REASONS = (
    "malformed_row", "timestamp_out_of_range", "off_grid", "duplicate_timestamp", "out_of_order",
    "non_positive_price", "negative_volume", "negative_trades", "ohlc_out_of_bounds",
)


class ArchiveRejected(ValidationFailure):
    """The whole file is refused. ``reason`` is a stable code; ``run_id`` and ``report`` are set by the importer."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.run_id: int | None = None
        self.report: dict | None = None


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    """Bounds that keep a hostile or damaged archive from exhausting memory, disk or time."""

    max_members: int = 50_000
    max_member_bytes: int = 2 * GIB
    max_total_bytes: int = 16 * GIB
    max_rows: int = 400_000_000
    max_compression_ratio: int = 100
    max_line_bytes: int = 256
    max_rejected_share: Decimal = Decimal("0.01")

    def __post_init__(self) -> None:
        for name in ("max_members", "max_member_bytes", "max_total_bytes", "max_rows", "max_compression_ratio",
                     "max_line_bytes"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(self.max_rejected_share, Decimal) or not Decimal(0) <= self.max_rejected_share <= 1:
            raise ValueError("max_rejected_share must be a Decimal between 0 and 1")


@dataclass(frozen=True, slots=True)
class ArchiveMember:
    name: str
    size: int
    compressed_size: int
    pair_code: str | None
    interval_minutes: int | None


class ArchiveRow(NamedTuple):
    open_unix: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trades: int


class ArchiveSource(Protocol):
    limits: ArchiveLimits

    def open(self, member: ArchiveMember) -> AbstractContextManager[BinaryIO]: ...


# --- names ---------------------------------------------------------------------------------------------------------


def _shown(name: str) -> str:
    return ascii(name[:60])


def check_member_name(name: str) -> None:
    """Allowlist: plain file names only. No separators, drive letters, dot-dot, controls or hidden files."""
    if not _SAFE_NAME.fullmatch(name) or ".." in name:
        raise ArchiveRejected("unsafe_member_name", f"unsafe archive member name {_shown(name)}; the importer only "
                              "reads flat, plain file names and refuses the whole file otherwise")


def parse_member_name(name: str) -> tuple[str, int] | None:
    """``XBTUSD_15.csv`` is ('XBTUSD', 15); anything else is not a data file."""
    found = _DATA_NAME.fullmatch(name)
    return None if found is None else (found.group(1), int(found.group(2)))


def symbol_for_pair_code(code: str) -> str | None:
    """The USD symbol for a Kraken pair code ('XBTUSD' -> 'BTC/USD'), or None when it is not USD-quoted.

    This is the one place the archive's pair names are interpreted. The collector stream reads names from
    Kraken's AssetPairs catalog; this function can later be replaced by a catalog lookup without other changes.
    """
    legacy = _LEGACY_USD.fullmatch(code)
    if legacy is not None:
        base = legacy.group(1)
    elif code.endswith("USD") and len(code) > 3:
        base = code[:-3]
    else:
        return None
    base = _PAIR_ALIASES.get(base, base)
    return f"{base}/USD" if _BASE.fullmatch(base) else None


def is_fiat_symbol(symbol: str) -> bool:
    """Whether the base of a symbol such as 'EUR/USD' is a national currency rather than a coin."""
    return symbol.split("/")[0] in FIAT_BASES


# --- rows ----------------------------------------------------------------------------------------------------------


def parse_row(line: bytes, step_seconds: int) -> ArchiveRow | str:
    """One CSV row (timestamp, open, high, low, close, volume, trades) or the reason it is rejected.

    The checks are at least as strict as the Candle contract, so an accepted row always builds a Candle.
    """
    try:
        text = line.decode("ascii")
    except UnicodeDecodeError:
        return "malformed_row"
    if text.endswith("\r"):
        text = text[:-1]
    fields = text.split(",")
    if len(fields) != 7:
        return "malformed_row"
    stamp, trades = fields[0], fields[6]
    if not _INTEGER.fullmatch(stamp) or not _INTEGER.fullmatch(trades):
        return "malformed_row"
    if not all(_DECIMAL.fullmatch(field) for field in fields[1:6]):
        return "malformed_row"
    opened = int(stamp)
    if not MIN_TIMESTAMP <= opened < MAX_TIMESTAMP:
        return "timestamp_out_of_range"
    if opened % step_seconds:
        return "off_grid"
    open_, high, low, close, volume = (Decimal(field) for field in fields[1:6])
    if min(open_, high, low, close) <= 0:
        return "non_positive_price"
    if volume < 0:
        return "negative_volume"
    count = int(trades)
    if count < 0:
        return "negative_trades"
    if low > min(open_, close) or high < max(open_, close) or low > high:
        return "ohlc_out_of_bounds"
    return ArchiveRow(opened, open_, high, low, close, volume, count)


class ReadBudget:
    """Running totals for one pass over an archive, shared by every member read in that pass."""

    def __init__(self, limits: ArchiveLimits) -> None:
        self.limits = limits
        self.bytes_read = 0
        self.rows_read = 0

    def add_bytes(self, count: int) -> None:
        self.bytes_read += count
        if self.bytes_read > self.limits.max_total_bytes:
            raise ArchiveRejected("total_size_limit", f"the archive inflates past the total limit of "
                                  f"{self.limits.max_total_bytes} bytes (raise --max-uncompressed-gb if intended)")

    def add_row(self) -> None:
        self.rows_read += 1
        if self.rows_read > self.limits.max_rows:
            raise ArchiveRejected("row_limit", f"the archive holds more than {self.limits.max_rows} rows")


class MemberReader:
    """Streams the accepted rows of one data member and counts everything it rejects. Use once."""

    def __init__(self, archive: ArchiveSource, member: ArchiveMember, budget: ReadBudget) -> None:
        if member.interval_minutes is None:
            raise ValueError("not a data member")
        self._archive = archive
        self.member = member
        self._budget = budget
        self.rows_read = 0
        self.accepted = 0
        self.rejected: Counter[str] = Counter()
        self.first_open: int | None = None
        self.last_open: int | None = None

    def __iter__(self) -> Iterator[ArchiveRow]:
        member, limits = self.member, self._archive.limits
        if member.size > limits.max_member_bytes:
            raise ArchiveRejected("member_size_limit", f"{_shown(member.name)} declares {member.size} bytes, over the "
                                  f"per-member limit of {limits.max_member_bytes}")
        step = (member.interval_minutes or 0) * 60
        try:
            with self._archive.open(member) as stream:
                for line in self._lines(stream):
                    self.rows_read += 1
                    self._budget.add_row()
                    row = parse_row(line, step)
                    if isinstance(row, str):
                        self.rejected[row] += 1
                        continue
                    if self.last_open is not None and row.open_unix <= self.last_open:
                        self.rejected["duplicate_timestamp" if row.open_unix == self.last_open else "out_of_order"] += 1
                        continue
                    self.accepted += 1
                    if self.first_open is None:
                        self.first_open = row.open_unix
                    self.last_open = row.open_unix
                    yield row
        except (zipfile.BadZipFile, zlib.error, EOFError, OSError) as error:
            # OSError: zipfile seeks to a stored offset, and bytes missing from the middle of the file (a mis-joined
            # download) make that offset invalid; a failing disk read lands here too.
            raise ArchiveRejected("corrupt_member", f"{_shown(member.name)} is damaged, truncated or unreadable"
                                  ) from error

    def _lines(self, stream: BinaryIO) -> Iterator[bytes]:
        member, limits = self.member, self._archive.limits
        allowed_inflation = max(member.compressed_size, 1) * limits.max_compression_ratio
        size = 0
        pending = b""
        while chunk := stream.read(CHUNK_BYTES):
            size += len(chunk)
            self._budget.add_bytes(len(chunk))
            if size > limits.max_member_bytes:
                raise ArchiveRejected("member_size_limit", f"{_shown(member.name)} inflates past the per-member limit "
                                      f"of {limits.max_member_bytes} bytes")
            if size > RATIO_GRACE_BYTES and size > allowed_inflation:
                raise ArchiveRejected("compression_ratio_limit", f"{_shown(member.name)} inflates more than "
                                      f"{limits.max_compression_ratio} times its stored size")
            *lines, pending = (pending + chunk).split(b"\n")
            for line in lines:
                if len(line) > limits.max_line_bytes:
                    raise ArchiveRejected("line_too_long", f"{_shown(member.name)} has a line over "
                                          f"{limits.max_line_bytes} bytes")
                yield line
            if len(pending) > limits.max_line_bytes:
                raise ArchiveRejected("line_too_long", f"{_shown(member.name)} has a line over "
                                      f"{limits.max_line_bytes} bytes")
        if pending:
            yield pending


# --- archives ------------------------------------------------------------------------------------------------------


def _member(name: str, size: int, compressed: int) -> ArchiveMember:
    parsed = parse_member_name(name)
    return ArchiveMember(name=name, size=size, compressed_size=compressed,
                         pair_code=None if parsed is None else parsed[0],
                         interval_minutes=None if parsed is None else parsed[1])


class ZipArchive:
    format = "zip"

    def __init__(self, path: Path, limits: ArchiveLimits) -> None:
        self.limits = limits
        try:
            self._zip = zipfile.ZipFile(path)
        except zipfile.BadZipFile as error:
            raise ArchiveRejected("not_a_zip", "the file is not a readable ZIP archive") from error
        except OSError as error:
            raise ArchiveRejected("not_a_file", f"the archive cannot be read: {error.strerror}") from error
        try:
            infos = self._zip.infolist()
            if len(infos) > limits.max_members:
                raise ArchiveRejected("member_limit", f"the archive has {len(infos)} members, over the limit of "
                                      f"{limits.max_members}")
            self._infos: dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                check_member_name(info.filename)
                if info.filename in self._infos:
                    raise ArchiveRejected("duplicate_member_name", f"two members are named {_shown(info.filename)}")
                # Unix mode sits in the high 16 bits; many writers leave the file-type bits at zero, which is fine.
                if stat.S_IFMT(info.external_attr >> 16) not in (0, stat.S_IFREG):
                    raise ArchiveRejected("unsafe_member_type", f"{_shown(info.filename)} is not a regular file "
                                          "(links and special entries are refused)")
                self._infos[info.filename] = info
            self.members = tuple(sorted((_member(i.filename, i.file_size, i.compress_size) for i in infos),
                                        key=lambda member: member.name))
        except BaseException:
            self._zip.close()
            raise

    def open(self, member: ArchiveMember) -> AbstractContextManager[BinaryIO]:
        info = self._infos[member.name]
        if info.flag_bits & 0x1 or info.compress_type not in _ALLOWED_COMPRESSION:
            raise ArchiveRejected("unsupported_member", f"{_shown(member.name)} is encrypted or uses a compression "
                                  "method other than stored/deflate")
        return self._zip.open(info)

    def close(self) -> None:
        self._zip.close()


class DirectoryArchive:
    format = "directory"

    def __init__(self, path: Path, limits: ArchiveLimits) -> None:
        self.limits = limits
        self._path = path
        members = []
        with os.scandir(path) as entries:
            for entry in entries:
                check_member_name(entry.name)
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    raise ArchiveRejected("unsafe_member_type", f"{_shown(entry.name)} is not a regular file")
                size = entry.stat(follow_symlinks=False).st_size
                members.append(_member(entry.name, size, size))
                if len(members) > limits.max_members:
                    raise ArchiveRejected("member_limit", f"the directory has more than {limits.max_members} files")
        self.members = tuple(sorted(members, key=lambda member: member.name))

    def open(self, member: ArchiveMember) -> AbstractContextManager[BinaryIO]:
        return open(self._path / member.name, "rb")

    def close(self) -> None:
        return None


def _classify_path(path: Path) -> str:
    if _MULTIPART.fullmatch(path.name):
        raise ArchiveRejected("multipart_archive", "this is one part of a split download; join the parts into one "
                              "ZIP first (and check Kraken's published SHA-256) before importing")
    try:
        mode = path.stat().st_mode
    except OSError as error:
        raise ArchiveRejected("not_a_file", "the archive path does not exist or cannot be read") from error
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "zip"
    raise ArchiveRejected("not_a_file", "the archive path is not a regular file or directory")


@contextmanager
def open_archive(path: Path | str, limits: ArchiveLimits | None = None) -> Iterator[ZipArchive | DirectoryArchive]:
    """Open a local ZIP or directory of CSV files. Applies the name, type and member-count policy up front."""
    path = Path(path)
    limits = limits or ArchiveLimits()
    kind = _classify_path(path)
    archive = ZipArchive(path, limits) if kind == "zip" else DirectoryArchive(path, limits)
    try:
        yield archive
    finally:
        archive.close()


# --- identity ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArchiveFingerprint:
    file_name: str
    size_bytes: int
    sha256: str
    format: str
    signature: tuple


def _file_signature(path: Path) -> tuple:
    status = path.stat()
    return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns)


def archive_signature(path: Path | str) -> tuple:
    """Cheap identity of the bytes on disk, to notice a file that changes while it is being imported."""
    path = Path(path)
    if path.is_dir():
        return tuple(sorted((entry.name, entry.stat(follow_symlinks=False).st_size,
                             entry.stat(follow_symlinks=False).st_mtime_ns)
                            for entry in os.scandir(path) if entry.is_file(follow_symlinks=False)))
    return _file_signature(path)


def fingerprint_archive(path: Path | str) -> ArchiveFingerprint:
    """File name, size and streamed SHA-256. A directory hashes its files' names, sizes and SHA-256 values."""
    path = Path(path)
    kind = _classify_path(path)
    signature = archive_signature(path)
    if kind == "zip":
        with open(path, "rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        return ArchiveFingerprint(path.name, path.stat().st_size, digest, "zip", signature)
    overall = hashlib.sha256()
    total = 0
    for entry in sorted(os.scandir(path), key=lambda item: item.name):
        if not entry.is_file(follow_symlinks=False):
            continue
        with open(entry.path, "rb") as stream:
            inner = hashlib.file_digest(stream, "sha256").hexdigest()
        size = entry.stat(follow_symlinks=False).st_size
        total += size
        overall.update(f"{entry.name}\0{size}\0{inner}\n".encode("ascii", "backslashreplace"))
    return ArchiveFingerprint(path.name, total, overall.hexdigest(), "directory", signature)
