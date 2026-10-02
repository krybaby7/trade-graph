# Forward-paper trial evidence

T18 has an executable protocol, private evidence registry and report calculator.
Actual economic validation remains pending T17's funded, credentialed paper
collection and a completed, predeclared forward horizon. Tests use synthetic
fixtures. They prove collection rules and arithmetic, and do not certify provider
bills, venue conditions, strategy profitability or live eligibility.

`trade_graph.evaluation_contracts.ForwardProtocol` declares development and
validation windows, purge duration, contiguous equal-duration held-out forward
blocks, initial EUR-valued capital, exact allowed version fingerprints, data,
friction and regime-classifier fingerprints, cash/buy-and-hold/deterministic
baseline artifacts, block-independence policy, decision/regime requirements, turnover/drawdown tolerances,
economic hurdles, stop/review rules and sensitivity definitions. Registration
must occur after validation ends and strictly before the first forward block.
The trusted collector timestamps registration; a candidate cannot supply an
earlier registration timestamp. Protocols, observations and trial attempts are
immutable. A changed hypothesis or a new test window requires another trial.

Use `trade_graph.evaluation_registry.TrialRegistry(path, clock=None)` with one
operator-owned database per deployment. Default clocks use current UTC. Injected
clocks are for deterministic tests. Keep the database in ignored private runtime
storage, outside Engineer-readable snapshots and the final holdout inputs.
This SQLite evidence registry does not hold credentials or change the
authoritative ledger, owner budgets, financial policy or execution authority.
It requires no application schema migration.

The collector uses these methods in order:

1. `register(protocol)` fixes the complete protocol and its digest before any
   forward data exists. The first family registration fixes its maximum trial
   count and uncertainty level. Every trial stays visible in reports; failed
   trial/variant selection cannot be removed from the registry.
2. `start_attempt(trial_id, Attempt(...))` records work before its outcome.
   `record_expense(ExpenseEvidence(...))` imports retained actual receipts,
   including Leadership, Engineer, repair, retry, fallback, failed-candidate,
   setup and attributable external costs. Historical setup/failed-work receipts
   retain their variant fingerprint and original source reference. Historical
   evidence is not turned into predeclared forward evidence.
3. `finish_attempt(trial_id, AttemptResult(...))` retains completed, failed and
   rejected outcomes with receipts. A zero-receipt local attempt still requires
   an auditable outcome reference; the upstream gateway remains responsible for
   collecting every paid attempt.
4. `allocate_expense(trial_id, ExpenseAllocation(...))` applies fixed documented
   weights to an arm. The sum of weights for one source receipt across all
   trials/arms cannot exceed one. Transactional Decimal checks enforce this under
   concurrent collectors. One deployment registry and globally unique receipt
   IDs are required; using unrelated databases is not a global allocation guard.
5. `observe(trial_id, ForwardObservation(...))` records each fixed block in
   chronological order after its outcome becomes available. All four arms must
   use the registered data/friction assumptions, initial capital, the same
   external-flow path and continuous opening/closing equity. Decisions name
   point-in-time input availability and outcome horizons confined to that block.
   Observations retain a credentialed-soak report reference, observed venue
   conditions and explicit paper-versus-venue differences. Collection adapters
   also retain a dependence assessment under the preregistered independence
   policy; unassessed or dependent blocks cannot support the hypothesis.
   Collection adapters
   must derive these from trusted persisted evidence; references and an
   `evidence_kind` label alone do not authenticate an upstream run.
6. `seal_cost_inventory(trial_id, CostInventory(...))` attaches a complete
   authoritative deployment-ledger export digest/cutoff and all attributed
   receipt IDs after the forward horizon. Omitted known attempt receipts are
   rejected. The trusted importer must verify the export includes every
   attributable expense, historical setup and failed variants; this registry
   cannot discover omitted upstream records. Sealing freezes observations,
   attempts and allocations. Unknown usage stays unresolved, and
   `resolve_expense(ExpenseResolution(...))` may later append an invoice
   reconciliation without replacing the original receipt or reopening the
   sample. Retain report snapshots when reconciled costs change a verdict.
   The inventory cutoff must cover every allocated receipt's incurred timestamp,
   including attributed post-horizon review work. Every started variant attempt
   requires a retained outcome before sealing; a failed seal leaves the trial
   open so the missing evidence can be recorded.
7. `report(trial_id)` returns JSON-compatible Decimal strings, trial/variant
   history, receipt allocations, cost/provenance limitations, baseline
   comparisons, uncertainty and sensitivity. Reporting is a read of imported
   evidence and cannot enable live trading or broader Engineer authority.

