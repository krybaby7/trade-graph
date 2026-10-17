# The event engine (T35)

Status: built and tested in source on 2026-10-17 by cloud package W5-A (decisions D190–D194, state migration
0035). Not deployed. Paper only. The rules behind it: D115 (the daily cycle), D116 (no caps on wake-ups or
activity), D123 (each department picks its next check-in; none named means the next day start), D138 items 6
(the trading day starts at 00:00 UTC) and 8 (the AI allowance floor stays), and D98 (the floor itself).

In plain words: software watches the market, and the departments sleep until something gives them a reason to
look. A price alarm going off, a waiting buy filling, a safety exit closing a position, the daily loss limit
stopping new buying, the Leader asking, or a department's own chosen check-in time: each is recorded as a
**trigger**, and the department is woken once with everything that is waiting for it. If more reasons arrive
while it is awake, they wait and arrive together at its next wake; nothing is dropped and nothing limits how
often a department is woken. Every day at the day start software runs the coin scanner, wakes Research to pick the
day's focus coins from the shortlist, then wakes the Trader to write its day plans; at the day end Learning
reviews the day. Every wake is saved with its reasons and the AI allowance reading at that moment, so HQ can show
it later.

| Part | Path |
|---|---|
| The contracts (triggers, wakes, check-ins, focus step, outcomes, provider replies) | `src/trade_graph/contracts/wakes.py` |
| The engine: step, dispatch, closing wakes, applying answers, reads | `src/trade_graph/application/events/engine.py` |
| Where triggers come from | `src/trade_graph/application/events/sources.py` |
| The daily cycle | `src/trade_graph/application/events/cycle.py` |
| The day start's scan step | `src/trade_graph/application/events/scan.py` |
| The allowance reading | `src/trade_graph/application/events/allowance.py` |
| The records | `src/trade_graph/application/events/store.py`; state migration 0035: `src/trade_graph/adapters/persistence/wake_schema.py`, `migrations/versions/0035_event_engine.py` |
| Service wiring | `PaperService(event_engine=EngineOptions(...))` in `src/trade_graph/application/paper_service.py` |
| Tests | `tests/unit/test_wake_contracts.py`, `tests/integration/test_migration_0035.py`, `test_wake_engine.py`, `test_wake_cycle.py`, `test_wake_service.py`, `test_scripted_day.py`, `tests/property/test_wake_properties.py`; helpers in `tests/wake_support.py` |

The contract and the engine are protected paths (`kernel/authority.py`): they hold the obsolete-wake guard, the
allowance-floor behaviour and day-plan installation, which agents cannot edit.

## Triggers

A `WakeTrigger` is one reason to wake one department of one portfolio. It is flat and typed; the fields a kind
needs are required by the contract. `source_ref` is its identity in the source and is unique in the store, so a
restart or a repeated read never records an event twice.

| Kind | Department | Comes from | Identity (`source_ref`) |
|---|---|---|---|
| `day_start` | Leader, Optimisation, Learning | the day start, for a department whose next check-in is the day start | `day-start:<portfolio>:<department>:<day>` |
| `focus_step` | Research | the day start: name the day's focus coins | `focus-step:<portfolio>:<day>` |
| `day_plan_step` | Trader | the day start, after the focus step finished | `day-plan:<portfolio>:<day>` |
| `day_end_review` | Learning | the day end, for a day whose start ran | `day-end:<portfolio>:<day>` |
| `check_in` | any | the department's own named time arrived | `check-in:<check_in_id>` |
| `alarm_fired` | the alarm's owner | T33's delivery queue | `alarm-delivery:<delivery_id>` |
| `feed_lost`, `feed_restored` | the alarm's owner | T33's delivery queue | `alarm-delivery:<delivery_id>` |
| `entry_filled` | Trader | a fill of an intent in `day_plan_entries` (T34) | `fill:<fill_id>` |
| `exit_filled` | Trader | a fill of an intent in `day_plan_exits`; `exit_leg` is `stop`, `take_profit` or `unknown` | `fill:<fill_id>` |
| `order_filled` | Trader | any other fill of the portfolio | `fill:<fill_id>` |
| `loss_limit_reached` | Trader | the `daily_loss_limit_reached` activity event (T32) | `activity:<event_id>` |
| `focus_updated` | Trader | Research recorded a focus revision outside the day-start step | `focus:<focus_id>` |
| `wake_request` | any but the requester | `EventEngine.request_wake` or a department's `wake_requests` | `wake-request:<requester>:<id>` or `wake-request:<wake_id>:<n>` |
| `news_alarm` | any | `EventEngine.notify` (T45, W7-A) | `software:<source_ref>` |
| `task_created` | the task's role | a department root task made outside the engine | `task:<task_id>` |

