"""Adapters from menu entries to two independent indicator libraries (test-only).

``pandas_ta_classic`` (called with ``talib=False``) is the second library of record: it covers every menu
entry. ``talipp`` is a pure-Python library with its own conventions; it covers every entry except MFI and
MOM and is used to expose seeding-convention differences. Neither adapter imports TA-Lib, and
``tests/unit/test_indicator_cross_check.py`` proves that by running the adapters with TA-Lib blocked.

Everything here only translates a menu request into the other library's call and renames its outputs.
"""

from __future__ import annotations

import json
import sys
import warnings
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

Columns = Mapping[str, np.ndarray]
Outputs = dict[str, np.ndarray]


def _series(columns: Columns, name: str) -> pd.Series:
    return pd.Series(np.asarray(columns[name], dtype=np.float64), dtype=np.float64)


def _column(frame: pd.DataFrame, prefix: str) -> np.ndarray:
    matches = [name for name in frame.columns if str(name).startswith(prefix)]
    if len(matches) != 1:
        raise LookupError(f"expected one column starting with {prefix!r}, found {list(frame.columns)}")
    return frame[matches[0]].to_numpy(dtype=np.float64)


def pandas_ta_outputs(name: str, params: Mapping[str, Any], columns: Columns) -> Outputs:
    """Run one menu entry with pandas-ta-classic's own implementation (never its TA-Lib path)."""
    import pandas_ta_classic as pta

    p = dict(params)
    close, high, low, volume = (_series(columns, field) if field in columns else None
                                for field in ("close", "high", "low", "volume"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if name in ("SMA", "EMA", "WMA", "DEMA"):
            function = {"SMA": pta.sma, "EMA": pta.ema, "WMA": pta.wma, "DEMA": pta.dema}[name]
            return {"value": function(close, length=p["timeperiod"], talib=False).to_numpy(dtype=np.float64)}
        if name == "BBANDS":
            upper = pta.bbands(close, length=p["timeperiod"], std=p["nbdevup"], ddof=0, talib=False)
            lower = pta.bbands(close, length=p["timeperiod"], std=p["nbdevdn"], ddof=0, talib=False)
            return {"upper": _column(upper, "BBU"), "middle": _column(upper, "BBM"), "lower": _column(lower, "BBL")}
        if name == "SAR":
            frame = pta.psar(high, low, af0=p["acceleration"], af=p["acceleration"], max_af=p["maximum"])
            long_side, short_side = _column(frame, "PSARl"), _column(frame, "PSARs")
            return {"value": np.where(np.isnan(long_side), short_side, long_side)}
        if name == "MIDPRICE":
            return {"value": pta.midprice(high, low, length=p["timeperiod"], talib=False).to_numpy(dtype=np.float64)}
        if name in ("HIGHEST_HIGH", "LOWEST_LOW"):
            frame = pta.donchian(high, low, lower_length=p["timeperiod"], upper_length=p["timeperiod"])
            return {"value": _column(frame, "DCU" if name == "HIGHEST_HIGH" else "DCL")}
        if name == "RSI":
            return {"value": pta.rsi(close, length=p["timeperiod"], talib=False).to_numpy(dtype=np.float64)}
        if name == "MACD":
            frame = pta.macd(close, fast=p["fastperiod"], slow=p["slowperiod"], signal=p["signalperiod"],
                             talib=False)
            return {"macd": _column(frame, "MACD_"), "signal": _column(frame, "MACDs"),
                    "histogram": _column(frame, "MACDh")}
        if name == "STOCH":
            frame = pta.stoch(high, low, close, k=p["fastk_period"], d=p["slowd_period"], smooth_k=p["slowk_period"],
                              talib=False)
            return {"slow_k": _column(frame, "STOCHk"), "slow_d": _column(frame, "STOCHd")}
        if name == "CCI":
            return {"value": pta.cci(high, low, close, length=p["timeperiod"], talib=False).to_numpy(np.float64)}
        if name == "MFI":
            return {"value": pta.mfi(high, low, close, volume, length=p["timeperiod"],
                                     talib=False).to_numpy(np.float64)}
        if name == "WILLR":
            return {"value": pta.willr(high, low, close, length=p["timeperiod"], talib=False).to_numpy(np.float64)}
        if name == "ROC":
            return {"value": pta.roc(close, length=p["timeperiod"], talib=False).to_numpy(dtype=np.float64)}
        if name == "ROCP":
            # scalar=1 asks pandas-ta-classic for the fraction instead of the percentage
            return {"value": pta.roc(close, length=p["timeperiod"], scalar=1, talib=False).to_numpy(np.float64)}
        if name == "MOM":
            return {"value": pta.mom(close, length=p["timeperiod"], talib=False).to_numpy(dtype=np.float64)}
        if name in ("ADX", "PLUS_DI", "MINUS_DI"):
            frame = pta.adx(high, low, close, length=p["timeperiod"], talib=False)
            prefix = {"ADX": "ADX_", "PLUS_DI": "DMP", "MINUS_DI": "DMN"}[name]
            return {"value": _column(frame, prefix)}
        if name == "AROON":
            frame = pta.aroon(high, low, length=p["timeperiod"], talib=False)
            return {"down": _column(frame, "AROOND"), "up": _column(frame, "AROONU")}
        if name == "ATR":
            return {"value": pta.atr(high, low, close, length=p["timeperiod"], talib=False).to_numpy(np.float64)}
        if name == "NATR":
            return {"value": pta.natr(high, low, close, length=p["timeperiod"], talib=False).to_numpy(np.float64)}
        if name == "OBV":
            return {"value": pta.obv(close, volume, talib=False).to_numpy(dtype=np.float64)}
        if name == "STDDEV":
            return {"value": pta.stdev(close, length=p["timeperiod"], ddof=0, talib=False).to_numpy(np.float64)}
    raise KeyError(name)


def _nan_list(values: list[Any], attribute: str | None = None) -> np.ndarray:
    return np.array([np.nan if item is None else (item if attribute is None else getattr(item, attribute))
                     for item in values], dtype=np.float64)


def talipp_outputs(name: str, params: Mapping[str, Any], columns: Columns) -> Outputs | None:
    """Run one menu entry with talipp, or return None where talipp has no equivalent (MFI, MOM)."""
    from talipp import indicators as ti
    from talipp.ohlcv import OHLCV

    p = dict(params)
    count = len(columns["close"]) if "close" in columns else len(next(iter(columns.values())))
    close = [float(x) for x in columns["close"]] if "close" in columns else None
    rows = [OHLCV(float(columns["open"][i]) if "open" in columns else 0.0,
                  float(columns["high"][i]) if "high" in columns else 0.0,
                  float(columns["low"][i]) if "low" in columns else 0.0,
                  float(columns["close"][i]) if "close" in columns else 0.0,
                  float(columns["volume"][i]) if "volume" in columns else 0.0) for i in range(count)]
    n = p.get("timeperiod")
    if name in ("SMA", "EMA", "WMA", "DEMA", "RSI", "ROC"):
        function = {"SMA": ti.SMA, "EMA": ti.EMA, "WMA": ti.WMA, "DEMA": ti.DEMA, "RSI": ti.RSI, "ROC": ti.ROC}[name]
        return {"value": _nan_list(list(function(n, input_values=close).output_values))}
    if name == "ROCP":
        # talipp reports percent only; the fraction is that percentage divided by 100 (a unit change)
        return {"value": _nan_list(list(ti.ROC(n, input_values=close).output_values)) / 100.0}
    if name == "STDDEV":
        return {"value": _nan_list(list(ti.StdDev(n, input_values=close).output_values))}
    if name == "BBANDS":
        upper = ti.BB(n, p["nbdevup"], input_values=close).output_values
        lower = ti.BB(n, p["nbdevdn"], input_values=close).output_values
        return {"upper": _nan_list(list(upper), "ub"), "middle": _nan_list(list(upper), "cb"),
                "lower": _nan_list(list(lower), "lb")}
    if name == "SAR":
        values = ti.ParabolicSAR(p["acceleration"], p["acceleration"], p["maximum"], input_values=rows).output_values
        return {"value": _nan_list(list(values), "value")}
    if name in ("MIDPRICE", "HIGHEST_HIGH", "LOWEST_LOW"):
        values = list(ti.DonchianChannels(n, input_values=rows).output_values)
        return {"value": _nan_list(values, {"MIDPRICE": "cb", "HIGHEST_HIGH": "ub", "LOWEST_LOW": "lb"}[name])}
    if name == "MACD":
        values = list(ti.MACD(p["fastperiod"], p["slowperiod"], p["signalperiod"], input_values=close).output_values)
        return {"macd": _nan_list(values, "macd"), "signal": _nan_list(values, "signal"),
                "histogram": _nan_list(values, "histogram")}
    if name == "STOCH":
        # talipp's %K is the raw reading and %D its simple average, which is TA-Lib's slow stochastic only
        # when the slow-K smoothing is 1. The cross-check applies it only then (see applies_when).
        values = list(ti.Stoch(p["fastk_period"], p["slowd_period"], input_values=rows).output_values)
        return {"slow_k": _nan_list(values, "k"), "slow_d": _nan_list(values, "d")}
    if name == "CCI":
        return {"value": _nan_list(list(ti.CCI(n, input_values=rows).output_values))}
    if name == "WILLR":
        return {"value": _nan_list(list(ti.Williams(n, input_values=rows).output_values))}
    if name in ("ADX", "PLUS_DI", "MINUS_DI"):
        values = list(ti.ADX(n, n, input_values=rows).output_values)
        return {"value": _nan_list(values, {"ADX": "adx", "PLUS_DI": "plus_di", "MINUS_DI": "minus_di"}[name])}
    if name == "AROON":
        values = list(ti.Aroon(n, input_values=rows).output_values)
        return {"down": _nan_list(values, "down"), "up": _nan_list(values, "up")}
    if name == "ATR":
        return {"value": _nan_list(list(ti.ATR(n, input_values=rows).output_values))}
    if name == "NATR":
        return {"value": _nan_list(list(ti.NATR(n, input_values=rows).output_values))}
    if name == "OBV":
        return {"value": _nan_list(list(ti.OBV(input_values=rows).output_values))}
    return None


def main() -> None:
    """Child process entry: run every adapter with TA-Lib blocked and print the results as JSON."""
    sys.modules["talib"] = None  # type: ignore[assignment]  # any import of TA-Lib now raises ImportError
    request = json.loads(sys.stdin.read())
    columns = {key: np.array(values, dtype=np.float64) for key, values in request["columns"].items()}
    document: dict[str, Any] = {}
    for item in request["requests"]:
        key = f"{item['library']}:{item['name']}:{json.dumps(item['params'], sort_keys=True)}"
        function = pandas_ta_outputs if item["library"] == "pandas_ta_classic" else talipp_outputs
        result = function(item["name"], item["params"], columns)
        document[key] = None if result is None else {out: values.tolist() for out, values in result.items()}
    sys.stdout.write(json.dumps(document))


if __name__ == "__main__":
    main()
