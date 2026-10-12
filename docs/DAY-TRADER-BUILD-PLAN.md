# Day-trader build plan

Status: plan, 2026-10-12. Phase D0 (T23, T24) is built and tested in source
on 2026-10-13 (D129) but not deployed. Nothing in the running system has
changed.

This plan turns decisions D114–D128 into build steps. The reasons are in
[the day-trader engine record](DAY-TRADER-ENGINE.md) and
[the decision log](DECISIONS.md). The steps are tasks T23–T43 in
`planning/tasks.json`, release `R2`, phases `D0`–`D7`.

## In one minute

- First we fix what stopped the first paper run from trading.
- Then we give the engine much better data: more coins, finer candles,
  months of history and a menu of standard indicators.
- Then a practice range, where a strategy is replayed on history with real
  fees before it is used.
- Then a coin scanner, and an event engine that sleeps until something
  happens instead of waking every 4 hours.
- Research and the Leader get web news through Perplexity. The connection
  can be built early; they switch over once the event engine runs.
- HQ shows every wake-up, all AI spending and proper price charts.
- Last, a paper test run, judged by a scorecard.

Everything stays paper. Nothing here allows real trading or real spending,
except the Perplexity test calls you already approved (D125).

## The phases

| Phase | What you get | Tasks | Can start after |
|---|---|---|---|
| D0 Fix list | The first run's blockers removed | T23, T24 | Now |
| D1 Data | Many coins, 4 candle sizes, history, indicators | T25–T27 | D0 |
| D2 Practice range | Replays with real fees and cautious fills | T28–T30 | T28 after D0; the rest after D1 |
| D3 Scanner | An hourly shortlist of about 10 coins | T31 | D1 and T28 |
| D4 Event engine | Money limits, alarms, day plans, wake-ups | T32–T35 | Alarms after D1; the rest after D2 and D3 |
| D5 Research and Leader | Perplexity research and news rounds | T36–T38 | T36 after D0; T37 and T38 after D4 |
| D6 HQ | Wake log, AI spending, charts | T39–T41 | Alongside D4 and D5 |
| D7 Paper test | Scorecard and a scored paper run | T42, T43 | All of the above and your decisions |

T30 (US stocks) is marked "later". Nothing else waits for it.

The longest chain of tasks, one after another, is:
T23 → T25 → T26 → T31 → T32 → T34 → T35 → T37 → T43.
Everything else can be built alongside it.

## What changed from the suggested order, and why

Your suggested order (fix list, data, practice range, scanner, event engine,
Research and Leader, HQ, test run) is kept as phases D0–D7. Some tasks start
earlier or wait longer. These are the only changes:

1. **One fill simulator, built first in D2 (T28).** The practice range and
   the paper broker must fill orders the same cautious way. So T28 is shared,
   and the event engine's order installer (T34) depends on it.
2. **Scanner and practice range run side by side.** Neither needs the other.
   Both need the D1 data and the cost profiles from T28.
3. **Money limits come first in D4 (T32).** Software must know your daily loss
   limit and amount per coin before it rests any paper orders. Until you set
   them, order installation stays switched off. T32 also widens the paper
   coin list to the scanner's coins, so it waits for the scanner.
4. **The Perplexity connection (T36) is built right after D0.** It needs no
   new data, and testing it early shows real costs sooner. Research and the
   Leader switch to it (T37, T38) after the event engine, as you suggested,
   because they use its check-ins and wake-ups.
5. **HQ is three small pieces**, each landing next to the phase it shows.
6. **Day plans need a practice-range report (T34 needs T29).** D119 says a
   strategy is replayed before it enters a day plan.
7. **The scorecard reuses the practice-range sums (T42 needs T29).** One set
   of sums for both means paper results and replays compare fairly.
8. **US stocks (T30) wait.** They need a stock price source you have not
   chosen yet (decision 9), so T30 is marked "later".

## Phase by phase

### D0 — Fix list (D114)

