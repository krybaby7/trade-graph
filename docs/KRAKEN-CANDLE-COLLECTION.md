# Kraken candle collection (T25, slice B)

Status: built and tested in source on 2026-10-13. Not deployed. Paper only, public data only, no key, no
paid service. No test touches the live exchange. The only live requests were 17 plain `curl` recordings (see
"What Kraken returns") and one `--smoke` run of the command (see "Evidence").
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
trade-graph collect-kraken-candles --intervals 1 --one-minute-pairs all
trade-graph candle-coverage
trade-graph candle-coverage --symbols BTC/USD --intervals 15 --max-ranges 50
```

| Option | Meaning |
|---|---|
| `--market-database` | the candle file (default `runtime/market_data.sqlite`) |
| `--intervals` | comma list from 1, 5, 15, 60 (default all four) |
| `--pairs` | restrict the 5, 15 and 60 minute candles to these `BASE/USD` symbols (default every eligible pair). Refused (exit 2) when `--intervals` selects none of 5, 15 and 60, because it would change nothing |
| `--one-minute-pairs` | the only coins collected at 1 minute (default `BTC/USD,ETH/USD`; an empty value collects none; `all` collects the same pairs as the other intervals, so every eligible pair unless `--pairs` narrows them). Refused when `--intervals` does not include 1, unless the value is empty |
| `--max-requests` | most requests of the whole run, the pair-list refresh included (default 2000, 2 to 10001) |
| `--smoke` | BTC/USD and ETH/USD at 15 minutes, at most 4 requests (pair list 1, candles 2, one repeat after a short throttle), labelled `live_public_smoke`; cannot be combined with the scope or limit options |
| `--catalog-only` | refresh the pair list and collect no candles |

Every real run first refreshes the pair list (one request, `GET /0/public/AssetPairs`, slice A) so that a new
listing or a status change is seen, then collects. If that refresh fails but an older list is stored, the
candles are still collected from the older list and the run is reported `partial`. The exception is a refresh
that Kraken rate limited: nothing more is requested (see "Rates and stopping").

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
`trade_graph.sqlite`, the default `runtime/trade_graph.sqlite` (also through a symbolic or hard link), any file
that holds the financial tables, and any file that has a write-ahead log (`-wal`) in use while its main file shows
no candle tables yet are refused with `status: refused`. The last rule exists because a database that is open keeps
its newest pages, its table list included, in that log, and the check reads the main file only (it must not lock,
create or change anything). It also refuses a candle database that is still in its very first moments; it works
again after the other process closes it. A file that exists but is not a usable SQLite database ends with
`status: failed`, `error: market_database_unusable` and exit 1, and is left untouched. A hard link to a live
financial database stored somewhere other than the default path cannot be recognised.

## What is collected

- Pairs: the USD-quoted pairs of the newest stored catalog that are **eligible**: status `online` and no
  stablecoin, yield-token or fiat mark (D131, D127). The others are listed in `skipped_pairs` with the reason
  (`status:cancel_only`, `excluded:stablecoin`, ...) and are never requested. A name given on the command
  line that is not in the catalog is listed in `unknown_symbols` and makes the run `partial`.
- Intervals: 5, 15 and 60 minutes for every eligible pair; 1 minute only for the explicit list (default
  BTC/USD and ETH/USD), because a quarter of one-minute candles is about 131,000 rows per pair. **This default is
  narrower than the task wording ("1/5/15/60 minutes", build plan: "for every Kraken USD coin") and has not been
  decided by the owner.** `--one-minute-pairs all` collects 1 minute for every eligible pair: on the 591 eligible
  pairs of 2026-10-13 that is 591 requests per pass (about 32 minutes at the limiter rate; 1-minute data must be
  fetched at least every 12 hours), about 77.4 million rows and 9.1 GB per quarter on top of the rest. The report
  prints this cost for the run's own scope under `storage` before anything is decided.
- Coins that nobody has looked at (`not_individually_reviewed`) are collected like the rest. The price-peg
  check that would catch an unknown stablecoin belongs to the scanner (T31). Every collection report says so in
  its `catalog` section: `eligible_pairs` is how many pairs are not marked, `eligible_unreviewed_pairs` is how many
  of those nobody has individually reviewed (566 of 591 in the live list of 2026-10-13 per
  [PAIR-CATALOG.md](PAIR-CATALOG.md)), `unreviewed_asset_count` counts the coins, and `verification_note` states
  that unmarked is not verified clean. The command's `catalog_refresh` block repeats the two counts.

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

Source: https://docs.kraken.com/api/docs/guides/spot-rest-ratelimits (read 2026-10-13, read again 2026-10-15 with
the same numbers: Starter maximum 15, drain 0.33 per second; the page also lists Intermediate 20 at 0.5 and Pro 20
at 1, which this collector does not assume). A counter rises by one
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
| Start of a command | the counter starts **at the ceiling** (`initial_counter` 10) | a new process cannot know what an earlier one asked for a moment ago. The first call waits 3.3 seconds, there is no burst, and a run that begins right after another never takes Kraken's real counter above 10. Simulated against Kraken's documented counter (15, drain 0.33): two runs of 30 calls back to back peak at 18.0 from an empty start and at 1.0 from the ceiling |
| Clock | the limiter reads `time.monotonic`, not the host clock | a time-service correction of the host clock must neither empty the counter (a step forward) nor freeze it (a step back) |

The mirror lives in one process. **Two collectors running at the same time are not coordinated** and can together
pass Kraken's maximum (each assumes it has the ceiling to itself). Run one at a time. The suggested plan below
(a short 1-minute run every 6 hours and a full run daily) will overlap once a day: start the full run first, or
give the short run a different clock time. Whether Kraken counts keyless public calls on one counter per address
is not documented; the limiter assumes it does.

Full-scope timing (591 eligible USD pairs in the live catalog of 2026-10-13, confirmed by the smoke run): 1,775
requests, about 98 minutes at the limiter rate; one interval for all pairs, 591 requests, about 32 minutes.

What happens on an error (no retry loops anywhere):

| Event | Behaviour |
|---|---|
| `EAPI:Rate limit exceeded`, HTTP 429, or any other rate-limit wording (`Too many requests`, `Rate limit exceeded` under any prefix such as `EOrder:`, `Temporary lockout`) | the run **stops**: `stop_reason: rate_limited`, that series is `failed`, the rest are listed as not reached. Kraken's list has several wordings and I could not confirm which one a keyless public call gets, so all of them stop the run |
| the same rate-limit answers (or `EService: Throttled`) to the **pair-list** call at the start | no candle is requested at all: the command prints `status: failed`, `error: public_catalog_rate_limited` (or `public_catalog_throttled`) and `stop_reason`, and exits 1, even when an older pair list is stored. The pair-list call is never waited out or repeated |
| `EService: Throttled: <time>` with the retry time at most **30 seconds** ahead of the host clock | waited out **once per run**: the run sleeps until the stated time plus a 2 second margin, then repeats that one request (it counts against `--max-requests`) |
| a second throttle, a retry time more than 30 seconds ahead, an unreadable one, or no request left in the limit | the run **stops**: `stop_reason: throttled` with the stated time |
| 10 failed series in a row | the run **stops**: `stop_reason: consecutive_failures`. A dead network would otherwise cost thousands of timeouts. Any success resets the count |
| `--max-requests` reached | the run **stops**: `stop_reason: max_requests` |
| one series fails (bad answer, unknown pair, storage error) | that series is `failed` with a fixed category; the others continue |

Failure categories are fixed words (`request_failed`, `rate_limited`, `throttled`, `unknown_pair`,
`service_unavailable`, `exchange_error`, `response_invalid`, `pair_mismatch`, `candle_not_closed`,
`clock_invalid`, `persistence_failed`). Response text and exception text never enter a report or a run record.
A failed series also carries `exchange_error_class` (Kraken's own class such as `EGeneral`, when the answer was an
exchange error) and `internal_error_class` (the class name of a local failure such as `ReadTimeout` or
`OperationalError`, only when it is a plain identifier), so a network fault and a storage fault differ in the
report without any text from either.

## What Kraken returns (recorded 2026-10-13)

Source of the endpoint description: https://docs.kraken.com/api/docs/rest-api/get-ohlc-data (read 2026-10-13, read
again 2026-10-15 with the same content: intervals 1, 5, 15, 30, 60, 240, 1440, 10080, 21600; at most the 720 most
recent entries; the last entry is the current, not-yet-committed timeframe and is always present; prices and
volume are strings; the page does not say whether intervals without trades are omitted or filled).
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
   window, not because it had no trades. The frozen contract text is not edited here; the correction is
   raised as a contract change request in the T25 handoff.
   **A reader of the store must treat `trade_count == 0` as "no trade happened", never as a price that traded.**
   The open, high, low and close of such a candle are the previous close carried over by Kraken, `Candle` and
   `window()` carry no separate marker, and an indicator that does not look at the count will read prices that
   never traded.
5. **A pair with no data in the window returns one row**: time 0, zero prices, count 0 (`ACAUSD`, a cancel-only
   pair). That is the forming row; it is dropped and the series reports `no_data`.
6. **The exchange clock ran 153 seconds behind the host clock** on every recorded response (the `Date` header
   against the host and against Windows time, which agreed with each other). I could not tell which was right.
   The collector uses the host clock for receipt times and refuses a response in which any row would close after
   that receipt time. All 17 recordings show the same offset (153.3 to 154.0 seconds), which points at a fixed clock
   difference, not at stale answers. On 2026-10-15 the Windows Time service on this PC was not running
   (`w32tm /query /status` reported the service not started), and WSL takes its time from Windows, so the two
   host readings are not independent of each other. That makes a drifting host clock the likelier cause, but it is
   not proven. A host clock that is *ahead* is safe (receipts are later than true). A host clock
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
  trade count from zero to 10^12 (a larger number is a corrupt answer, not a storage error); a hole between rows
  is allowed (it simply stays a missing slot);
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
| `connected` | the first returned candle is at most one interval after the newest stored one (the usual case is that it is the same candle again): no candle was lost |
| `gap` | at least one slot lies between the newest stored candle and the first returned one. `missing_slots` candles between them can no longer be fetched from this endpoint. `action` says: use the Trades backfill or the archive import (T26) |
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

The report's `recommended_every_hours` is the upper bound (half the window: 6, 30, 90 and 360 hours); the
"Suggested" column above is deliberately tighter for 15 and 60 minutes. A practical plan that respects all four: the short `--intervals 1` run for BTC and ETH every 6 hours, and the
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

## The host clock

Receipt times come from the host clock. If it is corrected backwards between two runs, a new run would date its
receipts before the older ones, and a point-in-time read could then show a newer candle beside a hidden older one.
So a collection run is refused with `error: clock_regression` (nothing is requested, nothing is stored, the failed
run is recorded) when the clock reads earlier than the latest start or finish time of any recorded run of the same
source; the report shows `latest_recorded_utc`. It works again once the clock has passed that time (at most the
size of the correction). A run that crashed before it finished is compared by its start time only. The guard is
per source: synthetic test runs and live runs never hold each other back.

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
- `recorded_public_fixture`: 17 plain requests were recorded; 16 responses are stored (trimmed) in
  `tests/fixtures/kraken/` (the 17th, a `since=1` probe, returned exactly the same rows as the plain request and
  is only a manifest entry). The end-to-end test runs 5 pairs at all four intervals, 20 series, through the whole
  collector: **13 of the 20 series are served from the stored recordings and 7 from the scripted exchange**
  (LINK and USDUC at 5 and 15 minutes, PAXG at 5, 15 and 60 minutes were not recorded). Only BTC and SOL have all
  four intervals recorded. The run carries the label `recorded_public_fixture` because most of it is recorded.
- `synthetic_fixture`: table-built responses from a scripted exchange that behaves like the recordings.
- `live_public_smoke`: `--smoke` against the real exchange, run once by the implementing agent on 2026-10-13 at
  15:54 UTC into a throwaway candle file that was not kept. **This result is a statement in this document, not a
  checkable file: neither the report JSON nor the database was kept, so nobody can verify it from the repository.**
  Repeating it is one command (3 requests); save its printed report with the status notes to turn it into
  evidence. Result as the agent reported it: exit 0, status `ok`, 3 requests (pair list 1,
  candles 2), no waiting in the limiter. The pair list had 677 USD pairs, 591 eligible, 0 rejected and 608 base
  coins not individually reviewed. BTC/USD and ETH/USD at 15 minutes each returned 721 rows, 720 candles stored,
  no quiet candles, one unbroken run of 720 slots (2026-10-06 03:45 to 2026-10-13 15:45), and `candle-coverage`
  read them back with no missing slot. An incremental second live run was not made (it would exceed the
  recording budget of 20 requests).
- `live_public`: a normal real run has not been made. A recording is evidence of the format on 2026-10-13, not of
  a full collector run.

## In the service

Since W6-D (decisions D221, D222 and D224) one collector runs inside the paper service and replaces the two Windows
scheduled tasks (`scripts/register_candle_schedule.ps1`, removed at step L3, [L3-INSTALL-CHECKLIST.md](L3-INSTALL-CHECKLIST.md)).
Code: `application/price_collector.py` (`PriceCollector`), `application/service_market_data.py` (the candle file, the
read-only handle and the coins the team works with). Tests: `tests/integration/test_price_collector.py`,
`test_price_collector_service.py`, `test_install_wiring.py`, `test_shared_rate_limit.py`,
`tests/unit/test_shared_kraken_limiter.py`.

- **Where.** The candle file is `market_data.sqlite` beside the financial database (`/var/lib/trade-graph` in the
  image). The collector is its only writer; the dashboard, the hourly features (D220) and the price watcher open it
  read-only.
- **When it runs.** With `public_data_enabled` and `price_collector.enabled` (on by default) in the protected
  `paper-config.json`. The service calls one collector step at a time in its own thread; a tick never waits for it,
  a failed step is reported as `collector:<error>` and management goes on. A stop ends its waiting at once; the
  request in flight finishes and the chunk's run is recorded.
- **What, most urgent first.** (1) The coins the team works with now (BTC/USD and ETH/USD, the quote feed's coins,
  the newest shortlist, today's focus coins, held coins, coins with an armed alarm or an active day plan) at 5, 15 and
  60 minutes, and at 1 minute for BTC/USD, ETH/USD, focus coins and coins with a 1-minute alarm: a series is due when
  a newer candle has closed and settled (30 seconds; 180 for an hour, because Kraken commits an hour 2.6 to 3.3
  minutes after it closed, D129), a 1-minute series at most every 5 minutes, and an answer without the newer candle
  is asked again after 60 seconds. (2) The ranking pass: every eligible pair's 60-minute series once per hour, then
  the hourly coin scan (one Ticker request, `CoinScanner`, `CoinScanStore`; also at 50 minutes past the hour when the
  pass is slow). (3) The wide pass: every eligible pair at 5 and 15 minutes once a day. The pair list refreshes once
  a day. These are collection cadences in the configuration (`price_collector`), not caps on anyone's work (D116).
- **One limiter.** Every request of the collector, the scan's Ticker request and any Trades backfill in the process
  go through one `KrakenRestLimiter` (D221): it starts at the ceiling of 10, drains 0.30 a second and spaces calls one
  second apart. A rate-limit or throttle answer stops the chunk, the counter is taken to be full and waited out, and
  collection pauses another 60 seconds. The 10-second quote feed keeps its own cadence beside it, as before (the
  headroom of 5 below Kraken's 15 is for it). Kraken does not document whether keyless calls share one counter per
  address; if the service sees rate-limit answers, the reports say so and `ranking_every_hours: 2` halves the
  largest share.
- **Records.** Each chunk (at most 30 requests) is one `public_collect` run with the parameters `collector:
  service-collector-v1` and a compact report: counts by kind, totals, failed series, gaps (with the backfill action)
  and the series not reached. The activity log gets a `price_collection` event for every pair-list refresh and hourly
  scan (with the ranking pass's counts), every gap or failure, and every change of status; an ordinary chunk is not
  logged. The clock guard of the command applies too: a clock behind a recorded run pauses collection until it has
  passed that run again.
- **Expected cost (computed, not measured).** About 591 requests an hour for the ranking pass, about 1,180 a day for the wide pass and a few
  hundred an hour for the coins the team works with: about 0.23 requests a second on average, under the limiter's
  0.30. The store grows by about 30 MB a day for this scope (Storage, above); a chunk's report is under 4 KB.

## Known limits

- One invalid row refuses its whole response. Because incremental requests start at the newest *stored* candle,
  the same row is in the next response too, so that series stays failed until the row is outside the window
  (12 hours at 1 minute, up to 30 days at 60 minutes). The report names the category; the Trades backfill could
  fill the span.
- Delisted pairs have no history here; a back-test over today's pairs is optimistic.
- The eligible list is Kraken's international one; whether the owner's EU account may trade a pair is not checked.
- Pair statuses other than `online` are not collected until `online` again; their stored candles stay.
