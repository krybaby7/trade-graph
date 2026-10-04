# Bounded live pilot readiness

T20 remains blocked. The dashboard, owner enable-live endpoint, paper runtime,
and the readiness functions keep live execution disabled. Repository development
and synthetic tests provide no real-order authority, capital allocation, or venue
eligibility proof.

`evaluate_live_enablement(record)` treats the historical `live_gate` row as an
unverified declaration. All caller booleans, a `supported` label, and a diagnostic
flag still return `enabled=false`, `ready=false`, and `status=unverified_record`.
Malformed, non-finite, float, negative, or excessively large monetary inputs fail
closed. Paper capital and reset values never create live allocation or expenses.

## Private advisory evidence contract

`evaluate_live_readiness(database, clock, scope=..., source=..., upstream=...)` is a protected,
read-only integration interface. It has no HTTP route, writer, signer, broker call,
deployment action, or live-policy transition. Its input is private operator data,
not a model tool or an Engineer-editable artifact.

The protected deployment pins `LivePilotScope`, the absolute evidence path,
the full bundle SHA256, and distinct owner/eligibility/venue/operations/economics
issuer keys. Keep those pins and keys outside the mutable process. A module in a
shared credentialed interpreter is not production isolation. The current T21
boundary work does not supply intended-host, backup, or protection certificates.

The bundle is a bounded JSON regular file in an owner-private directory; symlink
leaves/directories, hard-linked files, permissive modes, other ownership, excessive
size, changed bytes, missing keys, shared issuer keys, incorrect issuer domains,
or bad signatures are refused. The loader performs a descriptor-based bounded
read and compares the exact byte digest to the protected pin. Keys are immutable
copies and excluded from the source object's representation.

Issuer signatures use HMAC-SHA256 over
`trade-graph.live-readiness.v1\0` + document-kind + `\0` + the canonical typed
payload (`model_dump(mode="json")`, sorted keys, compact separators, ASCII,
non-finite JSON prohibited). The document kind is `owner_authorization` or the
specific evidence kind. Signatures prove issuer provenance and integrity. They
do **not** prove external checks happened, replace a trusted collector, or certify
profitability. The implementation provides no signing workflow; do not manufacture
production approvals from test helpers or manually enable policy through SQL.

Every document binds deployment, separate live portfolio, private venue account,
venue, single instrument, active owner-policy revision and exact stored policy
hash, owner-pinned deployment artifact hash, and current graph version hash.
Replacing any binding invalidates reuse. Approval and evidence windows must be
timezone-aware, positive and at most 31 days; future and expired records fail.
Account funding and read-only reconciliation additionally require observations
no older than 60 seconds; key permissions and venue metadata/fees have a one-day
maximum age. These are conservative local bounds, not promises about exchange
freshness.

The distinct signed owner authorization records the explicit
`authorize_bounded_live_pilot` action, `owner_real_funds` origin, native allocation
and maximum possible loss, EUR operating allowance and daily cap, the exact
deployment budget/role-limit digest, manage-only/flatten stop policy, and purpose.
Leverage and withdrawals must both be explicitly false. Amounts require positive,
finite, bounded fixed-point values; loss cannot exceed capital, and daily expenses
cannot exceed the allowance. Equal numeric paper and live amounts do not establish
allocation provenance.

Required independent evidence covers current legal/account eligibility, real
funding, read/trade-only keys without withdrawals, current venue rules/fees and
minimum size inside the allocation, authenticated account reconciliation, broker
uncertainty/cancel/fill/restart conformance, pause/independent recovery, actual host
and backup restore/alerts, and complete forward economics/receipts/venue differences.
Each document identifies immutable source hashes and the exact claims checked.
Economics binds the preregistered protocol, report, sealed expense inventory, and
complete registry snapshot digests. Duplicate or missing evidence and unknown,
synthetic, or unverified-import provenance fail closed.

## Current protected state

Within one SQLite read snapshot the projection checks:

- Active policy content integrity, explicit live permission, venue/symbol scope,
  withdrawals/leverage absent, and operating limits inside the owner policy.
- A separate open live portfolio, current one-instrument long-only mandate, and
  the active graph version matching the evidence scope.
- Exact owner budget identity and remaining real total, current-month, current-day,
  and ordinary allowance after preserving the priority reserve. Synthetic receipts
  never charge that allowance. Decimal comparisons use precision 100; state values
  are bounded before arithmetic.
