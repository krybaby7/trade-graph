# Strategy research ledger

**What this is.** One place that records everything Trade Graph has learned about *how to make money* with its rules: what was tried, what the numbers said, what is ruled out, what is still open, and what "good enough to start" means. It is written so that a person can read it in twenty minutes and so that the Trading desk can later be given the same facts as dated evidence.

**How to use it.** Append, never rewrite history. Every entry carries a date, the data it used, the numbers, and where the detail lives. A claim without a number and a source is a hypothesis, and goes in section 6, not section 4.

**Status on 2026-10-22.** No profitable strategy has been found. Every real-data replay so far loses after fees, and the system has never placed a paper order. What has been built is a fair test bench and a Trading desk that can run it. The next work is strategy research on that bench, not more infrastructure.

Branches: the engine and all results live on `claude/day-trader-engine-20261012` (WSL checkout, head 4e54c6d; GitHub head 7764c43 plus later local commits). The exchange reference lives on `codex/exchange-strategy-reference-2026-10-21` and its integration branch `codex/exchange-reference-next-start-20261021`. The Hyperliquid study and this ledger live on `codex/orchestrator-takeover-2026-10-04` (Windows checkout). These need one merge before the desk can read all of it.

---

## 1. The game we are playing

| Rule | Value | Source |
|---|---|---|
| Capital | USD 10,000 virtual, EUR reporting; real AI spend is separate and never paid from paper gains | D03, D203 |
| Instruments | Spot crypto only, long only, no leverage. No futures, perpetuals, funding, shorting, margin, lending or interest; no stablecoins, yield or staking tokens | D127, D131, D184 |
| Venue prices | Kraken USD pairs (archive + collector); OKX USDT pairs as a second source, never merged | D04, D168 |
| Fees modelled | Kraken starting tier 0.40% maker / 0.80% taker; Kraken after USD 10k volume 0.22% / 0.38%; OKX Europe 0.10% / 0.20% | D14, D126, D146 |
| Fill rules | A touch is not a fill; a limit fills only when price trades through it, as maker; market orders, rule exits and every stop or take-profit fill as taker at the next open plus 0.10% slippage | D145, D147, D148 |
| Money limits | Daily loss 3% of day-start value, 20% per coin, 80% total; resting entries count as filled | D138 |
| Coin universe | Hourly scanner over all Kraken USD pairs; excludes thin pairs (under USD 500k a day or spread over 0.50%), pegged coins, the excluded classes; shortlist = top 10 | D170–D173 |
| Fair-test rules | Tune on earlier days, confirm on later untouched days, never reuse a confirmation window; results hashed and re-verifiable | D166, D167 |
| What is not a gate | No pass mark, no confidence threshold, no committee. A confirmation report must exist; the desk reads it and decides | D138.5, D185 |
| Scored test | At least 4 weeks and 30 trades; "keep going" = beats cash and beats holding BTC after fees, AI spend shown beside it | D138.7, D204 |

Why these matter for strategy: a round trip costs 0.80–1.60% at Kraken's starting tier and 0.20–0.40% at OKX. BTC's typical daily swing is about 2.7%, a mid-size coin's about 6% (D126 study). A strategy therefore has to capture a large share of the daily move to pay for itself, or trade much less often.

---

## 2. Timeline: what we did and what each step taught us

**2026-09-29. Start.** Two starter templates, `slow-trend-pullback` and `range-reversion`, written as prose and marked "unproven". No historical features existed.

