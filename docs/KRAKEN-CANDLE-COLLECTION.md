# Kraken candle collection (T25, slice B)

Status: built and tested in source on 2026-10-13. Not deployed. Paper only, public data only, no key, no
paid service. Nothing here has been run against the live exchange by the tests; the only live requests
were 17 plain `curl` recordings (see "What Kraken returns").
Code: `adapters/market/rate_limit.py`, `adapters/market/kraken_candles.py`,
`application/candle_collection_plan.py`, `application/candle_collection_run.py`,
`application/collect_candles.py` (`collect_candles`), `market_data_cli/collect.py`.
Tests: `tests/unit/test_kraken_rate_limit.py`, `tests/unit/test_candle_collection_plan.py`,
`tests/integration/test_kraken_candles.py`, `test_collect_candles.py`, `test_collect_candles_cli.py`,
`test_candle_collection_isolation.py`. Fixtures: `tests/fixtures/kraken/` (README there).
Contract it writes to: [CANDLE-STORE.md](CANDLE-STORE.md). Pair list it reads: [PAIR-CATALOG.md](PAIR-CATALOG.md).

## In one minute

- One public request per coin and candle size: `GET /0/public/OHLC`. It returns the 720 newest finished
  candles, so one request is 12 hours of 1-minute, 2.5 days of 5-minute, 7.5 days of 15-minute or 30 days of
  60-minute candles.
