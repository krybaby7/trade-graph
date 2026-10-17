# Paper scorecard (T42)

Status: built and tested in source on 2026-10-17 by cloud package W5-C (decisions D200–D204, no state migration).
Not deployed. Paper only. Read only: the scorecard never writes, sends, pauses, limits or allows anything. The scored
paper test (T43) uses it for its final report. The rules behind it: D119 and D169 (one set of result sums with the
practice range), D127 (cash earns nothing, long only), D138 item 7 (the owner's rule for the scored test), D175
(three kinds of AI cost, unknown never zero) and the separate gates of
[08-DELIVERY-AND-VALIDATION.md](08-DELIVERY-AND-VALIDATION.md) and [10-PAPER-CAPITAL.md](10-PAPER-CAPITAL.md).

In plain words: the scorecard looks at the paper account over a period, for example the four weeks of the scored
test, and says what the Trader made or lost after every fee, compared with doing nothing (keeping the dollars) and
with simply buying Bitcoin at the start and selling it at the end. It counts the trades, how many won, the average
win and loss, the worst fall from a high point and the longest run of losing trades. Beside it, it shows what the AI
departments really cost in those same days, from their receipts, with costs not known yet shown as unknown, never
as zero. The paper money is simulated and never pays for the AI. And it says plainly that a good scorecard is not
proof that the Trader is profitable and never permission to trade real money.

| Part | Path |
|---|---|
| The result sums (shared with the practice range) and `TradeFill.from_fill_record` | `src/trade_graph/kernel/trade_metrics.py` |
| The scorecard read model and its plain text | `src/trade_graph/application/scorecard.py` |
| AI spending of one period (`period_spending`) | `src/trade_graph/application/ai_spending.py` |
| The reference rate the books use (`Ledger.fx_rate`) | `src/trade_graph/application/ledger.py` |
| The owner's command `trade-graph scorecard` | `src/trade_graph/market_data_cli/scorecard.py` |
| Tests | `tests/integration/test_scorecard.py`, `test_scorecard_cli.py`, `test_ai_spending_period.py`, `tests/property/test_scorecard_properties.py`, `tests/unit/test_scorecard_stays_offline.py`, the T42 cases in `tests/unit/test_trade_metrics.py`; helpers in `tests/scorecard_support.py`; the golden document `tests/fixtures/scorecard/golden_scorecard.json` (synthetic) |

The scorecard module and the sums are protected paths (`kernel/authority.py`): how a result is valued and compared
is a scoring definition, which agents cannot edit.

## The period (D200)

- The period is `(start, end]`. By default it starts at the opening deposit, or at the first BTC price recorded
  after it (so holding BTC can be valued from the first moment), and ends now. `--start` and `--end` choose another
  period, for example the scored test's four weeks; an end in the future is refused.
- Money added or taken out inside the period, or an operating cost paid from the paper cash, makes the period
  unscorable, with the reason. Resetting a paper account opens a new account (10-PAPER-CAPITAL.md), so a scored
  period never needs one.
- Only cash in the account's own currency (USD) and coins priced in it are counted; anything else is refused rather
  than converted at a guessed rate.

## Valuation (D200)

- The account is valued from the ledger's own records: its journal postings (cash and coins) and its price marks
  (the paper feed's mid price of each coin, about every 10 seconds).
- At the start it is valued as the ledger values it. Coins already held then are counted as bought at the start
  price with no fee (`carried-in:<coin>` in the trade's fill list), so a trade that began earlier is scored only for
  the part inside the period.
- The equity curve has a point after every posting and at every new price of a coin held. The worst drawdown is the
  largest fall from a running peak of that curve, as a fraction of the peak (D169).
- At the end each coin still held is valued as if sold at its mark with the taker fee of the fee profile, the same
  terms holding BTC gets, and that is the last point of the curve. Both figures are shown: the marked equity and the
  ending equity after the exit fee.
- The scorecard's running books must agree with the ledger's own replay at both ends, valued at the same prices, or
  nothing is scored.
- A price older than 15 minutes at the start or the end, or one marked stale, makes the scorecard **provisional**,
  with each reason listed; it is never shown as complete. A held coin without any price makes it unscorable.

## The sums (D169, D201)

`kernel/trade_metrics.py`, unchanged in meaning: a trade runs from flat to flat on one coin with every partial fill
and fee; it wins above zero after fees, loses below and exactly zero is even; fees paid count fills only (the end
valuation's selling fee is inside the ending equity). `TradeFill.from_fill_record` reads ledger fills: a fee in the
coin changes what is held (a buy holds `quantity - fee`, a sell gives up `quantity + fee`) and is valued at the fill
price; a fee in a third asset is valued at its identified rate; a net rebate lowers a buy's principal or raises a
sell's. Sums are exact decimals; ratios and averages are rounded half-even to ten places.

## Comparisons and EUR (D202)

- **Holding cash** earns nothing (no interest, D127): beating cash means a result above zero after fees.
- **Holding BTC** spends the starting equity on BTC at the ledger's BTC/USD price at the start, with the taker fee,
  floored to the paper instrument's lot (0.00000001 without one), and sells at the end price with the fee. The fee
  profile is `kraken-spot-starting-tier` (the paper broker's default) in force on the start day, or the one named
  with `--cost-profile`. Without a BTC price at the start or the end it is **unavailable**, never invented; keep
  BTC/USD in the paper feed's symbols (it is in the default `paper_symbols`).
- **EUR reporting:** the starting equity at the reference rate the books use at the start, everything else at the end
  rate. Holding the starting dollars as cash is the EUR baseline, so the dollar's own move against the euro is shown
  as the currency effect and not counted as trading (A43). Without a rate the EUR figures are unavailable; a stale
  rate is labelled.

## AI spending beside the result (D203)

Every paid AI call reserved inside the period, for the whole installation (AI budgets belong to the installation,
not to one paper account): Perplexity, Jev and any other paid price card, each call once, as *actual* (a receipt),
*estimated* (charged at the reserved maximum because the usage was never reported) or *unresolved* (no receipt by
the end: counted at its held maximum, "up to", never zero), in USD, per department, with test calls apart (D175).
Codex subscription turns are counted per department, not priced. The figure is shown beside the result and never
subtracted from it or paid from it: the paper result is simulated.

## Gates and the owner's test rule (D204)

Every scorecard, in JSON and in text, says: "Working software, economic evidence and permission to trade real money
are three separate gates." It judges none of them. It also reads D138 item 7 from its own figures: long enough (at
least 28 days and at least 30 completed trades), beats cash, beats BTC, and "keep going" only when it is long enough
and both are beaten (no reading while it is too short or BTC is unavailable). This is information for the owner and
for T43, not a pass mark for the Trader and not permission for real money.

## Commands

```text
trade-graph scorecard [--database runtime/trade_graph.sqlite] [--portfolio-id ID]
                      [--start 2026-11-02T00:00:00Z] [--end 2026-11-30T00:00:00Z]
                      [--cost-profile kraken-spot-starting-tier] [--format json|text]
```

It opens the state database read-only, never creates one, and scores the newest paper portfolio unless one is named.
A period that cannot be scored prints `{"available": false, "reason": ...}` and exits with code 1. On four weeks of
10-second prices for four coins (967,684 marks) and 60 trades, one scorecard took about 3.5 seconds in the build
container.

## Known limits

- HQ's Money tab shows the scorecard since the account opened (W8-C, D252), cached for a minute; T43's final
  report passes the scored test's own start and end.
- A fee in a third asset is valued at its identified rate in the trade, while the equity curve values that asset at
  its own mark; the paper broker charges every fee in USD, so paper scorecards are not affected.
- `valuation_marks` has no index, so each price query scans the table; at the size above it stays a few seconds.