**What you get.** The Trader sees the latest completed price and a short
series of recent candles. Each indicator appears when its own data is
there, instead of one gap hiding all of them. Decisions run after the
candle they need is stored. A stale euro rate no longer blocks dollar buys.
A refused order shows up as a rejection the Trader can see, not as a crash
followed by a safety stop. The leftover strategy names `slow-trend` and `s`
are removed from the default.

- T23 — Supply complete Trader market inputs on time.
- T24 — Unblock USD exposure checks, show refusals and clean mandate defaults.

**How we'll know it works.**

- Test data copying the first run's timing (candle stored at :40, Trader at
  :13) now gives the Trader the latest price and every ready indicator.
- A test copy of the 10 October BTC setup is visible to the Trader.
- A buy inside the limits is accepted on a weekend.
- After a refused buy, the next Trader turn still happens.
- Each cause from the first run has a test that failed before the fix.

The first run's database is private. Tests use synthetic copies of its
timing, never the real records.

### D1 — Data foundation (D118, D119)

**What you get.** Kraken candles at 1, 5, 15 and 60 minutes for every Kraken
USD coin. Months of history from Kraken's downloadable archive, with recent
gaps filled from Kraken's public trade list. A menu of standard indicators
computed by TA-Lib, a well-known open-source library.

- T25 — Collect 1-, 5-, 15- and 60-minute candles for many coins.
- T26 — Import Kraken's archive and fill recent gaps from public trades.
- T27 — Compute a TA-Lib indicator menu with cross-checked tests.

**How we'll know it works.**

- Every indicator matches a second, independent library and fixed reference
  numbers. Known small differences from TradingView are written down.
- At least three months of 15-minute history exist for the candidate coins.
  Every gap is listed.
- A candle that is still forming is never used.
- Prices and money stay exact decimals. Only indicator values may be
  ordinary numbers.
- Tests make no network calls. Real public downloads are labelled separately.

Note: coins that Kraken has delisted are missing from its history. Any test
of "which coins to pick" therefore looks better than reality.

### D2 — Practice range (D119, D126)

**What you get.** Before a strategy is used, software replays it on history
with real fees. Limit orders fill only when the price trades through them;
a touch is not a fill. Each replay runs under these cost profiles:

| Profile | Cost of one buy and one sell (limit / market) |
|---|---|
| Kraken spot, starting tier | 0.80% / 1.60% |
| Kraken spot, after $10k 30-day volume | 0.44% / 0.76% |
| OKX Europe spot | 0.20% / 0.40% |
| US stocks at Interactive Brokers, cash account (later) | about 0.03–0.05% |

- T28 — Share one cautious fill and protective-exit simulator, with dated
  cost profiles.
- T29 — Replay strategies in the practice range and report results. This
  includes OKX's own public prices for smaller coins, whose spreads differ.
- T30 — Add a US-stock cash-account profile at Interactive Brokers (later).

**How we'll know it works.**

- Fixed test cases pass: a touch does not fill; a price gap through a stop
  fills at the worse price; if a bar hits both exit levels, the loss is
  assumed.
- One strategy gives one report per cost profile. Rerunning gives the same
  numbers.
- The report shows: number of trades, win rate, average win and loss, result
  after fees against holding cash and holding BTC, worst drawdown and worst
  losing streak.
- Tuning uses an earlier period. Confirmation uses a later period that tuning
  never touched.

### D3 — Coin scanner (D120)

**What you get.** Every hour, software ranks all Kraken USD coins by traded
volume, typical swing compared with trading cost, spread and trend. Thin
coins are dropped. Research gets a shortlist of about 10.

- T31 — Rank Kraken USD coins hourly and shortlist candidates.

**How we'll know it works.**

- An hourly shortlist appears, with every number behind each ranking.
- Thin coins, stablecoins and riba-type products (D127) are excluded.
- Replays of the scanner on history are labelled "optimistic", because
  delisted coins are missing.
- No AI call is needed to make the shortlist.

### D4 — Event engine (D115–D117, D123)

