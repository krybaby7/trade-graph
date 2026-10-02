# Kraken offline identity and reconciliation continuation — 2026-10-02

T19 remains in progress. This slice verifies synthetic wire data and real local
SQLite recovery. It makes no private Kraken request and does not establish owner
eligibility, current key permissions, venue acceptance, economic evidence or live
authority. Paid provider calls and real orders remain disabled.

## Resulting behavior

Order parsing is side-effect-free until a complete open-order response or a unique
client lookup has passed validation. Overlapping open/closed snapshots, inconsistent
closed-order counts, reused client IDs, malformed later rows and changing native
order/client associations cannot establish a partial ownership cache. Cancellation
accepts one bounded native ID or one validated client ID, never a comma-separated
batch. A malformed acknowledgement remains uncertain, with no retry. Broker and
transport write-readiness flags require actual booleans.

Native history validates trade identities, complete declared counts, bounded
ledger references (at most 20 per trade) and exactly the requested QueryLedgers
identities in each batch. A later response cannot replace an earlier ledger record.
Explicit spot provenance is required: order descriptions must contain leverage
`none`; fill margin/leverage must both be present and zero, without a position
status. Unrepresented third-asset movements and negative native fee entries are
refused individually, including offsetting entries that would otherwise disappear
when summed. The transport refuses duplicate JSON keys, nonfinite JSON numbers and
parser nesting deeper than 64 containers; an ambiguous write response remains uncertain.

The new protected, read-only `DurableBrokerIdentity` can be supplied directly:

```python
identity = DurableBrokerIdentity(
    database, venue="kraken", account_id=protected_account_id, mode="live"
)
broker = KrakenLiveBroker(
    protected_transport, account_id=protected_account_id,
    intent_resolver=identity,
)
```

The resolver checks the native authorized-intent schema and its intent, portfolio,
client and venue/account/mode indexes. UUID32 and UUID36 client aliases normalize
consistently; collisions and mismatched supplied client/venue IDs fail closed. A
verified client ID may resolve an intent whose venue ID has not yet been persisted.
With a cold adapter cache, an exact persisted venue ID resolves terminal history.
Neither identity may select an intent in another execution scope. Missing ownership
returns `None`, keeping the account reconciliation gate incomplete. The resolver
does not mutate the journal or discover ownership from quantity/price coincidence.

## Source evidence

Bounded public reads again obtained official source files at these existing pinned
commits. Direct Kraken REST documentation and GitHub commit-discovery API reads
failed with proxy errors; the pinned commits were not verified as current HEAD.

| Source | Evidence used |
|---|---|
| [Kraken Rust account types](https://github.com/krakenfx/kraken-api-sdk/blob/a5d253c02036dd74f6fc5976cba188e39e8c1b4b/rust/src/api/account/types.rs#L420) | Order leverage `none` versus margin; trade margin/leverage, position status, native ledger fields and total-count pagination. |
| [Kraken Go REST requests](https://github.com/krakenfx/api-go/blob/a8484bc5ec985fd5ce5bcc0580f659727d8f7603/pkg/spot/rest.go#L229) | Typed history fields, client filtering and transaction-ID lookup. |
| [Kraken Rust time-in-force enum](https://github.com/krakenfx/kraken-api-sdk/blob/a5d253c02036dd74f6fc5976cba188e39e8c1b4b/rust/src/api/trade/types/enums.rs#L155) | SDK serializes lowercase `gtc`/`ioc`; this differs from the adapter's uppercase fixture mapping. Direct REST or authorized conformance must resolve this discrepancy before order acceptance is claimed. |

## Verification and remaining work

The focused command passed **232 tests**, with zero failures, errors or skips,
using frozen dependencies and an isolated test temporary directory:

```text
uv run pytest --basetemp=/tmp/trade-graph-t19-verification \
  --junitxml=/tmp/trade-graph-t19-focused.xml \
  tests/integration/test_kraken_live_adapter.py \
  tests/integration/test_kraken_identity_recovery.py \
  tests/integration/test_broker_identity.py \
  tests/integration/test_execution.py \
  tests/integration/test_execution_acceptance.py \
  tests/integration/test_execution_reconciliation_ordering.py \
  tests/integration/test_api_security_live.py \
  tests/unit/test_protocols.py tests/e2e/test_offline.py
```

Targeted Ruff checks and `git diff --check` passed. Tests exercise malformed and
reused identities, exact ledger batches,
unsupported native movements, required spot fields, strict write flags and malformed
JSON. A real SQLite restart test retains an UNKNOWN intent and its reservation
through two ambiguous lookups, then books one fee-bearing fill once after the
evidence becomes unique. A second terminal FILLED restart reconstructs ownership
from its durable venue ID without an active-order lookup, repeat submission or
duplicate fill. Existing late-history and 28-digit protected-ledger precision refusal
tests continue to run; this slice does not expand those financial contracts.

Actual authorized read-only conformance remains necessary for account fees,
permissions, client-ID reuse, pagination, boundary timestamps, ledger rounding and
time-in-force casing. Rounded trade costs, multiple native fee assets, rebates and
earlier newly discovered history still require financial replay/contract work or a
documented safe refusal before live acceptance. Native stops remain untested and
disabled on submission.

One protected-core follow-up remains explicit: a lost submit acknowledgement followed
by a partial-fill cancellation can enter CANCELLED without persisting the lookup's
venue ID. The read-only identity helper intentionally refuses that cold unbound
history. Persisting a proven venue identity during that reconciliation transition
requires a coordinated execution change; this slice leaves execution and migrations
unchanged. T17 delivery and owner-selected venue/least-privilege authenticated
verification remain T19 completion prerequisites. Real-order/protection tests require
their separate owner authority under T20.
