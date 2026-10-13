"""Synthetic Kraken OHLCVT archives for tests: headerless CSV files in a ZIP or a plain directory.

Prices come from integer arithmetic (a small linear congruential walk in cents), so no float is involved
and every build is identical. Nothing here is real Kraken data.
"""

from __future__ import annotations

import zipfile
from collections.abc import Collection, Mapping, Sequence
from decimal import Decimal
from pathlib import Path

FIRST_OPEN = 1_782_864_000  # 2026-07-01T00:00:00Z, the first open of the synthetic 2026Q3 quarter
DAY = 86_400


def _cents(value: int) -> str:
    return str(Decimal(value).scaleb(-2))


def csv_line(open_unix: int, open_: str, high: str, low: str, close: str, volume: str, trades: int) -> str:
    return f"{open_unix},{open_},{high},{low},{close},{volume},{trades}"


def synthetic_rows(first_open_unix: int, count: int, interval_minutes: int, *, seed: int = 1,
                   start_cents: int = 10_000, skip: Collection[int] = ()) -> list[str]:
    """``count`` consecutive candles; indexes in ``skip`` are left out (a gap, as Kraken leaves quiet slots)."""
    step = interval_minutes * 60
    state = seed * 2_654_435_761 % 2**31
    price = start_cents
    lines: list[str] = []
    for index in range(count):
        state = (state * 1_103_515_245 + 12_345) % 2**31
        opened = price
        price = max(50, price + (state % 101) - 50)
        high = max(opened, price) + (state >> 8) % 20
        low = min(opened, price) - (state >> 12) % 20
        volume = f"{(state >> 4) % 5000 + 1}.{(state >> 16) % 100_000_000:08d}"
        if index in skip:
            continue
        lines.append(csv_line(first_open_unix + index * step, _cents(opened), _cents(high), _cents(low),
                              _cents(price), volume, 1 + state % 50))
    return lines


def csv_text(lines: Sequence[str]) -> str:
    return "\n".join(lines) + "\n"


def universe_members(codes: Sequence[str], *, first_open_unix: int = FIRST_OPEN, days: int = 95,
                     intervals: Sequence[int] = (1, 5, 15, 60, 240, 1440), thin_days: int = 1,
                     skip: Mapping[tuple[str, int], Collection[int]] | None = None) -> dict[str, str]:
    """One ``PAIR_INTERVAL.csv`` per pair and interval, plus a MANIFEST.json as the real archive carries.

    One-minute and five-minute files are kept to ``thin_days`` so a test archive stays small.
    """
    skipped = skip or {}
    members: dict[str, str] = {"MANIFEST.json": '{"synthetic": true}\n'}
    for position, code in enumerate(codes, start=1):
        for interval in intervals:
            span = thin_days if interval < 15 else days
            count = span * 1440 // interval
            lines = synthetic_rows(first_open_unix, count, interval, seed=position * 31 + interval,
                                   start_cents=10_000 * position, skip=skipped.get((code, interval), ()))
            members[f"{code}_{interval}.csv"] = csv_text(lines)
    return members


def write_zip(path: Path, members: Mapping[str, str | bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> Path:
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        for name, content in members.items():
            archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 10, 1, 0, 0, 0)), content,
                             compress_type=compression)
    return path


def write_directory(path: Path, members: Mapping[str, str | bytes]) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for name, content in members.items():
        data = content.encode("ascii") if isinstance(content, str) else content
        (path / name).write_bytes(data)
    return path