**What you get.** The Trader writes a day plan per focus coin: strategy,
chosen indicators, alarms, and resting limit entries. Every entry carries a
protective exit. Software installs the plan, and the Trader sleeps. Software
watches every price update. It wakes the Trader when an alarm fires, an order
fills, a protective exit triggers or the Leader asks. Each department picks
its own next check-in. There are no caps on wake-ups or activity (D116).
Only money is limited.

- T32 — Enforce your money limits and the paper coin list.
- T33 — Watch prices with saved one-shot alarms.
- T34 — Install Trader day plans with resting entries and protective exits.
- T35 — Wake departments on events and on their own chosen check-ins.

**How we'll know it works.**

- Until your daily loss limit and amount per coin are recorded, the engine
  refuses to install orders.
- A test day that hits the loss limit stops new buying and cancels resting
  entries until the next day start. Protective exits stay.
- Every open paper position has a protective exit while the Trader sleeps.
  This is tested through fills, partial fills, restarts and pauses.
- A pause cancels resting entries and keeps protective exits.
- A scripted test day runs end to end: scanner, focus coins, day plan, alarm
  wake, fill wake, exit wake and the day-end review.
- An alarm fires once, then waits until the Trader re-arms it. Alarms that
  fire while the Trader is awake arrive together. These are alarm rules, not
  caps.
- The existing AI allowance floor still pauses new AI work while protection
  and records continue.

### D5 — Research and Leader news rounds (D121, D122, D124, D125)

**What you get.** Research and the Leader's news rounds think with Claude
Sonnet 5.5 through Perplexity's pay-as-you-go Agent API and search the web
through Perplexity. The Trader stays on the Codex subscription. Research
picks the day's focus coins from the scanner shortlist. The Leader skims
crypto news and news about AI trading agents. It asks Research to dig
deeper, and may wake the Trader with a note. It never places or approves
trades.

- T36 — Add the Perplexity Agent API connection with search evidence and
  receipts.
- T37 — Pick daily focus coins and refresh research on Perplexity.
- T38 — Run Leader news rounds and wake notes.

**How we'll know it works.**

- Every Perplexity call has a receipt. A call whose cost is not reported is
  shown as unresolved, never as free.
- Every search result (title, link, date, snippet) is saved before a finding
  may use it. A finding that cites anything else is rejected.
- Prices come from a dated price card: $2 / $10 per million input / output
  tokens, $0.0025 per search, $0.0005 per page fetch (published 2026-10-12).
- Tests pass with no key. A real call is opt-in and reported separately; a
  pretend call never counts as a real test.
- When you later set a money limit, reaching it pauses Research and Leader
  news rounds. The Trader, protection and records continue.
- The key is read only from your private file. It never appears in Git, logs
  or anything a model sees.
- Research notes last for the day plan, not the 47 minutes they lasted in the
  first run.

### D6 — HQ dashboard

**What you get.** Three additions to HQ:

- T39 — Every wake-up, its reason, and each department's next check-in.
- T40 — AI allowance and spending per department.
- T41 — Price charts with candles, chosen indicators, alarm lines and orders,
  drawn with TradingView Lightweight Charts.

**How we'll know it works.**

- Every wake in the records appears in HQ with its reason.
- Spending per department adds up to the receipts. Unknown costs are shown
  as unknown.
- Charts load from HQ itself, with no outside scripts, and show the required
  TradingView attribution link.
- The page works on a phone and an iPad, in light and dark.
- Reading HQ never starts AI work or places an order.

### D7 — Scored paper test

**What you get.** A paper run on the new engine, judged by a scorecard: profit
after fees compared with holding cash and holding BTC over the same days. AI
spending is shown next to it.

- T42 — Compute the day-trader scorecard.
- T43 — Run the scored paper test.

**How we'll know it works.**

- Scorecard numbers match hand-checked examples, to the cent.
- The run report comes from the real paper installation, not test data. Parts
  that failed or were missing are reported as such.
- The report lists wake-ups by reason, AI spending by department, incidents
  and what to tune next.

**What a good scorecard does not mean.** It does not prove the trader is
profitable, and it does not allow live trading. Working software, economic
evidence and permission to trade real money are three separate gates.