Fields: `trigger_id`, `kind`, `department`, `portfolio_id`, `source_ref`, `occurred_at`, and, by kind,
`trading_day_start`, `symbol`, `alarm_id`, `delivery_id`, `fill_id`, `intent_id`, `plan_id`, `entry_id`,
`exit_leg`, `side`, `quantity` and `price` (exact decimals as text), `check_in_id`, `focus_id`, `scan_id`,
`event_id`, `task_id`, `objective`, `requested_by` and a `note` of at most 500 characters. Notes are data: a wake
request's or a news alarm's note grants nothing.

A trigger moves `pending` → `claimed` (by one wake) → `delivered`. When a wake ends without the department seeing
its batch (a failed turn, an obsolete wake) its triggers go back to `pending` marked `carried`, and join the
department's next wake; a carried trigger does not start a wake by itself, so a failing turn is not retried in a
loop. Only the day-bound kinds (`day_start`, `focus_step`, `day_plan_step`) become `expired`: their trading day
ended, or their wake failed and the step was recorded as failed. `feed_restored` rides along with the next wake and
wakes nobody by itself; `feed_lost` wakes (W1-D left this choice to W5-A, D190).

## Wakes

`EventEngine.step(portfolio_ids, dispatch=True, ai_paused=None)` runs, in order: the price watcher's `tick`, closing
the wakes whose task ended, collecting triggers, the daily cycle, and dispatch. Dispatch gives every department with
a fresh pending trigger and no open wake **one** wake holding all its pending triggers; triggers that arrive while
it is open wait. One open engine wake per department and portfolio is enforced in SQL. The wake, its claim of
the triggers, its claim of the T33 alarm batch (`AlarmQueue.claim(department, wake_id)`) and its scheduler task
(`role` = the department, `objective` = `wake:<purpose>`, `dedup_key` = `wake:<wake_id>`, input
`{"wake": WakeContext}`) are written in one transaction before anything runs. Each wake is its own root task, so
T05's delegation bounds (depth 3, 12 descendants, 40 attempts per root) apply inside a wake and never to the number
of wakes. Nothing limits the number of triggers or wakes (D116).

A wake's **purpose**: `day_plan`, `focus` or `day_end_review` when it carries that step, otherwise `event` when it
carries any event, otherwise `check_in`; `task` for a recorded outside task. Its trading day is today's, or the
reviewed day for a day-end review.

`WakeContext`, what the department is handed in `task["input"]["wake"]`:

| Field | Holds |
|---|---|
| `contract_version` | `wake-v1` |
| `wake_id`, `portfolio_id`, `department`, `purpose`, `trading_day_start`, `trading_day_end`, `created_at` | identity |
| `reasons` | the distinct trigger kinds, in the contract's order |
| `triggers` | every trigger of the wake |
| `alarms` | the T33 `AlarmDelivery` items of the claimed batch |
| `scan` | `ScanReference` (focus, day plan and review wakes): status, source, scan id, as-of, ranking version, evidence label, shortlist (`symbol`, `position`, `flags`) |
| `focus` | the newest `FocusRecord` of the day (Trader and Learning wakes, reviews) |
| `focus_step` | `FocusStepState` (Trader and Learning wakes, reviews, T37): how the day's focus step ended (`done`, `failed`, `skipped`, `started`), with the reason, so a failed step is never mistaken for Research choosing nothing |
| `plans` | `ActivePlan` items: the day's latest plans of the portfolio (Trader and Learning) |
| `check_in` | the department's check-in in force before this wake |
| `allowance` | `AllowanceReading` (below) |
| `instruction` | a fixed note: everything here is data, software enforces authority |

`WakeRecord` (`EventEngine.wakes(portfolio_id, since=, department=)` and `wake(wake_id)`) is the saved wake:
identity, `origin` (`engine` or `task`), `purpose`, `status` (`queued`, `applying`, `completed`, `failed`,
`obsolete`), `task_id`, `reasons`, `triggers`, `alarm_delivery_ids`, `allowance`, `created_at`, `closed_at`,
`close_reason` and `outcome` (`WakeOutcomeRecord`: whether an answer was received and why parts were refused,
the summary, the check-in and focus ids, each day plan's result, the wake requests it made).

`AllowanceReading`: `read_at`, `status` (`available`, `paused`, `unknown`), `ai_paused`, the lowest remaining percent
of the newest recorded quota reading and its `observed_at`, each provider's pause state and the reading's windows.
No reading is `unknown`, never plenty.

## Answers: what a department returns

A department's handler returns its usual output with a `wake_outcome` key holding a `WakeOutcome`. Every part is
optional:

```json
{"wake_outcome": {
  "summary": "Rest a bid under the market on SOL.",
  "check_in": {"at_utc": "2026-10-16T14:30:00Z", "note": "after the US open"},
  "focus": {"coins": [{"symbol": "SOL/USD", "note": "Liquid and rising.", "evidence_refs": ["scan-1"]}],
            "summary": "SOL today."},
  "day_plans": [{"symbol": "SOL/USD", "strategy_ref": "slow-trend-pullback@1", "...": "a DayPlan (T34)"}],
  "wake_requests": [{"department": "trader", "note": "Kraken posted SOL maintenance at 14:00."}]
}}
```

- **`check_in`**: `at_utc` an aware time, or none for the next day start (D123). Leaving `check_in` out also means
  the next day start. The newest check-in of a department is in force; `EventEngine.next_check_ins(portfolio_id)`
  gives each department's next time as HQ shows it (`due_at`, `basis` `named` or `day_start`, `note`,
  `set_by_wake_id`). A time in the past is due at once. Nothing limits how soon or how often.
