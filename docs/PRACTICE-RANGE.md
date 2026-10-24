# Practice range (T29)

Status: implemented and tested in source on 2026-10-16 by cloud package W2-A (branch `claude/t29-practice-range`,
for `claude/day-trader-engine-20261012`). Not deployed. Decisions D165–D169 are proposed in
`planning/handoffs/W2-A.md`; the integrator copies them into the decision log. They build on D118, D119, D126,
D127, D138 item 5 and the fill engine decisions D145–D149 ([FILL-ENGINE.md](FILL-ENGINE.md)).

In plain words: before the Trader uses a strategy, software replays it on months of stored price history, with
the real fee tables and the same cautious fills as the paper account, and writes a report: how many trades, how
many won, the average win and loss, the result after fees compared with keeping the money in cash and with simply
holding Bitcoin, the worst fall from a high point and the longest run of losses. The strategy is tuned on an
earlier stretch of days and then checked once on a later stretch it has never seen; software refuses to tune on
those later days. A report is evidence for the Trader to weigh. It is not a pass mark, not a claim the strategy
makes money and not permission to trade real money.

| Part | Path |
|---|---|
| Strategy grammar and study windows | `src/trade_graph/contracts/practice_range.py` |
| Example strategies (data, not code) | `strategies/practice/*.json` |
| One indicator value per candle, equal to the engine's | `src/trade_graph/application/indicators/rolling.py` |
| The replay | `src/trade_graph/application/practice_range/replay.py` |
| Studies, reports, reruns | `src/trade_graph/application/practice_range/store.py`, `service.py`, `summary.py` |
| Result sums shared with the scorecard (T42) | `src/trade_graph/kernel/trade_metrics.py` |
| State tables (migration 0030) | `src/trade_graph/adapters/persistence/practice_range_schema.py` |
| OKX public candles | `src/trade_graph/adapters/market/okx_candles.py`, `application/okx_history.py` |
| Candle source `okx_public_candles` (candle database 0002) | `src/trade_graph/adapters/persistence/market_data_schema.py` |
| Commands | `src/trade_graph/market_data_cli/practice_range.py`, `market_data_cli/okx.py` |
| Market types at each decision and the report's split by them (W11-A) | `src/trade_graph/kernel/market_type.py`, `application/market_types/labeller.py`; see [MARKET-TYPES.md](MARKET-TYPES.md) |
| The strategy lab inside the service (W11-B, C11): the Trading desk's requests, queue, worker and `lab` context | `src/trade_graph/contracts/strategy_lab.py`, `src/trade_graph/application/strategy_lab/`; state migration 0051 (`adapters/persistence/strategy_lab_schema.py`) |

## Strategy versions

A strategy version is a JSON document in a fixed grammar (`PracticeStrategy`). It holds no code, formula or
expression. It names up to 8 indicators from the T27 menu with bounded settings, and states:

- `interval_minutes`: 1, 5 or 15 (D119: 15-minute candles or finer);
- `entry.all_of`: up to 8 conditions that must all hold at a candle's close. A condition compares two operands,
  `{"candle": "close"}` (any field of the latest completed candle), `{"indicator": "<name>", "output": ...}` or
  `{"level": "100"}`, with `above`, `below`, `crosses_above` or `crosses_below`. Both sides are rounded to the 12
  significant digits the indicator library vouches for, as alarms compare (D136); an indicator with no value
  (warming up or gapped) never satisfies a condition. Since W11-A an entry condition may also be
  `{"market_type": "rising", "relation": "is"}` (or `"is_not"`, with `rising`, `sideways`, `falling` or `unclear`):
  the coin's market type at the decision, from the same function the hourly scan uses (D288,
  [MARKET-TYPES.md](MARKET-TYPES.md)); `unclear` is a label like the others;
- `entry.order`: a market buy at the next open, or a limit buy at an operand's price less `offset_fraction`,
  resting for `expires_after_bars` candles;
- `exit`: a stop `stop_loss_fraction` below the entry (required: every position carries a protective exit, D117),
  an optional take-profit, optional rule exits (`any_of`) that sell at the next open, and an optional
  `max_holding_bars`;
- `sizing.equity_fraction`: the share of the cash one entry may spend, at most 1. There is no side to choose, no
  borrowing and no short selling (D127).

A version is registered once (`practice_strategy_versions`): `strategy_id@version` with the SHA-256 of its canonical
document. Registering the same reference with different content is refused; a change is a new version. The two
examples in `strategies/practice` are executable 15-minute readings of the prose templates in
`strategies/templates`; they are unproven starting points, not advice.

