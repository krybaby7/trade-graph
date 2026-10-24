# W6-D C2: Code ready to install: one price collector, new data store wired in

For the owner: The code for installing the new engine is ready: once installed, the service itself keeps the price
history current (the coins the team works with as their candles close, every coin's hourly candles and a fresh coin
shortlist every hour, the wider history once a day), so the two Windows price tasks can be removed, and the Trader
reads its hourly numbers from that same price history, for any coin. The AI Trader can now answer a wake-up with
its day plans, and the new event engine sits behind one setting that ships switched off and that you turn on during
the install, together with a written checklist (`docs/L3-INSTALL-CHECKLIST.md`) that names every command in order.
Nothing was installed or run against Kraken from the cloud; at step L3 you approve each step, and Research and the
Leader can only move to Perplexity once the extra wiring listed in that checklist exists.

Branch: `claude/c2-install-ready`, head: the commit that adds this handoff's pull request link (code and tests
at `0be53b5`, docs at `49e225a`). Pull request: [#17](https://github.com/krybaby7/trade-graph/pull/17).

## What was built

- **One limiter (D221).** `adapters/market/rate_limit.py`: `KrakenRestLimiter` is the only Kraken request limiter. It
  is thread-safe (charged under a lock, never sleeping inside it) and now has the backfill's two rules: spacing
  (`min_interval_seconds`) and `penalize` (a rate-limit or throttle answer: the counter is taken to be at Kraken's 15
  and waited out, never under 5 seconds or a stated throttle time, refused beyond 120 seconds with
  `RateLimitWaitTooLong`), plus `settings`, `penalties`, `waited_seconds` and `service_limiter()` (the service's one
  instance: starts at the ceiling, monotonic clock, wall-clock throttle times, 1-second spacing).
  `adapters/market/trades_rate_limit.py`: `RequestLimiter` is now that class with the backfill's documented settings
  (its numbers and tests are unchanged); `LimiterSettings` and `RateLimitWaitTooLong` moved with it.
  `application/backfill_trades.py` takes any `KrakenRestLimiter`.
- **Hourly features from the candle store (D220).** `application/price_history.py`: the v3 snapshot is computed by one
  function from either store; `CandleStoreHistory` reads the candle store's 60-minute series point in time for any
  coin and marks its snapshots `history_store: "candle_store"`; `HistoryStore` (`hourly_candles`) is unchanged for
  older installations. Both answer `supports`, `received_close` and `latest_close_at_or_before`.
  `application/candle_readiness.py` (the decision candle wait and `expected_candle`) and
  `application/market_context.py` read whichever store the execution carries (`Execution.hourly_history`, set by the
  assembly; `None` = `hourly_candles`); `market_context` takes `extra_symbols`. `api/hq_money.py`: HQ's trend label
  reads the candle file when HQ has it (`api/hq_chart.candle_store_for`).
- **The price collector in the service (D222).** `application/price_collector.py` (`PriceCollector`,
  `CollectorSettings`, `HotCoins`, `CollectorTransports`, `InterruptibleSleeper`, `newest_opens`): one step does the
  most urgent due work (the pair list; the coins the team works with; the hourly ranking pass of every eligible pair
  and then the coin scan; the daily wide pass at 5 and 15 minutes), at most 30 requests as one candle run with a
  compact report, through T25's `CollectionRun`; rate-limit answers pause it; a stop interrupts its waiting.
  `application/service_market_data.py`: `open_service_market_data` (the writer for the collector, a read-only handle
  for everything else, the collector with the scan wired to `CoinScanner` and `CoinScanStore`),
  `service_hot_coins` (from saved records only), `market_database_beside`, `activity_recorder`
  (`price_collection` activity events). `application/paper_service.py`: `PaperService(price_collector=...)` keeps one
  collector step running in its own thread; a failed step is `collector:<error>` in the tick; `request_stop` ends the
  collector's waiting; `_shutdown` waits for its last request. `application/collect_candles.py`: public
  `latest_recorded_time` (the clock guard).
- **The subscription Trader on wakes (D223).** `application/leadership.py`: `GatewayRole.reply_type_for(task)` (the
  reply contract per task; every department keeps its usual reply by default). `application/trader_workflow.py`: the
  Trader's is `TraderWakeReply` for a wake task (`task_wake`), with `wake` (`compact_wake`: newest 40 triggers and 20
  alarm items shown, with totals) and `practice_range` (`practice_range_context`: strategy versions with a
  confirmation report and their newest reports in plain words) in its context, market data for the wake's coins
  (`wake_symbols`), wake instructions, and its answer recorded under `wake_outcome` with a Secretary report
  (`_apply_wake`); nothing is ordered there. `application/subscription_runtime.py`: `"wake"` and `"practice_range"`
  added to the protected `ROLE_CONTEXT_KEYS["trader"]`; `SubscriptionTraderHandler` applies wake replies and shares
  its accounting (`_accounting`). `application/runtime_models.py`: recovery validates the per-task reply.
