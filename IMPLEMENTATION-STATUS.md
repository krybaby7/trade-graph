# Cloud wave 5 merged (T35, T42, T41), not deployed — 2026-10-17

Branch `claude/day-trader-engine-20261012`, integrated by a cloud session for the night shift. The merged result was pushed as the candidate branch `claude/night-candidate-wave5`; GitHub's `test` job passed on it and the night lead pushed the same commit (`e1008e5`) to the integration branch. Merged in the plan's order, each with `git merge --no-ff`: W5-A T35 `cd28455` ([#14](https://github.com/krybaby7/trade-graph/pull/14), package head `b84f5a2`, code `2711ee9`), W5-C T42 `bb8a837` ([#13](https://github.com/krybaby7/trade-graph/pull/13), `751bf0e`, code `cb114a5`), W5-B T41 `5fa9bf7` ([#12](https://github.com/krybaby7/trade-graph/pull/12), `72a2d8e`, code `9942f99`), on the integration head `d6feac5`; then one integration fix, `8cc7f50` (below). Decisions [D190–D204](docs/DECISIONS.md) are copied from `planning/handoffs/W5-A.md`, `W5-B.md` and `W5-C.md`. **Nothing was deployed or installed.** No paid, model-provider, exchange or network call was made, no order was placed and no key exists in the cloud; all new behaviour is tested on synthetic values, scripted departments and scripted quotes.

What changed for the owner (all in source; it reaches the PC at step L3):
- **T35 event engine** ([EVENT-ENGINE.md](docs/EVENT-ENGINE.md)): departments can now be woken by events instead of a fixed four-hour timer: the Trader when a price alarm goes off, a waiting buy fills, a safety exit closes a position or the daily loss limit stops new buying; any department when the Leader asks, at the time it chose for its own next check-in, or at the next day start if it chose none. Every day at the trading-day start (00:00 UTC) each portfolio's day runs once: the coin scanner, Research's focus coins from the shortlist, the Trader's day plans, and at the day end Learning's review. Nothing limits how often anyone is woken; what arrives while a department is busy is batched into its next wake, never dropped; every wake is saved with its reasons, its triggers and the AI allowance reading; a restart neither loses nor repeats a wake, and a wake left over from a day that already ended never places an order. Under the allowance floor new AI waits while management and safety exits go on. **It is off in the installed service**: `PaperService` runs it only when given `event_engine=EngineOptions(...)`, because the real AI Trader does not yet answer a wake with day plans (W6-D tonight). The contracts W6-A, W6-B, W6-C and W7-A build on are in `contracts/wakes.py` and the guide. State migration 0035.
- **T42 scorecard** ([SCORECARD.md](docs/SCORECARD.md)): `trade-graph scorecard [--start] [--end] [--format json|text]` scores one paper account over a period from the ledger: the result after every fee against keeping the dollars and against buying Bitcoin at the start and selling at the end (with the same fee), trades, win rate, average win and loss, worst fall and longest losing run, in USD with EUR reporting, and beside it the AI spending of the same days from receipts (unknown costs shown as unknown, never zero; practice money never pays for AI). Every scorecard says that working software, economic evidence and permission for real money are three separate gates, and it judges none of them. It reads only and writes nothing; no migration (0037 unused). Not shown in HQ yet.
- **T41 HQ price charts** ([HQ-DASHBOARD.md](docs/HQ-DASHBOARD.md)): a "Price charts" panel in HQ's Money tab draws each coin the team works with (today's day-plan coins, coins held, coins with alarms, the scanner's shortlist, BTC and ETH) as candles at 1, 5, 15 or 60 minutes with the Trader's chosen indicators, alarm lines, waiting buys, safety exits and fills, every line also written out in words. Data comes only from saved records through `GET /api/v1/hq/chart` (candles from the candle file opened read-only, indicators from the indicator engine); the drawing library is TradingView Lightweight Charts 5.2.1 (Apache-2.0), vendored under `web/static/vendor/lightweight-charts/` with its licence, notice and `VENDOR.json`, loaded from HQ itself with a pinned integrity only when the panel is shown; the attribution line and TradingView link sit under every chart. `trade-graph dashboard --market-database` names the candle file (default `runtime/market_data.sqlite`). Source S46. No migration (0036 unused).

Integration:
- One conflict: `src/trade_graph/kernel/authority.py`, where W5-A and W5-C each appended to `PROTECTED_PATH_PREFIXES`; both are kept (the wake contract and `application/events/`, then `application/scorecard.py`). No other conflict: the three packages were based on `b09cae4`, and the integration branch had moved only by the status-only commit `d6feac5`. State migration 0035 is registered after 0034 in `adapters/persistence/migrate.py`; Alembic 0035 has down revision 0034, one head; W5-B and W5-C use no migration, so nothing was re-linked or renumbered. Source S46 was free and keeps its number. No dependency or price record changed: `uv.lock` and `planning/model-prices.json` are untouched (`uv lock --check` passes); `pyproject.toml` only adds the vendor folder to the package data. No package edited a shared planning file.
- **Integration fix `8cc7f50`:** the W5-B branch head fails `uv run ruff check --no-cache src tests` with one import-order error (I001) in `tests/chart_support.py`, although its handoff reports a pass; the imports were sorted, nothing else changed.
- Reviewed against the plan's rules and each task's acceptance: no key or private data (hygiene check and a pattern scan of the wave diff), paid calls off, no real orders (the engine installs day plans only through T34's paper-only `DayPlans.install`), no cap on agent activity (no limit on triggers, wakes or check-ins; the 16 focus coins, 16 day plans and 16 wake requests per answer and the 500-row read batch are document and read bounds, D116), and each package used only its reserved numbers (D190–D194 and 0035; D195–D199; D200–D204). The engine is opt-in and off by default; the chart page's only outside address is the attribution link the licence asks for, and its policy stays `script-src 'self'`.
- Shared code reviewed: `application/paper_service.py` (W5-A: `event_engine=` replaces `_schedules`' fixed timers only when given, skips an obsolete wake before any snapshot or attempt, hands each quote to T33's price watcher, reports engine failures as `wakes:` in the tick); `application/ai_spending.py` (W5-C: `period_spending` added, HQ's report unchanged; W2-C's tests pass); `kernel/trade_metrics.py` (one classmethod, `TradeFill.from_fill_record`); `application/ledger.py` (one read-only `fx_rate`); `api/app.py`, `cli.py`, `dashboard.py`, the HQ page, scripts and stylesheet (W5-B); `scripts/build_protected_image.py` now accepts `.txt` package files (the chart licence and notice).
- Build plan: status board. `planning/progress.json` marks T35, T41 and T42 done, and the packaged `progress_catalog.json` is regenerated with `scripts/update_progress_catalog.py`.

Evidence (CPython 3.12.13 from `uv python install 3.12`, `uv sync --frozen --group dev --python 3.12`, in a cloud container, no `TRADE_GRAPH_*` variable set, so paid and live calls are off):
- After the W5-A merge (`cd28455`), the package's own tests (`tests/unit/test_wake_contracts.py`, `tests/integration/test_migration_0035.py`, `test_wake_engine.py`, `test_wake_cycle.py`, `test_wake_service.py`, `test_scripted_day.py`, `tests/property/test_wake_properties.py`) with `tests/integration/test_provider_wire_schemas.py`, `tests/unit/test_contracts.py`, `test_protocols.py` and `test_import_boundary.py`: **129 passed, 0 failed, 0 skipped** (40 s).
- After the W5-C merge (`bb8a837`), the package's own tests (`tests/integration/test_scorecard.py`, `test_scorecard_cli.py`, `test_ai_spending_period.py`, `tests/property/test_scorecard_properties.py`, `tests/unit/test_scorecard_stays_offline.py`, `test_trade_metrics.py`) with W2-C's `tests/integration/test_ai_spending.py`, `test_ai_spending_cli.py`, `test_hq_spending.py`, `tests/property/test_ai_spending_properties.py` and `tests/unit/test_wake_contracts.py`: **104 passed, 0 failed, 0 skipped** (33 s).
- After the W5-B merge (`5fa9bf7`), the package's new and changed tests (`tests/integration/test_hq_chart.py`, `test_hq_chart_routes.py`, `test_market_data_readonly.py`, `tests/unit/test_hq_chart_contract.py`, `tests/test_hq_charts_static.py`, `tests/test_hq_static.py`) with `tests/test_hq_template.py` and `tests/test_hq_js_behaviour.py`: **139 passed, 0 failed, 0 skipped** (23 s); `node --test tests/js/hq_charts.test.mjs`: **12 passed, 0 failed**; `uv run python scripts/vendor_lightweight_charts.py --check`: the vendored copy matches its pins.
- On the merged tree with the fix (`8cc7f50`), 161 related non-unit modules: all of the wave's own, and every module that mentions the paper service, the subscription operation, protected paths, the migration list (`apply_migrations`, Alembic, `sqlite_master`, `schema_migrations`), JSON or wire schemas, the alarm queue or price watcher, day plans, `kernel.authority`, the trade metrics, AI spending, the ledger, `create_app`, `dashboard_runtime`, the CLI parser, `hq_copy`, the image builder or `pyproject` (found with `grep -rlE` over `tests`), plus every HQ and dashboard module, `test_provider_wire_schemas.py`, `test_import_boundary.py` and `test_hourly_history_refresh.py`; four parallel shards of whole modules (`uv run pytest -p no:cacheprovider -rfs`) with `tests/unit` alongside, 23 min: **3011 passed, 0 failed, 15 skipped** (14 in `tests/security/test_subscription_process_boundary.py`: bubblewrap is not in the container, as on GitHub; 1 file-mode case the root container cannot make). `tests/unit`: **2180 passed, 0 failed, 7 skipped** (4 cross-checks talipp has no indicator for, 3 file-mode cases). On the final tree with this entry, `tests/unit` with `tests/integration/test_progress_projection.py`: **2201 passed, 0 failed, 7 skipped** (the same skips).
- `uv run ruff check --no-cache src tests` and the CI line `uv run ruff check src tests scripts/check_repository_hygiene.py scripts/verify_offline_acceptance.py`: all checks passed (after `8cc7f50`). `python3 scripts/check_plan.py`: 46 tasks valid. `python3 scripts/test_planning.py`: 11 OK. `python3 scripts/check_repository_hygiene.py` (and `--staged` before each commit): passed. `git diff --check`: clean. `uv lock --check`: the lockfile matches. A pattern scan of the wave diff for key formats, e-mail addresses and private paths found nothing private (only the cloud container's generic worktree paths in two handoffs).
- CI's offline acceptance step `scripts/verify_offline_acceptance.py` on the merged tree (with `TRADE_GRAPH_PAID_CALLS_ENABLED=false` and `TRADE_GRAPH_LIVE_ENABLED=false`): 464 of 464 passed, 40 of 40 cases passed, gate complete; the artifact hygiene check on its report passed.
- **Full suite:** GitHub Actions `test` job (run 39020715997, job 117361802700) on candidate commit `e1008e5` (claude/night-candidate-wave5), CPython 3.12.15, paid and live calls off, 51 min 50 s: 7347 passed, 0 failed, 18 skipped; offline acceptance 464 of 464, gate complete. The same commit was then pushed to the integration branch.

Missing credentials and real-data checks: none for T35, T41 or T42 in the cloud. The engine is scripted end to end; the real AI Trader and Research on wakes, a real scan with a live ticker and real candles come with W6-A, W6-D and step L3. The charts were checked in a test browser (Chromium) with made-up prices only: the owner opens HQ's Money tab on a phone and an iPad, in light and dark, at L2 or L3 once the price collector fills `runtime/market_data.sqlite`. No real paper run exists to score yet.

Open, for later packages and the owner:
- W6-D (tonight): the real subscription Trader on wakes: `TraderWakeReply` as its reply type for wake tasks, the `wake` context key in the protected `ROLE_CONTEXT_KEYS["trader"]` (and the owner's pinned subscription profile at L3), its result under `wake_outcome`, and the switch in `run_subscription_operation` and `PaperRuntimeConfig` that passes `event_engine=EngineOptions(...)`. Until then the installed service keeps its fixed timers. W6-D also owns the hourly scan in the service, the live ticker for the day start's scan, and the candle file kept current for the price watcher's candle and indicator alarms and for HQ's charts (the dashboard process needs read access to it; keep `.txt` in the image builder's suffixes).
- Not built: a Trader tool to close a day-plan position or move its exit (cancel the exit, reconcile, then sell or re-protect). A plain sell of protected coins is still refused because the exit reserves them.
- Not changed (W4-A's finding, passed to W5-A): `Execution.reconcile` leaves an `UNKNOWN` intent `UNKNOWN` while the broker reports it open, which blocks new buys of that coin on the older order path. W5-A's handoff proposes a fix for its own package, test first: when the broker answers `open` or `partially_filled` and the recorded fills equal its filled quantity, move the intent to `OPEN`/`PARTIALLY_FILLED` with its venue order id, in paper at least, and decide separately for live pilot effects.
- The scorecard is not in HQ yet (D204): a later HQ package would cache it (about 3.5 s over four weeks of 10-second prices) and keep within the 80 KB payload budget. T43 calls `compute_scorecard(...)` with the test's start and end.
- A later migration could add an index on `valuation_marks (portfolio_id, asset, observed_at)`: every "newest price of a coin" read (execution, the daily loss limit, HQ, the scorecard) scans the table, which grows by one row per coin about every 10 seconds (found by W5-C).
- HQ sends `Cache-Control: no-store`, so each page load that opens a chart fetches the 198 KB chart script again; gzip or long-lived caching for `/static/vendor/` is later work.
- Load-sensitive test to watch: `tests/integration/test_hourly_history_refresh.py::test_slow_hour_boundary_refresh_finishes_before_next_role_inputs_while_management_continues` expects two management loops within 50 ms of wall time; W5-A saw it fail once under five parallel pytest processes (it passed 5 of 5 alone). In this integration's related run (five pytest processes at once) it passed.

Next: **wave 6 (W6-D install readiness, W6-A focus coins, W6-B Leader news, W6-C HQ wake log) can start** once the full suite passes on the candidate commit. Owner: as before, switch on the price schedule (L1) and check that Perplexity auto top-up is off.

---

# Cloud wave 4 merged (T34), not deployed — 2026-10-17

Branch `claude/day-trader-engine-20261012`, integrated by a cloud session for the night shift. The merged result was pushed as the candidate branch `claude/night-candidate-wave4`; GitHub's `test` job passed on it and the night lead pushed the same commit (`b09cae4`) to the integration branch. Merged with `git merge --no-ff`: W4-A T34 `2b6a4f7` ([#11](https://github.com/krybaby7/trade-graph/pull/11), package head `a193b8c`, code `20dac09`), on the integration head `de43cc2`. Decisions [D185–D189](docs/DECISIONS.md) are copied from `planning/handoffs/W4-A.md`. **Nothing was deployed or installed.** No paid, model-provider, exchange or network call was made, no order was placed and no key exists in the cloud; all new behaviour is tested on synthetic values and scripted quotes.

What changed for the owner (all in source; it reaches the PC at step L3):
- **T34 Trader day plans** ([DAY-PLANS.md](docs/DAY-PLANS.md)): the Trader can write one plan per focus coin and trading day: the practice-range strategy version it follows, the menu indicators and alarms it watches, resting limit buys below the price, each with a safety stop below it and an optional take-profit above it, and a short rationale and invalidation. Software checks the plan against fixed rules only (paper account, the owner's money limits recorded, not paused, plain spot pair, a practice-range confirmation report exists for the strategy version, prices and sizes exactly on the coin's steps and above its minimums, every buy resting below the market and ending by the end of the trading day, and the per-coin and total shares counting every waiting buy as if filled), saves the plan, every order and every alarm before anything is sent, and never rests an order twice after a crash or retry. The moment a buy fills, even partly, its safety exit is saved in the same step and sent at once, so a position is never left unprotected while the Trader sleeps; a pause cancels the waiting buys and keeps the safety exits, and expired buys are cancelled. A new plan for a coin first cancels and settles the old plan's waiting buys. Nobody approves, scores or votes on a plan (no confidence threshold, committee or Leader approval), and plans can be written and revised without limit. **Plans stay off until the owner records the money limits at step L3** (`trade-graph money-limits --confirm`), and the Trader's morning turn that writes plans comes with W5-A. State migration 0034.
- **Fixed: the paper partial-fill bug wave 3 recorded as open** (D189, `kernel/fills.py`): on a quote without a volume figure, a crossed resting buy filled half of what remained without rounding, so the digits grew until the books refused a fill (`decimal.Inexact` after 26 such quotes). Partial fills are now rounded down to the coin's lot and finish cleanly at the minimum; the same order now ends in 18 whole-lot fills. The practice range does not pass the lot yet, so its replays and golden results are unchanged.

Integration:
- No merge conflicts: the package was based on `ce17fcb`, and the integration branch had moved only by the status-only commit `de43cc2`. State migration 0034 is registered after 0033 in `adapters/persistence/migrate.py`; Alembic 0034 has down revision 0033, one head. No dependency or price record changed: `pyproject.toml`, `uv.lock` and `planning/model-prices.json` are untouched. The package edited no shared planning file.
- Reviewed against the plan's rules and T34's acceptance: no key or private data, paid calls off, no real orders (day plans refuse every binding but paper), no cap on agent activity (the 16 entries, 16 alarms and 8 indicators per plan are document envelopes; plans may be written and revised without limit, D116), no confidence, approval or vote field, and only W4-A's reserved numbers (D185–D189, 0034) are used.
- Shared contracts reviewed: `AuthorizedOrderIntent.take_profit_price` and `BrokerCapabilities.native_take_profit` are left out of saved documents when unset, so existing intents and capability records serialise byte for byte as before; `Execution` gains the fill hook (inside the fill's own transaction, nested as a savepoint), `place_protection(..., take_profit_price=, protective=)`, `dispatch(protection_only=)` and `cancel_intents`; a take-profit exit is refused at dispatch by a broker without `native_take_profit`; `kernel/authority.py` adds `pause_allows_protection` and makes the three new modules protected paths; the paper broker passes its lot to the fill engine and carries a take-profit leg on a sell stop (one cancels the other); the paper service runs the day-plan management step after the money-limit step and sends protective exits right after the quote that filled an entry.
- The partial-fill rounding bug recorded as open by wave 3 is marked fixed in its entry below and in T32's progress evidence. Build plan: status board. `planning/progress.json` marks T34 done, and the packaged `progress_catalog.json` is regenerated with `scripts/update_progress_catalog.py`.

Evidence (CPython 3.12.13 from `uv python install 3.12`, `uv sync --frozen --group dev --python 3.12`, in a cloud container, no `TRADE_GRAPH_*` variable set, so paid and live calls are off):
- After the merge (`2b6a4f7`), the package's own tests (`tests/unit/test_day_plan_contract.py`, `tests/unit/test_fill_engine_lots.py`, `tests/integration/test_day_plans.py`, `test_day_plan_recovery.py`, `test_day_plan_service.py`, `test_migration_0034.py`, `test_paper_partial_fill_lots.py`, `tests/property/test_day_plan_protection_properties.py`): **105 passed, 0 failed, 0 skipped** (29 s).
- 84 related modules (the package's six non-unit modules among them): every non-unit module that mentions the paper broker, the fill engine (`fill_order_on_*`, `trigger_exit_on_*`), `kernel.authority`, `AuthorizedOrderIntent`, `BrokerCapabilities`, protected paths, pause rules, the paper service, the money limits, the practice range or day plans (found by `grep -rlE` over `tests`), plus `test_provider_wire_schemas.py`, `test_venue_conformance.py`, the execution, broker identity, paper runtime, paper live mapping, financial transition, live pilot dispatch, append-only fill replay, owner-expense upgrade, checkpoint and expenses modules, the paper soak and both end-to-end tests (`tests/e2e/test_dashboard_loop.py`, `tests/e2e/test_offline.py`), in four parallel shards of whole modules (`uv run pytest -p no:cacheprovider -rfs`), 5 min: **1530 passed, 0 failed, 0 skipped**.
- `tests/unit` (including `test_contracts.py` and `test_protocols.py`), in the fourth shard: **2138 passed, 0 failed, 7 skipped** (4 cross-checks talipp has no indicator for, 3 file-mode checks the root container cannot make). On the final tree with this entry, `tests/unit` with `tests/integration/test_progress_projection.py`: **2159 passed, 0 failed, 7 skipped** (the same skips).
- `uv run ruff check src tests` and the CI line `uv run ruff check src tests scripts/check_repository_hygiene.py scripts/verify_offline_acceptance.py`: all checks passed. `python3 scripts/check_plan.py`: 46 tasks valid. `python3 scripts/test_planning.py`: 11 OK. `python3 scripts/check_repository_hygiene.py` (and `--staged` before the commit): passed. `git diff --check`: clean. A pattern scan of the wave diff for key formats, e-mail addresses and private paths found nothing private (only the cloud container's generic worktree path in the handoff).
- CI's offline acceptance step `scripts/verify_offline_acceptance.py` on the merged tree (with `TRADE_GRAPH_PAID_CALLS_ENABLED=false` and `TRADE_GRAPH_LIVE_ENABLED=false`): 464 of 464 passed, 40 of 40 cases passed, gate complete; the artifact hygiene check on its report passed.
- **Full suite:** GitHub Actions `test` job (run 39010257667, job 117331111013) on candidate commit `b09cae4` (claude/night-candidate-wave4), CPython 3.12.15, paid and live calls off, 65 min 53 s: 7189 passed, 0 failed, 18 skipped; offline acceptance 464 of 464, gate complete. The same commit was then pushed to the integration branch.

Missing credentials and real-data checks: none for T34. At step L3 the owner runs `trade-graph money-limits` (preview), then `trade-graph money-limits --confirm` with the paper service stopped; until then every day plan is refused, as D138 requires. The first real day plans run in the scored paper test after L3 and W5-A; a scripted quote is not a live feed test.

Open, for later packages and the owner:
- Owner, step L3: `trade-graph money-limits --confirm` (W3-A) gates every day plan.
- W5-A (T35): the Trader's daily turn writes `DayPlan`s and calls `DayPlans.install` under the service's execution lock with a `request_id` from its task and coin; wake reasons are fills of `day_plan_entries` intents, fills of `day_plan_exits` intents (the broker order's `fill_terms` reason says stop or take-profit) and the plan's alarms; `DayPlans.status` is the plan's state for the Trader's context. A Trader tool to close a day-plan position or move its exit (cancel the exit, reconcile, then sell or re-protect) is not built: today a plain sell of protected coins is refused as insufficient balance.
- Passed to W5-A (found by W4-A outside its scope): `Execution.reconcile` leaves an `UNKNOWN` intent `UNKNOWN` while the broker reports it open (only filled, cancelled and not-found answers move it), so a lost acknowledgement blocks new entries on that coin until the order fills or is cancelled; a day-plan replacement cancels it, which resolves it.
- T19 (live): day plans refuse every binding but paper; per-fill exits can be below a real venue's minimum order value, so a live version merges or resizes them.
- Practice range (W2-A follow-up): passing the instrument's lot to `fill_order_on_candle` would make replays fill whole lots, but it changes replay results, so it needs its own decision.
- W5-B (T41) and W6-C (T39): HQ views of plans, entries, exits, refusals and plan events (nothing is shown yet).

Next: **wave 5 (W5-A event engine, W5-C scorecard, W5-B HQ charts) can start** once the full suite passes on the candidate commit. Owner: as before, switch on the price schedule (L1) and check that Perplexity auto top-up is off.

---

# Cloud wave 3 merged (T32), not deployed — 2026-10-17

Branch `claude/day-trader-engine-20261012`, integrated by a cloud session for the night shift. The merged result was pushed as the candidate branch `claude/night-candidate-wave3`; GitHub's `test` job passed on it and the night lead pushed the same commit (`ce17fcb`) to the integration branch. This is the second candidate: the first (`290713e`) failed the full suite with two Leader wire-schema failures that W3-A then fixed (see Evidence), and it never reached the integration branch. Merged with `git merge --no-ff`: W3-A T32 `ba14f1a` ([#10](https://github.com/krybaby7/trade-graph/pull/10), package head `53fc001`, code `837a57f`; first merged as `918a0ec` at `9fb6041`). Decisions [D180–D184](docs/DECISIONS.md) are copied from `planning/handoffs/W3-A.md`. **Nothing was deployed or installed.** No paid, model-provider, exchange or network call was made, no order was placed and no key exists in the cloud; all new behaviour is tested on synthetic values and scripted quotes.

What changed for the owner (all in source; it reaches the PC at step L3):
- **T32 money limits and coin list** ([MONEY-LIMITS.md](docs/MONEY-LIMITS.md)): once the owner records them, the paper account stops new buying for the rest of the trading day after losing the daily limit (D138: 3%) of the day's starting value, fees included; resting buys are cancelled, while protective exits, sales and reconciliation keep working and nothing is sold off; buying resumes at the next day start (default 00:00 UTC). No coin may take more than the per-coin share (20%) and coins together no more than the total (80%), counting every order still waiting as if it had filled. Under the coin rule an entry needs a pair that passed every check of the coin scanner's newest scan (no scan in 2 hours means no new buying, never a guess). Futures, perpetuals, swaps, staking or earn variants and leverage are refused, and any deposit or transfer that looks like interest or a reward is flagged for the owner (D127). The owner command `trade-graph money-limits` previews the D138 values and, with `--confirm`, records them as one audited owner-policy revision with a matching mandate; it is refused while the paper service runs. Until then the Trader's day-plan orders (W4-A) stay off. Agents cannot record or raise these values; the Leader may only narrow them. State migration 0033.

Integration:
- No merge conflicts. State migration 0033 is registered after 0032 in `adapters/persistence/migrate.py`; Alembic 0033 has down revision 0032, one head. No dependency or price record changed: `pyproject.toml`, `uv.lock` and `planning/model-prices.json` are untouched. The package edited no shared planning file.
- Reviewed against the plan's rules: no key or private data, paid calls off, no real orders, no cap on agent activity (these are owner money limits, D116), and only W3-A's reserved numbers (D180–D184, 0033) are used.
- The shared execution change (D181) was reviewed against T05 and broker conformance: it changes only how `authorize` and the dispatch re-check value exposure (every entry and resting entry as if filled at the highest of its price, the buy quote and the mark, against the equity left after their fees and mark shortfall) and moves the balance check before the exposure check, so an order the account cannot pay for says so. The paper broker's fills, partial fills, minimums, precision, same-bar exclusion and the ledger path are unchanged; the venue conformance, paper soak, end-to-end, execution, paper service, broker and authority modules pass after the first merge (below); the later fix touches only the mandate's JSON schema and the mandate check.
- Build plan: status board. `planning/progress.json` marks T32 done, and the packaged `progress_catalog.json` is regenerated with `scripts/update_progress_catalog.py`.

Evidence (CPython 3.12.13 from `uv python install 3.12`, `uv sync --frozen --group dev --python 3.12`, in a cloud container):
- **First candidate failed the full suite.** GitHub Actions `test` job 117269384964 on the first candidate `290713e` (package merged at `9fb6041` as `918a0ec`), 38 min: 7080 passed, **2 failed**, 18 skipped. Both failures were the Leader on the OpenAI wire in `tests/integration/test_provider_wire_schemas.py` (`test_actual_six_role_wire_dialects_and_original_typed_replies[OpenAIAdapter-leader]` and `test_real_six_role_schema_goes_through_gateway_http_fixture[leader-OpenAIAdapter]`); the base `868cda0` passes that file 65 of 65. Cause: W3-A's new `Mandate.coin_scope` was in the contract's JSON schema but left out of a listed mandate's document, and OpenAI's strict wire requires every schema property, so the Leader's serialised mandate change no longer matched its own schema. The integration's related-module run (below) had not included that file. W3-A fixed it on its branch (`837a57f`, head `53fc001`): the field is `SkipJsonSchema`, so schema and document agree and a model-made mandate is always listed (only the owner command sets `owner_rule`); under the coin rule a listed mandate may name any pair the rule covers (`owner_scope_covers` moved to `kernel/authority.py`); two new tests fail before and pass after; D180 and D183 say so. W3-A then ran 113 modules (every one that mentions `Mandate`, `OwnerPolicy`, the Leader reply, the wire schemas or schema exports, with all of its own) on its branch: 2832 passed, 0 failed, 14 skipped (bubblewrap is not in the container). The package was merged again on the untouched base as `ba14f1a`.
- After the second merge (`ba14f1a`): `tests/integration/test_provider_wire_schemas.py` **65 passed, 0 failed**; the package's own tests (`test_money_limit_rules.py`, both money-limit property files, `test_money_limits.py`, `test_money_limits_cli.py`, `test_migration_0033.py`) with `tests/unit/test_hq_refusal_copy.py`: **177 passed, 0 failed, 0 skipped**; 68 modules that use the Leader reply, the mandate and owner-policy checks, the JSON and wire schemas, the authority or the paper runtime (found by `grep -rlE` over `tests`, including the wire-schema file, the paper soak, execution, paper service and all of the package's integration and unit modules), 32 min: **1764 passed, 0 failed, 14 skipped** (all 14 in `tests/security/test_subscription_process_boundary.py`: bubblewrap is not in the container); `tests/unit` with `tests/integration/test_progress_projection.py` on the final tree: **2120 passed, 0 failed, 7 skipped** (4 cross-checks talipp has no indicator for, 3 file-mode checks the root container cannot make).
- On the first merge (`918a0ec`), the package's tests together with every module that uses the changed execution, authority, paper service, paper runtime, owner-policy and HQ wording code, including venue (broker) conformance, broker identity, the paper soak, both end-to-end tests, the execution, paper fill, paper service and authority modules and the accounting and fill-engine property tests (98 files, `uv run pytest -p no:cacheprovider -rf`, no `TRADE_GRAPH_*` variable set, so paid and live calls are off), 36 min: **2194 passed, 0 failed, 0 skipped**.
- `uv run ruff check src tests` and the CI line `uv run ruff check src tests scripts/check_repository_hygiene.py scripts/verify_offline_acceptance.py`: all checks passed. `python3 scripts/check_plan.py`: 46 tasks valid. `python3 scripts/test_planning.py`: 11 OK. `python3 scripts/check_repository_hygiene.py` (and `--staged` before the commit): passed. `git diff --check`: clean. A pattern scan of the wave diff for key formats, e-mail addresses and private paths found nothing.
- CI's offline acceptance step `scripts/verify_offline_acceptance.py` on both merges (with `TRADE_GRAPH_PAID_CALLS_ENABLED=false` and `TRADE_GRAPH_LIVE_ENABLED=false`): 464 of 464 passed, 40 of 40 cases passed, gate complete; the artifact hygiene check on its report passed.
- **Full suite:** GitHub Actions `test` job (run 38999619551, job 117299581747) on the second candidate commit `ce17fcb` (claude/night-candidate-wave3), CPython 3.12.15, paid and live calls off, 49 min 41 s: 7084 passed, 0 failed, 18 skipped; offline acceptance 464 of 464, gate complete. The same commit was then pushed to the integration branch.

Missing credentials and real-data checks: none for T32. At step L3 the owner runs `trade-graph money-limits` (preview), then `trade-graph money-limits --confirm` with the paper service stopped, to record the D138 values.

Open, for later packages and the owner:
- **Fixed in wave 4 by W4-A (D189, `kernel/fills.py`).** For W4-A (pre-existing in W1-B's quote path, found by W3-A, not fixed here): on a quote without a volume figure, a crossed resting limit fills half of its remainder, never rounded to the instrument's quantity increment, so the decimals keep growing; after about 29 such quotes `Execution._consume_reservation` raises `decimal.Inexact` inside `record_fill` (reproduced on the untouched base `868cda0`). The live paper feed sets Kraken's 24-hour volume on its quotes, so mainly synthetic or other feeds hit it, but W4-A's resting entries make it likelier; rounding partial fills down to the quantity increment would fix it.
- Live (off and out of T32's scope): `LiveService._management` does not run the money-limit step (`enforce_money_limits`), and the protected live financial service (`kernel/financial_service.py`) still reads `allowed_symbols`. A future live package adds both.
- HQ: a failure of the new step shows as "a routine step is failing" (`money_limits:` is not one of HQ's known problem prefixes), and the day's loss status (`MoneyLimitGuard.status(...).document()`) is not shown in HQ yet (W5-B, W6-C or later).
- Found at the integration review, not changed: the D127 symbol check refuses any pair whose name contains PERP, SWAP or FUTURE, so a plain spot token with such a ticker (for example PERP, if listed) is refused as an entry. That is the cautious side; a reviewer may narrow the words later.
- Contracts for the next waves: W4-A installs each day-plan entry with `Execution.authorize(portfolio_id, decision, day_plan=True)` and protective exits with `place_protection` (never gated); W5-A runs `await execution.enforce_money_limits(portfolio_id)` at the day start and after price updates, and a new stop (activity event `daily_loss_limit_reached`) is a natural Trader wake.

Next: **wave 4 (W4-A T34 day plans) can start** once the full suite passes on the candidate commit. Owner: as before, switch on the price schedule (L1) and check that Perplexity auto top-up is off.

---

# Cloud wave 2 merged (T29, T31, T40), not deployed — 2026-10-16

Branch `claude/day-trader-engine-20261012`, integrated by a cloud session for the night shift. The merged result was pushed as the candidate branch `claude/night-candidate-wave2`; GitHub's `test` job passed on it and the night lead pushed the same commit (`4c134c0`) to the integration branch. Merged in the plan's order, each with `git merge --no-ff`: W2-A T29 `0d5d9f2` ([#9](https://github.com/krybaby7/trade-graph/pull/9), package head `904d5c2`), W2-B T31 `c4a8002` ([#8](https://github.com/krybaby7/trade-graph/pull/8), `a95e872`), W2-C T40 `cfa74d1` ([#7](https://github.com/krybaby7/trade-graph/pull/7), `c6e1c22`). Decisions [D165–D179](docs/DECISIONS.md) are copied from `planning/handoffs/W2-*.md`. **Nothing was deployed or installed.** No paid, model-provider, exchange or network call was made, no order was placed and no key exists in the cloud; all new behaviour is tested on synthetic, scripted or recorded public data.

What changed for the owner (all in source; it reaches the PC at step L3):
- **T29 practice range** ([PRACTICE-RANGE.md](docs/PRACTICE-RANGE.md)): `trade-graph practice-range-register`, `practice-range-study`, `practice-range-run`, `practice-range-report` and `practice-range-verify` replay a written-down strategy version on stored candles, with the paper account's cautious fills, under each fee profile (Kraken starting tier, Kraken after USD10,000, OKX Europe). A study tunes on earlier days and confirms once on later days it can never tune on. Each report gives trades, win rate, average win and loss, the result after fees against cash, holding BTC and holding the coin, the worst fall and the longest losing run, and says it is evidence for the Trader, not a pass mark, proof of profit or permission for real money. Two example strategies (`slow-trend-pullback`, `range-reversion`) are unproven starting points. OKX public candles are their own source (`candles-okx-import`; `candles-okx-fetch` is the only command that uses the network, for L2). The result sums are in the protected kernel (`kernel/trade_metrics.py`) for the scorecard (W5-C). State migration 0030; candle database version 0002 (an existing candle file upgrades on open with every row kept).
- **T31 coin scanner** ([COIN-SCANNER.md](docs/COIN-SCANNER.md)): `trade-graph scan-coins` ranks every Kraken USD pair by 24-hour traded volume, typical hourly swing against the round-trip fee (TA-Lib NATR), spread (one public Ticker request per scan) and 24-hour trend (ROCP), and keeps the top 10 with every input, its source time and the ranking version; `coin-shortlist` shows the newest list. Left out: stablecoins, yield tokens, currencies, pairs not trading normally, thin pairs (under USD500,000 a day or a spread above 0.50%), unreviewed coins whose price sits still like a stablecoin's (the price-peg check), and U, STBL, UNITAS, CAP, DEBIT, RE and ST until someone reviews them. A replay on old history is always labelled optimistic. No AI is used. State migration 0031. Nothing runs it every hour yet (W5-A or W6-D).
- **T40 HQ AI spending** ([HQ-DASHBOARD.md](docs/HQ-DASHBOARD.md)): an "AI spending" panel on the Money tab shows Perplexity and Jev costs per department from receipts, with actual, estimated and not-yet-known costs apart ("up to" an amount, never zero), how each department uses the ChatGPT allowance over 7 days, and the testing total next to the prepaid credit the owner recorded. Past USD20 of Perplexity testing (USD5 for Jev), Needs you shows a "Keep an eye on" card; nothing is paused. Practice money never appears in the panel. `trade-graph ai-spending-report`; `trade-graph ai-spending-record` previews and writes only with `--confirm`. State migration 0032.

Integration:
- Conflicts arose only in `adapters/persistence/migrate.py`, where each package added one line after 0029. The state migrations are registered in numeric order 0030, 0031, 0032. Alembic was re-linked (0031's down revision is now 0030 and 0032's is 0031; both had 0029), with one head, `0032`. No dependency or price record changed: `pyproject.toml`, `uv.lock` and `planning/model-prices.json` are untouched.
- Reviewed against the plan's rules: no key or private data, paid calls off, no real orders, no cap on agent activity, and no package used another's reserved numbers. The only new network paths are keyless public market reads behind owner commands (`candles-okx-fetch`; the one Ticker request of a live `scan-coins`); the tests block the network.
- Sources [S44](docs/90-SOURCES.md) (OKX API v5 `history-candles`) and S45 (Kraken Ticker) were added, as W2-A and W2-B asked, both marked not yet verified because the cloud got HTTP 403 from both hosts. Build plan: status board. `planning/progress.json` marks T29, T31 and T40 done, and the packaged `progress_catalog.json` is regenerated with `scripts/update_progress_catalog.py`.

Evidence (CPython 3.12.13 from `uv python install 3.12`, `uv sync --frozen --group dev --python 3.12`, in a cloud container):
- After each merge, the package's own tests: W2-A 139 passed (its 114 and the 25 candle store tests it changed); W2-B 112 passed (its 75 with every state-migration test from 0025 to 0031); W2-C 162 passed (its 71 with the HQ projection, the HQ JavaScript behaviour and the 0030 and 0031 migration tests), and `node --test tests/js/hq_spending.test.mjs` 10 passed.
- All three packages' new tests and the related files named in their handoffs, run together after the last merge (156 files, `uv run pytest -p no:cacheprovider -rf` with `TRADE_GRAPH_PAID_CALLS_ENABLED=false` and `TRADE_GRAPH_LIVE_ENABLED=false`), 10 min: **3945 passed, 0 failed, 9 skipped** (5 file-mode checks the container cannot make because it runs as root, 4 cross-checks for which talipp has no indicator). On the final tree with this entry, the unit tests and every integration file that reads documents or planning files: 2456 passed, 0 failed, 8 skipped.
- `uv run ruff check src tests` and the CI line `uv run ruff check src tests scripts/check_repository_hygiene.py scripts/verify_offline_acceptance.py`: all checks passed. `python3 scripts/check_plan.py`: 46 tasks valid. `python3 scripts/test_planning.py`: 11 OK. `python3 scripts/check_repository_hygiene.py` (and `--staged` before the commit): passed. `git diff --check`: clean. A pattern scan of the whole wave diff for common key formats found nothing.
- CI's offline acceptance step `scripts/verify_offline_acceptance.py` on the merged tree: 464 of 464 passed, 40 of 40 cases passed, gate complete; the artifact hygiene check on its report passed.
- **Full suite:** GitHub Actions `test` job (run 38975744115, job 117224635295) on candidate commit `4c134c0` (claude/night-candidate-wave2), CPython 3.12.15, paid and live calls off, 47 min 53 s: 6929 passed, 0 failed, 18 skipped; offline acceptance 464 of 464, gate complete. The same commit was then pushed to the integration branch.

Missing credentials and real-data checks (step L2 on the owner's PC): the first real practice-range reports on the imported Kraken history, under all three profiles; a real OKX recording (okx.com refused the cloud, so T29's "recorded OKX public fixture" is for now a labelled synthetic OKX-format fixture, as the W2-A package section prescribes); the first real scan and one recorded Ticker answer (kraken.com refused the cloud); the swing needs about 30 days of hourly collector candles per pair, so the collector must keep running (L1); the first real AI receipts, and a look at the AI spending panel on a phone and an iPad, light and dark.

Open, for later packages and the owner:
- T31's recorded fixtures have 120 hourly candles and NATR 14 needs 710, so the recorded scan ranks no pair (every pair is excluded with its reason); a ranked shortlist is shown deterministic on synthetic candles and by property tests. A longer recorded series comes with L1 and L2.
- Contracts for the next waves: T32 (W3-A) reads `CoinScanStore.coin_rule` (unknown when the newest live scan is over 2 hours old or failed; W3-A decides what unknown means); T34 (W4-A) reads `PracticeRangeStore.has_report` and `reports_for`; T42 (W5-C) reuses `kernel/trade_metrics.py` and `application.ai_spending.spending_report`; T37 (W6-A) reads `CoinScanStore.latest()`.
- The older T15 "Pays for itself?" box on the Money tab compares practice results with the real costs the owner entered. The new panel never offsets AI costs against practice gains; whether the older box's wording should change is an owner or reviewer question (found by W2-C, not changed).
- `application/pair_classification.py`'s `VERIFICATION_NOTE` still says the scanner "will add" a price-peg check; it does now. Hourly scans add about 1.5–2 MB a day to the state database; a retention rule needs an owner decision later. The strategy grammar is not yet an R1 Engineer artifact class. Practice-range limits: one replay reads at most 100,000 candles, volume-share fill quantities are not rounded to a lot, and a tuning replay's indicator warm-up may read an earlier study's confirmation days (values only, never a fill).

Next: **wave 3 (W3-A T32 money limits and coin list) can start** once the full suite passes on the candidate commit. Owner: as before, switch on the price schedule (L1) and check that Perplexity auto top-up is off.

---

# Cloud wave 1 merged (T44, T28, T36, T33, C1), not deployed — 2026-10-16

Branch `claude/day-trader-engine-20261012`, integrated by a cloud session and pushed. Merged in the plan's order, each with `git merge --no-ff` (W1-D's late handoff evidence as `39af56b`): W1-E C1 `fff2792` ([#6](https://github.com/krybaby7/trade-graph/pull/6), package head `74ee9c8`), W1-B T28 `934d20d` ([#2](https://github.com/krybaby7/trade-graph/pull/2), `8fdb323`), W1-D T33 `87a7c75` ([#5](https://github.com/krybaby7/trade-graph/pull/5), `2d7f48e`), W1-C T36 `36cc045` ([#4](https://github.com/krybaby7/trade-graph/pull/4), `105d5cc`), W1-A T44 `c4bde76` ([#3](https://github.com/krybaby7/trade-graph/pull/3), `24904b1`). Decisions [D140–D164](docs/DECISIONS.md) are copied from `planning/handoffs/W1-*.md`. **Nothing was deployed or installed.** No paid, model-provider, exchange or news-feed call was made, no order was placed and no key exists in the cloud; all new behaviour is tested on synthetic or scripted data.

What changed for the owner (all in source; it reaches the PC at step L3):
- **T44 news alarm, shadow mode** ([NEWS-ALARM.md](docs/NEWS-ALARM.md)): `trade-graph news-alarm-poll` reads five free feeds (Kraken status and blog, CoinDesk, Cointelegraph, Decrypt) and stores each new headline. With Jev switched on (off by default; needs a key), it asks Jev three fixed questions per headline and logs whom it would have woken. Nobody is woken. `news-alarm-report` shows headlines, answers, would-be alarms and Jev's daily cost. State migration 0026.
- **T28 fill engine** ([FILL-ENGINE.md](docs/FILL-ENGINE.md)): one cautious fill and safety-exit module in the protected kernel, shared by the paper account and the coming practice range. A waiting order fills only when the price trades past it; a gap through a stop fills at the worse price. Three dated fee records: Kraken starting tier 0.40% / 0.80%, Kraken after USD10,000 of 30-day volume 0.22% / 0.38%, OKX Europe 0.10% / 0.20%. **Behaviour change once installed:** a paper stop exit now fills at the gapped bid less 0.10% slippage (D147).
- **T36 Perplexity research connection** ([PERPLEXITY-RESEARCH.md](docs/PERPLEXITY-RESEARCH.md)): Claude Sonnet 5.5 through Perplexity's Agent API. The software runs each web search itself, stores every result before the model reads it, rejects a finding that cites anything else, and writes a receipt for every call. An owner monthly limit pauses only these rounds. `trade-graph perplexity-smoke` only previews; a real call needs `--confirm-paid-call` and the private key file (step L2). Research and the Leader are not switched over yet (W6-A, W6-B). State migration 0028.
- **T33 price alarms** ([PRICE-ALARMS.md](docs/PRICE-ALARMS.md)): saved alarms on a price, a completed candle or a T27 indicator. Each fires once, then waits to be re-armed by its owning department. Fired alarms go to a stored per-department queue that the event engine (W5-A) will deliver one batch per wake. A stopped price feed is reported, never read as a quiet market. No AI is used and nothing limits how many alarms exist. State migration 0029. Nothing calls the watcher yet (W5-A).
- **C1 green checks:** the 20 known test failures are fixed at their causes. Mission Control's progress page reads the current plan again (the `todo` status and full timestamps). The GitHub test job may now run 120 minutes; at 45, every run since 2026-10-09 had been cancelled.

Integration:
- Conflicts arose only in `adapters/persistence/migrate.py`, `tests/integration/test_migration_0025.py`, `tests/integration/test_owner_checkins.py` and the one-line `planning/model-prices.json`. State migrations are registered in numeric order 0026, 0028, 0029. W1-B needed none, so 0027 stays unused; the runner does not need contiguous numbers. Alembic was re-linked to 0025 → 0026 → 0028 → 0029 (0028's down revision is now 0026 and 0029's is 0028), with one head. The 0025 tests keep both sides' checks: 0025 is registered once, right after 0024, every version is in order, and the Alembic head is the newest registered version. The price file keeps every dated record (W1-C's two Perplexity entries, W1-A's `typesafe:jev-1.13.0`); no existing entry was edited.
- Reviewed against the plan's rules: no key or private data, paid calls and Jev calls off by default, no real orders, no cap on agent activity, and no package edited another's reserved numbers. W1-E changed one security test, `test_subscription_egress_boundary::test_fixed_provider_proxy_is_the_only_child_egress_setting`, which needed bubblewrap on the host (it failed on GitHub's runner too). Its subject is the composed proxy command, which is never run, so it now stands in a root-owned binary for `bwrap`. No isolation code changed, and a new test shows that a host without bubblewrap still fails closed. It was kept.
- Sources [S42](docs/90-SOURCES.md) (the five news feeds) and S43 (the OKX fee page) were added, as W1-A and W1-B asked. Build plan: status board, the known-failures paragraph (none now), the cloud-container note and the install step (`uv python install 3.12`, then `uv sync --frozen --group dev --python 3.12`; the container's default is 3.13). `planning/progress.json` marks T28, T33, T36 and T44 done, and the packaged `progress_catalog.json` is regenerated with `scripts/update_progress_catalog.py`.
- **W1-D's evidence came after its code was merged.** When it was merged, W1-D's handoff still had `EVIDENCE_PLACEHOLDER`, `HEAD_PLACEHOLDER` and `PR_PLACEHOLDER`. Its session then pushed the evidence (`6a068f2`, handoff only), merged as `39af56b`. Its full suite at `2d7f48e`, run in seven slices after a single run hit that container's 2-hour limit: 6,338 passed, 14 failed, 23 skipped. All 14 also fail on the untouched base `435db71` in the same container: the 13 known failures and the bubblewrap egress test. That is no new failure. Its 114 package and migration tests passed again after the merge here.

Evidence (CPython 3.12.13 from `uv python install 3.12`, `uv sync --frozen --group dev --python 3.12`, in a cloud container):
- After each merge, the package's own and related tests: W1-E's changed files all passed (including the three financial files and the egress test); W1-B 88 fill-engine and execution tests and 94 broker-conformance, paper-service and pause tests; W1-D 114; W1-C 158 with the migration tests and 302 related (transport, gateway, cost, expense, contracts, import boundary); W1-A 315 with all four new migration tests and the files its handoff names. All passed.
- **Full suite** `uv run pytest -p no:cacheprovider -rf` at `c4bde76` (all five merges), with CI's `TRADE_GRAPH_PAID_CALLS_ENABLED=false` and `TRADE_GRAPH_LIVE_ENABLED=false`, 105 min: **6664 passed, 0 failed, 23 skipped**. That is no failure at all, so no base comparison was needed; W1-E's run at its own head was 6297 passed. The commit that adds this entry changes only documents, planning files and the packaged snapshot; the tests that read them were re-run on it (results in that commit's message).
- `uv run ruff check src tests scripts/check_repository_hygiene.py scripts/verify_offline_acceptance.py`: all checks passed. `python3 scripts/check_plan.py`: 46 tasks valid. `python3 scripts/test_planning.py`: 11 OK. `python3 scripts/check_repository_hygiene.py` (and `--staged` before each commit): passed. `git diff --check`: clean. A pattern scan of the whole wave diff for common key formats found nothing.
- CI's offline acceptance step `scripts/verify_offline_acceptance.py` on the final tree: 464 of 464 passed, 40 of 40 cases passed, gate complete; the artifact hygiene check on its report passed.

Missing credentials and real-data checks (step L2 on the owner's PC): one real sample of each news feed (every feed host and TypeSafe's documentation were refused from the cloud); an optional opt-in Jev call if the owner gets a key; one real Perplexity call with the D125 test key, confirming the endpoints, field names and both prices (the Search API is priced high at USD0.005 per request, unconfirmed); re-reading Kraken's and OKX's fee pages (unreachable from the cloud; the records keep the build-plan figures and say so). The 2026-10-12 Perplexity price card goes stale on 2026-11-11.

Open, for later packages:
- W1-C found that a repeated provider request id raises `IntegrityError` out of `BudgetGateway.commit` and leaves the reservation `RESERVED`. Not fixed; it belongs to the gateway's owner.
- Before real Jev calls under the protected deployment (W6-D, L3), bind Jev checks to protected budget origins and decide whether the runtime evidence collector reads `news_jev_attempts`.
- The paper broker's take-profit and one-cancels-the-other orders, and quote-path marketable limits charged as takers, go to T34 (W4-A).
- Suite speed: the run took 105 minutes here against W1-E's 54, and the GitHub job limit is 120. W1-E traced about half the time to `runtime_environment_sha256()` re-hashing the standard library on every pin check; that is protected engineering code, so a fix needs its own package or an owner decision. Smoke tasks keep a 270 s deadline while each cycle call may run 600 s.

Next: **wave 2 can start** (W2-A T29 practice range, W2-B T31 coin scanner, W2-C T40 HQ spending), then its integrator. Owner: as before, switch on the price schedule (L1), check that Perplexity auto top-up is off, and optionally get a Jev key.

---

# Owner decisions settled; cloud build plan; branch on GitHub — 2026-10-16

Branch `claude/day-trader-engine-20261012`, pushed to GitHub (`origin`) as the integration base for cloud sessions. Plan and documents only: no code changed, nothing deployed, no paid or private-exchange call.
- **[D138](docs/DECISIONS.md):** the owner accepted every recommendation in the build plan's "Decisions that need you" (1–10): 3% daily loss limit, 20% per coin and 80% total, the rule-based coin list, the Perplexity budget method (USD20 alert while testing, then about 1.5× observed per month), no practice-range pass mark, 00:00 UTC day start, a scored test of at least four weeks and 30 trades, the 40% AI allowance floor, the stock source later, and the D137 news-alarm defaults. They reach the installation through an owner policy revision at deployment.
- **[D139](docs/DECISIONS.md) and [CLOUD-BUILD-PLAN.md](docs/CLOUD-BUILD-PLAN.md):** the remaining tasks are 18 cloud packages in 7 waves (wave 1: T44, T28, T36, T33 and C1, a clean test suite), an integration session per wave, and local steps L1–L4 for private data, credentialed smoke tests, installation and the scored run (T43). Packages write `planning/handoffs/<package>.md` instead of this file or `planning/progress.json`; decision numbers D140–D239 and state migrations 0026–0042 are reserved per package.

Found on the owner's PC, done outside this file's earlier entries (night of 2026-10-15, UTC; private store in the WSL user's data folder, not in Git):
- **The 2026Q3 Kraken archive was imported** (`Kraken_OHLCVT_2026Q3.zip`, 576,348,633 bytes, 10,054 members, run from the `candle-collector-runtime` checkout under the collection lock). 5-, 15- and 60-minute import, 22:30–22:36Z: status ok, 9,055,201 rows read, 9,046,369 inserted, 8,832 duplicates (an earlier BTC/USD 15-minute probe), 0 rejected, 0 outside the quarter, 672 USD pairs in 2,016 series; 7 catalog pairs absent from the file (DEBIT, DNA, EURR, PNT, USDR, USDSUI, ZCHF). 1-minute BTC/USD and ETH/USD: 263,843 inserted, 0 rejected. Coverage checked for BTC, ETH and SOL: 2026-07-01 00:00Z to 2026-09-30 23:59Z. The candle file is about 1.13 GB.
- The open-time convention the importer assumed is **not yet checked** against the collector: the archive ends 30 September and the collector's candles start 13 October. Plan step L1 checks it with a one-slot Trades backfill.
- October is mostly missing: only the 2026-10-13/15 smoke runs and one scheduled-runner test (2026-10-15 21:05Z, 1-minute BTC/USD and ETH/USD, 1,440 candles) are stored.
- Commits `138aa5a` (scheduled collection runner with one lock and kept reports, tests in `tests/unit/test_run_candle_collection_script.py`) and `6d01220` (`scripts/register_candle_schedule.ps1`). **The Windows tasks are not installed:** Windows refused task creation from the agent session. The owner runs the script or permits it (plan step L1).

Checks: `python3 scripts/check_plan.py`, `python3 scripts/test_planning.py`, `python3 scripts/check_repository_hygiene.py` and `git diff --check` (results in the commit that adds this entry). Before the push, the 152 commits not yet on GitHub were scanned for credentials and private data: the hygiene script passed, the Perplexity test key's value appears nowhere in them, and no common key pattern matched.

Next: cloud wave 1 on the owner's start (T44, T28, T36, T33, C1) and local step L1. Owner: switch on the price schedule, check Perplexity auto top-up is off, and optionally get a Jev key.

---

# Phase D1 data foundation (T25–T27) in source, not deployed — 2026-10-15

Branch `claude/day-trader-engine-20261012` (WSL worktree `day-trader-engine-20261012`), not pushed. Streams were built in three worktrees and merged: archive and Trades backfill `a35e959` (stream head `9d07a75`), collector `09b3c2a` (`dea575e`), indicators `272b01c` (`882ad04`); integration fixes and documents follow (tip `987bba8`). Decisions [D132–D136](docs/DECISIONS.md). **Nothing was deployed, pushed or run against the installation;** the stopped service, its containers and `/opt/trade-graph-subscription` were not touched. No paid, private-exchange or Perplexity call was made. The only network use was public Kraken GET requests: recording fixtures (about 25 spaced requests), a few small live smoke runs, and read-only header checks of the three quarterly archive files.

What exists (all in `src/trade_graph`, with documents in `docs/`):
- **T25** — a separate private candle file (`runtime/market_data.sqlite`, [CANDLE-STORE.md](docs/CANDLE-STORE.md)): append-only revisions, receipt-time availability, point-in-time and replay reads, coverage reports. The Kraken USD pair list with stablecoin, yield-token and currency marks ([PAIR-CATALOG.md](docs/PAIR-CATALOG.md)). A bounded, rate-limited collector for 1, 5, 15 and 60 minutes ([KRAKEN-CANDLE-COLLECTION.md](docs/KRAKEN-CANDLE-COLLECTION.md)): `trade-graph collect-kraken-candles`, `candle-coverage`.
- **T26** — a local-file Kraken OHLCVT archive importer ([KRAKEN-ARCHIVE-IMPORT.md](docs/KRAKEN-ARCHIVE-IMPORT.md)): `import-kraken-archive`. A public Trades backfill with a resumable cursor, source reconciliation and precedence ([KRAKEN-TRADES-BACKFILL.md](docs/KRAKEN-TRADES-BACKFILL.md)): `backfill-kraken-trades`, `reconcile-candles`.
- **T27** — TA-Lib 0.8.2 and NumPy 2.5 locked; a 26-entry indicator menu with bounded settings; an engine that reads completed candles through the store and reports missing candles per indicator; cross-checks against pandas-ta-classic and talipp (test-only) and hand-derived reference values ([INDICATOR-MENU.md](docs/INDICATOR-MENU.md)). The hourly Trader fields are `hourly-price-features-v3`, computed by TA-Lib; `pullback_from_high`, `pullback_depth` and `midpoint_distance` are retired and `highest_high_20` is new. Stored v1 and v2 snapshots stay readable (v2-made fixtures are tested).

Evidence:
- Full suite, `uv run pytest -p no:cacheprovider -rf` at `28689d4` (36 min): **6274 passed, 20 failed, 12 skipped** (was 4070 passed at `3b7f062`). 18 failures are the known set: `e2e/test_dashboard_loop`, 4 `test_paper_soak`, 5 of the 6 flaky `test_plugin_staging` cases, 3 `test_progress_dashboard`, 4 `test_progress_projection` and `test_subscription_smoke::test_cycle_runs_only_fresh_research_and_trader_through_protected_handlers`. Two were new. `test_kraken_catalog::test_the_fixture_is_a_small_recorded_excerpt_with_the_required_cases` was my merge's fault (the combined fixtures README had a different heading); fixed in `987bba8` and passing. `test_financial_recovery::test_recovered_legacy_subscription_rows_are_sealed_before_first_normal_checkpoint[usage]` hit the 2-second ledger-replay helper limit under full-suite load (it is timing-sensitive under load, as earlier runs noted) and passed twice alone. `test_engineer_isolation` passed this time. `ruff check src tests` passes. `python3 scripts/check_plan.py` valid.
- Every stream was written test-first and then reviewed by two independent reviewers (acceptance evidence with mutation checks; invariants and adversarial inputs). Their findings were reproduced before each fix: for example an id-gap taint lost on a backfill resume, a ZIP with missing bytes escaping as a traceback, a rate-limit answer to the pair list not stopping the run, the same indicator value differing with the requested count, and hand-written max/min passing the "no in-house formula" test.
- **Live smoke, labelled `live_public_smoke`, not a test** (2026-10-15, BTC/USD and ETH/USD, kept in ignored `runtime/market/`): `collect-kraken-candles --smoke` exit 0, status ok, 3 requests, 1,440 candles stored, 677 USD pairs listed, 591 eligible. `backfill-kraken-trades --smoke` for BTC/USD 12:45–13:00Z: 3 requests, 3,000 trades, 1 candle built, no id gaps. `reconcile-candles` of that slot against Kraken's own candle: open, high, low, close, volume and trade count identical; only vwap differed (Kraken rounds it to one decimal), by 0.00004%. One slot only.
- Synthetic end-to-end: 95 days of 15-minute candles for five pairs imported from a synthetic archive with a planted gap, replayed in one window read; benchmark of 525,600 synthetic candles at about 22,000 per second and about 107 bytes each.

Findings that change earlier assumptions:
- Kraken's OHLC fills quiet minutes with flat zero-volume rows; it does not omit them. A missing collected candle means "not collected" ([CANDLE-STORE.md](docs/CANDLE-STORE.md) corrected). `since` is inclusive for OHLC and Trades.
- Trades backfill cost: BTC/USD makes about 164,000 trades a day (about 165 requests, 8 minutes at the safe rate per day of gap); BAT/USD about 13,700. A 90-day backfill of a busy pair is about 12 hours.
- Disk: about 118 bytes per candle. A first pass over 591 pairs at 5, 15 and 60 minutes plus BTC/ETH at 1 minute is about 151 MB, then about 29 MB a day (2.6 GB a quarter). All 591 pairs at 1 minute would add about 9 GB a quarter.
- This PC's clock ran about 153 seconds ahead of Kraken's on every request (Windows Time service stopped). That is the safe direction for availability times; the collector refuses a run if the clock goes backwards.

Not done or open:
- **No real Kraken archive has been imported.** The importer is verified on synthetic archives only, and the file layout (timestamp unit, candle open or close, folders in the ZIP) is unconfirmed. Nothing was downloaded. Quarterly files are 576 MB (2026Q3), 538 MB (Q2) and 524 MB (Q1); the complete archive is about 10 GB in five parts. The owner decides on the download.
- **Nothing is scheduled.** The collector must run again within each window (1-minute candles reach back 12 hours, 5-minute 2.5 days, 15-minute 7.5 days, 60-minute 30 days) or gaps appear. A full pass is about 1,775 requests and about 98 minutes at the safe rate. Two collectors at once are not coordinated.
- 1-minute detail is limited to BTC/USD and ETH/USD by default. `--one-minute-pairs` takes a list or `all`. The owner decides which pairs keep 1-minute detail.
- 608 of 677 USD coins are not individually reviewed for stablecoin or yield status; reports list them. Names that need a human look: U, STBL, UNITAS, CAP, DEBIT, RE, ST and USDE. The scanner (T31) is meant to add a price-peg check. It is not built.
- Two rate limiters exist (`rate_limit.py` for the collector, `trades_rate_limit.py` for the backfill) with the same counter model but different settings; unify them when one service process runs both (event engine). The `slow-trend-pullback` template now reads `highest_high_20` and `latest_close`; a database that already exists keeps its old template until the owner approves replacing its stored baseline (the retired ingredient is supplied meanwhile). Undefined oscillator readings in a market with no movement are still labelled `ready`. The owner dashboard's trend label still divides two averages (display only; D136 reads D118 as exempting it).
- Before deploying: build the protected image with the NumPy and TA-Lib wheels (`scripts/build_protected_image.py` was not exercised), re-pin the kernel manifest, and add the candle file to the runtime layout. The hourly features still read `hourly_candles`; moving them to the new store is later work.

Next: Phase D2 (T28 first) after the owner's go-ahead and decisions above; T44 (Jev shadow) is the owner's next task by D137.

---

# Jev news alarm planned as the next task, not built — 2026-10-15

Branch `claude/day-trader-engine-20261012`, not pushed. Plan and documents only: no code, no Jev or other paid call, nothing deployed.
- **Owner decision [D137](docs/DECISIONS.md):** the next task is a news alarm built on Jev, TypeSafe AI's decision model. Jev only notices important headlines between Leader news rounds. It does not rank coins, label market types, check strategy rules or estimate prices. Recommended defaults wait for the owner: feeds, a 5-minute check, pinned `jev-1.13.0`, wake the Leader (and the Trader for held coins), and a USD5 testing alert.
- **Tasks:** T44 (phase DN, ready now) runs the Jev connection, headline collection, receipts and would-wake logging in shadow mode. T45 turns the alarms into real wake-ups after T35, T38 and T39. Both sit right after T27 in `planning/tasks.json`, so `scripts/next_task.py --prompt` offers T44 first. Build plan section "DN — News alarm with Jev", owner decision 10, and source S40 (TypeSafe's official models and API pages, read 2026-10-15).
- **Missing credential:** no Jev key exists. The owner signs up at console.typesafe.ai. T44 can be built and tested without a key.
- **Phase D1 note:** T25–T27 are merged in source with decisions D133–D136 (`28689d4`); their status entry and progress evidence are in the section above.

Checks:
- `python3 scripts/check_plan.py`: 46 tasks valid.
- `python3 scripts/test_planning.py`: 11 OK. `test_day_trader_plan_order` was updated for the DN tasks and their place in the order.
- `scripts/next_task.py`: ready tasks are T44, T28 and T36.
- `git diff --check`: clean.
- `tests/integration/test_progress_projection.py` and `test_dashboard.py`: only the 4 known `test_progress_projection` failures. The packaged `progress_catalog.json` already lacked T23–T43 and still does.

Next: T44 on the owner's go-ahead. Owner: confirm the D137 defaults and get a Jev key. The open D1–D7 decisions in the build plan are unchanged.

---

# Owner follow-ups to the D114 fix list, not deployed — 2026-10-13

Branch `claude/day-trader-engine-20261012`, not pushed; nothing deployed or started. Owner decisions recorded as [D130](docs/DECISIONS.md):
- **Unusable Trader answers fail only their turn** (`548d9c3`). The cases are a schema or check failure, research cited outside the snapshot, an unknown strategy, a cut-off, declined or temporary model answer, and a state change during the turn. The task is FAILED with `failure_scope: turn` and a `trader_turn_set_aside` event, without a system pause. Lost credentials, unknown outcomes, quota, budget, exhausted attempts and software exceptions still pause. HQ words these turns plainly. Tests: `tests/integration/test_trader_turn_failure_scope.py` (failed on the prior commit with `KeyError: 'failure_scope'`; the lost-credentials case pauses before and after), `tests/unit/test_hq_turn_failure_copy.py`, and the updated `test_order_refusal.py::test_malformed_trader_reply_fails_only_that_turn`.
- **A euro reference-rate outage no longer blocks buys** (`3b7f062`). The paper and live feeds keep their quotes, retry after 5 minutes and record `reference_fx_health` start and end events. Tests: `tests/integration/test_reference_fx_outage.py` (the old code raised `OSError` out of the poll, which marked the feed failed); `test_feed_diagnostics` now covers only the metadata and ticker stages.
- **Provisional money-risk settings** (`5943d5c`, docs only): 3% daily loss, 20% per coin, 80% total, a rule-based coin list, and a Perplexity budget at about 1.5 times the observed cost after testing. They are applied when T32 is built and revisited in a bankroll session.

Checks: `uv run ruff check src tests` passed. Related runs gave 584 passed with 1 known failure, then 196, then 358 passed. Full suite at `3b7f062` (36 min): **4070 passed, 20 failed, 8 skipped**. That is the known set listed below plus `test_engineer_isolation::test_checker_wall_and_output_limits_are_enforced`, a timing case that passed alone twice and also fails under load on the base.

---

# Day-trader build plan and D114 fix list in source, not deployed — 2026-10-13

Branch `claude/day-trader-engine-20261012` (WSL worktree `day-trader-engine-20261012`), not pushed. **Nothing was deployed, started or run against the installation**; the stopped service, its containers and `/opt/trade-graph-subscription` were not touched. No paid, private-exchange or Perplexity call was made.

- **Plan** (`3838a2d`): tasks T23–T43 (release R2, phases D0–D7; T30 is LATER) and [the build plan](docs/DAY-TRADER-BUILD-PLAN.md) with the owner decisions still open. `scripts/next_task.py` now lists ready R1 and R2 tasks.
- **D114 fix list** (T23, T24; `71aa92a`..this commit; defaults in [D129](docs/DECISIONS.md)):
  - Hourly features v2: windows anchor at the newest available completed candle (at most 2 h late), per-field `stale` status and missing hours; a pending newest candle no longer withholds every field (`223f5e0`).
  - Trader and Research snapshots carry `latest_close`, up to 24 recent completed candles available at as-of and the missing slots (`ebf1d47`, `88a87ab`).
  - Schedules stay on their persisted grid; a fresh Trader task in the normal subscription operation waits up to 300 s after the hour for the just-closed candle; `history.expected_candle` says plainly whether it was stored (`d07296c`, `6cf81a2`).
  - Exposure checks compare USD with native USD equity; a carried EUR reference rate only labels EUR views; other-currency cash uses the inverse reference rate (`12f5be1`, `e1415ad`).
  - A refused order is a recorded decision plus `order_refused` event, shown to the next Trader turn (`portfolio.recent_order_refusals`) and in HQ in plain words; the task succeeds and no system pause is set (`ab0c42d`, `8d7e9f1`, `a3594ed`, `e3e1015`, `b4fe523`).
  - Default mandate is `["slow-trend-pullback"]`; `trade-graph mandate-cleanup` previews and `--confirm` installs a new revision for older databases (refused while the service runs); FLATTEN exits work under a named-strategy mandate (`71aa92a`, `c1626c2`, `da384f3`, `95d55b4`).
  - `tests/integration/test_first_run_replay.py` (`0de75e7`) replays the first run end to end on the subscription path with synthetic data: a Saturday with a stale EUR rate, candle wait, latest close and stale-or-ready features, an authorized buy, a refused buy without a pause, and the refusal shown on the next turn. It fails on the pre-fix source.

Evidence (WSL, synthetic data only):
- Each fix was written test-first; every new test file failed on `3838a2d` (for example `KeyError: 'latest_close'`, `StaleState: provisional valuation`, `('FAILED', 'MANAGE_ONLY') == ('SUCCEEDED', 'RUNNING')`, next due `04:00:07` instead of `04:00:00`, `invalid choice: 'mandate-cleanup'`) and passes now. An independent reviewer re-ran each track's new tests against the pre-fix source.
- `uv run ruff check src tests`: all checks passed. `python3 scripts/check_plan.py`: 44 tasks valid; `python3 scripts/test_planning.py`: 11 OK.
- Combined targeted run of all tracks' related files: 894 passed, 1 failed (the pre-existing timeout assertion below).
- Full suite `uv run pytest -p no:cacheprovider -rf`, at `6cf81a2` (35 min): 4043 passed, 18 failed, 8 skipped; at `daae41d` (34 min): **4053 passed, 19 failed, 8 skipped**. All failures already fail on the base: `e2e/test_dashboard_loop` (`KeyError: 'degraded'`), 4 `test_paper_soak` CLI tests, 6 `test_plugin_staging` 0.3-second-limit cases (flaky under load), 3 `test_progress_dashboard` and 4 `test_progress_projection` (Mission Control rejects the `updated_at` timestamp; `/progress` now redirects signed-out readers to `/login` with 303, which the stale 401 assertion misreads; it is not a privacy leak), and `test_subscription_smoke::test_cycle_runs_only_fresh_research_and_trader_through_protected_handlers` (timeout/max-output assertion).

Before deploying (owner-approved, as before): re-pin the protected kernel manifest and reseal the image (every `.py` change alters the digest), then deploy through the financial-manifest transition with the paper account in MANAGE_ONLY. Re-collect any Kraken read-only venue observation afterwards, because `execution.py`, `ledger.py` and `books.py` are part of its source digest. Then, with the service stopped and after `trade-graph backup`, run `trade-graph mandate-cleanup` on the state database and `--confirm` only on the owner's yes.

Open follow-ups (not in this slice):
- ~~A malformed Trader reply, research cited outside the snapshot or a stale snapshot still fails the turn and sets the system MANAGE_ONLY pause~~ (resolved by D130, see above).
- ~~A Frankfurter outage still marks the feed failed, which blocks buys~~ (resolved by D130, see above).
- A refused turn still counts as a failed version-trial sample. Dispatch-time rejections are not yet in `recent_order_refusals`. HQ shows a Trader task waiting for its candle as an ordinary queued task.
- Research carries the history twice (market and sources); widening the coin list (T31/T32) needs per-symbol budgeting.
- `api/financial._mark_maximum_age` finds no mandate before a new revision's creation time and falls back to the policy quote age (same value today).
- Mission Control accepts neither the `todo` status nor the full `updated_at` timestamp.

Next: Phase D1 (T25–T27) after the owner's go-ahead. Owner decisions in the build plan: daily loss limit, maximum per coin, paper coin list, Perplexity budget after testing, trading-day start, test-run length.

---

# Trade Graph HQ final full-suite check, not deployed — 2026-10-10

Branch `claude/super-dashboard-20261009` at `67a774d`. `uv run ruff check src tests` passed. Full suite (`uv run pytest -p no:cacheprovider -rf`, 36 min): 3948 passed, 23 failed, 8 skipped (Node tests skipped in WSL; Windows Node runs them). Of the 23:
- 19 are the failures already recorded for base `3504248`: 3 `test_progress_dashboard`, 4 `test_progress_projection`, 1 `test_subscription_smoke`, 4 `test_paper_soak` and the e2e dashboard loop (`KeyError: 'degraded'` before the page this branch edited), plus `test_plugin_staging`'s 0.3-second-limit cases, which are flaky on both commits (6 of 6 this run).
- 4 failed only in this full run: 2 in `test_financial_recovery_history` and 2 in `test_financial_transition`. Run alone, both files passed 56 of 56, twice in a row. They are timing-sensitive under full-suite load, not caused by this branch's changes. The earlier full run at `b1252c5` did not show them.

Desktop visual check (headless Edge, synthetic fixtures only, 1 440×900 light and dark): independent reviewer found nothing remaining. Not deployed; going live needs the owner-approved financial-manifest transition with every portfolio in MANAGE_ONLY and the service drained.

---

# Trade Graph HQ visual-check fixes (round 2), not deployed — 2026-10-10

Branch `claude/super-dashboard-20261009`, after `1021655` (decision D113). **Nothing was deployed, pushed or run against the installation.**
- **Punctuation stays with a time or word:** opening or closing punctuation that touches a tappable time, term or amount is kept on its line with it (`span.hq-nobreak`, `white-space: nowrap`). The allowance card reads "(tomorrow 3:00)." instead of leaving ")." alone on the next line at 1 366, 1 440 and 1 600 px. An opened time's full-time box and technical codes may still wrap, so nothing overflows on a phone.
- **Fixture copy and times:** the busy fixture's waiting turn says the real "Waiting to finish."; the "At … it decides without research notes." and "It decides at … without research notes." lines in quiet, live and reader, and every Pause card's "These would wait" list, use time tokens built with the real copy and `_waiting_turns` (so they show the reader's own time, not Cairo clock words). The real list names every scheduled department, so quiet, busy and allowance-paused now list four.
- **Desktop balance (≥ 1 200 px):** when the right-hand column would end more than 300 px below Story, Your bills (then Review check-ins) moves into the bottom row, stacked in its first third (`balanceDecide` in `hq-render.js`, run after renders and size changes, never while the focus is in the panel). The document order, anchors, iPad and phone layouts are unchanged. At 1 440 px the blank under Story falls from 834 to 119 px (safety stop), 695 to 223 (allowance paused), 1 328 to 614 (silent), 1 615 to 901 (empty) and 1 671 to 956 (offline); quiet, busy, the busy week, live and reader are unchanged.

Checks: `uv run ruff check src tests` passed; `uv run pytest -p no:cacheprovider tests/test_hq_*.py tests/unit/test_hq_*.py tests/integration/test_hq_*.py tests/integration/test_live_home.py` gave 284 passed, 8 skipped (Node tests skipped in WSL); Windows Node `node --check` on all `hq*.js` and `node --test tests/js/hq_*.test.mjs` gave 45 passed. Headless Edge at 1 440×900 (dark and light): no horizontal overflow and no script errors on the busy-week, quiet, safety-stop, allowance-paused, empty, silent, busy and live snapshots; no punctuation apart from its time or word at 1 024 to 1 600 px; resizing to 1 199, 1 023, 800 and 390 px puts the panels back.

---

# Trade Graph HQ visual-check fixes (round 1), not deployed — 2026-10-10

Branch `claude/super-dashboard-20261009`, after `b14c8ee` (decision D112). **Nothing was deployed, pushed or run against the installation.**
- **Calendar groups chain by gap:** a deadline joins its group when it is at most 1 hour after the group's latest member, up to 12 hours from its first (`CALENDAR_GROUP_GAP_SECONDS`, `CALENDAR_GROUP_SPAN_SECONDS`; the contract checks `until` against the 12 hours). The busy synthetic week's 40 note expiries are now one row ("40 research notes stop being used", 19:30 to 02:00 tomorrow in Cairo) instead of four identical ones; 63.6 KB for 14 days. A range ending on another day says so in the agenda ("to 2:00 tomorrow") and Up next. One note reads "A research note stops being used".
- **Week strip:** chip titles keep words whole (no break inside a word; a non-breaking hyphen in "Your check‑in"; soft hyphens only where a short label needs one on an iPad, e.g. "Allow-ance refill"; a department name stays on one line with an ellipsis if its column is too narrow). Chips show only the start time. Short labels for allowance refills, extra reviews, review time limits, trial ends and permission ends.
- **Desktop columns:** Prompt settings and Help & glossary are one full-width, two-column row below both columns (`.hq-decide-more`; phones, the tab bar and iPad portrait unchanged). The left-column gap under Story on the busy week went from about 770 px under Story to none, with about 700 px under Your bills instead; on quiet it fell from about 1 950 px to about 200 px.
- **Small fixes:** the "Authorised model" field's placeholder is "the model I authorised for Trade Graph", not this page's address; a calendar warning is one text beside its icon ("at 12:56." without a gap).
- **Fixtures:** future department turns, estimates and Up next regenerated with the real `_future_turns` (no 40–56 h gap; research and learning series too), allowance lines from the real `allowance_plain` ("Refills tomorrow 3:00", a time, not "Mon 00:00 UTC"), and the real copy for the stuck Leader job (no second "won't retry"), the safety-stop turn problem and the Leader's last turn.

Checks: `uv run ruff check src tests` passed; `uv run pytest -q -p no:cacheprovider tests/test_hq_*.py tests/unit/test_hq_*.py tests/integration/test_hq_*.py tests/integration/test_live_home.py` passed (8 Node tests skipped in WSL); Windows Node `node --check` on all `hq*.js` and `node --test tests/js/hq_*.test.mjs` gave 43 passed. Headless Edge at 1440×900 (light and dark) and 1024: no horizontal overflow and no script errors on the busy-week, quiet, safety-stop, allowance-paused, empty, silent, busy and live snapshots; 900 and 375 px layouts match the previous commit except the one extra "tomorrow" line.

---

# Trade Graph HQ desktop-check fixes, not deployed — 2026-10-10

Branch `claude/super-dashboard-20261009`, after `6aa968d`. Five issues from the owner's desktop visual check (decision D111). **Nothing was deployed, pushed or run against the installation.**
- **Calendar groups:** research-note expiries (and improvement-idea expiries and Engineer time limits) of the same department, status and texts within two hours of the group's first are one item with the additive, omit-not-null `count` and `until` keys and a counted title, e.g. "13 research notes stop being used", shown as "19:30 to 21:30" in the agenda, "19:30–21:30" in the week strip and "Between … and …" in Up next. The ten expiry items are now the ten soonest groups. The busy synthetic week drops from 10 single expiry items to 4 grouped ones (62.6 KB for 7 days, 63.2 KB for 14 days).
- **Conditions said once:** the estimates condition ("Only if the team is running and not paused …") is one line above the agenda, and an equal condition on consecutive future rows of one day is shown on the first only. Up next keeps each turn's condition (U1).
- **Desktop (≥ 1024 px):** the two columns flow independently (floats; page order, anchors, iPad and phone layouts unchanged), so a tall "Needs you" no longer leaves a gap under the map.
- **Up next:** the line under the clock time is "in 2 h" today, then "tomorrow" or the date.
- **Fixtures:** Learning's cadence is every 3½ days (`302400`), with its next turn 3½ days after its last and the real copy ("Learning finished its turn (every 3½ days).").

Checks: `uv run ruff check src tests` passed; `uv run pytest -q -p no:cacheprovider tests/test_hq_*.py tests/unit/test_hq_*.py tests/integration/test_hq_*.py tests/integration/test_live_home.py` passed (8 Node tests skipped in WSL); Windows Node `node --check` on all `hq*.js` and `node --test tests/js/hq_*.test.mjs` gave 39 passed. Headless Edge at 1440×900 (light and dark), 1024, 900 and 375 px: no horizontal overflow and no script errors on the busy-week, quiet, safety-stop, allowance-paused, empty and silent snapshots.

---

# Trade Graph HQ review fixes integrated in source, not deployed — 2026-10-10

Branch `claude/super-dashboard-20261009`. Review base `721bc8a`; `claude/hq-fix-wp1` (backend B1–B20, decision D110) merged as `9e60e8f` and `claude/hq-fix-wp2` (frontend F1–F13) as `b1252c5`, both `--no-ff` without conflicts; integration fix `070b0b4` plus this status commit. This closes the HQ review fix plan: 49 findings, all real. **Nothing was deployed, pushed or run against the installation**; every check used temporary databases or fixtures. Shipping still needs the owner-approved financial-manifest transition with every portfolio in MANAGE_ONLY and the service drained ([D105](docs/DECISIONS.md)). By owner choice, the second independent review round was skipped.

What changed for the owner:
- **Wording and safety:** a live account is never called "practice", in the page, prompts or help. "Pause AI" never weakens a stronger Leader pause. The Stop card and holdings see a new open order at once. The safety-stop cause comes only from evidence close in time, and its Stop preview says "Partly reversible" and "Not recommended".
- **Calendar and turns:** the calendar and room tiles never show an impossible or past turn as normal. Overdue tiles say Waiting, Due now or Overdue, and Up next shows each turn's condition. Overdue items sit under "Now", and estimated turns more than 48 hours ahead appear as one "about every …" series per department.
- **Money and counts:** an unreadable reserve rule says so, never "none" (the review prompt says it too). "Since you last looked" says "at least" when the story was cut. Long AI-allowance waits are counted in full, and older story pages are always reachable.
- **Using the page:** open panels and messages survive the 30-second refresh. Bill wizards move focus at each step and take the share as a percentage. Unsent requests expire after 24 h, and resent ones say "Already done". The page fits on a phone, with HQ's own footer.
- **Robustness:** the story endpoint answers 422, never 500, for odd dates. Redaction is anchored. HQ never takes the database lock. A busy week fits the 80 KB payload budget.

Contract cross-check (FIX-PLAN §1): the backend emits all three additive keys: `team.allowance.stop_line_state`, `departments[].next.status` and `calendar.estimates`. All are omit-not-null and validated by `hq_contract.py`. All nine fixtures carry them. Real snapshots from a fresh database, the offline demo loop and the busy synthetic week (7 and 14 days) validate. Rendered through the HQ scripts in the Node harness, they show no script errors and no "undefined"/"NaN"/"null" text. The Story `window_start`/`next_before` meaning matches the client's "at least" logic. One mismatch was fixed (`070b0b4`): the review prompt ignored `stop_line_state`.

Commands (WSL2 Ubuntu unless noted; temporary databases only):
- `uv run ruff check src tests`: all checks passed.
- **Full suite, run once at `b1252c5`:**
  - Command: `uv run pytest -q -p no:cacheprovider`, which took 33.5 min.
  - Totals: 3965 collected, **3940 passed, 17 failed, 8 skipped**. The failure count comes from the short summary. The project's `addopts -q` plus `-q` hides pytest's own total line, so the totals are derived from collect-only, the 17 listed failures and the 8 skips seen in a rerun of the only test files that can skip.
  - All 17 failures are known and fail the same way on base `3504248`:
    - 3 `test_progress_dashboard`
    - 4 `test_progress_projection`
    - 1 `test_subscription_smoke`
    - 4 `test_paper_soak` (database open `ProgrammingError`)
    - 4 of the 6 flaky `test_plugin_staging` 0.3 s cases
    - `e2e/test_dashboard_loop`, failing at line 37 with `KeyError: 'degraded'`. A copy of that test without that one assertion passes, including the branch's added `/journal` page.
  - The 8 skips are `tests/test_hq_js_behaviour.py` (no Node in WSL).
- **After `070b0b4`:**
  - `tests/test_hq_static.py tests/test_hq_template.py tests/test_dashboard_pages.py tests/test_hq_js_behaviour.py`: 87 passed, 8 skipped.
  - With Windows Node v24.14.1: `node --check` passed on all 7 `hq*.js` files, and `node --test tests/js/hq_*.test.mjs` gave 36 passed.
- **Synthetic smoke (FastAPI test client, 30 warm requests per route):**
  - Fresh `trade-graph init` database: `/` 200 (30.4 KB, p95 6.7 ms), `/journal` 200 (83.1 KB, p95 10.8 ms), `/api/v1/hq` 200 (17.1 KB, p95 7.2 ms), `/api/v1/hq/story` 200 (p95 4.9 ms).
  - Offline demo loop database: `/` 200 (59.7 KB, p95 9.2 ms), `/journal` 200 (120.4 KB, p95 21.0 ms), `/api/v1/hq` 200 (44.1 KB, p95 9.9 ms), `/api/v1/hq/story` 200 (p95 5.7 ms).
  - Both databases: signed-out `/` and `/journal` redirect to `/login`, and extreme story dates give 422 (or 200 for the far future), never 500.
  - Busy synthetic week: 65.9 KB (7 days) and 66.5 KB (14 days), within the 80 KB budget.

Synthetic only. There was no real installation data, no Windows Edge or iPad check, and no 768 px or full light/dark browser pass. The Stop preview's "Partly reversible" wording was not checked in a browser. Owner question Q1 (guided safety-stop recovery) is unchanged. T17/T21, funded/economic/live and off-host acceptance are unchanged.

---

# Trade Graph HQ integrated in source, not deployed — 2026-10-10

Branch `claude/super-dashboard-20261009`, based on deployed source `3504248` plus the frozen HQ contract `35da7f4`. Work packages merged in spec order: WP3 owner check-ins (`5c9b534`), WP1 HQ read model (`88c75f8`), WP2 HQ page (`3a984bb`); WP4 integration `a313014` and its docs commit. **Nothing was deployed, pushed or run against the installation.** Shipping needs the owner-approved financial-manifest transition with every portfolio in MANAGE_ONLY and the service drained ([D105](docs/DECISIONS.md)); implementers do not deploy.

What changed for the owner: after sign-in, `/` is [Trade Graph HQ](docs/HQ-DASHBOARD.md), a plain-language, phone-first home built only from saved records (right now, needs you, up next, HQ map, calendar, story, money, decide). The previous home moved unchanged to `/journal` (keeps `?end_at=`); every other page is under "Detailed views". New routes: `GET /api/v1/hq`, `GET /api/v1/hq/story`, owner-only `GET`/`POST /api/v1/owner/checkins`. Migration `0025` adds the append-only owner check-in store. HQ decisions reuse the existing owner endpoints with fixed bodies (D107); no new authority. AI/allowance state never uses the cached readiness verdict (D106).

Commands (WSL2 Ubuntu, isolated temporary databases only):
- `uv run ruff check src tests` — all checks passed.
- `uv run pytest -p no:cacheprovider` (full suite, at `a313014`) — 3839 passed, 18 failed in 35 min. Every failure also fails on base `3504248` (checked in a temporary `git archive` copy): `e2e/test_dashboard_loop` (the "sk-" check matches "task-capacity"), 3 `test_progress_dashboard`, 4 `test_progress_projection`, 1 `test_subscription_smoke`, 4 `test_paper_soak` (database open `ProgrammingError`), and 5 `test_plugin_staging` resource cases whose trusted replay must finish within a 0.3 s wall clock (flaky on both: 4–6 of 6 fail on base and branch across repeated runs).
- Focused HQ/dashboard/security set (24 files including `tests/security`) after the final edits — 773 passed. New WP4 tests `test_hq_routes`, `test_hq_owner_writes`, `test_migration_0025` — 18 passed.
- Synthetic smoke with the real CLI `init` (USD10,000 paper, EUR reporting) and the offline demo loop: `/`, `/journal`, `/progress`, `/api/v1/hq` and `/api/v1/hq/story` all 200; signed-out `/` and `/journal` redirect to `/login`. `/api/v1/hq` is 17 KB (fresh) and 44 KB (demo loop), median 6.5 / 9.5 ms and p95 7.6 / 11.1 ms over 40 warm requests.
- Synthetic browser check (throwaway 127.0.0.1 server over the demo loop database, session injected server-side): 1440×900 light, 768×1024 light, 375×812 dark; no console or CSP errors, no horizontal scroll at 375 px; "Detailed views" opens without JavaScript; Pause then Resume previews and confirms against the real endpoints (owner config read, then POST, both 200); the journal cutoff form goes to `/journal?end_at=…`.

Not done or still open: check-in plan/done/skip, the `.ics` download, bill entry and the plain-HTTP-from-LAN copy fallback were exercised by tests and WP2's fixture browser run, not in this browser session. No real installation data, Windows Edge or iPad check. Open owner questions Q1 (guided safety-stop recovery), Q2 (check-ins in the review snapshot) and Q3 (ship together) keep their recommended defaults. Unrelated pre-existing failures above remain. T17/T21, funded/economic/live and off-host acceptance are unchanged.

---

# Live owner dashboard deployed — 2026-10-09

Branch `codex/live-dashboard-20261009`; executable source `c488e67`, based on newer capacity/logging source `493c25c`. Final immutable image `sha256:34abee6a44d00647f3e53bc8fd1febce6b7399fa18875146309f9677969c477b`, Python 3.12.15 / SQLite 3.53.4. Exactly one NORMAL paper worker and RUNNING owner controls are verified after authenticated reconciliation/resume. Original database inode, capability key, witness scopes, financial/native/task/account history, earlier failures and the accepted historical observation gap remain. Strategy, schedules, Sol-only routing, empty fallback, quota reserve and disabled live/billed/extra routes are preserved. No inference or trade was forced; an existing scheduled task subsequently completed and applied naturally.

Home separates operational health from economic coverage; it exposes safe department/native/application work and attempt usage, immutable owner bill/allocation evidence, historical economic periods and a complete scoped authenticated review export. HTML navigation redirects to sign-in; API authentication/CSRF remain. Browser reads reuse quota metadata, while owner admission and dispatch/retries retain fresh guard checks. See [dashboard use](docs/LIVE-DASHBOARD.md) and [sanitized review](docs/reviews/2026-10-09-live-dashboard.md).

Final focused tests: 302 passed, zero failures/errors/skips. Ruff, Node syntax, repository hygiene, plan validation and diff checks pass. Actual installed image/source/mount/network verification, 1000 concurrent WAL commits, ownership/integrity/foreign-key checks and all nine actual Linux isolation checks pass. These probes made zero inference/provider API requests. Actual Windows Edge synthetic and deployed sign-in/home/API authentication/export/decision/offline/mobile checks pass without browser script errors or horizontal overflow; the final image was reconfirmed in Windows Edge. Development tests used isolated temporary databases, never the operating database.

Two supported new-AI pauses: 13:23:31–13:33:18 UTC (587.293 seconds) and 13:53:29–13:56:21 UTC (172.396 seconds), three hours later in Cairo. Management continued until graceful worker exit; no AI work was in flight. The first handover retained a temporary management-only admission refusal caused by the copied old isolation-proof pointer; the pointer was rebound to actual new-image proof, preserving policy. Both transitions preserve old scopes and immutable prefixes. Previous containers, private backups and diagnostic evidence remain. See the review for the initial process-wait/expiry-format observations and final manual-control freshness regression.

Operational health is measurable from service heartbeat, control state, retained work and resource counters. Reported usage has missing fields/attempt coverage; configured routing is separate from unknown native model echo/internal retries. Native USD capital and current reporting cash/exposure are measurable, but inception FX, owner bill/completeness evidence and accepted recovery limitations leave complete trading/net economic conclusions unknown. No actual owner bill was invented or entered. Sanitized source is published on `codex/live-dashboard-20261009`; private runtime/evidence stays local. Private operating records, exact financial observations, token totals and browser/export artifacts are not published. T17/T21, funded/economic/live and off-host acceptance remain open; the full suite was not rerun.

---

# Subscription capacity repair deployed — 2026-10-09

The recovered paper installation runs one NORMAL worker after the supported authenticated manifest transition and reconciled owner resume. Subscription-only limits are now finite 192 tasks / 1 GiB RAM / one CPU / no additional swap. The create specification and exact independent verifier changed together; existing isolation and all non-subscription limits are preserved. Controller-launched worker diagnostics now retain three rotating 256 KiB sanitized event files rather than losing all operational output.

Branch `codex/resource-capacity-20261009`; executable commits `986c19d`/`97f1290`, image verifier `60cf18e`/`905e2c7`. Deployed immutable image `sha256:8f949f8052cd734932f1b345b5aab47258f8e4d0caafd3875f6209f33eb9717d`, Python 3.12.15 / SQLite 3.53.4, unchanged dependency lock/native tools/graph and paper strategy configuration. The original database inode/key, independent witness, historical financial/AI records and accepted reporting recovery gap remain. The naturally scheduled Trader completed and applied its result; no inference was forced. Whole-container resource limits produced no new events in the observed run. This is bounded operational evidence, not worst-case capacity or full historical/economic acceptance.

196 focused deployment/diagnostic/controller/service tests and 92 financial continuity tests pass; actual installed image/WAL/ownership/isolation checks pass. A transient synthetic startup observer read is now retried within the existing finite readiness bound. Actual account, quota, scheduling and inspection details are private. Research expiry alignment is documented only; schedules and strategy are unchanged. See [capacity repair and diagnostics](docs/reviews/2026-10-09-subscription-capacity.md) for measured cause, verification and logging limits. T17/T21 remain in progress; broader acceptance statuses are unchanged.

---

# Normal paper operation resumed; repaired Trader verified — 2026-10-08

The owner separately authorized normal paper AI on the recovered installation.
The documented management-to-NORMAL handover completed without replacing the
database, clearing history or replaying failed work. Authenticated Owner Resume
returned RUNNING after reconciliation at 17:55:41 UTC. The first existing scheduled
Trader inference completed at 17:56:02 UTC and its decision persisted at
17:56:04 UTC: one application attempt produced a validated, persisted `no_action` decision and report using
fresh, feature-ready inputs and the repaired native/reporting context.

Executable source remains `780dce1`, immutable image
`sha256:4d0ee615aa0e15d535aa2025422404891ab6acc9fdef6f9ea90d6f99ec9ed1e7`,
Python 3.12.15 / SQLite 3.53.4. The management subprocess exited gracefully; both
service leases and inode ownership were released before authenticated Start
Trading launched one continuous NORMAL worker with the owner pause still in
force. Financial integrity, foreign keys, protected prefixes and retained
terminal AI records passed before resume. The accepted eleven known historical
public/reporting gaps, possible additional reporting loss and the recovery's
new evaluation period remain explicit.

Fresh official metadata admitted the configured Sol-only route; every dispatch
and retry retains the strictly-more-than-40% guard, comprising a 30-point reserve
and 10 points of headroom, with a maximum 30-second metadata age. The shorter
quota window remains unavailable. Protected `gpt-6.1-sol` selection, empty
fallback, ordinary included allowance and no spendable credits were verified.
Configured selection is not a provider model echo. Live trading, separately
billed APIs and paid extras remain disabled; normal schedules remain enabled.

Retained totals after this Trader task are 16 subscription attempts and 14
invocations. All prior failures and unknown costs remain. Historical Research
and Learning successes are retained; repaired Research publication, Leader and
Optimisation success remain unverified, and Engineer has no actual installation
run. Completed inference alone is not successful departmental application.
No order or fill was created by this run. No profitability or broader task
acceptance claim follows from the successful no-trade decision.

Independent post-task verification through 18:01:30 UTC found one NORMAL worker,
a current heartbeat, owner RUNNING, a responsive authenticated dashboard and the
visible historical-gap banner. Protected files/session/database identity and all
prior attempt/invocation rows remained unchanged. Post-task accounting and a
consistent private checkpoint passed. Financial reporting remains provisional
because the preserved history and unknown cost limitations remain.

Final observation at 18:07:12 UTC confirmed RUNNING and current 18:00 UTC
features, with 80% reported weekly allowance. An intermittent recovery-panel
verification warning was traced to cross-process witness-lock contention;
a direct retry verified the same accepted gap, and full integrity/FK checks
passed. The final overview displayed the gap banner again. This display limitation
is retained in the evidence rather than hidden by a state or history change.

See [resume evidence and verification limits](docs/reviews/2026-10-08-normal-paper-resume.md).
The prior MANAGE_ONLY handoff below is historical.

---

# Partial-history recovery deployed; AI remains paused — 2026-10-08

The owner-approved reporting-only recovery is running in a separate private
installation. Originals, backup, original witness/keys and previous image are
preserved outside Git. The authenticated incident records eleven known public
price/reporting gaps plus possible additional reporting loss. Full historical
continuity is not certified and the corruption cause remains unknown.

Independent exact-record audit supports retained financial/AI continuity with
explicit legacy-witness proof limits. All 15 attempts/13 invocations, failures and
unknown usage/costs remain. Full SQLite integrity, foreign keys, balanced Decimal
accounting and protected financial-prefix validation pass. No account reset,
manufactured missing payload or AI replay occurred.

Deployed executable source `780dce1`, image
`sha256:4d0ee615aa0e15d535aa2025422404891ab6acc9fdef6f9ea90d6f99ec9ed1e7`,
Python 3.12.15 / SQLite 3.53.4. Authenticated database-identity recovery and then
manifest transition preserved original witness commitments. Exactly one
MANAGE_ONLY management worker runs inside the dashboard's protected container;
one owner holds both service leases, the inode lock excludes a second owner,
and no AI task was claimed. Dashboard: <http://localhost:8000/> (authenticated
pages and login verified). Fresh Kraken quotes, completed hourly history and
reporting FX have actual receipt times; all ten required features per symbol are
ready. A consistent private checkpoint passes integrity/FK checks.

184 focused regressions and independent 147-case review selection passed, plus
actual-image lock/WAL/isolation probes, Ruff, hygiene and planning checks. See
[dated evidence and limits](docs/reviews/2026-10-08-partial-history-recovery.md).
Registered operating checkout: `first-paper-cycle`; integration branch:
`codex/paper-recovery-20261008`. Sol-only, empty fallback and the existing quota
guard remain unchanged. AI, live trading, separately billed APIs and extras remain
disabled. No inference was initiated for recovery. A later dashboard Stop was
respected; explicit owner instruction then restored one MANAGE_ONLY worker and
all post-start checks passed again.

The next owner step is a separate explicit normal-paper AI resume instruction.
The operator must hand over from the management subprocess to one NORMAL worker
while the owner pause persists, verify fresh quota/readiness, then use authenticated
Owner Resume with the current revision. The exact order is in the
[normal operating runbook](docs/NORMAL-PAPER-OPERATION.md). Historical acceptance
and broader economic/live gates remain unchanged.

Earlier dated sections below are historical evidence, not current service status.

---

# Non-destructive recovery audited; continuity gap keeps installation stopped — 2026-10-08

The owner explicitly authorized a separate private recovery copy and a supported
recovery path. Originals remain unchanged: damaged database and any surviving
sidecars, original witness, protected owner files/keys, healthy backup, incident
evidence and previous image. A private preservation manifest verified all copied
file hashes. AI remains paused; live trading, separately billed APIs and paid
extras remain disabled.

The newest verified healthy backup passes full SQLite integrity and foreign-key
checks. Native journal postings balance using Decimal; the retained paper account
has no orders or fills. All 15 attempts and 13 invocations match retained incident
evidence, including failures and unknown usage/costs. The existing witness MAC,
scope, checkpoint and committed prefixes verify without creating fresh authority.

Continuity is **not** verified after the backup cutoff. Independent raw-page and
official SQLite recovery-tool checks found at least seven post-backup valuation identities
whose full payloads are absent, plus four missing public-observation payloads.
Exact identities and page/row evidence remain private; damaged trees cannot prove that these are the only lost rows. Recoverable newer marks,
FX and observations are preserved separately as unpromoted evidence. A header
repair or successful SELECT over a damaged tree is not an authoritative recovery.

No recovery approval was installed, no witness was resealed, no database was
switched, no AI task was replayed, and no dashboard/management service was restarted.
The actual deployed image remains the previous image from source 6bf6697; its
dashboard container is stopped. The registered operating checkout remains
first-paper-cycle, branch codex/normal-paper-2026-10-07. Recovery integration is
on codex/paper-recovery-20261008, verified source e2bae96; test/build evidence is recorded in
[the recovery review](docs/reviews/2026-10-08-paper-recovery.md).

Source checks passed: 173 financial regressions, nine independently written regression cases, and an independent 150-case recovery/checkpoint/transition/budget/locking selection. An immutable candidate image using SQLite 3.53.4 passed actual reserved-lock retention, descriptor lifetime, single-owner exclusion and 1,000 concurrent WAL commits on isolated WSL ext4 storage, followed by full integrity and foreign-key checks. The original installation was never mounted for these checks. This is candidate-image evidence, not a deployment or production acceptance.

Windows reports healthy storage and WSL ext4 has available space with no matching
kernel I/O/filesystem errors. Detailed hardware counters are unavailable. These
observations do not establish the corruption cause, which remains **unknown**.

The next operational step is locating authentic complete payloads for the missing
records in another backup or retained evidence. After continuity is proven, use
the reviewed authenticated identity recovery, then manifest transition, then one
MANAGE_ONLY management/dashboard worker and public-data validation. AI still
requires a later explicit authenticated owner resume and fresh quota admission.

Earlier dated sections below are historical evidence, not current service status.

---

# Repair checkpoint; deployment blocked by storage corruption — 2026-10-08

The authenticated owner MANAGE_ONLY control was applied before repair work and
achieved `managing`. Public collection, reconciliation and dashboard initially
continued, with no in-flight subscription attempts. A consistent private backup,
the existing financial witness, all owner files and all 15 retained attempts were
preserved before source changes. No AI call was initiated for this repair.

The existing worker subsequently exited during an unexpected storage failure.
The dashboard first reported SQLite malformed-image errors; later fresh reads
reported `file is not a database`. Earlier full host/container integrity checks
had both passed. The damaged database and available evidence are retained, and
the old dashboard container is stopped. A forensic copy with only its header
repaired fails full integrity checks across several trees; it is not authoritative.
The verified pre-repair backup remains intact. No restore, reseal, database
replacement, manifest transition, deployment or resume has been performed.

The registered operating checkout is `first-paper-cycle`, branch
`codex/normal-paper-2026-10-07`, rather than the main Linux checkout. Integrated repair source is `7be13b1`; the equivalent sanitized publication
source is `4570876` on `codex/paper-repair-2026-10-08`, preserving the newer remote
manual review schedule. All five requested source repairs and the reproduced
SQLite-lock defect repair are implemented. Final regression/review evidence is
recorded in the linked repair evidence: **363 focused regressions passed** and
**108 independent continuity checks passed**, with no unresolved review findings.
Ruff, staged hygiene and planning checks passed. The installed image remains the previous image from source
`6bf6697`; its dashboard container is stopped and new repairs are not deployed. See [repair evidence](docs/reviews/2026-10-08-paper-repair.md).
Only `gpt-6.1-sol`, empty fallback and the existing strictly-more-than-40% reported
quota guard remain configured. Live trading, separately billed APIs and extras
remain disabled. Leader's original cause is still unknown.

Earlier dated sections below are historical evidence, not current service status.

---

# Normal paper operation running — 2026-10-07

The owner authorized morning operation and replaced the overnight start hold.
Existing owner controls reconciled/resumed the hold and started one normal paper
service at 08:12:48 UTC. The graph and loopback dashboard are running; normal
future schedules remain enabled. There is no mandatory one-cycle stop.

The owner's [manual review schedule and fresh-context prompts](docs/PAPER-REVIEW-SCHEDULE-2026-10-07.md)
cover 7 October 12:30/16:30/20:30 local, optional bedtime, and 8 October 12:30.
This documentation starts no review job, model work or runtime change and records
no additional operating results.

The first actual operating results are recorded in the
[morning evidence review](docs/reviews/2026-10-07-morning-paper-operation.md).
The active protected source remains **`6bf6697`**. New source `c056faf` adds
citation guidance and sanitized native diagnostics and passes relevant checks,
but its image update is blocked by the project's unimplemented financial manifest
continuity transition. The exact verified prior image/pins were restarted on the
**current database and witness**, preserving all records. No database rollback or
additional inference occurred. At 08:50:15 UTC the graph reported RUNNING, a fresh
heartbeat, AI available and no AI blockers; normal future schedules remain enabled.

- Native WSL Codex 0.160.1 uses ChatGPT subscription authentication. All six AI
  departments and every retry are pinned to **gpt-6.1-sol**; protected
  `fallback_profiles: []` and the sole-model catalog were verified. Provider model
  echo is unavailable; configured/pinned model is not mislabeled as an echo.
- Fresh weekly metadata is mandatory. Every reported supported window must have
  **strictly more than 40% remaining** before each dispatch/application retry:
  **30% reserve + 10 percentage points of headroom**. Missing shorter readings,
  reporting lag, shared-account consumption and unbounded active-call percentage
  consumption prevent an exact all-window or post-call floor guarantee.
  Metadata/model unavailability pauses new AI; management and dashboard continue.
- Refreshed public Kraken BTC/USD and ETH/USD each have 168 completed hourly
  candles through Oct 7 08:00 UTC, with zero gaps. Quotes were freshly acquired
  before AI work. The latest available ECB reference is dated Oct 6; EUR reporting
  remains provisional. This path makes no private Kraken requests.
- Research and Trader have completed validated invocations and applied results.
  Research retained two attributed findings; Trader recorded a legitimate
  `no_action`. Learning also succeeded. No orders/fills were created, and native
  accounting/financial tables match the prestart backup. Any paper orders/fills
  are **local Trade Graph simulations**, not Kraken-hosted orders.
- Initial Leader work failed on three native attempts. Optimisation inference
  completed but its applied result failed citation validation. Both remain failed
  records, with unknown costs; neither is counted as departmental success.
  A narrow citation-interface fix preserves strict validation but is not deployed.
  Original native error details were discarded, so their exact cause is still
  unknown. Deploying both fixes requires the missing protected continuity
  transition; this is existing project design, not an obsolete diagnostic gate.
- At the first post-run observation: five invocations, seven application attempts,
  four completed attempts and three failed attempts. Internal native retry counts,
  unreported usage fields, attributable shared fees and all-in costs remain unknown.
  Recorded zero API expenses do not mean subscription work was free.
- Relevant quota/migration/recovery/dashboard checks passed (218 focused cases at
  `6bf6697`); the integrated citation/diagnostics follow-up passed 149 selected
  cases at `2a5cb82`, followed by 164 final selected cases at `c056faf`. The existing
  manifest-transition refusal regression also passes. Overlapping runs are not
  summed. Both image probes/production inspections passed; active-image source,
  running security/network and model policy were reverified after recovery.
  No unrelated full-suite gate.

Original checkpoint `e36e447`, Windows local changes, original databases, owner
settings and private evidence are preserved. Sanitized publication uses a new
fast-forward checkpoint on the established working branches. Exact account
readings are excluded from current public files; previously published history is
retained because force-push/history rewriting is not authorized. Main is unchanged.
Real trading, separately billed APIs, paid extras and purchases remain disabled.
Earlier dated sections below are retained historical evidence, not current policy.

---

# Historical owner model selection checkpoint — 2026-10-07

The owner selected **GPT-6.1 Sol only**, exact ID `gpt-6.1-sol`, for all six
departments and retries through the current Codex subscription. [D97](docs/DECISIONS.md)
and the [normal operating runbook](docs/NORMAL-PAPER-OPERATION.md) record this
choice and disable authorization for alternate-model/provider fallback.

At that documentation-only checkpoint, the last prepared primary route already selected this model and had no admitted
fallback. The private PC must verify its protected primary profile, empty fallback
list and single-model catalog. This documentation-only checkpoint makes no new
claim about PC configuration, current service state or actual inference results.
No runtime records, owner files, acceptance states or source behavior changed.

---

# Normal paper preparation complete; graph awaiting owner start — 2026-10-07

The latest owner instruction is **do not start the graph**; wait for an explicit
green light tomorrow morning. The prepared normal paper dashboard is available at
<http://127.0.0.1:8000>. At 2026-10-06 23:16:50 UTC it reports **STOPPED**, zero
active graph services, MANAGE_ONLY owner hold, AI available and live unavailable.
A web server running is not a graph run. No start or scheduled model work occurred.

Source **fbed4be**, integration branch `codex/normal-paper-2026-10-07`, preserves
original `e36e447`, both original databases, owner settings and private evidence.
[Normal operating evidence](docs/reviews/2026-10-07-normal-paper-operation.md) and
[normal runbook](docs/NORMAL-PAPER-OPERATION.md) supersede diagnostic limits below;
older sections are retained historical records, not current operating policy.

- Native WSL **Codex 0.160.1**, ChatGPT subscription, configured **gpt-6.1-sol**:
  actual authentication/account metadata, ordinary allowance, credential refresh
  directory, protected provider egress and final-image process isolation verified.
  No interactive login is currently required. The configured model has not run.
- Normal departmental scheduling/recurring Optimisation, supported native retries,
  configurable application attempts and independently verified subscription fallback
  are implemented. No mandatory diagnostic, one-cycle cap or postcycle pause.
  The current owner hold remains authoritative. Claude is installed but signed out;
  no fallback route is currently configured/admitted. Billed APIs/extras remain off.
- Public refresh at **2026-10-06 23:09:42 UTC**: BTC/USD and ETH/USD each have
  **168 completed hourly candles**, Sep 29 23:00 through Oct 6 23:00 UTC, zero gaps.
  Captured quotes were 0.792074s / 0.378020s old; ECB USD/EUR **0.88739**, dated
  Oct 6, retrieved at 23:09:42 UTC. Data ages while stopped and must refresh at start.
- Research/model/application attempts **0**, Trader decisions **0**, proposed orders
  **0**, paper fills **0**. No validated Research or legitimate model no-trade result
  exists yet. Existing **USD10,000** virtual cash remains intact, no open positions
  or orders; dashboard valuation EUR8873.9. Trading/net economic PnL and shared
  subscription cost remain unknown/null. Paper orders will be **local Trade Graph
  simulations**, not Kraken-hosted orders. No private Kraken request occurred.
- Final source focused verification includes 126 usage/adapter/profile/departments
  checks and the final 129 receipt/pricing/adapter regressions; overlapping runs are
  not summed. Ruff, planning checks, actual nine-boundary image probes and strict
  production container inspection pass. No unrelated full-suite prerequisite added.
  Exact private quota/runtime receipts and original evidence remain outside Git.

No primary subscription connection blocker remains. Hosted research-search result
integration and a signed-in compatible fallback are missing optional capabilities;
short-window capacity, auto-reload setting, attributable usage and costs remain
unknown. These are disclosed rather than fabricated or relabeled as zero. The
remaining actual normal graph/Research/Trader evidence awaits the owner's start.

---

# First actual Kraken-data paper cycle — 2026-10-07

**Operating milestone pending owner subscription login.** Tested source `643f185`,
branch `codex/first-paper-cycle-2026-10-07`, descends from preserved `e36e447`.
[Concrete operating evidence](docs/reviews/2026-10-07-first-paper-cycle.md) and
[exact owner login command](docs/FIRST-PAPER-CYCLE.md#owner-login-and-included-usage-check)
supersede the earlier native-install/egress limitations below.

- Native WSL Claude 2.1.292 is signature/checksum verified and separately installed;
  final protected image, actual nine-check isolation, strict production mount
  inspection and public HTTP transport pass. Native subscription login is absent;
  extra usage/auto-reload disabled status and Claude capacity need owner verification.
- Final public refresh: 168 completed BTC/USD and ETH/USD hourly candles each,
  latest close 2026-10-06 22:00 UTC, zero gaps. Final stored quotes were 19.625036s /
  18.9596s old at 22:26:17 UTC. ECB USD/EUR 0.88739 dated 2026-10-06 was retrieved
  22:25:59 UTC. Freshness must be renewed after login.
- Diagnostic Research, normal Research, Trader and Leader inference attempts: 0.
  No validated Research, Trader decision, simulated order or fill. This is not a
  recorded model no-trade. Original USD10,000 portfolio/financial journal,
  owner budget and mandate remain intact. Explicit owner pause is now MANAGE_ONLY,
  achieved managing; service stopped and portfolio flat/no open orders.
- Three journaled native invocations maximum, each 120s / 4096 tokens / one turn / 256KiB;
  zero CLI transport/timeout retries and one structured attempt, no application
  retries/repair/fallback/API billing. A valid diagnostic gates the cycle;
  fresh quotes precede Trader. Model-free continuous management works without auth.
- 445 selected source tests pass, zero failures/errors/skips, 47.190s; Ruff and
  planning checks pass. All 95 pre-existing private files and progress sidecar
  preserved. Dashboard read evidence retained privately. No private Kraken request,
  purchase, real order or withdrawal. Unknown subscription/preparation/shared costs
  are not counted as free. Shared Codex capacity was observed privately and is not Claude
  quota; the short-window reading was unavailable.

Functioning commands include the runbook's native version/auth-status checks,
`PYTHONPATH=src "$VIRTUAL_ENV/bin/python" -m pytest` from the repository root with
the reviewed virtual environment activated and the recorded selected tests, and
the protected image's `check-subscription`. The credential-free
actual image probes/public transport passed. `research-subscription` and
`boot-subscription` are implemented/tested bounded phases, **not credentialed
successes**. Protected operating-state migration and actual inference remain
pending. No task acceptance status or live authority was advanced.

---

# Implementation status — owner startup controls and recorded checkpoints

## Current owner startup implementation — 2026-10-06

Tested source **b1ca4c5**, branch `codex/start-controls-2026-10-06`, based on clean WSL
checkpoint `937437e708bda23a0286de7d9c10fa4e6de9b219`. This section supersedes the
older preflight limitations below. The independent Windows checkout is preserved.

- **Dashboard/controller implemented:** Start Trading starts or attaches to one
  locked service; Start Optimisation queues one bounded owner cycle. Automatic
  scheduling and pending automatic descendants are disabled. Starts, failures,
  quota blocks and interruption recovery are durable and visible. Pause keeps
  protection active; nonflat Stop retains management until verified flat.
- **Subscription adapter implemented/tested, operationally blocked:** all six roles
  use one provider/model, one journaled invocation per task, local structured
  validation, deadlines/cancellation and no retry/repair/fallback. Actual WSL
  namespace tests deny synthetic private files and host access. Invalid/changed
  subscription admission blocks AI while deterministic management continues.
- **Protected live path implemented/tested, disabled and not commissioned:** exact
  image/config/account/owner admission precedes credential construction. Limits,
  durable attempts, uncertain reconciliation and duplicate suppression remain
  protected. Live Resume queues locally; only the exclusive worker can apply it
  after whole-account reconciliation/current grant checks. It cannot create authority.

**Research model/result: none; inference attempts: 0.** Windows Codex 0.125.0
had verified ChatGPT login earlier; the final login-status check cannot parse
the current `ultra` reasoning setting, even with a per-command `high` override.
Existing configuration/login files are unchanged. Current CLI login is unverified.
Current official 0.160.1 retains built-in request/stream
retries 4/5 without a supported zero-retry override. No update solves that blocker.
Windows Claude 2.1.280 is logged out; documented controls need native 2.1.285+.
Neither native WSL CLI is installed. Official native login, disabled extra usage/
credits/purchases, root-pinned CLI and actual intended-image isolation remain
required. A subscription-specific protected provider-egress assembly is also
**not implemented/verified**: standard image networking is off and live egress
permits Kraken/FX. Login alone cannot commission the route. See
[subscription evidence and official sources](docs/SUBSCRIPTION-ADAPTER.md).

Read-only official Codex account metadata was observed during that historical
preparation. Exact account capacity, reset and credit readings are retained
privately. The shorter-window reading was unavailable. These Windows observations
did not establish WSL admission or Research usage. Claude quota and subscription
fee/preparation/model cost attribution remained unknown, not free. Real expenses,
live allocation and virtual paper capital remained separate.

Public history was **not refreshed**. Retained coverage: 168 completed hourly
candles each BTC/USD and ETH/USD, 2026-09-29 14:00 through 2026-10-06 14:00 UTC,
zero gaps at collection; current freshness is not claimed. Separately billed model
calls, purchases, private Kraken requests, real orders and withdrawals: **0**.
No continuous paper AI operation or live authorization occurred.

Verification: **435 final-source tests pass**, zero failures/skips, in 39.96 seconds:
startup/subscription/isolation/controller/live, private SQLite, dashboard financial
projections, manual cycles and public proxy controls. Unknown subscription
inference/shared fees keep economic P&L and all-in costs provisional/null;
recorded expenses, allowance, trading P&L and virtual capital remain unchanged.
Independent review passes 84 financial/dashboard tests and 17 proxy tests, with
no remaining critical finding. Earlier 275, 245 and 48-test slices passed.
The frozen 90d4f55 full run stopped after **2,905 passed / one failed** on a stale
test expectation about explicit `trust_env`. Correction 98af9a4 passes all 227
unit/funded-profile regressions. Collected node IDs match successful XML results
across these runs and the final 435-test slice: **all 2,998 collected cases covered**.
This is combined verification, not a clean monolithic final-source full run.
An earlier interrupted run exposed private-file/soak regressions; both were fixed
and retested. Source/tests Ruff, JavaScript syntax, 23-task DAG and 16 planning/
progress tests pass. The final offline wheel installs into the previously frozen
dependency environment; all 58 packages are compatible. Installed imports, private
0018 storage, CLI/manual scheduling and unknown-fee projections pass. One final
disposable deterministic paper tick created no orders, AI calls or expense receipts.
A new protected image was not built or commissioned. Historical image evidence
does not certify this source. See [verification](docs/reviews/2026-10-06-start-controls.md).

From the repository root:

```bash
uv run --frozen trade-graph dashboard --database runtime/trade_graph.sqlite --session-file runtime/owner-session.json
uv run --frozen trade-graph subscription-status --provider codex
uv run --frozen trade-graph subscription-status --provider claude
uv run --frozen trade-graph run --mode paper --database runtime/trade_graph.sqlite --once
```

Open `http://127.0.0.1:8000/progress` using the existing private owner session.
Start Trading currently supplies deterministic paper management; AI/Optimisation
stay unavailable until protected subscription admission. `--once` is a bounded
deterministic paper tick. Protected live commands/provisioning are documented in
[the commissioning guide](docs/LIVE-STARTUP-COMMISSIONING.md).

Next: native Claude login/disabled extras, approved provider egress and actual
intended-image isolation, then exactly one refreshed public Research diagnostic.
Continuous paper evaluation is a separate gate. Live additionally needs actual
read/trade-only Kraken provisioning, whole-account/uncertainty/restart/alert
verification, independent signed reviews, separate real allocation/loss allowance,
current protected image and explicit owner live grant. T17–T22 acceptance is
unchanged. Tests, Research inference, continuous paper and live operation remain
separate evidence.

The clean WSL installation fast-forwarded to tested source **b1ca4c5** on
`codex/orchestrator-takeover-2026-10-04`. The dashboard alone restarted on loopback
8000 with its existing session/database: WSL and Windows login returned HTTP 200;
authenticated progress is paper/IDLE, AI blocked, live disabled, automatic
Optimisation false, with zero graph worker starts or subscription invocations.
All existing financial/progress table rows and all 30 protected-file contents/modes
match the consistent backups. The only old-table addition is schema version 0018;
new provider metadata records the observed quota and refusal. No owner policy or
budget changed. Sanitized verification documentation follows this tested source;
the final local checkpoint is reported with the handoff. No push.

**Owner continuation focus, 2026-10-05:** finish the existing crypto paper
operation and forward evaluation. Read [the reassessed next steps](docs/CRYPTO-NEXT-STEPS.md).
The [multi-asset direction](docs/MULTI-ASSET-DIRECTION.md) is saved and deferred.
The direction notes preserve task acceptance status. The Mission Control
dashboard continuation below adds presentation and bounded test tracking;
the round 4 full-suite, installed-wheel and protected-image evidence remains
historical and does not certify this newer source.

## Owner-local bounded Research preflight — 2026-10-06

The WSL checkout fast-forwarded from `9dedf97` to `e920cb7`. The real existing
paper database now retains **168 completed hourly candles each for BTC/USD and
ETH/USD**, covering 2026-09-29 14:00 through 2026-10-06 14:00 UTC, with **zero gaps**.
A single bounded public-data tick refreshed quotes/FX, created no decisions and
reported no failures. Private consistent backups and preservation manifests
confirm financial/order/decision/budget/expense/authority records, retained
private Kraken evidence, progress history and owner-session bytes unchanged.
The stale dashboard process was restored on loopback using the same database
and session; WSL and Windows `/login` returned HTTP 200. Windows checkout local
changes were untouched.

**AI analysis is blocked before inference.** Installed official Codex 0.125.0
is logged in through ChatGPT, and its official account/rate-limit metadata was
verified without starting a thread or turn. Its built-in provider's automatic
retries cannot be disabled using the documented provider configuration;
installed Claude Code 2.1.280 is logged out. Shared Codex config was restored
byte-for-byte after read-only compatibility checks. No Research model call,
real model/result, direct API spend, private Kraken request or live operation
occurred. Exact available quota and unknown subscription/preparation costs are
recorded in an ignored private preflight receipt, not a fabricated usage bill.

A public-only packet, prompt and locally checked result schema are prepared
privately. No production subscription adapter or continuous graph operation
was configured, and T17–T22 acceptance is unchanged. **162 relevant tests passed**;
full-suite/wheel/image checks were not rerun. See the
[commands, preservation evidence, official sources and exact next step](docs/reviews/2026-10-06-research-subscription-preflight.md).
Documentation checkpoint branch: `codex/research-diagnostic-2026-10-06`, source
base `e920cb7`. Next: official Claude subscription login plus quota/extra-usage
verification and a tested zero-retry isolated Research invocation, or an official
Codex zero-retry configuration. Refresh the public snapshot before inference.

## Bounded public Kraken hourly history — 2026-10-06

Verified source: `ce4dbb7cca688a657e65095bd3d5eabb435ab166` on
`codex/kraken-hourly-history-2026-10-06`, based on `9dedf976`.
Documentation follows that verified source. The integration publication target
is `codex/orchestrator-takeover-2026-10-04`; no merge to `main` is required.
The sanitized source/documentation checkpoint `25611438809ecadb3436c7338b0378b36efc4a83`
was fast-forward published and independently matched to the remote branch on
2026-10-06. This publication record follows that checkpoint. No force push,
`main` merge or new GitHub CI result is claimed.

An additive 0017 migration retains immutable completed BTC/USD and ETH/USD
hourly candle revisions alongside existing financial records and snapshots.
The public collector makes at most two fixed OHLC GETs, accepts 1–719 hours,
bounds each response to 1 MiB with a ten-second timeout, and always excludes
Kraken's final uncommitted candle. It has no account/credential request route.
Restart deduplicates unchanged evidence without renewing its availability.

Research and Trader now receive the fields requested by their pinned active
strategy templates in their actual retained worker snapshots and model
requests. Decimal precision 50/ROUND_HALF_EVEN, documented 20/50-hour means,
20-hour pullback and 24-hour range definitions, source manifests and actual
receipt-time availability accompany explicit insufficient/gapped/stale or
unsupported history. Future closes and later receipts cannot enter earlier
as-of inputs. Retained contexts remain unchanged after later collection and
restart. See [formulas, limits and operations](docs/KRAKEN-PRICE-HISTORY.md).

Verified on locked Python **3.12.15** under WSL2:

- **354 relevant tests passed** in 45.05 seconds, covering exact arithmetic,
  malformed/public transport bounds, revisions/deduplication/restart, additive
  migration preservation, actual Research/Trader worker snapshots and requests,
  concurrent writer/clock boundaries, future/later-available exclusion,
  explicit missing history, evidence citations and existing paper controls.
- Full source/test Ruff, the 23-task DAG/reference validator, ten planning
  checks, repository/staged hygiene and diff checks passed. Independent
  collector, storage and context reviews found no blocking findings.
- The final wheel built offline. A fresh isolated wheel installation acquired
  the unchanged locked runtime dependencies; installed CLI, 0017 migration,
  two scripted collections, exact features, context module and installed
  strategy templates passed. The initial offline-only install lacked cached
  dependencies; no lockfile or dependency versions were changed.
- An **actual public HTTPS smoke** in a separate disposable paper database
  collected **60 BTC/USD + 60 ETH/USD completed candles**, with zero missing
  requested hours and two GETs. Both latest closes were
  `2026-10-06T12:00:00Z`; post-response availability was
  `12:33:32.636891Z` and `12:33:34.371941Z` respectively. These are actual
  public observations, distinct from synthetic checks; raw candle/runtime
  files are excluded from Git.

Bounded owner-PC command, from the updated WSL2 checkout and existing private
paper database:

```bash
uv run --frozen trade-graph collect-kraken-history --database runtime/trade_graph.sqlite --hours 168
```

This session preserved the original Windows local modification, owner WSL2
checkout, financial/runtime databases and private owner evidence. No private
account request, owner database initialization/reset, paid model call or live
order occurred. USD10,000 virtual capital/EUR reporting and separate real
budget controls remain unchanged. Full-suite, protected-image rebuild and
intended-host acceptance were not rerun; funded/forward economic evidence,
complete venue conformance and **all remaining T17–T22 gates stay pending**.

## Historical authenticated Kraken read-only observation — 2026-10-06

The owner-local retained **BTC/USD authenticated read-only observation**
completed from `2026-10-06T12:05:45.367049Z` to
`2026-10-06T12:05:46.457662Z`, verified/imported at
`2026-10-06T12:05:46.500666Z`. A read-only, allowlisted extraction from the
private Mission Control sidecar reports collector-owned HTTPS, **four
authenticated private reads**, and verified completed instrument, account-fee,
balance, open-order and bounded-native-history stages. The source matched at
import; the owner-local source checkpoint was `9dedf976ef18944e96e7127ca1e2ce7006d0d243`.

This records the verified redacted historical projection. Its original
**60-second freshness window has expired**; importing or documenting it does
not renew freshness. This coding session made no private-account requests
and did not rerun credentials or reverify the native private evidence.
Credentials, account identifiers, balances, raw payloads and verification
keys are excluded from the public checkpoint. Both earlier and successful
observations and the owner's financial/runtime records remain retained.

Still pending: unrequested order lookups and earlier history; owner
eligibility/account identity; key-permission inventory and withdrawal
absence; protected-ledger reconciliation; write/cancel uncertainty and native
stop conformance; partial-fill reserve bounds; intended-host/dependency
admission; full T17–T22 acceptance, funded/economic evidence, protected image,
production and live authorization. The read-only observation establishes
scoped authenticated connectivity, not full venue conformance. Earlier
pending statements below describe their original historical checkpoints.

## Kraken public-catalog compatibility fix — 2026-10-06

Source fix: `571765f05f45c5e2871c6ed56b13d363490d4e92` on
`codex/fix-kraken-one-character-assets-2026-10-06`, based on the clean owner
checkpoint `5f7225e`. An owner-local redacting script located the retained
observation and matched its displayed projection ID without exposing private
scope, keys or native account responses. It reported only `instruments`,
`ValidationFailure`, and request method `Assets`.

The hash-matched retained **public** Assets response contains valid one-character
asset names/altnames. The old minimum of two characters fails before the selected
BTC/USD AssetPairs request. A length-only fix still failed on the public catalog's
`USD_CREDIT`; the final bounded grammar admits one to 32 characters, beginning
with an ASCII alphanumeric and allowing alphanumerics, dots and underscores
thereafter. Alias identity, exact selected-pair scope, coherent metadata
publication, Decimal normalization and all read/write authority controls remain.

Verified on locked Python 3.12.15: **264 relevant synthetic tests passed**, including
29 new catalog/boundary/invalid-input/default-disabled-write cases. Both one-character
and underscore regressions failed before their fixes. Command:
`uv run pytest tests/integration/test_kraken_live_adapter.py
tests/integration/test_kraken_identity_recovery.py tests/integration/test_venue_conformance.py
tests/integration/test_kraken_onboarding.py tests/integration/test_kraken_onboarding_flow.py
tests/unit/test_repository_hygiene.py`. Full source/test Ruff, planning validation,
repository/staged hygiene and diff checks passed. Independent review found no blockers.

A public-only replay of the entire retained Assets response plus an actual
public HTTPS AssetPairs GET through `KrakenRestTransport` passed and returned
exactly BTC/USD. No private Kraken requests, order dispatch, paid calls or
owner-local command rerun occurred. Before/after private digest/mode comparisons
confirmed retained evidence and runtime records unchanged. Services were not
restarted. The owner will rerun the bounded command; authenticated stages,
full-suite/wheel/image verification and T17–T22 acceptance remain pending.

## Owner-local Kraken read-only implementation — 2026-10-05

Integrated source checkpoint `7cd5605` on
`codex/orchestrator-takeover-2026-10-04`, descended from `caae082`.
The CLI and dashboard implementation commits are `12277de` and `4ab5e0d`.
Development used isolated WSL2 Linux-home worktrees, followed by a fast-forward
into the existing checkout. The earlier local setup evidence below was preserved.

`uv run trade-graph kraken-read-only --database runtime/trade_graph.sqlite
--owner-directory ~/.local/share/trade-graph-owner/kraken --symbol BTC/USD`
now provides hidden owner-terminal credential entry, exact typed authorization,
separate signed/pinned private intent and bounded native collection. It validates
the existing policy, active artifact, version/generation and current retained
proof before importing an allowlisted historical projection. Defaults are
60 seconds, 128 requests, five history pages and a seven-day history window;
the signed grant expires after two minutes. Credentials remain in the local
collector process and are not saved. Private grants and retained native evidence
remain outside the checkout and financial runtime directory.

Mission Control calls this **Kraken read-only account check**. Browser Run stays
disabled and provides CLI instructions. Results show verified completed stages,
original observation/verification times, actual owned HTTPS versus synthetic
injected transport, private-read counts and pending checks. Missing stages and
unrequested order lookups remain pending. Historical freshness expires from
the original observation time; imports do not renew it or claim full reconciliation.

Verified **655 relevant synthetic tests passed**, with no final failures/skips,
including the complete command/backend/collector/verifier/import/API/HTML path
and a retained partial permission failure. Full source/test Ruff, JavaScript
syntax, staged and repository hygiene, and diff checks passed. An isolated
in-app Chromium check confirmed the disabled button, readable pending gates,
expanded-result refresh and the change from fresh to stale after 60 seconds,
with no console errors. The initial broader fixture run had one database-mode
warning failure under umask 0022; the documented private umask 0077 resolved it.
The full suite, installed wheel and protected image were not rerun/rebuilt.

Before and after integration, private digests confirmed the financial database,
existing progress history, planning acceptance, lockfile, owner-session file and
earlier local evidence remained unchanged. No runtime account was initialized
or reset, and no authenticated Kraken request or paid model call was made.
The existing loopback dashboard was reloaded onto the new source using the same
database and session file. Windows `http://localhost:8000/login` returned HTTP
200; a read-only preflight validated the existing policy/artifact scope without
requesting credentials. Financial records and retained history stayed unchanged.
Actual account connectivity, owner identity, permission inventory, withdrawal
absence, ledger reconciliation and T17–T22 acceptance remain pending. The owner
can configure a read-only key privately and run the command next; see
[permissions and instructions](docs/KRAKEN-ONBOARDING.md).

## Owner Windows/WSL2 local setup — 2026-10-05

Scoped local operation was verified on the owner's Windows PC using WSL2
Ubuntu 26.04.1 LTS, Linux x86_64 and the Linux-home checkout
`~/trade-graph`. Implementation branch:
`codex/orchestrator-takeover-2026-10-04`; source checkpoint:
`caae08262b67edb38aa2854f99cf68ec5f13a020`. Python 3.12.15 and the
unchanged committed lockfile were installed with
`uv sync --frozen --group dev --python 3.12`.

No existing Linux runtime database was found. A fresh USD10,000 virtual paper
account with EUR reporting was initialized once. Its runtime directory and
owner-session file were verified at 0700 and 0600 respectively. The dashboard
is running on loopback; the actual Windows browser address is
`http://localhost:8000/login`. Windows localhost returned HTTP 200;
authenticated login, `/progress` and `/api/v1/progress` returned HTTP 200,
and unauthenticated progress access returned HTTP 401. The session token was
kept out of command output and chat.

Actual owner-authorized Mission Control results, retained in the private
`runtime/progress-runs.sqlite` sidecar: local rehearsal passed all **14**
scripted checks, with zero external provider calls and no Kraken orders;
all **four actual Kraken public checks** passed (server time, online status,
BTC/ETH pair rules and bid/ask spreads). Private-account connectivity and
order execution were not tested. **36 targeted dashboard/progress/CLI tests**,
Ruff for source/tests, the 23-task DAG validator and **10 planning tests**
passed. The full suite and protected image/host acceptance were not run.

`trade-graph doctor` returned **degraded**, with database integrity/schema
current and warnings for a provisional financial result, pending model
routing and no persisted market observations. Its separate network/public
probe fields remain pending; Mission Control's public-check evidence does
not populate those fields. The bounded paper service `run --mode paper
--once` completed one tick with reconciled recovery, zero decisions and no
failures. Paid calls and live trading remained disabled; actual spend was
zero. No continuous trading service was started.

Private local setup, doctor and maintenance reports are retained under
`runtime/`; no tokens or private financial records are published in Git.
This scoped evidence supersedes earlier statements that this local dashboard
path had not been tested. Historical cloud proxy failures remain historical.
T17–T22 acceptance states and all funding, private-account, economic,
protected-host/image, production and live-authority gates remain unchanged.

## Kraken onboarding continuation — 2026-10-05

**Latest host direction:** test on the owner's existing Windows PC using WSL2
Ubuntu; defer a cloud-server purchase. The [local guide](docs/WINDOWS-LOCAL-TESTING.md)
provides initial dashboard and no-paid checks. The local evidence above now
records scoped Windows/WSL2 verification. Current model adapters use direct APIs;
Claude Code/Codex subscription-backed runtime adapters are not implemented.
[The interface review](docs/reviews/2026-10-05-local-subscription-options.md)
records official noninteractive CLI capabilities worth investigating; current
subscription eligibility/permitted use and a compatible gateway remain unverified.

The owner reports completed Kraken verification and Kraken Pro access. No
credentials or authenticated account observation were supplied or run here.
The [onboarding guide](docs/KRAKEN-ONBOARDING.md) records the PC-first arrangement,
iPad viewing path and first bounded read-only account check. The local dashboard
is verified on loopback; iPad access remains unverified.
The owner-local command and verified historical Mission Control import are now
implemented and tested synthetically, as recorded above. Actual authenticated
account behavior is still unverified. These changes grant no task acceptance,
spending or live authority.

## Mission Control continuation — 2026-10-05

The authenticated dashboard now includes `/progress`: an interactive department
map, the 23-task roadmap, evidence milestones, actual runtime activity and a
persistent Kraken test tracker. Login opens this view. Source checkouts refresh
planning state; installed wheels use a labelled packaged progress snapshot.
Project acceptance remains **17 done / 4 in progress / 2 blocked**.

Owner-started checks are limited to the existing isolated offline loop and four
fixed Kraken public GETs. Results live in a private sidecar database, not the
financial journal. Account reconciliation, order validation and real-order tests
remain unavailable here. Serving or refreshing the view starts no test, trading
service or paid model request. See [the guide](docs/MISSION-CONTROL.md).

Verified: **191 targeted tests passed**, full source/test Ruff, Node syntax,
planning/DAG and ten planning checks. Actual Chromium desktop/mobile checks
passed graph selection, all/blocked roadmap filters, expanded-detail retention,
owner-started rehearsal/status refresh, offline snapshots and recovery, without
horizontal overflow or browser script errors. The actual rehearsal passed all
14 scripted checks without external AI calls or Kraken orders. An actual public
check failed at connection through this cloud environment's proxy; no public,
private-account or real-order success is claimed. An offline wheel includes the
new modules, catalog, template and static assets. This is scoped dashboard
verification; the 2,544-test suite and protected image were not rerun/rebuilt.
See [the continuation review](docs/reviews/2026-10-05-mission-control.md).

## Historical published round 4 checkpoint

Updated: 2026-10-05. All six T17–T22 implementation/review streams are integrated;
local verification is complete. **Actual acceptance gates remain open.** T00–T16
retain their recorded completion scopes: 17 of 23 tasks are complete. No funded
soak, authenticated venue collection, live pilot or production rollout is running.

Verified source: `153c6a5adf9a7bd7b99a1f1c3a21cc2c3c05eb0c`.
Tree: `f00d10dc928a60fb6617279f1707ad1aa5301995`.
Branch: `codex/orchestrator-takeover-2026-10-04`; continuation from `1cb4e5a`.
Previous checkpoints, branches and worktrees remain preserved. Final documentation
follows verified source without package/README/image-input changes. This checkpoint
was verified locally on 2026-10-04. GitHub publication was verified on 2026-10-05
at `48fecba2c62df14fa1b56dbcd653f9ec381c79de` on the integration branch;
this publication record follows that commit. The existing implementation branch
is `cursor/trade-graph-r1-548a`. No merge to `main` or new CI outcome is claimed.
The runtime workflow now allows 45 minutes for the measured 25-minute full suite.
All 26 linked workspaces were audited; no omitted implementation was found.
See [the publication audit](docs/reviews/2026-10-05-publication-audit.md).
Default: USD10,000 virtual capital, EUR reporting; real operating budget separate;
paid calls and live trading disabled.

## Working implementation

The installed paper service, six durable handlers, R1 artifact Engineer,
authenticated dashboard, deterministic accounting, management, backup/offline
restore and reconciliation remain available. See [operations](docs/operations.md).

This round adds exact service binding and actual process restart proof;
preregistered four-arm paper producers and immutable historical verification;
signed native asset fees/rebates, append-only earlier-fill correction and protected
paper auxiliary fee holds; authenticated owner incident review and exact paper/live
scope mapping; complete bounded financial/cost checkpoints with a separate local
rollback witness and provider reservation/settlement publication; strict optional
provider proxy preparation; and commissioned synthetic Secretary application
generation, compatible sidecar activation, fenced health recovery and source rollback.

See [the round 4 review](docs/reviews/2026-10-04-next-contracts.md),
[financial continuity](docs/FINANCIAL-CHECKPOINTS.md),
[provider profile](docs/FUNDED-PAPER-PROFILE.md),
[application commissions](docs/APPLICATION-COMMISSIONS.md) and
[application preparation](docs/APPLICATION-PREPARATION.md).

## Verified results

- **2,544 full tests passed**, zero failures/errors/skips; independent JUnit count.
- **464 mapped tests and all 40 applicable A01–A38/A43–A44 cases passed**, with
  identical clean source before/after. A39–A42 production/intended-host acceptance
  remains pending.
- Ruff for source/tests/four verification scripts, ten planning tests, 23-task
  DAG/reference validation, diff checks and tracked/staged/exact-artifact hygiene passed.
- A fresh exact-commit archive built/installed its wheel offline after frozen
  dependency acquisition. Isolated installed imports, migrations 0001–0016, all
  14 demo checks/four fills, zero restart submissions/provider calls, maintenance,
  actual backup/offline restore, preflight and unfunded-soak refusal passed.
  Installed fee/replay/cold restart and actual two-process lost-ack service proof
  passed. Actual commissioned application generation/activation/health/restart/
  rollback retained all financial, expense and application facts without repeated work.
- A fresh whole-source immutable image passed four actual inspected containers,
  30 child OS attacks, five owner-loader attacks, the memory bound, lost-ack
  reconciliation, management during failure, finance-preserving rollback, zero
  restart dispatch and default service boot/clean stop. Root checked all 150
  protected installed source/artifact hashes and 166 build-context source files.
- No new public GET probe, funded provider, private venue or intended-host success
  is claimed. Historical public probes failed through the configured proxy.

Python 3.12.14; unchanged lock SHA256:
`1ac5a01f8beae2c1156273feb5e664e500d6672349264e2838dd823f1c67ae39`.
Full JUnit `/tmp/trade-graph-r4-final.xml`;
mapped gate `/tmp/trade-graph-r4-final-acceptance.json`;
installed evidence `/tmp/trade-graph-r4-installed-9z4c__6j/installation.json`;
image pin `/tmp/trade-graph-r4-image-153c6a5/image-pin.json`;
image proof `/tmp/trade-graph-r4-image-proof-153c6a5/proof.json`.
Image ID `sha256:8466abac94a81d2c88c13a81afb619d0ab47d0dd29971a4035da15263072b8d5`.
Private machine artifacts remain outside Git and may be transient. Exact hashes,
review findings and limits are recorded in [the review](docs/reviews/2026-10-04-next-contracts.md).
The [previous takeover review](docs/reviews/2026-10-04-orchestrator-takeover.md)
remains historical evidence.

## Remaining acceptance and implementation

| Task | Remaining work |
| --- | --- |
| T17 — in progress | Owner-designated installation, approved public connectivity/current model routes/prices, separate funding/private credentials/paid permissions, actual paper soak/receipts/invoices, intended-host restart, encrypted off-host restore and delivered alerts. Local restart proof is implemented. |
| T18 — in progress | Untouched actual future four-arm blocks, authenticated market/billing provenance, complete failed/shared costs, actual baseline execution and dependence/regime/useful-decision assessment. Historical producers and exact paper/live mapping are implemented; larger economic-source history needs design. |
| T19 — in progress | Eligible least-privilege actual native account/venue conformance; complete funding/transfer/permission/protection proofs; factual future partial-fill bounds and identified fee/rate semantics. Larger replay, finer execution ties and late fee-only amendments remain contract work. |
| T20 — blocked | Genuine T17/T18/T19/T21 evidence, full external account history, explicit owner capital/loss/all-role expense permissions and protected live commissioning; then separately authorized measured pilot/protection/stop evidence. Mapping and incident review never grant/resume live authority. |
| T21 — in progress | Actual intended-host/image/distribution admission and adversarial/recovery evidence there; externally protected rollback anchor; protected key rotation, database migration, interrupted-checkpoint recovery and manifest transition procedures. Provider profile needs separately reviewed public market/FX routing plus actual owner setup before funded soak. |
| T22 — blocked | Genuine T18/T21 evidence, broader owner class grants, production scheduling/routing/admission, funded commissioning and broader compatible migration/deployment proof. The commissioned Secretary route is implemented in explicit synthetic preparation. |

Bounds are distinct: complete economic source 8 MiB/20,000 rows; native initial
audit/new correction 4,096 rows/8 MiB; complete financial checkpoint scans
1,000,000 processed rows/512 MiB/five seconds. Scans and long normal Ledger/FIFO
projections are not incremental. The local witness cannot defeat restoring the
whole host/database/witness. The provider profile refuses public-data routing;
adding credentials alone will not close these technical gaps.

Next, prepare the owner-designated host and reviewed provider/public-data routes,
then perform authorized funded paper observation. Genuine T18 forward collection
and separately authorized T19 conformance can proceed in parallel when their
inputs are available. T20/T22 remain prerequisite and owner-authority gated.

## Working commands

```bash
uv sync --frozen --group dev --python 3.12
uv run ruff check src tests scripts/check_repository_hygiene.py scripts/verify_offline_acceptance.py scripts/build_protected_image.py scripts/verify_protected_image.py
uv run pytest --junitxml=/tmp/trade-graph-verification-new.xml
uv run python scripts/verify_offline_acceptance.py --report /tmp/trade-graph-acceptance-new.json
python3 scripts/check_plan.py
python3 scripts/test_planning.py
uv run trade-graph demo --offline --work /tmp/trade-graph-demo-new
```

Use fresh artifact paths. Protected composition requires explicit owner pins.
Offline plugin/application preparation does not install default production routes
or broader class grants. Live readiness remains closed.