## Immutable report handoff

`snapshot(trial_id)` appends an immutable `EvaluationSnapshot` in the same
transaction that reads its sources and builds the report. It includes canonical
`report_json`, its digest, the preregistration digest, the sealed inventory and
ledger-export digests/cutoff when available, the paper portfolio, market stream
and selected version. The report and snapshot always carry
`verification_basis="unverified_imports"`. This includes a conditional
`supported` result: importing actual labels and source references does not
authenticate a paid soak, complete invoices or independent observations.

The snapshot's `source_manifest_sha256` binds the complete deployment registry,
including every protocol, observation, variant attempt/outcome, expense,
reconciliation, allocation and inventory. Each frozen source binding includes
both its canonical document digest and the digest of the exact retained document
bytes, along with record identity, trial identity and collection timestamp.
Report scope, as-of time, protocol and inventory must agree with these bindings.
Snapshot and derived runtime-capture records are excluded from source manifests
to avoid recursive hashes. Runtime collection bindings remain in the manifest.
Repeated emission under the same fixed test clock is idempotent.

`verify_snapshot(snapshot)` revalidates nested structures, requires the identical
snapshot to be retained in that operator-owned registry, re-reads the complete
current source inventory and rebuilds the report at its original as-of time in
one transaction. A new invoice reconciliation, receipt, family trial or shared
allocation makes an earlier snapshot stale. Emit a new snapshot and retain the
old report so a changed economic verdict remains auditable. Even a semantically
equivalent rewrite of retained source bytes invalidates the old handoff.

This verification establishes a current, consistent registry handoff. It does
not establish upstream source authenticity or discover omitted deployment
expenses. A protected downstream verifier must independently check actual
collection provenance, complete authoritative receipts and their FX valuation,
then bind the snapshot digests to the intended deployment, venue, account and
instruments. Those live-account identities are absent from the paper protocol
and cannot be inferred from its portfolio name. Evidence expiry belongs to that
protected consumer's policy; snapshot collection time does not choose a new
owner-approved validity period. No snapshot method enables paid calls, live
execution or broader Engineer permissions.

For an already registered private trial, the handoff is:

```python
snapshot = registry.snapshot(existing_trial_id)
registry.verify_snapshot(snapshot)
# Retain snapshot.model_dump_json() privately. Independent protected source
# verification is still required before any downstream authorization gate.
```

No real protocol dates, operating allowance or observation data are supplied by
this example. The real collector still needs the T17 funded-paper evidence and
an operator-selected future protocol registered before collecting its holdout.

## Protected runtime source collection

`trade_graph.runtime_evidence.RuntimeEvidenceCollector(database, registry, clock,
deployment_id=...)` reads the actual operator-owned runtime database. Bind it to
an existing registered trial with `bind(trial_id)` strictly before the first
forward block and before any forward observation import. The retained binding
fixes deployment, open paper portfolio, market stream, protocol digest, database
file identity and the complete initial source inventory. Reopening the same file
is supported; an identical copy cannot substitute for the bound database.
Future-dated actual observations, decisions, snapshots, native events, fills,
receipts or collected source facts in that initial inventory are rejected.
Future scheduling, expiry and configured price effective dates do not represent
collected outcome observations and remain permissible configuration.

`capture(trial_id)` retains canonical current source bytes and their SHA-256,
the exact evaluation snapshot and its SHA-256, and collection time. It reads
every row of the required source tables in one SQLite snapshot: native ledger
events/postings, fills, market observations, decision snapshots and versions,
deployment budgets, reservations, usage receipts, price cards, exact FX source
links, shared allocations, invoice reconciliations, engineering attempts and
provider transport attempts. Owner policy, mandates, pause states, outbox,
broker orders, artifact admission and protected runtime/RPC state also belong to
the footprint. This is a complete inventory of those required tables, rather
than a claim that unknown external invoices or hosting expenses were discovered.
Unrelated presentation/session tables are outside the financial source footprint.

Collection refuses oversized cells in SQLite before fetching their contents.
Rows stream under per-table, total-row and exact canonical byte limits (10,000
rows per table, 20,000 total, 128 KiB per cell and 8 MiB per source inventory).
Exceeding a bound rejects the whole capture; no pagination, truncation or first
page can impersonate a complete export. Captures contain private requests and
journals and belong in ignored protected storage.

