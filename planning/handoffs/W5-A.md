# W5-A T35: Event engine: wake on alarms, fills, exits and check-ins

For the owner: The departments no longer need a fixed four-hour timer: software now wakes the Trader when a price
alarm goes off, a waiting buy fills or a safety exit closes a position, wakes any department the Leader asks for or at
the time it chose for itself (or at the next day start if it chose none), and every day at 00:00 UTC it runs the coin
scanner, asks Research for the day's focus coins, asks the Trader for its day plans and, at the day end, asks Learning
to review the day. Nothing limits how often anyone is woken, nothing that arrives while a department is busy is lost,
every wake is saved with its reasons and the AI allowance reading, and a wake left over from a day that already ended
never places an order. It is built and tested with made-up prices and scripted departments only, and it is not
switched on in your running system yet: the real AI Trader first needs to learn to answer a wake with day plans
(listed below for the next package), so there is nothing for you to decide or do now.

Branch: `claude/t35-event-engine`, head: the commit that adds this line; code at `2711ee9` (docs to `07b89da`; the evidence runs are at `07b89da`). Pull request:
https://github.com/krybaby7/trade-graph/pull/14.

## What was built

- `src/trade_graph/contracts/wakes.py`: the typed contracts other packages build on: `WakeTrigger` (16 kinds:
  `day_start`, `focus_step`, `day_plan_step`, `day_end_review`, `check_in`, `alarm_fired`, `feed_lost`,
  `feed_restored`, `entry_filled`, `exit_filled`, `order_filled`, `loss_limit_reached`, `focus_updated`,
  `wake_request`, `news_alarm`, `task_created`), `WakeContext` (what a woken department is handed in
  `task["input"]["wake"]`), `WakeOutcome` (what it answers under `"wake_outcome"`: check-in, focus, day plans, wake
  requests), `CheckInRequest`/`CheckIn`, the focus step (`ScanReference`, `ShortlistCoin`, `FocusCoin`,
  `FocusSelection`, `FocusRecord`), `AllowanceReading`, `WakeRecord`/`WakeOutcomeRecord`/`AppliedPlan`, and the
  strict provider replies `TraderWakeReply` and `ResearchFocusReply` (both pass the OpenAI and Anthropic wire
  schemas). No adapter, SDK or model import; exact decimals.
- `src/trade_graph/application/events/`: the engine. `engine.py` (`EventEngine.step`, dispatch, closing wakes,
  applying answers, `before_run`, `finish_task`, `request_wake`, `notify`, and the reads `wakes`, `wake`,
  `next_check_ins`, `focus`, `cycle`; `EngineOptions`, `TaskSettings`), `sources.py` (triggers from T33's alarm
  queue, fills of day-plan entries and protective exits and other orders, the daily loss stop, due check-ins and
  department tasks made outside the engine), `cycle.py` (the day start and day end), `scan.py` (`CoinScanStep`, the
  day start's scan, and the newest recorded scan as fallback), `allowance.py` (the allowance reading), `store.py`
  (the records).
- `src/trade_graph/adapters/persistence/wake_schema.py`, `migrations/versions/0035_event_engine.py`, one import and
  one line in `adapters/persistence/migrate.py`: state migration 0035 (six tables: `wake_triggers`, `wakes`,
  `department_check_ins`, `focus_selections`, `day_cycle_steps`, `wake_cursors`; SQL triggers keep every record,
  freeze what a wake was handed, move triggers and wakes only along their paths, keep one open wake per department,
  one check-in and focus per wake, and cursors moving forward).
- `src/trade_graph/application/paper_service.py`: `PaperService(..., event_engine=EngineOptions(scan=, watcher=,
  daily_cycle=))`. With it, `_schedules` runs the engine instead of the fixed role timers (also when AI is paused:
  wakes are recorded and wait); `_run_role` skips an obsolete wake task before any snapshot or attempt and closes
  each wake as soon as its turn ends; `_ingest` hands every quote to T33's `PriceWatcher`; engine failures appear in
  the tick as `wakes:<part>:<error>` and never hold back management. Without `event_engine` nothing changes.
- `src/trade_graph/kernel/authority.py`: the contract and `application/events/` are protected paths.
- `docs/EVENT-ENGINE.md` (new): the rules, every contract field, the daily cycle, restart and obsolete handling, the
  service wiring and what W6-A, W6-B, W6-C, W6-D and W7-A use.
- Tests: `tests/unit/test_wake_contracts.py`, `tests/integration/test_migration_0035.py`, `test_wake_engine.py`,
  `test_wake_cycle.py`, `test_wake_service.py`, `test_scripted_day.py`, `tests/property/test_wake_properties.py`;
  helpers in `tests/wake_support.py`.

## The contracts for the next packages (full text in `docs/EVENT-ENGINE.md`)

- **A wake.** `EventEngine.step(portfolio_ids)` gives each department with a fresh pending trigger and no open wake
  one wake with all its pending triggers, written with its scheduler task (`role` = department, `objective`
  `wake:<purpose>`, `dedup_key` `wake:<wake_id>`, input `{"wake": WakeContext}`) and its T33 alarm-batch claim in one
  transaction. Purposes: `day_plan`, `focus`, `day_end_review`, `event`, `check_in` (and `task` for an outside task).
- **An answer.** The handler output's `"wake_outcome"` is a `WakeOutcome`: `check_in` (`at_utc` or none = next day
  start), `focus` (Research only; coins on the day's shortlist), `day_plans` (the Trader only; one per coin, installed
  by `DayPlans.install(..., request_id="wake:<wake_id>:<symbol>", task_id=...)` under the execution lock),
  `wake_requests` (`department`, `note`), `summary`.
