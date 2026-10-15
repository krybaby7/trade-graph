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
| pandas-ta-classic | 0.8.32, 2026-09-14 | Pure-Python wheel, needs numpy and pandas only; TA-Lib is an optional extra that is off by default. Active. | All 25 | **Chosen** as the second library of record. |
| talipp | 2.7.0, 2025-09-09 | Pure-Python wheel, no dependencies. Last release over a year old but stable. | 23 of 25 (no Money Flow Index, no momentum) | Added as a convention probe (below). |
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

A difference is the absolute difference divided by a scale: the median close for outputs in price units, 1
for outputs in points or percent, and the largest absolute value for volume. Each entry is then judged by one
of four modes, all recorded in `tests/fixtures/indicators/cross_check.json`:

- **match**: the difference stays within 1e-9 (1e-8 for CCI, whose values reach the hundreds). Result:
  pandas-ta-classic matches on 23 of 25 entries (worst 5e-11, CCI); talipp matches on 16 (worst 7e-10, CCI)
  and differs on the rest as documented below.
- **converges**: larger differences early on, bounded by a stated number, then within 1e-6 after a stated
  number of periods past the warm-up.
- **state dependent**: for Parabolic SAR, which remembers its path. The test requires a minimum share of
  candles in agreement, a longest allowed run of disagreement, a bound on the size of any difference, and
  agreement on whether the stop is above or below the close.
- **not covered**: talipp has no MFI or momentum.

Each documented difference is also tested to be real: if a library stops differing from TA-Lib, the test
fails and the row must be removed. Nothing is patched with a formula of our own.

### Documented differences

