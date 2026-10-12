# Day-trader engine direction

Status: owner-approved direction, 2026-10-12. This is a design record. No
runtime behavior has changed. Decisions D114–D127 are in
[DECISIONS.md](DECISIONS.md).

## Why the engine changes

The first continuous paper run (2026-10-07 08:13Z to 2026-10-10 02:02Z,
about 66 hours) made 17 Trader decisions, all `no_action`, and placed no
orders. The owner's goal is a profitable day trader. Analysis of the retained
database and the deployed source (`3504248`) found these causes:

| Cause | Effect in the run |
|---|---|
| The hourly candle was stored at about :40 and the Trader woke at about :13. One missing slot withholds every feature (`application/price_history.py`). | 7 of 17 turns had no features. |
| The Trader context never includes the latest completed close, but the `slow-trend-pullback` entry and exit are defined on it. | It blocked the only qualifying setup (BTC, 2026-10-10 01:56Z). |
| Authorizing more exposure raises `StaleState("provisional valuation")` whenever the USD→EUR reference rate is not today's (`application/execution.py`, `adapters/market/paper_feed.py`). | Buys were impossible from about 00:00Z to 15:00Z on weekdays and all weekend: 11 of 17 turns. |
| A failed Trader task sets a system `MANAGE_ONLY` pause that does not resume by itself (`application/paper_service.py`). | One refused buy would have stopped every later turn. |
| The Trader ran on a fixed 4-hour schedule with no event wake-ups, although D09 intended them. | Reaction time was up to 4 hours. |
| One slow, long-only template on BTC/ETH hourly data. The default mandate `strategy_ids` contains the test leftovers `slow-trend` and `s` (`application/authority.py:204`). | There was nothing to do in a falling market. |
| Research saw only the same features, and its findings expired after about 47 minutes. | Research input was empty on all 17 turns. |
| Optimisation and Engineer can edit only the two strategy JSON files. | The graph could not repair any of the above. |

Fees: paper charges maker 0.40% and taker 0.80% (D14), which equals Kraken's
tier 1. A 30-day replay of the hourly pullback rule (Kraken public candles,
2026-09-12 to 2026-10-12) lost money after fees on both BTC and ETH. Venue
costs are compared in "Venues and cost profiles" below; risk limits are still
open.

## The daily cycle

1. **Day start.** Software scans all Kraken USD pairs and produces a shortlist.
2. **Focus coins.** Research reviews the shortlist with news context and names
   the day's focus coins.
3. **Day plan.** For each focus coin, the Trader writes a plan: strategy,
   indicators chosen from the menu, alarms, and orders. Orders are resting
   entries, each with a protective exit.
4. **Install and sleep.** Software validates and installs the plan. The Trader
   sleeps.
5. **Watch and wake.** Software watches every price update. It wakes the
   Trader when an alarm fires, an order fills, a protective exit triggers, or
   the Leader asks.
6. **Research refreshes** during the day on its own judgement or when the
   Leader asks.
7. **Leader rounds.** The Leader skims the news broadly, checks on the
   departments and asks Research for deep dives.
8. **Day end.** Learning reviews the day's decisions and the results after
   fees.

## Component decisions

### Fix list first (D114)

Before new features:

- Supply the latest completed close and a short recent candle series to the
  Trader.
- Emit each feature from the candles that are available instead of
  withholding all of them. Run decisions after the candle they need is stored.
- Stop stale EUR *reporting* FX from blocking USD-native exposure checks.
- Record a refused order as a rejection the Trader can see, not as a task
  failure followed by a system pause.
- Remove the leftover strategy IDs from the mandate default.

### Wake-ups: no caps (D115, D116)

The fixed 4-hour Trader timer stops being the main wake mechanism. Alarms,
fills, protective exits and Leader requests wake the Trader. Software, not the
model, watches the market, which costs no tokens.

There is no limit on how often the Trader wakes or acts. The owner tunes the
graph from what it actually does. HQ shows every wake, its reason and the
allowance each department uses.

