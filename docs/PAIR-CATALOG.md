# Kraken pair catalog and its classification (T25, slice A)

Status: built and tested in source on 2026-10-13. Not deployed. Paper only, public data only, no key.
Code: `contracts/pair_catalog.py`, `adapters/market/kraken_catalog.py`,
`application/pair_classification.py`, `application/collect_candles.py` (`refresh_catalog`).
Tests: `tests/unit/test_pair_catalog_contract.py`, `tests/unit/test_pair_classification.py`,
`tests/integration/test_kraken_catalog.py`. Fixture: `tests/fixtures/kraken/`.

## In one minute

- One public request, `GET /0/public/AssetPairs`, lists every Kraken spot pair with its trading status,
  smallest order and price step.
- Software keeps the USD-quoted pairs as a dated list in the candle database. An unchanged list adds no row;
  a change (a new coin, a status flip) adds a new dated row and the old one stays.
- Each pair is marked if its coin is a **stablecoin** or a **yield-paying token** (D131; D127 for riba), or is a
  **fiat currency** listed against USD. Marked pairs are not eligible for collection.
- Coins nobody has looked at stay unmarked, and every report lists them as **not individually reviewed**.
  An unmarked pair is never "verified clean".

## Names

Kraken calls Bitcoin `XBT` and Dogecoin `XDG`. The engine uses `BTC/USD` and `DOGE/USD` (`engine_symbol`).
Only these two whole codes are renamed. `AIXBT`, `XBTC`, `DOG` and `DOGS` are different assets.
The symbol comes from Kraken's `wsname` (`XBT/USD`). The pair code used for requests is `altname`
(`XBTUSD`), which is what `PairRecord.source_pair` and `Candle.source_pair` hold. `base` and `quote` keep
Kraken's own asset codes (`XXBT`, `ZUSD`).

Kraken's older codes carry an `X` or `Z` prefix (`XXBT`, `ZEUR`). The parser cross-checks them against the
`wsname` using an explicit table, not by stripping letters: `ZCHF` is Frankencoin, a Swiss-franc stablecoin,
and `ZETA` and `XION` are ordinary coins. A pair whose codes disagree with its `wsname` is rejected as
`asset_code_mismatch`, so an unknown new naming pattern is visible instead of silently mislabelled.

## Trading status

Kraken's statuses are `online`, `cancel_only`, `post_only`, `limit_only` and `reduce_only`. **Only `online` is
normal.** The other four restrict the order types the engine needs (market-type protective exits, new buying)
or mean the pair is winding down (`cancel_only` often comes before a delisting). Such a pair stays in the
catalog with its real status. It is not eligible for collection until it is `online` again, and the dated
observations show when it changed. An unrecognised status is a rejected pair, not a guess.

`PairRecord.in_normal_status` and `PairRecord.eligible_for_collection` (normal status and no exclusion) are the
two checks. The collector's pair scope (slice B) starts from `eligible_for_collection`.

## Classification rules

`application/pair_classification.py` holds `ASSET_RULES`, a table with one entry per base coin:

| Class | Meaning | Excluded |
|---|---|---|
| `stablecoin` | pegged to a currency (dollar, euro, pound, franc, peso, real) | yes (D131) |
| `yield_token` | accrues or pays yield by being held: liquid-staking and restaking receipts, rebasing coins | yes (D131, D127) |
| `stablecoin_and_yield_token` | both, for example a rebasing dollar | yes |
| `fiat_currency` | a fiat currency listed against USD (EUR, GBP, AUD, CAD, JPY) | yes |
| `reviewed_not_marked` | looked at and not marked; the entry says why | no |
| `not_individually_reviewed` | no entry; **not** verified clean | no |

Every entry has a one-line `reason` and a `basis`, a note on where the judgement comes from:

- "Kraken announcement or asset page found by web search, read 2026-10-13", or "Issuer, protocol or news page
  found by web search, read 2026-10-13": the entry was checked against a page that day.
