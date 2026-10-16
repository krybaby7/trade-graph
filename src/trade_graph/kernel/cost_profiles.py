"""The book of dated fee profiles (T28, D146). Published revisions are append-only and pinned by content hash.

The records live in ``cost_profiles.json`` next to this module. Each published revision's hash is pinned below,
in protected kernel source, so a quiet edit of a published fee, date or source is refused at load instead of
silently changing later replays. A changed fee is a new revision with its own effective date and a new pin; the
earlier revision keeps resolving exactly as before, and every fill names the revision it used.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from trade_graph.contracts.fills import CostProfile
from trade_graph.domain.errors import ValidationFailure

KRAKEN_STARTING = "kraken-spot-starting-tier"
KRAKEN_AFTER_10K = "kraken-spot-after-10k-volume"
OKX_EEA = "okx-eea-spot"
SCHEMA_VERSION = 1
MAX_PROFILES = 256
_DATA = Path(__file__).with_name("cost_profiles.json")

# ref -> sha256 of the canonical record. Add a pin for each new revision; never change an existing one.
PUBLISHED_PINS: Mapping[str, str] = {
    "kraken-spot-starting-tier@1": "65ec96d721676e6d2fbcf3abef664c9d147c3c17052e22570e87af9fd243cbba",
    "kraken-spot-after-10k-volume@1": "0092a0ae6b34a17a1da0554715098e979d36c6ed0ec7e943e6a736a7a0cf00a3",
    "okx-eea-spot@1": "27a2829abdc3404430da22c30b6a7a0f9bf97231ab2b999e0a8c7f60c2c245bb",
}


def packaged_document() -> dict[str, Any]:
    """A fresh copy of the packaged records, as stored."""
    return json.loads(_DATA.read_text(encoding="utf-8"))


class CostProfileBook:
    """Validated published fee profiles, every revision kept."""

    def __init__(self, profiles: tuple[CostProfile, ...]) -> None:
        self._profiles = profiles
        self._by_ref = {profile.ref: profile for profile in profiles}

    @classmethod
    def from_document(cls, document: Any, *, pins: Mapping[str, str] = PUBLISHED_PINS) -> CostProfileBook:
        if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
            raise ValidationFailure("cost profiles need schema version 1")
        records = document.get("profiles")
        if not isinstance(records, list) or not 1 <= len(records) <= MAX_PROFILES:
            raise ValidationFailure("cost profiles hold between 1 and 256 records")
        profiles: list[CostProfile] = []
        for record in records:
            try:
                profiles.append(CostProfile.model_validate(record))
            except ValidationError as error:
                raise ValidationFailure(f"invalid cost profile: {error.errors()[0]['msg']}") from None
        refs = [profile.ref for profile in profiles]
        if len(set(refs)) != len(refs):
            raise ValidationFailure("a cost profile revision appears twice")
        for profile in profiles:
            if profile.basis != "published_schedule":
                raise ValidationFailure("the book holds published fee schedules only")
            pinned = pins.get(profile.ref)
            if pinned is None:
                raise ValidationFailure(f"cost profile {profile.ref} is not pinned; pin each new revision")
            if pinned != profile.content_sha256:
                raise ValidationFailure(
                    f"cost profile {profile.ref} differs from its pinned content; publish a new revision instead")
        missing = sorted(set(pins) - set(refs))
        if missing:
            raise ValidationFailure(f"pinned cost profile revisions are missing: {', '.join(missing)}")
        for profile_id in {profile.profile_id for profile in profiles}:
            history = sorted((p for p in profiles if p.profile_id == profile_id), key=lambda p: p.revision)
            if [p.revision for p in history] != list(range(1, len(history) + 1)):
                raise ValidationFailure(f"revisions of {profile_id} must be contiguous from 1")
            if len({(p.venue, p.market) for p in history}) != 1:
                raise ValidationFailure(f"revisions of {profile_id} must keep one venue and market")
            if any(later.effective_from <= earlier.effective_from for earlier, later in zip(history, history[1:])):
                raise ValidationFailure(f"each revision of {profile_id} must take effect later than the one before")
        return cls(tuple(sorted(profiles, key=lambda p: (p.profile_id, p.revision))))

    @property
    def profiles(self) -> tuple[CostProfile, ...]:
        return self._profiles

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted({profile.profile_id for profile in self._profiles}))

    def get(self, profile_id: str, revision: int | None = None) -> CostProfile:
        """The named revision, or the newest one."""
        history = [profile for profile in self._profiles if profile.profile_id == profile_id]
        if not history:
            raise ValidationFailure(f"unknown cost profile {profile_id}")
        if revision is None:
            return history[-1]
        return self.resolve(f"{profile_id}@{revision}")

    def resolve(self, ref: str) -> CostProfile:
        profile = self._by_ref.get(ref)
        if profile is None:
            raise ValidationFailure(f"unknown cost profile revision {ref}")
        return profile

    def effective(self, profile_id: str, on: date) -> CostProfile:
        """The revision in force on a day, for replaying a past window under the fees of that time."""
        history = [p for p in self._profiles if p.profile_id == profile_id and p.effective_from <= on]
        if not history:
            raise ValidationFailure(f"no revision of {profile_id} was in force on {on.isoformat()}")
        return history[-1]


@lru_cache(maxsize=1)
def load_cost_profiles() -> CostProfileBook:
    return CostProfileBook.from_document(packaged_document())