These are engineering defaults, not caps:

- An alarm fires once and is then spent until the Trader re-arms it.
- Alarms that fire while the Trader is awake are delivered together.

The existing subscription-allowance floor stays because it protects the
owner's shared account. The owner may change it.

Each department chooses its own next check-in time (D123). The day start
always runs. A department that names no time is checked in at the next day
start. HQ shows each department's chosen next time.

### Orders and protection (D117)

- The Trader may leave entries resting as limit orders, which pay the maker
  fee.
- Every open position carries a protective exit while the Trader sleeps.
- Software keeps enforcing the money envelope (per-coin and total exposure).
  The daily-loss value is still open (see "Pending owner decisions").
- On a pause, resting entries are cancelled and protective exits remain.

### Indicators and charts (D118)

- Software computes indicators with a proven open-source library. TA-Lib is
  preferred: its wheels bundle the C library from 0.6.5 and cover Python
  3.12 on Linux.
- Inputs are Kraken candles at 1, 5, 15 and 60 minutes. The Trader picks
  indicators and parameters from a menu and receives numbers, not chart
  images.
- Indicator values are analytical and may be floats. Prices, quantities, fees
  and balances stay Decimal.
- No indicator formula is written in-house (owner concern, 2026-10-12). The
  engine's own code only:
  - supplies completed candles, never the still-forming one;
  - reports a missing candle per indicator;
  - provides enough warm-up history;
  - compares results with alarm levels.
- Tests cross-check library output against an independent second library and
  against fixed reference values. Documented differences from TradingView
  (seeding of smoothed averages) are acceptable.
- Price-only protective exits (stop-loss, take-profit) use the venue's native
  conditional orders where available, and the paper broker simulates them.
- TradingView cannot be the engine. It has no public API for creating alerts,
  and webhooks need a paid plan, a public endpoint and limited alert slots, so
  the Trader could not set its own alarms.
- HQ charts use TradingView Lightweight Charts (Apache-2.0, attribution link
  required) to show candles, the chosen indicators, alarm lines and orders.

### Practice range (D119)

Software replays a proposed strategy on Kraken history before it enters a day
plan.

- **History:** at least three months, at 15-minute bars or finer.
- **Simulation:** tiered fees and conservative limit fills, where a touch is
  not a fill.
- **Report:**
  - number of trades
  - win rate
  - average win and loss
  - result after fees versus holding cash and holding BTC
  - worst drawdown and worst losing streak
- **Validation:** tune on an earlier period, then confirm on an untouched
  later period.

Data sources:

- Kraken's downloadable OHLCVT archive: every pair from inception, 1-minute to
  daily, updated quarterly.
- The public `Trades` endpoint, to fill the gap since the last quarterly file.
- The engine's own collector from now on.

Delisted pairs are missing from Kraken's data, so coin-selection tests are
optimistic.

### Coin scanner (D120)

Software ranks all Kraken USD pairs every hour by:

- traded volume
- swing size relative to trading cost
- spread
- trend

It excludes thin pairs. Research receives a shortlist of about 10, adds news
context and picks the focus coins. The owner approved this on 2026-10-12. The
paper symbol scope widens through an owner policy revision when this is
implemented.

### Leader (D121)

The Leader works like a human manager. It runs rounds of broad, shallow
reading: crypto market news and news about AI trading agents. It avoids rabbit
holes and asks Research to go deeper. It may wake the Trader with a note. It
never places or approves individual trades.

Ideas that affect the strategy go to Research or Optimisation and through the
practice range before use. The system does not rewrite itself while it runs.

### Research web access (D122, D124)

Perplexity rather than Codex web search (D122):

- Software stores each search result (title, link, publication date and
  snippet) as a snapshot source before the model's finding is accepted. That
  fits the existing rule that a finding may cite only sources in the persisted
  snapshot. The Codex adapter records only search IDs, not what was found.
- It uses no Codex allowance, which trading and the owner's own coding
  sessions share.

