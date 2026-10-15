"""The size an indicator output is judged against in the settling and cross-check tests.

A difference between two runs means little unless it is measured against the output it belongs to. A tolerance
stated against the median *price* lets the MACD histogram or the ATR, which are a few percent or a few tenths of a
percent of price, be wrong by as much as their own size. So point readings (0 to 100 scales, directional indices)
are judged in absolute points, and every other output against its own typical size: the median absolute value of
the library's own output over the candles where it has one.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from trade_graph.contracts.indicators import OutputUnit


def output_scale(unit: OutputUnit, values: NDArray[np.float64]) -> float:
    """1 for points; the largest absolute value (at least 1) for volume; otherwise the median absolute value."""
    if unit == "points":
        return 1.0
    finite = np.abs(values[np.isfinite(values)])
    if finite.size == 0:
        return 1.0
    if unit == "volume":
        return max(1.0, float(finite.max()))
    typical = float(np.median(finite))
    return typical if typical > 0.0 else 1.0