- **The installation (D223, D224).** `paper_runtime.py`: `PaperRuntimeConfig.price_collector`
  (`PriceCollectorConfig`, on by default) and `event_engine` (off by default; refused without public data and the
  collector); `collector_transports` (pair list, OHLC and Ticker limits, through the owner-pinned market proxy in a
  protected deployment); `assemble_paper_runtime(market_database=, market_transports=, market_limiter=,
  market_evidence_label=)` opens the candle file and the collector and sets the hourly features to it.
  `application/subscription_operation.py`: the candle file beside the database, the collector to the service, the
  hourly refresh into `hourly_candles` only without the collector, the decision candle wait on the candle store,
  `event_engine_options` (the price watcher on the read-only candle store; the day start uses the collector's scan),
  only `day_plan` and `check_in` wakes wait for the closed hour (`_WAITING_WAKES`), `price_collection` and
  `event_engine` in the result. With the engine on, `adapters/market/paper_feed.py` also quotes the day's coins
  (`extra_symbols`, from `service_market_data.quoted_day_coins`: focus, plans, alarms, held, within the owner's coin
  scope) in one request per poll (`adapters/market/public.KrakenPublicRest.fetch_tickers`); a failure of that request
  is recorded as `public_feed_day_coins` and never fails the configured coins. `kernel/deployment_image.py`:
  `MARKET_DATABASE` (`/var/lib/trade-graph/market_data.sqlite`) passed to the installed dashboard
  (`--market-database`).
- **The image (D224).** `scripts/build_protected_image.py`: `native_wheel_pins` checks, before any download, that the
  exported requirements pin NumPy and TA-Lib with the hash of the lock's CPython 3.12 Linux x86-64 wheel.
  `deploy/Dockerfile.protected`: the final stage imports `numpy` and `talib` (and computes one SMA) before sealing.
- **Docs.** `docs/L3-INSTALL-CHECKLIST.md` (new: every L3 command in order, the configuration, the Perplexity
  prerequisites); `KRAKEN-CANDLE-COLLECTION.md` ("In the service"); `EVENT-ENGINE.md`, `KRAKEN-TRADES-BACKFILL.md`,
  `INDICATOR-MENU.md`, `COIN-SCANNER.md`, `CANDLE-STORE.md`, `NORMAL-PAPER-OPERATION.md`, `PROTECTED-DEPLOYMENT.md`.
- **Tests.** New: `tests/unit/test_shared_kraken_limiter.py`, `tests/integration/test_shared_rate_limit.py`,
  `test_hourly_features_candle_store.py`, `test_price_collector.py`, `test_price_collector_service.py`,
  `test_subscription_trader_wakes.py`, `test_install_wiring.py`, `test_paper_feed_day_coins.py`,
  `tests/security/test_image_native_wheels.py`.
  Changed: `tests/unit/test_trades_docs.py` (the resolved limiter note), `tests/security/test_subscription_network.py`
  (the dashboard's `--market-database`, and that it is the file beside the database).

## Decisions (reserved block D220–D224)

- **D220 (2026-10-17, implementation default for C2): the hourly features read the candle store.** In an installation
  with the price collector (public data on, `price_collector.enabled`), the Trader's and Research's hourly features
  are computed from the candle store's 60-minute series (`kraken_public_ohlc`; `synthetic` for scripted quotes), read
  point in time through `CandleStore.window`, for every coin of the mandate and, on a wake, the wake's coins, not
  only BTC/USD and ETH/USD. The fields, TA-Lib functions, windows, anchor rule, statuses, rounding and the snapshot
  hash rule stay `hourly-price-features-v3` and are computed by the same code from either store, so the feature
  version does not change; a snapshot names its store (`history_store: "candle_store"`) and each candle by its store
  revision (`candle-store:<source>:<symbol>:60:<open>:r<n>`, with its run). Kraken's flat quiet hours are read as
  Kraken sent them, as the scanner and the indicator engine read them. The decision candle wait and
  `expected_candle` read the same store; HQ's trend label reads the candle file when HQ has it (display only, D136).
  Without the collector everything reads `hourly_candles` as before, and every stored v1, v2 and v3 snapshot stays
  readable and is never recomputed; with it, the hourly BTC/ETH refresh into `hourly_candles` stops. Reason: D136
  "later work"; the collector's 60-minute series is the same Kraken OHLC data, kept for every coin.
- **D221 (2026-10-17, implementation default for C2): one Kraken request limiter.** There is one implementation,
  `rate_limit.KrakenRestLimiter`, with the collector's counter model and the backfill's spacing and penalty rules;
  the backfill's `RequestLimiter` is that class with its own documented settings (15, 0.33 a second, headroom 2,
  1-second spacing, 5 to 120 seconds of penalty, millisecond waits), so the backfill command's numbers are unchanged.
  The limiter is safe to share between threads and never sleeps holding its lock. In the service one instance
  (`service_limiter`: ceiling 10, drain 0.30 a second, 1-second spacing, starting at the ceiling, on a monotonic
  clock, throttle times read on the wall clock) carries every request of the price collector, the hourly scan's
  Ticker request and any Trades backfill, so together they never pass the ceiling (tested against Kraken's own
  counter). The 10-second quote feed keeps its existing cadence outside it (0.1 requests a second, 0.2 with the
  event engine's day coins); the headroom of 5 below Kraken's 15 is for it, as T25 designed, but during the hourly
  ranking pass the sum is above the 0.33 a second Kraken documents for a key's counter. Kraken documents its counter
  for API keys and not for keyless calls; if the service sees rate-limit answers, the reports show them and
  `ranking_every_hours: 2` halves the largest share (a check at step L3). Reason: the
  2026-10-15 status entry ("unify them when one service process runs both").
- **D222 (2026-10-17, implementation default for C2): the price collector in the service.** One collector runs in
  the paper service whenever public data is on (`price_collector.enabled`, default true) and replaces the two
  Windows tasks (removed at L3 once it is seen working). Each step does the most urgent due work, at most 30
  requests, recorded as one `public_collect` run with a compact report (`collector: service-collector-v1`): first the
  pair list (daily; also when none is stored); then the coins the team works with now, from saved records only
  (BTC/USD and ETH/USD, the quote feed's coins, the newest live shortlist, today's focus coins, held coins, coins with
  an armed alarm or a day plan not yet ended) at 5, 15 and 60 minutes, and at 1 minute for BTC/USD, ETH/USD, focus
  coins and coins with a 1-minute alarm, each series when a newer candle has closed and settled (30 seconds; 180
  for an hour, D129), a 1-minute series at most every 5 minutes, an answer without the newer candle again after 60
  seconds; then every eligible pair's hourly candles once per hour (the scanner reads them, D170) and the hourly
  coin scan as soon as that pass is done or 50 minutes into the hour (it does not wait for the wide pass); then
  every eligible pair at 5 and 15 minutes once a day. A rate-limit answer stops the chunk, waits the counter out and
  pauses collection 60 seconds; a clock behind a recorded run pauses it until the clock passes that run (the T25
  guard); a stop interrupts its waiting at once. The service runs one step at a time in its own thread and never
  waits for it; a failed step is reported as `collector:<error>` and management goes on. The activity log records
  every pair-list refresh and hourly scan (with the pass's counts), every gap or failure and every change of status,
  not ordinary chunks. The hourly scans keep the coin rule's answer fresh (W2-B's two-hour bound); the day's focus
  still comes from the day start's shortlist. These are cadences of public data collection, set in the
  configuration, not caps on anyone's work (D116). 1-minute candles for every pair stay an owner decision (D133).
  Reason: the W6-D package ("one price collector inside the service ... for the shortlist and focus coins"), W2-B's
  and W5-A's handoffs (the hourly scan), T33's candle alarms (a candle more than 10 minutes late is a lost feed).
- **D223 (2026-10-17, implementation default for C2): the subscription Trader on wakes, and the switch.** A
  department chooses its reply contract per task (`GatewayRole.reply_type_for`, default its usual reply). The
  Trader's is `TraderWakeReply` for a wake task and `TraderReply` for any other. A wake turn is handed `wake` (the
  wake as W5-A recorded it, showing the newest 40 triggers and 20 alarm items with their totals, so one turn stays
  inside the subscription's 128 KiB request bound; every trigger stays in the wake record), `practice_range` (up to
  6 strategy versions with a confirmation report and their 2 newest reports in plain words: D138 item 5, the Trader
  reads them and decides) and market data for up to 6 of the wake's coins (its triggers', the day's focus coins and
  active plans, or the shortlist when Research named none). Both keys are added to the protected
  `ROLE_CONTEXT_KEYS["trader"]` and must be added to the owner's pinned subscription profile at L3. The answer is
  recorded under `wake_outcome` with a Secretary report and no order; the event engine installs the plans through
  `DayPlans.install` and its checks. `event_engine` in the protected `paper-config.json` ships `false`; `true`
  (step L3) gives `run_subscription_operation` `EngineOptions` with T33's price watcher on the collector's candle file
  (read-only) and no day-start scan step (the day start uses the collector's newest hourly scan, so the service step
  makes no request). The engine is refused without public data and the collector. The owner's `graph.py` must pass a
  wake answer through `apply_role_result` (one that edits a Trader reply's `rationale` fails every wake turn, set
  aside without effects; the reference graph passes answers through). With the engine on, the paper feed also quotes
  the day's coins (today's focus coins, coins with a day plan, an armed alarm or a position, filtered by the owner's
  coin scope) in one extra Ticker request per poll, because `DayPlans.install` refuses a coin without a quote and the
  paper broker fills and protects only on quotes; that request's failure is recorded and never fails the configured
  coins. A Trader wake for an event (alarm, fill, exit, request) is answered at once; only `day_plan` and `check_in`
  wakes wait, as the fixed timer's decisions did, for the closed hour (`decision_candle_wait_seconds`). Research and
  the Leader keep their usual subscription replies; their wake answers on Perplexity are W6-A's and W6-B's. Reason:
  W5-A's handoff (D192, D194), rule 7 (no silent change of the running system), AGENTS.md (protected owner
  configuration).
