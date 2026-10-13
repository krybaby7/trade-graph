# Recorded Kraken public Trades pages

Six real responses from `GET https://api.kraken.com/0/public/Trades`, recorded on 2026-10-13 with plain `curl` GET
requests (no credentials, no cookies sent), at least seven seconds apart, from this repository's T26 backfill work.
They are public market data. Prices, volumes, times and trade ids are byte-identical to what Kraken served; only
rows were removed (see "Trimmed" below). The evidence label for tests that use them is `recorded_public_fixture`.

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

## What the recordings showed

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
