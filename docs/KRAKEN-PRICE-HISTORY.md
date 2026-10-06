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
versioned software definitions, not validated profitable parameters.

| Field | Required contiguous completed candles | Formula / units |
|---|---:|---|
| `sma_20` | 20 | Mean hourly close, USD per BTC/ETH |
| `sma_50` | 50 | Mean hourly close, USD per BTC/ETH |
| `pullback_from_high` | 20 | (Highest hourly high − latest close) / highest high; unitless fraction |
| `range_high` | 24 | Highest hourly high, USD per BTC/ETH |
| `range_low` | 24 | Lowest hourly low, USD per BTC/ETH |
| `midpoint` | 24 | (Range high + range low) / 2, USD per BTC/ETH |

The range midpoint is distinct from the current bid/ask midpoint (`mid`).
For starter-template compatibility, `hourly_return` requires two closes and
equals latest/prior − 1; `four_hour_drift` requires five closes and equals
latest/four-hours-prior − 1. `pullback_depth` uses the same 20-hour pullback
fraction; `midpoint_distance` uses the 24-hour range and equals
(latest close − range midpoint) / range midpoint. Ratios are fractions, not
percentages. All feature arithmetic uses a local Decimal context, precision
50 and ROUND_HALF_EVEN; repeating division is rounded under that documented
policy. Wire prices, volumes and output values retain decimal strings.

## Point-in-time context and retained evidence

Each candle retains the exact UTC open/close, native decimal prices/volume,
trade count, stable source reference, source hash and actual post-response
receipt timestamp. Receipt time is availability; the exchange's candle close
does not imply the graph possessed the data then. Exchange publication time
is unknown. Public observations and synthetic fixtures occupy distinct source
namespaces and are never merged.

Feature windows end at the latest completed UTC hour at the context's as-of
time. A field needs its exact contiguous documented hourly slots; software
does not lengthen a lookback to hide a gap. Per-field context reports ready,
insufficient history, gaps or unsupported requests, including missing slots.
Missing tail data is visibly stale. Unready values are omitted rather than
filled with zero or an invented trend.

Research and Trader obtain requested fields from their pinned active strategy
templates and mandate. Ready values, source/candle identities, availability,
feature version and explicit missing-data metadata enter their actual retained
worker snapshots and model requests. Research can cite the supplied derived
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