## Rules every phase keeps

- Paper only. Live trading needs its own, separate permission.
- Paid calls stay off, except the Perplexity test calls (D125). Every one of
  those gets a receipt.
- No caps on how often agents wake or act (D116). Only money risk is limited.
- No riba (D127): no futures or perpetuals, no short selling, no margin or
  borrowing, no lending, "earn" or interest. Spot only, long only.
- Money, fees and quantities are exact decimals.
- No indicator formula is written in-house (D118).
- Outside research is information, never instructions.
- Keys and passwords never enter Git, logs or model context.

## Decisions that need you

These are settings for the virtual USD10,000 account. They are not advice
about real money. A real allocation is a separate decision.

### 1. Daily loss limit (still open, D117)

**Question.** How much may the paper account lose in one day before new
buying stops?

**Options.**

- 2% of the day's starting value ($200). Stops early on bad days, but may
  stop the test too often to learn much.
- 3% ($300). A middle setting.
- 5% ($500). More room, bigger bad days.

**When it is hit.** Either stop new buying until the next day start, keeping
protective exits (fees stay low), or sell everything at once (costs more in
fees and locks in the loss).

**Recommendation.** 3%, then stop new buying and cancel resting entries until
the next day start. Protective exits stay.

**Needed before** T34 is switched on in the paper installation, and before
the test run (T43).

### 2. Maximum amount per coin (still open)

**Question.** How much of the account may sit in one coin?

**Today.** The example policy allows 50% in one coin and 80% in total.

**Options.** 10% ($1,000), 20% ($2,000) or 33% ($3,300) per coin.

- Smaller spreads risk across coins, but each trade earns less.
- Larger lets one bad coin hurt more.

**Recommendation.** 20% per coin. Keep 80% in total.

**Needed before** T34 is switched on, and before T43.

### 3. Which coins the paper account may trade (D120)

**Question.** The paper account may only trade BTC and ETH today. The
scanner needs a wider list, set through an owner policy revision.

**Options.**

- A rule: any Kraken USD spot coin that passes the scanner's liquidity and
  exclusion checks at that moment. New busy coins come in by themselves.
- A fixed list you approve, for example the 20 most traded coins. You stay
  in control, but you must update it from time to time.
- Keep BTC and ETH. The scanner then has nothing to choose from.

**Recommendation.** The rule, for paper. It matches D120 and needs no upkeep.

**Also decide.** Should stablecoins and tokens that pay a yield (for example
staking or lending tokens) be excluded? **Recommendation:** exclude both.
Stablecoins barely move, so there is nothing to day-trade. A yield may count
as riba (D127) under mainstream views, until a scholar advises otherwise.

**Needed before** T32 is finished.

### 4. Perplexity budget after testing (D125)

**Question.** Testing runs with no spending limit, so we can see natural
costs. What limit comes after?

**Options.**

- A monthly money limit, about 1.5 times the observed monthly cost.
- A daily money limit.
- No limit, only an alert when spending passes an amount.

**What happens at a limit.** Research and Leader news rounds pause. The
Trader, protection and records continue.

**Recommendation.** Watch about two weeks of testing in HQ (T40). Then set a
monthly limit of about 1.5 times what you saw. A money limit is not an
activity cap, so it fits D116.

**Also decide.** Would you like an alert, not a stop, during testing? For
example at $20.

**Needed after** two weeks of testing.

### 5. Must a strategy pass the practice range?

**Question.** D119 says every strategy is replayed before use. Must the
result be good, or is having the report enough?

**Options.**

- Report required, no pass mark. The Trader sees the report and decides.
- A minimum result, for example "beat holding cash after fees".

**Recommendation.** Report required, no pass mark, for paper. A pass mark
would be a confidence threshold, which the rules avoid. You can add one later.

**Needed before** T34.

### 6. When the trading day starts

**Question.** Crypto trades all day, every day. When do the day start and day
end run?

**Options.** 00:00 UTC, which matches Kraken's daily candles. Or a time in
your morning, so you can read the day plan. The daily loss limit also resets
at this time.