- No unresolved actual usage, invoice differences, unknown/submitting/cancel-pending
  orders, owner commands still processing, recovered owner effects awaiting
  explicit review, or artifact recovery pending. A terminal failed receipt does
  not resolve an unknown effect; an ordinary known rejection is distinct.
- An explicit current, complete reconciliation-health proof for that exact live
  venue/account scope. A missing proof means pending verification; it does not
  assert that the existing runtime failed. Current execution logs only health
  changes and may omit a healthy initial state, so a genuine collector/proof
  recording path is still required.

Outputs omit private account identifiers, evidence contents, source paths, and keys.
They are advisory snapshots; they must never be reused as order authorization.
Protected execution must independently enforce capital, loss, expense, freshness,
and permission bounds at each effect when a future live lifecycle exists.

## Durable lifecycle preparation

`ProtectedPilotLifecycle` in `live_pilot.py` implements private persisted grant,
effect and management transitions. It exposes no dashboard/model route, signer,
provider request or broker transport. A protected service can retain a pinned
signed authorization as `PENDING`; activation calls the concrete current readiness
evaluator without an injectable trust callback and remains refused. Staging is
not approval or a policy change. Existing advisory readiness remains closed.

The controller prepares each prospective increase from the exact stored account,
instrument, live portfolio, owner policy, graph version, unsent limit-order intent
and held native execution reservation. Unbounded market buys are refused. The
future dispatcher must call `reserve_increase` and then `begin_submission` before
its external effect. Both recheck current pinned evidence/readiness in a writer
transaction. The one-use effect binds the request digest and grant generation;
revocation, restart, intent edits and stale source pins cannot reuse it.

The acquisition envelope conservatively assumes every acquired position can lose
its entire quote cost including reserved fees. Cumulative committed and unresolved
acquisition costs must fit both the allocation and maximum-loss cap. Profitable
sales never replenish this envelope. This deliberately bounds a small pilot
without claiming that a price stop guarantees a loss cap. All actual deployment
expense reservations, including uncertain usage and work by other roles, consume
the EUR total/day envelope; synthetic reservations are excluded. Financial
amounts are bounded before arithmetic and compared with precision 100.

Stop/revocation latch the declared `MANAGE_ONLY` or `FLATTEN` position/order policy
while preserving every existing non-running owner pause and its current management
semantics. They preserve unresolved holds.
Startup recovery closes the grant and marks prepared/submitting effects unknown;
the old authorization cannot resume. Effect reconciliation reads durable native
order/fill state, requires fresh complete account history to release a known
unfilled terminal order, and retains committed costs permanently, including through
later uncertainty and cancellation. Own-effect release requires an explicit
`owned_intent_fill_history` observation strictly after the effect and latest native
order/attempt/fill/ledger changes; a same-clock or bare complete label is refused.
Stop completion requires retained authenticated full venue-account and protected
ledger proof, no unresolved order/effect, and no native position. A local flat book
or owned-history observation does not supply full-account proof. The current venue
collector leaves protected account-ledger reconciliation pending, so account-level
completion remains refused. A revoked grant does not permit a replacement while old account
effects, orders or inventory remain unresolved. Audit events are append-only.

The default runtime and existing live adapter do not adopt this lifecycle. Genuine
protected dispatch integration, production host isolation/recovery, authenticated
upstream evidence, measured pilot fills/fees/latency/rejections/paper differences,
and a signed continuation-or-stop review remain pending. The implementation does
not provide an activation bypass or treat a stored historical `ACTIVE` row as
current execution authority. Synthetic lifecycle tests seed such historical rows
only to test restart/revocation/hold preservation; no production grant is issued.

## Evidence that remains missing

The optional `LiveUpstreamSources` references are protected runtime configuration,
not a web/request schema. They point to the actual T18 `RuntimeEvidenceCollector`
and retained capture, T17 `HostObservationCollector` and pinned private observation,
and T19 `PinnedVenueObservation`. Arbitrary verifier callbacks or declarations
cannot supply a replacement implementation. Missing collectors remain pending;
altered, unretained, stale or mismatched sources are refused.

