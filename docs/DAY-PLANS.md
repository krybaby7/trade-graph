# Trader day plans (T34)

Status: built and tested in source on 2026-10-17 by cloud package W4-A (decisions D185–D189, state migration
0034). Not deployed. Paper only. Day-plan orders stay switched off until the owner records money limits at step L3
(`trade-graph money-limits --confirm`, [MONEY-LIMITS.md](MONEY-LIMITS.md)). The Trader's own turn that writes plans
is wired by the event engine (W5-A, T35). The rules behind it: D115 (the daily cycle), D117 (resting entries, a
protective exit on every open position, a pause cancels entries and keeps exits), D119 and D138 item 5 (a
practice-range report must exist, with no pass mark), D127 (spot, long only, no leverage), D138 items 1, 2 and 6
(money limits and the trading day) and D116 (no caps on activity).

In plain words: for each coin it is watching today, the Trader writes a short plan: which tested strategy it is
following, which indicators it is watching, which price alarms should wake it, and one or more buy orders that wait
below the current price. Every waiting buy names its safety exit: a stop-loss below it and, if the Trader wants, a
profit target above it. Software checks the plan against your rules (money limits, the coin list, no riba, a
practice-range report for the strategy) and the exchange's rules (price steps and lot sizes), but it never judges
the Trader's idea: there is no confidence score, no committee and no approval by the Leader. Then it saves
everything before sending anything. When a buy fills, even partly, software puts the safety exit on the coins just
bought at once, so a position is never left without one while the Trader sleeps. A pause cancels the waiting buys
and keeps the safety exits.

| Part | Path |
|---|---|
| The plan contract | `src/trade_graph/contracts/day_plans.py` |
| Validation, installation, replacement, the management step and plan status | `src/trade_graph/application/day_plans.py` |
| Protective exits for filled entries (called with every fill) | `src/trade_graph/application/day_plan_protection.py` |
| Take-profit legs on paper sell stops | `take_profit_price` on `AuthorizedOrderIntent` and `native_take_profit` on `BrokerCapabilities` (`contracts/models.py`); `adapters/brokers/paper.py` |
| Hooks in execution | `Execution.record_fill`, `place_protection(take_profit_price=, protective=)`, `dispatch(protection_only=)`, `cancel_intents` (`application/execution.py`); `pause_allows_protection` (`kernel/authority.py`) |
| Service wiring | `PaperService._management` and `_ingest` (`application/paper_service.py`) |
| State migration 0034 | `src/trade_graph/adapters/persistence/day_plan_schema.py`, `migrations/versions/0034_day_plans.py` |
| Tests | `tests/unit/test_day_plan_contract.py`, `tests/integration/test_day_plans.py`, `test_day_plan_recovery.py`, `test_day_plan_service.py`, `test_migration_0034.py`, `tests/property/test_day_plan_protection_properties.py`; helpers in `tests/day_plan_support.py` |

The validation, installation and protection modules and the contract are protected paths (`kernel/authority.py`):
they are execution controls, which agents cannot edit.

## The plan

```json
{
  "symbol": "SOL/USD",
  "strategy_ref": "slow-trend-pullback@1",
  "indicators": [{"indicator": "RSI", "interval_minutes": 15, "parameters": [{"name": "timeperiod", "value": 14}]}],
  "alarms": [{"kind": "quote_price", "symbol": "SOL/USD", "relation": "crosses_below", "level": "95",
              "note": "the pullback is deeper than planned"}],
  "entries": [{"quantity": "5", "limit_price": "99.5", "stop_price": "97", "take_profit_price": "104",
               "expires_at_utc": null}],
  "rationale": "SOL pulled back to its slower average inside a rising trend; rest a bid under the market.",
  "invalidation": "A quote below 95."
}
```

- **Coin and strategy.** `symbol` is a plain spot pair; `strategy_ref` is a registered practice-range strategy
  version (`strategy_id@version`, [PRACTICE-RANGE.md](PRACTICE-RANGE.md)).
- **Indicators.** Menu indicators ([INDICATOR-MENU.md](INDICATOR-MENU.md)) on a collected interval (1, 5, 15 or
  60 minutes) with menu-bounded settings, given as name and value pairs. They record what the plan watches; the
  menu defaults are filled in when read (`resolved_parameters`).
- **Alarms.** A flat form of the alarm contract ([PRICE-ALARMS.md](PRICE-ALARMS.md)): `quote_price`,
  `candle_price` or `indicator`, a relation and an exact level. Installed alarms belong to the Trader and carry
  the plan's id as `day_plan_ref`.
- **Entries.** Resting limit buys: an exact quantity and limit price, a stop below the limit and an optional
  take-profit above it, and an optional expiry (default: the end of the trading day).
