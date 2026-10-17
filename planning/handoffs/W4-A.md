# W4-A T34: Trader day plans with resting entries and safety exits

For the owner: The Trader can now write a plan for each coin it watches, with buy orders waiting below the price and
a safety exit for each, and software checks the plan against your money limits, coin list and no-riba rules, saves
everything before sending anything, and puts the safety exit on the coins the moment a buy fills, even partly, so a
position is never left unprotected while the Trader sleeps; a pause cancels the waiting buys and keeps the safety
exits. Nothing is switched on yet: plans stay off until you record your money limits at step L3
(`trade-graph money-limits --confirm`), and the part that asks the Trader to write a plan each morning comes with
the next package (W5-A). Nothing needs a decision from you; everything was tested with made-up prices, and no real
money, key, paid service or exchange was used.

Branch: `claude/t34-day-plans`, head: the commit that adds this line; code at `20dac09`. Pull request: added in the next commit.

## What was built

- `src/trade_graph/contracts/day_plans.py`: the `DayPlan` contract: focus coin, practice-range strategy version,
  menu indicators with settings (name and value pairs), alarms (a flat form of the T33 alarm contract), resting limit
  entries each with a stop below and an optional take-profit above, an optional expiry, and a concise rationale and
  invalidation. Exact decimals, floats refused, unknown fields refused; no confidence, score, approval or vote field.
  Plain named objects, so it can be a strict provider reply when T35 asks the Trader for a plan (tested through both
  provider wire schemas).
- `src/trade_graph/application/day_plans.py`: `DayPlans.install` (deterministic validation, then the plan, every
  entry's authorized intent with reservation and outbox row, and the alarms in one transaction before anything is
  sent; idempotent by `request_id`; a new plan for a coin replaces the last after cancelling and reconciling its
  resting entries, and is refused while any of them is unsettled), `manage` (the management step: a pause cancels
  resting entries, expired entries are cancelled, filled parts are protected, protective exits are sent), `status`,
  `active`, `get`. A refused plan writes only a `day_plan_refused` activity event.
- `src/trade_graph/application/day_plan_protection.py`: `DayPlanProtection`: `after_fill` (called by
  `Execution.record_fill` inside the fill's own transaction), `protect` (exit for whatever is filled and not covered,
  never more than is held and unreserved), `sweep` (idempotent retry for held coins), `coverage`.
- `src/trade_graph/application/execution.py`: `place_protection(..., take_profit_price=, protective=)`,
  `dispatch(protection_only=)`, `cancel_intents(ids, reason=)` (asks again when an earlier cancellation got no answer),
  the fill hook, a protective exit may be sent under any pause that keeps protection, and a take-profit intent is
  refused at dispatch by a broker without `native_take_profit`.
- `src/trade_graph/contracts/models.py`: `AuthorizedOrderIntent.take_profit_price` and
  `BrokerCapabilities.native_take_profit`, both left out of the serialised document when absent, so every existing
  intent, payload and capability record reads and serialises byte for byte as before.
- `src/trade_graph/adapters/brokers/paper.py`: a sell stop may carry a take-profit leg (one order, one cancels the
  other); partial fills use the instrument's lot (below).
- `src/trade_graph/kernel/fills.py`: partial fills rounded down to the lot when the caller gives the instrument's
  increment and minimum (the fix of the pre-existing W1-B bug, below); without them the behaviour is unchanged.
- `src/trade_graph/kernel/authority.py`: `pause_allows_protection`; the three new modules are protected paths.
- `src/trade_graph/application/paper_service.py`: `_management` runs `DayPlans.manage` for every managed portfolio
  after the money-limit step (a failure is reported as `day_plans:<error>` and never holds back dispatch); `_ingest`
  sends protective exits right after a quote that filled an entry, so the next quote of the same poll can trigger
  them (a failure there is not a feed failure).
- `src/trade_graph/adapters/persistence/day_plan_schema.py`, `migrations/versions/0034_day_plans.py`, one import and
  one line in `adapters/persistence/migrate.py`: state migration 0034 (four append-only tables).