- **D224 (2026-10-17, implementation default for C2): the runtime layout, the image and the checklist.** An
  installation's candle file is `market_data.sqlite` beside the financial database (`/var/lib/trade-graph` in the
  image, `runtime/` in a checkout), in the same private state mount, so the container launch keeps its two mounts.
  The collector is its only writer (it creates it private when missing); the service's hourly features, the price
  watcher and the dashboard (`--market-database`, set by `boot-subscription-dashboard`) open it read-only. It is
  public price data and not part of the financial books or their recovery unit (D132); at L3 the existing WSL candle
  file is copied in with SQLite's backup command. The image builder refuses, before any download, an export that does
  not pin NumPy and TA-Lib with the hash of the lock's CPython 3.12 Linux x86-64 wheel, and the image imports both
  before it is sealed; the real build is step L3. `docs/L3-INSTALL-CHECKLIST.md` names every L3 command in order,
  matching the plan's L3 order, with the Perplexity prerequisites as explicit conditions of its step 12. Reason: the
  W6-D package bullets and W5-B's note (the dashboard reads the candle file).

## Evidence

- Tests written first: each slice's new tests were written before its code and failed first (the new modules
  `price_collector`, `service_market_data` and `CandleStoreHistory` did not import; the shared limiter had no
  `penalize`, spacing or thread safety; the Trader had no per-task reply, so a wake was asked for `TraderReply`; the
  installed service had no candle file or collector). They pass now. Mutation checks: putting back the old scan order
  (scan after the wide pass), removing the retry memo, and the old single reply type each made the new tests fail.
