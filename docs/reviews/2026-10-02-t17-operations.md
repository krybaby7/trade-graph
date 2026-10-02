# T17 public acquisition and operations continuation

T17 remains in progress. This continuation hardens public acquisition and records
actual workspace operations results; funded observation and intended-host verification
remain pending. USD10,000 is virtual, with EUR reporting. Paid calls, private exchange
calls and live trading remained disabled throughout.

## Concrete changes

Public HTTP now streams an uncompressed response, refuses redirects and unsupported
content encodings, and enforces declared and actual response byte limits. Its default
cap is 1 MiB; the paper feed retains its five-second network-phase timeout. Elapsed
checks between raw chunks refuse trickled responses. A blocked read can extend beyond
that elapsed deadline until the network-phase timeout; this is not a claim of a precise
wall-clock cancellation deadline. The configured HTTP(S) proxy policy is preserved.

Frankfurter rate parsing previously filled a missing source date with the requested
historical date and defaulted missing currency fields to the request. It also accepted
a rate for another pair. Those cases could invent provenance or apply a rate in the
wrong direction. Responses now require the actual matching base/quote and a canonical
ISO date; a historical response dated after the request is rejected. Genuine carried
prior dates retain their original provenance. The paper feed separately refuses rates
dated after retrieval and labels carried prior-day valuation provisional.

The existing Frankfurter provider-scoped URL is valid in the official source and was
preserved. Reviewed source commit:
`915fabfef4f4074437e32e25aac71f437ecfa76e`, obtained from the official repository on
2026-10-02. References:

- [OpenAPI endpoint and required Rate fields](https://github.com/lineofflight/frankfurter/blob/915fabfef4f4074437e32e25aac71f437ecfa76e/lib/public/v2/openapi.json)
- [Provider-scoped routing](https://github.com/lineofflight/frankfurter/blob/915fabfef4f4074437e32e25aac71f437ecfa76e/lib/versions/v2.rb)

This source review is not a successful current public API response.

## Actual results

Focused tests: **113 passed**, covering public normalization/reconnect/freshness,
the new identity/date validation and transport bounds, continuous paper service,
funded-soak gates/reporting and operations. Ruff and `git diff --check` passed.
JUnit evidence remains private at `/tmp/trade-graph-t17-continuation-tests.xml`.

Bounded real public GETs used the configured proxy and no credentials. Kraken Time,
Kraken AssetPairs for XBT/USD and ETH/USD, and the configured Frankfurter ECB USD/EUR
endpoint all failed with `ProxyError`, with CONNECT 403 observed. No current external
response or resulting market/FX observation is claimed. The hardened transport reproduced
the same failures, taking less than one second per request in this workspace.
Sanitized mode-0600 evidence is at
`/tmp/trade-graph-t17-bounded-probe-q34rp0ea/public-probe.json`.
Direct Frankfurter documentation was also blocked; official Git source was readable.
No proxy control was bypassed.

A bounded local CLI drill performed initialization, three maintenance ticks, a private
SQLite backup while the service lease was active, offline restore, doctor/report,
manage-only pause and one restored reconciliation tick. All eight commands returned
zero. The backup SHA256 matched its checksum; database, backup, checksum and restored
database files had mode 0600. No service lease remained after drain. Restored fills,
decisions, usage receipts and budget reservations were all zero, as expected for this
maintenance-only exercise. Existing tests separately verify populated WAL balances,
costs, uncertainty and restore refusal behavior. Private drill evidence is at
`/tmp/trade-graph-t17-drill-fisyw8v3/operations-drill.json`.

This workspace is not an owner-designated deployment host. Checking the uninstalled
systemd template directly reported that its `/opt/trade-graph` executable does not
exist here; no service user, installed production unit or production deployment is
claimed. Prior installation/package verification remains recorded separately.

## Remaining setup and evidence

1. Select an existing intended host and obtain approved HTTPS connectivity to
   `api.kraken.com` and `api.frankfurter.dev`. Repeat public collection there and
   retain actual metadata, quote receipt times and dated FX evidence.
2. Supply one selected provider's dedicated runtime credential, approved current
   immutable price cards and all six protected role routes. Source review and the
   planning-price snapshot are not credential or current-price verification.
3. Set a separate real EUR expense allowance and deliberate persisted owner paid
   permission; enable the private runtime paid flag only for the intended funded
   observation. Paper capital does not supply funding or permission. No such funded
   setup was supplied or inferred in this continuation.
4. Verify installation ownership, private persistent storage, dashboard access,
   service start/stop/restart and restore under the intended host's actual service
   identity. Retain private encrypted off-host backups and test their recovery.
   Review shutdown/forced-stop behavior for the chosen paid configuration.
5. Run a bounded funded paper observation, inspect actual/unknown receipts and holds,
   reconcile the provider bill/export against forecasts, and preserve degraded or
   failed evidence. Actual model responses, invoice comparison and the intended-host
   operational drill are required before closing T17.

No forward-economic support, authenticated venue conformance, real order or broader
Engineer authority follows from these local checks.