`verify(capture)` revalidates frozen nested contracts, retained bytes and exact
registry/report digests, then re-reads the current runtime inventory and scope
under consistent transactions. Any required source or registry change makes an
old capture stale. Verification compares native posting groups to ledger replay,
fill records to ledger events, decision inputs to their actual retained market
snapshots and preregistered versions, usage costs to native price-card arithmetic,
and non-EUR receipts to their exact pre-dispatch frozen FX rows. Changed FX facts
invalidate evidence while the original billed costs remain retained. It audits
shared allocations, unresolved reservations, and provider transport identities,
request/endpoint digests, scope, timestamps, response bounds and completeness.
An unfinished or uncertain wire observation remains unresolved even if another
row describes the reservation as terminal.

Continuity also compares immutable fact identities and bytes against all initial
bindings and every retained capture across the deployment registry. A new trial
cannot erase the deployment's earlier paid facts. Previously collected receipts, native facts,
price/FX records, invoice records and stable paid-request identities cannot
disappear or change when a new snapshot is emitted. Reservation settlement and
invocation/transport completion can change their documented mutable state;
retained results and complete HTTP responses remain immutable. Native postings
must belong to an existing transaction and portfolio, and each transaction's
metadata and posting group must match its exact replayed ledger event.
Historical revalidation streams captures after SQLite preflights refuse more
than 256 bindings/captures, 128 MiB total retained bytes, or 48 MiB for one record.
Exceeding these protected limits refuses capture/verification without truncation.

Invoice checks derive EUR arithmetic and collection availability, then compare
historical recorded totals to the receipts available at the invoice cutoff.
Later receipts cannot inflate an earlier invoice's recorded total. Equal-time
receipt ordering lacks a retained sequence link and is reported as pending
cutoff ambiguity; a contradictory amount, currency or future timestamp is invalid.
These checks establish local consistency rather than provider authentication.

`import_expenses(capture)` derives registry records from current retained native
receipts, reservations and durable results; callers supply no monetary amount or
source reference. Its source digest covers the full receipt/reservation/invocation
link and its conversion reference binds the frozen FX bytes. Missing or partial
non-EUR FX links remain unknown, and usage without a durable result cannot prove
work succeeded. Synthetic classifications persist. Known operative versions can
be attributed from protected version history; candidate-specific engineering
attribution still needs its commissioned candidate evidence. Importing expenses
changes the registry manifest, so emit another capture afterward. Existing or
reconciled registry records with the same receipt ID must match the derived
amount, classification, outcome and source/conversion links exactly.

The downstream gate can require a sealed inventory's ledger-export SHA-256 to
equal the complete runtime source SHA-256, its receipt set to equal the collector's
derived `receipt_ids`, and no `unresolved_reservation_ids`. These conditions prove
current local consistency. `RuntimeVerification.actual_external_provenance_verified`
is always false: protected HTTP observations, request IDs, source labels, supplied
keys and synthetic flags do not independently corroborate a real provider bill or
venue observation. Missing external transport provenance, complete provider
invoices, baseline runtime collection and independence/regime source verification
remain explicit reasons. The collector emits source/expense evidence, and cannot
invent four-arm performance, measured risk, independent regimes or a completed
future horizon from missing records. Arbitrary observation imports retain
`verification_basis="unverified_imports"`; no method grants live authority.

For an operator-selected future trial, the protected composition uses:

```python
collector.bind(existing_trial_id)  # before its forward horizon
capture = collector.capture(existing_trial_id)
verification = collector.verify(capture)
collector.import_expenses(capture)
capture = collector.capture(existing_trial_id)  # binds the changed registry
```

The example neither supplies an owner deployment nor starts a real forward trial.

Trading-only P&L is closing equity minus opening equity and external flows, with
embedded operating expenses added back. Fill fees and measured slippage already
affect equity and appear as diagnostics; they are not deducted again. Actual
recurring expenses and all-in recurring plus setup/engineering expenses are
distinct. Synthetic modeled receipts appear separately and prevent an actual
economic support verdict. Embedded expenses must also appear in the imported
expense inventory, so all-in costs count exactly once. Financial reports are
monetary P&L comparisons, not a time-weighted return estimator; flow timing,
high-water marks and exposure measurements must come from the source collector.
Drawdown is labeled sampled and must be collected across the complete trial,
including internal block marks rather than only closing equity.

The report also reconstructs a continuous sampled EUR boundary path. Trading
boundary equity is closing equity minus cumulative net external flows plus
cumulative embedded operating expenses. All-in boundary equity subtracts each
allocated actual expense by its retained incurred timestamp; historical costs
affect the opening sample, and later attributed costs use the last observed
market mark with that limitation disclosed. High-water marks carry across every
block, starting at the declared initial capital. The reported risk drawdown is
the largest reconstructed trading/economic boundary drawdown or imported
internal-block drawdown. Deposits, withdrawals and embedded expenses cannot
erase losses or count costs twice. These are sampled monetary drawdowns, not
continuous observation or a time-weighted return estimator. Nonzero block
flows provide no timing or unitised NAV path in the current contract; such a
trial remains `insufficient_evidence` for its risk gate until a trusted
collection contract supplies that missing information.

