# Protected read-only Kraken observation

This is an opt-in Python interface for a separately authorized account observation.
It is not a live adapter activation path. No private Kraken request, owner credential,
real order or funded observation was used to implement or verify this checkpoint.
Injected HTTPS clients produce synthetic transport evidence even when fixtures use
realistic signing, balances or account labels.

`KrakenReadOnlyConformance` requires a protected `PinnedReadOnlyAuthority`: an exact
owner-private grant file pin, a separate owner verification key, an explicit
`observe_read_only_venue_account` action, the credential's SHA256 binding, exact
deployment/portfolio/venue/account/live/symbol/policy/artifact/version scope, and a
bounded observation window. A grant can last at most ten minutes; each single-use
collector permits at most 256 requests, 20 history pages, 300 seconds, one MiB per
wire response and 16 MiB of retained captures. The defaults are 128 requests and
60 seconds. Lookups must use the selected symbol and bounded native identities.
Before every request, the collector rereads and verifies the pinned grant, its
signature and expiry. There is no caller `verified` or `authorized` Boolean.

The owner and collector verification keys must differ. Keep those keys, the
credential and the authority file outside the mutable graph/Engineer process.
Account naming and key ownership are protected configuration bindings; successful
reads alone do not independently establish legal eligibility, native account owner
identity or the full key permission inventory.

## Collection and verification contract

Given a separately approved and provisioned authority, trusted parent code can use:

```python
collector = KrakenReadOnlyConformance(
    authority, collector_key=protected_collector_key,
    api_key=protected_api_key, api_secret=protected_api_secret,
)
capture = await collector.collect(existing_owner_private_directory)
```

Default collection creates its own bounded Kraken HTTPS transport with order writes
disabled, preserving the configured network/proxy environment. A supplied
`httpx.AsyncClient` is always classified `injected_transport`; no caller flag can
change that classification. Transport read captures exclude credentials and signing
headers. The API exposes no withdrawal, order submission, cancellation or raw endpoint.

Each successful read retains its exact response bytes, method, canonical native
parameters, exact transmitted request hash, response hash and observation times.
Capture records receive collector MACs; the private final report pins each retained
record, the normalized native summary and the copied read-only authority source.
Files are written exclusively, sealed to mode 0400 and retained in an owner-private
0500 observation directory. Symlinks, hardlinks, public permissions, oversized data,
changed pins and inconsistent timestamps fail verification. Private actual account
artifacts must never be published or copied into model context.

Collection normalizes instruments, explicit account fees, total/held/available spot
balances, open orders, requested order lookups and globally ordered frozen native
history. A malformed or unsupported stage retains prior facts and reports a pending
stage. It does not invent a complete account reconciliation. The collector neither
writes the financial journal nor resolves ownership from amount/price coincidences.
The selected instrument response must contain exactly the normalized requested
pairs. An unrelated or incomplete response, conflicting native aliases or one
unsupported fee tier cannot establish instrument readiness. Metadata refresh
publishes the asset/pair/rule/fee registry together after validation.

The duration starts at the beginning of collection and includes setup. No response
received after the grant's duration or expiry is admitted as a successful capture.
An asynchronous deadline preserves the completed stage prefix and its retained
sources; the interrupted stage stays pending.

The stable proof interface is:

```python
source = PinnedVenueObservation(
    capture.path, capture.observation_sha256, protected_collector_key
)
proof = source.verify(now=current_utc_time, maximum_age_seconds=60)
```

`verify` runs synchronously outside an active event loop; async protected callers
can use `await asyncio.to_thread(source.verify, now=current_utc_time)`. Verification
does no network work. It checks exact private retained bytes and MACs, the grant's
scope/window/key binding, request order/native IDs and receipt times, then replays
normalization against the current adapter. It independently recomputes collector,
adapter and wire-contract source digests and compares the native summary. Changed
code adds a pending source reason; changed or inconsistent retained data is refused.
Receipt times must follow the serialized request order and remain inside the
grant's duration. Replay includes successful captures from an incomplete native
stage, independently recovering normalization failures even if a signed report
omits their label. Every retained capture must be consumed by that replay; extra
or unrelated responses are refused. Missing successful transport evidence remains
pending. Source digests are pinned before collection and checked again at its end.

