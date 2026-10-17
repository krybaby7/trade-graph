# Candle store contract (T25, shared by T26 and T27)

Status: contract agreed 2026-10-13 before parallel work (decision D132).
Code: `contracts/candles.py`, `adapters/persistence/market_data*.py`,
`application/candle_store.py`. Tests: `tests/unit/test_candle_contract.py`,
`tests/integration/test_candle_store.py`.

## Why a separate database file

Candles for many pairs at 1, 5, 15 and 60 minutes add up to tens of millions of
rows (about 130,000 one-minute candles per pair per quarter). The financial
database holds ledger, authority and recovery records that are backed up,
witnessed and recovered as one unit. Price history does not belong in that
unit: it is rebuildable public data, it would make every backup and recovery
many times larger, and a long bulk write would compete with trading writes.

So candles live in their own SQLite file (default `runtime/market_data.sqlite`,
ignored by Git, private permissions, WAL). It has its own small migration list.
The financial schema, `hourly_candles` and earlier feature snapshots are not
touched. The financial database never needs this file; the file never needs
the financial database.

In an installation the file is `market_data.sqlite` beside the financial database
(`/var/lib/trade-graph` in the protected image, W6-D, D224). The service's price
collector is its only writer; the dashboard, the hourly features and the price
watcher open it read-only (`ReadOnlyMarketData`).

## Tables (append-only; triggers refuse UPDATE and DELETE)

| Table | Holds |
|---|---|
| `candle_series` | one row per (symbol, source, interval): symbol `BTC/USD`, venue pair code `XBTUSD` |
| `market_candles` | one row per candle **revision**: key (series, open time, revision), receipt time, run, exact decimal text, content hash |
| `candle_runs` / `candle_run_results` | what a collection, import or backfill did: parameters at start, report at the end. A run with no result row was interrupted, which is a visible fact |
| `pair_catalog_observations` | dated Kraken pair lists with each pair's status and its stablecoin / yield-token marks (D131) |

Times are stored as integers (open time in unix seconds, receipt time in unix
microseconds). Prices, volumes and counts keep their exact decimal text. There
are no floats in the store.

## Rules every writer follows

1. **Completed candles only.** A `Candle` cannot be built unless its receipt
   time is at or after its close time. The collector also drops the final,
   still-forming row of every Kraken OHLC response.
2. **Receipt time is availability.** `available_at_utc` is the moment this
   process obtained the data. It is never taken from the exchange and never
   backdated: a candle whose receipt is earlier than the stored latest
   revision's receipt is refused. Archive files and backfills are available at
   import time, not at the historical close.
3. **Immutable revisions.** Saving identical content again changes nothing.
   Changed content for the same slot becomes the next revision. A -> B -> A is
   three revisions. Earlier revisions stay and stay readable.
4. **Sources never merge.** The sources are `kraken_public_ohlc` (collector),
   `kraken_ohlcvt_archive` (T26 import), `kraken_public_trades` (T26 backfill)
   and `synthetic` (tests). Each is its own series. Differences between them
   are reported by T26's reconciliation, never smoothed away in storage.
5. **A missing slot stays missing.** No carry-forward, no zero fill by us.
   What a missing slot means depends on the source (recorded 2026-10-13):
   - `kraken_public_ohlc`: Kraken's OHLC endpoint itself fills quiet
     intervals with a flat row (vwap 0, volume 0, trade count 0). The
     collector stores such rows as sent. A missing slot therefore means "not
     collected" or "older than the 720-candle window".
   - `kraken_ohlcvt_archive`: Kraken's page says a row exists only for
     intervals with trades, so a missing slot may simply mean "no trades".
   - `kraken_public_trades`: built from trades; a slot with no trades is not
     emitted.
   Reports say "no candle" and never guess which reason applies; T26's
   reconciliation shows where sources differ because of this.
6. **Money stays Decimal.** Candle fields are `Decimal`. Only indicator
   outputs (T27) may be floats.

## Reading

`CandleStore.window(symbol, interval, source, end_close_time, count, as_of, mode)`
returns exactly `count` slots ending at `end_close_time`, oldest first. Each slot
is a stored candle (with its revision and run) or a missing slot.

- `point_in_time` mode (live use, the default): a candle is visible only if its
  receipt time is at or before `as_of`.
- `replay` mode (practice range, scanner history): visibility uses the candle's
  close time only, so months of imported history can be replayed. The result
  says `availability_basis = "event_time_only"`. Replay results are for
  research and are never evidence that live data had arrived by then.

`CandleStore.coverage(...)` lists, per series, the first and last candle, how many
slots are present and every missing range. Reports from the collector, the
importer and the backfill use this one function.

## Intervals

The store accepts Kraken's candle sizes 1, 5, 15, 30, 60, 240, 720 and 1440
minutes (open times aligned to the epoch). The collector gathers 1, 5, 15 and 60
(`COLLECTED_INTERVALS`).

## Disk use

Measured on 2026-10-13 with synthetic one-minute candles: 100,000 rows took
11.8 MB (about 118 bytes per candle revision, indexes included) and saved at
about 32,000 rows per second. One pair at one-minute detail for a quarter is
about 130,000 rows (15 MB); 350 pairs would be about 5.4 GB. Candles of 5, 15
and 60 minutes are 5, 15 and 60 times fewer. The store is append-only, so the
way to limit growth is to choose which pairs are collected at which interval
(the collector takes that scope), not to delete rows.

## Commands

Commands live as modules in `trade_graph/market_data_cli/` (discovered; each
defines `register(sub)` and `run(args, parser)`). They take
`--market-database` (default `runtime/market_data.sqlite`).

## What the next streams may add

- T25 collector: the pair catalog fetch, classification, `kraken_public_ohlc`
  rows, run records, gap reports, CLI.
- T26: `kraken_ohlcvt_archive` and `kraken_public_trades` rows, source
  precedence views and reconciliation reports on top of `window`/`coverage`,
  and a migration only if a new table is truly needed (append a new version to
  `MARKET_DATA_MIGRATIONS`, never edit version 0001).
- T27: reads windows through `CandleStore.window`; no schema change.