Uncertainty uses a two-sided Decimal Hoeffding bound on each block's paired
net-economic excess against each baseline, with a Bonferroni correction for the
three baselines and the predeclared maximum family trial count. Actual forward
recurring and engineering expenses belong to their incurred block, so a large
first-block expense cannot disappear from its bounds check by being averaged
across the trial. A receipt exactly at a closing boundary belongs to the completed
block; a receipt at the first opening belongs to the first block. Only setup
expenses incurred strictly before the forward window are fixed overhead shifted
equally across blocks. Historical recurring expenses, expenses after the forward
window and costs for uncollected blocks keep their all-in monetary attribution
but cannot establish a dated statistical allocation. Their intervals remain
unavailable and the verdict stays `insufficient_evidence` until a richer trusted
collection contract supplies the missing allocation evidence. Blocks must be equally sized, untouched,
complete, independent and drawn from a population inside the predeclared excess
bounds. A bounds violation invalidates the interval. Nonoverlapping outcome
horizons provide a count used for the minimum-decision gate; they do not prove
independence. Autocorrelation, source selection and regime change remain explicit
limitations. An operator must choose conservative bounds and collect suitable
blocks; a narrow observed range cannot justify narrowing bounds after the run.

`supported` requires complete imported forward evidence and cost inventory,
known actual costs, required regime/decision coverage, compliant drawdown and
turnover, a met all-in hurdle and lower uncertainty bounds above every declared
baseline hurdle. Complete evidence that fails declared economic/risk criteria
returns `not_supported`. Missing, synthetic, correlated-insufficient,
out-of-bounds or unresolved evidence and overlapping uncertainty intervals can
return `insufficient_evidence`. Even a supported imported result is conditional
on the declared population/independence assumptions and upstream verification;
it is not a promise of profitability or permission to allocate real capital.

Sensitivity cases predeclare higher fees, additional spread/slippage per
turnover, higher operating costs/additional model calls and missed profitable
block P&L penalties. These are transparent penalty envelopes, not new observed
fills or a counterfactual execution replay. Additional stressed friction is
deducted beyond the fees/slippage already included in equity. The report shows
all four arms and excess against each baseline in each scenario.

The older `evaluate_forward(...)` helper is retained for the offline demo. It
always reports `insufficient_evidence`; a count and positive P&L cannot establish
preregistration, real source provenance or uncertainty.

## Remaining delivery sequence

- T17 must provide an actual owner-funded credentialed paper collector and its
  complete receipts; absent credentials or operating allowance remain pending.
- T18 must preregister its real collection protocol, attach complete upstream
  trial/expense exports and venue differences, collect the untouched forward
  blocks and review independence/regime assumptions. The software/tests are
  preparation, not completion of this observation-dependent task.
- T19 now has normalized Kraken metadata, native fees, orders, balances and
  bounded fill history, plus a protected REST transport. The
  [adapter review](reviews/2026-10-02-kraken-foundation.md) records official
  source evidence, synthetic conformance and unresolved wire/accounting limits.
  Actual authenticated checks need owner-selected venue,
  eligibility and permitted least-privilege credentials. Real-order testing
  requires separate explicit owner authority.
- T20 stays closed pending T18/T19 plus eligibility, explicit owner loss-capital
  and operating budgets, key permissions, protection/recovery, host/backup/alert
  readiness and a deliberate owner enablement record. USD10,000 paper capital
  cannot establish live allocation. Caller-supplied booleans in a fixture gate
  are not verified prerequisite evidence.
- T21 is dependency-ready after T14/T16. Its new
  [owner-pinned process scaffold](PROCESS-BOUNDARY.md) confines arbitrary Python
  before parsing candidate input and has actual-host synthetic adversarial
  evidence. The production financial kernel, gateway and controller still need
  extraction, authenticated durable business RPC, immutable owner-pinned images
  and deployment-host protection/recovery tests before claiming A39/A40.
- T22 requires T18 and demonstrated T21 plus an owner-granted broader class,
  then pure-plugin capability/determinism checks, immutable staging/build
  artifacts, compatible migrations and controller-driven rollout/rollback.
  Executable plugins and application-code promotion remain disabled until
  these gates are demonstrated. Repository implementation work does not grant
  the deployed Engineer new permissions.
