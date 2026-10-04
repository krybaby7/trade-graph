# Kraken native conformance integrity — 2026-10-04

T19 remains in progress. This checkpoint tightens existing native metadata and
retained read-only conformance contracts. No private Kraken request, exchange
credential, real order, cancellation, paid call or deployment was used.

## Resulting behavior

Instrument readiness requires the native response to contain exactly the
normalized selected symbols. A BTC/USD grant cannot mark instruments observed
from an ETH/USD response. Asset/pair aliases must be unambiguous, and asset,
pair, rule and fee registries publish as one validated snapshot. A failed refresh
keeps the previous coherent registry. Negative or malformed individual fee tiers
are refused even when another tier has a larger valid rate.

Read-only collection starts its duration before setup, checks response times
against that duration and the grant expiry, and retains previously completed
facts when an actual asynchronous deadline interrupts a later stage. Source
digests are pinned before requests and compared again after collection.
The fact window ends at the last retained native response. Local cancellation,
transport cleanup and sealing do not refresh earlier facts or extend the grant;
an expiry-limited timeout remains verifiable after cleanup crosses its deadline.

Verification independently replays successful native captures from the first
incomplete stage. A signed pending-label omission cannot hide a malformed balance
response. Every retained response must be consumed in the native request order;
extra responses, unrequested parameters, overlapping/reordered receipt times and
captures after the granted duration are refused. Missing successful responses
remain explicit transport-evidence gaps. The public `PinnedVenueObservation` and
`VerifiedReadOnlyAccountObservation` fields are unchanged; pending reasons are
additive.

## Verification

The isolated worktree import path was asserted. The focused command passed **290
tests**, with zero failures, errors or skips, using the existing frozen dependency
environment and an isolated temporary directory:

```sh
PYTHONPATH=/workspace/trade-graph-t19-takeover/src \
  /workspace/trade-graph/.venv/bin/python -m pytest -q \
  --basetemp=/tmp/t19-takeover-focused \
  --junitxml=/tmp/t19-takeover-focused.xml \
  tests/integration/test_kraken_live_adapter.py \
  tests/integration/test_kraken_identity_recovery.py \
  tests/integration/test_broker_identity.py \
  tests/integration/test_venue_conformance.py \
  tests/integration/test_execution.py \
  tests/integration/test_execution_acceptance.py \
  tests/integration/test_execution_reconciliation_ordering.py \
  tests/integration/test_submission_completion.py \
  tests/integration/test_api_security_live.py \
  tests/unit/test_protocols.py tests/e2e/test_offline.py
```

Twenty-one new regressions exercise exact scope and alias handling, coherent failed
refresh, individual unsupported fee tiers, independent failed-stage replay,
unconsumed or unrequested native captures, serial receipt time checks, granted
duration and real asyncio deadlines at both duration and grant expiry with
synthetic transport. The adapter/collector subset passed 136 tests. Targeted Ruff and `git diff --check`
passed. Evidence files are private-free temporary JUnit reports, not account
observations.

## External gates still open

The direct Kraken API-key-info documentation request failed with `ProxyError`
through the configured proxy. Bounded public reads of the previously pinned
Kraken-owned Rust account methods and Go REST requests succeeded; they do not
establish current private acceptance or a complete permission-introspection
contract. No permission endpoint was added from speculation.

T17 delivery, owner-selected eligibility, native account identity, permanent
least-privilege key inventory with funding/withdrawal permissions absent, actual
authenticated fee/order/history reconciliation, intended-host identity and
separately authorized write/cancel/protection conformance remain pending.
Adapter method absence and a signed owner declaration do not prove permanent
withdrawal permission absence. Native stops stay untested and disabled. Rounded
native cost, multi-asset fee, rebate and late-history replay limits retain their
existing safe refusals and require a protected financial contract before live
acceptance. Real pilot authority remains a separate T20 owner decision.
