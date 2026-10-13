# Public Trades backfill and source reconciliation (T26 slice B)

Status: implemented and tested with scripted transports and six recorded public pages (2026-10-13). No live
backfill has been run for real yet; the only live requests so far are the six recordings (plus one clock check)
described below.

The archive (slice A) ends when Kraken last published a quarterly file. The collector (T25) starts when someone
first ran it. Between them is a gap. This command fills it by rebuilding **completed** candles from Kraken's public
list of executed trades, and a second command compares the three sources where they overlap. Nothing here places an
order, uses a key, or costs money: every request is an unauthenticated public GET, and paid calls stay disabled.

## What Kraken's pages say (reviewed 2026-10-13)

* [Get Recent Trades](https://docs.kraken.com/api/docs/rest-api/get-recent-trades), `GET /0/public/Trades`:
  `pair` (required), `since`, `count` (1 to 1000, default 1000, the page says it "returns the last 1000 trades by
  default"). Each row is price (string), volume (string), time (Unix seconds with a fraction), side (`b`/`s`), order
  type (`m`/`l`), miscellaneous, trade id (integer). `result` holds the pair's rows and `last`, which the page
  describes as the value to pass as `since` when polling for new trades.
* [Spot REST rate limits](https://docs.kraken.com/api/docs/guides/spot-rest-ratelimits): a counter per API key
  starts at 0; most calls add 1 (ledger and trade-history calls add 2); the counter falls by 0.33 per second for the
  Starter tier and is limited to 15 (Intermediate 20 and 0.5, Pro 20 and 1). `EAPI:Rate limit exceeded` means the
  counter went over its maximum; `EService: Throttled: <unix time>` means too many concurrent requests and names the
  time to retry after. The page gives no backoff recipe or ban duration.

What the pages **do not** say, and what was therefore observed on 2026-10-13 from the recordings
([`tests/fixtures/kraken/README.md`](../tests/fixtures/kraken/README.md)):

| Question | Answer from the recordings |
|---|---|
| Unit of `since` | Both Unix seconds (`since=1577836800` returned 2020-01-01 trades) and the 19-digit value returned as `last` were accepted. |
| Is `since` inclusive? | Yes. Requesting with the previous page's `last` returned that page's final trade again as the first row. |
| What is `last`? | The final returned trade's time in nanoseconds (a few digits finer than the row's seven-decimal `time`). |
| Time precision | Seven fraction digits (100 ns). Kept exact: the response is parsed with `Decimal`. |
| Trade ids | An integer sequence per pair; consecutive within every recorded page. Many trades share one timestamp. |
| How far back | At least to 2020-01-01 for BTC/USD. The page states no limit; the true limit was not probed. |
| Result key | `XXBTZUSD` for the request `XBTUSD`, but `BATUSD` for `BATUSD`. |
| Do public calls share the counter? | Unknown. The page describes the counter per API key and does not mention unauthenticated calls. The limiter assumes the worst case: one shared counter for every call this process makes. |

If the Trades call turned out to cost 2 on the counter instead of 1, the sustained rate in the tables below would
halve (every time would double). `LimiterSettings.cost` is the one number to change.

## How the backfill works

1. **Target range.** For each pair and interval: from the end of the archive series (last archive candle's close) to
   the start of the collector series (its first candle's open), both read from the store's series summaries. `--start`
   and `--end` replace either bound and are aligned inward to the interval. A bound that is neither in the store nor
   given is reported (`no_target`, with the sentence saying which), never guessed. If the archive ends after the
   collector starts there is no gap (`already_covered`; use `reconcile-candles`).
2. **One stream, every interval.** Trades are fetched oldest first, 1000 per request, `since` = the range start
   minus one second on the first request, then the previous page's `last`. The same trades build every requested
   interval (1, 5, 15, 60; default 15), so extra intervals cost no extra requests.
3. **Completed candles only.** A candle covers `[open, open + interval)`; a trade exactly on the close belongs to
   the next candle (assumed to match Kraken's OHLC rule; `reconcile-candles` would show any difference). A candle is
   built only when a *later trade in the stream* has passed its close, never from the computer's clock: this
   computer's clock read 153 seconds ahead of Kraken's `Date` header on every recorded request. The still-forming
   candle at the end of the range is therefore never produced. Slots without trades produce nothing.
4. **Values.** Open, high, low and close from trade order; volume is the exact sum; `vwap` is the exact sum of
   price times volume divided by volume, rounded half-even to four decimals finer than the finest price (the grid
   contains every price, so vwap stays inside the low/high range); `trade_count` counts rows of the Trades list.
5. **Receipt time.** `available_at` is the injected clock read after the network call of the page whose trades
   completed the candle. Archive and backfilled candles are available at fetch time, not at their historical close.
   If a page arrives with a receipt earlier than a candle's close (a slow clock) that candle is withheld and counted
   (`withheld_receipt_before_close`), never backdated.
6. **The boundary trade.** Because `since` is inclusive, the page after a full page begins with the trades that share
   the previous page's final timestamp. A trade is consumed once, by id: a row whose id is at or below the highest
   consumed id is a repeat. A repeat must equal the row already consumed (the whole row); the
   consumed rows with the final timestamp are kept for that comparison. A repeat that differs refuses the page
   (`boundary_mismatch`, nothing from that page is applied). A repeat that is *not* among those rows is counted as
   `unexpected_overlap` and skipped. A full page that adds no new trade (more trades at one instant than the page
   holds) stops the pair (`cursor_stalled`) instead of looping.
7. **Id gaps.** Ids were consecutive in every recorded page. If a new trade's id is not the previous id plus one, the
   candles that the missing trades could belong to (the open candle of each interval and the one the new trade opens)
   are marked incomplete and **withheld, not stored**; the report lists the gaps (`id_gaps`, examples) and counts the
   withheld candles. This is a safety rule for an unconfirmed property; if real data has frequent id gaps the counts
   will show it immediately. **Limit:** a gap is seen only between two trades the run received. The trade before the
   first one a run receives is unknown, so an id missing just before it cannot be seen. For a fresh run that is the
   first trade at or after the range start, and the first candle of the range is the only one it could affect. A
   resumed run carries the gap flag of its cursor candle forward (next section), so resuming never hides a gap that
   an earlier run saw.
8. **Bounded.** `--max-requests` (default 100, at most 100,000; `--smoke` 3) counts every HTTP attempt including
   retries. When it is spent the run stops, records its cursor, and reports exactly what it did not reach.

### The cursor, and how it is read back

Every run is recorded with `kind = "trades_backfill"`, `source = "kraken_public_trades"` and an evidence label
(`live_public`, or `live_public_smoke` under `--smoke`; tests use `recorded_public_fixture` or
`synthetic_fixture`). Its report holds, for each pair and interval, `range_start_unix`, `range_end_unix` and
`next_open_unix`: **every slot before `next_open_unix` was decided** (stored, empty, or withheld). It is the open
time of the oldest candle that was still forming when the run stopped.

The next run reads it with `store.runs()`: `read_resume_points(store, symbol, pair_code, interval, range_start=...)`
returns the highest `next_open_unix` among finished runs that covered the same venue pair code and began at or
before the current range start (an earlier start cannot trust a later run's cursor). The stream restarts at the
oldest `next_open_unix` among the intervals minus one second and each interval ignores trades before its own cursor,
so no partly built candle is ever stored. The cost is re-fetching at most one candle of the largest requested
interval. The one thing the re-fetch cannot rebuild is a gap in trade ids just before the candle it starts at (the
trade before it is not fetched again), so each interval's entry also records `open_candle_has_id_gap`: whether the
candle still forming at the stop was already known to lack trades. The next run starts that candle tainted, so it is
withheld exactly as an uninterrupted run would have withheld it. A cursor written without this flag counts as flagged
(unknown withholds), and when several runs ended on the same cursor any flagged one counts. `--from-start` ignores stored cursors. A run that never finished (for
example a power cut) has no report and gives no cursor; the next run then starts from the range start and the store
turns repeated candles into no-ops. Ctrl-C and a termination request (SIGTERM) do finish and record the run.

### Why a run stops

Each pair ends with exactly one `stop_reason`:

| `stop_reason` | Meaning |
|---|---|
| `end_reached` | A trade at or after every interval's range end was seen: the range is complete. |
| `max_requests` | The request budget was spent. A pair that never got a request is `not_started`. If the run decided no slot at all (the budget was smaller than the requests one whole candle needs, because a run restarts at the open candle), the pair's `message` says so and the command repeats it on the error stream: raise `--max-requests`. `--smoke` (3 requests) is for a first look at one pair and may end this way on a busy pair. |
| `stream_exhausted` | Kraken had no further trades yet, before the range end (the final partial candle is not built). |
| `rate_limited` | Kraken still answered `EAPI:Rate limit exceeded` or `EService: Throttled` after two waits. |
| `rate_limit_wait_too_long` | The required wait exceeded 120 seconds. The whole run stops instead of sleeping on: pairs not yet started are reported `not_started` with this reason and get no request, so a long pause asked for by Kraken is not probed once per remaining pair. |
| `transport_error` | The HTTP request failed three times (two back-off retries). |
| `page_refused` | The page failed validation; `refusal_reason` names why (for example `invalid_json`, `pair_mismatch`, `kraken_error`, `row_invalid`). |
| `cursor_stalled` | A full page contained only trades already consumed. |
| `cursor_regressed` | The returned cursor went backwards. |
| `boundary_mismatch` | A repeated trade differed from the one consumed earlier. |
| `time_regressed` | A new trade id carried an earlier time than a consumed trade. |
| `store_refused` | The candle store refused a write (for example a different venue pair code for an existing series). |
| `interrupted` | Ctrl-C (exit 130) or a termination request, SIGTERM (exit 143); the run and its cursor are still recorded. A hard kill (SIGKILL, power cut) cannot be recorded. |
| `unexpected_error` | A bug; the run is recorded as failed and the error is re-raised. |
| `no_target` | No usable range (a bound is missing). |
| `already_covered` | The archive and collector overlap or touch. |

The `refusal_reason` of a `page_refused` stop is one of `response_too_large`, `invalid_json`, `duplicate_keys`,
`shape`, `pair_mismatch`, `cursor_invalid`, `cursor_inconsistent`, `too_many_rows`, `row_invalid`,
`page_not_ordered`, `kraken_error`, `receipt_clock_backwards` or `clock_invalid`. The two rate-limit answers,
`rate_limited` and `throttled`, are waited out and retried first.

Candles already saved stay saved in every case. The report also lists `not_reached` (pair, interval, slot count and
time span) and, for the decided part, the store's own coverage with the 25 largest empty ranges.

### Rate limiting

`adapters/market/trades_rate_limit.py` models the counter: maximum 15, decay 0.33 per second, 1 per call, with 2
points of headroom (never above 13), at least 1 second between calls, an injected clock and sleeper. After a
rate-limit or throttle answer it assumes the counter is full and waits until it is back under the ceiling (9.091
seconds, or until the throttle time if that is later), then asks the same page again, twice at most. A failing transport is backed off the same way. Sustained, this is one call every 3.03 seconds.
**For the integration owner:** the collector stream is writing its own `adapters/market/rate_limit.py` with the same
counter model; the two should become one limiter shared by every REST caller in the process.

## How big is the job? (measured 2026-10-13)

Cost is per **trade**, not per candle: 1000 trades per request, and the limiter allows 1,188 requests per hour. The
trade counts below come from the trade-id difference over almost exactly 24 hours (recordings 5 and 6) and are one
day each; busy and quiet days differ by a factor of two or more (the 1000 BTC/USD trades of recording 1 spanned only
775 seconds at that moment, 1.29 per second, against the day's average of 1.90). Averaged over 2020-01-01 to
2026-10-13 BTC/USD made about 37,000 trades a day, so older history is cheaper than the table.

| Pair (observed day) | Trades per day | Requests per day | One day of history at the limit |
|---|---:|---:|---:|
| BTC/USD, a busy pair | 164,273 | 165 | 8.3 minutes |
| BAT/USD, a thinner pair | 13,680 | 14 | 42 seconds |

| Gap of N days | BTC/USD requests | BTC/USD time | BAT/USD requests | BAT/USD time |
|---:|---:|---:|---:|---:|
| 1 | 165 | 8.3 min | 14 | 42 s |
| 7 | 1,150 | 58.1 min | 96 | 4.8 min |
| 14 | 2,300 | 1 h 56 min | 192 | 9.7 min |
| 30 | 4,929 | 4 h 9 min | 411 | 20.8 min |
| 90 | 14,785 | 12 h 27 min | 1,232 | 1 h 2 min |

Requests are the trade count divided by 1000, rounded up; time is requests times 3.03 seconds (0.33 per second).
Neither pair is the quietest: a pair with 1,000 trades a day costs one request a day. The other pairs were not
measured. All pairs share one counter, so costs add. Illustration only (assumed, not measured): a 14-day gap for
5 BTC-class pairs and 45 BAT-class pairs is about 5 x 2,300 + 45 x 192 = 20,140 requests, about 17 hours of running.
So in practice: a gap of a few days or weeks is realistic for any pair, the handful of busiest pairs dominate the
cost, and a 90-day backfill of a BTC-class pair is a half-day job. One backfill keeps going safely across runs, so
it can be split into runs of `--max-requests` each.

## Reconciling sources

`reconcile-candles` and `application/reconcile_candles.py` compare the same slots across two or more sources and
change nothing. For a symbol, an interval and a window it reports per slot one of `identical`, `differing`,
`single_source` (and which source holds it) or `absent_in_all`, then for every pair of sources: how many slots both
hold, how many are identical or differ, how many only one holds, and per field (open, high, low, close, volume, vwap,
trade_count) how many were compared, how many differ, the largest absolute difference, the largest and mean relative
difference. The relative size is `|a - b| / max(|a|, |b|)` rounded to 12 decimals. `vwap` is compared only where both
sources hold one (the archive has none; those are counted as `not_comparable`, not as differences). The report
lists the worst `N` differing slots (largest relative size first, earlier slots first on ties) with every
difference. Differences are reported, never merged. A missing slot means no candle is stored, which is either "no
trades" or "not collected".

`precedence_window(store, symbol, interval, sources, end_close_time=..., count=..., as_of=..., mode=...)` is the
reading side. For each slot it returns the stored candle of the **first source in the order that has it**, says which
source supplied it, and names any other source that disagreed there and on which fields. It never averages,
fills or blends, and the chosen candle is the stored candle unchanged (its own `source`, `vwap` and `trade_count`
included). `mode` and `as_of` mean what they mean for `CandleStore.window`: `point_in_time` hides candles received
after `as_of`; `replay` uses event time only and is labelled `event_time_only`.

Default order: `kraken_public_ohlc`, then `kraken_ohlcvt_archive`, then `kraken_public_trades`. Reasons:

1. The collector's candles are what Kraken's own OHLC endpoint answered when this program asked, they carry a real
   receipt time, and they are seen live.
2. The archive is Kraken's own published file, but it was received in bulk, its format is unconfirmed and it has no
   vwap.
3. The Trades-built candles are aggregated by this program from raw trades (including its own assumptions about slot
   edges and what a trade count is), so they are the most derived.

The order is a choice, not a truth: pass another tuple to prefer a different source. A window that mixes sources can
show small steps where the supplying source changes; the per-slot labels and disagreement lists show where.

## Commands

```bash
# one pair, at most 3 live public requests, labelled live_public_smoke (choose the range explicitly on a new store)
uv run --frozen trade-graph backfill-kraken-trades --pairs BTC/USD --smoke \
    --start 2026-10-13T12:00:00Z --end 2026-10-13T13:00:00Z

# the real gap between the stored archive end and collector start, 15 and 60 minute candles, 500 requests this run
uv run --frozen trade-graph backfill-kraken-trades --pairs BTC/USD,ETH/USD --intervals 15,60 --max-requests 500

# compare the sources over the newest 2000 fifteen-minute slots (read-only)
uv run --frozen trade-graph reconcile-candles --pair BTC/USD --interval 15 --count 2000
```

Both take `--market-database` (default `runtime/market_data.sqlite`), refuse the financial database
(`trade_graph.sqlite`) and any SQLite file that is not a candle-history file, and print one JSON report.
`backfill-kraken-trades` exits 0 when the run was recorded (status `ok` or `partial`), 1 when nothing could be done
(`failed`), 2 for a usage error, 130 after Ctrl-C and 143 after SIGTERM. A run is `failed` when no range could be planned, or when a pair
stopped for a failure reason (the table above: everything except `end_reached`, `max_requests`, `stream_exhausted`,
`no_target` and `already_covered`) and no slot was decided in this run (`decided_this_run` is 0 in every interval).
Real progress keeps `partial` even if a later request failed, so a scheduler can tell a dead network from a slow day.
`reconcile-candles` refuses a missing file instead of creating
it. Pair names may be written `BTC/USD` or `XBTUSD`; stablecoin pairs such as `USDT/USD` are refused as a typing
guard only. The authority on stablecoin and yield-token exclusion (D131) is the collector's dated pair catalog.

## Evidence and what is not proven

* Six recorded pages (`tests/fixtures/kraken/`, label `recorded_public_fixture`) and a scripted exchange that
  reproduces the observed cursor rules drive the default tests. Default tests make no network calls. A scripted
  transport is not a live result.
* One extra public GET (`/0/public/Time`) was made on 2026-10-13 to compare clocks; it is not a recorded fixture.
* **Unconfirmed:** that trade ids stay consecutive for all pairs and periods; that a trade exactly on a close belongs
  to the next candle; how Kraken's own candle counts trades; how far back Trades reaches; whether unauthenticated
  calls share the rate counter; pair names other than `XBTUSD` and `BATUSD` (a symbol is turned into a Kraken code
  by the archive's rule: `BTC` is `XBT`, `DOGE` is `XDG`, otherwise the base followed by `USD`); and the behaviour of
  `since` for very old dates beyond Kraken's retention.
* A hard kill (not Ctrl-C or SIGTERM) loses that run's cursor, so at most one run's requests are repeated.
* Trailing quiet slots of the archive are fetched again (the archive's last candle is its last slot with trades).
* Delisted pairs have no history here, so tests of coin selection on this data stay optimistic.
