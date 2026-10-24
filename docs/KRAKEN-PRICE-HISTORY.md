# Public Kraken hourly price history

The public-only collection command supplies completed BTC/USD and ETH/USD
hourly candles to the existing Research and Trader workflows. It uses the
existing private paper database and does not initialize/reset a portfolio,
request account data, start a service, enable paid models or authorize orders.

From the owner's WSL2 checkout, after updating to the published checkpoint:

```bash
uv run --frozen trade-graph collect-kraken-history --database runtime/trade_graph.sqlite --hours 168
```

This explicit invocation makes at most two public GET requests, one for each
fixed pair, with interval 60 minutes. The requested window is bounded to
1–719 hours, with a 1 MiB response ceiling and ten-second transport timeout
per request. There is no pagination or retry loop. Successful responses are
retained even if the other pair fails; the safe JSON report distinguishes
partial/failing collection and discloses missing hourly slots. Repeat the
same command later to accumulate history; unchanged candles deduplicate
across restart and retain their original availability.

Kraken's [official OHLC reference](https://docs.kraken.com/api-reference/market-data/get-ohlc-data),
reviewed 2026-10-06, specifies up to 720 recent entries and an always-present
final uncommitted candle. The collector unconditionally excludes the final
entry. It cannot retrieve an arbitrarily old window by paging `since`.
Missing hours are reported; no synthetic carry-forward candles are created.

## Feature definitions

The installed templates request the following fields. These are initial
versioned software definitions, not validated profitable parameters. Since
`hourly-price-features-v3` (D136) every value is computed by TA-Lib, so no
indicator formula is written in this repository (D118). Earlier snapshots keep
their v1/v2 values and are never recomputed.

| Field | Required contiguous completed candles | v3 definition (TA-Lib function) |
|---|---:|---|
| `sma_20` | 20 | `SMA` over 20 hourly closes, USD per BTC/ETH |
| `sma_50` | 50 | `SMA` over 50 hourly closes |
| `highest_high_20` | 20 | `MAX` over 20 hourly highs (new in v3) |
| `range_high` | 24 | `MAX` over 24 hourly highs |
| `range_low` | 24 | `MIN` over 24 hourly lows |
| `midpoint` | 24 | `MIDPRICE` over 24 hours: (highest high + lowest low) / 2 |
| `hourly_return` | 2 | `ROCP` over 1 hour: latest / prior − 1, a fraction |
| `four_hour_drift` | 5 | `ROCP` over 4 hours: latest / four-hours-prior − 1, a fraction |

`pullback_from_high`, `pullback_depth` and `midpoint_distance` are retired in
v3: each was one division of two numbers and no library function returns it.
A request for one gets status `unsupported`, and the snapshot supplies its
ingredients instead (`highest_high_20` or `midpoint`, together with
`latest_close`). The range midpoint is distinct from the current bid/ask
midpoint (`mid`). Ratios are fractions, not percentages. The library works in
binary floats, so v3 turns each result into exact decimal text by one stated
rule (rounded half-even to 12 significant digits). The four selection fields are
withheld, not rounded, for prices with more than 11 significant digits. Wire
prices, volumes and output values retain decimal strings. The accuracy bounds
and the retained-snapshot tests are in
[the indicator menu](INDICATOR-MENU.md#hourly-price-features-v3). Before v3 the
same fields were hand-written Decimal arithmetic (precision 50, ROUND_HALF_EVEN);
that code is gone and survives only as a test oracle.

## Point-in-time context and retained evidence

Each candle retains the exact UTC open/close, native decimal prices/volume,
trade count, stable source reference, source hash and actual post-response
receipt timestamp. Receipt time is availability; the exchange's candle close
does not imply the graph possessed the data then. Exchange publication time
is unknown. Public observations and synthetic fixtures occupy distinct source
namespaces and are never merged.

All feature windows of one snapshot end at a shared anchor: the newest
completed candle actually available at the context's as-of time, when it is
at most two hours before the latest completed UTC hour. Otherwise the anchor
is the latest completed UTC hour itself. A field needs its exact contiguous
documented hourly slots ending at the anchor; software does not lengthen a
lookback to hide a gap, compute partial windows or carry candles forward.
Per-field context reports `ready`, `stale` (complete through the anchor and
computed there, while newer completed hours are still pending), insufficient
history, gaps or unsupported requests. `missing_close_times_utc` lists the
gaps inside the field's window and then the pending hours after the anchor;
the window start/end times show the anchored window. An internal gap
withholds only the fields whose window covers it. The snapshot-level `stale`
flag and `latest_close_time_utc` still compare the newest available candle
with the latest completed hour. Unready values are omitted rather than filled
with zero or an invented trend. This anchoring was introduced in feature
version `hourly-price-features-v2` and `hourly-price-features-v3` keeps it;
retained `hourly-price-features-v1` snapshots ended every window at the latest
completed hour, so one missing newest candle withheld every field.

Research and Trader obtain requested fields from their pinned active strategy
templates and mandate. Ready and stale values, source/candle identities,
availability, feature version and explicit missing-data metadata enter their
actual retained worker snapshots and model requests. Independently of the
requested fields, each snapshot also supplies `recent_candles`: the completed
candles available at as-of among the 24 hourly slots ending at the latest
completed hour, oldest first, as decimal-string open/high/low/close/volume with
open, close and receipt times. `latest_close` is the newest of them (null, not
zero, when none is available) and `recent_missing_close_times_utc` names the
unavailable slots; nothing is filled. The snapshot's candle identities and
source hash cover these values. Research can cite the supplied derived
feature snapshot; its finding preserves unknown publication time. Existing
financial, fresh-quote, mandate and discretionary-trading controls still apply.

Additive migration 0017 creates an append-only hourly-candle table without
rewriting financial records or earlier snapshots. A changed candle creates
later evidence. Future closes and later actual receipts cannot enter an earlier
as-of query; persisted worker contexts and requests remain immutable.
The store trusts software-supplied receipt timestamps. This collector supplies
current post-response times, and exposes no backdated import route. Introducing
out-of-order imports with historical receipt claims would need a separate
admission/confirmation contract: deduplicated unchanged receipts are not a
complete observation timeline for such historical recomputation.
Collection is bounded, while retention accumulates without deleting earlier
decision evidence. This path prepares inputs; funded operation, venue
conformance, forward economics, protected-host/image checks and live authority
remain separate pending gates.

## Multi-interval candles for many pairs (Phase D1)

The hourly BTC/ETH path above is unchanged and still feeds the Trader's hourly
features. Phase D1 adds a separate, much larger candle store for every Kraken
USD pair at 1, 5, 15 and 60 minutes, in its own private SQLite file:

- [Candle store contract](CANDLE-STORE.md) (D132): append-only revisions,
  receipt-time availability, point-in-time and replay reads, coverage reports.
- [Pair catalog and classification](PAIR-CATALOG.md): which pairs exist and
  which are stablecoins, yield tokens or currencies (D131).
- [Public candle collection](KRAKEN-CANDLE-COLLECTION.md): the bounded,
  rate-limited collector and its gap reports.
- [Archive import](KRAKEN-ARCHIVE-IMPORT.md) and
  [Trades backfill](KRAKEN-TRADES-BACKFILL.md): months of history from a
  locally supplied Kraken file, recent gaps from public trades, and a report of
  where the sources differ. Delisted pairs are absent from Kraken's data, so
  coin-selection tests on that history are optimistic.
- [Indicator menu](INDICATOR-MENU.md) (D118, D136): standard indicators from
  TA-Lib, cross-checked against independent libraries.
