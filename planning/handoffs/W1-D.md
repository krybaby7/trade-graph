# W1-D T33: Price alarms that fire once

For the owner: The Trader (and the other departments) can now leave saved alarms on a price or on a standard
indicator, and software checks every price update and every finished candle for them without using any AI. Each
alarm goes off once and then waits until its owner switches it on again; alarms that go off while a department is
awake are handed over together at its next wake, and a stopped price feed is reported instead of being mistaken for
a calm market. Nothing wakes anyone yet: the part that wakes departments is a later package (W5-A), and nothing was
installed or changed in the running system, so there is nothing for you to decide or do for this package.

Branch: `claude/determined-dirac-i1eyfa` (this session's own branch, reset to
`origin/claude/day-trader-engine-20261012` as the package prompt allows instead of `claude/t33-price-alarms`),
head: the commit that adds this evidence, on top of code commit `2d7f48e` (the code the full suite ran on).
Pull request: https://github.com/krybaby7/trade-graph/pull/5 (opened from the owner's local session).

## What was built

- `src/trade_graph/contracts/alarms.py`: the alarm contract. Three kinds of condition: `quote_price` (mid, bid, ask
  or last of a symbol's public price updates), `candle_price` (close, high or low of each completed candle of one
  series) and `indicator` (a T27 menu indicator on each completed candle). Relations `above`, `below`,
  `crosses_above`, `crosses_below` (the indicator engine's). Exact Decimal levels. One owning department, optional
  `portfolio_id`, `day_plan_ref`, `task_id`, `request_id` (idempotent retries) and a short note. Queue item and
  batch contracts. No library, adapter or model import, and no field that could cap alarms.
- `src/trade_graph/adapters/persistence/price_alarm_schema.py` and `migrations/versions/0029_price_alarms.py`: state
  migration 0029 (six tables). SQLite triggers enforce one fire per arming, re-arming only after a fire, nothing
  after a cancellation, append-only history, immutable queue items with a fixed status path, one open wake claim
  per department, and alternating lost/restored feed events.
- `src/trade_graph/application/alarms/book.py`: `AlarmBook`: create (idempotent by `request_id`), re-arm
  (owner only, after a fire, idempotent with `after_generation`), cancel (owner only), list; `fire` records the fire,
  marks the alarm spent and queues the delivery in one transaction; watcher cursors and quote baselines; feed
  lost/restored events with notices to the owning departments.
- `src/trade_graph/application/alarms/queue.py`: `AlarmQueue`, the queue contract for W5-A (below).
- `src/trade_graph/application/alarms/watcher.py`: `PriceWatcher`. `observe_quote` for every public price update;
  `tick` for completed candles and feed health. Candles only through `CandleStore.window`, point-in-time as of the
  clock; every completed candle after the cursor evaluated in order (catch-up after downtime); waits for a candle
  not yet received; steps over a missing candle only once a later one exists; indicator values and comparisons
  from the T27 engine; feed loss and return recorded and noticed; one failing alarm does not stop the others.
- `docs/PRICE-ALARMS.md`: the design, rules, defaults, tables and the queue contract.
- Tests: `tests/unit/test_alarm_contract.py`, `tests/integration/test_migration_0029.py`,
  `tests/integration/test_price_alarm_book.py`, `tests/integration/test_alarm_queue.py`,
  `tests/integration/test_price_alarm_watcher.py`, `tests/property/test_price_alarm_properties.py`, helpers in
  `tests/alarm_support.py`.
- `tests/integration/test_migration_0025.py` and `tests/integration/test_owner_checkins.py`: three assertions that
  0025 is the *newest* migration now check that 0025 is registered once, right after 0024, with exactly its schema,
  and that the Alembic chain has one head. They still prove what they proved about 0025.

## The queue contract for W5-A (T35)

Full text with the document fields: `docs/PRICE-ALARMS.md`, "The delivery queue". In short, `AlarmQueue(database,
clock)`:

- Every fire adds exactly one `alarm_fired` item to the owning department's queue, in the transaction that records
  the fire. Every lost or restored feed that an armed alarm depends on adds one `feed_lost` / `feed_restored` item
  per affected department. Items are never dropped, merged or rewritten.
- `waiting()` → `{department: pending count}`: a pending item is a reason to wake a sleeping department.
- `claim(department, wake_id)` → `DeliveryBatch`: every pending item of the department, oldest first, becomes that
  wake's batch. Items arriving during the wake stay pending and come together at the next wake. Same `wake_id` →
  same batch. One open claim per department (`open_claim(department)` finds it after a crash).
- `acknowledge(wake_id)`: delivered. `release(wake_id)`: back to pending in original order (returns the items it
  put back). Both idempotent. A wake id is used once.
- W5-A decides whether a feed notice wakes a department, records the wake itself, and wires the department tools
  (`AlarmBook.create`, `rearm`, `cancel`, `alarms`) and the watcher calls (`observe_quotes(feed.poll())` in the feed
  thread, `tick()` after candle saves and on a short timer).

## Decisions (reserved block D155–D159)

- **D155 (2026-10-16, implementation default for T33):** Alarms live in the state database (migration 0029) as
  non-financial tables. An alarm has one owning department (trader, research, leader, learning or optimisation),
  one condition (`quote_price`, `candle_price` or `indicator`), the indicator engine's four relations and an exact
  Decimal level, plus optional portfolio, day-plan and task references and a note of at most 280 characters. It
  fires at most once per arming, enforced by a unique constraint, so restarts and repeated ticks cannot fire twice.
  Only the owning department re-arms (after a fire) or cancels; a re-arm keeps the condition, and a new level means
  a new alarm. No field or setting limits how many alarms exist or how often they fire (D116). Reason: the task's
  one-shot rule as a store guarantee, with full history kept.
- **D156 (2026-10-16, implementation default for T33):** Candle and indicator alarms read candles only through
  `CandleStore.window`, point-in-time as of the clock, and evaluate every completed candle that closed strictly
  after arming (or after the cursor) in order, so candles completed during downtime are not missed. The watcher
  waits at a candle that has not been received and steps over a missing one, saying so, only once a later candle
  is present; a candle that arrives later than its successor is not evaluated (alarms watch the live edge). A
  crossing needs two consecutive candles. Candle alarms use the collected intervals 1, 5, 15 and 60 minutes. Level
  alarms on On-Balance Volume are refused (its level depends on where history starts, D136), and so are levels
  outside an output's declared range. One tick evaluates at most 5,000 candles per alarm and continues next tick;
  a whole 5,000-slot step without any candle is stepped over (reported) once newer candles exist, so an outage that
  can never be backfilled does not freeze the alarm. Stored alarms are read back without re-checking today's menu,
  so a later menu change makes one alarm report an error instead of stopping the watcher.
- **D157 (2026-10-16, implementation default for T33):** Quote alarms use quote and trade observations received
  after arming, on `mid` (default, (bid + ask) / 2), `bid`, `ask` or `last`; a missing price is not guessed. A
  crossing alarm saves the side of its level whenever it changes, so a crossing across a restart or a feed gap
  still fires, and the fire reports the previous reading on the other side and the seconds since the watcher's
  previous quote of that symbol. A quote stamped in the future is refused; one older than the alarm's held reading
  is ignored.
- **D158 (2026-10-16, implementation default for T33):** A feed that an armed alarm depends on is lost after 120
  seconds without a quote (the paper feed polls every 10 seconds) or when no candle has arrived for the slot due
  10 minutes ago. Loss and return are each recorded once and sent as a notice to every owning department (a return
  also to every department told about the loss); a department that arms on a feed while it is lost gets the same
  loss notice; after a restart a lost quote feed stays lost until a fresh update arrives (a backlog of old quotes
  is no recovery); a candle series the store refuses to read counts as lost. An alarm whose evaluation fails keeps its
  cursor, carries the failure as its reason, is reported in the tick, and does not stop the other alarms. These are
  health thresholds, not caps.
- **D159 (2026-10-16, implementation default for T33, contract for T35):** Fired alarms and feed notices go to a
  persisted per-department queue that the wake dispatcher claims one batch per wake (`claim`, `acknowledge`,
  `release`, one open claim per department, single-use wake ids). Items that arrive while a department is awake
  stay pending and are delivered together at its next wake. Nothing is dropped or merged, and nothing limits items
  or wakes. Whether a feed notice wakes a department is W5-A's decision.

## Evidence

All runs on Python 3.12.13 (`uv sync --frozen --group dev --python 3.12`), in this cloud container, at code commit
`2d7f48e`. No network call, key, paid call or exchange call in any test.

- Tests written first, and what they showed:
  - `tests/unit/test_alarm_contract.py`, `tests/integration/test_price_alarm_book.py`,
    `tests/integration/test_alarm_queue.py`: failed at collection (`ModuleNotFoundError` / `ImportError`, the modules
    did not exist) and pass now.
  - `tests/integration/test_price_alarm_watcher.py`: written before the watcher; with the watcher module removed it
    fails at collection (`ImportError: cannot import name 'watcher'`). On the first run of the watcher two of its
    tests failed because they sent only `last` prices to `mid` alarms (a test bug, fixed).
  - `tests/integration/test_migration_0029.py`: written with the schema module before either was run; its first run
    found one trigger bug (a content change on a claimed delivery was reported as a status error), fixed.
  - Error isolation (`test_one_alarm_that_cannot_be_evaluated_does_not_blind_the_others`) failed with
    `CandleSelectionError` before the fix. After an independent read-only review, six more tests were added first and
    failed (long candle hole, late arming on a lost feed, backlog quote, stale stored condition, `release` return,
    watch trigger), then the fixes made them pass; the stale-quote test was strengthened to reach its check.
  - `tests/property/test_price_alarm_properties.py` (Hypothesis, derandomized, 80 examples each plus explicit edge
    examples) was written after the code and checked by mutation: a strict-crossing mutation, a replay-mode
    (not point-in-time) candle read and a forgotten quote baseline after restart each make it fail.
- `uv run ruff check src tests`: all checks passed.
- New tests: `uv run pytest -p no:cacheprovider tests/unit/test_alarm_contract.py tests/integration/test_migration_0029.py
  tests/integration/test_price_alarm_book.py tests/integration/test_alarm_queue.py
  tests/integration/test_price_alarm_watcher.py tests/property/test_price_alarm_properties.py`: **62 passed**.
- Related existing tests (0025 migration and owner check-ins/expenses whose assertions changed, the indicator engine
  and alarm comparison the watcher uses, the import boundary): `tests/integration/test_migration_0025.py
  test_owner_checkins.py test_owner_expenses.py test_owner_expense_upgrade.py test_indicator_engine.py
  tests/unit/test_indicator_alarm_compare.py test_import_boundary.py`: **244 passed**.
- `python3 scripts/check_plan.py`: plan valid (46 tasks, acyclic). `python3 scripts/test_planning.py`: 11 tests OK.
  `python3 scripts/check_repository_hygiene.py`: passed, no private runtime files or credentials.
- Full suite, `uv run pytest -p no:cacheprovider -rf` at `2d7f48e`: **6,375 tests: 6,338 passed, 23 skipped,
  14 failed**, about 85 minutes of test time. One single run was stopped at this container's 2-hour background limit
  at about 60%, so the suite was run once more as seven contiguous slices of the collected file list (every file
  exactly once, JUnit report per slice); a container restart interrupted one slice, which was re-run in full.
  Every failure was re-run on the untouched base `435db71` in a separate worktree and fails there identically:
  - known list: `tests/e2e/test_dashboard_loop.py::test_dashboard_reconciles_full_loop_and_navigates_retained_evidence`;
    4 `tests/integration/test_paper_soak.py` (`test_cli_observer_failure_after_real_worker_effects_retains_interrupted_evidence`,
    `test_cli_existing_report_refusal_is_sanitized_and_precedes_model_work`,
    `test_cli_actual_feed_failure_degrades_completed_soak_even_without_role_calls[True]` and `[False]`);
    3 `tests/integration/test_progress_dashboard.py`; 4 `tests/integration/test_progress_projection.py`;
    `tests/integration/test_subscription_smoke.py::test_cycle_runs_only_fresh_research_and_trader_through_protected_handlers`;
  - container only: `tests/security/test_subscription_egress_boundary.py::test_fixed_provider_proxy_is_the_only_child_egress_setting`
    (`ValueError: Linux bubblewrap is unavailable; isolation fails closed`; also fails on the base here).
  - **No new failure.** The load-sensitive timing cases on the known list (`test_plugin_staging`,
    `test_financial_recovery`, `test_financial_transition`, `test_engineer_isolation`) passed in this run.
- Real or synthetic: everything is synthetic. Candles are built in the tests (`tests/alarm_support.py`) or come from
  the existing integer-arithmetic indicator series (`tests/indicator_series.py`); quotes are scripted
  `Observation`s; clocks are frozen. No recorded public data was needed, and no paid, model, network or exchange
  call was made. A mock is not a real feed test: the watcher has not yet watched a live Kraken feed (that needs
  W5-A/W6-D wiring).

## Schema and dependencies

- State migration: **0029** (W1-D's reservation), registered in `migrate.py` after 0025 with a comment that
  0026–0028 belong to the other wave-1 packages. The Alembic revision `0029` has `down_revision = "0025"`;
  **the integrator re-links it to the newest earlier revision (0028) after merging W1-A, W1-B and W1-C**.
  `tests/integration/test_migration_0029.py` checks `down_revision` against the version registered just before
  0029 in `migrate.STATEMENTS`, so it passes once re-linked. Candle database: no change.
- Dependencies added: none.

## Not done, open or for someone else

- Waking departments from the queue, recording wakes, department tools for alarms, and calling the watcher from
  the service: W5-A (T35), using the contract above.
- Keeping the candle store current inside the service: W6-D. Until then, with the 6-hourly Windows collector, a
  candle or indicator alarm would correctly report its candle feed as lost.
- Alarm lines in HQ charts: W5-B (reads `price_alarms` and `price_alarm_watch`). Active alarms and feed notices in
  HQ's wake log: W6-C.
- Day plans that create alarms with `day_plan_ref`: W4-A (T34).
- Integrator: re-link Alembic 0029 (above); the edits to the "0025 is newest" assertions may meet similar edits
  from W1-A, W1-B or W1-C in the same lines; keep the version-order checks.
- Observed outside scope: the cloud container's default Python is 3.13; this session installed Python 3.12.13 with
  `uv python install 3.12` and ran everything on it, matching the project target.
