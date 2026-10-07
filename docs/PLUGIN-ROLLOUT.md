# Offline autonomous feature activation and recovery

`OfflinePluginRolloutController` completes an opt-in functional lifecycle for
[commissioned feature candidates](PLUGIN-COMMISSIONS.md). Its protected
`advance()` selects a READY candidate under the pinned offline policy, verifies
the actual commission and complete retained synthetic model cost evidence,
activates its exact sealed executable bytes, runs independent functional health
and either retains the new release or restores the previous one. No model or
candidate-supplied success flag controls recovery.

This is offline preparation. The controller requires a fresh paper portfolio
with the explicit experiment ID `offline-plugin-rollout`; default paper and R1
active pointers are refused. It rejects real provider invocations, nonsynthetic
usage receipts, order intents and real venue fills. It does not register itself
with the runtime, Leader activation tool, financial gateway or default Engineer.
All reports retain `production_authorization=false`, `live_authorization=false`
and `economic_evidence=not_evaluated`. An offline experiment label, receipt key
or source fingerprint does not grant deployed code authority.

## Actual bytes and one authoritative pointer

An independently selected `PluginRolloutPolicy` pins the portfolio, commission
policy, retained health corpus, maximum functional error, minimum samples,
resource/deadline bounds and finite commissioned-history quota. The controller
has a separate independently approved source fingerprint and parent-only
authentication key. The health corpus must bind the same exact baseline source,
runtime and protected numeric manifest as the commission policy. Test fixtures
select their synthetic pins explicitly; production pins must come from an
independent owner release.

Existing trusted SQLite version tables hold the authoritative state. No new
financial schema, artifact bundle or private pointer file is introduced.
`active_versions.artifact_hash` identifies the exact single-file source map,
while its strict `offline_pure_feature_plugin` fingerprint separately binds the
runtime bundle digest, source digest, rollout/controller policy and sealed
authenticated release receipt. Source-map and executable-bundle hashes are
different identities. Parent-authenticated transition receipts bind the old and
new fingerprints and monotonic generation; `version_events` retains their exact
references in the same transaction as each pointer change. Interrupted receipt
publication can leave retained orphan evidence without changing SQLite state.

Fresh baseline initialization refuses ordinary/R1 fingerprints. Activation
requires the exact currently commissioned baseline, READY source, independent
passing replay/build/shadow receipts, current authority and a quiescent task
boundary. The protected controller verifies the generated source against its
original billed model result and compares-and-sets the current generation.
`advance()` reads only a finite policy-bounded commissioned candidate history,
refuses overflow and never treats another source class as a numeric plugin.

`evaluate(observations)` launches the actual copied sealed worker and returns
numeric features tagged with the selected runtime and generation. It does not
translate features into decisions or orders. The trusted parent requires the
complete exact feature-key set and bounded canonical Decimal strings. An
obsolete generation cannot publish its result. Mutable failure requests
independent rollback; it does not produce a fictional successful decision.

## Independent finite health and durable recovery

Health uses independently retained point-in-time numeric cases, not model
metrics or candidate-selected tests. Before each child executes, the parent
authenticates and persists a STARTED intent identifying its input digest, case,
runtime, policy and generation. Completion is bound to that exact intent and
stores the actual child output/status, including protected fallback diagnostics.
Completed cases are not repeated after restart. A persisted interrupted intent
is an unresolved local outcome and triggers rollback without replaying that
candidate or claiming a successful sample.

Protected Decimal comparisons run under an independent precision of 100 digits;
ambient application precision cannot loosen the declared tolerance. Magnitude
checks use context-independent absolute values. The parent requires exact
feature keys and recomputes each retained health result from its actual outputs
and expected values. Only current, authenticated corpus/runtime/policy/generation
samples count toward ACTIVE. A changed state flag, empty feature output,
another case's receipt, excessive sample rows or oversized pointer cannot
replace those samples. SQL reads refuse more cases than the independent corpus
and preflight pointer bytes before loading them.

The policy bounds each child and the complete health envelope. The parent does
not start a child unless its full wall-time allowance fits the remaining
monotonic quota. Resource/contract failure, excessive functional error,
interrupted health or an observation deadline requests deterministic rollback.
Persisted ACTIVE state is rechecked against the original independent evidence
on maintenance and restart.

Recovery authenticates the durable current transition and exact predecessor
identity before touching the pointer. It can recover from damaged current
candidate bytes or changed candidate cost proof while independently verifying
the previous sealed runtime, source, replay and durable version history. The
previous fingerprint must equal the predecessor in the authenticated activation
event. A compare-and-set prevents obsolete recovery from restoring over a newer
generation. An unavailable or corrupted predecessor refuses recovery; it does
not manufacture healthy bytes. Rollback changes source selection and lifecycle
records only, retaining all ledger events, fills, orders, reservations, usage
receipts and model invocations.

## Verification and limits

```bash
PYTHONPATH=/workspace/trade-graph-t22-takeover/src:/workspace/trade-graph-t22-takeover \
  /workspace/trade-graph/.venv/bin/pytest tests/integration/test_plugin_rollout.py
```

Tests use actual commissioned gateway work and actual confined workers. They
demonstrate changed numeric behavior after autonomous activation, authenticated
health and restart, denied-source failure on a new independent input,
deadline/damaged-candidate/interrupted-work rollback, exact Decimal regression,
CAS/quiescence/offline fences, R1/real-provider/receipt refusal and tampered
history/pointer/key denial while preserving finance and costs. All credentials,
owner grants, cases, expenses and usage are synthetic.

Finite functional health does not establish economics, general determinism,
untouched final data, immutable deployment distribution or intended-host proof.
Repeated replay/shadow/health feedback remains development evidence. T22 still
requires genuine T18/T21 evidence, explicit owner class admission, funded broader
operation and production deployment/health/rollback integration. These APIs do
not activate code in the default application or provide a financial feature-to-
decision bridge.
