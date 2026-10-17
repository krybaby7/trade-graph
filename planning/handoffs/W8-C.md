# W8-C C5: HQ shows today's loss, the day plans and the scorecard

For the owner: HQ's Money tab now shows today's loss against your 3% daily loss limit, the Trader's day plans coin by
coin (waiting buys, their safety exits, what filled and which plans software refused), a scorecard comparing the
paper result after every fee with keeping the cash and with holding Bitcoin, and whether the price collector is
working; a failing money-limit step is now named in plain words instead of "a routine step is failing". The older
"Pays for itself?" box now reads "Paper result against real costs" and says plainly that paper money never pays a real
bill, and phones no longer download the chart library again on every page load. Nothing needs a decision from you;
after the install (step L3) please open the Money tab once on your phone and iPad, because it was checked only in a
test browser with made-up numbers.

Branch: `claude/c5-hq-loss-plans-scorecard`, head: the commit that adds this line; code at `c8df666`. Pull request:
to be added once it is opened.

## What was built

- `src/trade_graph/api/hq_trading.py`: the snapshot's new `trading` block. Today's loss is W3-A's own reading
  (`MoneyLimitGuard.status(portfolio, policy)` with `record=False`, shown from `LossStatus.document()`); the
  scorecard is W5-C's `compute_scorecard` from the opening deposit to now, shown from its `document()`; the price
  collector is read from its `price_collection` activity records of the last 24 hours. Cached for 60 seconds behind a
  fingerprint of fills, ledger events, day-start values, loss stops, owner policies, AI receipts, budget reservations
  and reference rates (like `hq_money`). `notices()` adds `loss_limit_reached` while the limit is reached or the
  day's stop is recorded.
- `src/trade_graph/api/hq_contract.py` (additive): `HQSnapshot.trading` (omittable, never null) with `TradingBlock`,
  `LossLimit`, `Scorecard`, `PriceCollector`; problem prefixes `money_limits:`, `day_plans:`, `collector:`; notice
  code `loss_limit_reached`; glossary keys `loss_limit`, `day_plan`, `safety_exit`, `scorecard`, `price_collector`.
  Validators: the loss is exactly the day-start value less the current value, in the account's currency; a state
  never claims more than its figures show; an unavailable scorecard shows no figures; beating cash means a result
  above zero (D127); "provisional" always says why.
- `src/trade_graph/api/hq_plans_contract.py` and `src/trade_graph/api/hq_plans.py`: `GET /api/v1/hq/plans`
  (`trade-graph.hq.plans.v1`, any signed-in role, reads only): each coin's newest day plan of today and any older plan
  whose order is still open (a safety exit still protecting coins), with every waiting buy, its fills, the safety exits
  software attached and what they sold, any filled part without a safety exit right now, the plan's events and the
  Trader's rationale and invalidation as labelled quotes; and today's refused plans in plain words. Plain SQL in one
  read snapshot; no execution, broker, engine or provider is imported.
- `src/trade_graph/api/static_cache.py` and one middleware line in `api/app.py`: `Cache-Control: public,
  max-age=31536000, immutable` only for a vendored script asked for by its pinned version
  (`/static/vendor/lightweight-charts/lightweight-charts.standalone.production.js?v=5.2.1`); everything else stays
  `no-store`. `web/static/hq-charts.js` loads that address; `scripts/vendor_lightweight_charts.py --check` keeps it
  equal to `VENDOR.json`'s version.
- `src/trade_graph/api/hq_copy.py`: `TRADING` and `PLANS` words, plain words for the three new problem prefixes,
  section help for the four panels, and the reworded comparison words (`MONEY`, `PROMPT`).
  `src/trade_graph/api/hq_state.py`: the prefix tuples (money-limit and day-plan failures also light the Accounts
  room) and `pays_plain(..., live=)`. `src/trade_graph/api/hq.py`: the `trading` key and its notice.
