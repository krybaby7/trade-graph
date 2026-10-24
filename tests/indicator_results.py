"""A hand-built ``IndicatorResult`` for tests that exercise alarm comparison without a candle store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trade_graph.application.indicators.engine import IndicatorResult

RESULT_END = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def make_result(values, *, status="ready", outputs=("value",), indicator="RSI") -> IndicatorResult:
    """A result as the engine would hand over; ``values`` is a list for one output or a dict of lists."""
    series = {outputs[0]: tuple(values)} if not isinstance(values, dict) else {k: tuple(v) for k, v in values.items()}
    points = len(next(iter(series.values()))) if series and status == "ready" else 0
    if status != "ready":
        series = {}
    return IndicatorResult(
        indicator=indicator, parameters={}, spec_version="indicator-menu-v1/test", library={"ta_lib_python": "0"},
        symbol="BTC/USD", interval_minutes=15, source="synthetic", mode="point_in_time",
        availability_basis="receipt_time", as_of_utc=RESULT_END, end_close_time_utc=RESULT_END, status=status,
        requested_points=max(points, 1), lookback_candles=14, seeding_candles=0, required_candles=15,
        available_candles=15 if status != "warming_up" else 3, start_dependent=False,
        close_times_utc=tuple(RESULT_END - timedelta(minutes=15 * k) for k in reversed(range(points))),
        values=series, missing_close_times_utc=() if status == "ready" else (RESULT_END,), candles_used=(),
        input_sha256="0" * 64)