- `docs/DAY-PLANS.md` (new): the rules, the contract, installation, protection, the management step, the tables and
  the contracts for W5-A, W5-B and W6-C. `docs/FILL-ENGINE.md`: the lot rule and take-profit legs.
- Tests: `tests/unit/test_day_plan_contract.py`, `tests/unit/test_fill_engine_lots.py`,
  `tests/integration/test_day_plans.py`, `test_day_plan_recovery.py`, `test_day_plan_service.py`,
  `test_migration_0034.py`, `test_paper_partial_fill_lots.py`, `tests/property/test_day_plan_protection_properties.py`;
  helpers in `tests/day_plan_support.py`.

## The pre-existing partial-fill bug (assigned to W4-A by the night lead)

Reproduced first on this branch's base (`ce17fcb`) with a 13 SOL resting buy crossed by volume-less quotes: the 27th
quote raised `decimal.Inexact` in `Execution._consume_reservation` (by then 12.99999980628490447998046875
SOL had filled, in ever longer decimals). Root cause: on a quote without a volume figure the shared fill engine
filled `remaining × 0.5` and nobody rounded it to the instrument's lot. Fix at the root, in `kernel/fills.py`: given
the instrument's `quantity_increment` and `min_quantity` (the paper broker now passes them), every partial fill is
rounded down to the lot; a share that covers what remains fills it exactly; when the rounded share would be below the
minimum, an order filled on the engine's own assumption (no volume figure) finishes, and an order limited by a
reported volume or displayed size fills nothing yet. The same order now finishes in 18 fills, each on the 0.0001 lot,
with no reservation left held. The candle path accepts the same parameters, but the practice range does not pass
them yet, so its replays and golden results are unchanged (W2-A's known limit stays). T05 guarantees and every test
that uses the paper broker or the fill engine pass (40 modules, 748 tests, before the day-plan work).

## Decisions (reserved block D185–D189)

- **D185 (2026-10-17, implementation default for T34): the day-plan contract.** A `DayPlan` names one focus coin
  (a plain spot pair), a registered practice-range strategy version, the menu indicators it watches (collected
  intervals 1, 5, 15, 60; menu-bounded settings as name and value pairs), alarms (a flat form of the T33 alarm
  contract, owned by the Trader, `day_plan_ref` the plan), resting limit buys each with a stop below the limit and an
  optional take-profit above it and an optional expiry, and a concise rationale (at most 1,000 characters) and
  invalidation (500). It has no confidence, score, approval or vote field and refuses unknown fields; prices and
  quantities are exact decimals, floats refused. A plan may set alarms without entries (the Trader is never made to
  trade) but must do one or the other. Document size bounds (16 entries, 16 alarms, 8 indicators) are request
  envelopes, not limits on activity: plans may be written and revised without limit (D116). The shapes are plain
  named objects so the contract can be the Trader's strict provider reply in T35. Reason: the T34 deliverable,
  AGENTS.md (real Trader discretion, concise rationale) and D116.
- **D186 (2026-10-17, implementation default for T34): deterministic validation.** A plan is refused, writing only a
  `day_plan_refused` activity event, unless: the execution binding and portfolio are paper (live venues need native
  protective orders first, T19); the owner has recorded money limits (D138, W3-A's gate); the portfolio is
  `RUNNING`; the coin is a plain spot pair (D127); the strategy version is registered and has at least one
  practice-range **confirmation** report recorded before now (D138 item 5, with W2-A's recommendation that a tuning
  report alone is in-sample evidence; still no pass mark); prices are on the instrument's tick and quantities on its
  lot and at or above its minimums (refused, never rounded, so what rests is exactly what the Trader wrote); every
  entry's limit is below the current ask (a resting maker bid, not an order that would fill at once as a taker; this
  also avoids W1-B's open point about marketable quote-path limits); and every entry expires in the future and no
  later than the end of the trading day (default: the end of the day, D138 item 6). Each entry then goes through
  `Execution.authorize(..., day_plan=True)`, which applies the coin list or rule, the mandate (strategy id, order
  type), the daily loss limit, quote freshness, the balance and the per-coin and total shares counting every resting
  entry as if filled (D181); one refusal refuses the whole plan. Reason: T34 "deterministic validation", D138 and
  "unknown is not a yes".
- **D187 (2026-10-17, implementation default for T34): installation, idempotency and replacement.** The plan row,
  every entry's authorized intent with its reservation and outbox row, the alarms and an `installed` event are
  written in one transaction before anything is sent; the service's dispatch sends the entries. `(portfolio,
  request_id)` is unique, so a retried installation returns the installed plan (the same request with another
  document is refused) and never rests an entry twice; an interrupted submission is `UNKNOWN`, reconciled by its
  client id and never resent. Each coin of a portfolio has one chain of plans (SQL triggers): a new plan replaces the
  coin's latest plan after cancelling and reconciling its outstanding entries (a cancellation that got no answer is
  asked again), and is refused while any of them is still unknown or cancel-pending; the replaced plan's alarms are
  cancelled; positions it opened keep their exits. If the new plan is then refused (for example by the money
  limits) the replaced entries stay cancelled, and the refusal is visible. Reason: AGENTS.md (persist intents before
  effects; unknown is not rejection; reconcile before replacement) and the T34 acceptance "no duplicate entries
  after a crash".
- **D188 (2026-10-17, implementation default for T34): protective exits and pauses.** An exit is one paper sell stop
  at the entry's stop with the entry's take-profit as a second leg of the same order (`take_profit_price`, capability
  `native_take_profit`), so whichever fills first closes it (one cancels the other); both fill as triggered taker
  sells with the 0.10% exit slippage (D147). `Execution.record_fill` attaches the exit inside the transaction that
  records an entry fill, for exactly the filled part not yet covered; each partial fill gets its own exit. Coverage
  is arithmetic over records: an outstanding or unknown exit covers its whole quantity (so an unknown status never
  leads to a second exit), a filled exit what it sold, an exit that ended unfilled only what it sold; an exit never
  sells more than is held and unreserved. A placement that cannot be made yet is recorded once per reason
  (`protection_deferred`) and the management step's sweep retries it; the fill is never lost. Protective exits may be
  placed and sent under `RUNNING`, `PAUSE_DECISIONS`, `NO_NEW_EXPOSURE` and `MANAGE_ONLY`; the owner's `CANCEL_ALL`
  cancels them by choice and nothing is re-attached until it ends, `FLATTEN` sells everything itself, `STOPPED` is
  flat. Under every pause but `RUNNING`, including `PAUSE_DECISIONS` and the system's `MANAGE_ONLY`, the management
  step cancels day-plan resting entries (D117: a pause cancels resting entries and keeps exits); other resting buys
  keep their existing pause behaviour. Expired entries are cancelled; cancelled entries are never restored. The paper
  service sends protective exits right after the quote that filled an entry. Reason: D117, the T34 acceptance
  "every open paper position carries a protective exit while the Trader sleeps" and AGENTS.md (every pause specifies
  order and position management; protection continues).
- **D189 (2026-10-17, implementation default for T34, fixing a W1-B defect): partial fills on the lot.** Given the
  instrument's `quantity_increment` and `min_quantity`, the shared fill engine rounds every partial fill down to the
  lot; a share that covers what remains fills it exactly (even a remainder left off the lot earlier); when the
  rounded share is below the minimum, an order filled on the engine's own assumption (a quote with no volume figure,
  `paper-quote@1`'s half of what remains) finishes, because what remains is then at most about two minimum lots, and
  an order limited by a reported volume or displayed size fills nothing yet, because that much did not trade. The
  paper broker passes its instrument rules; without them (the practice range today) nothing changes. Reason: the
  reproduced `decimal.Inexact` after 26 volume-less quotes, venues fill whole lots, and resting day-plan entries
  make volume-less crossings common.

## Evidence

All runs on CPython 3.12.13 (`uv sync --frozen --group dev --python 3.12`) in this cloud container, in
`/home/user/claude/t34-day-plans`. No network, key, paid, model or exchange call in any test.

- Tests written first:
  - `tests/integration/test_paper_partial_fill_lots.py` and `tests/unit/test_fill_engine_lots.py` failed before the
    fix (`decimal.Inexact` in `Execution._consume_reservation`; 9 failures: unrounded quantities, a volume below one
    lot filling) and pass after it.
  - `tests/integration/test_day_plans.py` was written before `application/day_plans.py`; its first run against the
    module showed four test mistakes (a falling ask also fills the resting bid; `advance_pause` already cancels buys
    under `NO_NEW_EXPOSURE`; unordered dispatch) and one real gap: a cancellation that got no answer was never asked
    again, so a coin's next plan stayed refused forever; `cancel_intents` now asks again.
  - `tests/integration/test_day_plan_service.py` failed before the paper service wiring (`AttributeError: day_plans`;
    the resting entry not cancelled under `MANAGE_ONLY`; the stop not at the broker when the next quote of the same
    poll fell through it) and passes after it.
  - The contract, migration, recovery and property tests were written alongside their modules. Mutation checks: with
    the fill hook removed, the property test and five integration tests fail; with the sweep removed, the property
    test fails; with protective exits sent only under `pause_allows_reduction`, the `PAUSE_DECISIONS` test fails;
    without the unsettled-entry check or the request lookup, four integration and recovery tests fail (the request
    lookup is backed by the SQL unique constraint). Counting outstanding exits as uncovered does not fail any test,
    because the base-asset reservations alone already stop a second exit for the same coins (defence in depth).
- `uv run ruff check src tests`: all checks passed (at `20dac09`).
- New tests: `uv run pytest -p no:cacheprovider -rfs tests/unit/test_day_plan_contract.py
  tests/unit/test_fill_engine_lots.py tests/integration/test_day_plans.py tests/integration/test_day_plan_recovery.py
  tests/integration/test_day_plan_service.py tests/integration/test_migration_0034.py
  tests/integration/test_paper_partial_fill_lots.py tests/property/test_day_plan_protection_properties.py` at
  `20dac09`: **105 passed, 0 failed, 0 skipped** (28 s).
- Related tests: two runs, each `uv run pytest -p no:cacheprovider -rfs <modules>` split into parallel
  shards of whole modules.
  - At `ae70b32` (all code but the last fix): every module that imports or exercises changed code, 274 modules: the
    235 test modules that mention `Execution`, `PaperService`, `PaperBroker`/`DropAckBroker`, `place_protection`,
    `AuthorizedOrderIntent`, `BrokerCapabilities`, the fill engine, `kernel.authority`, path protection, pause rules,
    `model_json_schema`, `export_schemas`, golden or snapshot JSON, the wire schemas (`_wire_schema`,
    `output_schema`), the migrations, `Database(`, the paper runtime, `contracts.models`, the money-limit helpers,
    the practice range, the paper service or execution (found with `grep -lE` over `tests`), plus all of `tests/unit`
    and `tests/property/test_price_alarm_properties.py`, in four shards (27 to 37 min each): **6,351 passed, 2 failed,
    23 skipped**. The two failures were this package's:
    `tests/integration/test_owner_expense_upgrade.py::test_schema_0023_checkpoint_chain_upgrades_to_0024_without_resealing[preserve-originals]`
    and `[reject-old-prefix-edit]` (`sqlite3.OperationalError: no such table: day_plan_entries`): the test records a
    fill on a database deliberately kept at schema 0023, and the new fill hook read the day-plan tables. Fixed in
    `20dac09`: the hook and the management step do nothing on a database before 0034 (new regression test
    `test_a_database_still_on_an_older_schema_records_fills_without_day_plans`, which failed the same way first).
    The 23 skips: 14 in `tests/security/test_subscription_process_boundary.py` (bubblewrap is not installed in the
    cloud container, as on GitHub), 5 archive-permission cases (this user can read any file) and 4 indicator
    cross-checks the second library cannot make.
  - At `20dac09`: the 71 modules that construct `Execution` or `PaperService`, call `dispatch`, truncate the
    migration list, or use the day-plan or money-limit helpers (including `test_owner_expense_upgrade.py`,
    `test_financial_transition.py`, `test_financial_recovery.py`, `test_live_pilot_dispatch.py`,
    `test_kraken_live_adapter.py`, `test_execution*.py`, `test_pause_profiles.py`, `test_paper_service.py`), in three
    shards: **1,553 passed, 0 failed, 0 skipped**; `tests/integration/test_provider_wire_schemas.py
    tests/unit/test_contracts.py tests/unit/test_protocols.py`: **71 passed**;
    `tests/integration/test_venue_conformance.py`: **37 passed**.
  - Before the day-plan work, after the lot fix (`8c3de4c`): the 40 modules that use the paper broker, the fill
    engine or the money-limit helpers: **748 passed, 0 failed**.
- `uv run pytest -p no:cacheprovider -q tests/unit`: at `20dac09`: **2,138 passed, 0 failed, 7 skipped** (36 s; the skips as above).
- `python3 scripts/check_plan.py`: plan valid, 46 tasks, acyclic; `python3 scripts/test_planning.py`: 11 tests OK;
  `python3 scripts/check_repository_hygiene.py` (and `--staged` before every commit): passed
- Full suite: not run (night shift: the wave integration runs it).
- Real or synthetic: everything is synthetic and labelled (a USD10,000 account, synthetic limits, scripted quotes,
  the example practice strategy with one synthetic confirmation report that replayed nothing). No real order, key,
  paid call or exchange call. A scripted quote is not a live feed test: the first real day plans run in the scored
  paper test after L3 and W5-A.

## Schema and dependencies

- State migration: **0034** (W4-A's reservation), registered in `migrate.py` after 0033; the Alembic revision `0034`
  has `down_revision = "0033"` and is the single head; `test_migration_0034.py` checks both. Candle database: no
  change.
- Dependencies added: none (`pyproject.toml` and `uv.lock` unchanged).

## Not done, open or for someone else

- Owner, step L3: `trade-graph money-limits --confirm` (W3-A). Until then every plan is refused, as D138 requires.
- W5-A (T35): the Trader's day-start turn writes `DayPlan`s and calls `DayPlans.install` under the service's
  execution lock with a `request_id` from its task and coin; wake reasons are fills of `day_plan_entries` intents,
  fills of `day_plan_exits` intents (the broker order's `fill_terms` reason says stop or take-profit) and the plan's
  alarms; `DayPlans.status` is the plan's state for the Trader's context. A Trader tool to close a day-plan position
  or move its exit (cancel the exit, reconcile, then sell or re-protect) is not built: today a plain sell of
  protected coins is refused as insufficient balance.
- W5-B (T41) and W6-C (T39): HQ views of plans, entries, exits, refusals and plan events (nothing is shown yet).
- T19 (live): day plans refuse every binding but paper; per-fill exits can be below a real venue's minimum order
  value, so a live version merges or resizes them.
- Practice range (W2-A follow-up): pass the instrument's lot to `fill_order_on_candle` when replays should fill
  whole lots; this changes replay results, so it needs its own decision.
- Integrator: copy D185–D189 into `docs/DECISIONS.md`; mark T34 in `planning/progress.json` with this evidence.
  Shared lines touched: `contracts/models.py` (`AuthorizedOrderIntent.take_profit_price`,
  `BrokerCapabilities.native_take_profit`), `application/execution.py` (the fill hook, `place_protection`,
  `dispatch`, `cancel_intents`, `_mark_submitting`, `_unsupported_feature`), `application/paper_service.py`
  (`__init__`, `_management`, `_ingest`), `kernel/authority.py` (protected paths, `pause_allows_protection`),
  `kernel/fills.py`, `adapters/brokers/paper.py`, `adapters/persistence/migrate.py` (one import, one line),
  `docs/FILL-ENGINE.md`. W5-A will likely touch `paper_service.py` and `execution.py` as well.
- Observed outside scope: `Execution.reconcile` leaves an `UNKNOWN` intent `UNKNOWN` while the broker reports it
  open (only filled, cancelled and not-found answers move it), so a lost acknowledgement blocks new entries on that
  coin until the order fills or is cancelled; day-plan replacement cancels it, which resolves it. Worth a look in T35.
