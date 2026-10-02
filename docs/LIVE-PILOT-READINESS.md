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

`evaluate_live_readiness(database, clock, scope=..., source=...)` is a protected,
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
  orders, owner commands still processing, or artifact recovery pending.
- An explicit current, complete reconciliation-health proof for that exact live
  venue/account scope. A missing proof means pending verification; it does not
  assert that the existing runtime failed. Current execution logs only health
  changes and may omit a healthy initial state, so a genuine collector/proof
  recording path is still required.

Outputs omit private account identifiers, evidence contents, source paths, and keys.
They are advisory snapshots; they must never be reused as order authorization.
Protected execution must independently enforce capital, loss, expense, freshness,
and permission bounds at each effect when a future live lifecycle exists.

## Evidence that remains missing

Even a correctly signed bundle whose recorded checks match current state returns
`ready=false`, `enabled=false`, `diagnostic=false`, and `status=blocked`.
`recorded_checks_passed` reports only the consistency of retained issuer documents
and local state. `authoritative_upstream_economic_verification` remains false:
the current forward registry preserves `unverified_imports`, and an issuer's digest
labels cannot replace authenticated upstream collection or current-source rechecks.
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
After that lifecycle is implemented and separately authorized, a pilot must record
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