<!-- BEGIN differences-table -->
| Library | Entry | Result | What differs and why | TradingView |
|---|---|---|---|---|
| pandas-ta-classic | **CCI** | matches to rounding error (within 1e-08) | Rounding error only. CCI is unbounded and often in the hundreds, and it divides by a mean deviation, so the absolute rounding error is larger than for other point-valued entries (observed worst 5e-11). | - |
| pandas-ta-classic | **MACD** | differs early by up to 0.002 (scaled); within 1e-06 after 3 x `slowperiod` candles past the warm-up | pandas-ta-classic runs the fast average as an ordinary EMA from the first candle. TA-Lib starts the fast average late, at the first candle where the slow average exists, from the mean of the fast-length candles ending there (reproduced in test_talib_macd_fast_average_is_seeded_where_the_slow_average_starts using only TA-Lib's own EMA). The lines differ at the start and converge as the earlier seed fades. | TradingView's ta.macd subtracts two ordinary EMAs, like pandas-ta-classic (per the Pine Script reference; not re-checked in this task). |
| pandas-ta-classic | **SAR** | path dependent: agrees on at least 90% of candles, never disagrees for more than 40 in a row, never by more than 0.03 of price | Parabolic SAR remembers its trend, extreme point and acceleration, so one early difference can persist until the next reversal. pandas-ta-classic makes its first step after the start with zero acceleration, where TA-Lib uses the initial acceleration (bars 2-10 of trend_pullback). TA-Lib also reverses when a low exactly touches the SAR, while pandas-ta-classic needs the low to go below it: in range_burst the low equals the SAR at bar 407, TA-Lib reverses there and pandas-ta-classic two bars later, and the two paths differ until bar 420. | TradingView's ta.sar has its own start rules (not checked); treat the first candles of any history, and the candles right after a reversal, as library-specific. |
| talipp | **CCI** | matches to rounding error (within 1e-08) | Rounding error only. CCI is unbounded and often in the hundreds, and it divides by a mean deviation, so the absolute rounding error is larger than for other point-valued entries (observed worst 7e-10). | - |
| talipp | **MFI** | not compared | talipp has no Money Flow Index. | - |
| talipp | **MOM** | not compared | talipp has rate of change but no price-difference momentum. | - |
| talipp | **STOCH** | matches to rounding error, compared only when `slowk_period`=1 | talipp's Stoch returns the raw %K and a simple average of it as %D. That equals TA-Lib's slow stochastic only when the slow-K smoothing is 1, so it is compared only for those settings. | - |
| talipp | **MACD** | differs early by up to 0.002 (scaled); within 1e-06 after 3 x `slowperiod` candles past the warm-up | Same cause as for pandas-ta-classic: talipp runs the fast average as an ordinary EMA from the first candle, TA-Lib seeds it where the slow average begins. | TradingView's ta.macd subtracts two ordinary EMAs, like talipp (per the Pine Script reference; not re-checked in this task). |
| talipp | **SAR** | path dependent: agrees on at least 88% of candles, never disagrees for more than 60 in a row, never by more than 0.05 of price | talipp needs four candles before its first SAR and starts from a different initial trend and extreme point than TA-Lib. The paths realign at a later reversal (bar 38 of trend_pullback, bar 420 of range_burst). | TradingView's ta.sar has its own start rules (not checked); treat the first candles of any history, and the candles right after a reversal, as library-specific. |
| talipp | **ADX** | differs early by up to 8 (scaled); within 1e-06 after 24 x `timeperiod` candles past the warm-up | talipp divides its smoothed directional movement by an ATR that counts the first candle's high minus low as a true range and seeds with plain averages of the first N values. TA-Lib seeds the smoothed sums with the first N-1 values and applies the first smoothing step at candle N (found by fitting hand-derived values; TA-Lib documents no formula). Wilder smoothing forgets its seed by a factor (N-1)/N per candle, and ADX smooths twice, so the gap takes about 16 periods to fall below one millionth (measured). | TradingView's ta.dmi uses ta.rma, which starts from a simple average of the first N values, and its first true range is high minus low (per the Pine Script reference; not re-checked in this task), so it behaves like talipp here. |
| talipp | **PLUS_DI** | differs early by up to 4 (scaled); within 1e-06 after 20 x `timeperiod` candles past the warm-up | Same seeding difference as ADX; the directional indicators are one smoothing deep, so they settle slightly sooner. | As for ADX. |
| talipp | **MINUS_DI** | differs early by up to 4 (scaled); within 1e-06 after 20 x `timeperiod` candles past the warm-up | Same seeding difference as ADX; the directional indicators are one smoothing deep, so they settle slightly sooner. | As for ADX. |
| talipp | **ATR** | differs early by up to 0.001 (scaled); within 1e-06 after 8 x `timeperiod` candles past the warm-up | talipp counts the first candle's high minus low as a true range and averages candles 0 to N-1 for its first value. TA-Lib's true range needs a previous close, so it starts at the second candle and averages candles 1 to N. After the seed both smooth by Wilder's rule, so the gap shrinks by (N-1)/N per candle. | TradingView's ta.atr smooths ta.tr with ta.rma, and ta.tr is high minus low on the first candle (per the Pine Script reference; not re-checked in this task), so it behaves like talipp here. |
| talipp | **NATR** | differs early by up to 0.1 (scaled); within 1e-06 after 14 x `timeperiod` candles past the warm-up | Normalised ATR is the ATR above divided by the close, so it carries the same seed difference. It is measured in percent, where one millionth is a tighter bar than for price-unit outputs, so it needs more periods. | As for ATR. |
<!-- END differences-table -->

How to read this. "Scaled" is the scale defined above. The TradingView column is from the Pine Script
reference as the author remembers it; TradingView was not available to check in this task, so treat it as
unverified. What is verified is the TA-Lib behaviour, by experiment:

- **MACD.** `test_talib_macd_fast_average_is_seeded_where_the_slow_average_starts` rebuilds TA-Lib's MACD and
  signal line to within 1e-9 of price using only TA-Lib's own EMA function applied to a later slice of the
  closes, which shows the fast average is seeded at the first candle where the slow average exists. Two
  ordinary EMAs differ from it until about candle 110 (about 80 candles after the warm-up, at the 1e-9
  level) and then agree.
- **ADX and the directional indicators.** TA-Lib's values were reproduced exactly by hand on a small example
  only when the smoothed sums are seeded with the first N-1 values and the first smoothing step is applied at
  candle N (see `adx-2` in the reference values). The textbook seed (the sum of the first N values) gives
  different early numbers. Which one TradingView uses is the unverified part.
- **EMA, DEMA and RSI.** TA-Lib, pandas-ta-classic and talipp agree to rounding error on EMA,
  DEMA and RSI: all seed the EMA with a simple average of the first N closes and RSI with Wilder's simple
  averages of the first N changes. TradingView's `ta.ema` is believed to start from the first close instead
  (so its first values differ from all three and converge); this is the "seeding of smoothed averages"
  difference D118 accepts, and it is documented, not patched.

## Start-sensitive indicators and how much history to supply

Eleven entries are "start-sensitive": their value at a candle depends on where the history started, because
they smooth recursively or accumulate. The same candle gives slightly different numbers if the run began
earlier or later. This matters for alarms and for comparing a live value with a replay. Two things follow.

First, for the same end candle, supply the same history length every time (or at least beyond the guidance
below), so two readings of the same indicator agree.

Second, the contract carries measured guidance, and the engine adds exactly that much history (see "The
engine"). `Settling` says how many periods of the main setting to add beyond the library lookback so that the
remaining effect of the starting point is below `SETTLE_TOLERANCE` (1e-5 of the output scale).
`tests/unit/test_indicator_settling.py` measures it by running TA-Lib on a full history and on histories that
start 1, 13 and 61 candles later, for the default and two other settings of each entry, on both series. It
also checks that a third of the guidance is not enough, so the numbers are not inflated. This is engineering
guidance measured on two synthetic series, not a guarantee for every market.

<!-- BEGIN settling-table -->
| Name | Extra history beyond warm-up | At defaults | Why the start matters |
|---|---|---|---|
| **EMA** | 4 x `timeperiod` | 80 candles | It starts from the plain average of the first N closes and then blends each new close into the previous value, so where the history starts slightly changes every later value. |
| **DEMA** | 4 x `timeperiod` | 80 candles | It is built from two exponential averages that both start from plain averages, so where the history starts slightly changes every later value. |
| **SAR** | none: it never forgets its start | - | The level remembers its trend and speed since the history started, so a different starting point can give a different state until the next reversal. |
| **RSI** | 20 x `timeperiod` | 280 candles | Gains and losses are smoothed with Wilder's method starting from a plain average of the first N changes, so where the history starts slightly changes later values. |
| **MACD** | 4 x `slowperiod` | 104 candles | Fast, slow and signal averages all start from plain averages, so where the history starts slightly changes later values. |
| **ADX** | 24 x `timeperiod` | 336 candles | Directional movement is smoothed twice with Wilder's method, each time started from the first few values, so where the history starts slightly changes later values. |
| **PLUS_DI** | 20 x `timeperiod` | 280 candles | Directional movement is smoothed with Wilder's method started from the first few values, so where the history starts slightly changes later values. |
| **MINUS_DI** | 20 x `timeperiod` | 280 candles | Directional movement is smoothed with Wilder's method started from the first few values, so where the history starts slightly changes later values. |
| **ATR** | 8 x `timeperiod` | 112 candles | The true range is smoothed with Wilder's method starting from a plain average of the first N values, so where the history starts slightly changes later values. |
| **NATR** | 14 x `timeperiod` | 196 candles | The true range is smoothed with Wilder's method starting from a plain average of the first N values, so where the history starts slightly changes later values. |
| **OBV** | none: it never forgets its start | - | It is a running total from the first candle, so its level depends on where the history starts; only changes in OBV are comparable between runs. |
<!-- END settling-table -->

Parabolic SAR and OBV never forget their start. For OBV only differences between candles are comparable
across runs. For SAR, treat the first candles of any history and the candles right after a reversal as
unreliable between runs. Indicators that are not start-sensitive (moving windows and ranges) agree to
rounding error however much history precedes the window; the settling test checks that too.

## Reference values

`tests/fixtures/indicators/reference_values.json` holds 47 cases with expected numbers written as exact
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
those numbers is in the fixture. All reference numbers are therefore derived by hand from the cited
definitions (Wilder 1978 for RSI, ATR, ADX and SAR; Appel for MACD; Lane, Lambert, Quong and Soudack,
Williams, Chande, Granville, Mulloy and Bollinger for the rest, plus the TA-Lib function list for the
simple ones), then confirmed against TA-Lib. The derivations were first computed in exact rational
arithmetic in a throwaway script that is not committed; a reader can repeat them by hand from the text.
Adding the published ChartSchool examples would need the spreadsheets supplied or a download approved.

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
   it receives and raises `CandleSelectionError` if a candle belongs to another slot, was received before it
   closed, closes after `as_of`, or (in point-in-time mode) was received after `as_of`. The still-forming candle
   is never stored (store contract), so it cannot reach the engine.
3. **How much history is read** is the sum of three numbers, none of them computed here:
   - the library's own lookback for the chosen settings (`lookback()`, asked from TA-Lib);
   - the *seeding history* of a start-sensitive indicator: the menu's measured `Settling` guidance (for
     example 20 x `timeperiod` for RSI), added so that the effect of where the history began stays below
     1e-5 of the output scale (see "Start-sensitive indicators" below);
   - the number of output points asked for.

   Parabolic SAR and On-Balance Volume never forget their start and have no measured guidance. They get a
   fixed 200 candles of seeding history (`START_DEPENDENT_HISTORY`) and the result says
   `start_dependent: true`. Their levels are not comparable between readings with a different `count`; ask
   for the same `count` every time, and treat OBV as changes only.

   The table after the rules lists the history read at the default settings.

   Bounds: `count` is a whole number from 1 to 500 (`MAX_OUTPUT_POINTS`), and one request may read at most
   20,000 candles (`MAX_HISTORY_CANDLES`; the store's own limit is 100,000). The largest valid menu request
   (ADX with period 500 and 500 points: 999 + 12,000 + 500) needs 13,499, so no valid request is refused by the
   bound; a test checks every entry at its maximum settings. For the same end candle, windowed indicators give
   the same value however many points are asked for; settling indicators agree within the 1e-5 tolerance
   (tested for RSI, 1 point against 60).
4. **Missing candles are reported per indicator, and any hole withholds the values.** The engine reads exactly
   the span in rule 3. If any slot of it has no candle:

   | Status | When | Values |
   |---|---|---|
   | `ready` | every slot of the span has a candle | `count` points per output, oldest first, each with its close time |
   | `warming_up` | only the oldest slots are missing, and no candle is visible in the stretch of the same length just before the span: the history has not begun | none |
   | `gapped` | any other missing slot: in the middle, in the warm-up or seeding zone, at the newest slot (a collector that stopped), or at the oldest slots when candles exist before them | none |

   `missing_close_times_utc` lists every missing slot (a document shows the first 100 and the full count).
   Nothing is carried forward or zero-filled, no partial answer is given (a hole that only touches the oldest
   point's window still withholds all points), and the span is never lengthened or shortened to get around a
   hole: the answer for a span is the answer for that span. Whether a missing slot means "no trades in that
   interval" or "not collected" is unknowable from the store (contract rule 5), so the report says "no
   candle" and does not guess. The only extra read is the look-behind that tells `warming_up` from `gapped`;
   it is one more `window` call of the same length, made only when the oldest slots are the only ones missing.
   A hole elsewhere in the older candles does not affect a span that does not contain it.
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

   The level must be a finite `Decimal` (a float, int or string level is refused). The float indicator value
   is converted to Decimal as its shortest decimal string that converts back to the same float (`repr`), and
   the two Decimals are compared exactly: no tolerance, no rounding. This way a level of at most 15 significant
   digits equals the float nearest to it (the value 0.1 is not "above" the level 0.1), where the binary
   expansion of the float would say it is above by a hair; and 0.1 + 0.2 really is above 0.3, because the
   float is 0.30000000000000004. Property tests check that exactly one of above, below and equal holds, that
   the comparison is monotonic in the value, and that a crossing is the two points compared with the same rule.
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

### History read at the default settings

For one value (`count` 1); each extra point adds one candle.

<!-- BEGIN history-table -->
| Name | Warm-up (asked from TA-Lib) | Seeding history | Candles read for one value |
|---|---|---|---|
| **SMA** | 19 | none | 20 |
| **EMA** | 19 | 4 x `timeperiod` = 80 | 100 |
| **WMA** | 19 | none | 20 |
| **DEMA** | 38 | 4 x `timeperiod` = 80 | 119 |
| **BBANDS** | 19 | none | 20 |
| **SAR** | 1 | 200 (fixed: it never forgets its start) | 202 |
| **MIDPRICE** | 13 | none | 14 |
| **HIGHEST_HIGH** | 19 | none | 20 |
| **LOWEST_LOW** | 19 | none | 20 |
| **RSI** | 14 | 20 x `timeperiod` = 280 | 295 |
| **MACD** | 33 | 4 x `slowperiod` = 104 | 138 |
| **STOCH** | 17 | none | 18 |
| **CCI** | 13 | none | 14 |
| **MFI** | 14 | none | 15 |
| **WILLR** | 13 | none | 14 |
| **ROC** | 10 | none | 11 |
| **ROCP** | 10 | none | 11 |
| **MOM** | 10 | none | 11 |
| **ADX** | 27 | 24 x `timeperiod` = 336 | 364 |
| **PLUS_DI** | 14 | 20 x `timeperiod` = 280 | 295 |
| **MINUS_DI** | 14 | 20 x `timeperiod` = 280 | 295 |
| **AROON** | 14 | none | 15 |
| **ATR** | 14 | 8 x `timeperiod` = 112 | 127 |
| **NATR** | 14 | 14 x `timeperiod` = 196 | 211 |
| **OBV** | 0 | 200 (fixed: it never forgets its start) | 201 |
| **STDDEV** | 19 | none | 20 |
<!-- END history-table -->

## Hourly price features v3

The hourly features the Trader already receives (decision D89, window rules from D114) were hand-written
Decimal arithmetic: an in-house formula for every one of them. D118 forbids that, so
`hourly-price-features-v3` computes them with the library. The windows, the shared anchor, the per-field
statuses (`ready`, `stale`, `gapped_history`, `insufficient_history`, `unsupported`), the per-field missing
hours, `latest_close`, `recent_candles` and the snapshot hash rule are unchanged. The hourly features still read
the `hourly_candles` table; they are not wired to the new candle store (that is a later step).

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
replacement; it does not widen the lookback and does not change any other field. If the owner wants the
ratios back as numbers, that is a decision to allow a labelled in-house division or to find a library that
provides it.

**Exact text, one rule.** The library returns floats; the snapshot keeps exact decimal text so hashing stays
stable. The rule is `rounded_decimal`: take the float's exact binary value, round it half-even to 12
significant digits using a Decimal context of its own (the ambient context cannot change it), and write it
in plain notation without trailing zeros (`-0` becomes `0`). Why 12: a float64 carries about 15.95 digits and
the library's running sums add rounding noise in the last one or two, so rounding at 12 makes the text identical
across platforms and compilers, while every price the hourly pairs quote (at most 7 digits) passes through
unchanged (`range_high`, `range_low`, `highest_high_20` and `midpoint` are exactly the candle prices). The cost
is a relative rounding error of at most 5e-12 on the derived values (averages and returns). The v2
arithmetic is kept as an oracle in the tests only: for 36 seeded random series (BTC-like, ETH-like and a
5-decimal coin) every v3 value agrees with the v2 exact value within 6e-12 relative, and the four exact ones
agree exactly. A value rounded across a 12-digit boundary by a one-bit platform difference would change the
text (probability roughly 1 in 10,000 per value); a changed text changes that snapshot's hash, never a
stored one.

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
3. A start-sensitive indicator is read at the same `count` each time, so successive readings agree; SAR and OBV
   are read as changes and reversals, not as levels.
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