- **W6-A (T37).** Research's focus wake: `purpose` `focus`, `reasons` `["focus_step"]`, `scan.shortlist`. Answer
  `ResearchFocusReply(...).outcome()`. A later focus answer is a new revision and wakes the Trader (`focus_updated`).
- **W6-B (T38).** The Leader wakes at day starts (unless it named a time) and its own check-ins; it wakes others with
  `wake_requests` or `EventEngine.request_wake(portfolio, department, note, requested_by="leader")`. It cannot install
  plans.
- **W6-C (T39).** HQ reads `wakes`, `wake_triggers`, `department_check_ins`, `focus_selections`, `day_cycle_steps` or
  `EventEngine.wakes`, `next_check_ins`, `focus`, `cycle`. Reading starts nothing.
- **W7-A (T45).** `EventEngine.notify(portfolio, department, kind="news_alarm", source_ref=<headline id>, note=...,
  symbol=...)`: one trigger per headline; it only adds wakes.

## Decisions (reserved block D190–D194)

- **D190 (2026-10-17, implementation default for T35): triggers, wakes and coalescing.** Every reason to wake a
  department is a trigger recorded once per source event (`source_ref` unique: a fill, an alarm delivery, a check-in,
  a day step, a request). A department of a portfolio has at most one open wake (SQL); dispatch gives a department
  with a fresh pending trigger one wake with all its pending triggers, and triggers arriving meanwhile wait for the
  next wake (D116 batching). A wake that ends without the department seeing its batch (failed turn, obsolete) puts
  its alarm batch back in T33's queue and its triggers back as `carried`: they join the next wake but do not start
  one by themselves, so a failing turn is not retried in a loop; the day-step triggers of a failed wake expire with
  the step recorded as failed. A lost feed (`feed_lost`) wakes the owner of the dependent alarms; its return
  (`feed_restored`) rides with the next wake (W1-D left this to W5-A). Each wake is its own root task with the
  allocation, attempt bound and version pin of a scheduled task, so the T05 delegation bounds apply inside a wake
  and never to the number of wakes; nothing limits triggers or wakes. Department root tasks made outside the engine
  (Secretary routing, owner requests) are recorded as `task` wakes so the wake log is complete; they are not
  coalesced, their trigger is delivered whatever their outcome, and they change a check-in only when their answer
  names one. Reason: T35's
  acceptance "coalescing may batch wakes but never drops them", D116, W1-D's queue contract.
