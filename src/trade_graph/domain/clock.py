"""Injected clocks. Tests never depend on the wall clock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_utc(value: str) -> datetime:
    if not value.endswith("Z"):
        raise ValueError("expected UTC timestamp ending in Z")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    def __init__(self, now: datetime) -> None:
        if now.tzinfo is None:
            raise ValueError("frozen clock requires a timezone")
        self._now = now.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)
