"""The two example strategies an installation starts its lab with (W11-B, D293).

They are the repository's ``strategies/practice/*.v1.json`` (unproven 15-minute readings of the prose templates in
``strategies/templates``), packaged here so the protected image carries them: only ``src/trade_graph`` is copied
into the image. A test keeps the copies equal to the repository's. Registering them gives the lab a start; it is not a
confirmation: the desk asks for the confirmations itself, as for any version (D138 item 5).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from trade_graph.application.practice_range.store import PracticeRangeStore
from trade_graph.contracts.practice_range import PracticeStrategy

_FOLDER = Path(__file__).with_name("starting")
STARTING_FILES: Final = {name: _FOLDER / name for name in ("slow-trend-pullback.v1.json",
                                                           "range-reversion.v1.json")}


def starting_strategies() -> list[PracticeStrategy]:
    return [PracticeStrategy.model_validate_json(path.read_text(encoding="utf-8"))
            for path in STARTING_FILES.values()]


def register_starting(store: PracticeRangeStore) -> list[str]:
    """Register the examples when the installation has no registered strategy at all; returns what was registered."""
    if store.strategies():
        return []
    return [store.register(strategy).ref for strategy in starting_strategies()]


__all__ = ["STARTING_FILES", "register_starting", "starting_strategies"]