## Studies: tune earlier, confirm later, never reuse

A study (`StudyPlan`) fixes, before any replay, a tuning window and a later confirmation window, each of whole UTC
days (start included, end excluded; the trading day starts at 00:00 UTC, D138 item 6). The store and SQL triggers
enforce (D166):

- the confirmation window starts no earlier than the tuning window ends;
- a strategy family never tunes on days any of its studies set aside for confirmation, and never confirms on days
  it tuned or confirmed on before;
- a tuning replay stays inside its study's tuning window (sub-windows are allowed, for walk-forward checks);
- a confirmation replays the whole confirmation window, and the first version confirmed there spends it: another
  version needs a new study with a later, untouched window;
- each confirmation report lists what was tried in the study (`selection`: tuning runs and versions), so selection
  is visible.

D119's "at least three months" is read as 90 replayed days, tuning plus confirming; every report says whether its
study meets it (`meets_three_month_minimum`).

## The replay

One replay is one strategy version, one candle series, one window and one cost profile. Candles are read only
through `CandleStore.window`, point-in-time as of a fixed data cut-off (`data_as_of`, the time of the run), so a rerun
with the same cut-off reads exactly the same candle revisions: the store is append-only and never backdates a
receipt. Decisions use event time only: at each candle the indicators are the values the T27 engine itself would
give for that candle (`rolling_latest`, checked float for float against `compute_indicator`), and orders placed at a
close can only fill from the next candle. Fills come only from the shared kernel fill engine with fill policy
`bar-replay@1` (D145–D148).

Each candle of the window, in order: a pending market sell (rule exit) at the open; the protective exit armed before
the candle (the stop assumed first); a pending market entry at the open, or a resting limit during the candle (a new
fill re-arms the exit on everything held and checks it against the same candle, where only the stop counts); then at
the close equity is marked, orders whose life ran out are cancelled, and the decision is made. Order lives count
candle slots, present or missing: a missing candle fills nothing and is reported. No order is placed at the window's
last close, because nothing after the window may be read.

One position at a time. An entry is sized at the taker fee plus entry slippage so a fill never needs more cash than
there is; a fill that would is refused, never borrowed. Without instrument rules quantities floor to 0.00000001 and
prices are not rounded to a tick; with `--price-increment` and `--quantity-increment` order levels and sizes follow
the venue's tick and lot, and the venue's minimums refuse an entry instead of resizing it. A position still held at
the end is valued at the last close less exit slippage and the taker fee; it is reported as open, not as a trade.

Replays use the newest revision of each cost profile: the fees a strategy would pay if it were used now. The report
names the revision and its hash.

## The report

Stored in `practice_range_runs.report_json` with its run, study, phase and profile:

- `label`: `evidence_for_trader_discretion`, `pass_mark: null`, `live_authorization: false` and the sentence that a
  report is not a pass mark, not a claim of profitability and not permission for real money (D138 item 5);
- `evidence`: the candle runs' labels (`synthetic_fixture`, `recorded_public_fixture`, `local_archive_file`,
  `live_public`, ...), whether real market data was used, and `paid_calls: 0`, `orders_sent: 0`;