- `uv run ruff check --no-cache src tests scripts/check_repository_hygiene.py scripts/verify_offline_acceptance.py`:
  all checks passed (also `scripts/build_protected_image.py`).
- Related tests: the 178 non-unit modules that import or exercise the changed code (including
  `tests/integration/test_provider_wire_schemas.py`, `test_wake_cycle.py`, `test_protected_department_graph.py`,
  `tests/security/test_subscription_network.py` and every new module), in four parallel shards with
  `uv run pytest -p no:cacheprovider -rfs <modules>` at `0be53b5`: 3,975 passed, 0 failed, 14 skipped (the native
  bubblewrap boundary tests in `test_subscription_process_boundary.py`), 1 warning (an existing archive test's
  duplicate zip name). After that run only docs changed, and one test constant (a profile model name set to
  `scripted` in `test_subscription_trader_wakes.py`: 4 passed). An earlier run had two failures caused by editing
  source while it ran (the protected package pin changed under it); they passed on rerun and the final run has none.
- `uv run pytest -p no:cacheprovider -q tests/unit` (counts from its JUnit report): 2,200 tests, 2,193 passed,
  0 failed, 7 skipped.
- `python3 scripts/check_plan.py`: plan valid, 46 tasks. `python3 scripts/test_planning.py`: 11 tests OK.
  `python3 scripts/check_repository_hygiene.py`: passed (and `--staged` before every commit). `uv lock --check`:
  passed. `git diff --check`: clean.
