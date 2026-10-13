"""A thin wrapper around TA-Lib: validate against the menu, convert Decimal to float64, call, label.

No indicator formula is written here (D118). Every number comes from the library, and the number of
candles an indicator needs comes from the library's own lookback function. This module only

- checks the request against ``contracts/indicators.py``;
- converts Decimal candle columns to float64 arrays, refusing anything that is not an exact, finite,
  positive (price) or non-negative (volume) Decimal, so floats never enter as prices;
- calls the TA-Lib function and labels its outputs by the menu's output names.

Indicator outputs are floats and may contain NaN where the library defines no value yet. Prices,
quantities, fees and balances stay Decimal everywhere else in the system.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from decimal import Decimal

import numpy as np
import talib
from numpy.typing import NDArray
from talib import abstract

from trade_graph.contracts.candles import Candle
from trade_graph.contracts.indicators import get_indicator
from trade_graph.domain.errors import TradeGraphError, ValidationFailure

# The candle store reads at most this many slots at once; one computation never needs more.
MAX_ROWS = 100_000
_PRICE_FIELDS = frozenset({"open", "high", "low", "close"})
_CANDLE_FIELDS = ("open", "high", "low", "close", "volume")
# Process-wide TA-Lib settings that would silently change recursive indicators if anything set them.
_UNSTABLE_PERIODS = ("ADX", "ATR", "DX", "EMA", "MINUS_DI", "MINUS_DM", "NATR", "PLUS_DI", "PLUS_DM", "RSI")


class IndicatorComputationError(TradeGraphError):
    """The library failed or was left in a state that would change the numbers."""

    code = "indicator_failed"


def columns_from_candles(candles: Sequence[Candle]) -> dict[str, tuple[Decimal, ...]]:
    """Decimal columns by candle field, oldest first, exactly as stored (no conversion)."""
    return {field: tuple(getattr(candle, field) for candle in candles) for field in _CANDLE_FIELDS}


def library_versions() -> dict[str, str]:
    """What computed the numbers, for receipts: the Python wrapper, the bundled C library and NumPy."""
    return {
        "ta_lib_python": talib.__version__,
        "ta_lib_c": talib.__ta_version__.decode("ascii").split()[0],
        "numpy": np.__version__,
    }


def lookback(spec_name: str, params: Mapping[str, object] | None = None) -> int:
    """Leading positions with no value for these parameters, as reported by TA-Lib itself."""
    spec = get_indicator(spec_name)
    resolved = spec.resolve_parameters({} if params is None else params)
    _require_library_defaults()
    function = abstract.Function(spec.talib_function)
    function.parameters = {**resolved, **spec.fixed_parameters}
    return int(function.lookback)


def compute_raw(spec_name: str, params: Mapping[str, object],
                arrays: Mapping[str, Sequence[Decimal]]) -> dict[str, NDArray[np.float64]]:
    """Run one menu entry over Decimal candle columns and return float64 arrays named by the menu.

    Positions before the library's lookback are NaN (no value yet). Columns the entry does not read are
    ignored. A request outside the menu raises ``ValidationFailure`` before the library is called.
    """
    spec = get_indicator(spec_name)
    resolved = spec.resolve_parameters(params)
    if not isinstance(arrays, Mapping):
        raise ValidationFailure("candle columns must be a mapping of field name to Decimal values")
    missing = [field for field in spec.requires if field not in arrays]
    if missing:
        raise ValidationFailure(f"{spec.name} needs the candle columns: {', '.join(missing)}")
    columns = {field: _float_column(field, arrays[field]) for field in spec.requires}
    if len({column.shape for column in columns.values()}) != 1:
        raise ValidationFailure("the candle columns must all have the same length")
    _require_library_defaults()
    function = getattr(talib, spec.talib_function, None)
    if function is None:
        raise IndicatorComputationError(f"TA-Lib has no function {spec.talib_function}")
    try:
        result = function(*(columns[field] for field in spec.inputs), **resolved, **spec.fixed_parameters)
    except Exception as error:  # the C wrapper reports every library failure as a plain Exception
        raise IndicatorComputationError(f"TA-Lib {spec.talib_function} failed: {error}") from error
    series = result if isinstance(result, tuple) else (result,)
    if len(series) != len(spec.outputs):
        raise IndicatorComputationError(
            f"TA-Lib {spec.talib_function} returned {len(series)} outputs, the menu lists {len(spec.outputs)}")
    return {output.name: np.asarray(values, dtype=np.float64) for output, values in zip(spec.outputs, series)}


def _float_column(field: str, values: Sequence[Decimal]) -> NDArray[np.float64]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValidationFailure(f"{field} must be a sequence of Decimal values")
    if len(values) > MAX_ROWS:
        raise ValidationFailure(f"at most {MAX_ROWS} rows may be computed at once")
    numbers: list[float] = []
    for index, item in enumerate(values):
        if type(item) is not Decimal:
            raise ValidationFailure(f"{field}[{index}] must be a Decimal, not {type(item).__name__}")
        if not item.is_finite():
            raise ValidationFailure(f"{field}[{index}] must be finite")
        number = float(item)
        if not math.isfinite(number) or (number == 0.0 and item != 0):
            raise ValidationFailure(f"{field}[{index}] is outside the range a float can hold")
        if field in _PRICE_FIELDS and number <= 0.0:
            raise ValidationFailure(f"{field}[{index}] must be a positive price")
        if field not in _PRICE_FIELDS and number < 0.0:
            raise ValidationFailure(f"{field}[{index}] must not be negative")
        numbers.append(number)
    return np.fromiter(numbers, dtype=np.float64, count=len(numbers))


def _require_library_defaults() -> None:
    if talib.get_compatibility() != 0:
        raise IndicatorComputationError("TA-Lib is not in its classic compatibility mode")
    for name in _UNSTABLE_PERIODS:
        if talib.get_unstable_period(name) != 0:
            raise IndicatorComputationError(f"TA-Lib unstable period for {name} is not zero; values would change")


__all__ = [
    "MAX_ROWS", "IndicatorComputationError", "columns_from_candles", "compute_raw", "library_versions", "lookback",
]