**Recommendation.** 00:00 UTC.

**Needed before** T32 is finished. Software uses 00:00 UTC until you choose.

### 7. How long the test runs, and what counts as good

**Question.** How long should the scored paper test (T43) run, and what
result means "keep going"?

**Options.** Two weeks. Four weeks. Or until a set number of completed
trades.

**Recommendation.** At least four weeks and at least 30 completed trades,
whichever takes longer. "Keep going" means profit after fees beats both
holding cash and holding BTC. AI spending is shown next to it. Even a good
result is not permission for live trading.

**Needed before** T43.

### 8. The AI allowance floor (optional)

**Question.** New AI work pauses when less than 40% of the shared Codex
allowance is left (D98). More wake-ups will use more allowance.

**Options.** Keep 40%. Or lower it, which leaves less for your own coding
sessions.

**Recommendation.** Keep 40% for the first week of the event engine. Then
look at the wake-up counts in HQ (T39) and decide.

### 9. A stock price source (later, for T30)

**Question.** The US-stock profile needs stock price history. Good sources
may cost money.

**Recommendation.** Decide only when crypto results justify stocks.

## Things only you can do

- **Apply protected settings.** Agents cannot change these: the Research and
  Leader switch to Perplexity (D124 changes the "one model for everything"
  setting from D97/D98), your money limits, and the coin list.
- **Check Perplexity auto top-up is off.** The prepaid credit is the outer
  bound while there is no limit (D125).
- **Revoke the test key** when testing ends (D125).
- **Get Kraken's archive file.** It is a large download from Kraken's support
  pages. You download it, or permit an agent to. The importer only reads a
  local file.
- **Update the current mandate.** T24 fixes the default. The running
  installation keeps its old strategy list until you approve a new revision:
  with the service stopped, `trade-graph mandate-cleanup` shows the change and
  `--confirm` installs it (an agent can run it on your yes).
- **Approve each upgrade.** As before, new software is installed only with
  your approval, while the paper account only looks after what it already
  holds.

## What this plan does not do

- No live trading, real budget, withdrawals or other paid APIs.
- No new exchange connections. OKX and Interactive Brokers appear only as
  cost profiles. A connection is built only if a strategy's results depend on
  that venue's costs (D126).
- No euro trading pairs. The paper account is in US dollars.
- No self-rewriting. New strategy ideas go through Research or Optimisation
  and the practice range (D121).

## Notes for implementers

- Tasks are T23–T43, release `R2`, except T30, which is `LATER`.
  `python3 scripts/next_task.py` lists ready `R1` and `R2` tasks; `--all`
  adds `LATER` ones.
- Deployment still follows the owner-approved financial-manifest transition
  (docs/FINANCIAL-TRANSITION.md), with the paper account in `MANAGE_ONLY`.
- T35 owns the wake, check-in and focus-step contracts; T37 and T38 build on
  them. Old fixed role intervals and event coalescing must not become hidden
  caps (D116).
- Mission Control's progress page (`src/trade_graph/api/progress.py`) does
  not accept the `todo` status, and it already rejects the timestamp format of
  `updated_at` in `planning/progress.json`. Its page shows "unavailable" before
  and after this plan. Fix it separately: accept `todo`, and the full
  timestamp.
- Recommended defaults; record them in `docs/DECISIONS.md` when built:
  - The D89 hourly fields (averages, ranges, pullback) move to library
    functions or retire, so no indicator formula stays in-house (T27).
  - The second indicator library is a test-only dependency (T27).
  - Search runs in software as a registered tool, so every result is saved.
    Perplexity's built-in search is used only if its results can be captured
    the same way (D124). Check the search price on Perplexity's official
    pricing page first; D122's figure came from third-party pages.
  - Protective exits in paper fill as market orders at the taker fee, with
    slippage. Live venues use their own conditional orders later (T19).
  - Interactive Brokers replays respect US market hours and cash settlement:
    sale proceeds are reused only after they settle (T30).
- Keep each task's acceptance honest. A mock is not a paid-provider test, and
  a synthetic run is not the real paper run.
