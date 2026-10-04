# Retained venue verification in async execution — 2026-10-04

Protected lifecycle readiness can synchronously verify a retained native observation
inside an async execution writer transaction. The public proof shape, source pins,
MACs, scope, freshness, exact receipt bytes/order and request-consumption checks are
unchanged. Actual account/private conformance and pilot readiness remain pending.

The verifier constructs its broker with the exact in-memory retained replay closure.
Every existing normalization await resolves through that closure and local broker
state; it has no transport or private network capability. The trusted bounded
pipeline coroutine is driven once with `send(None)` and closed in `finally`.
Completion returns the same summary as outside an active loop. Any yielded await
is refused as a changed retained-normalization contract and is never resumed.
There is no nested event loop, worker thread, scheduling or network fallback.

This preserves replay of successful captures from an incomplete stage and the
requirement that every retained response is consumed. Changed source identity still
produces a pending proof, and a mismatched retained request still refuses. Injected
transport captures remain synthetic and cannot establish authenticated reads or a
permanent least-privilege permission inventory.

The dedicated regressions compare identical proofs inside/outside a running loop,
including changed source digests; forbid creating a transport or invoking
`asyncio.run` during verification; reject tampered retained requests in either
context; and inject an actual suspension, asserting its coroutine closes, never
resumes, adds no async task and makes no further transport request. **319 tests**
passed with zero failures, errors or skips. Targeted Ruff, diff checks and staged/test
artifact hygiene passed. The JUnit report contains synthetic test results only.

```sh
PYTHONPATH=/workspace/trade-graph-t19-takeover/src \
  /workspace/trade-graph/.venv/bin/python -m pytest -q \
  --basetemp=/tmp/t19-retained-async-final \
  --junitxml=/tmp/t19-retained-async-final.xml \
  tests/integration/test_venue_conformance.py \
  tests/integration/test_native_quote_cost.py \
  tests/integration/test_kraken_live_adapter.py \
  tests/integration/test_kraken_identity_recovery.py \
  tests/integration/test_live_readiness.py \
  tests/integration/test_execution_reconciliation_ordering.py
```
