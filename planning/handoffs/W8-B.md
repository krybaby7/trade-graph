# W8-B C4: Trader exit tools and order reconcile

For the owner: The Trader can now change its mind about a position its day plan opened: when it is woken it can
close that position at once or move its safety exit to a new price, and software then cancels the old exit, checks
with the (practice) broker that the cancel really happened, and sells or puts the new exit on, keeping the old exit
in place whenever a step is refused or gets no clear answer. Two smaller fixes come with it: in practice
trading, an order whose confirmation got lost no longer blocks new buys of that coin once the broker shows the order,
and a database index keeps the "latest price of a coin" lookups fast as the price history grows. Nothing needs your decision or your hands now;
one question for later (whether the confirmation fix should also apply with real money) is listed below for the
live pilot, and everything was tested only with made-up prices, without any key, paid service or exchange.

Branch: `claude/c4-trader-exit-tools`, head: the commit that adds this line; code at `__CODE__`. Pull request:
__PR__.

## What was built

- `src/trade_graph/contracts/day_plans.py`: `PositionAction` (the Trader's own words: `action` `close` or
  `move_exit`, `symbol`, `stop_price`, `take_profit_price`, a concise `reason`; exact decimals, floats refused, no
  confidence, approval or vote field, unknown fields refused; a close names no prices, a move names its stop and an
  optional take-profit above it), and the read-only position view `DayPlanPosition` / `PositionExit` /
  `PositionActionSummary`.
