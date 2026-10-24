# T15 — authoritative dashboard and protected controls

Recovered starting checkpoint: `85bfb1c424cbcde9947ab4d31dc10c5a5125343f` on
`cursor/trade-graph-r1-548a`, preserving the verified T14 implementation.
Final implementation: `8760fb2667d4e677cac67646a01e86bdb4d563eb`;
tree: `a75fe189ae9dfb5b639fe1aae4cacef961602f9e`.

## Result and acceptance scope

T15 delivers the FastAPI financial/organisation/cost/change projections, responsive server-rendered pages,
authenticated owner policy/budget/pause controls, bounded Leader assignments and redacted evidence navigation.
The default account remains USD10,000 paper capital with EUR reporting and a separate real expense allowance.
Live prerequisites are visible, but actual live implementation and authorization are false.

**A34:** all page values come from the corresponding authoritative projection, within one database snapshot.
Ledger flows, FIFO results, fills/fees, lots, reporting FX, native-cash benchmark, accrued/settled/uncertain
actual expenses, synthetic receipts and shared-cost allocations reconcile without trusting a runtime spend
counter. Tests cover repeated/reset portfolios, foreign records, event-time FX, stale and missing marks,
receipt provenance, order uncertainty, pagination and actual retained improvement evidence.
The browser performs no monetary arithmetic. Drawdown is explicitly sampled trading drawdown, adjusted for
subsequent external flows and embedded expenses; unusable historical observations remain provisional.

**A35:** every evidence API/page requires a persisted owner/role session. Protected writes enforce actual role
authority, cookie CSRF, typed bounded inputs, request replay identity and revision compare-and-set.
Policy/budget changes share a deployment revision; their effects and responses commit atomically.
Leader activation cannot supply its own trusted attestation. Leader work consumes bounded role/root resources.
No dashboard configuration grants live operation, paid calls, withdrawals, secret paths or arbitrary executable changes.

Resume latches manage-only before awaiting reconciliation and retains that safe state on interruption.
Unknown orders, unresolved billing, system halts and incomplete management/version recovery prevent fresh activity.
A newer owner control fences an older in-flight resume. Emergency manage-only is local and remains available
when a prior command is pending. Completed failures carry a durable terminal marker; unknown browser outcomes
retain the original command ID and exact values instead of issuing a new effect.

**A37:** recursive projection redaction strips credentials, private filesystem/account details, raw requests,
responses and conversations. Public health returns only minimal operational flags. Session files use exclusive
no-follow creation and mode 0600; the resolved paper database directory must be private. The staged hygiene
scan reads index bytes and catches credential/runtime leakage even if the working copy was subsequently scrubbed.
CI runs it before source/review artifacts are uploaded. Only synthetic source fixtures are committed.

## Verification

Python 3.12.14 with the unchanged lockfile: **534 passed; zero failures, errors or skips** in the final full suite,
independently verified from JUnit. Baseline: 391 tests. Ruff, ten planning tests, dependency/reference validation,
cost illustration, staged hygiene and wheel packaging passed. Detailed commands are in IMPLEMENTATION-STATUS.md.

`tests/integration/test_dashboard_financial.py` exercises authoritative financial projections and sampled drawdown;
`test_dashboard_evidence.py` covers scoped decisions, research, lessons, candidate artifacts and events;
`test_dashboard_controls.py` covers permissions, bounded resources, revision/replay races, interrupted resume,
emergency controls and terminal failures. Boundary/session/render tests cover snapshot isolation, redaction,
private startup, login, CSRF, pagination and server-rendered pages. Hygiene unit tests scan real staged fixtures.
`tests/e2e/test_dashboard_loop.py` reopens the actual offline Engineer/Leader/Trader loop, reconciles values,
navigates rollback evidence and retains four fills without extra effects.

Actual Chromium checks pass login, eight page routes, desktop/mobile layouts and dark theme with no document
overflow, JavaScript exceptions or CSP errors. Injected browser responses verify stale-revision reload,
503/403/success replay with one request identity, readable terminal failures, emergency manage-only while
another command is pending, and malformed HTTP 200/login acknowledgements without false success.
These fault responses are scripted; the page/server and offline paper data are real local implementations.

## Explicit limits and handoff

Model-routing writes remain unavailable until T17 assembles an approved persisted consumer; a successful owner
configuration cannot claim a routing effect that no worker consumes. The standalone server starts no scheduler
or provider. Hard-crashed dashboard commands stay visibly pending and block ordinary writes; later controller
recovery must resolve them without replaying external effects. Emergency manage-only remains available.

Legacy cross-currency receipts lack linked FX source IDs. Stored conversion values are retained and labelled
incomplete. Sampled drawdown cannot certify continuous history; historical economic drawdown is unavailable
without cost-allocation effective timestamps. Setup/recurring costs lacking a recorded classification remain
unclassified rather than being guessed from role names.

T16's full fault catalogue and production-equivalent loop remain open. T17 continuous operation, funded provider
verification, economic evaluation, live trading and protected-host isolation are separate work. All validation
here is local and credential-free; no new CI result or economic success is claimed.
