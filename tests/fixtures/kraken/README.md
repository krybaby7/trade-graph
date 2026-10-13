# Kraken recorded public fixtures

Trimmed public exchange data. No account, key or private information is in these files.

## asset_pairs_2026-10-13.json

- Source: `GET https://api.kraken.com/0/public/AssetPairs` (public, no key, default parameters).
- Recorded: 2026-10-13 at 14:58:32 UTC, with one plain `curl` request (HTTP 200, 678,583 bytes,
  empty `error` list). The full response held 1,460 pairs: 1,365 `online`, 78 `cancel_only` and
  17 `post_only`; 677 of them were quoted in USD.
- Trimmed to 53 pairs. Every kept entry is the exchange's own entry, unmodified, in the exchange's
  own order. Nothing was added or edited by hand. The numbers are equal to the recorded response
  (checked when the file was made).
- What the subset covers: BTC (`XXBTZUSD`, wire name `XBT/USD`) and DOGE (`XDGUSD`, `XDG/USD`)
  renames, ETH, SOL and other large coins; stablecoins, including a euro coin (`EURC`), a Swiss
  franc coin (`ZCHF`, which is not a legacy `Z` fiat code) and `USDUC`, a memecoin whose name looks
  like a stablecoin; liquid-staking and yield tokens; fiat currencies listed against USD (`EUR`,
  `GBP`, `AUD`); pairs with status `cancel_only` and `post_only`; and pairs quoted in other
  currencies (EUR, GBP, USDT, USDC, PYUSD, BTC) to prove they are filtered.
- The fixture is evidence of the response format on that day, not a current list of tradable
  pairs. Tests read this file; they never call the network.
