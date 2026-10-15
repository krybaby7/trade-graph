# Kraken recorded public fixtures

Trimmed public exchange data. No account, key or private information is in these files. Tests read them and never
call the network. They show the format on the day they were recorded and are not live results; their evidence label
is `recorded_public_fixture`.

## asset_pairs_2026-10-13.json (T25 pair catalog)

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
- The fixture is evidence of the response format on that day, not a current list of tradable pairs.

## ohlc_*.json (T25 candle collector)

- Source: `GET https://api.kraken.com/0/public/OHLC?pair=<code>&interval=<minutes>[&since=<unix>]`
  (public, no key). 17 plain `curl` requests on 2026-10-13 between 15:17:41 and 15:23:41 UTC by the host
  clock, at least 4.6 seconds apart, all HTTP 200 with an empty `error` list. This is separate from the
  single AssetPairs request above, so 18 public requests in all. `ohlc_manifest.json` holds, for every
  request, the pair code, interval, `since`, start and finish time, the exchange's `Date` header, the byte
  size, how many rows the response had and how many are kept. Tests use the finish time as the receipt
  clock. Nothing is edited.
- Trimming: each response is the exchange's own text with the older rows cut off. The last 120 committed rows
  and the final, still forming row are kept in the exchange's order (300 for `ohlc_USDUCUSD_1.json`), so
  `result.last` is still the open time of the last committed row. Complete responses had 721 rows
  (720 committed plus the forming one).
- Pairs and intervals: `XBTUSD` (result key `XXBTZUSD`) at 1, 5, 15 and 60 minutes; `SOLUSD` at 1, 5, 15 and
  60; `LINKUSD` at 1 and 60; `USDUCUSD` (a thin coin) at 1 and 60; `PAXGUSD` at 1; `ACAUSD` (a cancel-only
  pair with no data in the window) at 1.
- Since probes: `ohlc_XBTUSD_15_since1791903600.json` and `ohlc_XBTUSD_15_since1791902700.json` show that
  `since` is inclusive (the candle that opens at `since` is returned) and that the forming row is still last.
  A `since=1` request for the 60-minute BTC series returned exactly the same 721 rows as the request without
  `since` (manifest entry `XBTUSD_60_since1`, not stored again).
- What they show: quiet intervals are present as flat rows with vwap 0, volume 0 and trade count 0
  (`USDUCUSD_1`: 678 of 720 minutes; `PAXGUSD_1`: 365 of 720; `LINKUSD_1`: 76 of 720; `USDUCUSD_60`: 71 of 720
  hours); no interval is omitted; a pair with no data returns one row with time 0 and zero prices (`ACAUSD_1`).
  The exchange's `Date` header ran 153 seconds behind the host clock in every request (see
  docs/KRAKEN-CANDLE-COLLECTION.md).

## trades_*.json (T26 Trades backfill)

Six real responses from `GET https://api.kraken.com/0/public/Trades`, recorded on 2026-10-13 with plain `curl` GET
requests (no credentials, no cookies sent), at least seven seconds apart. Prices, volumes, times and trade ids are
byte-identical to what Kraken served; only rows were removed (see "Trimmed" below).

| File | Request | Served (Kraken `Date` header, UTC) | Rows kept |
|---|---|---|---|
| `trades_xbtusd_recent_tail.json` | `pair=XBTUSD&count=1000` | 2026-10-13 13:15:14 | last 40 of 1000 |
| `trades_xbtusd_since_cursor.json` | `pair=XBTUSD&since=1791897312329940052&count=10` | 13:15:37 | all 10 |
| `trades_xbtusd_since_2020.json` | `pair=XBTUSD&since=1577836800&count=5` | 13:16:13 | all 5 |
| `trades_batusd_recent_tail.json` | `pair=BATUSD&count=1000` | 13:16:21 | last 30 of 1000 |
| `trades_xbtusd_one_trade_24h_before.json` | `pair=XBTUSD&since=1791811008&count=1` | 13:16:49 | all 1 |
| `trades_batusd_one_trade_24h_before.json` | `pair=BATUSD&since=1791811016&count=1` | 13:16:56 | all 1 |

Trimmed: the two `count=1000` pages were cut to their **last** rows so that the served `last` cursor still describes
the final row. Nothing else was edited. The untrimmed pages were not committed.

What the recordings showed:

* `since` accepts Unix seconds (`since=1577836800` returned trades from 2020-01-01) and the nanosecond cursor that
  Kraken returns as `last`. The documentation does not state either unit.
* `since` is **inclusive**: the page requested with the first page's `last` (file 2) begins with that page's final
  trade (id 111249195) again.
* `last` is the final returned trade's time in nanoseconds (it carries a few digits that the row's seven-decimal
  `time` rounds away). Trade ids were consecutive within every recorded page. Many trades share one timestamp.
* The response key is `XXBTZUSD` for the request `XBTUSD` but `BATUSD` for `BATUSD`.
* Busy pair, untrimmed page: 1000 trades spanned 774.7 s (id 111248196 to 111249195, 13:02:17 to 13:15:12 UTC), a
  68,803 byte response. Quieter pair: 1000 trades spanned 5,807.9 s (ids 1541100 to 1542099), 65,992 bytes.
* Trade ids 24 hours apart (files 5 and 6 against the untrimmed pages) give one day's trade count: BTC/USD
  111,084,931 on 2026-10-12 13:16:53 UTC to 111,249,204 on 2026-10-13 13:15:20 UTC is 164,273 trades; BAT/USD
  1,528,419 to 1,542,099 is 13,680 trades. See `docs/KRAKEN-TRADES-BACKFILL.md`.
* The computer's clock read 153 seconds later than Kraken's `Date` header on every request.
