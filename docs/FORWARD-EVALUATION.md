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
7. `report(trial_id)` returns JSON-compatible Decimal strings, trial/variant
   history, receipt allocations, cost/provenance limitations, baseline
   comparisons, uncertainty and sensitivity. Reporting is a read of imported
   evidence and cannot enable live trading or broader Engineer authority.

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

Uncertainty uses a two-sided Decimal Hoeffding bound on each block's paired
net-economic excess against each baseline, with a Bonferroni correction for the
three baselines and the predeclared maximum family trial count. All-in expenses
shift the paired block amounts equally. Blocks must be equally sized, untouched,
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
- T19's original Kraken adapter is a wire fixture: metadata, open orders and
  fills return empty data and authenticated transport/conformance are absent.
  Complete normalized metadata/fees/order/fill mapping and read-only
  reconciliation first. Actual authenticated checks need owner-selected venue,
  eligibility and permitted least-privilege credentials. Real-order testing
  requires separate explicit owner authority.
- T20 stays closed pending T18/T19 plus eligibility, explicit owner loss-capital
  and operating budgets, key permissions, protection/recovery, host/backup/alert
  readiness and a deliberate owner enablement record. USD10,000 paper capital
  cannot establish live allocation. Caller-supplied booleans in a fixture gate
  are not verified prerequisite evidence.
- T21 is dependency-ready after T14/T16, but its same-UID process protocol
  fixture and AST denylist do not separate the production kernel. Implement an
  independently pinned kernel/controller with scoped authenticated RPC and an
  unprivileged OS-enforced mutable runtime; test host-files, inherited
  descriptors, credentials, network, resource limits and independent rollback
  on the intended deployment host before claiming A39/A40.
- T22 requires T18 and demonstrated T21 plus an owner-granted broader class,
  then pure-plugin capability/determinism checks, immutable staging/build
  artifacts, compatible migrations and controller-driven rollout/rollback.
  Executable plugins and application-code promotion remain disabled until
  these gates are demonstrated. Repository implementation work does not grant
  the deployed Engineer new permissions.