The forward source is re-read from its exact original runtime database and
append-only evaluation registry. The gate binds the registered paper portfolio
separately from the target live portfolio, matches the selected graph version,
requires the owner-pinned canonical capture digest, and compares the issuer's
protocol/report/inventory/snapshot digests and actual report verdict. A supported
label cannot override an insufficient retained report. The sealed ledger export
must equal the complete runtime source digest, contain every collected receipt
exactly, and leave no unresolved attempt. The collector independently audits
native ledger replay, receipts/reservations, price and exact frozen FX links,
invocation/wire provenance, allocated costs and effective imported expense amounts.
New receipts, source revisions or registry records invalidate an earlier capture;
the capture must also be no older than 24 hours.
The existing protocol binds the paper portfolio, market stream and version, but
does not authenticate a mapping to the live account, instrument and current owner
policy. That exact economic scope proof remains explicitly pending.

The host source is authenticated and rechecked against the current local package,
collector, host and database/configuration identities. Its exact stored policy
hash, selected paper graph version, independently pinned paper portfolio and
configured host/package fingerprints must match the deployment. A matching raw
fingerprint does not authenticate independent owner host designation, which stays
pending. The signed issuer evidence must retain the report, collector and package digests and postdate
collection. The local observation must be no older than 24 hours. Configured
funding and credential presence are preflight facts; they never prove a funded
soak, actual bill, off-host backup, immutable deployment or delivered alert.
A loaded, active service still lacks exact unit configuration binding, and a
successful local backup/restore drill still lacks service restart reconciliation.
Those proofs remain separately pending.

The venue source is rechecked from the retained private bounded wire captures and
native normalized summaries. Scope must match every target deployment/account/
venue/instrument/policy/artifact/version field and remain live. Issuer documents
must bind the observation and collector/adapter/wire-contract digests and postdate
capture; the independent verifier applies a 60-second maximum age. Injected or
scripted transport observations cannot become authenticated account proof.
Successful reads alone do not verify legal eligibility, key permissions, absence
of withdrawals, write/cancel uncertainty, native stops or protected ledger recovery.

`upstream_verification` reports structured pending/refused codes and mechanical
source checks. It omits raw account, path, receipt and diagnostic values. Known
source gaps such as complete invoice exports, external transport authentication,
baseline collection and dependence/regime review are reported separately; counts
preserve unknown or additional diagnostic burdens without exposing private data.
These checks never write policy, imports, grants, receipts or orders and never
perform a paid/private/network request.

Even a correctly signed bundle whose recorded checks match current state returns
`ready=false`, `enabled=false`, `diagnostic=false`, and `status=blocked`.
`recorded_checks_passed` reports only the consistency of retained issuer documents
and local state. `declared_economic_verdict` labels the issuer's recorded declaration;
`economic_evidence` remains `insufficient_evidence` while actual source/cost
authentication is missing, even when that declaration says `supported`.
`authoritative_upstream_economic_verification` remains false:
the current forward registry preserves `unverified_imports`. The new collector
integration verifies retained local source consistency; its external provenance
status remains false. Issuer signatures, local wire captures and digest labels
cannot replace actual complete independent authenticated collection.
New receipts, invoice resolutions, trials or source changes must invalidate economic
handoffs through an actual protected verifier. No caller trust flag bypass exists.

A diagnostic grant is a distinct recorded purpose for measuring execution
differences. It can retain `insufficient_evidence` or `not_supported` economics;
it bypasses no eligibility, funding, account, operational, or provenance check.
`diagnostic_authorization_recorded` describes a consistent signed record, never an
active pilot. The dashboard must preserve the actual economic verdict.

Completion requires T17's funded paper and operations evidence; T18's actual future
evaluation and independently verified complete upstream provenance; T19's selected
venue eligibility, permissions and authenticated conformance; a separately granted
real allocation/loss/expense envelope; actual intended-host protection, backups,
alerts and independent recovery; and a protected live grant/stop/revocation lifecycle.
The new controller supplies persisted preparation/management logic; adoption by
protected live dispatch and intended-host verification remain unaccepted.
After that lifecycle is integrated and separately authorized, a pilot must record
real fills, fees, latency, rejections, paper differences, measured loss/spend, and a
continuation-or-stop review. Venue minimums cannot silently enlarge the allocation.

## Local verification

`tests/integration/test_live_readiness.py` uses explicitly synthetic issuer keys,
accounts, scopes and temporary databases. It verifies immutable pin/signature and
issuer-domain refusal, scoped replay/freshness, malformed money and exponent
expansion, missing/unverified evidence, paper-capital separation, policy/version/
budget changes, exact expense room, unknown orders/account history, and absent
actual economic verification. No private venue, paid provider or real-order calls
are made. These results are implementation evidence only.
