# Coin scanner fixtures (T31)

**Synthetic.** Nothing in this folder is a recording. kraken.com and api.kraken.com were unreachable from the cloud
build session on 2026-10-16 (the network policy refused the connection), so no public Ticker answer could be recorded.
Tests that read these files label their results `synthetic_fixture`.

## ticker_synthetic.json

- Format: Kraken's public `GET https://api.kraken.com/0/public/Ticker` with no `pair` parameter, which answers for
  every tradeable pair at once. Each entry keeps Kraken's field names: `a` (ask price, whole-lot volume, lot volume),
  `b` (bid, the same), `c` (last trade price and volume), `v`, `p`, `t`, `l`, `h` (today and the last 24 hours) and
  `o` (today's opening price). Prices are text, as Kraken sends them.
- Wrapper: `evidence_label` (always `synthetic_fixture` here), `received_at_utc` (the receipt time a test uses) and
  `response` (the answer itself).
- Cases: legacy result keys (`XXBTZUSD`, `XETHZUSD`) that match a pair through its Kraken base and quote codes, plain
  keys (`SOLUSD`, `LINKUSD`, `USDUCUSD`), a pair quoted in another currency (`XXBTZEUR`), and an entry without a bid
  (`XDGUSD`).
- The prices are made up and are not market data. A real recording of one answer is step L2 on the owner's PC.

The recorded Kraken files (pair list and candles, 2026-10-13) stay in `tests/fixtures/kraken/`.
