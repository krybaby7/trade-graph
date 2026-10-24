# Indicator menu (T27)

Status: implemented and tested on 2026-10-13, branch `claude/d1-indicators-20261013`. Decision D118 is the
rule behind it: indicators come from a proven library, no indicator formula is written in this repository,
and the Trader receives numbers, not chart images. Slice A delivered the locked dependency, the menu
contract, a thin library wrapper and the cross-check tests. Slice B adds the engine that selects completed
candles from the candle store, supplies warm-up history, reports missing candles per indicator and compares
values with alarm levels, and re-expresses the hourly D89 fields through the library
(`hourly-price-features-v3`). Both are described below.

In plain words: the Trader can pick from 26 standard indicators (averages, RSI, MACD, Bollinger Bands, ATR,
and so on) and choose their settings inside fixed limits. TA-Lib, a well-known open-source library, does
every calculation. The tests check each indicator against a second, separately written library and against
small examples worked out by hand. Where two libraries differ at the start of the history, the difference
is written down below instead of being hidden. Around the library, the engine hands it only completed
candles that were already received at the moment in question, refuses to give a number when any candle it
needs is missing (and says which), and tells the Trader plainly whether a value is ready, still warming up
or blocked by a hole in the data.

Code and data:

| Part | Path |
|---|---|
| Menu contract (names, settings, bounds, outputs, warm-up rule) | `src/trade_graph/contracts/indicators.py` |
| Wrapper around TA-Lib | `src/trade_graph/application/indicators/library.py` |
| Engine (reads the candle store, sizes the history, labels the answer, compares with alarm levels) | `src/trade_graph/application/indicators/engine.py` |
| Hourly price features v3 | `src/trade_graph/application/price_history.py` |
| Tests | `tests/unit/test_indicator_contract.py`, `test_indicator_library.py`, `test_indicator_cross_check.py`, `test_indicator_settling.py`, `test_indicator_reference_values.py`, `test_indicator_menu_doc.py`, `test_indicator_alarm_compare.py`, `tests/property/test_indicator_types.py`, `test_indicator_engine_properties.py`, `tests/integration/test_indicator_engine.py`, `test_hourly_features_v3.py` |
| Fixed series and second-library adapters | `tests/indicator_series.py`, `tests/indicator_second_library.py` |
| Tolerances and documented differences (the table the tests read) | `tests/fixtures/indicators/cross_check.json` |
| Hand-derived reference values with their derivations | `tests/fixtures/indicators/reference_values.json` |
| Stored `hourly-price-features-v2` snapshots (produced by the v2 code) for the reader tests | `tests/fixtures/hourly/snapshots_v2.json` |
| Regenerates the tables in this document | `scripts/render_indicator_menu.py --update` |

## The menu

Every setting has a default, a minimum and a maximum, so every call is valid and bounded. Whole-number
settings (periods) run from 2 to 500, or from 1 where a period of 1 is meaningful (rate of change, momentum,
and the smoothing lengths of the stochastic). Decimal-valued settings have their own limits. Two ordering
rules are enforced because TA-Lib would otherwise quietly swap or ignore values: MACD needs a fast period
below the slow period, and Parabolic SAR needs its acceleration at or below its maximum. Choices the Trader
cannot change (the moving-average type inside Bollinger Bands and the stochastic, and the width multiplier of
the standard deviation) are listed as "fixed".

"Warm-up" is the number of leading candles for which TA-Lib has no value yet. It is not computed in this
repository: `lookback()` asks TA-Lib for it for the chosen settings, and a test checks that the first value
appears exactly there, at the smallest, default and largest settings of every entry.

<!-- BEGIN menu-table -->
| Name | Group | Reads | Settings: default (range) | Outputs | Warm-up at defaults | Start-sensitive |
|---|---|---|---|---|---|---|
| **SMA** (TA-Lib `SMA`) | moving average | close | `timeperiod` 20 (2 to 500) | `value` | 19 candles | no |
| **EMA** (TA-Lib `EMA`) | moving average | close | `timeperiod` 20 (2 to 500) | `value` | 19 candles | yes |
| **WMA** (TA-Lib `WMA`) | moving average | close | `timeperiod` 20 (2 to 500) | `value` | 19 candles | no |
| **DEMA** (TA-Lib `DEMA`) | moving average | close | `timeperiod` 20 (2 to 500) | `value` | 38 candles | yes |
| **BBANDS** (TA-Lib `BBANDS`) | volatility | close | `timeperiod` 20 (2 to 500)<br>`nbdevup` 2 (0.5 to 5)<br>`nbdevdn` 2 (0.5 to 5)<br>fixed `matype`=0 (simple average) | `upper`, `middle`, `lower` | 19 candles | no |
| **SAR** (TA-Lib `SAR`) | trend | high, low | `acceleration` 0.02 (0.001 to 0.5)<br>`maximum` 0.2 (0.01 to 1)<br>rule: `acceleration` <= `maximum` | `value` | 1 candle | yes |
| **MIDPRICE** (TA-Lib `MIDPRICE`) | price range | high, low | `timeperiod` 14 (2 to 500) | `value` | 13 candles | no |
| **HIGHEST_HIGH** (TA-Lib `MAX`) | price range | high | `timeperiod` 20 (2 to 500) | `value` | 19 candles | no |
| **LOWEST_LOW** (TA-Lib `MIN`) | price range | low | `timeperiod` 20 (2 to 500) | `value` | 19 candles | no |
| **RSI** (TA-Lib `RSI`) | momentum | close | `timeperiod` 14 (2 to 500) | `value` | 14 candles | yes |
| **MACD** (TA-Lib `MACD`) | momentum | close | `fastperiod` 12 (2 to 500)<br>`slowperiod` 26 (2 to 500)<br>`signalperiod` 9 (2 to 500)<br>rule: `fastperiod` < `slowperiod` | `macd`, `signal`, `histogram` | 33 candles | yes |
| **STOCH** (TA-Lib `STOCH`) | momentum | high, low, close | `fastk_period` 14 (2 to 500)<br>`slowk_period` 3 (1 to 500)<br>`slowd_period` 3 (1 to 500)<br>fixed `slowk_matype`=0 (simple average)<br>fixed `slowd_matype`=0 (simple average) | `slow_k`, `slow_d` | 17 candles | no |
| **CCI** (TA-Lib `CCI`) | momentum | high, low, close | `timeperiod` 14 (2 to 500) | `value` | 13 candles | no |
| **MFI** (TA-Lib `MFI`) | volume | high, low, close, volume | `timeperiod` 14 (2 to 500) | `value` | 14 candles | no |
| **WILLR** (TA-Lib `WILLR`) | momentum | high, low, close | `timeperiod` 14 (2 to 500) | `value` | 13 candles | no |
| **ROC** (TA-Lib `ROC`) | momentum | close | `timeperiod` 10 (1 to 500) | `value` | 10 candles | no |
| **ROCP** (TA-Lib `ROCP`) | momentum | close | `timeperiod` 10 (1 to 500) | `value` | 10 candles | no |
| **MOM** (TA-Lib `MOM`) | momentum | close | `timeperiod` 10 (1 to 500) | `value` | 10 candles | no |
| **ADX** (TA-Lib `ADX`) | trend | high, low, close | `timeperiod` 14 (2 to 500) | `value` | 27 candles | yes |
| **PLUS_DI** (TA-Lib `PLUS_DI`) | trend | high, low, close | `timeperiod` 14 (2 to 500) | `value` | 14 candles | yes |
| **MINUS_DI** (TA-Lib `MINUS_DI`) | trend | high, low, close | `timeperiod` 14 (2 to 500) | `value` | 14 candles | yes |
| **AROON** (TA-Lib `AROON`) | trend | high, low | `timeperiod` 14 (2 to 500) | `down`, `up` | 14 candles | no |
| **ATR** (TA-Lib `ATR`) | volatility | high, low, close | `timeperiod` 14 (2 to 500) | `value` | 14 candles | yes |
| **NATR** (TA-Lib `NATR`) | volatility | high, low, close | `timeperiod` 14 (2 to 500) | `value` | 14 candles | yes |
| **OBV** (TA-Lib `OBV`) | volume | close, volume | none | `value` | none (a value from the first candle) | yes |
| **STDDEV** (TA-Lib `STDDEV`) | volatility | close | `timeperiod` 20 (2 to 500)<br>fixed `nbdev`=1 | `value` | 19 candles | no |
<!-- END menu-table -->