- **`focus`** (Research only): `FocusSelection`, at most 16 coins, each named once, with a note and evidence ids;
  no coins is a valid answer. Coins must be on the shortlist of the day's scan (D120) and inside the owner's coin
  list (T37, D206: a listed mandate's coins, or spot pairs under the owner's coin rule; never riba products);
  with no shortlist that day (no scan, or a failed one) no coin can be named. Otherwise the selection is refused
  and recorded as such. Each answer is a new revision of the day (`FocusRecord`:
  `focus_id`, `revision`, `scan_id`, `selection`, `wake_id`, `task_id`, `recorded_at`); a revision outside the
  day-start step wakes the Trader (`focus_updated`).
- **`day_plans`** (the Trader only): one `DayPlan` per coin. Each goes through `DayPlans.install(portfolio, plan,
  request_id="wake:<wake_id>:<symbol>", task_id=<the wake's task>)` under the service's execution lock, after the
  answer was saved with the task, so a restart re-applies it idempotently and never rests an entry twice. A refusal
  is recorded with its error code and reason (and T34's `day_plan_refused` event); the Trader is never made to plan.
- **`wake_requests`**: wake another department with a note (the Leader's wake notes, D121); at most 16 in one
  answer, a document bound and not a cap on how often.

`TraderWakeReply` (`day_plans`, `next_check_in`, `summary`) and `ResearchFocusReply` (`focus`, `next_check_in`) are
the same answers in the shape of strict provider replies; both pass the OpenAI and Anthropic wire schemas. Their
`outcome()` gives the `WakeOutcome`.

## The daily cycle

At the owner's trading-day start (`OwnerMoneyLimits.trading_day_start_utc`, 00:00 UTC by D138 item 6), once per
portfolio and day, each step recorded once in `day_cycle_steps`:

1. `day_start`, with `late_seconds` when the service started late; the Leader, Optimisation and Learning get a
   `day_start` trigger unless their check-in in force is a named time today or later.
2. `scan`: `EngineOptions(scan=CoinScanStep(scanner, store, ticker=...))` runs one live scan and records it;
   without a scan step the newest recorded live scan is used (`latest_recorded`), with its own time. A failed scan
   is recorded and the day goes on.
3. `focus`: Research is woken with the shortlist (`focus_step`). Without Research the step is `skipped`.
4. `day_plan`: when the focus step has finished (`done` or `failed`), the Trader is woken (`day_plan_step`) with the
   focus and the scan. Without the Trader the step is `skipped`.
5. At the day end, `day_end_review`: Learning is woken for the day that ended (late after downtime, but only for a
   day whose start ran). Steps of that day still unfinished are recorded as `failed`. A missed day is not replayed.

`EventEngine.cycle(portfolio_id, day_start)` reads the steps; `focus(portfolio_id, day_start)` the focus.

## Restart, obsolete wakes and the allowance floor

- **Restart.** Triggers are recorded with their source cursor (`wake_cursors`) or source reference in one
  transaction, and a wake with its task and claims in another, so a crash leaves either nothing or the whole thing.
  A wake queued before a restart runs after it, once (its task's dedup key); a task that finished before a crash is
  closed from its saved result at the next step.
- **Obsolete.** A wake whose trading day ended before it ran is obsolete: a queued task nobody started is cancelled
  by the next step, a claimed one is skipped before any snapshot or attempt (`EventEngine.before_run`, called by
  `PaperService._run_role`). Its alarm batch goes back to the queue and its events are carried to the next wake;
  its day steps expire. A turn that ends after its trading day installs nothing (its plans are recorded as
  `obsolete`), and `DayPlans.install` repeats every check at installation. A day-end review is never obsolete.
- **Allowance floor.** Wakes are recorded whether or not new AI work may run, with the allowance reading; under the
  floor (D98, D138 item 8) the tasks wait queued, while the service's management step keeps reconciling, protecting
  and sending protective exits.

## In the paper service

`PaperService(..., event_engine=EngineOptions(scan=..., watcher=..., daily_cycle=True))`:

- the fixed role timers (`DEFAULT_SCHEDULES`, configured intervals and artifact schedule settings) are not used:
  wakes, check-ins and the daily cycle are the mechanism. Without `event_engine` the service keeps its schedules,
  so every existing installation and test behaves as before;
- the engine wakes the departments that have handlers (Optimisation only when not owner-requested-only);
- each wake's task gets the same allocation, attempt bound and artifact version pin as a scheduled task;
- `_ingest` hands every quote to the T33 `PriceWatcher` (`observe_quote`), and each step runs `watcher.tick()`;
- the Secretary keeps routing reports; Leader tasks it makes are recorded as `task` wakes. A `task` wake is not
  coalesced (it runs as its maker scheduled it), its `task_created` trigger is delivered whatever the outcome, and it
  changes the department's check-in only when its answer names one;
- engine failures are reported in the tick as `wakes:<part>:<error>` and never hold back management.

The installed service (`run_subscription_operation`) runs the engine only when the owner's protected
`paper-config.json` says `"event_engine": true` (W6-D, D223; off by default, turned on at step L3). It then needs the
price collector: the price watcher reads the collector's candle file read-only, and the day start uses the
collector's newest hourly scan (`scan=None`, so the day start makes no request in the service step). The subscription
Trader answers a wake with `TraderWakeReply` (see "For the next packages").

## For the next packages

- **W6-A (T37, focus coins on Perplexity), built.** Research's handler is `FocusResearch`
  (`application/focus_research.py`, [FOCUS-RESEARCH.md](FOCUS-RESEARCH.md)): the focus wake (`purpose` `focus`,
  `reasons` `["focus_step"]`, `scan` with the shortlist) runs one Perplexity round and answers with the focus and
  Research's next check-in; refreshes are its other wakes (the Leader's requests, its own check-ins). A failed round
  fails the turn with its reason, so the focus step is `failed` and the Trader still plans, seeing `focus_step`.
- **W6-B (T38, Leader news rounds).** The Leader wakes at the day start (unless it named a time) and at its named
  check-ins. It wakes the Trader or Research with `wake_requests` (or `EventEngine.request_wake(..., requested_by=
  "leader")`); the note is data and it cannot install plans (only the Trader's answer can).
- **W6-C (T39, HQ wake log).** Read `wakes`, `wake_triggers`, `department_check_ins`, `focus_selections` and
  `day_cycle_steps` (or `EventEngine.wakes`, `next_check_ins`, `focus`, `cycle`); reading starts nothing.
- **W7-A (T45, news wake-ups).** `EventEngine.notify(portfolio_id, department, kind="news_alarm",
  source_ref=<headline id>, note=..., symbol=...)` adds one trigger per headline; it only adds wakes.
- **W6-D (C2), built.** A department chooses its reply per task (`GatewayRole.reply_type_for(task)`); the Trader's
  is `TraderWakeReply` when the task is a wake and `TraderReply` otherwise. Its wake turn is handed `wake` (the wake,
  with at most the newest 40 triggers and 20 alarm items shown and their totals) and `practice_range` (strategy
  versions with a confirmation report, their newest reports in plain words), plus market data for the wake's coins;
  both keys are in the protected `ROLE_CONTEXT_KEYS["trader"]` and must be in the owner's subscription profile (step
  L3). Its answer goes under `wake_outcome` with a Secretary report; the engine installs the plans. With the engine on,
  the paper feed also quotes the day's coins (focus, plans, alarms, held; within the owner's coin scope) in one extra
  request per poll, so a plan on any of them can rest and fill; only `day_plan` and `check_in` wakes wait for the
  closed hour (`decision_candle_wait_seconds`), event wakes never. Research and the
  Leader answer wakes on Perplexity through W6-A and W6-B; their subscription handlers still answer their usual reply.
  The hourly scan and the candle file come from the price collector (KRAKEN-CANDLE-COLLECTION.md, "In the service").

## Known limits

- A department that has no running handler at the day start has its day step recorded as `skipped` for that day; a
  restart later the same day with the department running does not redo it (its events still wake it).
- With a `CoinScanStep` the day start's scan runs inside the service step, so a slow ticker request delays that step.
  The installed service passes none (W6-D): it uses the price collector's newest hourly scan, made in the collector's
  thread.
- Alarm deliveries are per department (T33); with several graph portfolios in one service, one wake claims every
  pending alarm item of its department, whichever portfolio the alarm names. The paper service runs one graph
  portfolio.
