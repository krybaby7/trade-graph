# Kraken spot adapter foundation — 2026-10-02

This checkpoint implements normalized, bounded reconciliation infrastructure and
synthetic broker/transport tests. **T19 remains pending.** No private Kraken API,
exchange credential, real order, cancellation, withdrawal, account eligibility
check or live protection test was performed. T17 functional delivery and T18
economic evidence do not provide live authorization.

## Implemented behavior

`KrakenLiveBroker` accepts an injected synchronous or asynchronous callable. The
protected `KrakenRestTransport` supports public `Assets`/`AssetPairs` and allowlisted
private account reads. Neither object exposes a withdrawal/funding method. The
transport refuses order writes by default and has no arbitrary endpoint or redirect
capability. The broker independently defaults to `live_enabled=False` and
`key_present=False`. These flags are necessary conditions; a later live controller
must enforce separate owner eligibility, allocation, budget and execution authority.
The paper CLI/dashboard do not assemble this adapter.

Metadata normalizes Kraken asset and pair aliases into native symbols using the
asset registry. BTC/XBT aliases are explicit; arbitrary X/Z prefixes are retained.
Precision uses Decimal tick size and base volume decimals. Both base quantity and
quote notional minimums are retained. Missing pair status is unknown and prevents
new submissions. Public fee percentages become fractions and supply a conservative
maximum execution reserve. A separately requested account fee lookup cannot lower
that conservative bound. Missing/expired fee and instrument readiness must be
resolved before execution reserves funds: submission refuses to load new fees
inside its write path. The default readiness lifetime is 300 seconds.

`BalanceEx` returns total native spot holdings, including held funds. The adapter
also retains `balance_holds` and `available_balances` with the same snapshot basis.
Earn/staking suffix assets are preserved as separate asset identities. Margin
credits, inconsistent holds and duplicate native aliases are explicit refusals.
The common `BalanceSnapshot` DTO carries total holdings; callers must consult the
held/available projection rather than count all holdings as freely spendable.

Order lookup uses `QueryOrders {txid}` for a known venue ID. Client-only lookup uses
filtered `OpenOrders` and `ClosedOrders`, never unsupported `QueryOrders {cl_ord_id}`.
UUID forms normalize to the same stable 32-character identity. A successful empty
sweep, invalid order ID, reused client ID, incomplete filtered history or failed
query remains unknown. Canceled/expired orders retain partial filled quantities.
Cancellation sends one identifier and always requires subsequent status/fill
reconciliation. Invalid-order errors do not prove successful cancellation.

Trades are retrieved with a fixed end timestamp, stable total-count checks,
`consolidate_taker=false`, ledger references and a protected page bound. The entire
bounded window is sorted by exact Decimal timestamp, native trade sequence where
available, and stable trade transaction ID before DTO timestamp conversion. The
restartable cursor retains the frozen window/count/offset. Repeated or incomplete
pages, changing counts, future trades and unsupported cursors fail explicitly.
The 50-row REST offset convention is fixture-tested here; current direct REST
documentation and private conformance still need verification. Short or oversized
pages cannot silently complete an inconsistent history.

Each fill uses the trade transaction-map key as its stable identity, rather than
the parent order ID. Native fees come from requested `QueryLedgers {id}` entries,
with unique ledger references, trade links, asset units and signed trade legs
validated. Quote-valued TradesHistory fees cannot be divided by price to invent a
base fee. Missing maker attribution, ledger gaps, native amount disagreements,
multiple native fee assets, rebates and costs whose rounded native total cannot
be represented by the shared quantity-times-price contract remain unsupported.
Those cases require a financial-contract extension or more conformance evidence;
they are not replaced with estimated successful fills.

For restart recovery, an installation supplies a protected
`intent_resolver(client_order_id, venue_order_id)` backed by durable intent records.
Status lookup establishes the client/venue association before fill mapping. Unknown
external trades retain `intent_id=None`; they do not become authorized portfolio
records. No process-local submission map establishes ownership after restart.
An optional owner-selected `history_start_utc` defines the audited account window;
the default is the complete history subject to its explicit bound. Exceeding that
bound refuses reconciliation rather than dropping old fills.

REST signing uses the exact transmitted form body, a serialized per-key nonce and
HMAC-SHA512. The default nonce increases within one process even if its clock
regresses; installations needing a floor across restarts must supply a durable
nonce source and must not share a key between independent senders. HTTP errors,
malformed acknowledgements and ambiguous writes are not retried automatically.
Responses stream under a byte cap and overall deadline, with compression and
redirects refused. Errors expose safe categories rather than private wire bodies.

