# Coin scanner (T31)

Status: built and tested in source on 2026-10-16 by cloud package W2-B (decisions D170–D174, state migration
0031). Not deployed. Paper only, public data only, no key, no AI. Decisions D120 (rank by volume, swing against
cost, spread and trend; drop thin pairs), D131 (no stablecoins or yield tokens), D127 (no riba) and D138 item 3
(the paper coin list is "whatever passes the scanner's checks at that moment") are the rules behind it.

| Part | Path |
|---|---|
| Rules, reason and flag words | `src/trade_graph/contracts/coin_scanner.py` |
| One public Ticker request for every pair's bid and ask | `src/trade_graph/adapters/market/kraken_ticker.py` |
| One pair's inputs and checks | `src/trade_graph/application/coin_scanner/inputs.py` |
| The ranking step (pure) | `src/trade_graph/application/coin_scanner/ranking.py` |
| One scan, live or replay | `src/trade_graph/application/coin_scanner/scanner.py` |
| The record, the latest shortlist and the coin rule | `src/trade_graph/application/coin_scanner/store.py` |
| State migration 0031 | `src/trade_graph/adapters/persistence/coin_scan_schema.py`, `migrations/versions/0031_coin_scans.py` |
| Commands `scan-coins`, `coin-shortlist` | `src/trade_graph/market_data_cli/coin_scanner.py` |
| Tests | `tests/unit/test_coin_scanner_rules.py`, `test_coin_ranking.py`, `test_kraken_ticker.py`, `tests/property/test_coin_ranking_properties.py`, `tests/integration/test_coin_scanner.py`, `test_coin_scanner_recorded.py`, `test_coin_scan_store.py`, `test_coin_scanner_cli.py`, `test_migration_0031.py` |
| Synthetic ticker fixture | `tests/fixtures/coin_scanner/` |

## In one minute

- Once an hour, software looks at every Kraken coin traded against the US dollar and ranks the ones worth a day
  trader's attention. No AI takes part, and nothing is bought or sold.
- First it drops what the owner ruled out: stablecoins, coins that pay a yield, currencies, coins Kraken is not
  trading normally, and seven coins the pair list marked for the owner's look. A coin nobody has reviewed is
  dropped too if its price sits still like a stablecoin's.
- Then it drops coins that are too quiet to trade (less than USD500,000 traded in a day, or a bid-ask gap above
  0.5%), and any coin whose numbers are incomplete. A missing number is never guessed.
- The rest are ranked on four things at once: how much is traded, how far the price typically moves compared
  with the fee for one buy and one sell, how small the bid-ask gap is, and whether the price went up over the
  last day. The top ten are the shortlist for Research.
- Every scan is stored with every number, the time each number was observed, and the version of the rules. A
  "replay" of a past hour is always labelled optimistic, because Kraken's history leaves out coins that were
  later delisted.

## The inputs

Every input is read **point-in-time** in a live scan: only candles received at or before the scan moment
count. Candles come from the collector's hourly series (`kraken_public_ohlc`, 60 minutes, D133). Each pair's
window ends at its newest stored hour, at most two hours behind the scan hour (the pair is flagged
`candles_lagging` when it is behind; more than two hours behind is `missing_candles`).

| Input | How it is obtained | Kept with it |
|---|---|---|
| Pair facts | The newest stored pair catalog at or before the scan moment (T25): status, stablecoin / yield / fiat marks, review state | catalog observation id and time, classification rules version |
| Traded volume, USD, last 24 hours | The exact sum of volume × vwap of the 24 hourly candles (volume × close when a traded candle has no vwap, as in Kraken's archive; the basis is recorded). Any missing hour withholds it | first and last close, base volume, candle runs, newest receipt time |
| Swing | `NATR` 14 on the hourly candles, from the indicator menu (TA-Lib, D118): the typical hourly true range as a percentage of the close | the engine's provenance: settings, menu version, status, candle count, runs, input hash |
| Swing relative to cost | NATR ÷ the round-trip rate in percent: maker 0.40% per side in `kraken-spot-starting-tier@1` (W1-B, D146) gives 0.80% | cost profile reference, content hash, rates, effective date |
| Spread | One public `GET /0/public/Ticker` for every pair: (ask − bid) ÷ mid, rounded to 12 significant digits | bid, ask, mid, last, Kraken's result key, receipt time, evidence label |
| Trend | `ROCP` 24 on the hourly candles: the change over the last 24 hours as a fraction (menu, TA-Lib) | the engine's provenance |
| Price-peg check (unreviewed coins only) | `HIGHEST_HIGH` and `LOWEST_LOW` 72 on the hourly candles (menu, TA-Lib) | both values with provenance and the result |

Library floats are rounded half-even to the twelve significant digits the library can vouch for
(`rounded_decimal`, D136) before any comparison or ranking. Money, volumes, prices and fees are exact decimals.

**No indicator formula is written here (D118).** The scanner's own code only sums volume × price (money
arithmetic), compares library outputs with levels (like an alarm), expresses the fee in the same unit as NATR and
ranks. NATR is chosen because it is the menu's only swing already measured as a share of the price, so no
division turns a price-unit swing into one. A swing indicator other than NATR is refused by the rules.