- **D191 (2026-10-17, implementation default for T35): check-ins and the daily cycle.** A department's answer names
  its next check-in (`at_utc`) or none, which means the next day start (D123); the newest check-in is in force, a
  past time is due at once, and nothing limits how soon. At the owner's trading-day start (00:00 UTC, D138 item 6;
  the money-limit day start when recorded) each portfolio's day runs once, each step recorded once: `day_start`
  (the Leader, Optimisation and Learning get a `day_start` trigger unless their check-in is a named time today or
  later); `scan` (one live scan by the configured `CoinScanStep`, else the newest recorded live scan, labelled; a
  failed scan is recorded and the day goes on); `focus` (Research is woken with the shortlist); `day_plan` (the
  Trader is woken once the focus step finished, whatever its result); at the day end `day_end_review` (Learning, late
  after downtime, only for days whose start ran). Unfinished steps of an ended day are recorded as failed; a missed
  day is not replayed. Without a department its step is `skipped`. The hourly scan inside the service stays with
  W6-D (C2), which owns the in-service price collector; T35 needs only the day start's scan. Reason: the T35
  deliverables, D115, D123, D138 item 6.
- **D192 (2026-10-17, implementation default for T35): department answers and the Trader's day-plan turn.** A
  department answers under `wake_outcome` in its handler output (`WakeOutcome`); the engine applies it after the
  task's result is saved, so a crash re-applies it idempotently. Day plans come only from the Trader and are
  installed one per coin with `DayPlans.install(request_id="wake:<wake_id>:<symbol>")` under the service's execution
  lock; a refusal is recorded (error code, reason, T34's `day_plan_refused`) and nothing else is retried. Focus coins
  come only from Research and must be on the shortlist of the day's scan when one exists (D120: Research picks from
  the about-10 candidates); a selection with a coin outside it is refused as a whole and recorded; no coins is a
  valid answer. A focus revision outside the day-start step wakes the Trader. Wake requests (any department, never to
  itself) carry a note that is data and grants nothing; the Leader still never places or approves a trade.
  `TraderWakeReply` and `ResearchFocusReply` are the provider-strict forms. Reason: AGENTS.md (persist before
  effects, real Trader discretion, research is data), D121, D138 item 5.
- **D193 (2026-10-17, implementation default for T35): restart, obsolete wakes and the allowance floor.** Triggers
  are written with their source cursor or reference in one transaction; a wake with its task and claims in another;
  a crash leaves nothing or everything, and a queued wake runs once after a restart. A wake whose trading day ended
  before it ran is obsolete (a day-end review never is): a queued task nobody started is cancelled by the next step,
  a claimed one is skipped before any snapshot or paid attempt, and a turn that ends after its trading day installs
  no plan (recorded `obsolete`); its events are carried to the next wake. Wakes are recorded with the allowance
  reading (`available`, `paused` or `unknown`; unknown is never plenty) whether or not AI may run; under the floor
  (D98, D138 item 8) their tasks wait while management, protection and protective exits go on. The contract and
  the engine are protected paths. Reason: T35's acceptance ("restart neither loses nor repeats a wake; obsolete
  wakes never become trades"; "the subscription-allowance floor still pauses new AI while management continues").
- **D194 (2026-10-17, implementation default for T35): the records and the switch.** State migration 0035 holds the
  engine's records as non-financial orchestration tables (fills, intents and the ledger stay the books); its CHECK
  lists are frozen in the migration and a test keeps them equal to the contract, so a new trigger kind needs a new
  migration (`news_alarm` is already in for W7-A). The engine is opt-in in `PaperService` (`event_engine=
  EngineOptions(...)`); without it the service keeps its fixed schedules, so existing installations and tests are
  unchanged. The installed subscription service does not switch it on in this package, because the provider-backed
  departments do not yet read `task["input"]["wake"]` or answer `wake_outcome`; that needs the protected
  subscription context allowlist and the owner's profile, so it is left to the next package and step L3. Reason:
  rule 7 (no silent behaviour change of the running system), AGENTS.md (protected owner configuration).

