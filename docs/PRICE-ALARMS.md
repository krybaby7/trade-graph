# Price alarms (T33)

Status: built and tested in source on 2026-10-16 by cloud package W1-D (branch
`claude/determined-dirac-i1eyfa`, pull request into `claude/day-trader-engine-20261012`). Not deployed, not
wired into the running service yet: the wake dispatcher that consumes fired alarms is package W5-A (T35), and
the in-service price collector is W6-D. Decisions D155–D159 (reserved block for W1-D; the integrator copies
them into [DECISIONS.md](DECISIONS.md)).

In plain words: a department, usually the Trader, can leave an alarm on a price ("tell me if BTC goes above
65,000") or on an indicator from the [menu](INDICATOR-MENU.md) ("tell me if the 15-minute RSI drops below
30"). Software, not a model, checks every price update and every finished candle, so watching costs no AI
tokens. An alarm goes off once and then waits until its owner switches it on again. If a price feed stops, the
software says so to the departments whose alarms depend on it, instead of treating silence as a calm market.
Alarms that go off while a department is already awake are handed over together at its next wake. There is no
limit on how many alarms exist or how often they go off (D116).

## Code and data

| Part | Path |
|---|---|
| Contract (conditions, requests, records, queue items) | `src/trade_graph/contracts/alarms.py` |
| Alarm book (create, re-arm, cancel; fire, cursors, feed events) | `src/trade_graph/application/alarms/book.py` |
| Delivery queue (what the wake dispatcher claims) | `src/trade_graph/application/alarms/queue.py` |
| Price watcher (quotes, completed candles, indicators, feed health) | `src/trade_graph/application/alarms/watcher.py` |
| State migration 0029 (six tables, append-only triggers) | `src/trade_graph/adapters/persistence/price_alarm_schema.py`, `migrations/versions/0029_price_alarms.py` |
| Tests | `tests/unit/test_alarm_contract.py`, `tests/integration/test_migration_0029.py`, `test_price_alarm_book.py`, `test_alarm_queue.py`, `test_price_alarm_watcher.py`, `tests/property/test_price_alarm_properties.py` (helpers in `tests/alarm_support.py`) |

## What an alarm watches

Every alarm has one owning department (`trader`, `research`, `leader`, `learning` or `optimisation`), one
condition, and optional references: `portfolio_id`, `day_plan_ref` (the T34 day plan it serves), `task_id`, a
`request_id` that makes a retried tool call return the same alarm, and a short `note` (at most 280 characters,
the concise reason, not a transcript).

| Kind | Watches | Price or value | Evaluated on |
|---|---|---|---|
| `quote_price` | a symbol's public price updates | `mid` ((bid + ask) / 2, the default), `bid`, `ask` or `last` | every quote or trade observation received after arming |
| `candle_price` | one candle series in the candle store | `close` (default), `high` or `low` | every completed candle that closed after arming |
| `indicator` | a menu indicator on one candle series | the named output (required when the entry has several) | every completed candle that closed after arming |

Relations are the indicator engine's four: `above` (newest > level), `below` (newest < level), `crosses_above`
(previous <= level < newest) and `crosses_below` (previous >= level > newest). A path that touches the level and
leaves it crosses once; one that only touches it does not cross.

Refused when the alarm is created, with a message:

- a level that is not a finite decimal, a binary float, or more than 64 digits; a price level of zero or less;
- an indicator that is not on the menu, a misspelled or out-of-bounds setting, a multi-output entry without the
  output named;
- **On-Balance Volume**: its level depends on where its history starts (D136), so a level alarm on it means
  nothing;
- a level outside the range an output can read (RSI above 100, Williams %R above 0): such an alarm could never
  mean anything;
- an interval the collector does not collect (only 1, 5, 15 and 60 minutes);
- any field that would cap alarms: the contract has none, and a test checks that.

Indicator settings are stored with the menu defaults filled in, so the record says exactly what is computed.

## One fire per arming

An alarm starts **armed** (generation 1). When its condition holds it **fires**: one transaction records the
fire, marks the alarm **spent** and queues the delivery. It then waits until its own department **re-arms** it
(generation 2, and so on). A department can **cancel** an armed or spent alarm for good. To change the level,
cancel the alarm and create a new one; a re-arm keeps the condition.

The store enforces this, so it holds whatever code writes: one `fired` row per alarm and generation (a unique
constraint), re-arming only after the previous arming fired, nothing after a cancellation, and no update or
delete of an alarm, its history, its deliveries or the feed events. A restart, a repeated tick or a second watcher
cannot fire an arming twice. Re-arming takes `after_generation`, so a retried re-arm returns the same arming
instead of failing. Only the owning department may re-arm or cancel.

These are alarm semantics, not caps (D116): thousands of alarms can be armed on one symbol, all of them can fire
on one price update, and one alarm can be re-armed and fire again as often as its department wants. Tests do
exactly that.

## How the watcher works

`PriceWatcher(book, store, clock, policy=WatchPolicy())` is driven by two calls:

- `observe_quote(observation)` (or `observe_quotes`) for every public price update the feed delivers;
- `tick()` after candles are stored and on a short timer (every few seconds is fine; a tick with nothing new
  does almost nothing).

It never calls a model, imports no model, budget or gateway code (a test reads its imports) and creates no
reservation or receipt.

**Completed candles only.** Candles are read only through `CandleStore.window`, in point-in-time mode as of the
clock: a candle is used only after it closed *and* was received. The still-forming candle is never stored (store
contract), and a live quote never stands in for a candle. Indicator values come from the T27 engine
(`compute_indicator`, then `compare_to_level` with its 12-significant-digit rule). An indicator that is not
ready (warming up or gapped) never fires; the reason is kept on the alarm.

**Every completed candle, in order.** Each candle alarm keeps a cursor (`evaluated_through`). A tick evaluates,
oldest first, every candle that closed after the cursor, or after arming for a new arming, so candles that
completed while the watcher was down are still seen and the first one that satisfies the alarm is the one that
fires. The watcher waits at a candle that has not arrived yet. It steps over a missing candle, recording "no
candle closing at ...", only once a later candle is present; a candle that arrives after a later one was
evaluated is not evaluated again (alarms watch the live edge; the practice range replays history). A crossing
compares two consecutive candles, so a crossing over a missing candle is not judged. One tick evaluates at most
5,000 candles per alarm (`CATCH_UP_SLOTS`); the rest wait for the next tick. That bounds the work of one call and
never skips a candle.

**Quotes.** Quote and trade observations are used; bars are left to the candle store. A missing price (a quote
without an ask, for a `mid` or `ask` alarm) is not guessed. A quote received before the alarm was armed, or in the
future, is not used (the second is refused as a programming error). A crossing alarm keeps the side of its level
it last saw; the side is saved whenever it changes, so a crossing that happens across a restart still fires.
The fire records the previous reading on the other side and the seconds since the watcher's previous quote of
that symbol (empty after a restart), so a crossing across a gap is visible as one. A quote older than the reading
an alarm already holds is ignored.

**Feed loss is reported, never a quiet market.** For each feed that an armed alarm depends on:

| Feed | Lost when | Default |
|---|---|---|
| quotes of a symbol | no update for `quote_stale_after` (the paper feed polls every 10 seconds) | 120 seconds |
| one candle series | no candle closing at or after the slot that should have arrived `candle_grace` ago | 10 minutes |

A loss is recorded once as a `price_feed_events` row and every department with armed alarms on that feed gets a
`feed_lost` item in its queue, listing the alarms that cannot see their prices. The return is recorded the same
way (`feed_restored`, also sent to every department told about the loss). Ticks while a feed stays lost add
nothing. After a restart a lost quote feed stays lost until an update actually arrives. A candle series that the
store refuses to read counts as lost, with the error in the notice. Every tick also reports `feeds` (ok or lost
per series) and `blind_alarms`. These thresholds are health rules, not limits on alarms.

Today's Windows collector runs every 6 hours, so with it any candle alarm would correctly report its feed as lost.
Candle alarms become useful once W6-D keeps the candle store current inside the service.

**One bad alarm does not blind the others.** If evaluating one alarm fails (for example the store or the
library refuses), its cursor stays put, the failure is written on the alarm and returned in the tick's `errors`,
and the other alarms are still evaluated. The same candle is tried again next tick.

## The delivery queue (contract for W5-A, T35)

Fired alarms and feed notices are delivered to a persisted per-department queue in the state database. The wake
dispatcher (W5-A) consumes it through `AlarmQueue(database, clock)`:

| Call | Meaning |
|---|---|
| `waiting()` | `{department: count}` of pending items. A pending item is a reason to wake a sleeping department. |
| `pending(department)` | that department's pending items, oldest first (read only) |
| `claim(department, wake_id)` | every pending item of the department becomes this wake's batch (`DeliveryBatch`), oldest first. The same `wake_id` returns the same batch, even if it was empty. Refused while the department has another open claim. |
| `open_claim(department)` | the department's claimed but not yet closed batch, for recovery after a crash |
| `acknowledge(wake_id)` | the department received the batch: its items are `delivered`. Repeating it changes nothing. |
| `release(wake_id)` | the wake ended before the department saw the batch: its items go back to `pending`, in their original order, and join the next wake. Repeating it changes nothing. |
| `batch(wake_id)` | read a batch |

Rules the dispatcher can rely on:

1. **Nothing is dropped, merged or rewritten.** Each fire adds exactly one item (unique per alarm and arming),
   each feed transition one item per department. Item content is immutable; status only moves
   `pending -> claimed -> delivered`, or `claimed -> pending` on release.
2. **Batching, not capping.** Items that arrive while a department is awake stay pending, because they were not
   pending when the wake claimed. They are delivered together at the department's next wake. If items are
   pending when a wake ends, the next wake may start at once; nothing here limits wakes.
3. **One open claim per department.** A dispatcher that crashed finds its unfinished claim with `open_claim`,
   then acknowledges it (the department did see it) or releases it (it did not). Save the `wake_id` before
   calling `claim`; claiming again with the same id is safe.
4. **A wake id is used once.** A released wake id cannot claim again; use a new one.
5. **Departments are separate.** A wake id belongs to the department that first claimed with it.

Item (`AlarmDelivery`): `delivery_id`, `sequence` (queue order), `department`, `kind` (`alarm_fired`,
`feed_lost` or `feed_restored`), `alarm_id` and `generation` (alarm fires only), `portfolio_id`, `created_at`
and `document`.

`document` of an `alarm_fired` item (plain JSON, decimals as strings, times as `...Z` UTC text):

```json
{
  "contract": "price-alarm-v1", "alarm_id": "…", "generation": 1, "department": "trader",
  "portfolio_id": "…", "day_plan_ref": "…", "note": "…", "condition": {"kind": "indicator", "…": "…"},
  "armed_at": "…", "fired_at": "…",
  "observed": {
    "basis": "quote | candle",
    "value": "65010", "previous_value": "64900 or null",
    "quote only": "observation_id, venue, source, kind, event_time_utc, available_at_utc, price, bid, ask, last, previous_at, seconds_since_previous_quote",
    "candle only": "kind, symbol, interval_minutes, source, close_time_utc",
    "candle_price only": "price, as_of_utc, candle {revision, run_id, content_hash, available_at_utc}",
    "indicator only": "level, relation, indicator {name, parameters, output, status, spec_version, library, mode, as_of_utc, required_candles, start_dependent, input_sha256}"
  }
}
```

`document` of a feed notice: `contract`, `feed_event_id`, `feed` (`quote` or `candle`), `symbol`,
`interval_minutes`, `source`, `series_key`, `status`, `detected_at`, `armed_alarm_ids`, and the evidence
(`last_update_at` and `stale_after_seconds` for quotes, with `watching_since` when no update was seen since the
watcher started, and `first_update_at` on a quote feed's return; `due_close_time_utc`, `checked_through_utc` and
`grace_seconds` for candles, plus `read_error` when the store refused).

What the dispatcher decides, not this package: whether a feed notice wakes a department, how a department that
was woken by alarms names its next check-in (D123), and the wake record itself. The alarm document is data for
the department, never an instruction; the `note` is the department's own earlier text.

## Wiring notes for W5-A and W6-D

- Create one `AlarmBook`, `AlarmQueue` and `PriceWatcher` per installation over the state database and the
  candle store. Call `observe_quotes(feed.poll())` in the feed thread and `tick()` after each candle save and on a
  short timer. Both are safe to call from different threads.
- Department tools (`set_alarm`, `rearm_alarm`, `cancel_alarm`, `list_alarms`) map directly to `AlarmBook.create`,
  `rearm`, `cancel` and `alarms`; pass the calling department and task, never a department chosen by the model.
- Two watcher processes would still never fire an arming twice (the store forbids it), but feed health uses each
  process's own last-quote time; run one watcher.

## Tables (migration 0029)

| Table | Holds | Changes |
|---|---|---|
| `price_alarms` | one row per alarm: owner, condition JSON, references, contract version | never updated or deleted |
| `price_alarm_events` | `armed`, `fired`, `cancelled` per generation | append-only; one fire per generation; order enforced |
| `price_alarm_watch` | current status, candle cursor, quote baseline, last reason | updated; status must match the events; never deleted |
| `alarm_deliveries` | the queue | content immutable; status moves as above; never deleted |
| `alarm_wake_claims` | one row per claiming wake | closes once; one open claim per department; never deleted |
| `price_feed_events` | lost and restored transitions per feed | append-only; must alternate, starting with lost |

None of them is financial state: they are not in the ledger, the financial checkpoint or the review export.
Version 0029 is W1-D's reservation; 0026–0028 belong to other wave-1 packages, so the Alembic revision's
`down_revision` is re-linked by the integrator.