- Page: `web/templates/hq.html` (four panels in Money: "Today's loss limit", "Today's day plans" and "Scorecard"
  after the scoreboard, "Price collector" after Market weather, each with a first paint of its headline and a help
  button), `web/static/hq-render.js` (views `loss`, `scorecard`, `collector`; the reworded comparison box),
  `web/static/hq-plans.js` (new: the day plans panel, read only while it is on screen, at most every 25 s),
  `hq-core.js` (the `plans` read), `hq.js` (one `init`), `hq-glossary.js` (five terms, four section helps, the
  neutral "Result against real costs" entry), `hq-prompts.js` (the review prompt's label), `hq.css` (theme tokens
  only).
- Docs: `docs/HQ-DASHBOARD.md` ("Today's trading" section, data rules, the route), one line each in
  `docs/SCORECARD.md`, `docs/DAY-PLANS.md` and `docs/MONEY-LIMITS.md`.
- Tests (new): `tests/integration/test_hq_trading.py`, `tests/integration/test_hq_plans.py`,
  `tests/integration/test_hq_vendor_cache.py`, `tests/unit/test_hq_trading_contract.py`,
  `tests/unit/test_hq_plans_contract.py`, `tests/test_hq_trading_static.py`, `tests/js/hq_trading.test.mjs`,
  `tests/js/hq_plans.test.mjs`, helper `tests/hq_trading_support.py`. Changed: the nine
  `tests/fixtures/hq/snapshot_*.json` carry a `trading` block made by the real builder from synthetic facts (every
  other key unchanged), `tests/js/hq_harness.mjs` (three views in Money), `tests/test_hq_static.py` (`SCRIPTS`,
  `FETCH_ALLOWLIST`, jargon check), `tests/test_hq_charts_static.py` and `tests/js/hq_charts.test.mjs` (the versioned
  address), `tests/integration/test_hq_projection.py` (`snap()` resets the trading cache like the money cache).

## Decisions (reserved block D250–D254)