**2026-10-06 to 10-10. First paper cycle and first continuous run.** 66 hours on Codex `gpt-6.1-sol`, Trader on a 4-hour timer, BTC/ETH hourly candles. 17 decisions, all `no_action`, zero orders. Lesson: the system could not trade at all, for eight recorded reasons (candle timing withheld features on 7 of 17 turns, the latest close was never in the Trader's context, a stale EUR rate blocked buys on 11 of 17 turns, a failed turn paused the system, a 4-hour timer, one slow long-only rule, empty research, no self-repair). All fixed in source by D129/D130. Detail: docs/DAY-TRADER-ENGINE.md, D114.

**2026-10-12. First replay and the redesign.** A 30-day replay of the hourly pullback rule on BTC and ETH lost after fees at the starting tier (no figures recorded). The owner-run venue study measured daily swing divided by round-trip cost: Kraken starting tier about 3× (BTC) / 8× (mid-size), Kraken after 10k about 6× / 14×, OKX Europe about 13× / 30×, US large-cap stocks at IBKR 50–80×. The redesign (D114–D131) made Trade Graph an event-driven day trader with a practice range, a coin scanner, TA-Lib indicators, Leader news rounds, and the no-riba rule.

**2026-10-13 to 10-16. Data and settings.** Pair catalog: 1,460 pairs, 677 in USD, 591 eligible after exclusions. TA-Lib menu of 26 indicators, no VWAP. Jev decision model evaluated: no better than a coin flip at price direction in its only public test; allowed only as a news alarm (D137). The owner accepted all ten money and test settings (D138).

**2026-10-16 to 10-21. The engine is built.** Thirteen cloud waves: strategy grammar (JSON, up to 8 indicators, 8 entry conditions, mandatory stop, 1/5/15-minute bars), practice range with tune/confirm and walk-forward offers, cost profiles, fill engine, scanner, day plans, scorecard, market types (`rising / sideways / falling / unclear` from SMA 168 slope + ADX 14 on hourly candles), a strategy lab inside the service, one Trading desk with playbook and stances, HQ desk room. Full suite 8,427 tests passing. Nothing deployed yet.

**2026-10-19. L2 part 1: first real test.** Both v1 strategies, 5 coins (BTC, ETH, SOL, XRP, DOGE), Kraken 15-minute archive, tune Apr–Jul 2026, confirm Aug–Sep 2026, three cost profiles: **59 of 60 reports lose after fees.** The one winner: range reversion, ETH, Aug–Sep, OKX fees, +61. Before fees, summed over 5 coins: slow-trend-pullback −2,370 (Apr–Jul) and +920 (Aug–Sep); range-reversion −745 and +15. Fees at the starting tier were 15–25% of the account in four months. In falling months both lost less than holding; in rising months both missed nearly all of the rise. Detail: planning/handoffs/L2.md.

**2026-10-20. Two years of prices and one desk.** Kraken and OKX history for the five coins from Nov 2024 (OKX has zero missing slots; Kraken has a 6-hour hole on 1 Nov 2025 and three hourly holes in 2025). The owner chose one Trading desk that picks per coin whether to wait, buy dips or follow the trend, runs its own lab and reads news (D280).

**2026-10-21. L2 part 1b: two-year rerun.** Same v1 strategies, tune Nov 2024–Mar 2026, confirm Apr–Sep 2026, Kraken and OKX, 120 reports: **0 of 120 make money.** Best: range reversion, ETH, OKX, confirm window, −571. Averages per USD 10k:

| Strategy, window | Kraken start | Kraken after 10k | OKX | Holding the coin | Before fees (OKX) |
|---|---|---|---|---|---|
| Slow trend pullback, tune | −8,374 | −6,381 | −4,482 | +835 / +963 | −1,046 |
| Slow trend pullback, confirm | −4,087 | −2,441 | −1,442 | +1,921 / +2,063 | −278 |
| Range reversion, tune | −6,372 | −4,068 | −3,024 | +835 / +963 | −661 |
| Range reversion, confirm | −2,522 | −1,362 | −683 | +1,921 / +2,063 | +10 |

Slow-trend-pullback traded 480–930 times per coin in 17 months; its fees at the starting tier averaged 77% of starting capital. Split by market type (5 coins, OKX fees, tune window): slow-trend-pullback lost 16,654 in sideways markets over 2,504 trades and 4,400 in rising markets over 1,056 trades; range-reversion lost 8,827 in sideways and 5,940 in falling. Conclusion recorded: no edge for cheaper fees to rescue. Market-type rules v2 (D230) fixed the hole problem without changing results.

**2026-10-21. Exchange reverse-engineering (Codex branch).** A 900-second public capture of Kraken and OKX books (1.35M records), client code review, fee and rule pages. Findings: same-quote depth gaps between the venues never exceeded 4.0 bp (BTC) / 4.6 bp (ETH) at ≤250 ms age, so cross-venue arbitrage produced zero positive observations even at a favourable 5 bp per leg; OKX's retail price-improvement liquidity showed at most 1.0–1.3 bp of displayed improvement; Kraken's derivatives Maker Protection (20 ms hold) is a structural idea but outside spot; the Kraken-minus-OKX funding differential summed to about 27 bp a month, against about 20 bp of fees for four legs. All of these are derivatives or latency plays and are excluded or unprofitable for us. The useful product is the venue mechanics reference (docs/strategy/KRAKEN.md, OKX.md) and a strategy-brief format. One discrepancy to resolve: that reference quotes OKX EEA spot at 0.20% / 0.35% for a regular account, while our `okx-eea-spot@1` profile uses 0.10% / 0.20%; the profile may be optimistic.

**2026-10-22. The best Hyperliquid trader.** Wallet 0xecb6…, attributed to Wintermute. Two million fills over 14 days, USD 5.58B notional, 87% maker, median fill USD 200, spread capture 6–13 bp per round trip on BTC/ETH/SOL, lifetime 0.67 bp of volume, plus off-venue token delivery flows. It is a perpetuals market maker with a fee rebate and a hedge elsewhere, which is three things we cannot do. Transferable: judge strategies in basis points of volume after fees, prefer resting orders and measure markouts, cap inventory per coin, do not pause on losing days, measure edge by session. Detail: docs/research/2026-10-22-hyperliquid-wallet-0xecb6-study.md.

---

## 3. Where we are now

- **Infrastructure: done in source.** Fair-test bench, lab, desk, scorecard, market types, scanner, news inputs, money limits. Not yet installed (L3 paused by the owner on 21 Oct).
- **Strategy: nothing works yet.** Two v1 families, both without an edge before fees. No strategy has a positive confirmation report.
- **Data: adequate for 15-minute research on five coins over two years, and on all USD pairs since April 2026.** Survivorship bias (delisted coins absent) makes coin-selection results optimistic.
- **Research inputs: wired, barely used.** Perplexity's first real call succeeded (USD 0.04). News alarm ships in shadow mode. No Leader round has run against the real desk.

---

## 4. What we know (established facts, each with evidence)

1. **Fees are the first-order problem, not prediction.** At Kraken's starting tier the v1 strategies paid 15–25% of the account in fees in four months and up to 77% of capital over 17 months. Even the best account on Hyperliquid, with a rebate and co-location, nets 0.67 bp of its volume. (L2.md; Hyperliquid study.)
2. **Both v1 families have no edge before fees.** Before-fee results are within ±1,000 per 10k across windows and venues, around zero. Cheaper fees shrink the loss but cannot make a profit. (L2.md part 1b.)
3. **Trading often is what loses.** 480–930 trades per coin in 17 months at 15-minute resolution is the direct cause of the fee drag. (L2.md.)
4. **Sideways markets are where the money goes.** Both families lose most, and trade most, when the market type is sideways. Rising markets were also negative for the trend rule after fees. (L2.md market-type split.)
5. **Holding beats both rules in rising periods; cash beats both in falling periods.** Long-only rules with 2–3% stops miss most of a rally and bleed in a drawdown. (L2.md part 1.)
6. **A cross-venue price gap at retail latency does not exist in size.** Zero positive observations at 5 bp per leg. (Exchange reference §7.)
7. **Leaderboard "best traders" are market makers.** Reverse-engineering them yields execution lessons, not signals. (Hyperliquid study.)
8. **The first run's zero trades were plumbing, not strategy.** Eight causes, all fixed in source. Any future zero-trade period must be re-diagnosed before blaming the strategy. (D114, D129, D130.)
9. **Data details change results.** One missing hour blanked market-type labels for 36 days under rules v1; OKX candles are clean, Kraken's archive has holes; delisted pairs are missing. (L2.md, KRAKEN-PRICE-HISTORY.md.)
10. **Models are not oracles.** Jev scored worse than a coin flip on price direction; the Trader on Codex produced `no_action` 17 times when its inputs were broken. Software labels, rules and fair tests carry the evidence; the model decides within them. (D137, D114.)

---

## 5. Ruled out (and why)

| Idea | Why it is out | Source |
|---|---|---|
| Perpetual market making, funding harvesting, basis carry, shorting, margin | No-riba rule; also needs a hedge leg and rebate tier we do not have | D127, Hyperliquid study |
| Cross-venue spot arbitrage | No positive gap at 5 bp per leg, retail latency | Exchange reference §7 |
| OKX price-improvement routing as a strategy | Execution improvement of ≤1.3 bp, not a return | OKX.md |
| Stablecoin, yield, staking, pegged coins | Riba rule; no movement | D131, D172 |
| The two v1 strategies as they stand | 0 of 120 profitable over two years | L2.md |
| Pass marks, confidence thresholds, committees | Owner invariant; the desk decides on evidence | D138.5, AGENTS.md |
| Decision models (Jev) for prices, rankings or rules | Coin-flip evidence; news alarm only | D137 |
| In-house indicator maths | TA-Lib only | D118 |
| Copying a Hyperliquid wallet's trades | Market-making economics are not available to a 10k spot account | Hyperliquid study |

---

## 6. Open hypotheses, ranked by how likely they are to matter

Each one names a falsifying test the lab can already run, except where a gap is noted.

**H1. Trade far less often: multi-day swing holding with slow entries.** The mechanism is arithmetic: if a round trip costs 0.44–0.80% and the average trade lasts days instead of hours, fee drag per month falls by an order of magnitude. Test: a 15-minute-bar strategy whose indicators span 1–3 weeks of candles (the grammar allows periods up to 500 bars, about 5 days at 15 min; longer needs the 60-minute interval, which the grammar does not yet allow: gap). Measure trades per month, before-fee edge per trade, result vs cash and vs holding, on an untouched window. Target before starting: fewer than 15 trades per coin per month.

**H2. Only trade with the market type.** Enter trend rules only when the coin is `rising`; stay in cash when `sideways` or `falling`. Caution: the split already shows the trend rule losing 4,400 in rising markets at OKX fees, so gating alone does not rescue v1. The test is gating plus H1. The grammar supports `{"market_type": "rising", "relation": "is"}` on entries today.

**H3. Cut the taker side.** Every exit in the simulator fills as a taker (stop, take-profit, rule exit). On a real venue a take-profit is a resting limit sell and would fill as maker. Modelling take-profits as maker fills would cut one side's cost from 0.80% to 0.40% at the starting tier. Gap: the fill engine needs a maker take-profit rule, and the live exit tool must place it as a limit. Worth doing before more replays, because it changes every result.

**H4. Wider stops, fewer stop-outs.** Stops of 2–3% on coins with 6% daily swings turn noise into realised losses and taker fees. Test: stops at 1.5–2× ATR 14 on the strategy's interval, holding limits in days; compare stop-out rate and result.

**H5. Breadth through the scanner.** Mid-size coins have swing-to-cost of 8–30× against BTC's 3–13×. The scanner already ranks volume, swing/cost, spread and trend. Test: run H1/H2 on the daily top-10 shortlist since April 2026 instead of the fixed five coins, with the survivorship caveat recorded on every report.

**H6. Buy-dips in a confirmed uptrend (the owner's "buy dips" stance).** Enter on a pullback to a rising weekly average while the market type is rising; exit on a cross below it or after N days. This is slow-trend-pullback with longer averages, a wider stop and the market-type gate. Test as one new family (new `strategy_id`), so the Aug–Sep 2026 window is still available to confirm it.

**H7. News-driven stances cannot be backtested.** Record every news-driven stance with its reason and judge it in the scorecard split by stance after the fact. This is already built; it needs the install to produce data.

**H8. Fee tier and venue.** Reaching Kraken's after-10k tier halves costs; OKX Europe may be a quarter of the starting tier, or may not (profile discrepancy above). Test: re-read both fee pages, correct the profiles, rerun the best candidate from H1–H6 on both. Not a strategy, but it decides whether any strategy clears the bar.

**H9. Session timing.** The Hyperliquid account's activity and profit concentrate in US hours. Test: split a strategy's trades by hour of entry in the report (gap: the report has no hour split today).

**H10. Funding and leveraged-crowding as a signal, not a trade.** Persistently high perp funding marks crowded longs. Low priority: needs a funding feed and a hypothesis about what it predicts for spot over days.

---

## 7. What "worth starting the trading graph with" means

This is the owner's bar for pressing start, not a software gate (D138.5 stays). A candidate is ready when, on an untouched confirmation window of at least 90 days:

- it beats cash after fees on at least 3 of 5 coins, and beats holding the coin on at least 2, at the fee profile the account will actually pay;
- its before-fee edge per trade is at least twice its round-trip cost (so a fee-page surprise does not flip the sign);
- it has at least 30 trades in the window, and its worst drawdown is within the 3% daily / 20% per-coin limits as the desk would run it;
- the same version was not tuned on that window (the store enforces this), and the number of versions tried before it is recorded next to the result.

Then the scored paper test (D138.7: 4 weeks, 30 trades, beats cash and BTC) is the next gate, and only after that does any live question arise.

---

## 8. Can the Trading desk carry this forward on its own?

What already exists for a self-improving loop: the desk can write strategy versions in the grammar, request tune and confirm runs on all cost profiles, read the reports with the market-type split, keep an append-only playbook of stances with reasons, receive Leader strategy ideas, and have Learning read a scorecard split by strategy, market type and stance. The fairness rules (no reuse of confirmation days, hashed results) are protected from the deployed Engineer.

What is missing for the loop to be honest rather than busy:

1. **A hypothesis register the desk can read.** This ledger is it, but the runtime does not read Markdown. It must be delivered as dated evidence through the same route the exchange reference uses (a packaged catalog with expiry), or as a `lab` context section.
2. **A count of tries.** Unlimited tuning plus one confirmation per family per window invites "confirmation shopping": try 40 families, confirm each once, report the one that passed. Each confirmation report must carry the number of versions and families tried in the study and since the last confirmed one, and the desk's instruction must say that a pass after many tries is weaker evidence.
3. **Retirement rules.** A confirmed strategy that then loses for N trades in the scorecard must be retired by the desk, with the reason, not quietly replaced.
4. **The three fill-engine gaps above** (maker take-profit, 60-minute interval, hour-of-entry split), because the desk will otherwise optimise against a simulator that mis-prices exits.
5. **Slow evidence.** 30 trades at a sensible frequency is one to three months. The loop can run many replays a day but only one forward test a quarter. The desk must be told that replays propose and forward results dispose.

---

## 9. Next actions, in order

1. Merge the three branches into one so the desk's documentation, the exchange reference and this ledger are on the engine branch.
2. Close H3 (maker take-profit) and the 60-minute-interval gap in the fill engine and grammar, with tests, before any new replay batch.
3. Re-read the Kraken and OKX fee pages and correct `okx-eea-spot@1` if needed (H8).
4. Run H1 + H2 + H4 as one new family on the five coins over two years, tune to March 2026, confirm Apr–Sep 2026; then H5 on the shortlist since April 2026.
5. Record every result here with the try count. If nothing clears section 7, say so and continue; do not lower the bar.
6. Install (L3) only once one candidate clears section 7, so the scored test starts with something that has at least passed a fair replay.