Owner decision (D124):

- Research and the Leader's news rounds think with Claude Sonnet 5.5
  (`anthropic/claude-sonnet-5-5`) through Perplexity's pay-as-you-go Agent
  API, and search through Perplexity. The Trader stays on the Codex
  subscription.
- A Perplexity Pro or Max subscription cannot run software, so the API is
  required.
- Every search result the model sees is recorded as dated evidence. Prefer
  software-run searches offered to the model as a registered tool (D20). Use
  Perplexity's built-in `web_search` only if its returned results can be
  captured equally.
- Published prices on the Agent API models page on 2026-10-12:
  - $2 per million input tokens
  - $10 per million output tokens
  - $0.0025 per `web_search` call
  - $0.0005 per `fetch_url` call

  Record them as a dated price card.
- During testing there is no Perplexity spending limit (D125). Every call
  still gets a receipt, and HQ shows the actual spend. The owner adds a
  budget after seeing natural costs. Perplexity's prepaid credit, with
  automatic top-up off, is the outer bound.
- The test key is stored outside Git in an owner-only file (D125). It is
  never committed or logged, and the owner revokes it after testing.

### Venues and cost profiles (D126)

The 2026-10-12 venue session compared venues open to an EU account. Its
inputs were an owner-run Perplexity study, corrected against official fee
pages: Bitstamp's real standard fees are 0.30%/0.40%, not the stablecoin
column's 0.02%/0.04%, and Nordnet does have an order API. The measure is the
typical daily swing divided by the cost of one buy and one sell, using limit
orders (BTC 2.7%, mid-size coins about 6%).

| Profile | Round-trip cost (limit / market) | Swing ÷ cost, BTC / mid-size coins |
|---|---|---|
| Kraken spot, starting tier | 0.80% / 1.60% | ~3× / ~8× |
| Kraken spot, after $10k 30-day volume | 0.44% / 0.76% | ~6× / ~14× |
| OKX Europe spot (official notice, 2026-09-25) | 0.20% / 0.40% | ~13× / ~30× |
| US large-cap stocks at Interactive Brokers, cash account | ~0.03–0.05% | ~50–80× |

The practice range replays each strategy under these cost profiles:

- Kraken spot, at the current tier and after the volume discount.
- OKX Europe spot. Kraken prices stand in for the main coins; smaller coins
  are confirmed with OKX's own public data because their spreads differ.
- US stocks at Interactive Brokers, later. This needs stock history and
  market-hours handling.

A new venue connection is built only if a strategy's evidence depends on that
venue's costs. Bitvavo (0.30%/0.50%) is the euro-native runner-up. Bitstamp,
Coinbase and Nordnet bring no cost advantage. Crypto futures (0.04%/0.10%) are
excluded by D127.

### No riba (D127)

The owner accepts anything that improves profit as long as it is not
considered riba. Under mainstream standards (the OIC International Islamic Fiqh
Academy resolution 63 (1/7) of 1992, and AAOIFI Shariah standards as reported
by secondary sources), the engine excludes:

- futures and perpetuals, including their periodic funding payments;
- conventional short selling;
- margin and any other interest-bearing borrowing;
- lending, "earn" and interest on idle balances (these stay switched off, and
  the engine flags any interest it receives).

Spot crypto and cash-account stocks remain. The existing long-only and
no-leverage rules stay. The owner may revise this with a scholar's opinion.
With shorting excluded, a falling market means holding cash. The scanner's
breadth partly compensates, because some liquid coins usually rise while the
majors fall. Bybit's certified Islamic Account (spot-only) is the only
dedicated option found; whether an EU account can use it is unverified.

## Pending owner decisions

- A Perplexity budget, once testing shows natural costs (D125).
- Daily loss limit and per-coin exposure.

## Unchanged

- Paper is the default.
- Paid calls stay disabled until the owner sets a budget.
- Live trading needs separate authorization.
- Money stays Decimal.
- External research is data, not authority.
- Credentials never enter model context or Git.
