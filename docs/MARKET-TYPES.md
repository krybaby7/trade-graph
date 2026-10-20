# Market types (C10)

Status: built and tested in source on 2026-10-20 by cloud package W11-A (branch `claude/c10-market-types`, for
`claude/day-trader-engine-20261012`). Not deployed. Decisions D285–D289 are proposed in `planning/handoffs/W11-A.md`;
the integrator copies them into the decision log. They build on D280 item 3, D118, D119, D120, D132, D136,
D165–D169 and D170–D174.

In plain words: once an hour, software says for each coin the team works with whether its price is **rising**,
**sideways** or **falling**, or that it cannot tell (**unclear**: too little history, a missing hour, no recent
price). No AI takes part. The practice range uses the very same rule, so every practice report now also says how
many trades a strategy made, and what it earned or lost after fees, when the coin was rising, sideways, falling or
unclear at the moment it decided to buy. A strategy may also say it only buys when the coin is, or is not, of a given
type. A label is information for the Trading desk, not advice and not a rule: software never refuses a day plan
because of it (D280 item 5).

| Part | Path |
|---|---|
| The four words | `src/trade_graph/domain/market_types.py` |
| The rules `market-type-rules@1` and the one labelling function (protected) | `src/trade_graph/kernel/market_type.py` |
| The split of a report's sums by group (protected) | `src/trade_graph/kernel/trade_metrics.py` (`split_results`) |
| The readings from stored candles, live and over a stretch | `src/trade_graph/application/market_types/labeller.py` |
| The hourly labels after each scan | `src/trade_graph/application/market_types/hourly.py` |
| The record and the newest label per coin | `src/trade_graph/application/market_types/store.py` |
| State migration 0050 | `src/trade_graph/adapters/persistence/market_type_schema.py`, `migrations/versions/0050_market_type_labels.py` |
| The practice-range condition and the report split | `contracts/practice_range.py`, `application/practice_range/replay.py`, `service.py`, `summary.py` |
| Command `market-types` | `src/trade_graph/market_data_cli/market_types.py` |
| Tests | `tests/unit/test_market_type_rules.py`, `tests/integration/test_market_type_labels.py`, `test_market_type_store.py`, `test_market_types_cli.py`, `test_migration_0050.py`, `test_practice_range_market_types.py`, `test_practice_range_compatibility.py`, `tests/unit/test_practice_range_market_type_condition.py`, `tests/property/test_market_type_properties.py`, `test_market_type_split_properties.py`; helpers in `tests/market_type_support.py` |

## The rules (`market-type-rules@1`, D285)

The rules are a versioned document with bounded settings. Its SHA-256 is pinned by a test, every stored label and
every report names the reference and the hash, and any change is a new version.

| Setting | Value | Bounds | Why |
|---|---|---|---|
| Candle size | 60 minutes | 15 or 60 | The collector keeps hourly candles for every eligible pair (the scanner reads them, D170) and Kraken's archive has them (D134); a market type is a matter of days, not minutes. |
| Average | `SMA` 168 of the close | 20–500 | One week of hours: every day of the week counts once. |
| Slope | the average now against 24 candles earlier | 1–168 | One day: is the weekly average higher or lower than a day ago? |
| Trend strength and side | `ADX`, `PLUS_DI`, `MINUS_DI` 14 | 7–50 | The menu's directional indicators (Wilder's usual setting). |
| Trend present | ADX at or above 20 | 10–40 | The label asks whether a trend is there at all, not whether it is strong; the other three checks must agree too. |
| Lag | 2 candles | 0–6 | Like the scanner's 2-hour rule (D170): a collector that is an hour or two behind still gives a label. |

A coin is **rising** when all four hold: ADX at or above 20, +DI above -DI (buyers ahead), the close above the
average, and the average higher than a day earlier. **Falling** is the mirror: ADX at or above 20, -DI above +DI, the
close below the average, the average lower than a day earlier. **Sideways** is every other case: no trend, or signs
that disagree. **Unclear** names its reason: `no_recent_candle` (the newest candle is more than 2 intervals behind
the moment, or there is none), `not_enough_candles` (fewer than 863 complete hourly candles in a row end at it),
`gap` (an hour is missing among them), `no_value` (an indicator gave none) or `evaluation_failed` (the candles could
not be read). `unclear` is a label like the others, never a guess.

**No in-house formula (D118).** Every number is a TA-Lib menu value from the indicator engine (`rolling_latest`, the
engine's own span, float for float the live engine's value). Software only compares them with each other and with a
level, after rounding both sides half-even to 12 significant digits, as alarms compare (D136). There is no slope
ratio or distance in percent: "higher than a day ago" is a comparison of two averages.