- `venue`: the data's venue and the profile's; Kraken prices under the OKX profile are marked as a stand-in (D126);
- `currency_note` when the quote currency is not USD (OKX's USDT pairs are compared as given, not converted);
- `result` (hashed as `result_sha256`): the request (strategy reference and hash, symbol, source, window, data
  cut-off, cost profile and fill policy references, hashes and rates, capital, instrument rules, benchmark series),
  the data facts (candles read, missing ranges, runs, a hash of every candle revision read), the indicators with
  their menu versions and library versions, every fill with its purpose, the end valuation, a daily equity curve
  with the hash of the full curve, event counts, and the metrics;
- since W11-A (D289), `result.request.market_type_rules` (reference, hash, candle size) and `result.market_types`:
  the 60-minute candles the labels read, how many decision candles were of each market type, and `by_market_type`,
  the trades, wins, losses and result after fees by the market type at the decision that placed each entry, a
  position still held at the end in its entry's type with its valued result, so the rows sum to the report's totals.
  A report made before W11-A has neither and is still reproduced exactly by `practice-range-verify`
  ([MARKET-TYPES.md](MARKET-TYPES.md)).

`practice-range-verify` replays a stored report from its own inputs and records whether the result hash is identical
(`practice_range_verifications`). A changed strategy document, profile revision, fill policy or candle input shows
up as not reproduced, with the parts that differ.

## Metrics (shared with the scorecard, T42)

`kernel/trade_metrics.py`, pure Decimal functions in the protected kernel:

- `TradeFill` (principal and fee in the quote currency; `from_sim_fill`), `round_trips(fills)`: a trade runs from
  flat to flat on one symbol, partial fills and every fee included, result `sold - bought - fees`; the rest are open
  positions. Selling more than is held is refused (long only).
- `summarize(currency, starting_equity, ending_equity, fills, equity_points, benchmarks)` → `TradeMetrics`: trades,
  wins (result above zero), losses (below), flat trades, win rate, total and average win and loss, fees paid, the
  worst drawdown from a running peak (fraction, amount, times), the worst losing streak, the result against cash
  (cash earns nothing, D127) and against each benchmark.
- `hold_benchmark(...)`: spend the capital on one coin at the first price with the taker fee, floored to the lot,
  and sell at the last price with the fee.

Sums are exact; ratios and averages are rounded half-even to ten decimal places. The golden tests in
`tests/unit/test_trade_metrics.py` and `tests/integration/test_practice_range_replay.py` are checked by hand.
The paper scorecard (T42) uses the same sums on the ledger's fills: [SCORECARD.md](SCORECARD.md).

## The strategy lab inside the service (W11-B, C11)

Built on 2026-10-20 by cloud package W11-B (decisions D290–D294, state migration 0051; proposed in
`planning/handoffs/W11-B.md`). In plain words: the Trading desk (the Trader's role, D280) writes, replays and confirms
its own strategy versions inside the installed system, and is woken with the results; protected software keeps the test
fair. Paper only; no order, paid call or network request.

- **Requests** (`contracts/strategy_lab.py`, `LabRequest`). The desk's wake answer (`TraderWakeReply`) may carry
  `lab_requests`, as many as it likes (D116): `register_strategy` (a `PracticeStrategy` document as JSON text, so the
  reply stays a strict provider schema), `create_study`, `tune` (a coin, a source, an optional sub-window of the tuning
  window, cost profiles) and `confirm` (the whole confirmation window). `LabRequest.problem()` checks one request on its
  own (the grammar, missing fields, window order) when the event engine queues the answer's requests; a refused one is
  recorded with its reason at once. The store's study rules are checked when its turn comes. Nothing is dropped
  silently, and one bad request never sinks the rest of the answer.
- **The queue** (`application/strategy_lab/queue.py`, tables `strategy_lab_batches` and `strategy_lab_requests`). One
  batch per answer, the requests in order. A request moves `queued` → `running` → `done`, `refused` or `failed`; a
  restart re-runs a `running` one, a clean stop puts it back; ended is final; rows are never deleted.
- **The worker** (`worker.py`, `installation.py`). In the installed service, in its own thread like the price collector
  (D222): one request per step, reading the candle file through its own read-only handle. Replays run outside any
  transaction under the profiles asked for (all published ones when none is named) and always the account's own (the
  paper broker's, Kraken's starting tier by default), at one data cut-off, with the USD10,000 capital, no
  instrument rules (as in step L2) and the current market-type rules (W11-A, as `PracticeRange.run`; added by the
  wave 11 integration), so every lab report has the split by market type at entry and a version that requires a
  market type can be tested; then every report and the request's end are written in one transaction, so a
  request is never half recorded. Each start and each finished request is a `strategy_lab` activity (what ran, how it
  ended and why, the seconds spent, how many are queued and running).
- **The wake** (`lab_results`, D291). When every request of a batch has ended, the event engine's trigger source wakes
  the desk once; like a news alarm it keeps the desk's own check-in.
- **What the desk reads** (`context.py`, the `lab` context key). Its newest results with every report in plain words
  (`summary.report_lines`) and numbers (`summary.report_overview`), one per cost profile, so whatever a report says
  reaches the desk unchanged (W11-A's split by market type included); the queue; per family the versions, studies,
  tuning and confirmation runs, confirmed versions, newest studies and the **newest untouched confirmation window**
  (`PracticeRangeStore.confirmation_offer`: walk-forward, new days from the collector become new confirmation days);
  all within 24 KiB of the 128 KiB request.
- **First start** (`starting.py`, D293). An installation without any registered strategy registers the two examples,
  packaged in `application/strategy_lab/starting/` because the protected image carries only `src/trade_graph` (a test
  keeps them equal to `strategies/practice`). The desk asks for their confirmations itself.
- **Protected** (D294). `contracts/practice_range.py`, `application/practice_range/`, `contracts/strategy_lab.py` and
  `application/strategy_lab/` are protected paths: the deployed Engineer cannot change the rules that keep the test fair.

## For day plans (T34)

D138 item 5: a report must exist for a strategy version before it enters a day plan; there is no pass mark.
`PracticeRangeStore.has_report(strategy_ref, confirmed=False)` and `reports_for(strategy_ref, phase=None)` answer
that; `summary.report_lines(record)` gives the plain sentences to show the Trader (with one sentence per market type
since W11-A), and `summary.report_overview(record)` the few numbers a list shows, with its `by_market_type` rows. Whether a tuning report is enough
or a confirmation is needed is T34's choice (the recommendation is a confirmation report).

## OKX candles

OKX Europe's fees are a cost profile (D126); smaller coins are confirmed on OKX's own prices because their spreads
differ from Kraken's. OKX candles are their own source, `okx_public_candles`, never merged with Kraken's (D132). The
candle database version 0002 widens the source checks for it by rebuilding `candle_series` and `candle_runs` the way
SQLite documents (foreign keys off for the copy, then a full foreign-key check before commit); every existing row
keeps its id.

- `candles-okx-import --recording FOLDER` stores a recorded folder (`manifest.json` plus `page-NNN.json`) under the
  label its manifest gives (`synthetic_fixture` or `recorded_public_fixture`); receipt time is the import time.
- `candles-okx-fetch --inst-id SOL-USDT --interval 15 --pages N [--record FOLDER]` pages back from the newest candle
  on `https://www.okx.com/api/v5/market/history-candles` (GET, no key), 0.25 s between requests, and stores each page
  as `live_public`. It is the only practice-range command that uses the network.

The response format (newest first, `[ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]`, `confirm` `"0"` for the
forming candle) is this build's reading of OKX's public documentation: okx.com was unreachable from the cloud on
2026-10-16. The only OKX fixture is synthetic and labelled so (`tests/fixtures/okx`). Step L2 records a real one.
OKX gives no trade count or volume-weighted price: OKX candles carry `trade_count` 0 (unknown for this source) and
no vwap.

## Commands

```
uv run trade-graph practice-range-register --database runtime/trade_graph.sqlite \
  --strategy strategies/practice/slow-trend-pullback.v1.json
uv run trade-graph practice-range-study --database runtime/trade_graph.sqlite --study-id stp-2026q3 \
  --strategy-id slow-trend-pullback --tune-start 2026-07-01 --tune-end 2026-09-01 \
  --confirm-start 2026-09-01 --confirm-end 2026-10-01
uv run trade-graph practice-range-run --database runtime/trade_graph.sqlite \
  --market-database runtime/market_data.sqlite --study-id stp-2026q3 --phase tune \
  --strategy slow-trend-pullback@1 --symbol BTC/USD --source kraken_ohlcvt_archive
uv run trade-graph practice-range-report --database runtime/trade_graph.sqlite --strategy slow-trend-pullback@1 \
  --format text
uv run trade-graph practice-range-verify --database runtime/trade_graph.sqlite \
  --market-database runtime/market_data.sqlite --run-id <run id>
```

`--profile` (repeatable) takes `all` (the default: every published profile), a profile id or `id@revision`.
`--capital` defaults to 10000 (the virtual USD10,000 account). `practice-range-report` opens the database read-only.

## Step L2 (owner's PC)

1. Import Kraken's archive for the candidate coins at 15 minutes (`import-kraken-archive`), so at least three months
   of history exist.
2. Record OKX history for a few smaller coins with `candles-okx-fetch --record`, and commit a trimmed recorded
   fixture with a README section saying when it was recorded.
3. Register the example strategies, create a study per family with about 60 tuning and 30 confirmation days, run
   tuning and confirmation under all profiles, and keep the text reports.

## Known limits

- One replay reads at most 100,000 candles (the store's limit): about 69 days at 1 minute. A three-month study at
  1 minute needs tuning and confirmation windows under that each.
- Exit quantities are what was bought; the fill engine's volume share can produce quantities off the venue's lot,
  which a real venue would round.
- Kraken has no history for delisted coins, so coin-selection results are optimistic (D119).
- A tuning replay reads indicator warm-up candles from before its window. If those fall on an earlier study's
  confirmation days, they shape the first indicator values only, never a fill or a decision.
