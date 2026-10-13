# Indicator menu (T27, slice A)

Status: implemented and tested on 2026-10-13, branch `claude/d1-indicators-20261013`. Decision D118 is the
rule behind it: indicators come from a proven library, no indicator formula is written in this repository,
and the Trader receives numbers, not chart images. This slice delivers the locked dependency, the menu
contract, a thin library wrapper and the cross-check tests. The engine code that selects completed candles
from the candle store, supplies warm-up history, reports missing candles per indicator and compares values
with alarm levels, and the retirement of the hourly D89 fields, is slice B and is not described here.

In plain words: the Trader can pick from 25 standard indicators (averages, RSI, MACD, Bollinger Bands, ATR,
and so on) and choose their settings inside fixed limits. TA-Lib, a well-known open-source library, does
every calculation. The tests check each indicator against a second, separately written library and against
small examples worked out by hand. Where two libraries differ at the start of the history, the difference
is written down below instead of being hidden.

Code and data:

| Part | Path |
|---|---|
| Menu contract (names, settings, bounds, outputs, warm-up rule) | `src/trade_graph/contracts/indicators.py` |
| Wrapper around TA-Lib | `src/trade_graph/application/indicators/library.py` |
| Tests | `tests/unit/test_indicator_contract.py`, `test_indicator_library.py`, `test_indicator_cross_check.py`, `test_indicator_settling.py`, `test_indicator_reference_values.py`, `test_indicator_menu_doc.py`, `tests/property/test_indicator_types.py` |
| Fixed series and second-library adapters | `tests/indicator_series.py`, `tests/indicator_second_library.py` |
| Tolerances and documented differences (the table the tests read) | `tests/fixtures/indicators/cross_check.json` |
| Hand-derived reference values with their derivations | `tests/fixtures/indicators/reference_values.json` |
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
  an error. Slice B turns NaN into an explicit "unknown" for the Trader.
- A missing candle is not handled here. Slice B must pass only a complete run of consecutive completed
  candles (the candle store's `window` marks missing slots); the wrapper never invents, carries forward or
  zero-fills anything.
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

Second, the contract carries measured guidance. `Settling` says how many periods of the main setting to add
beyond the library lookback so that the remaining effect of the starting point is below `SETTLE_TOLERANCE`
(1e-5 of the output scale). `tests/unit/test_indicator_settling.py` measures it by running TA-Lib on a full
history and on histories that start 1, 13 and 61 candles later, for the default and two other settings of
each entry, on both series. It also checks that a third of the guidance is not enough, so the numbers are not
inflated. This is engineering guidance measured on two synthetic series, not a guarantee for every market.

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