- Full suite: not run (night shift: the wave integration runs it).
- Real or synthetic: everything is synthetic. Kraken answers (OHLC, AssetPairs, Ticker, rate-limit errors) are
  scripted in Kraken's documented format; the subscription Trader is a scripted CLI; clocks are frozen or advanced by
  the tests. No network call, no paid call, no exchange call and no order was made; the protected image was not
  built (no Docker in the cloud; its inputs are tested).

## Schema and dependencies

- State migration: **none** (0041 stays unused): the collector writes only the candle file and existing tables
  (`coin_scans` and `coin_shortlist_entries`, `activity_events`); the wake reply uses W5-A's records. Candle
  database: no change.
- Dependencies added: none (`pyproject.toml` and `uv.lock` unchanged; `uv lock --check` passes).

## Not done, open or for someone else

- **Perplexity in the installed service** (W6-A's and W6-B's left-open item; the night lead asked W6-D to build it
  if safely in scope): not built, because W6-A's and W6-B's code is not in this package's base and the pieces cross
  the protected process boundary. Each piece is an explicit prerequisite of step 12 in
  `docs/L3-INSTALL-CHECKLIST.md`: (1) Research's focus handler and the Leader's news-round handler assembled inside
  the protected process with a paid-enabled gateway (today `run_subscription_operation` refuses `models` and
  `price_cards`); (2) a pinned, separately reviewed route to both Perplexity endpoints (the subscription market proxy
  admits only Kraken and Frankfurter); (3) the key from the owner's private file inside the image (D154); (4) seeded,
  re-verified price cards (the 2026-10-12 card goes stale on 2026-11-11); (5) a wake allocation for a switched-on
  department (W6-A's `_wake_task` change, to merge with this package's `paper_service.py`); (6) `event_engine: true`
  (this package). Who: a follow-up package before L3, or the integrator. Until then step 12 is skipped and Research
  and the Leader stay on the subscription.