- "Agent knowledge of the issuer's published design; not re-checked against its documents on 2026-10-13": the
  well-known coins (USDT, USDC, DAI and similar). This is a judgement by the implementing agent, not an audit.

The table is versioned (`CLASSIFICATION_RULES_VERSION`, now `pair-rules-2026-10-13.1`) and dated. Every stored
catalog row and every run records the version. To change a judgement, edit the table and bump the version; old
rows keep the version they were made with.

Judgement calls the owner may revise:

- **Native proof-of-stake coins are not yield tokens.** ETH, SOL, ADA, DOT, AVAX and ATOM pay rewards only to
  holders who stake them, which the system never does, so they are `reviewed_not_marked`. Receipt tokens that
  accrue yield just by being held (LSETH, LSSOL, MSOL, JITOSOL, METH, CMETH, OUSD) are marked.
- **Fiat currencies are excluded** as their own class, although D131 names only stablecoins and yield tokens:
  EUR/USD is a currency pair with nothing to day-trade as a coin.
- **Names can mislead.** `USDUC` ("Unstable Coin") is a parody memecoin and `STABLE` is a blockchain token;
  both are `reviewed_not_marked`. `ZCHF` and `USTABLES` look unusual but are stablecoins.
- `USDE` is marked a stablecoin. Its design relies on hedging with perpetual futures, which D127 excludes as
  trading products; that is a property of the issuer's reserve, not of buying the token, and is left to the owner.

### What this cannot do

A list cannot notice a stablecoin or yield token it does not know. On the full response recorded on
2026-10-13, 677 pairs were USD-quoted and the table named 69 of their coins (44 pairs marked), so 608 coins,
and 566 of the 591 pairs eligible for collection, are unreviewed.
The scanner (T31) adds a **price-peg check** on observed prices (a coin that sits at one dollar, or at
another currency's value, for days is flagged whatever its name). It is not part of this slice; see
[COIN-SCANNER.md](COIN-SCANNER.md) (D172).

## The catalog report

`refresh_catalog(store, clock, transport, evidence_label)` records a run of kind `catalog` and returns its report:

- `status`: `ok`, `partial` (some pair was rejected) or `failed`. `error` is one of the fixed strings
  `public_catalog_request_failed`, `public_catalog_invalid` or `catalog_persistence_failed`; response text is
  never included.
- Counts: pairs in the response, spot currency pairs parsed, USD pairs, other-quote pairs by quote code,
  out-of-scope pairs (another asset class), rejected pairs (count, reasons, first 200 with their result key).
- `by_status` and `by_class` over the USD pairs, `excluded_pairs`, `eligible_for_collection`,
  `not_normal_status`, `reviewed_asset_count`, `unreviewed_asset_count` and the full `unreviewed_assets` list.
- `request_count` (1), `paid_calls_enabled` false, `live_trading_enabled` false, `price_peg_check`
  "not_applied", the evidence label and the rules version.

The run is stored before the request. A process that dies mid-request leaves a run with no result, which the
store shows as `interrupted`. `evidence_label` is one of `synthetic_fixture`, `recorded_public_fixture`,
`live_public_smoke` or `live_public`; a scripted or recorded response must never carry a live label.

## Bound on the response

The transport takes a byte limit of up to 16 MiB. The catalog uses **8 MiB** (`CATALOG_MAXIMUM_RESPONSE_BYTES`).
The real response on 2026-10-13 was 678,583 bytes for 1,460 pairs, so the limit leaves about twelve times that
room for new listings and fields, and is half of the hard ceiling so a runaway response cannot exhaust memory.
`make_catalog_transport()` builds the real transport with that limit and a 30 second elapsed deadline. The
parser measures bytes again, so a scripted transport cannot bypass the bound.

## Not covered

- Which pairs the owner's own Kraken account may trade in the EU is not checked here. The list is Kraken's
  international catalog.
- Leverage and margin fields in the response are ignored (D127: spot only).
- Candle collection, gap reports and the command line are slice B.