## Evidence

All runs on CPython 3.12.13 (`uv sync --frozen --group dev --python 3.12`) in this cloud container, in
`/home/user/claude/t35-event-engine`. No network, key, paid, model or exchange call in any test.

- Tests written first:
  - `tests/unit/test_wake_contracts.py` failed at collection without `contracts/wakes.py` (`ModuleNotFoundError`) and
    passes now; its protected-path test failed (3 of 4 paths not protected) before `kernel/authority.py` was changed.
  - `tests/integration/test_migration_0035.py` was written before `wake_schema.py` (import error) and passed on the
    schema's first run.
  - `tests/integration/test_wake_engine.py` and `test_wake_cycle.py` were written before the engine (import error).
    Their first runs found: a test whose fake slow turn outlived its 30-second lease (test fixed); the day-start rule
    waking a department whose own check-in was due at the same moment (fixed: a named time today or later wins); and
    a real gap: a queued wake whose trading day ended without ever being claimed kept its department's next triggers
    waiting forever (fixed: the step cancels such a task and closes the wake as obsolete). A review found two more,
    each shown by a new test first (`test_a_task_made_outside_the_engine_is_recorded_as_a_wake_and_keeps_its_
    departments_check_in`): a failed outside task's trigger was carried and turned the department's next wake into a
    `task` wake, and an outside task that knew nothing of wakes reset the department's named check-in.
  - `tests/integration/test_wake_service.py` failed before the service wiring (`ImportError: EngineOptions`) and
    passes now. `tests/integration/test_scripted_day.py` was written against the wired service; its first runs showed
    only test assumptions (two wakes made at the same instant run in either order; the review needed one more tick).
  - `tests/property/test_wake_properties.py` (Hypothesis, derandomized, 60 examples plus four explicit sequences):
    mutation-checked: making a failed wake expire its event triggers instead of carrying them fails it. The race of an
    alarm firing between collection and dispatch has its own test, which fails when the claimed batch is not
    mirrored into the wake.
- `uv run ruff check src tests`: all checks passed (at `07b89da`).
- New tests: `uv run pytest -p no:cacheprovider -rfs tests/unit/test_wake_contracts.py
  tests/integration/test_migration_0035.py tests/integration/test_wake_engine.py tests/integration/test_wake_cycle.py
  tests/integration/test_wake_service.py tests/integration/test_scripted_day.py tests/property/test_wake_properties.py`:
  **56 passed, 0 failed, 0 skipped** (39 s). The scripted day and the service and cycle tests were also run six times
  in a row (wakes made at the same instant run in random task-id order): all passed each time.
- Related tests: every test module that imports or exercises changed code, 73 modules: those that mention
  `PaperService`/`paper_service`, the normal subscription service, path protection, the migration list
  (`migrate`, `apply_migrations`, Alembic heads, `sqlite_master`, `schema_migrations`), `model_json_schema`,
  `export_schemas` or `_wire_schema`, `AlarmQueue` or `DayPlans` (found with `grep -lE` over `tests`), plus the new
  modules and the Engineer path-protection tests, in three parallel shards of whole modules
  (`uv run pytest -p no:cacheprovider -rfs <modules>`): **1,234 passed, 0 failed, 15 skipped** (14 in
  `tests/security/test_subscription_process_boundary.py`: bubblewrap is not installed in the cloud container, as on
  GitHub; 1 archive-permission case: this user can read any file). An earlier run of the same list at `3f8008a`, in
  four shards with `tests/unit` running at the same time, had one failure:
  `tests/integration/test_hourly_history_refresh.py::test_slow_hour_boundary_refresh_finishes_before_next_role_inputs_while_management_continues`
  (`assert len(management) >= 2`: it expects two management loops within 50 ms of wall time). It does not use the
  engine, passed 5 of 5 runs alone and passed in the final run; it is load-sensitive, like the timing cases on the
  old known list. `tests/integration/test_provider_wire_schemas.py tests/unit/test_contracts.py
  tests/unit/test_protocols.py tests/unit/test_import_boundary.py`: **73 passed**.