- **Focus step on the subscription**: with the engine on and no Perplexity wiring, Research's subscription answer
  names no focus coins, so each day's focus step is recorded as failed and the Trader's day-plan wake uses the
  scanner's shortlist (D223). Who: the Perplexity follow-up above.
- **Hourly scans and the day's shortlist**: the collector's hourly scans keep the coin rule's scan fresh, but they do
  not widen the day-start shortlist during the day; W6-A's engine check (focus coins must be on the day start's
  shortlist) is unchanged. Widening it would change the engine check and the workflow together. Who: owner decision,
  later package.
- **Shared files for the integrator** (keep both sides): `application/paper_service.py` (W6-D: the collector task in
  `_tick`, `_start`, `request_stop`, `_shutdown`; W6-A: `_wake_task`); `application/leadership.py`
  (`GatewayRole.reply_type_for`: W6-A's and W6-B's departments can use it for a per-task reply);
  `application/subscription_runtime.py` (protected `ROLE_CONTEXT_KEYS`; W6-A or W6-B may add keys for their
  departments); `application/trader_workflow.py`; `application/subscription_operation.py`; `paper_runtime.py` (new
  config fields `price_collector`, `event_engine`); `application/market_context.py` (`extra_symbols`);
  `application/price_history.py`; `adapters/market/rate_limit.py`, `trades_rate_limit.py`, `paper_feed.py`,
  `public.py`; `kernel/deployment_image.py`; `scripts/build_protected_image.py`; `deploy/Dockerfile.protected`;
  `docs/EVENT-ENGINE.md` (W6-A edits it too). W6-A's stricter `_focus` check and its `WakeContext.focus_step` pass
  through the Trader's `wake` context unchanged (`compact_wake` keeps every field). No migration, so the
  0038 to 0040 chain is unaffected.
- **Step L3 (real checks, owner)**: the real image build (downloading the NumPy and TA-Lib wheels); the first
  collector pass against Kraken without a key (Kraken does not document keyless counters; the quote feed's 0.1
  requests a second, 0.2 with the engine on, are outside the limiter, and during the hourly ranking pass the sum is
  above the 0.33 a second Kraken documents for a key; if rate-limit answers appear, set `ranking_every_hours: 2`);
  the candle file's growth (about 30 MB a day); the subscription provider accepting the `TraderWakeReply` schema in a
  real turn; the owner profile's two new Trader context keys, the re-pinned manifest and network profile, and the
  owner `graph.py` check (checklist step 3).
- **Not built**: an HQ panel for the collector (HQ's activity log shows `price_collection`); gap backfill inside the
  service (running `backfill-trades` while the service runs uses a second, uncoordinated limiter: run it with the
  service stopped); the separately billed API Trader's wake path end to end (the per-task reply applies to it, but
  only the subscription path is tested end to end). Who: later packages.
- **Protected paths**: the new modules (`application/price_collector.py`, `application/service_market_data.py`)
  follow the existing market modules and are not in `PROTECTED_PATH_PREFIXES`; their cadences come from the
  protected `paper-config.json`. Who: integrator, if the collector should be protected too.
- W5-A's open items are untouched.