- The newest, still-forming candle is thrown away. Everything kept was finished when it was received, and each
  candle carries the moment it was received (never the exchange's clock).
- Every request waits for the rate limiter first. The limiter assumes the smallest Kraken tier and stays well
  inside it.
- Each coin-and-size is saved on its own. One bad answer never loses the others. A run that stops early says
  exactly which coin-and-size pairs it did not reach.
- Running again is safe: it asks only from the newest stored candle onward, repeated candles change nothing, and
  the original receipt times stay.
- If the collector is not run for longer than the window above, the candles in between cannot be fetched from
  this endpoint any more. The report calls this a **gap** and says the Trades backfill or the archive import
  (T26) must fill it.

## How to run

All commands print one JSON report. Paths default to `runtime/market_data.sqlite` (private, Git-ignored).

```
trade-graph collect-kraken-candles --smoke
trade-graph collect-kraken-candles --catalog-only
trade-graph collect-kraken-candles
trade-graph collect-kraken-candles --intervals 5,15,60 --pairs SOL/USD,LINK/USD --max-requests 100
trade-graph collect-kraken-candles --intervals 1 --one-minute-pairs BTC/USD,ETH/USD
trade-graph candle-coverage
trade-graph candle-coverage --symbols BTC/USD --intervals 15 --max-ranges 50
```

| Option | Meaning |
|---|---|
| `--market-database` | the candle file (default `runtime/market_data.sqlite`) |
| `--intervals` | comma list from 1, 5, 15, 60 (default all four) |
| `--pairs` | restrict the 5, 15 and 60 minute candles to these `BASE/USD` symbols (default every eligible pair) |
| `--one-minute-pairs` | the only coins collected at 1 minute (default `BTC/USD,ETH/USD`; an empty value collects none) |
| `--max-requests` | most requests of the whole run, the pair-list refresh included (default 2000, 2 to 10001) |
| `--smoke` | BTC/USD and ETH/USD at 15 minutes, at most 4 requests (pair list 1, candles 2, one repeat after a short throttle), labelled `live_public_smoke`; cannot be combined with the scope or limit options |
| `--catalog-only` | refresh the pair list and collect no candles |

Every real run first refreshes the pair list (one request, `GET /0/public/AssetPairs`, slice A) so that a new
listing or a status change is seen, then collects. If that refresh fails but an older list is stored, the
candles are still collected from the older list and the run is reported `partial`.

Exit codes: `0` only when the printed `status` is `ok`; `1` for `partial`, `failed` or a refused database;
`2` for a bad option (nothing is opened or created); `130` for an interrupted run (the run stays in the
database as interrupted and the finished series are kept).

`status` is `ok` (everything planned was done, no failure, no stop, no unknown name), `partial` (something was
saved but something failed, was not reached or was not recognised) or `failed` (nothing was saved). Gaps do
not change the status: they are facts about the data, reported under `continuity`.

`candle-coverage` reads only. It lists every stored series of one source (default `kraken_public_ohlc`):
candle count, first candle, last close, expected and present slots, the longest unbroken run and the missing
ranges (the first 20 per series, with the total and a truncated flag). It counts every stored candle whatever
its receipt time (an inventory, not a point-in-time read), creates nothing and says `market_database_not_found`
if the file does not exist.

Both commands refuse the financial database before reading or writing anything: a file called
`trade_graph.sqlite`, the default `runtime/trade_graph.sqlite` (also through a link), or any file that holds
the financial tables is refused with `status: refused`.

## What is collected

- Pairs: the USD-quoted pairs of the newest stored catalog that are **eligible**: status `online` and no
  stablecoin, yield-token or fiat mark (D131, D127). The others are listed in `skipped_pairs` with the reason
  (`status:cancel_only`, `excluded:stablecoin`, ...) and are never requested. A name given on the command
  line that is not in the catalog is listed in `unknown_symbols` and makes the run `partial`.
- Intervals: 5, 15 and 60 minutes for every eligible pair; 1 minute only for the explicit list, because a
  quarter of one-minute candles is about 131,000 rows per pair.
- Coins that nobody has looked at (`not_individually_reviewed`) are collected like the rest. The price-peg
  check that would catch an unknown stablecoin belongs to the scanner (T31).

### Order

A response reaches back 720 candles, so unfetched data is lost soonest at the smallest interval. Requests run
interval by interval: 1, then 5, then 15, then 60 minutes. Inside an interval the series whose newest stored
candle is oldest go first (closest to losing data), then series never collected, by symbol. If `--max-requests`
or an error stops the run, the most perishable data has been collected.

### Incremental requests

A series with stored candles asks `since = newest stored open time - one interval`. Kraken's `since` is
inclusive, so this fetches the last two committed candles again; identical repeats are free and a changed one
becomes a new revision (reported as `revised`). A new series asks without `since` and gets the full 720.

## Rates and stopping

Source: https://docs.kraken.com/api/docs/guides/spot-rest-ratelimits (read 2026-10-13). A counter rises by one
for each call, public calls included. The Starter tier maximum is 15 and the counter falls by 0.33 per second.
Past the limit Kraken answers `EAPI:Rate limit exceeded` and restricts calls for a few seconds, longer if calls
continue. `EService: Throttled: <unix time>` signals too many concurrent requests and carries the time to
retry. The page describes one counter per API key; these requests carry no key, so the limiter assumes one
counter for the whole address, shared with every other public request from it. That is the cautious reading.

`KrakenRestLimiter` (`rate_limit.py`) mirrors that counter with an injected clock and sleeper:

| Setting | Value | Why |
|---|---|---|
| Ceiling | 10 | 5 calls of headroom below Kraken's 15, for other users of the same address (the trading service's own polling) |
| Drain | 0.30 per second | about 10% slower than Kraken's 0.33, so the mirror runs ahead of the real counter |
| Result | a burst of 10, then one call per 3.33 seconds | |

Full-scope timing (591 eligible USD pairs seen in the 2026-10-13 catalog, not re-counted since): 1,775
requests, about 98 minutes at the limiter rate; one interval for all pairs, 591 requests, about 32 minutes.

What happens on an error (no retry loops anywhere):

| Event | Behaviour |
|---|---|
| `EAPI:Rate limit exceeded`, or HTTP 429 | the run **stops**: `stop_reason: rate_limited`, that series is `failed`, the rest are listed as not reached |
| `EService: Throttled: <time>` with the retry time at most **30 seconds** ahead of the host clock | waited out **once per run**: the run sleeps until the stated time plus a 2 second margin, then repeats that one request (it counts against `--max-requests`) |
| a second throttle, a retry time more than 30 seconds ahead, an unreadable one, or no request left in the limit | the run **stops**: `stop_reason: throttled` with the stated time |
| 10 failed series in a row | the run **stops**: `stop_reason: consecutive_failures`. A dead network would otherwise cost thousands of timeouts. Any success resets the count |
| `--max-requests` reached | the run **stops**: `stop_reason: max_requests` |
| one series fails (bad answer, unknown pair, storage error) | that series is `failed` with a fixed category; the others continue |

