# W5-B T41: Price charts in HQ with TradingView Lightweight Charts

For the owner: HQ's Money tab now has a "Price charts" panel that draws each coin the team is working with (today's
day plans, coins held, coins with alarms, the scanner's shortlist, and BTC and ETH for reference) as candles at
1 minute, 5 minutes, 15 minutes or 1 hour, with the indicators the Trader chose, the alarm lines, the waiting buys,
the safety exits and every fill, and with every line also written out in words underneath. Everything comes from
the system's own saved records and from a copy of TradingView's free charting library kept inside Trade Graph, so
the page never loads anything from TradingView or anywhere else, and the line under every chart credits TradingView
and links to its website and licence as the licence asks. Nothing needs a decision from you; at step L2 or L3,
please open the Money tab on your own phone and iPad once the price collector is running, because the charts were
checked in a test browser with made-up prices only.

Branch: `claude/t41-hq-charts`, head: the commit that adds this line; code at `9942f99`, documents at `6116a3e`. Pull request:
https://github.com/krybaby7/trade-graph/pull/12 (not merged).

## What was built

- `src/trade_graph/web/static/vendor/lightweight-charts/`: TradingView Lightweight Charts **5.2.1** (Apache-2.0,
  the registry's `latest`, published 2026-08-12), byte for byte from the npm registry tarball
  `https://registry.npmjs.org/lightweight-charts/-/lightweight-charts-5.2.1.tgz`, fetched 2026-10-17 and refused
  unless its SHA-512 equals the registry integrity
  `sha512-IVwoK1RLFiLPubaKIjNbtjWLnpPMqiABSrTay6whmNa8L1+19292VtHJ+BWyPUuLCwF0tcQlhEWd1CLB2a1nsQ==`:
  `lightweight-charts.standalone.production.js` (SHA-256
  `e21cc5caa0226ef30bd8549c50b9ef926615f2a4ee6b4e486353477a55f598cf`, 197,922 bytes), `LICENSE.txt` (the
  tarball's `LICENSE`, SHA-256 `70c9d5382506dd184465425c08a99ad9bd6d9ac1313c252968ba0b585e5ef823`) and
  `NOTICE.txt` (the `NOTICE` at GitHub tag `v5.2.1` from raw.githubusercontent.com, SHA-256
  `f76c6afab94884448f0426e30d6e9d555ca7247894cd3484e477d2f87513036e`), with `VENDOR.json` recording the version,
  URLs, date, hashes and the script's SRI value
  `sha384-KkYqTZlM13Zya6fVUF3IGCBOQ1ehFeJVVgxaVvD/0MebineF9EI8Jkd3NnWNChqN`.
- `scripts/vendor_lightweight_charts.py`: `--check` (offline: the committed copy matches its pins and the page
  script pins the right integrity; run by the tests) and `--fetch` (network: downloads the pinned release and
  verifies it; used once on 2026-10-17). Standard library only; never runs at HQ runtime.
- `src/trade_graph/api/hq_chart_contract.py`: the `trade-graph.hq.chart.v1` document (Pydantic, strict): coins with
  reasons, the window of 300 candle slots, candles as `[open time, open, high, low, close, volume]` in exact decimal
  text, missing slots, indicators (status, words, values lined up with the slots), lines (alarm, entry, stop,
  take-profit, order), markers (buy, sell, alarm), notes. Fixed vocabularies: nothing in it can name a colour, a
  script or an address.
- `src/trade_graph/api/hq_chart.py`: the projection `chart(runtime, symbol=None, interval=None)` and the route
  `GET /api/v1/hq/chart` (any signed-in role, reads only, validated `symbol` and `interval`); `PLACEMENT` (which menu
  entries are drawn on the prices); `ChartCandleFile` (the dashboard's handle on the candle file).
- `src/trade_graph/adapters/persistence/market_data_readonly.py`: `ReadOnlyMarketData`, the candle file opened with
  SQLite's read-only mode: never created or upgraded, private folder and file, no symlinks, every write refused, one
  read snapshot per request.
- `src/trade_graph/dashboard.py`, `cli.py`, `api/app.py`: `trade-graph dashboard --market-database` (default
  `runtime/market_data.sqlite`), the paper dashboard runtime's `chart_candles`, the route registration, and closing
  the file on shutdown. A live dashboard has no charts ("not connected").
- Page: `web/templates/hq.html` (a "Price charts" panel in Money after Market weather, with the attribution line,
  and `hq-charts.js` in the script list), `web/static/hq-charts.js` (the drawing; module `charts`),
  `hq-core.js` (the `chart` read endpoint), `hq.js` (starts it), `hq-glossary.js` and `api/hq_copy.py` (section
  help "charts"), `hq.css` (theme tokens only, phone and tablet heights).
- `scripts/build_protected_image.py`: `.txt` added to the package suffixes the image accepts (the licence and notice).
  `pyproject.toml`: the vendor folder in the package data.
- `docs/HQ-DASHBOARD.md` (owner guide section, data rule, route), `docs/90-SOURCES.md` (S46).
- Tests: `tests/unit/test_hq_chart_contract.py`, `tests/integration/test_hq_chart.py`,
  `tests/integration/test_hq_chart_routes.py`, `tests/integration/test_market_data_readonly.py`,
  `tests/test_hq_charts_static.py`, `tests/js/hq_charts.test.mjs` (run by `tests/test_hq_js_behaviour.py`), helper
  `tests/chart_support.py`. Changed: `tests/test_hq_static.py` (the new script, the `/api/v1/hq/chart` read and the
  one allowed outside link).

## Decisions (reserved block D195–D199)

- **D195 (2026-10-17, implementation default for T41): one pinned, vendored release, loaded only when a chart is
  shown.** Lightweight Charts 5.2.1, the standalone production build, is kept under
  `web/static/vendor/lightweight-charts/` with its licence and notice (named `.txt`, because the protected image
  accepts only known suffixes; `.txt` was added there) and `VENDOR.json`; `scripts/vendor_lightweight_charts.py`
  is the only way it changes and checks it offline. The page fetches it from HQ's own origin with a pinned
  Subresource Integrity value the first time the Price charts panel is on screen (an `IntersectionObserver`; on a
  phone, when the Money tab opens), never on page load, because HQ sends `Cache-Control: no-store` and a 198 KB
  script on every page load would cost phones for nothing. No outside script or data request at runtime (the
  browser check saw none). Reason: the T41 deliverable "pinned, vendored ... served from HQ's own origin under the
  existing content security policy", and the policy stays `script-src 'self'`.
- **D196 (2026-10-17, implementation default for T41): the attribution is HQ's own line, the library's logo is
  off.** The library's `attributionLogo` injects a `<style>` element, which HQ's `style-src 'self'` refuses, and its
  link carries `utm_source=<HQ's host and path>`, which would tell TradingView the owner's private address. So it is
  off (`attributionLogo: false`), which the library's own documentation allows when the link requirement is met
  otherwise. Under every chart box the panel shows: "Charts drawn with TradingView Lightweight Charts™ (Apache License
  2.0). TradingView Lightweight Charts™ · Copyright (c) 2025 TradingView, Inc." (the NOTICE text; upstream types a
  Cyrillic "с" in "(с)", shown as a Latin "c"), a plain `https://www.tradingview.com/` link (`rel="noopener
  noreferrer"`, no tracking parameters) and links to the vendored licence and notice. The script draws no chart
  when that line is missing. This link is the only outside address on the page; `tests/test_hq_static.py` now
  allows exactly it. Reason: the T41 acceptance "the attribution link and licence notice are visible wherever a
  chart is shown" and the README's licence section (S46).
- **D197 (2026-10-17, implementation default for T41): what a chart shows, all from saved records.** A separate
  endpoint, `GET /api/v1/hq/chart`, so the HQ snapshot (65.9–76.1 KB of its 80 KB, W2-C) does not grow; the page
  asks only while the panel is on screen and refreshes every minute. Coins: today's day-plan coins, held coins (the
  ledger's open lots), coins with an armed alarm (this portfolio or none), the newest live scan's shortlist if that
  scan is under 24 hours old and ranked something (a newer failed scan means no shortlist), then BTC/USD and
  ETH/USD; a coin not on the list is not charted. Candles: the last 300 slots of the collector's source
  (`kraken_public_ohlc`, what the Trader's indicators and alarms read) through `CandleStore.window`, point in time
  as of now; a missing slot stays an empty gap; the chart ends at the newest received candle when it is at most two
  slots behind (the collector stores a candle seconds after it closes), otherwise at now, so a stopped feed shows
  its empty slots. Indicators ("indicator outputs" in the acceptance): the T27 engine's numbers
  (`compute_indicator`, 300 points) over the same candles for the coin's day-plan indicators on this candle size and
  the indicators its armed indicator alarms watch; a gap or a young coin withholds the whole indicator, as for the
  Trader, and the legend says why; indicators chosen for another size are named. Nothing is computed in the browser
  or by a third party; `hq_chart.py` has no formula (checked with `tests/indicator_guard.py`). Lines: armed alarms
  (quote and candle levels on the prices; an indicator alarm on the prices for averages and bands, on its own
  indicator's panel otherwise, or in words when that panel is not drawn); open orders: day-plan resting entries
  (limit, with the planned stop and target in words), safety exits (stop and take-profit), other open orders; an
  order in `UNKNOWN`, `SUBMITTING` or `CANCEL_PENDING` is drawn and labelled "being checked". Marks: the coin's
  fills and alarm fires inside the window (the newest 200; the legend says when there were more). Alarm notes
  (model text) are not shown. Prices, quantities and levels are exact decimal text; indicator values are rounded to
  eight significant digits for drawing; only the canvas turns text into numbers. Reading writes nothing (tested on
  both databases). Older state databases without the alarm, day-plan or scan tables still chart.
- **D198 (2026-10-17, implementation default for T41): how the chart looks.** A "Price charts" panel in the Money
  tab after Market weather (no new tab), with a coin picker that says why each coin is listed and four candle-size
  buttons. Candles are hollow when the price rose and filled when it fell (a shape, not colour alone); colours come
  from the theme's tokens (`--success`, `--danger`, `--accent`, `--warning`, `--muted`, `--text`), so light and
  dark follow the Theme toggle and the system setting (the chart redraws on a change). Averages and bands draw on
  the prices without labels (the legend names them; labels crowded a phone), other indicators in their own panels
  below, the price panel three times their height. Line styles: alarms dashed amber, resting buys solid blue,
  stops dashed red, targets dashed green, other orders dotted; the legend repeats each line in words with its style.
  The price scale stretches to include resting buys, exits and other orders within a tenth of the newest close, so
  the money at stake is on screen; alarm lines show once the price nears them (all are in the legend). Times are in
  the viewer's time zone; a readout under the chart shows the hovered or newest candle's exact saved prices. The page
  keeps scrolling over the chart (no wheel or vertical-swipe capture); drag sideways to look back, pinch or drag the
  time axis to zoom. No inline style anywhere: heights come from CSS by the number of panels.
- **D199 (2026-10-17, implementation default for T41): HQ only reads the candle file.** The dashboard opens the
  file given by `--market-database` (default `runtime/market_data.sqlite`) read-only on the first chart and again
  on later charts after a refusal, so a file the collector creates later is picked up without a restart. It refuses
  a missing file (never creates one), a file or folder that is not private (the store's own 600/700 rule), a
  symlink, a file that is not a candle file and one that still needs a candle-file upgrade (never upgrades it); the
  chart then says why in plain words and still lists the alarm and order levels. A live dashboard has no charts.
  Reason: AGENTS.md (reads start nothing), D110 (cheap reads, no locks), and the candle store's privacy rules
  (docs/CANDLE-STORE.md).

## Evidence

All runs on CPython 3.12.13 (`uv sync --frozen --group dev --python 3.12`) in this cloud container, in
`/home/user/claude/t41-hq-charts`. No network in any test, no key, paid, model or exchange call.

- Tests written first, run before the code existed: `tests/unit/test_hq_chart_contract.py` and
  `tests/integration/test_hq_chart.py` failed at collection (`cannot import name 'hq_chart_contract'`; `No module
  named 'trade_graph.adapters.persistence.market_data_readonly'`); `tests/test_hq_charts_static.py` (5 of 6) and the
  changed `tests/test_hq_static.py` (7 cases: the script list, the new script's rules, the allowed link, the fetch
  allowlist, storage) failed; `node --test tests/js/hq_charts.test.mjs`: 12 of 12 failed (no `charts` module). The
  read-only, route and dashboard tests were written alongside their modules. Mistakes the first runs found: the
  busy chart was 84 KB (indicator digits cut from 10 to 8 and candles made compact rows: 69 KB); cross-realm values
  in the JS assertions; a test fixture's unpacking.
- New and directly related tests: `uv run pytest -p no:cacheprovider -o addopts="--import-mode=importlib" -q -rfs
  tests/integration/test_hq_chart.py tests/integration/test_hq_chart_routes.py
  tests/integration/test_market_data_readonly.py tests/unit/test_hq_chart_contract.py tests/test_hq_charts_static.py
  tests/test_hq_static.py tests/test_hq_template.py`: **130 passed, 0 failed, 0 skipped** (40 s).
  `node --test tests/js/hq_charts.test.mjs`: **12 passed, 0 failed**.
- Related tests (every test module that imports or exercises changed code: `create_app`, `dashboard_runtime`, the
  CLI, `hq_copy`, the HQ pages, scripts and static files, `pyproject.toml` packaging, the image builder, plus all
  `tests/integration/test_hq_*.py`, `test_dashboard_*.py`, `tests/unit/test_hq_*.py`, the owner check-in tests and
  `tests/test_hq_js_behaviour.py`, which runs every `tests/js/hq_*.test.mjs`; 77 modules): `uv run pytest -p
  no:cacheprovider -o addopts="--import-mode=importlib" -q -rfs <modules>` on the Python code of `9942f99` (unchanged since `8484ef3`): **1,444 passed, 0 failed, 1 skipped** (the existing `tests/archive_support.py:104` skip: the container runs as root, which can read any file). After the last script change (`9942f99`): `tests/test_hq_js_behaviour.py tests/test_hq_static.py tests/test_hq_charts_static.py` **62 passed**; `node --test tests/js/hq_*.test.mjs` **67 passed, 0 failed**.
- `uv run pytest -p no:cacheprovider -q tests/unit` at `9942f99`: **2,156 passed, 0 failed, 7 skipped** (71 s; the skips: 4 indicator cross-checks the second library cannot make, 3 archive-permission cases as root).
- `uv run ruff check src tests`: all checks passed. `python3 scripts/check_plan.py`: plan valid, 46 tasks;
  `python3 scripts/test_planning.py`: 11 tests OK; `python3 scripts/check_repository_hygiene.py` (and `--staged`
  before every commit): passed. `python3 scripts/vendor_lightweight_charts.py --check`: the vendored copy matches
  its pins.
- Full suite: not run (night shift: the wave integration runs it).
- Browser check at `9942f99` (real vendored library, real page, real FastAPI app, synthetic records): Chromium from
  Playwright 1.56 (`/opt/pw-browsers`) against the app served on 127.0.0.1 over a synthetic database (the test
  account with one SOL day plan, three plan alarms and a Leader alarm, one filled entry with its safety exit) and a
  candle file of synthetic candles at all four sizes (one 5-minute gap). At phone 390×844, iPad portrait 820×1180,
  iPad landscape 1180×820 and desktop 1280×900, each in light and dark (system setting), then switching to 1 hour
  and flipping the Theme toggle: the chart drew (canvases present, two panels for RSI), the attribution line and its
  TradingView link were visible, no library logo and no `<style>` element was added, no Content Security Policy
  violation, no console error or warning, no request outside HQ's origin, no horizontal page overflow, and the
  chart followed the toggle. Screenshots stayed in the session's scratch folder, not the repository. Not checked:
  Safari/WebKit (only Chromium is installed), a real iPad, and real Kraken candles.
- Size and speed: a busy synthetic chart (8 indicators with 14 outputs, 59 lines, 300 candles, 2,400 candles of
  history) is 70,192 bytes compact (budget 80 KB, `hq_chart.PAYLOAD_BUDGET_BYTES`) and takes about 0.2 s; the HQ
  snapshot is unchanged.
- Real or synthetic: everything is synthetic and labelled (the T34 test account, deterministic synthetic candles in
  runs labelled `synthetic_fixture`, a synthetic scan). The library itself is the real published release, verified
  by its registry integrity. No paid, model or exchange call; the only network use was the one-off vendoring fetch.

## Schema and dependencies

- State migration: none (0036 not used). Candle database: none (HQ only reads it).
- Dependencies added: none to Python. One vendored browser library (above), not a package dependency.

## Not done, open or for someone else

- **Owner / step L2–L3:** open HQ's Money tab on your phone and iPad once the price collector fills
  `runtime/market_data.sqlite`, in light and dark, and say if anything is hard to read. Start the dashboard with
  `--market-database` if the candle file lives elsewhere.
- **W6-D (C2, install readiness):** the runtime layout should give the dashboard process read access to the candle
  file (the same private folder rules), and `scripts/build_protected_image.py` now accepts `.txt` package files
  (the chart licence and notice); keep that when adding the NumPy and TA-Lib wheels. The dashboard process now
  imports the indicator engine (TA-Lib and NumPy) for charts.
- **W6-C (T39, HQ wake log) and the integrator — shared files:** `web/templates/hq.html` (new panel in Money, new
  script before `hq.js`), `web/static/hq-core.js` (`chart` read and `chartQuery`), `hq.js` (one `init` line),
  `hq-glossary.js` and `api/hq_copy.py` (`charts` section help), `hq.css` (a chart block before the phone rules),
  `tests/test_hq_static.py` (`SCRIPTS`, `FETCH_ALLOWLIST`, the one allowed outside link), `api/app.py` (one import,
  one `register`), `cli.py` and `dashboard.py` (`--market-database`). `api/hq_contract.py`, the snapshot and the
  nine `tests/fixtures/hq/snapshot_*.json` are unchanged. `docs/90-SOURCES.md` S46 may need renumbering if another
  wave 5 package also added S46.
- **Integrator:** copy D195–D199 into `docs/DECISIONS.md`; mark T41 `done` if the evidence holds.
- **Later (not in T41):** gzip or long-lived caching for `/static/vendor/` (today every page load that opens a
  chart fetches the 198 KB script again, because HQ sends `no-store`); a chart for a coin outside the list; showing
  alarm notes as labelled quotes; the shortlist's 24-hour age is a display rule, not the coin rule's 2-hour age.