No entry reads the open price (it is allowed as a column but nothing uses it). Volume is read by MFI and OBV
only.

ROC and ROCP are the same quantity in two units: ROC is a percentage (1.5 means up 1.5 percent) and ROCP is a
fraction (0.015). ROCP is on the menu because the hourly return fields are fractions and nothing may be
converted by arithmetic on library output; the cross-check compares it with the other libraries as a fraction.

Not on the menu, on purpose: TA-Lib's candlestick pattern functions (out of scope by instruction), and
anything TA-Lib does not provide. VWAP in particular is not in TA-Lib; computing it here would be an in-house
formula, which D118 forbids. If the owner wants it, the options are a second library for that one entry or a
decision to allow it.

## Using the wrapper

```python
from trade_graph.application.indicators.library import columns_from_candles, compute_raw, lookback

columns = columns_from_candles(window.candles)          # Decimal columns, exactly as stored
result = compute_raw("MACD", {"fastperiod": 12}, columns)  # {"macd": array, "signal": array, "histogram": array}
lookback("MACD", {})                                    # 33, asked from TA-Lib
```

- The request is checked against the menu before TA-Lib is called: unknown indicator, unknown or misspelled
  setting, wrong type (a float where a whole number is required, a boolean anywhere), out-of-bounds value and
  ordering-rule violations raise `ValidationFailure`.