Failure categories are fixed words (`request_failed`, `rate_limited`, `throttled`, `unknown_pair`,
`service_unavailable`, `exchange_error`, `response_invalid`, `pair_mismatch`, `candle_not_closed`,
`clock_invalid`, `persistence_failed`). Response text and exception text never enter a report or a run record.

## What Kraken returns (recorded 2026-10-13)

Source of the endpoint description: https://docs.kraken.com/api/docs/rest-api/get-ohlc-data (read 2026-10-13).
17 plain `curl` GET requests were recorded between 15:17 and 15:24 UTC (BTC, SOL, LINK, a thin coin
USDUC, PAXG and a cancel-only pair, at 1, 5, 15 and 60 minutes; manifest in `tests/fixtures/kraken/`).

1. **A full answer is 721 rows**: the 720 newest committed candles and, last, the one still forming. This
   matches the documentation ("up to 720 of the most recent entries", final item is the not-yet-committed
   timeframe, always present). The fetcher accepts at most 721 rows.
2. **`since` is inclusive.** `since=T` returns the candle opening at T if it is committed
   (`ohlc_XBTUSD_15_since*.json`). `since=1` returns the same 721 rows as no `since`.
3. **`last` is the open time of the last committed candle** (the documentation: the id to use as `since`).
4. **Intervals without trades are filled, not omitted.** The documentation does not say; the recordings do.
   Kraken returns a flat row for the quiet interval: open = high = low = close = the previous close, vwap 0,
   volume 0, trade count 0. Counts in the recordings (of 720 committed candles): USDUC 1-minute 678 quiet,
   PAXG 1-minute 365, LINK 1-minute 76, USDUC 60-minute 71; BTC and SOL none. The longest quiet run was 178
   minutes (USDUC). No interval was ever missing from the sequence: every answer was an unbroken sequence at
   the interval spacing. The collector stores such a row as sent; `trade_count = 0` marks it, and reports count
   them as `no_trade_candles`. It does not drop them (that would make "no trades" look like "not collected")
   and does not invent them (it only ever stores what a response contained).
   Correction to [CANDLE-STORE.md](CANDLE-STORE.md) rule 5, which assumes Kraken returns no row for such an
   interval: for this endpoint it does return one. The store and the `Candle` model already accept it (vwap 0
   with volume 0). A slot is therefore missing in the store only when it was not collected or is outside the
   window, not because it had no trades.
5. **A pair with no data in the window returns one row**: time 0, zero prices, count 0 (`ACAUSD`, a cancel-only
   pair). That is the forming row; it is dropped and the series reports `no_data`.
6. **The exchange clock ran 153 seconds behind the host clock** on every recorded response (the `Date` header
   against the host and against Windows time, which agreed with each other). I could not tell which was right.
   The collector uses the host clock for receipt times and refuses a response in which any row would close after
   that receipt time. A host clock that is *ahead* is safe (receipts are later than true). A host clock
   *behind* the exchange by more than a few seconds would make that refusal fire right after each interval
   boundary: the series fails as `candle_not_closed` and succeeds on the next run; nothing wrong is stored.
   Keep the host clock synchronised.
7. The OHLC interval list in the documentation is 1, 5, 15, 30, 60, 240, 1440, 10080 and 21600. The store also
   accepts 720 (the archive has it); the collector gathers only 1, 5, 15 and 60.

## Rules for each candle

The fetcher (`kraken_candles.py`) validates before anything is stored; one invalid row refuses the whole
response (`response_invalid`) and nothing of that series is saved:

- the final row is dropped, never inspected for values, and only its shape (8 items) is checked;
- each committed row has 8 items, an integer open time on the interval grid, strictly increasing, and an integer
  trade count of zero or more; a hole between rows is allowed (it simply stays a missing slot);
- prices and volume are exact decimals (text or JSON numbers, never floats), positive prices, non-negative
  volume, high and low enclosing open and close, vwap inside the range, or 0 with zero volume;
- a row with trades counted 0 but volume above 0 is refused;
- the receipt time is read from the clock after the network call returns, must not be earlier than the time
  before the call, and no row may close after it;
- the result key must be the pair's own name (code, wire name, or base plus quote), so a response for another
  pair is refused (`pair_mismatch`);
- at most 1 MiB of response text, 721 rows, no duplicate JSON fields, no NaN or infinity.