`VerifiedReadOnlyAccountObservation` contains `observation`, `source_sha256`,
`authenticated_reads`, `source_current` and `pending`. `authenticated_reads` is
derived from protected successful private captures through the collector-owned
HTTPS client and is empty for injected clients. Signed report flags cannot complete
a native stage, promote injected wire records or remove the verifier's independently
derived pending checks. These MACs establish protected collector provenance, not a
venue signature or a complete upstream authorization certificate.

The observation's typed scope matches the live-pilot scope plus `mode="live"`.
Consumers must compare the exact protected scope, current code/artifact identities,
retained report hash and observation freshness. They must not reuse it as an order
authorization or substitute a signed issuer declaration for these source checks.
The collector is single-use per instance; its request bounds are per collection.
Protected scheduling and any reuse of an owner grant remain owner-controlled.

## Gates still pending

Successful read captures do not prove withdrawal permission absence, legal/account
eligibility, native stop execution, order/cancel uncertainty behavior, financial
account reconciliation, host/dependency identity or paper/live economic differences.
The verifier always reports these missing facts. A narrowed history start also
remains explicit; it cannot silently prove the full earlier account history.

Rounded native trade costs, multiple native fee assets, rebates, late earlier fill
history and protected-ledger precision overflow continue to fail closed under the
existing financial contract. Completing replay requires an append-only audit and
financial projection contract agreed with the protected execution owner; this
collector does not rewrite ledger events or cached portfolio state. Native stops
remain untested and disabled. T19 needs T17 delivery and actual separately authorized
venue eligibility, least-privilege permissions and authenticated conformance before
completion. T20 real orders and protection tests need their separate owner grant.

## Official source and local verification

Normal public Git HTTPS reads on 2026-10-02 independently resolved official HEADs:
Kraken SDK `a5d253c02036dd74f6fc5976cba188e39e8c1b4b`, and Kraken Go
`a8484bc5ec985fd5ce5bcc0580f659727d8f7603`. A shallow filtered SDK clone was read as
source data. The official Rust REST
[AddOrder form builder](https://github.com/krakenfx/kraken-api-sdk/blob/a5d253c02036dd74f6fc5976cba188e39e8c1b4b/rust/src/api/trade/types/requests.rs#L228)
explicitly emits the lowercase enum value, and the
[TradeVolume method](https://github.com/krakenfx/kraken-api-sdk/blob/a5d253c02036dd74f6fc5976cba188e39e8c1b4b/rust/src/api/account/mod.rs#L405)
explicitly requests `fee-info`. The adapter now aligns those REST SDK request forms:
lowercase `gtc`/`ioc` and `fee-info=true`. The earlier uppercase mapping had only
synthetic fixture evidence. This resolves the SDK mapping discrepancy; actual venue
acceptance remains unverified. No write is performed to test it.

Direct REST/API-key-info documentation and GitHub API discovery remained proxy-blocked;
an attempted public OpenAPI location returned 404. No proxy bypass was attempted.
Source review is not authenticated conformance, and no permission-introspection
endpoint was added without readable current official evidence.

The focused adapter/identity/collector/execution suite passed **269 tests**, with
zero failures/errors/skips. Worktree imports were asserted under explicit
`PYTHONPATH` while sharing the frozen environment. Tests cover retained native replay,
synthetic classification, false report labels/check flags, scope/key/authority
mismatch, request bounds, partial native refusal, changed sources, expiry, private
file requirements and cold SQLite FILLED/CANCELLED recovery. Ruff and diff checks
passed. The generated private-free JUnit evidence is `/tmp/t19-r2-focused.xml`.

The [2026-10-04 integrity continuation](reviews/2026-10-04-kraken-conformance-integrity.md)
passes **289 focused tests**, including 20 new scope, native-fee, retained-replay
and deadline regressions. Its account/transport data remain synthetic. This
verification does not complete the pending actual-account gates above.