**History (D136).** ADX 14 is start-sensitive, so the engine reads 863 hourly candles for it (about 36 days); the
average and its slope need 192. A coin needs 863 complete hours in a row before its first label, and an hour missing
among them makes the label unclear until 863 complete hours follow it again. The collector's hourly series backfills
up to 720 hours after a break (Kraken's window), so live gaps are rare; Kraken's archive leaves out hours without a
trade, so thin coins are often unclear in replays. On the owner's PC the collector's hourly series reaches back to
2026-09-18 (L1), so labels are possible from about 2026-10-24.

## One function, point in time (D286)

`kernel/market_type.py` holds the rules and decides: `slot_verdicts` gives each hourly slot its verdict from the 863
slots that end at it, and `MarketTypeTimeline.at(moment)` gives the label at a moment from the newest candle that
closed at or before it, within the lag. The application only supplies the readings
(`application/market_types/labeller.py`). The hourly scan's live labels (`label_coin`) and the practice range's
replays (`read_timeline`) end in these two functions with the same rules; there is no second copy. A label never
uses a candle that closed after its moment, and a live label reads only candles received by its moment (point in
time, D132). A slot's verdict reads nothing outside its own 863 slots, so later candles never change an earlier label
(property-tested, including late candles and later revisions), and a replay sees the label the live function gives on
the same candles at every decision (tested at every decision of a four-day replay).

## Live labels (D287)

After each live coin scan is recorded, the installed service labels every shortlisted coin and every coin of the day
(today's focus coins, held coins, coins with a day plan or an armed alarm) at the scan's moment from the collector's
hourly candles (`kraken_public_ohlc`), and HQ's activity log notes the round (`market_types`: how many, by type,
which could not be read). A coin whose candles cannot be read is stored as `unclear` (`evaluation_failed`) and the
others carry on; a labelling failure is noted and never stops the scan or the collector. `scan-coins` labels the same
way after a live scan (without held coins, which only the service's books know) and prints the counts; a replay
labels nothing.

State migration 0050 adds `market_type_labels`: one row per coin per moment with the type, the reason when unclear,
the candle it rests on and its lag, the source and interval, the rules reference and hash, the scan that asked for
it, the weakest evidence label of its candles, and the whole label document (every reading, the menu versions, the
candle runs and their input hash) with its SHA-256. SQL triggers keep it append-only; a CHECK refuses a label resting
on a candle that closed after its moment and a reason on a label that is not unclear. A read whose document no longer
matches its hash is refused. Labels are not financial state, not in the checkpoint, the runtime evidence or the review
export, and not in any model context until W12-A.

**For W12-A.** `read_latest_market_types(connection, symbols=None, as_of=None)` in
`application/market_types/store.py` returns the newest label of each coin at or before `as_of`, as
`StoredMarketType` (`market_type`, `reason`, `as_of`, `candle_close`, `lag_bars`, `rules_ref`, `evidence`,
`document`, `sentence()`, `is_fresh(at, max_age=DEFAULT_LABEL_AGE)` with 2 hours), on any `sqlite3.Row`
connection, read-only included; a database before 0050 gives `{}`. `MarketTypeStore(database, clock).latest(...)`
is the same through the service's database, and `history(symbol, since=..., until=...)` gives a coin's labels over
time.

## In the practice range (D288, D289)

**The condition.** An entry may require the market type at the decision:

```json
{"market_type": "rising", "relation": "is"}
{"market_type": "falling", "relation": "is_not"}
```

among its `entry.all_of` conditions (up to the 8 conditions D165 allows). `unclear` is a label like the others, so
`is_not falling` also holds while the type is unclear; add `{"market_type": "unclear", "relation": "is_not"}` to
require a known type. Two `is` of different types, or `is` and `is_not` of the same type, can never hold together
and are refused. Exits do not read the type. Documents without the condition serialise exactly as before, so their
hashes are unchanged (the two shipped strategies keep `771329ed…` and `df0229bb…`).

**The labels in a replay.** With market-type rules in the request (every report the service makes now), the replay
reads the rules' 60-minute candles of the same symbol and source once, for the window and the 863 hours before it,
point in time at the run's cut-off like its own candles, and asks the live function for the label at each decision
candle's close. A strategy that reads the type needs the rules (refused otherwise). When the source has no 60-minute
candles (an OKX series fetched only at 15 minutes), every label is unclear and the report says so.

**The split.** Every new report has a `market_types` section in its hashed result: the rules reference and hash, the
candles it read (runs, evidence labels, input hash), how many decision candles were of each type, and
`by_market_type`: per type, in the order rising, sideways, falling, unclear, the trades, wins, losses, flat trades
and result after fees of the trades whose entry was decided in that type. A position still held at the end counts in
its entry's type with its valued result (end value after the selling fee, plus what it sold, less what it bought and
its fees), so the rows sum exactly to the report's trades, wins, losses and result after fees (tested, and
property-tested over random strategies). `summary.report_lines` reads it out per type, and
`summary.report_overview` gives it as `by_market_type` rows (`market_type`, `trades`, `wins`, `losses`,
`result_after_fees` as decimal text, `open_positions`) with `market_type_rules`. For example:

```
By market type at entry (software's label when the entry was decided; market-type-rules@1 on 60-minute candles):
Rising at entry: 14 trades (5 won, 9 lost), result after fees -260.07 USD; the coin was rising at 25.33% of the decision candles.
Sideways at entry: 30 trades (10 won, 20 lost), result after fees -684.46 USD; the coin was sideways at 50.66% of the decision candles.
```

**Older reports stay valid.** A report made before W11-A names no market-type rules; `practice-range-verify`
replays it without them and gets exactly the old result hash (tested on a report the base code stored), and its
plain sentences read as before, with no split; `report_overview` gives `by_market_type: []`. A new report's
verification checks the rules' hash too.

## Commands

```
uv run trade-graph market-types --database runtime/trade_graph.sqlite            # plain words, read-only
uv run trade-graph market-types --database runtime/trade_graph.sqlite --format json --symbol SOL/USD
uv run trade-graph market-types --database runtime/trade_graph.sqlite --as-of 2026-10-20T14:05:00Z
```

`market-types` opens the database read-only and never creates or upgrades it; a label older than two hours at the
moment read is marked as old.

## Not covered

- No real label has been made: the cloud has no market data. Golden and property tests use synthetic hourly series
  (`synthetic_fixture`); the local session labels real coins after the merge (the hourly scan in the installed
  service at L3, or `scan-coins` on the owner's candle file before it) and reruns the practice range on real prices.
- The desk does not see the labels yet (W12-A); HQ does not show them (W13-A); the scorecard does not split by them
  yet (W13-A, which can reuse `split_results`).
- Whether the defaults suit real coins (the 20 threshold, a week's average) is for the desk to judge from the split
  in its own lab runs; a change is a new rules version.
