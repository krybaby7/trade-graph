"""One indicator value per candle of a long window, each exactly what the engine would answer at that candle (T29).

A replay decides at every completed candle, so it needs, for each candle, the newest value the engine would give
for ``count=1`` ending there. Asking the engine candle by candle would read and convert the same span thousands of
times. ``rolling_latest`` reads nothing itself: it takes one window already read through ``CandleStore.window``,
converts it to float64 once, and for each candle runs the library over exactly the span the engine would read
(``plan_history(name, params, 1).span`` candles ending there). So:

- the value at a candle is float for float the engine's ``latest()`` there (tests compare them), including the
  start-sensitive entries, whose span the engine pads to a fixed start;
- a candle whose span holds a missing slot, or reaches before the window's first slot, has no value (``None``),
  as the engine withholds a gapped or warming-up indicator. Nothing is carried forward or partly computed.

No indicator formula is written here (D118): every number comes from ``library.compute_prepared``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from trade_graph.application.indicators.engine import _assert_completed, plan_history, spec_version
from trade_graph.application.indicators.library import (
    IndicatorComputationError,
    compute_prepared,
    float_columns,
    library_versions,
)
from trade_graph.contracts.candles import CandleWindow
from trade_graph.contracts.indicators import get_indicator
from trade_graph.domain.errors import ValidationFailure


@dataclass(frozen=True, slots=True)
class RollingSeries:
    """Per-slot newest values of one menu entry over one window; ``None`` where the engine would give no value."""

    indicator: str
    parameters: Mapping[str, int | float]
    spec_version: str
    library: Mapping[str, str]
    span: int
    close_times: tuple[datetime, ...]
    values: Mapping[str, tuple[float | None, ...]]

    def output_name(self, output: str | None) -> str:
        names = get_indicator(self.indicator).output_names
        if output is not None:
            if output not in names:
                raise ValidationFailure(f"{self.indicator} has no output {output!r}")
            return output
        if len(names) != 1:
            raise ValidationFailure(f"{self.indicator} has several outputs ({', '.join(names)}); name one")
        return names[0]

    def at(self, index: int, output: str | None = None) -> float | None:
        return self.values[self.output_name(output)][index]

    def unready_count(self) -> int:
        """Candles of the window that have no value (the same for every output)."""
        first = next(iter(self.values.values()))
        return sum(1 for value in first if value is None)


def rolling_latest(name: str, params: Mapping[str, object] | None, window: CandleWindow) -> RollingSeries:
    spec = get_indicator(name)
    resolved = spec.resolve_parameters({} if params is None else params)
    span = plan_history(name, resolved, 1).span
    _assert_completed(window)
    slots = window.slots
    candles = [None if slot.stored is None else slot.stored.candle for slot in slots]
    columns = float_columns(candles)
    missing_before = [0]
    for candle in candles:
        missing_before.append(missing_before[-1] + (candle is None))
    outputs = spec.output_names
    series: dict[str, list[float | None]] = {output: [] for output in outputs}
    for index in range(len(slots)):
        start = index - span + 1
        if start < 0 or missing_before[index + 1] - missing_before[start]:
            for output in outputs:
                series[output].append(None)
            continue
        raw = compute_prepared(name, resolved, {field: column[start:index + 1] for field, column in columns.items()})
        for output in outputs:
            value = float(raw[output][-1])
            if not math.isfinite(value):
                raise IndicatorComputationError(
                    f"{name} {output} has no finite value where the library lookback says it should")
            series[output].append(value)
    return RollingSeries(
        indicator=name, parameters=MappingProxyType(dict(resolved)), spec_version=spec_version(spec),
        library=MappingProxyType(library_versions()), span=span,
        close_times=tuple(slot.close_time_utc for slot in slots),
        values=MappingProxyType({output: tuple(values) for output, values in series.items()}))


__all__ = ["RollingSeries", "rolling_latest"]