Stored candles follow [CANDLE-STORE.md](CANDLE-STORE.md): immutable revisions, source `kraken_public_ohlc`
(`synthetic` for runs labelled `synthetic_fixture`, so scripted data never joins the real series), receipt time
as availability. Hourly BTC and ETH history in `hourly_candles` is not touched; the two are separate stores
(test: byte-for-byte unchanged file, rows and feature snapshots; the run opens no other SQLite file).

## Gaps

Per series the report has two views.

**`continuity`** compares the first returned candle with what was stored before the request:

| Status | Meaning |
|---|---|
| `first_collection` | nothing was stored; the series now starts at the window start (older history comes from T26) |
| `connected` | the first returned candle is at or before the newest stored one: no candle was lost |
| `gap` | the first returned candle is later than the newest stored one. `missing_slots` candles between them can no longer be fetched from this endpoint. `action` says: use the Trades backfill or the archive import (T26) |
| `no_data` | nothing stored and nothing returned (the pair has no data in the window) |
| `unverified` | something was stored but this response held no committed candle, so connection could not be checked |

**`coverage`** is the candle store's own `coverage` for the window this response covers (from the newest stored
candle before the request to the last returned one): expected, present and missing slots and the missing ranges.
It also shows a hole that Kraken left inside one response.

Because Kraken fills quiet intervals, a gap is a real collection gap, not a quiet market.

## Cadence

The longest pause between runs that leaves no new gap equals the window. A full pass also takes time (see
rates), so run more often than the window.

| Interval | Window | Longest pause without a gap | Suggested |
|---|---|---|---|
| 1 minute | 12 hours | 12 hours | every 6 hours |
| 5 minutes | 60 hours | 60 hours | every 30 hours, in practice daily |
| 15 minutes | 7.5 days | 180 hours | every 3 days |
| 60 minutes | 30 days | 720 hours | weekly |

A practical plan that respects all four: the short `--intervals 1` run for BTC and ETH every 6 hours, and the
full run once a day. The report prints these numbers for the run's own scope under `cadence`
(`requests_per_pass` and `minutes_per_pass_at_the_limiter_rate` included). This module schedules nothing; the
owner or the integrator decides how it is run.

## Storage

Measured cost is 118 bytes per candle revision (docs/CANDLE-STORE.md). The report's `storage` section gives, per
run, rows inserted times 118, the rows and bytes now in the store for that source, and a one-quarter projection
(91 days) per interval for the planned scope:

| Per pair, per quarter | Rows |
|---|---|
| 1 minute | 131,040 |
| 5 minutes | 26,208 |
| 15 minutes | 8,736 |
| 60 minutes | 2,184 |

For the full default scope on 591 eligible pairs (and 1-minute for two): about 22.2 million rows, about 2.6 GB,
per quarter; about 244,000 rows (29 MB) a day once caught up; the first run adds about 1.28 million rows
(151 MB). The 5-minute candles are 70% of that. Narrow `--pairs` or `--intervals` to limit growth; the store
never deletes.

## Restart safety

- The run is stored with its scope, limit and catalog before the first request. A process that dies leaves a
  run with no result, shown as `interrupted`.
- Each series is saved in one transaction after its response is validated. A crash loses at most the series in
  flight; finished series stay.
- A second run asks from the newest stored candle, so it picks up where the first stopped. Repeating a response
  inserts nothing and keeps the first receipt time. Changed content for a stored slot becomes the next revision
  with the later receipt time.

## Evidence

- Default tests make no network calls (a guard fails any socket use).
- `recorded_public_fixture`: the 17 recorded responses (trimmed), run through the whole collector.
- `synthetic_fixture`: table-built responses from a scripted exchange that behaves like the recordings.
- `live_public_smoke`: `--smoke` against the real exchange. **Not run by this work.** `live_public` is a normal
  real run, also not run. A recording is evidence of the format on 2026-10-13, not of a collector run.

## Known limits

- One invalid row refuses its whole response. Because incremental requests start at the newest *stored* candle,
  the same row is in the next response too, so that series stays failed until the row is outside the window
  (12 hours at 1 minute, up to 30 days at 60 minutes). The report names the category; the Trades backfill could
  fill the span.
- Delisted pairs have no history here; a back-test over today's pairs is optimistic.
- The eligible list is Kraken's international one; whether the owner's EU account may trade a pair is not checked.
- Pair statuses other than `online` are not collected until `online` again; their stored candles stay.