- **D250 (2026-10-17, implementation default for C5): today's loss in HQ.** The snapshot gains the additive,
  omittable `trading` key (`loss`, `scorecard`, `collector`). Today's loss is the money-limit guard's own reading,
  `MoneyLimitGuard.status(portfolio, policy)` with `record=False`, so reading HQ never records a day-start value or a
  stop (only the paper service's management step does) and never decides anything. It is shown in the account's
  currency (USD), exactly, never converted at a guessed rate; HQ works the loss out from the two values the guard
  read, so it always adds up. States: `not_recorded` (no money limits yet: day plans stay off, the command is named),
  `within`, `reached` (new entries are refused already; the next step records the stop), `stopped`, `unknown` (the
  guard's reason in plain words: new buying waits), and `not_applied` for a live account (the paper service's limit
  doesn't run there). A meter shows the share of the limit used (0 for a gain) with its percentage in words. While the
  limit is reached or stopped, the HQ tab shows a `loss_limit_reached` notice (attention tone, linked to Trading
  records); nothing needs the owner, so it is not a Needs-you card. The service's `money_limits:`, `day_plans:` and
  `collector:` failures get problem prefixes with plain words of their own (none says "practice", so a live account
  reads them too); money-limit and day-plan failures also turn the Accounts room's light to "needs a look". Reason:
  the W8-C package ("today's loss against the 3% limit, and a money-limit failure in plain words"), W3-A's contract
  for HQ and "unknown is not a yes" (D182).
- **D251 (2026-10-17, implementation default for C5): the day plans are a separate read.** `GET /api/v1/hq/plans`
  (`trade-graph.hq.plans.v1`) is read only while its panel is on screen, never inside the snapshot, as the charts and
  the wake log are (D197, D215): a busy synthetic day of plans is about 70 KB, more than the snapshot has left of its
  80 KB. Listed: each coin's newest plan whose trading day has not ended, then older plans that still have an order
  open (a safety exit protecting coins bought on an earlier day, or a buy being checked), newest first. What is not
  protected uses `DayPlanProtection.coverage`'s arithmetic (an exit open or being checked covers its whole quantity, a
  sold exit what it sold, an exit that ended unfilled only what it sold); a filled part without a safety exit is marked
  for attention in the panel. Order states are words: sending, waiting, partly filled, filled, cancelled (with the
  recorded reason: a pause, the end of its time, a newer plan), refused, being checked (an unknown status is never
  read as refused). Today's refusals come from `day_plan_refused` activity records with the day-plan checks in plain
  words and HQ's usual order-refusal words for the money rules; the recorded reason shows only with "Show technical
  names". The rationale and invalidation are labelled quotes (280 characters). Bounds are page bounds, never limits on
  plans (D116): 8 plans, 16 buys (a plan's own bound), 4 safety exits and 4 fills per order, 8 events per plan, 10
  refusals, each list saying how many more there are. Reason: the package ("today's day plans per coin: entries,
  safety exits, fills, refusals and plan events") within HQ's payload budget, and D219 (reading starts nothing).
- **D252 (2026-10-17, implementation default for C5): the scorecard in HQ.** HQ shows W5-C's scorecard for the paper
  portfolio from its opening deposit to now (D200's default period; T43's report passes the scored test's own start
  and end), cached for 60 seconds behind the same fingerprint as today's loss: fills, ledger events, day-start values,
  loss stops, owner policies, AI receipts, budget reservations and reference rates. Prices are not in the
  fingerprint, so the value of coins still held refreshes at most once a minute; at four weeks of 10-second prices one
  refresh takes about 3.5 s (W5-C's measure). Shown: the result after every fee with its percentage, beats cash
  (result above zero, D127) and beats holding Bitcoin as a word and a ✓ or ✗ shape, up to 10 figures (trades, won,
  average win and loss, fees, worst fall, longest losing run, positions still held), up to 6 lines (cash, Bitcoin,
  euros with the dollar's own move, real AI spending of the same days with "up to" for unknown costs and "under
  $0.01" for tiny ones, never paid by the paper result, and the owner's test rule D138 item 7 as information, not a
  pass mark), up to 5 provisional reasons, and the scorecard's own gates statement (D204). A period that can't be
  scored says why in the scorecard's plain words; a live account has none. Reason: W5-C's handoff ("a block would
  need caching like HQ's money facts ... and must stay within the 80 KB payload budget"): the fullest block is about
  6 KB and the busy week with it is about 72 KB.
- **D253 (2026-10-17, implementation default for C5): the price collector panel and caching the chart library.**
  The panel reads the collector's `price_collection` activity records of the last 24 hours (newest first, at most
  2,000; more reads "at least"): the newest hourly coin scan, the newest candle status change, Kraken's pair list,
  rate-limit pauses, gaps and the number of problems. It needs a look when the newest scan failed, there was no scan
  in the last 2 hours (new buys under the coin rule wait for a fresh one, D183), the newest candle step failed or was
  partial, the clock is behind, or the newest pair-list update failed; an installation whose settings switch the
  collector off says so. Ordinary successful steps are not recorded (W6-D), so "working" is read from the hourly scan.
  Separately, every HQ response stays `no-store` except the vendored chart library asked for by its pinned version
  (`?v=<VENDOR.json version>`): its bytes never change under that address and the page pins its integrity, so a
  browser may keep it for a year (`public, max-age=31536000, immutable`); the same file without the version, any other
  version, every other static file, page and API stay `no-store`. Reason: the package and W5-B's follow-up ("today
  every page load that opens a chart fetches the 198 KB script again").
- **D254 (2026-10-17, recommended default for W2-C's question): the comparison box never suggests paper gains pay
  bills.** On the paper account the older "Pays for itself?" box is titled "Paper result against real costs", says
  "On paper, the result is X above (or short of) the real costs you entered" and always adds "A comparison only: paper
  money is simulated, never pays a real bill and never refills your budget"; the glossary entry is the neutral "Result
  against real costs", and the review prompt's label follows. A live account keeps the old words, because its
  trading results are real money. The contract keys and states (`pays_for_itself`, `covered`, `not_covered`) are
  unchanged. Reason: AGENTS.md ("USD10,000 paper equity and resets cannot replenish the real operating budget") and
  W2-C's open question.

## Evidence

All runs on CPython 3.12.13 (`uv sync --frozen --group dev --python 3.12`) in this cloud container, in
`/home/user/c5-hq-loss-plans-scorecard`. No network, key, paid, model or exchange call in any test.

- Tests written first: `tests/integration/test_hq_trading.py` and `tests/integration/test_hq_plans.py` failed at
  collection before their modules existed (`ImportError: cannot import name 'hq_trading'` / `'hq_plans'`), and
  `tests/integration/test_hq_vendor_cache.py` before `api/static_cache.py`; the nine
  `test_snapshot_validates_against_contract` cases failed (`Extra items in the left set: 'trading'`) until the fixtures
  carried the block. Rechecked against the untouched base `e7ffca4` (its source on `PYTHONPATH`, the new test files
  copied in): every new Python test module fails there (collection errors, and `test_hq_trading_contract.py` fails
  its cases); with `HQ_STATIC_DIR` pointing at the base scripts, `tests/js/hq_trading.test.mjs` and
  `tests/js/hq_plans.test.mjs` fail 15 of 15 and `tests/js/hq_charts.test.mjs` fails its versioned-address case. The
  first busy-day measure of the plans document (12 plans, 104 KB) led to the 8-plan bound (70 KB); the first fill line
  said "fee an unknown amount" for a Decimal fee (fixed, covered by the tests).
- New tests: `uv run pytest -p no:cacheprovider -q tests/integration/test_hq_trading.py
  tests/integration/test_hq_plans.py tests/integration/test_hq_vendor_cache.py tests/unit/test_hq_trading_contract.py
  tests/unit/test_hq_plans_contract.py tests/test_hq_trading_static.py`: **92 passed, 0 failed, 0 skipped** (9 s).
  `node --test tests/js/hq_trading.test.mjs tests/js/hq_plans.test.mjs`: 15 passed, 0 failed.
- JS: `node --test tests/js/hq_*.test.mjs`: **92 passed, 0 failed, 0 skipped** (all eight files, including the two new ones and the changed `hq_charts.test.mjs`).
- Related tests: every module that imports or exercises HQ, the dashboard app or its routes (`create_app`, `hq_copy`, `hq_contract`, `hq_state`, `hq_money`, `hq_facts`, `dashboard_runtime`), every HQ, dashboard, money-limit, day-plan, scorecard, AI-spending, price-collector and migration 0032–0042 module, the paper service, packaging and install wiring, and `tests/test_hq_js_behaviour.py` (which runs every `tests/js/hq_*.test.mjs`): 80 modules, one run of `uv run pytest -p no:cacheprovider -q -rfs <modules>` at `c8df666` (while two other builders ran their suites on the same 4 CPUs): **1,117 passed, 0 failed, 0 skipped**. The modules: `tests/e2e/test_dashboard_loop.py` `tests/integration/test_account_checks.py` `tests/integration/test_ai_spending.py` `tests/integration/test_ai_spending_cli.py` `tests/integration/test_ai_spending_period.py` `tests/integration/test_api_security_live.py` `tests/integration/test_authorization_review.py` `tests/integration/test_cookie_writes.py` `tests/integration/test_dashboard.py` `tests/integration/test_dashboard_activity.py` `tests/integration/test_dashboard_boundary.py` `tests/integration/test_dashboard_controls.py` `tests/integration/test_dashboard_evidence.py` `tests/integration/test_dashboard_financial.py` `tests/integration/test_dashboard_recovery_history.py` `tests/integration/test_dashboard_session.py` `tests/integration/test_dashboard_subscription.py` `tests/integration/test_day_plan_recovery.py` `tests/integration/test_day_plan_service.py` `tests/integration/test_day_plans.py` `tests/integration/test_evidence_acceptance.py` `tests/integration/test_first_run_replay.py` `tests/integration/test_hourly_features_candle_store.py` `tests/integration/test_hq_chart.py` `tests/integration/test_hq_chart_routes.py` `tests/integration/test_hq_cost.py` `tests/integration/test_hq_engine_turns.py` `tests/integration/test_hq_news_wakes.py` `tests/integration/test_hq_order_refusal.py` `tests/integration/test_hq_owner_writes.py` `tests/integration/test_hq_payload.py` `tests/integration/test_hq_plans.py` `tests/integration/test_hq_projection.py` `tests/integration/test_hq_routes.py` `tests/integration/test_hq_spending.py` `tests/integration/test_hq_trading.py` `tests/integration/test_hq_vendor_cache.py` `tests/integration/test_hq_wakes.py` `tests/integration/test_hq_wakes_routes.py` `tests/integration/test_install_wiring.py` `tests/integration/test_kraken_onboarding_flow.py` `tests/integration/test_live_owner_resume.py` `tests/integration/test_live_startup_service.py` `tests/integration/test_migration_0032.py` `tests/integration/test_migration_0033.py` `tests/integration/test_migration_0034.py` `tests/integration/test_migration_0035.py` `tests/integration/test_migration_0038.py` `tests/integration/test_migration_0039.py` `tests/integration/test_migration_0042.py` `tests/integration/test_money_limits.py` `tests/integration/test_money_limits_cli.py` `tests/integration/test_native_incident_resolution.py` `tests/integration/test_news_wake_day.py` `tests/integration/test_optimisation_readiness.py` `tests/integration/test_owner_checkins.py` `tests/integration/test_owner_command_recovery.py` `tests/integration/test_paper_packaging.py` `tests/integration/test_paper_runtime_funded.py` `tests/integration/test_paper_service.py` `tests/integration/test_price_collector.py` `tests/integration/test_price_collector_service.py` `tests/integration/test_progress_dashboard.py` `tests/integration/test_scorecard.py` `tests/integration/test_scorecard_cli.py` `tests/integration/test_service_controller.py` `tests/integration/test_shared_rate_limit.py` `tests/integration/test_subscription_financial_projection.py` `tests/property/test_ai_spending_properties.py` `tests/property/test_day_plan_protection_properties.py` `tests/property/test_money_limit_execution_properties.py` `tests/property/test_money_limit_properties.py` `tests/property/test_scorecard_properties.py` `tests/test_dashboard_pages.py` `tests/test_hq_charts_static.py` `tests/test_hq_js_behaviour.py` `tests/test_hq_static.py` `tests/test_hq_template.py` `tests/test_hq_trading_static.py` `tests/test_hq_wakes_static.py`.
- `uv run pytest -p no:cacheprovider -q -rfs tests/unit`: **2,313 passed, 0 failed, 7 skipped** (the existing skips: 4 indicator cross-checks the second library cannot make, 3 archive-permission cases because the container runs as root)
- `uv run ruff check src tests`: all checks passed. `python3 scripts/check_plan.py`: plan valid, 46 tasks.
  `python3 scripts/test_planning.py`: 11 tests OK. `python3 scripts/check_repository_hygiene.py` (and `--staged` before
  every commit): passed. `python3 scripts/vendor_lightweight_charts.py --check`: the vendored copy matches its pins.
- Full suite: not run (night shift: the wave integration runs it).
- Browser check (Chromium from Playwright 1.56.1, `/opt/pw-browsers`; never installed here): the real app on
  127.0.0.1 over a synthetic account (synthetic 4% limit, one SOL day plan with a partly filled buy and its safety
  exit, a waiting buy and a refused plan, synthetic collector records), signed in as the owner, Money tab at phone
  390×844, iPad portrait 820×1180, iPad landscape 1180×820 and desktop 1280×900, each in light and dark (system
  setting): the four panels drew (the day plans after scrolling them into view, as designed), the loss meter was
  present, no horizontal page overflow, no console error or warning, no request outside HQ's origin, and the light
  and dark backgrounds followed the setting. Screenshots stayed in the session's scratch folder. Not checked:
  Safari/WebKit (only Chromium is installed), a real phone or iPad, real records.
- Size: the fullest trading block is about 6 KB (`test_the_fullest_block_stays_small_and_valid`); the busy week with
  it is about 72 KB compact, under 80 KB readable too; a busy synthetic day of day plans (8 coins, 4 partly filled buys
  each with 2 sold safety exits, 10 events, 40 refusals) is about 70 KB of the endpoint's own 80 KB.
- Real or synthetic: everything is synthetic and labelled (W3-A's and W4-A's synthetic accounts, W5-C's synthetic
  golden ledger, synthetic collector records, fixtures made by the real builder from synthetic facts). No real
  order, key, paid call or exchange call; no network was used.

## Schema and dependencies

- State migration: none (the reserved 0045 is unused): HQ reads existing records only. Candle database: none.
- Dependencies added: none (`pyproject.toml` and `uv.lock` unchanged).

## Not done, open or for someone else

- **Owner, step L2–L3:** open HQ's Money tab on your phone and iPad, in light and dark, once the new engine runs.
  Until you run `trade-graph money-limits --confirm` (W3-A) the loss panel says your limits aren't recorded and the
  day plans panel says plans are off.
- **Integrator:** copy D250–D254 into `docs/DECISIONS.md`; no migration to re-link. Shared files touched (keep both
  sides): `api/hq_contract.py` (vocabularies, new models, `HQSnapshot.trading`), `api/hq_copy.py` (new blocks,
  `PROBLEM_PREFIX`, `SECTION_HELP`, `MONEY`, `PROMPT`), `api/hq_state.py` (prefix tuples, `pays_plain`), `api/hq.py`,
  `api/app.py` (one import, one `register`, the middleware's cache line), `web/templates/hq.html`, `web/static/hq-core.js`,
  `hq.js`, `hq-render.js`, `hq-glossary.js` (GLOSSARY keys appended in the contract's order), `hq-prompts.js`,
  `hq-charts.js`, `hq.css`, `scripts/vendor_lightweight_charts.py`, the nine `tests/fixtures/hq/snapshot_*.json`
  (they carry `ai_spending` and now `trading`; a fixture regenerated elsewhere must keep both), `tests/js/hq_harness.mjs`,
  `tests/test_hq_static.py`, `tests/test_hq_charts_static.py`, `tests/js/hq_charts.test.mjs`,
  `tests/integration/test_hq_projection.py` (one line), four docs. Not touched: `kernel/authority.py`,
  `adapters/persistence/migrate.py`, `contracts/`, `planning/progress.json`, `IMPLEMENTATION-STATUS.md`,
  `docs/DECISIONS.md`. W8-B's trader exit tools add new day-plan events or reasons: an unknown event kind reads
  "Recorded at …" and an unknown cancel reason reads without a reason until `hq_copy.PLANS` names it.
- **Found outside the package (not changed):** `Ledger._marks` reads every valuation mark of the portfolio up to the
  moment asked (no limit), so each `native_equity` call (the paper service's money-limit step on every tick, HQ's
  loss figure once a minute, the money facts) grows with weeks of 10-second prices; W8-B's index on
  `valuation_marks (portfolio_id, asset, observed_at)` speeds up "newest mark" queries but not this one. A later
  package could read only the newest mark per coin. Who: integrator or a later package.
- **Later (not in C5):** a "Needs you" card when a filled part has had no safety exit for more than a few minutes
  (today it is marked in the day plans panel only); the scorecard of the scored test period (T43) in HQ next to the
  since-opening one; gzip for static files.