- `uv run pytest -p no:cacheprovider -rfs tests/unit`: **2,152 passed, 0 failed, 7 skipped** (39 s; the indicator
  cross-checks the second library cannot make and the archive-permission cases).
- `python3 scripts/check_plan.py`: plan valid, 46 tasks, acyclic; `python3 scripts/test_planning.py`: 11 tests OK;
  `python3 scripts/check_repository_hygiene.py` (and `--staged` before every commit): passed.
- Full suite: not run (night shift: the wave integration runs it).
- Real or synthetic: everything is synthetic and labelled: a USD10,000 paper account with synthetic money limits, the
  example practice strategy with one synthetic confirmation report, synthetic hourly candles, catalog and ticker for
  the real coin scanner, scripted quotes and scripted Research, Trader, Learning and Leader departments. No real
  order, key, paid call or exchange call; a scripted department is not a model test.

## Schema and dependencies

- State migration: **0035** (W5-A's reservation), registered in `migrate.py` after 0034; the Alembic revision `0035`
  has `down_revision = "0034"` and is the single head; `test_migration_0035.py` checks both. W5-B (0036) and W5-C
  (0037) also chain from 0034: **the integrator re-links them after 0035** (their tests check `down_revision`
  against the previous registered version). Candle database: no change.
- Dependencies added: none (`pyproject.toml` and `uv.lock` unchanged).

## Not done, open or for someone else

- **Provider-backed departments (W6-D or a small follow-up before L3; W6-A and W6-B for Research and the Leader).**
  The subscription Trader still answers `TraderReply`; to write day plans on a wake it needs `TraderWakeReply` as its
  reply type for wake tasks, the `wake` context key added to the protected `ROLE_CONTEXT_KEYS["trader"]` (and to the
  owner's pinned subscription profile at L3), and its result put under `wake_outcome`. Then `run_subscription_operation`
  (and `PaperRuntimeConfig`) needs the switch that passes `event_engine=EngineOptions(...)`. Until then the installed
  service keeps its fixed timers.
- **The hourly coin scan in the service and the live ticker request for the day start's scan:** W6-D (C2), with the
  in-service price collector (W2-B left the scan schedule to "W5-A or W6-D"; T35 needs only the day start's scan,
  built here as `CoinScanStep`, which takes the ticker answer as an argument). The price watcher's candle and
  indicator alarms need the candle store kept current in the service (also W6-D).
- **A Trader tool to close a day-plan position or move its exit** (W4-A's open point): not needed by the scripted
  day (its exits close positions), so not built. A plain sell of protected coins is still refused because the exit
  reserves them; a later package adds "cancel the exit, reconcile, then sell or re-protect".
- **`Execution.reconcile` and a lost acknowledgement** (W4-A's finding): an `UNKNOWN` intent stays `UNKNOWN` while the
  broker reports it open (only filled, cancelled and not-found answers move it), which blocks new buys of that coin
  on the older order path. Not in this package's path (a day plan's replacement cancels and reconciles it), and the
  live-pilot tests rely on unknown live effects staying under owner review, so it was not changed here. Proposed fix
  for its own package, test first: when the broker answers `open` or `partially_filled` and the recorded fills equal
  its filled quantity, move the intent to `OPEN`/`PARTIALLY_FILLED` with its venue order id, in paper at least, and
  decide separately whether live pilot effects follow.
- **Integrator:** copy D190–D194 into `docs/DECISIONS.md`; mark T35 in `planning/progress.json` with this evidence.
  Shared lines touched: `application/paper_service.py` (`__init__`, `_schedules`, `_run_role`, `_ingest`, `_tick`),
  `kernel/authority.py` (protected paths), `adapters/persistence/migrate.py` (one import, one line). W5-B and W5-C
  add their own lines after 0034 in `migrate.py`.
- Observed outside scope: the integration branch moved one status-only commit (`d6feac5`) past this branch's base
  (`b09cae4`); no file overlaps.