- `src/trade_graph/contracts/wakes.py`: `position_actions` (at most one per coin) in `WakeOutcome` and the strict
  provider reply `TraderWakeReply`; `WakeContext.positions` (the coins' day-plan positions, at most 16);
  `AppliedAction` and `WakeOutcomeRecord.actions`. Every new field has a default, so saved answers, contexts and
  outcome records read as before; both strict wire schemas (OpenAI, Anthropic) accept the reply.
- `src/trade_graph/application/day_plan_actions.py` (new, protected path): `DayPlanActions.apply` carries out an
  action as one recorded sequence: deterministic checks; the intents about to be cancelled recorded before any cancel;
  cancel and reconcile (a close cancels the coin's resting plan buys first, then its exits); then one market sell of
  what those exits protected (each entry's part recorded) or new exits at the new terms for exactly what is filled
  and uncovered; sent at once; one end (`done`, `refused`, `failed`). A cancel without an answer, or a sell or exit
  refused by software or the broker, leaves the old exits in place (kept, or put back with their own terms) and ends
  the action. A crash leaves it running: the management sweep protects the position meanwhile and applying the same
  request again finishes it from the records, never selling twice. `abandon` ends an unfinished action (obsolete
  wake); a newer action for the coin supersedes one; both abandon a sell persisted but never sent. `positions`
  gives the Trader's view.
- `src/trade_graph/application/day_plan_protection.py`: the coverage arithmetic counts a close's sell per entry like
  an exit (outstanding, unknown or filled: its part; ended: what it sold, shared out in order); a fill's exit keeps
  the entry's own terms while re-protection (the sweep) uses the terms in force (the newest move that ended `done`);
  `place` is the raising placement the actions use; `protect` keeps its deferral. A database before 0044 counts no
  actions.
- `src/trade_graph/application/execution.py`: the reconcile fix (below) and `dispatch(intent_ids=...)`, which sends
  only the named intents under every usual dispatch rule (the close sends its sell right after the cancel).
- `src/trade_graph/application/events/engine.py`: applies the Trader's position actions under the execution lock
  after the answer is saved and before the answer's day plans (`request_id` `wake:<wake_id>:action:<symbol>`),
  records each result with the wake, refuses them from other departments, marks them `obsolete` after the trading
  day (and abandons an unfinished one); puts `positions` into Trader and Learning wake contexts; the engine's
  `INSTRUCTION` names `position_actions`.
- `src/trade_graph/application/trader_workflow.py`: the Trader's wake brief (`WAKE_INSTRUCTION`) explains
  `wake.positions` and `position_actions`; the Secretary report lists the actions.
- `src/trade_graph/adapters/persistence/day_plan_action_schema.py`, `migrations/versions/0044_day_plan_actions.py`,
  one import and one line in `adapters/persistence/migrate.py`: state migration 0044 (below).
- `src/trade_graph/kernel/authority.py`: `application/day_plan_actions.py` is a protected path (one comment and one
  line after the T34 lines).
- Docs: `docs/DAY-PLANS.md` (new section "Closing a position or moving its exit", the 0044 tables, known limits),
  `docs/EVENT-ENGINE.md` (`positions`, `position_actions`, `AppliedAction`, the wake brief),
  `docs/04-EXECUTION-AND-RECOVERY.md` (one sentence on the reconcile fix).
- Tests: `tests/unit/test_position_action_contract.py`, `tests/integration/test_reconcile_open_unknown.py`,
  `test_migration_0044.py`, `test_day_plan_actions.py`, `test_wake_position_actions.py`,
  `tests/property/test_day_plan_action_properties.py`; helpers in `tests/day_plan_action_support.py`. One expectation
  in `tests/integration/test_day_plan_recovery.py` changed with the reconcile fix (below).

## Decisions (reserved block D245–D249)

- **D245 (2026-10-17, implementation default for C4): the Trader's position actions.** In its wake reply the Trader
  may name at most one `PositionAction` per coin: `close` (sell the coin's day-plan position now) or `move_exit` (a
  new stop and an optional take-profit; none means no take-profit leg; the new terms replace all of the coin's exits).
  The position is coin-level: what the coin's day-plan exits protect across every plan of the portfolio (today's and
  earlier ones), plus filled parts still waiting for their exit; the Trader sees it in `wake.positions`. Software
  checks only deterministic facts before touching anything: the paper account and portfolio (live needs native
  protective orders first, T19); the portfolio is `RUNNING` (any pause refuses: the pause manages the position and
  the exits stay); spot only (D127); a position exists; a quote; for a move, prices on the tick, the stop below the
  bid and the take-profit above it (otherwise it would sell at once: to sell now, close); for a close, the mandate
  allows the market sell and it is above the venue minimum. No confidence threshold, committee, approval or score
  (D116, D121); a refusal is recorded with its reason and touches nothing. A close also cancels the coin's resting
  day-plan buys first, so the position stays closed until the Trader plans again. Selling only part of a position
  is not built. Reason: the C4 deliverable, D115–D117, AGENTS.md (real Trader discretion, concise rationale).
- **D246 (2026-10-17, implementation default for C4): one recorded sequence that never leaves a position bare.**
  Each action is a row in `day_plan_actions` with its steps in `day_plan_action_steps`: the intents about to be
  cancelled are recorded before any cancel request; cancel and reconcile; then a close's single market sell (each
  entry's part in `day_plan_action_orders`, counted by the protection arithmetic like an exit) or a move's new exits
  (one per entry, for exactly what is filled and uncovered); sent at once; one end. A cancel without an answer ends the
  action `failed` and the unanswered exit keeps covering its coins (unknown is not cancelled); a sell or new exit
  refused by software or the broker ends it `refused`; in both cases every exit it had cancelled is put back with its
  own stop and take-profit for what it had not sold, and anything still uncovered is swept. A position is without a
  safety exit only during the sell itself (between the cancel and the sell's fill, or while the sell's own status is
  unknown). A fill's exit keeps the entry's bracket terms; re-protection after the owner's cancel-all uses the terms
  in force (the newest move that ended `done`; a refused move never becomes them). One action per coin runs at a time
  (SQL); a crash leaves it running, the management sweep protects the position meanwhile, and the same request
  applied again finishes it from the records (a persisted sell is sent, never placed twice). A newer action for the
  coin, or an obsolete wake, ends an unfinished one `failed`, abandons a sell it persisted but never sent, and puts
  the old exits back. Reason: the C4 acceptance, AGENTS.md (persist intents before effects, unknown is not rejection,
  reconcile before replacement, every pause specifies order and position management), D117, D187, D188.
- **D247 (2026-10-17, implementation default for C4): the event engine and what the Trader sees.** The engine
  applies position actions under the execution lock after the answer is saved and before the same answer's day
  plans, with `request_id` `wake:<wake_id>:action:<symbol>`, so a restart re-applies them once and a close never
  cancels a plan written in the same turn; each result is recorded in the wake outcome (`AppliedAction`: done,
  refused, failed or obsolete, with the action id, error code and reason). Only the Trader acts on positions; an
  answer that arrives after its trading day acts on nothing (D193). Trader and Learning wakes carry `positions`
  (held, protected, unprotected, the exits by terms, resting entries, the newest action), at most 16 coins and 8
  terms each, a document bound and not a cap on activity; the Trader's wake brief and the engine's instruction
  explain the action. No new trigger kind: a close's sell fill wakes the Trader as `order_filled`, a moved exit's
  fill as `exit_filled`. Reason: D190–D194, D223, D116.
- **D248 (2026-10-17, implementation default for C4, W4-A's finding with W5-A's proposed fix): a lost
  acknowledgement the paper broker can see.** On the paper binding, when the broker answers `open` or
  `partially_filled` for an intent whose submission had no answer (`UNKNOWN`, or `SUBMITTING` after a crash) and the
  recorded fills equal its filled quantity, reconciliation moves it to `OPEN` or `PARTIALLY_FILLED` with its venue
  order id, so it no longer blocks new entries of that coin ("unresolved execution"). Nothing is resent; a cancel
  still unanswered (`CANCEL_PENDING`) is not reopened; when the fills differ it stays `UNKNOWN`. Live bindings keep
  today's behaviour (unknown live effects stay under owner review); the live question is an owner question below.
  Reason: the C4 deliverable, AGENTS.md (unknown order status is not rejection).
- **D249 (2026-10-17, implementation default for C4): state migration 0044 and the valuation-mark index.** Three
  append-only tables (`day_plan_actions`, `day_plan_action_steps`, `day_plan_action_orders`) hold the actions as
  execution controls and evidence (intents, fills and the ledger stay the books); SQL keeps one request per action,
  one running action per coin, one end per action and nothing after it, a sell or attachment once, a close's sell a
  market sell of the entry's coin and an action's exit a day-plan exit of that entry. The index
  `valuation_marks_newest` on `valuation_marks (portfolio_id, asset, observed_at)` serves every "newest mark of a
  coin" read (W5-C's finding); existing databases upgrade once without rewriting rows. Day-plan tables of 0034 are
  unchanged (entries a close cancels are recorded in the action's steps, not as `day_plan_events`, whose reasons are
  frozen). `Execution.dispatch(intent_ids=...)` is additive. The new module is a protected path. Reason: rule 11,
  AGENTS.md (persist before effects, protected execution controls).

## Evidence

All runs on CPython 3.12.13 (`uv sync --frozen --group dev --python 3.12`) in this cloud container, in
`/home/user/c4-trader-exit-tools`. No network, key, paid, model or exchange call in any test.

- Tests written first:
  - `tests/integration/test_reconcile_open_unknown.py`: 4 of 8 failed before the fix (`'UNKNOWN' == 'OPEN'`, a
    missing `venue_order_id`); all pass after it. The other 4 pin what must not change (fills that differ, an
    unanswered broker, an unanswered cancel, a live binding).
  - `tests/unit/test_position_action_contract.py` failed at import (`ImportError: DayPlanPosition`) before the
    contract; `tests/integration/test_migration_0044.py` failed at import before the schema module;
    `tests/integration/test_day_plan_actions.py` and `test_wake_position_actions.py` were written before
    `day_plan_actions.py` and the engine wiring (import error, then 7 of 7 engine tests failed before the wiring).
    Their first runs showed test mistakes only (a wrapper attribute, the order two exits are created in, a helper's
    default take-profit, a wrong error code name).
  - A self-review found one real gap, shown by a new test first
    (`test_an_unfinished_close_whose_sell_was_never_sent_never_sells_once_it_is_abandoned`: the persisted sell stayed
    `SUBMISSION_PENDING` and the next dispatch would have sent it); fixed in `1d5932a`.
  - `tests/property/test_day_plan_action_properties.py` (Hypothesis, derandomized, 70 generated examples plus 10
    explicit sequences, one per fault path): random quotes with whole and partial fills, closes, moves with valid and
    refused terms, broker faults (an unanswered cancel, a refused market sell, a refused or unacknowledged new stop),
    crashes in the middle of an action and the retry, pauses, restarts and new plans. A count of outcomes showed every
    path is reached (closes and moves done, refused by terms, pauses and the broker, failed by an unanswered cancel,
    superseded after a crash).
  - Mutation checks (each run against `test_day_plan_actions.py` and the property test; each mutation made at least
    one test fail): no restore of cancelled exits; the close's sell not counted by the protection arithmetic; a broker
    refusal of the sell ignored; no resume from a persisted sell (re-run the sequence); an unanswered cancel treated as
    settled; re-protection with the entry's own terms instead of the terms in force.
- `tests/integration/test_day_plan_recovery.py::test_a_crash_while_sending_is_reconciled_and_never_resent`: its
  `UNKNOWN` assertion after the startup reconciliation encoded the behaviour D248 changes (W4-A's finding); it now
  asserts `OPEN` with the venue order id, and still that nothing is resent.
- `uv run ruff check src tests`: __RUFF__
- New tests: __NEW__
- Related tests: __RELATED__
- `tests/unit`: __UNIT__
- `python3 scripts/check_plan.py`: plan valid, 46 tasks, acyclic; `python3 scripts/test_planning.py`: 11 tests OK;
  `python3 scripts/check_repository_hygiene.py` (and `--staged` before every commit): passed.
- Full suite: not run (night shift: the wave integration runs it).
- Real or synthetic: everything is synthetic and labelled (a USD10,000 paper account, synthetic money limits, the
  example practice strategy with one synthetic confirmation report, scripted quotes, scripted and fault-injecting
  paper brokers, scripted departments). No real order, key, paid call or exchange call; a scripted broker is not an
  exchange test.

## Schema and dependencies

- State migration: **0044** (W8-B's reservation), registered in `migrate.py` after 0042; the Alembic revision `0044`
  has `down_revision = "0042"` (the head on the base) and is the single head; `test_migration_0044.py` checks both
  against the previous registered version. **W8-A (0043) and W8-C (0045) are built in parallel: the integrator
  re-links `down_revision` in numeric order** (with 0043 merged, 0044's becomes `"0043"`; the test then expects it).
  Candle database: no change.
- Dependencies added: none (`pyproject.toml` and `uv.lock` unchanged).

## Not done, open or for someone else

- **Owner question (live pilot, T19):** should the D248 settlement also apply to live bindings, that is, should a
  live order whose acknowledgement was lost move from `UNKNOWN` to `OPEN` once Kraken shows it open with every fill
  recorded? Recommended default for then: yes, but only through the pilot lifecycle, which records the venue order
  identity and keeps the owner's review of unknown live effects; until the owner says so, live stays as it is.
- **Selling part of a position:** a close sells the whole day-plan position of the coin. A partial close (sell part,
  re-protect the rest) is later work.
- **HQ (W8-C or later):** the actions and their steps are not shown in HQ yet (`day_plan_actions`,
  `day_plan_action_steps`, `day_plan_action_orders`; `WakeOutcomeRecord.actions` in the wake log's outcome JSON).
  Entries a close cancels are in the action's steps, not in `day_plan_events`.
- **Observed, not changed:** a coin the owner removed from a listed mandate can no longer be closed by the Trader
  (the existing exit rule in `AuthorityRecord.require_for_decision` refuses any decision outside the list, as it
  refuses a flatten exit); its safety exit still protects it. Worth a look with the owner's coin-list work.
- **Live (T19):** day plans and position actions refuse every binding but paper.
- **Integrator:** copy D245–D249 into `docs/DECISIONS.md`; re-link 0044 after W8-A's 0043 (and W8-C's 0045 after
  0044). Shared lines touched: `kernel/authority.py` (two lines in `PROTECTED_PATH_PREFIXES`, after the T34 lines),
  `adapters/persistence/migrate.py` (one import, one line), `application/execution.py` (`reconcile`, the new
  `_settles_lost_acknowledgement`, `dispatch(intent_ids=)`), `application/day_plan_protection.py`,
  `application/events/engine.py`, `application/trader_workflow.py`, `contracts/day_plans.py`, `contracts/wakes.py`,
  `docs/DAY-PLANS.md`, `docs/EVENT-ENGINE.md`, `docs/04-EXECUTION-AND-RECOVERY.md`. No HQ file, HQ fixture or
  `api/hq_contract.py` changed.
