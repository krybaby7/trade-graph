"""The four market types software gives a coin (W11-A, C10, D280 item 3): the words only.

``kernel/market_type.py`` decides which one applies; the practice-range grammar (``contracts/practice_range.py``) and
the stored labels use the same words. Protected with the rest of ``domain/`` (``kernel/authority.py``).
"""

from __future__ import annotations

from typing import Final, Literal

MarketType = Literal["rising", "sideways", "falling", "unclear"]
MARKET_TYPES: Final[tuple[MarketType, ...]] = ("rising", "sideways", "falling", "unclear")

__all__ = ["MARKET_TYPES", "MarketType"]
