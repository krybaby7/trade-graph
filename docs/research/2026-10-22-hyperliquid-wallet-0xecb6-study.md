# Study: Hyperliquid wallet 0xecb63caa…b82b00 (attributed to Wintermute)

Recorded 2026-10-22, extended the same day with a 14-day fill export. Research only. Nothing in this document changes runtime policy, allocations or the owner's rules (spot only, no shorting, no leverage, no perpetuals, D127).

## Short answer

This is not a "trader" in the sense Trade Graph means. It is the Hyperliquid market-making and execution account of a professional firm. On-chain analysts attribute the address to Wintermute; the firm has not confirmed it. The account rests two-sided limit orders on about 130 instruments, earns a few basis points of spread per round trip, keeps its inventory nearly flat relative to what it trades, and moves token inventory (wrapped BTC, HYPE, PUMP) off the venue in size, which means part of its business is buying on Hyperliquid for delivery elsewhere. Its edge comes from volume, fee tier, speed and a hedging leg we cannot see, not from predicting price direction.

## Evidence collected

Sources, all public and free:

- Hyperliquid `info` API: `userFills` (only the 2,000 most recent fills are served, 24 minutes of activity), `clearinghouseState`, `spotClearinghouseState`, `userFees`, `portfolio`, `userFunding` (13 months, since 2025-09-21), `userNonFundingLedgerUpdates` (full life, 81,380 rows), `userRateLimit`, `spotMeta`, `candleSnapshot`.
- Hypedexer free exporter (trade-export.hypedexer.com), run by the owner in a normal browser after a Cloudflare human check: 1,997,317 fills from 2026-10-08 00:00 to 2026-10-21 23:59 UTC, 300 MB CSV with time (1 s resolution), coin, direction, price, size, notional, fee, fee token, closed PnL and tx hash. No maker/taker flag; fills were classified by fee rate (0.3 bps rebate tier = maker, 1.82 bps = taker). The CSV stays outside version control (`strategies/*.csv` is ignored); the analysis copy and SQLite database live in the session scratchpad.
- Hypedexer paid API (pre-March-2025 history needs the Scale plan, USD 1,999/month) and the requester-pays Hyperliquid S3 archive were not used.
- Web: Nansen, HyperTracker, Hypurrscan profiles and news coverage attributing the wallet to Wintermute.

## What the account looks like (2026-10-22 09:30 UTC)

| Measure | Value |
|---|---|
| Perp account value | USD 33.4M |
| Spot balances | USD 12.0M USDC plus small token balances |
| Open perp positions | 86 instruments |
| Gross long / gross short notional | USD 5.9M / USD 99.7M |
| Net exposure | USD −93.8M (net short) |
| Largest shorts | ETH 41.0M, SOL 17.7M, BTC 10.5M, DOGE 3.9M, XRP 3.8M, HYPE 2.3M |
| Leverage settings | cross, 3x–20x depending on asset |
| Liquidations in account history | 0 |
| Fee tier | taker 1.82 bps, maker −0.3 bps (a rebate) |
| Lifetime volume (since Jan 2024) | USD 313B |
| Lifetime PnL | USD 211M (perp 179M, spot 32M) |
| Lifetime PnL per dollar traded | 0.67 bps |
| Last 30 days: volume / PnL | USD 12.5B / USD 19.4M (1.5 bps) |
| Funding received: last 30 days / last 13 months | USD 1.0M / USD 9.5M |
| Daily PnL last 31 days | 18 positive days, median +USD 0.4M, best +7.7M, worst −3.2M |
| Max drawdown ever | USD 49M peak-to-trough, bottom on 2024-12-11 |
| Deposits / withdrawals over life | USD 8.59B in / USD 8.40B out |

PnL by year: 2024 +16.8M, 2025 +157.0M, 2026 to date +37.5M.

Correction to the first draft of this study: the USD 9.5M funding figure covers 13 months, not one. Funding is roughly 5% of monthly profit, a side income, not the second pillar.

## The 14 days in numbers (2026-10-08 to 2026-10-21)

| Measure | Value |
|---|---|
| Fills | 1,997,317 (143k per day; peak 338 fills in one second; a fill in 64% of all seconds) |
| Notional traded | USD 5.58B (USD 399M per day) |
| Instruments | 129: 59% crypto perps, 25% equity and commodity perps (xyz:SP500, NVDA, TSLA, crude, nat gas, silver), 10% spot pairs |
| Maker share | 76% of perp fills, 99.7% of equity/commodity fills, 56% of spot fills; 87% of notional overall |
| Fill size | median USD 200, 90th percentile USD 4.1k, 99th USD 65k, largest USD 698k |
| Realised PnL (closed fills) | USD 11.74M |
| Fees paid, net of rebates | USD 0.27M |
| Funding received | USD 0.24M |
| Account-level PnL change over the window | USD +8.15M (so about 3.5M of realised gains were given back on inventory marked to market) |
| Positive days | 11 of 14; the last three days were −18k, −145k and −490k |
| Biggest day | 2026-10-08: +4.31M realised, of which +1.79M in the 15:00–16:59 UTC hours |

## How it trades

**Spread capture, measured cleanly.** Taking only hours where the account both bought and sold a coin in comparable size (so inventory drift does not pollute the number), the average sell price minus the average buy price on maker fills was:

| Coin | Median per hour | Hours positive |
|---|---|---|
| BTC | +5.9 bps | 70% |
| ETH | +6.5 bps | 75% |
| SOL | +12.8 bps | 82% |
| XRP | +8.2 bps | 70% |
| DOGE | +6.2 bps | 63% |
| HYPE | +31.8 bps | 82% |
| HYPE/USDC spot | +8.2 bps | 87% |
| xyz:SP500 | +1.2 bps | 70% |
| xyz:NVDA | +3.2 bps | 71% |

That is the business: a few basis points per round trip, positive in about three hours out of four, repeated across 130 books. Thin books (HYPE, SOL, memecoins) pay far more per round trip than BTC or the S&P 500 perp.

**Where the money came from.** Realised PnL by coin: SOL +2.17M, ETH +2.13M, BTC +1.15M, PUMP +0.74M, HYPE +0.67M, DOGE +0.64M, ENA +0.50M, XRP +0.46M, ZEC +0.41M. Losers: TIA −0.35M, xyz:SP500 −0.13M, GRASS −0.07M. BTC was 35% of volume but only 10% of profit (0.6 bps of its notional); SOL made 163 bps of its notional. The equity and commodity perps were 25% of volume for 5% of profit (0.4 bps): the firm quotes them for presence and rebates, not for edge.

**Inventory stays flat relative to turnover.** Over 14 days the account traded USD 1.98B of BTC perps and ended with a net change of USD 18M (turnover 111 times net). ETH: 986M traded, 20M net. Day-to-day net flow in the big coins swings between −27M and +27M and reverts; the big short positions are the baseline it quotes around, not a view being built.

**Taker fills are inventory housekeeping.** 21% of fills and 13% of notional crossed the spread. They split evenly between opening and closing shorts (USD 193M vs 197M), which is what skew-correction looks like, not what a directional trader looks like.

**Hour of day.** Activity follows US hours: 13:00–17:59 UTC carries 33% of notional and the single best hour across the window (15:00 UTC) earned 15% of the realised PnL. Asian hours (03:00–09:00 UTC) carry 2.5–3.3% of notional each.

**Off-venue flows are a second business.** In the 14 days the account net-bought USD 174M of UBTC/USDC spot (Hyperliquid's wrapped Bitcoin) with near-zero realised PnL on that pair, and the ledger shows 2,710 UBTC transferred out against 638 in. HYPE spot: USD 12M net bought, 1.43M HYPE sent to a single external address. PUMP tokens moved in both directions in the billions. Over the account's life there are 71,890 such token transfers and USD 8.6B of deposits against 8.4B of withdrawals. This is a firm buying on Hyperliquid for delivery or sale elsewhere, and hedging the perp book against whatever it holds off-venue. The Hyperliquid data alone cannot show whether the whole book is profitable; it shows one leg.

**Funding.** USD 9.5M over 13 months on the net-short book, only 9 negative days out of 397. Useful, not decisive: about 2% of the 14-day realised PnL.

**Infrastructure.** 17.1 billion API requests used against a request cap earned by volume. Every order carries a structured client order id. No TWAP usage. This is a co-located quoting engine, not discretionary trading.

## What is and is not transferable to Trade Graph

Not transferable under the owner's rules and resources:

- Perpetual-futures market making and the maker rebate. Trade Graph trades spot only, and at retail volume Kraken maker fees are 16–25 bps, wider than the 6–13 bps this account captures per round trip in BTC, ETH and SOL. Market making is also explicitly out of R1 scope (docs/00-PRODUCT.md).
- Funding-rate income. It requires short perps and a hedge, both excluded (D127).
- Cross-venue delivery and inventory shuttling, and co-located execution.

Transferable ideas, in order of usefulness:

1. **Judge any strategy in basis points per dollar traded after fees, not in win rate.** The best account on the venue earns 0.7–1.5 bps of its volume. Our v1 strategies lost money over two years of Kraken and OKX history partly because fees dominate; this is a second data point that fees and spread are the first-order problem for a small spot account.
2. **Prefer resting limit orders, and measure markouts.** Kraken charges less for maker fills. The hourly "sell VWAP minus buy VWAP" and the 1/5/10-minute markout checks used here are cheap, objective ways to see whether our order placement is being adversely selected. Worth adding to the paper-run evaluation.
3. **Many small positions, bounded inventory per instrument, adjust by skewing not crossing.** The account never risks a single-name view. Our Trader can apply the same discipline with spot inventory limits per asset.
4. **Expect losing streaks even when the process is right.** Three straight losing days closed the window for an account that is up USD 211M. Our evaluation should not pause a strategy on a bad day; it should pause on a bad cost-adjusted edge over many trades.
5. **Trade where the activity is.** A third of this account's volume and more of its profit sit in the US afternoon (13:00–18:00 UTC). For our spot strategies the wake schedule should at least measure whether edge differs by session.
6. **Withdraw rather than compound on the venue.** Separating profit from working capital matches the owner's existing paper-equity-versus-real-budget rule.
7. **Funding rates as information, not as a trade.** Persistently positive funding tells a spot-only trader that leveraged longs are crowded; it is a usable research signal even though we cannot collect it.

What this study still does not show: the off-venue leg, and the account's behaviour before March 2025 (paid data only).

## Reproducing the numbers

The 14-day CSV was loaded into SQLite with the Python standard library (no pandas on this machine), fills classified maker/taker by fee rate, and summarised with plain SQL. Scripts and the database are in the session scratchpad under `scripts/` and `export14d/`; they are throwaway and not part of the product.