- **Rationale and invalidation.** A concise decision summary (at most 1,000 and 500 characters), not a transcript.
- A plan rests at least one entry or sets at least one alarm. A plan with alarms and no entries is allowed: the
  Trader is never made to trade. To do nothing, the Trader records `no_action` as before.
- Prices and quantities are exact decimals as text; a binary float is refused. There is no field for a
  confidence figure, a score, an approval or a vote, and an unknown field is refused.
- Size bounds of one document (16 entries, 16 alarms, 8 indicators) are input limits like a request envelope, not
  limits on activity: the Trader may write and revise plans as often as it wants (D116).
- The shapes are plain named objects, so the same contract can be a provider's strict structured reply when the
  event engine asks the Trader for a plan (a test runs it through both provider wire schemas).

## What software checks, and what it does not

Every check is deterministic; a refused plan writes nothing but a `day_plan_refused` activity event the Trader and
HQ can read (reason, error code, request, coin, strategy and the document's hash).

| Check | Refusal |
|---|---|
| The paper account (venue, account and mode `paper`) and a paper portfolio | `AuthorityDenied` |
| The owner has recorded money limits (W3-A's gate, D138) | `AuthorityDenied` "day-plan orders stay off until the owner records money limits" |
| The portfolio is not paused | `AuthorityDenied` "pause X refuses new day plans" |
| Spot only (D127) | `AuthorityDenied` |
| The strategy version is registered and has a practice-range **confirmation** report recorded before now (D138 item 5) | `ValidationFailure` |
| Prices on the instrument's price increment, quantities on its lot, at or above its minimums | `ValidationFailure` (exact: nothing is rounded) |
| Every entry rests: its limit is below the current ask | `ValidationFailure` "rests below the market"; no quote is `StaleState` |
| Every entry expires in the future and no later than the end of the trading day | `ValidationFailure` |
| Each entry through `Execution.authorize(..., day_plan=True)`: the coin list or coin rule, the mandate, the daily loss limit, fresh quotes, the balance, and the per-coin and total shares counting every resting entry as if filled (D181) | whatever `authorize` raises; the whole plan is refused, never half installed |

Software does not check whether the idea is good. The practice-range report is evidence the Trader reads; it has
no pass mark. Nobody approves a plan.

## Installing

`await DayPlans(execution).install(portfolio_id, plan, request_id=..., task_id=..., snapshot_id=...)`:

1. If this `request_id` already installed a plan for the portfolio, that plan is returned (`created` false) and
   nothing is written or sent. The same request with a different document is refused (`DuplicateRecord`).
2. The checks above run without writing.
3. If the coin has an earlier plan, its outstanding entries are cancelled and reconciled first
   (`Execution.cancel_intents`). A cancellation that got no answer is asked again.
4. In one transaction: the checks again; a refusal if any entry of the earlier plan is still unsettled (status
   unknown or cancel pending: it is reconciled before the plan is replaced); the plan row; a `superseded` event on
   the earlier plan; every entry's authorized intent with its reservation and outbox row; the alarms; the earlier
   plan's alarms cancelled; an `installed` event.
5. Nothing has been sent yet. The service's next dispatch sends the entries; the paper broker rests them.

A crash at any point leaves either nothing or the whole plan; a retry with the same `request_id` never rests an
entry twice, and the outbox sends each entry once (an entry whose submission was interrupted is `UNKNOWN`,
reconciled by its client id, and never sent again). Each coin has one chain of plans, enforced in SQL. If the
earlier plan's entries were cancelled and the new plan is then refused (for example by the money limits), the
earlier entries stay cancelled: the Trader asked to replace them, and the refusal is visible.

Call `install` under the service's execution lock (the Trader's turn, T35), like other execution writes.

## Protective exits

- **On every fill.** `Execution.record_fill` calls `DayPlanProtection.after_fill` inside the transaction that
  records the fill. A fill of a day-plan entry gets a protective exit for the part that is filled and not yet
  covered: a paper sell stop at the entry's stop, with the entry's take-profit as a second leg of the same order
  (one cancels the other). The exit's intent, reservation and outbox row are persisted with the fill, so the
  records never show a filled part without its exit on the way. Partial fills each get their own exit.
- **Sent at once.** The paper service sends protective exits right after the quote that filled the entry
  (`dispatch(protection_only=True)` in `_ingest`), so the next quote of the same poll can already trigger the
  stop; the management step sends them too.
- **Coverage is arithmetic over records.** An entry still needs protecting by what it filled less what its exits
  cover: an outstanding exit, or one whose status is unknown, covers its whole quantity (an unknown status never
  leads to a second exit); a filled exit covers what it sold; an exit that ended unfilled covers only what it sold.
  An exit never asks to sell more than is held and not already reserved, so spot stays spot (the reservations
  alone already stop a second exit for the same coins).
- **Retried.** A placement that cannot be made (no quote yet, a broker refusal) does not lose the fill: a
  `protection_deferred` event is recorded once per reason, and the management step's sweep places the exit later.
- **Pauses.** A protective exit may be placed and sent under `RUNNING`, `PAUSE_DECISIONS`, `NO_NEW_EXPOSURE` and
  `MANAGE_ONLY` (`pause_allows_protection`). Under the owner's `CANCEL_ALL` every order, exits included, is
  cancelled by the owner's explicit choice, and nothing is re-attached until the pause ends; the sweep then
  protects the position again. `FLATTEN` sells everything itself; a `STOPPED` portfolio is flat.
- **Exits count.** The take-profit fills from its level less the 0.10% exit slippage when the bid trades above it;
  the stop fills from the bid less slippage when the bid reaches it ([FILL-ENGINE.md](FILL-ENGINE.md)).

## The management step

`await DayPlans(execution).manage(portfolio_id)`, run by `PaperService._management` on every tick after the pause
and money-limit steps; a failure is reported as `day_plans:<error>` and never holds back dispatch:

- under any pause but `RUNNING`, every outstanding day-plan entry is cancelled (reason `pause`);
- an entry still resting at its expiry is cancelled (reason `expired`); a filled part keeps its exit;
- every filled, uncovered part of an entry of a held coin is protected (the sweep);
- protective exits are sent.

The daily loss stop (W3-A) cancels resting entries itself and keeps exits. Cancelled entries are not restored when
a pause ends or a new day starts: the Trader plans again.

`DayPlans.status(plan_id)` gives each entry's order state, filled quantity, exits (state, quantity, sold) and what
is still unprotected, with the plan's events; `active(portfolio_id)` lists the latest plan of each coin whose
trading day has not ended.

## Tables (migration 0034)

| Table | Holds | Changes |
|---|---|---|
| `day_plans` | the plan document and hash, coin, trading day, strategy version and the confirmation reports relied on, policy and mandate revisions, the plan it replaced | append-only; one per `(portfolio, request)`; one chain per coin; replaced once |
| `day_plan_entries` | each resting entry and the authorized intent it rests as | append-only; the intent must be a limit buy of the plan's coin |
| `day_plan_exits` | each protective exit and its intent; `fill` or `sweep`, and the fill that prompted it | append-only; the intent must be a protective sell stop of the entry's coin |
| `day_plan_events` | `installed`, `superseded`, `entry_cancelled` (`pause`, `expired`, `replaced`), `protection_deferred` | append-only; installed and superseded once per plan, a cancellation once per entry |

They are execution controls and evidence: the intents, fills and ledger stay the books. They are not added to agent
context, the financial checkpoint or the review export. HQ shows them in the Money tab's "Today's day plans" panel
(`GET /api/v1/hq/plans`, W8-C, D251).

## For the next packages

- **W5-A (T35, event engine).** The Trader's day-start turn writes one `DayPlan` per focus coin (the contract fits
  a strict provider reply) and installs it with `DayPlans.install`, passing its `task_id` and a `request_id`
  derived from the task and coin, under the service's execution lock. Wake reasons: a fill whose intent is in
  `day_plan_entries` (an entry filled), a fill whose intent is in `day_plan_exits` (a protective exit triggered;
  `fill_terms` in the broker order says whether stop or take-profit), and the plan's alarms (their
  `day_plan_ref`). `DayPlans.status` is the plan's state for the Trader's context.
- **W5-B (T41, HQ charts).** Entry limits, stops and targets are in `day_plan_entries`; live exits in
  `day_plan_exits` with their intents' states.
- **W6-C (T39, HQ wake log).** Plans, refusals (`day_plan_refused` activity events) and plan events.
- **Live (T19).** Day plans refuse every binding but paper until a live venue has tested native protective orders.

## Known limits

- One exit per filled part: many partial fills make many small exits. On paper that is exact; a real venue would
  refuse an exit below its minimum order value, so live use needs exits merged or resized first (T19).
- The Trader has no tool yet to close a day-plan position by hand: its exit reserves the coins, so a plain sell
  of them is refused as insufficient balance until the exit is cancelled. Closing or moving an exit is later
  work (T35's Trader tools); until then the plan's exit or a pause manages the position.
- An entry whose cancellation got no answer and which the broker cannot find stays `CANCEL_PENDING` (unknown is not
  cancelled), and the coin's next plan is refused until reconciliation settles it; that needs the owner to look.
- Entries placed on the older single-order Trader path (`TraderReply`) carry no protective exit; the event engine
  replaces that path with day plans.
