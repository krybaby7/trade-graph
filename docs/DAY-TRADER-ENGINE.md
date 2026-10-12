# Day-trader engine direction

Status: owner-approved direction, 2026-10-12. This is a design record. No
runtime behavior has changed. Decisions D114–D124 are in
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
2026-09-12 to 2026-10-12) lost money after fees on both BTC and ETH. Coins,
fees and risk limits are a separate owner session (see "Deferred").

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
  The daily-loss value is set in the deferred session.
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
- The owner sets a monthly research budget, which is a money limit and not an
  activity cap. Spending uses the existing reservation and receipt
  accounting.
- The key goes in a private runtime file through a local command. It never
  passes through chat, Git or model context.

## Pending owner decisions

- Monthly research budget for Perplexity, and installing the key privately
  (D124).
- Daily loss limit, per-coin exposure and the paper fee ladder (deferred
  session).

## Deferred: coins, fees and risk

The owner wants a dedicated session on coin choice, Kraken fees and ways
around them. The 2026-10-12 inputs for that session:

- Kraken spot tiers: 0.40%/0.80%, then 0.30%/0.60% from $2,500 of 30-day
  volume, then 0.22%/0.38% from $10,000.
- Kraken Futures tier 1: 0.02%/0.05%.
- A one-day scan in which the 40 most-traded coins had a median 24-hour swing
  of 7.5%, against 2.0% for BTC, with spreads near 0.07%.

## Unchanged

- Paper is the default.
- Paid calls stay disabled until the owner sets a budget.
- Live trading needs separate authorization.
- Money stays Decimal.
- External research is data, not authority.
- Credentials never enter model context or Git.