- Columns must be `Decimal` values. Floats, integers and strings are refused with a message, so a float can
  never enter as a price. Prices must be positive and volume non-negative; NaN, infinity and values a float
  cannot hold are refused. At most 100,000 rows per call (the candle store's own window limit).
- Outputs are `float64` arrays, one per menu output, with one value per candle. Positions before the
  library's lookback are NaN, meaning "no value yet". A history shorter than the lookback gives all NaN, not
  an error. The engine below never hands such a NaN on: it reports `warming_up` instead.
- A missing candle is not handled here. The engine passes only a complete run of consecutive completed
  candles (the candle store's `window` marks missing slots); the wrapper never invents, carries forward or
  zero-fills anything.
- `rounded_decimal(value)` is the one place a library float becomes exact decimal text for stored snapshots
  (rule under "Hourly price features v3" below).
- TA-Lib has process-wide settings (a compatibility mode and per-function "unstable periods") that change
  recursive indicators if anything sets them. The wrapper refuses to compute, with `IndicatorComputationError`,
  if any of them is not at its default.
- `library_versions()` returns the TA-Lib wrapper version, the bundled C library version and NumPy's version
  so a computation can be attributed to them.
- `tests/unit/test_indicator_library.py` contains a syntax-tree check that the wrapper uses numpy only to
  convert and label (`float64`, `fromiter`, `asarray`), so an averaging or smoothing step cannot creep in.

Prices, quantities, fees and balances stay `Decimal` everywhere. A Hypothesis property test
(`tests/property/test_indicator_types.py`) checks, for random valid candles and random in-bounds settings of
every menu entry, that the candle fields and the Decimal columns are unchanged and are still `Decimal` after
the call, that the outputs are `float64` of the right shape, and that any non-Decimal value in a required
column is refused.

## Dependency findings

Checked on 2026-10-13 on this machine (WSL Ubuntu, uv 0.12.23).

| Item | Finding |
|---|---|
| TA-Lib Python package | PyPI name `TA-Lib` (import `talib`). Resolved **0.8.2**, uploaded 2026-10-12, one day before it was locked. The previous release, 0.8.1, is from 2026-09-21. The package moves fast (0.7.0 in July, 0.8.0 in September). BSD-2-Clause. |
| Python 3.12 Linux wheel | Yes: `ta_lib-0.8.2-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl` (4,062,217 bytes). PyPI also lists cp312 wheels for Linux aarch64, musllinux, macOS and Windows (win_amd64, win32, arm64), so the Windows PC and the Linux server both install without compiling. |
| Bundled C library | Yes. The wheel contains `ta_lib.libs/libta-lib-a24660d1.so.1.1.0`; `ldd` on the extension module resolves to that file inside the environment, and `talib.__ta_version__` reports C library **0.8.1**. No system TA-Lib is needed. |
| NumPy | Resolved **2.5.4** (declared directly as `numpy>=2.0`; TA-Lib 0.8 supports NumPy 2 only). |
| Declared constraints | `ta-lib>=0.8.2,<0.9` and `numpy>=2.0` in `[project].dependencies`. The upper bound is deliberate: a new minor version of TA-Lib brings a new C library and can move numbers, so upgrading is a decision with a test run, not a side effect. |
| Clean install | A fresh virtual environment built with `uv sync --frozen --python 3.12` from the committed lock gave Python 3.12.15, TA-Lib 0.8.2 (C 0.8.1) and NumPy 2.5.4. `uv lock --check` reports the lock consistent and `uv run --frozen` works. |
| Lock file | `uv.lock` gains eight packages (numpy, pandas, pandas-ta-classic, python-dateutil, six, ta-lib, talipp, tzdata) and no existing package changed version. uv 0.12.23 also rewrote the header from lock revision 3 to 5 and added resolution markers (NumPy resolves differently for Python 3.14), which comes from the new dependencies (pandas has Python 3.14 specific requirements and tzdata has platform
splits). |
| Test-only packages | In the `dev` dependency group: `pandas-ta-classic>=0.8.32` (MIT), `talipp>=2.7` (MIT) and `pandas>=2.0` (BSD, resolved 3.0.6). The wrapper does not import them. |
| Import boundary | TA-Lib and numpy are imported only in `application/indicators/library.py`. `tests/unit/test_import_boundary.py` now also forbids `talib`, `talipp`, `pandas`, `pandas_ta_classic` and `numpy` in `domain/`, `contracts/` and `kernel/`, and imports every module of those packages in a fresh interpreter to prove that none of the libraries gets loaded through them. |

## Why pandas-ta-classic is the second library

The brief was to pick an independent library that implements its indicators itself, installs on Python 3.12
Linux, is maintained, and covers the menu. Candidates, from PyPI metadata on 2026-10-13; the first three were
also installed and run:

| Library | Version, date | Install and maintenance | Menu coverage | Verdict |
|---|---|---|---|---|
| pandas-ta-classic | 0.8.32, 2026-09-14 | Pure-Python wheel, needs numpy and pandas only; TA-Lib is an optional extra that is off by default. Active. | Every entry | **Chosen** as the second library of record. |
| talipp | 2.7.0, 2025-09-09 | Pure-Python wheel, no dependencies. Last release over a year old but stable. | Every entry except Money Flow Index and momentum | Added as a convention probe (below). |
| stockstats | 0.6.9, 2026-09-28 | Pure-Python wheel, numpy and pandas. Active. | Partial: no WMA, DEMA, OBV, NATR, momentum or Parabolic SAR under the names tried | Not used. |
| ta | 0.11.0, 2023-11-02 | Source distribution only; no release since 2023 | Not evaluated | Stale. |
| finta | 1.3, 2021-04-03 | Unmaintained | Not evaluated | Stale. |
| pandas-ta | 0.4.71b0, 2025-09-14 | Beta; pins an exact numba version | Not evaluated | Too heavy and unstable a pin. |
| tulipy | 0.4.0, 2019-04-11 | Source and Windows wheels only, no Linux wheel | Not evaluated | A truly separate C library, but not installable without compiling. |
| technical | 1.7.0, 2026-07-21 | Depends on TA-Lib | Not evaluated | Not independent. |
| ta-py | 2.1.0, 2026-10-10 | Released three days before this check, unknown provenance | Not evaluated | Too new to trust. |

Why pandas-ta-classic: it is the only candidate that covers every entry, which the acceptance needs ("every
menu entry matches an independent second library"). Its indicators are its own pandas/numpy code. The TA-Lib code paths in the functions we call sit behind an
explicit `talib=True` argument, which the adapters never pass, and its metadata lists TA-Lib only under an
optional extra. The child-process test below proves the adapters never need TA-Lib.

Its weakness is the reason talipp was added. pandas-ta-classic deliberately follows TA-Lib's conventions in
many places (its code even says "matching TA-Lib behaviour"), so agreement with it proves the inputs,
settings and outputs are wired correctly but says little about whose seeding convention is right. talipp has
no TA-Lib code or reference, uses its own conventions, and so exposes the real differences. It is a second
test-only dependency beyond the single one the brief asked for; removing it means deleting one entry in
`pyproject.toml`, `tests/fixtures/indicators/cross_check.json` and the adapter in
`tests/indicator_second_library.py`.

Independence is tested, not assumed (`test_indicator_cross_check.py`):

- every adapter is run in a child process in which `import talib` fails, and the numbers must be identical
  to those computed in the normal process;
- neither library has a hard dependency on TA-Lib in its installed metadata;
- the installed versions must equal the versions recorded in the table, so a changed library forces a
  re-run and a re-read of this document.

## The cross-check

Two fixed synthetic series of 600 candles each are built with integer arithmetic only
(`tests/indicator_series.py`), so every platform produces the same candles. `trend_pullback` is BTC-like
(around 30,000 with long up and down drifts and pullbacks). `range_burst` is alt-coin-like (around 1.2, mean
reverting, with a volatile stretch and a single 8% jump). For every menu entry, at its default settings and
at one to three other settings, TA-Lib's output is compared with each library over the candles where both
have a value. More than 136,000 candle comparisons run in match mode alone.

A difference is the absolute difference divided by a scale: 1 for outputs in points (the 0 to 100 readings),
the largest absolute value for volume, and for every other output its own typical size (the median absolute
TA-Lib value of that output, `tests/indicator_scale.py`). The scale is deliberately not the price: the MACD
histogram and the ATR are small next to it, and a bound stated against the price would let them be wrong by as
much as their own size. Each entry is then judged by one of four modes, all recorded in
`tests/fixtures/indicators/cross_check.json`. The counts below are generated from the data, so they cannot go
stale:

<!-- BEGIN coverage-counts -->
The menu has 26 entries and the reference values hold 50 cases. pandas-ta-classic is compared on 26 of the 26 entries and matches TA-Lib on 24; it differs on MACD, SAR; talipp is compared on 24 of the 26 entries (not MFI, MOM) and matches TA-Lib on 17; it differs on MACD, SAR, ADX, PLUS_DI, MINUS_DI, ATR, NATR. Every difference is documented below.
<!-- END coverage-counts -->

- **match**: the difference stays within 1e-9 (1e-8 for CCI, whose values reach the hundreds).
- **converges**: larger differences early on, bounded by a stated number, then within a stated tolerance (1e-8,
  and 1e-10 for MACD; the observed worst is at least five times smaller for every entry) after a stated number of
  periods past the warm-up. The first version of these bounds was stated against the price, which let the MACD
  histogram be off by about 0.3 percent of its own size and the ATR by about 0.03 percent; against their own size,
  MACD, ATR and NATR needed more periods than first stated, and the table below carries the measured numbers.
- **state dependent**: for Parabolic SAR, which remembers its path. The test requires a minimum share of
  candles in agreement, a longest allowed run of disagreement, a bound on the size of any difference, and
  agreement on whether the stop is above or below the close.
- **not covered**: talipp has no MFI or momentum.

Each documented difference is also tested to be real: if a library stops differing from TA-Lib, the test
fails and the row must be removed. Nothing is patched with a formula of our own.

### Documented differences

<!-- BEGIN differences-table -->
| Library | Entry | Result | What differs and why | TradingView (unverified recollection) |
|---|---|---|---|---|
| pandas-ta-classic | **CCI** | matches to rounding error (within 1e-08) | Rounding error only. CCI is unbounded and often in the hundreds, and it divides by a mean deviation, so the absolute rounding error is larger than for other point-valued entries (observed worst 5e-11). | - |
| pandas-ta-classic | **MACD** | differs early by up to 1 (of the output's size); within 1e-10 after 8 x `slowperiod` candles past the warm-up | pandas-ta-classic runs the fast average as an ordinary EMA from the first candle. TA-Lib starts the fast average late, at the first candle where the slow average exists, from the mean of the fast-length candles ending there (reproduced in test_talib_macd_fast_average_is_seeded_where_the_slow_average_starts using only TA-Lib's own EMA). The lines differ at the start and converge as the earlier seed fades. | Unverified recollection of the Pine Script reference (not checked in this task, not covered by T27 acceptance): ta.macd is recalled as subtracting two ordinary EMAs; how those EMAs are seeded is not known, so whether its early values match this library, TA-Lib or neither is not known. |
| pandas-ta-classic | **SAR** | path dependent: agrees on at least 90% of candles, never disagrees for more than 40 in a row, never by more than 0.03 of its size | Parabolic SAR remembers its trend, extreme point and acceleration, so one early difference can persist until the next reversal. pandas-ta-classic makes its first step after the start with zero acceleration, where TA-Lib uses the initial acceleration (bars 2-10 of trend_pullback). TA-Lib also reverses when a low exactly touches the SAR, while pandas-ta-classic needs the low to go below it: in range_burst the low equals the SAR at bar 407, TA-Lib reverses there and pandas-ta-classic two bars later, and the two paths differ until bar 420. | Unverified recollection of the Pine Script reference (not checked in this task, not covered by T27 acceptance): ta.sar has its own start rules, not known here; treat the first candles of any history, and the candles right after a reversal, as library-specific. |
| talipp | **CCI** | matches to rounding error (within 1e-08) | Rounding error only. CCI is unbounded and often in the hundreds, and it divides by a mean deviation, so the absolute rounding error is larger than for other point-valued entries (observed worst 7e-10). | - |
| talipp | **MFI** | not compared | talipp has no Money Flow Index. | - |
| talipp | **MOM** | not compared | talipp has rate of change but no price-difference momentum. | - |
| talipp | **STOCH** | matches to rounding error, compared only when `slowk_period`=1 | talipp's Stoch returns the raw %K and a simple average of it as %D. That equals TA-Lib's slow stochastic only when the slow-K smoothing is 1, so it is compared only for those settings. | - |
| talipp | **MACD** | differs early by up to 1 (of the output's size); within 1e-10 after 8 x `slowperiod` candles past the warm-up | Same cause as for pandas-ta-classic: talipp runs the fast average as an ordinary EMA from the first candle, TA-Lib seeds it where the slow average begins. | Unverified recollection of the Pine Script reference (not checked in this task, not covered by T27 acceptance): ta.macd is recalled as subtracting two ordinary EMAs; how those EMAs are seeded is not known, so whether its early values match this library, TA-Lib or neither is not known. |
| talipp | **SAR** | path dependent: agrees on at least 88% of candles, never disagrees for more than 60 in a row, never by more than 0.05 of its size | talipp needs four candles before its first SAR and starts from a different initial trend and extreme point than TA-Lib. The paths realign at a later reversal (bar 38 of trend_pullback, bar 420 of range_burst). | Unverified recollection of the Pine Script reference (not checked in this task, not covered by T27 acceptance): ta.sar has its own start rules, not known here; treat the first candles of any history, and the candles right after a reversal, as library-specific. |
| talipp | **ADX** | differs early by up to 5 (of the output's size); within 1e-08 after 24 x `timeperiod` candles past the warm-up | talipp divides its smoothed directional movement by an ATR that counts the first candle's high minus low as a true range and seeds with plain averages of the first N values. TA-Lib seeds the smoothed sums with the first N-1 values and applies the first smoothing step at candle N (found by fitting hand-derived values; TA-Lib documents no formula). Wilder smoothing forgets its seed by a factor (N-1)/N per candle, and ADX smooths twice, so the gap takes about 21 periods to fall below one hundred-millionth of the output (measured). | Unverified recollection of the Pine Script reference (not checked in this task, not covered by T27 acceptance): ta.dmi is recalled as using ta.rma, which starts from a simple average of the first N values, with a first true range of high minus low, which would behave like talipp here. |
| talipp | **PLUS_DI** | differs early by up to 2.5 (of the output's size); within 1e-08 after 20 x `timeperiod` candles past the warm-up | Same seeding difference as ADX; the directional indicators are one smoothing deep, so they settle slightly sooner. | As for ADX. |
| talipp | **MINUS_DI** | differs early by up to 2.5 (of the output's size); within 1e-08 after 20 x `timeperiod` candles past the warm-up | Same seeding difference as ADX; the directional indicators are one smoothing deep, so they settle slightly sooner. | As for ADX. |
| talipp | **ATR** | differs early by up to 0.06 (of the output's size); within 1e-08 after 16 x `timeperiod` candles past the warm-up | talipp counts the first candle's high minus low as a true range and averages candles 0 to N-1 for its first value. TA-Lib's true range needs a previous close, so it starts at the second candle and averages candles 1 to N. After the seed both smooth by Wilder's rule, so the gap shrinks by (N-1)/N per candle. | Unverified recollection of the Pine Script reference (not checked in this task, not covered by T27 acceptance): ta.atr is recalled as smoothing ta.tr with ta.rma, with ta.tr equal to high minus low on the first candle, which would behave like talipp here. |
| talipp | **NATR** | differs early by up to 0.06 (of the output's size); within 1e-08 after 16 x `timeperiod` candles past the warm-up | Normalised ATR is the ATR above divided by the close, so it carries the same seed difference. It is measured in percent against its own size, which is under one percent of the close, so it needs as many periods as the ATR. | As for ATR. |
<!-- END differences-table -->

How to read this. "Of the output's size" is the scale defined above. **The TradingView column is an unverified
recollection and is not part of what T27 demonstrates.** It was written from memory of the Pine Script
reference; no Pine reference text and no TradingView output was read or compared, and no test or dependency
implements TradingView's conventions. A test-only reference implementation of them (an EMA started from the
first close, RMA seeds) would be needed to show the differences it suggests; that was not built. What is verified
is the TA-Lib behaviour, by experiment:

- **MACD.** `test_talib_macd_fast_average_is_seeded_where_the_slow_average_starts` rebuilds TA-Lib's MACD and
  signal line to within 1e-9 of the MACD line's own size using only TA-Lib's own EMA function applied to a later
  slice of the closes, which shows the fast average is seeded at the first candle where the slow average exists.
  Two ordinary EMAs differ from it until candle 136 of the first series (about 100 candles after the warm-up,
  at the 1e-9 level of the line's own size; candle 103 if the price is used as the scale) and then agree.
- **ADX and the directional indicators.** TA-Lib's values were reproduced exactly by hand on a small example
  only when the smoothed sums are seeded with the first N-1 values and the first smoothing step is applied at
  candle N (see `adx-2` in the reference values). The textbook seed (the sum of the first N values) gives
  different early numbers. That seed was found by matching TA-Lib's numbers to a candidate rule, not read from a
  TA-Lib document; the case is labelled as a convention. Which seed TradingView uses is not known.
- **EMA, DEMA and RSI.** TA-Lib, pandas-ta-classic and talipp agree to rounding error on EMA,
  DEMA and RSI: all seed the EMA with a simple average of the first N closes and RSI with Wilder's simple
  averages of the first N changes. Whether TradingView does the same, or starts `ta.ema` from the first close
  (which would make its first values differ from all three and converge), is not known; the seeding of
  smoothed averages is a difference D118 accepts, so it is documented as open, not patched.

## Start-sensitive indicators and how much history to supply

Eleven entries are "start-sensitive": their value at a candle depends on where the history started, because
they smooth recursively or accumulate. The same candle gives slightly different numbers if the run began
earlier or later. This matters for alarms and for comparing a live value with a replay. Two things follow.

First, the engine reads the same span for the same end candle whatever number of points is asked for: a short
request is padded back to the start of the longest one (see "The engine"), so one candle gets one number, bit for
bit, for a request of 1 point, 2 or 60. `tests/integration/test_indicator_engine.py` checks this for every
entry. When the span followed the number of points, the newest MACD histogram of the test series differed by
about 0.03 percent between 1 point and 2, enough to put an alarm level on one side or the other. Readings at
different end candles still start at different places, because the start slides with the end; the guidance
below bounds how much that matters.

Second, the contract carries measured guidance, and the engine adds exactly that much history (see "The
engine"). `Settling` says how many periods of the main setting to add beyond the library lookback so that the
remaining effect of the starting point is below `SETTLE_TOLERANCE` (1e-5) of the size of the output being read:
absolute points for the 0 to 100 readings, and for every other output its own typical size (the median absolute
value), not the price. The first version measured price outputs against the price, which let the MACD and ATR
outputs, which are small next to it, be off by 5 in 10,000 and 3 in 100,000 of their own size and still pass;
against their own size the MACD needs 8 slow periods (not 4) and the ATR 13 (not 8). EMA and DEMA went from 4 to 5 periods for
margin: with a 200-candle setting, 4 periods left 4e-6 to 7e-6 against the 1e-5 limit at some later starts.
`tests/unit/test_indicator_settling.py` measures all of it by running TA-Lib on a full history and on histories
that start later, by 1, 13, 61 and 97 candles and by fractions of the setting itself (up to twice the setting),
for the default, a small, a medium and a large setting of each entry (an EMA of 200, an RSI of 100, an ADX of 60
and so on), on both series. It also checks that a third of the guidance is not enough, so the numbers are not
inflated. This is engineering guidance measured on two synthetic series, not a guarantee for every market.

<!-- BEGIN settling-table -->
| Name | Extra history beyond warm-up | At defaults | Why the start matters |
|---|---|---|---|
| **EMA** | 5 x `timeperiod` | 100 candles | It starts from the plain average of the first N closes and then blends each new close into the previous value, so where the history starts slightly changes every later value. |
| **DEMA** | 5 x `timeperiod` | 100 candles | It is built from two exponential averages that both start from plain averages, so where the history starts slightly changes every later value. |
| **SAR** | none: it never forgets its start | - | The level remembers its trend and speed since the history started, so a different starting point can give a different state until the next reversal. |
| **RSI** | 20 x `timeperiod` | 280 candles | Gains and losses are smoothed with Wilder's method starting from a plain average of the first N changes, so where the history starts slightly changes later values. |
| **MACD** | 8 x `slowperiod` | 208 candles | Fast, slow and signal averages all start from plain averages, so where the history starts slightly changes later values. |
| **ADX** | 24 x `timeperiod` | 336 candles | Directional movement is smoothed twice with Wilder's method, each time started from the first few values, so where the history starts slightly changes later values. |
| **PLUS_DI** | 20 x `timeperiod` | 280 candles | Directional movement is smoothed with Wilder's method started from the first few values, so where the history starts slightly changes later values. |
| **MINUS_DI** | 20 x `timeperiod` | 280 candles | Directional movement is smoothed with Wilder's method started from the first few values, so where the history starts slightly changes later values. |
| **ATR** | 13 x `timeperiod` | 182 candles | The true range is smoothed with Wilder's method starting from a plain average of the first N values, so where the history starts slightly changes later values. |
| **NATR** | 14 x `timeperiod` | 196 candles | The true range is smoothed with Wilder's method starting from a plain average of the first N values, so where the history starts slightly changes later values. |
| **OBV** | none: it never forgets its start | - | It is a running total from the first candle, so its level depends on where the history starts; only changes in OBV are comparable between runs. |
<!-- END settling-table -->

Parabolic SAR and OBV have no settling guidance. OBV never forgets its start: its level moves by a constant
when the history starts a candle later, so only differences between candles are comparable across readings
(`test_on_balance_volume_has_no_settled_level_only_changes_agree_between_starts` shows the offset is one
constant). An alarm level on OBV itself is therefore not meaningful; use its changes. SAR forgets its start at a
reversal, so a fixed amount of extra history works, and it is measured: with 200 candles
(`START_DEPENDENT_HISTORY`) two readings of one candle that start 1, 13 or 61 candles apart agree on every sampled
candle of both series, with 100 they agree on 91.6 to 100 percent of sampled candles depending on the shift, and
with 50 on 89.7 to 99.7 percent for the shifts that fit
(`test_the_fixed_history_given_to_parabolic_sar_makes_nearby_starts_agree`). The first candles of any
history and the candles right after a reversal remain unreliable between runs. Indicators that are not
start-sensitive (moving windows and ranges) agree to rounding error however much history precedes the window;
the settling test checks that too.

## Reference values

`tests/fixtures/indicators/reference_values.json` holds the reference cases (counted in the cross-check section
above) with expected numbers written as exact
fractions or decimals, each with its definition source and a written derivation that can be repeated with a
calculator. Every menu entry, and every output of every entry, has at least one hand-derived case. They use
small settings (3 candles, or 2) so the arithmetic is checkable by eye. Extra "boundary" cases need no
source because the value follows from the definition (RSI of only rises is 100, Williams %R with the close at
the high is 0, a flat series has zero standard deviation, and so on). Three conventions are pinned and
labelled as such in their cases: TA-Lib's MACD seeding, TA-Lib's smoothing seed for ADX and the directional
indicators, and TA-Lib's Parabolic SAR start.

What could not be sourced: no published numeric worked example. The StockCharts ChartSchool pages describe
the formulas, but their numbers live in downloadable spreadsheets (`cs-rsi.xls`, `cs-adx.xls`, `cs-atr.xls`,
`cs-bbands.xls` and similar). Downloading files needs the owner's permission and was not done, so none of
those numbers is in the fixture. All reference numbers are therefore derived from the cited
definitions (Wilder 1978 for RSI, ATR, ADX and SAR; Appel for MACD; Lane, Lambert, Quong and Soudack,
Williams, Chande, Granville, Mulloy and Bollinger for the rest, plus the TA-Lib function list for the
simple ones), then confirmed against TA-Lib. The derivations were first computed in exact rational
arithmetic in a throwaway script that is not committed, and each case writes its derivation out so that a reader
can repeat it by hand from the text. The exceptions are the conventions the definitions do not decide (the
cases with a `convention` field: MACD's fast-average seed, the smoothing seed of ADX and the directional
indicators, and the Parabolic SAR start): there the pinned number is TA-Lib's own, found by matching TA-Lib's
output to a candidate rule, and the case says so. Adding the published ChartSchool examples would need the
spreadsheets supplied or a download approved.

## The engine

In plain words: the engine is the part that stands between the stored candles and the Trader. Ask it for "RSI
14 on BTC/USD, 15-minute candles, as of now" and it gets exactly the candles that indicator needs, checks that
every one of them is complete and was already received, runs the library, and hands back the numbers with a
label saying how much to trust them. If a single candle it needs is missing, it hands back no number at all
and lists the missing times. It never fills a hole, never uses a shorter or longer window to get round one,
and never lets an indicator that is not ready trigger an alarm.

```python
from decimal import Decimal
from trade_graph.application.indicators.engine import compare_to_level, compute_indicator

result = compute_indicator(store, "RSI", {"timeperiod": 14}, symbol="BTC/USD", interval_minutes=15,
                           source="kraken_public_ohlc", end_close_time=close, as_of=now, count=3)
result.status               # "ready", "warming_up" or "gapped"
result.values["value"]      # (61.2, 58.9, 55.0): floats, oldest first, empty unless ready
result.close_times_utc      # the close time each value belongs to
compare_to_level(result, Decimal("30"), "crosses_below")   # a yes or no, with the numbers behind it
```

Code: `application/indicators/engine.py`. Tests: `tests/integration/test_indicator_engine.py` (synthetic
stores: ready, warming up, gap in the middle, gap in the warm-up zone, gap in the seeding history, gap at the
newest slot, replay mode, a candle received after `as_of`, a revision received after `as_of`, all four candle
sizes, bounds), `tests/unit/test_indicator_alarm_compare.py` and `tests/property/test_indicator_engine_properties.py`
(random menu entries, settings and holes; exact comparison).

### Rules

1. **Candles are read only through `CandleStore.window`.** `point_in_time` is the default: a candle is
   visible only if it was received at or before `as_of`. `replay` (research, practice range) uses close times
   only; the result is labelled `mode: replay` and `availability_basis: event_time_only` and is never evidence
   that the data had arrived (`as_of` may be left out in replay mode and then equals the end close time;
   point-in-time reads require it). A test reads the engine's source and fails if it imports TA-Lib, NumPy, pandas or
   sqlite, or touches the store's database or any read other than `window`.
2. **Only completed candles, checked.** The store already promises this; the engine asserts it for every candle
   it receives and raises `CandleSelectionError` if the slots of the window are not consecutive (one interval
   apart), a candle belongs to another slot or interval, a candle is of another symbol or source than the window's,
   a candle was received before it closed, closes after `as_of`, or (in point-in-time mode) was received after
   `as_of`. These checks also guard `compute_from_window`, which takes a window from any caller, so a hand-built
   window with a hole between its slots is refused rather than computed across. The still-forming candle is never
   stored (store contract), so it cannot reach the engine.
3. **How much history is read** is the sum of three numbers, none of them computed here:
   - the library's own lookback for the chosen settings (`lookback()`, asked from TA-Lib);
   - the *seeding history* of a start-sensitive indicator: the menu's measured `Settling` guidance (for
     example 20 x `timeperiod` for RSI), added so that the effect of where the history began stays below
     1e-5 of the size of the output (see "Start-sensitive indicators" below);
   - the number of output points asked for.

   **A start-sensitive entry reads the same span for every `count`.** The span is padded back by the points not
   asked for (`MAX_OUTPUT_POINTS` minus `count`), so it starts at the same candle whatever `count` is, and the
   value at a candle is a function of that candle, not of how many neighbours were requested with it: a request
   for 1 point, 2 or 60 gives the identical number, and an alarm level cannot be on one side of the one-point
   reading and the other side of the sixty-point reading. (The largest request reads this much anyway, so the
   bound below does not move.) The price is history: an RSI of 14 needs 794 candles, not 295. Window entries
   (averages, ranges, bands) need nothing before their window and read `lookback + count`; the library's
   running sums can still differ from one start to another in the last one or two digits, which is why the
   comparison with an alarm level (rule 6) is made at twelve significant digits.

   Parabolic SAR and On-Balance Volume have no measured settling guidance. They get a fixed 200 candles of
   seeding history (`START_DEPENDENT_HISTORY`, above the padding) and the result says `start_dependent: true`.
   For SAR the 200 is measured (see "Start-sensitive indicators"). For OBV no amount of history makes its level
   agree between readings at different end candles, because the history slides with the end: its level is not a
   usable alarm level, so treat OBV as changes only.

   The table after the rules lists the history read at the default settings.

   Bounds: `count` is a whole number from 1 to 500 (`MAX_OUTPUT_POINTS`), and one request may read at most
   20,000 candles (`MAX_HISTORY_CANDLES`; the store's own limit is 100,000). The largest valid menu request
   (ADX with period 500 and 500 points: 999 + 12,000 + 500) needs 13,499, so no valid request is refused by the
   bound; a test checks every entry at its maximum settings. For the same end candle, start-sensitive
   indicators give the identical value however many points are asked for (tested for every entry, 1, 2, 3 and
   17 points against 60); window indicators agree to rounding noise (the same test, to 12 digits).
4. **Missing candles are reported per indicator, and any hole withholds the values.** The engine reads exactly
   the span in rule 3. If any slot of it has no candle:

   | Status | When | Values |
   |---|---|---|
   | `ready` | every slot of the span has a candle | `count` points per output, oldest first, each with its close time |
   | `warming_up` | only the oldest slots are missing, the newest slots are present, and no candle is visible in the stretch of the same length just before the span: the history has begun inside the span | none |
   | `gapped` | any other missing slot: in the middle, in the warm-up or seeding zone, at the newest slot (a collector that stopped), at the oldest slots when candles exist before them, or every slot (a series that does not exist, a feed that stopped long ago, or a date before the series began) | none |

   `missing_close_times_utc` lists every missing slot (a document shows the first 100 and the full count).
   Nothing is carried forward or zero-filled, no partial answer is given (a hole that only touches the oldest
   point's window still withholds all points), and the span is never lengthened or shortened to get around a
   hole: the answer for a span is the answer for that span. Whether a missing slot means "no trades in that
   interval" or "not collected" is unknowable from the store (contract rule 5), so the report says "no
   candle" and does not guess. A span in which no candle is present at all is `gapped`, never `warming_up`:
   nothing the engine saw shows that the history has not begun, and a dead collector must not read as a young
   coin (the price is that a date before the series began is `gapped` too). The only extra read is the
   look-behind that tells `warming_up` from `gapped`; it is one more `window` call of the same length, made only
   when the oldest slots are the only ones missing and at least one candle is present.
   A hole elsewhere in the older candles does not affect a span that does not contain it.
   A request whose span would run off the calendar (a date in year 1, say) or whose settings are absurdly large
   numbers is refused with `ValidationFailure`, like any other bad request.
5. **A result carries its provenance:** indicator, resolved settings, symbol, interval, source, `mode` and
   `as_of`, the status, the number of candles required and available, the lookback and seeding sizes, the
   close time of every value, and for every candle read its revision, collection run and content hash, plus
   one SHA-256 over exactly those (`input_sha256`) so a receipt can show two readings used identical inputs.
   It also carries `spec_version` (the menu version plus a short digest of the entry, so any edit to an
   entry changes it) and the versions of TA-Lib, its C library and NumPy. `to_document()` gives plain JSON
   with ISO times and numbers and never NaN.
6. **Comparing with an alarm level** (`compare_to_level(result, level, relation, output=None)`):

   | Relation | True when |
   |---|---|
   | `above` | newest value > level |
   | `below` | newest value < level |
   | `crosses_above` | previous value <= level < newest value |
   | `crosses_below` | previous value >= level > newest value |

   The level must be a finite `Decimal` (a float, int or string level is refused). Value and level are both
   rounded half-even to 12 significant digits (`SNAPSHOT_DIGITS`, the rule the stored hourly snapshots use) and
   the two Decimals are then compared exactly. The reason is the library's own noise: its running sums add rounding
   error in the last one or two digits, so seven closes of exactly 63000.1 average to 63000.09999999999 and fifty
   of them to 63000.10000000006. Compared bit for bit, an alarm "above 63000.1" would fire on a flat market (19
   of the 32 flat averages in the test, 8 prices at 4 periods, differ from the price they equal); at 12 digits it does not
   (`test_a_real_library_average_of_equal_closes_is_not_above_or_below_that_price`). Two numbers that differ
   at the twelfth digit or earlier are different, and a level with more digits than that is read at twelve.
   Rounding is monotonic, so exactly one of above, below and equal still holds, the comparison is monotonic
   in the value, and a crossing is the two points compared with the same rule (property tests). What this does
   not remove: noise around zero. A level of exactly 0 on an output that is a difference has no significant
   digits to round away: the MACD line of a market flat at 63000.1 reads -3.6e-11, not 0, so "MACD crosses above
   0" can fire on a flat market (measured; momentum, rate of change and the histogram read exactly 0 there).
   Use a level that means something in the market.
   A path that touches the level and then leaves it fires `crosses_*` once; a path that only touches it does
   not. A crossing needs `count` of at least 2. **An indicator that is not ready never satisfies any
   relation**, and neither does one with fewer points than the relation needs; the answer then has
   `evaluated: false` and a reason. A multi-output entry (MACD, BBANDS, STOCH, AROON) needs the output named.
   The answer is a `LevelComparison`: `satisfied` (a plain `bool`), the relation, the level, the value and
   (for crossings) the previous value, the close time the value belongs to, and the reason; its truth value
   follows `satisfied`, so `if compare_to_level(...)` cannot fire on an unready indicator.
7. **Failures are loud.** A request outside the menu raises `ValidationFailure` before the store is read. If
   TA-Lib returns no finite value where its own lookback says there must be one, the engine raises
   `IndicatorComputationError` instead of inventing one.
8. **A market with no movement is not detected; the library's convention shows through.** Where a ratio is 0/0
   (candles with no range and no volume) TA-Lib returns a number, and the engine labels it `ready`, because
   deciding which entries are undefined when would take a formula per entry (D118). On 800 candles of one price
   and zero volume (`test_a_market_with_no_movement_gets_the_librarys_zero_convention_labelled_ready`): RSI, MFI,
   both STOCH lines, Williams %R, CCI, ADX, PLUS_DI and MINUS_DI read 0, AROON reads 100 and 100, the averages,
   SAR, bands and ranges read the price, and ATR, NATR, OBV, STDDEV, momentum and rate of change read 0. An RSI of
   0 normally means every change was a loss and a Williams %R of 0 means the close is at the high; here neither
   is true, nothing moved. The Trader reads a ready oscillator next to the candles it already receives
   (`recent_candles`, `latest_close`), and a run of identical prices on a thin pair is the case to watch for.
   Making these readings unknown would need a per-entry "undefined when flat" rule on the menu; it is left to the
   owner (see the integration notes).

### History read at the default settings

For one value (`count` 1). A window entry reads one more candle for each extra point; a start-sensitive entry
reads the same number whatever the `count` (rule 3).

<!-- BEGIN history-table -->
| Name | Warm-up (asked from TA-Lib) | Seeding guidance | Candles read |
|---|---|---|---|
| **SMA** | 19 | none | 20 for one value, one more for each extra point |
| **EMA** | 19 | 5 x `timeperiod` = 100 | 619, whatever the number of points |
| **WMA** | 19 | none | 20 for one value, one more for each extra point |
| **DEMA** | 38 | 5 x `timeperiod` = 100 | 638, whatever the number of points |
| **BBANDS** | 19 | none | 20 for one value, one more for each extra point |
| **SAR** | 1 | 200 (fixed: it never settles) | 701, whatever the number of points |
| **MIDPRICE** | 13 | none | 14 for one value, one more for each extra point |
| **HIGHEST_HIGH** | 19 | none | 20 for one value, one more for each extra point |
| **LOWEST_LOW** | 19 | none | 20 for one value, one more for each extra point |
| **RSI** | 14 | 20 x `timeperiod` = 280 | 794, whatever the number of points |
| **MACD** | 33 | 8 x `slowperiod` = 208 | 741, whatever the number of points |
| **STOCH** | 17 | none | 18 for one value, one more for each extra point |
| **CCI** | 13 | none | 14 for one value, one more for each extra point |
| **MFI** | 14 | none | 15 for one value, one more for each extra point |
| **WILLR** | 13 | none | 14 for one value, one more for each extra point |
| **ROC** | 10 | none | 11 for one value, one more for each extra point |
| **ROCP** | 10 | none | 11 for one value, one more for each extra point |
| **MOM** | 10 | none | 11 for one value, one more for each extra point |
| **ADX** | 27 | 24 x `timeperiod` = 336 | 863, whatever the number of points |
| **PLUS_DI** | 14 | 20 x `timeperiod` = 280 | 794, whatever the number of points |
| **MINUS_DI** | 14 | 20 x `timeperiod` = 280 | 794, whatever the number of points |
| **AROON** | 14 | none | 15 for one value, one more for each extra point |
| **ATR** | 14 | 13 x `timeperiod` = 182 | 696, whatever the number of points |
| **NATR** | 14 | 14 x `timeperiod` = 196 | 710, whatever the number of points |
| **OBV** | 0 | 200 (fixed: it never settles) | 700, whatever the number of points |
| **STDDEV** | 19 | none | 20 for one value, one more for each extra point |
<!-- END history-table -->

## Hourly price features v3

The hourly features the Trader already receives (decision D89, window rules from D114) were hand-written
Decimal arithmetic: an in-house formula for every one of them. D118 forbids that, so
`hourly-price-features-v3` computes them with the library. The windows, the shared anchor, the per-field
statuses (`ready`, `stale`, `gapped_history`, `insufficient_history`, `unsupported`), the per-field missing
hours, `latest_close`, `recent_candles` and the snapshot hash rule are unchanged. Since W6-D (D220) an installation
with the price collector reads them from the candle store's 60-minute series (`CandleStoreHistory`, for any coin, point
in time, the snapshot saying `history_store: "candle_store"`); one without it still reads the `hourly_candles` table.
The numbers come from the same code either way.

<!-- BEGIN hourly-table -->
| Field | hourly-price-features-v2 (hand-written) | hourly-price-features-v3 | Why |
|---|---|---|---|
| `sma_20` | Mean of the last 20 closes, summed and divided in Decimal | TA-Lib `SMA` (timeperiod 20) over 20 completed hourly candles | Same window and meaning; computed by the library. |
| `sma_50` | Mean of the last 50 closes, summed and divided in Decimal | TA-Lib `SMA` (timeperiod 50) over 50 completed hourly candles | Same window and meaning; computed by the library. |
| `highest_high_20` | (new in v3) | TA-Lib `MAX` (timeperiod 20) over 20 completed hourly candles | The 20-hour high that the pullback ratio was built on. With `latest_close` it gives the same information without a hand-written ratio. |
| `range_high` | Maximum high of the last 24 candles, scanned in Python | TA-Lib `MAX` (timeperiod 24) over 24 completed hourly candles | Same window and meaning; computed by the library. |
| `range_low` | Minimum low of the last 24 candles, scanned in Python | TA-Lib `MIN` (timeperiod 24) over 24 completed hourly candles | Same window and meaning; computed by the library. |
| `midpoint` | (range_high + range_low) / 2 in Decimal | TA-Lib `MIDPRICE` (timeperiod 24) over 24 completed hourly candles | Same window and meaning; computed by the library. |
| `hourly_return` | Latest close / previous close - 1 in Decimal | TA-Lib `ROCP` (timeperiod 1) over 2 completed hourly candles | Same meaning; ROCP is the fractional change, so no percent conversion is needed. |
| `four_hour_drift` | Latest close / close four hours earlier - 1 in Decimal | TA-Lib `ROCP` (timeperiod 4) over 5 completed hourly candles | Same meaning; ROCP over 4 hours. |
| `pullback_from_high` | (20-hour maximum high - latest close) / maximum high in Decimal | **retired**: read highest_high_20 with latest_close | No library function gives this ratio, and a hand-written one is an in-house formula (D118). Its ingredients are supplied instead. |
| `pullback_depth` | Alias of pullback_from_high | **retired**: read highest_high_20 with latest_close | Same as pullback_from_high, of which it was an alias. |
| `midpoint_distance` | (latest close - midpoint) / midpoint in Decimal | **retired**: read midpoint with latest_close | No library function gives this ratio (D118). Its ingredients are supplied instead. |
<!-- END hourly-table -->

**What happened to the three ratios.** `pullback_from_high`, `pullback_depth` and `midpoint_distance` are
each one division of two library numbers. No library function returns them, and writing the division here
would be an in-house formula, so the options were to keep a hand-written ratio (against D118), to ask for a
second library (none offers it on the menu), or to retire the ratios and supply their ingredients. The third
was chosen: `highest_high_20` (TA-Lib `MAX` over the last 20 completed highs) is new, `midpoint` and
`latest_close` were already supplied, and the Trader (or an alarm level it sets) reads the gap between them.
A request for a retired name is answered with status `unsupported` and a definition that names its
replacement, and the replacement numbers (`highest_high_20` for the two pullback names, `midpoint` for the
distance) are supplied in the same snapshot as if they had been requested, with their own `ready` entries. That
last part matters for installations that already exist: the new `runtime_artifacts.json` and
`strategies/templates/slow_trend_pullback.json` ask for `highest_high_20`, but `VersionController.ensure` keeps
the active baseline a database already has, so such a database keeps the previous template, which asks for
`pullback_from_high` and never for `highest_high_20`. Without the supply it would have received neither the ratio
nor its ingredient (`test_an_installation_that_kept_the_v2_slow_trend_template_still_gets_usable_inputs` uses the
previous template as a fixture). The ratio itself is never a value. The other fields do not change, except that the
shared lookback follows the ingredients as it would if they had been asked for. The artifact grammar still accepts
the retired names, because a retained bundle has to keep validating; a new Engineer bundle that names one gets the
same answer (unsupported plus the ingredient). The cleaner remedy for an existing database is an owner-approved
replacement of its stored baseline with the new templates (a migration or a reset), which this branch does not do.
If the owner wants the ratios back as numbers, that is a decision to allow a labelled in-house division or to find
a library that provides it.

**Exact text, one rule.** The library returns floats; the snapshot keeps exact decimal text so hashing stays
stable. The rule is `rounded_decimal`: take the float's exact binary value, round it half-even to 12
significant digits using a Decimal context of its own (the ambient context cannot change it), and write it
in plain notation without trailing zeros (`-0` becomes `0`). Why 12: a float64 carries about 15.95 digits and
the library's running sums add rounding noise in the last one or two, so rounding at 12 makes the text identical
across platforms and compilers, while every price the hourly pairs quote (at most 7 digits) passes through
unchanged (`range_high`, `range_low`, `highest_high_20` and `midpoint` are exactly the candle prices).

How exact the numbers are, stated as measured:

- **The four selection fields** (`highest_high_20`, `range_high`, `range_low`, `midpoint`) are exact for inputs
  of up to 11 significant digits (`EXACT_INPUT_DIGITS`): the value is an input price, or half the sum of two,
  and fits the 12-digit text. v2 returned such a price exactly whatever its digits, and the candle contract allows
  up to 128 characters, so for a window with a price of more than 11 significant digits these fields are
  `unsupported` with a definition that says why, instead of a value that was silently rounded
  (`test_a_price_with_more_digits_than_the_stored_text_carries_is_withheld_not_silently_rounded`). No price a
  hourly pair quotes comes near that.
- **The averages** (`sma_20`, `sma_50`) are within 5e-12 of their value, relative.
- **The two returns** (`hourly_return`, `four_hour_drift`) are within 5e-12 of their value, relative, **plus 2.5e-16
  absolute**. The absolute part comes from the float64 subtraction of two nearly equal prices and does not shrink
  with the return, so a very small return has fewer than 12 trustworthy digits: a one-tick move of BTC/USD from
  63000.1 to 63000.2 is stored as 0.00000158729906776 against the exact 0.000001587299067779..., a relative error
  of 1.2e-11. The earlier statement that every value was within 6e-12 relative was true only for the moves of the
  test (up to 2 percent a candle), where the return is large; for returns below roughly 4e-4 the absolute term
  dominates. In trading terms the error is about 2e-16, immaterial; the digits shown are not all meaningful.
  The feature definitions say so, and `test_returns_after_tiny_moves_stay_within_the_stated_absolute_bound` checks
  the bound on 30 tick-sized random walks at five price levels.

The v2 arithmetic is kept as an oracle in the tests only: for 36 seeded random series (BTC-like, ETH-like and a
5-decimal coin, moves up to 2 percent a candle) every v3 value agrees with the v2 exact value within 6e-12
relative, and the four selection fields agree exactly. A value rounded across a 12-digit boundary by a one-bit
platform difference would change the text (probability roughly 1 in 10,000 per value); a changed text changes
that snapshot's hash, never a stored one.

**Retained snapshots.** `hourly-price-features-v2` snapshots and contexts that are already stored are never
recomputed and stay as they were. `FEATURE_VERSION_V2` remains in `price_history.py`. The evidence reader keeps
every v2 name in its allow-list and the artifact grammar still accepts a template that names
`pullback_from_high`. `tests/fixtures/hourly/snapshots_v2.json` holds three snapshots produced by the v2 code
(complete history, pending newest hour, gapped and short history); `test_hourly_features_v3.py` checks that each
still hashes to its stored `source_ref`, passes unchanged through the context compaction, the department context
filter and the evidence reader with every name, value, status and definition intact, and that a v3 snapshot
of the same candles has the same provenance and the same exact values where nothing changed.

**Deployment note.** `price_history.py` now imports TA-Lib and NumPy (through the wrapper), which the
runtime did not need before. A deployed image has to be built with those wheels; this branch changes no image and
touches no running service.

## How the Trader is meant to use the menu

The Trader never sees a chart and never calculates an indicator. In its day plan it names entries from the
menu and their settings (for example "RSI 14 on 15-minute candles", "EMA 20 and EMA 50 on 5-minute candles",
"Bollinger Bands 20, 2 deviations") and sets alarm levels on their numbers ("RSI crosses below 30"). Software
does the rest:

1. For each chosen indicator the engine returns the numbers (a few recent points, so a direction can be read),
   the close time of each, and the status. The Trader reads them as plain numbers next to `latest_close` and the
   recent candles it already receives. Where the status is `warming_up` or `gapped` it gets no value and the
   missing times, and says nothing about what the value might have been.
2. Between wakes, software evaluates each alarm when a new candle closes with `compare_to_level`. The alarm
   fires once, and only from a ready indicator. A hole in the data produces a data-problem note, never a trade
   signal.
3. A start-sensitive indicator gives the same number for a candle whatever `count` is asked, so the view the
   Trader reads and the alarm that watches the same candle cannot disagree; OBV is read as changes and SAR as
   reversals, not as OBV levels. A ready oscillator on a market with no movement is read next to the candles
   (rule 8).
4. Everything the Trader acted on is attributable afterwards: the document carries the candle revisions, the run
   ids, the `as_of`, the mode and the versions.

Wiring the engine into the Trader context, the alarm watcher and the day plan is later work; this slice
delivers the engine and its tests only.

## Changing the menu

1. Edit `INDICATOR_MENU` in `contracts/indicators.py`. The contract tests, the library tests (function name,
   input count, output count, parameter names, fixed values, lookback at the minimum, default and maximum
   settings) and the property test pick the entry up automatically.
2. Add the entry to `variants` in `tests/fixtures/indicators/cross_check.json`, add adapters for both
   libraries in `tests/indicator_second_library.py`, and add hand-derived cases to `reference_values.json`.
   The tests fail until each exists.
3. If it is start-sensitive, measure and add `settle` guidance and a line in `_VARIANTS` in
   `test_indicator_settling.py`.
4. Run `uv run --frozen python scripts/render_indicator_menu.py --update` and commit the document.

Upgrading TA-Lib, pandas-ta-classic or talipp: change the constraint, run `uv lock`, run the indicator tests
(`tests/unit/test_indicator_*.py`, `tests/property/test_indicator_types.py`), then update `version_checked` in
`cross_check.json` and this document.