**What the swing means.** NATR on hourly candles is the typical *hourly* move. D126's "BTC 2.7%" figure is a
*daily* swing, so the numbers here are smaller: in the synthetic tests BTC-like data shows about 1% an hour,
1.2 times the 0.80% round-trip fee. The ranking only compares coins with each other, so the horizon does not change
the order; a different horizon would be a new rules revision.

**History needed.** NATR 14 is start-sensitive, so the engine reads 710 hourly candles (about 30 days, D136).
A coin with less history is `missing_swing` (status `warming_up`) and is not ranked: young listings wait a month.
The collector's 60-minute window is 720 candles, so a collector that runs at least every 30 days keeps enough.

## Exclusions

A pair can have several reasons; every one is recorded. Catalog reasons need no candles, so those pairs read none.

| Reason | When | Decision |
|---|---|---|
| `not_normal_status` | the catalog status is not `online` (`cancel_only`, `post_only`, `limit_only`, `reduce_only`) | D133 |
| `stablecoin`, `yield_token`, `fiat_currency` | the pair catalog marks the base asset (T25's dated rules) | D131, D127, D133 |
| `held_for_review` | U, STBL, UNITAS, CAP, DEBIT, RE or ST: D133 left them for the owner's eye; held until reviewed | D171 |
| `price_peg` | an unreviewed coin whose 72-hour low and high stayed inside 0.98–1.02 USD | D172 |
| `price_pinned` | an unreviewed coin whose 72-hour high is at most 2% above its low (a peg to another currency, or a dead market) | D172 |
| `peg_check_unavailable` | an unreviewed coin without 72 complete hours | D172 |
| `missing_candles`, `missing_volume`, `missing_swing`, `missing_trend`, `missing_spread` | that input is incomplete | D170 |
| `thin_volume` | under USD500,000 traded in 24 hours | D171 |
| `wide_spread` | spread above 0.50% of the mid | D171 |
| `evaluation_failed` | the candle store or indicator engine refused this pair; the other pairs carry on | D170 |

**Riba (D127).** The catalog holds only spot currency pairs: futures, perpetuals and margin are not in it, and its
leverage fields are ignored. Staking, lending and rebasing tokens are the catalog's yield marks. USDE is marked a
stablecoin. No other riba product type exists in Kraken's spot list as parsed; a new one would be a new mark in
the pair rules or a new hold here.

**Why USD500,000.** D138 lets one coin hold 20% of the USD10,000 paper account, USD2,000. The practice range
lets one fill take at most 10% of a bar's volume (D148), so a whole position needs USD20,000 traded per hour:
USD480,000 a day, rounded up. **Why 0.50%.** A protective exit sells at the bid and pays half the spread; at
0.50% that is 0.25%, still below the 0.40% maker fee of one side. Both are rules, not owner settings; changing
them makes a new ranking version.

**The price-peg check** exists because a list cannot know a stablecoin it has never seen: 608 of 677 USD coins
were not individually reviewed on 2026-10-13 (PAIR-CATALOG.md). It is a comparison of two library outputs with
levels, not a review: a coin that passes it is still flagged `not_individually_reviewed` on the shortlist.

## Ranking (D173)

Each pair that passed every check gets a place in four lists: traded volume (higher is better), swing relative to
cost (higher), spread (lower) and trend (higher). Equal values share a place, and the next place is skipped. The
score is the sum of the four places, so the factors count equally. The lowest score ranks first; equal scores go
to the pair with more traded volume, then to the symbol in alphabetical order. The result never depends on the
order the pairs were read in (property-tested), and a better value in any factor never moves a pair down.

The shortlist is the first 10 places. It is never padded: when fewer pairs pass, it is shorter.

The **ranking version** is `coin-scanner-r<revision>-<date>/<12 hex>`, the digest of every rule: thresholds,
indicator settings, cost profile, holds and the method. Any change gives a new version, and a stored shortlist
keeps the version it was made with.

## What is stored (migration 0031)

| Table | One row per | Holds |
|---|---|---|
| `coin_scans` | scan | mode, as-of, ranking version, status (`ok`, `no_candidates`, `failed` with a reason), evidence label, optimistic, counts, and the scan document (rules, catalog, ticker, cost profile, library versions, counts, every excluded pair by reason, flags) with its SHA-256 |
| `coin_scan_pairs` | pair that passed every check | place, score, per-factor places and the four values |
| `coin_shortlist_entries` | shortlist entry | every input value and its source time, the ranking version, flags |

Rows are append-only (SQL triggers), a replay row must be optimistic and a live one never is, places exist only for
a scan that ranked something and inside its counts, and an entry needs a shortlisted place. A read whose document
no longer matches its hash is refused. Nothing here is financial state or model context.

`CoinScanStore.latest()` returns the newest **live** scan; a replay is returned only when asked for by name, so
Research is never handed an optimistic replay as the current list.

**The coin rule for W3-A.** `CoinScanStore.coin_rule(symbol, as_of=..., max_age=2 hours)` answers D138 item 3:
`passed_checks` (True, with its place), `excluded` (False, with the reasons), `not_in_catalog` (False), or unknown
(`passed` None): `no_recent_scan` when the newest live scan is older than `max_age`, `scan_failed` when it failed.

## Replays

`scan(mode="replay", source=...)` reads a stated source (normally `kraken_ohlcvt_archive`) by event time only
(`availability_basis: event_time_only`) and is always labelled optimistic, with the reasons:

- `delisted_pairs_missing`: Kraken's history has no delisted pairs (D119, D134), so coin selection looks better
  than reality;
- `no_historical_spread`: there is no historical bid and ask, so spread is neither checked nor ranked;
- `catalog_observed_after_the_replayed_moment`, when the pair list is newer than the replayed hour;
- `cost_profile_after_the_replayed_moment`, when no fee revision was in force on the replayed day.

Archive candles have no vwap, so replay volume uses volume × close, and the archive omits quiet hours, so a thin
coin often shows a missing hour rather than a low volume. Either way it is not ranked.

## Commands

```bash
# One live scan: one public Ticker request, recorded in the paper state database.
trade-graph scan-coins --database runtime/trade_graph.sqlite --market-database runtime/market_data.sqlite
# Every hour at :05, 24 times (the hour's candle is collected a few minutes after it closes).
trade-graph scan-coins --scans 24 --at-minute 5
# Replay 48 past hours on the archive (optimistic).
trade-graph scan-coins --replay --as-of 2026-09-28T00:05:00Z --scans 48 --source kraken_ohlcvt_archive
# Read the newest live shortlist in plain words (read-only).
trade-graph coin-shortlist --database runtime/trade_graph.sqlite --format text
```

`scan-coins` needs an initialised paper database in a private (0700) folder and an existing candle file; it refuses
the financial database as a candle file (the D133 guard) and creates neither file. `--fixture-ticker` reads a
labelled fixture instead of Kraken. Each scan prints one JSON line with `public_requests`, `model_calls: 0` and
`paid_calls: false`. `coin-shortlist` opens the database read-only and never creates or upgrades it.

Running the scan every hour inside the paper service (after each candle collection) is wiring for the event
engine and install packages (W5-A, W6-D); until then `--scans` or a scheduled task runs it.

## Measured (synthetic data, cloud container, 2026-10-16)

- 100 pairs with 760 hourly candles each: one scan took about 4.5 s, so the full list of about 590 eligible
  pairs is about 25–30 s. Almost all of it is the candle store hydrating the 710 candles NATR reads per pair.
- Stored size for 25 pairs (12 passed): scan document 4 KB, places 2.3 KB, ten shortlist entries 31 KB (each
  entry carries the full provenance of four indicators). For the real list (about 590 pairs, perhaps 100 to 200
  passing) expect about 60–80 KB per scan, about 1.5–2 MB a day if run hourly. Rows are append-only; a retention
  rule is an open item.

## Not covered

- No real ranking has been made: the cloud could not reach Kraken, so the ticker fixture is synthetic and the
  recorded hourly candles (five days, 2026-10-13) are too short for NATR. The recorded-data test shows the
  determinism and the missing-input exclusions; the first real scan is step L2.
- Research reading the shortlist (T37, W6-A), HQ showing it, and the money limits using the coin rule (T32, W3-A).
- The paper coin list still needs the owner's policy revision at deployment (D138, L3).