## Verified source evidence

Direct reads of `docs.kraken.com`, `api.kraken.com` and `support.kraken.com` were
blocked by this execution environment's proxy with HTTP 403 CONNECT responses.
Kraken-owned GitHub source/documentation was accessible and was read at these
pinned commits; documentation review is not exchange acceptance:

| Wire behavior | Official source read |
|---|---|
| Assets, ticks, quantity/cost minimums, public fee percentages | [Rust market types](https://github.com/krakenfx/kraken-api-sdk/blob/a5d253c02036dd74f6fc5976cba188e39e8c1b4b/rust/src/api/market/types.rs#L418) |
| Missing pair status and slashless keys | [Rust market-data guide](https://github.com/krakenfx/kraken-api-sdk/blob/a5d253c02036dd74f6fc5976cba188e39e8c1b4b/rust/docs/guides/market-data.md#L5) |
| BalanceEx totals/holds; native ledger fees; trade quote fees/maker/ledger IDs | [Rust account types](https://github.com/krakenfx/kraken-api-sdk/blob/a5d253c02036dd74f6fc5976cba188e39e8c1b4b/rust/src/api/account/types.rs#L309) |
| Client filtering and QueryOrders transaction IDs | [Go REST request structs](https://github.com/krakenfx/api-go/blob/a8484bc5ec985fd5ce5bcc0580f659727d8f7603/pkg/spot/rest.go#L256) |
| QueryLedgers `id` parameter | [Rust account methods](https://github.com/krakenfx/kraken-api-sdk/blob/a5d253c02036dd74f6fc5976cba188e39e8c1b4b/rust/src/api/account/mod.rs#L344) |
| ClosedOrders/TradesHistory offset and count fields | [Rust account requests](https://github.com/krakenfx/kraken-api-sdk/blob/a5d253c02036dd74f6fc5976cba188e39e8c1b4b/rust/src/api/account/requests.rs#L55) |
| Accepted UUID32/UUID36/short client IDs | [Rust identifier validation](https://github.com/krakenfx/kraken-api-sdk/blob/a5d253c02036dd74f6fc5976cba188e39e8c1b4b/rust/src/types/ids.rs#L174) |
| Just-placed orders can be briefly absent | [Rust wire-quirks guide](https://github.com/krakenfx/kraken-api-sdk/blob/a5d253c02036dd74f6fc5976cba188e39e8c1b4b/rust/docs/guides/wire-quirks.md#L71) |
| Fee currency flags are preferences | [Rust trade enums](https://github.com/krakenfx/kraken-api-sdk/blob/a5d253c02036dd74f6fc5976cba188e39e8c1b4b/rust/src/api/trade/types/enums.rs#L45) |
| Invalid-order cancellation ambiguity and nonce constraints | [Rust error-handling guide](https://github.com/krakenfx/kraken-api-sdk/blob/a5d253c02036dd74f6fc5976cba188e39e8c1b4b/rust/docs/guides/error-handling.md#L109) |
| Exact-body signing | [Rust signer](https://github.com/krakenfx/kraken-api-sdk/blob/a5d253c02036dd74f6fc5976cba188e39e8c1b4b/rust/src/auth/signer.rs#L38) |

The account-specific fee shape, endpoint permission labels, client-ID reuse scope,
private pagination convention and uppercase time-in-force wire casing still need
direct current REST or separately authorized conformance. Uppercase GTC/IOC are
used by the fixture write mapping; native stops remain untested and disabled in
submission even though venue support is described by capability metadata.

## Verification and remaining gate

Credential-free tests exercise normalization, genuine SQLite ledger restoration,
UNKNOWN execution without resubmission, exact fees and native amounts, chronological
pagination/cursors, UUID aliases, precision/minimum/status refusals, write defaults,
native fee provenance, malformed responses, exact-body signing, nonce ordering,
redirect/error handling, response streaming bounds and deadline cancellation.
All provider/exchange/account payloads are synthetic. Tests never call the private
exchange or establish owner eligibility or profitability.

Review also identified core execution requirements before live acceptance: resolve
all order identities before ingesting one globally chronological fill stream across
intents, and refuse unknown or insufficient live fee reserves before authorization
and dispatch. Those integration changes belong to the protected execution owner;
the adapter checkpoint does not claim that this gate has passed.

T19 stays pending until the bounded reconciliation protocol, durable ownership,
account permissions/fees, selected venue eligibility and actual authorized private
read-only conformance are verified. T20 live allocation and real-order/protection
tests require their separate explicit owner authority. Keep withdrawal/funding
permissions absent from any key supplied later.
