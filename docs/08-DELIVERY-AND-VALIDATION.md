# 08 — Development phases, acceptance and live progression

The machine-readable dependency graph is planning/tasks.json. All runtime tasks initially remain unimplemented. The planning utilities validate this handoff, not a trading application. Resolve compatible current dependencies at implementation time and keep the package lockfile, contract schemas and migration ordering under one integration owner.

## Phases and concrete delivery gates

| Phase | Tasks | Deliverable and dependency | Acceptance / exit gate |
|---|---|---|---|
| P0: foundation | T00–T03 | Typed project, domain contracts, migrations/durable stores, deterministic financial ledger and EUR valuations | Fresh install and migration; exact synthetic accounting fixture; immutable policy/ledger boundaries; no SDK import in domain; tests run without credentials |
| P1: deterministic operation and paid gateway | T04–T08 | Market snapshots, simulated broker, OpenAI/Anthropic adapters, reservations/receipts, order state/recovery | Shared broker conformance; fee/precision/partial-fill tests; provider failure fixtures; concurrent budget cap; crash/unknown-order recovery without duplicate submission |
| P2: complete organisation | T09–T12 | Durable scheduling, research/Trader, Learning/Optimisation, Leader/software Secretary | Scripted roles complete evidence flow; bounded delegation; direct research-to-Trader delivery; true trade/no-trade distinction; Leader delegates orders rather than approves each one |
| P3: implemented improvements and dashboard | T13–T15 | Isolated Engineer, trusted validation/activation/rollback and owner dashboard | Actual allowed patch written/tested/activated; forbidden patch rejected; next decision carries new version; open orders survive update; dashboard reconciles to ledger and receipts |
| P4: R1 release | T16–T17 | Fault suite, reproducible offline demonstration, local deployment, backup/restore and credentialed paper soak | Full loop including rejected change and restart; no unexplained financial difference; paid usage reconciled; honest verification report and owner-funded live-data paper run |
| P5: economic/live assessment | T18–T20 | Forward-paper evaluation, conditionally eligible live adapter and owner-enabled tiny live pilot | Separate economic and operational report, eligibility/permissions proven, venue conformance, spend/capital limits, no automatic live enablement |
| P6: broader engineering | T21–T22 | Protected kernel process and unprivileged mutable plugins/application modules | Candidate cannot access secrets/kernel/policy/gates; adversarial isolation tests, staged deployment and proven rollback before broader owner authority grant |

R1 means P0–P4. It exercises the entire trading/learning/automatic-improvement loop, not a partial organisation postponed to P6. P5/P6 expand evidence, live support and engineering authority rather than add the missing core concept.

## Required acceptance catalogue

Tests use frozen clocks, synthetic market events, scripted model adapters, temporary databases and bounded sandboxes by default. Real-credential tests are explicitly marked and opt-in. A test is not passed until its command/result and evidence are recorded.

### Financial and cost correctness

- **A01** The exact EUR100 buy/mark/sell fixture in 03 yields trading P&L EUR3.16 and economic P&L EUR2.26 after the external expense.
- **A02** Deposits/withdrawals change capital but not profit; internal transfers do not appear as new capital or expense.
- **A03** Partial fills, multiple lots, fee-in-base/quote/third-asset, rebates and rounding reconcile to native postings and realized/unrealized projections.
- **A04** Fees embedded in fills, spread/slippage and invoice settlement are not deducted twice; embedded versus externally paid operating expenses yield the same economic result.
- **A05** Missing/stale FX/marks are visible and provisional; historical rates and later invoice corrections retain provenance.
- **A06** Concurrent paid requests cannot oversubscribe total/period/root budgets; reservations include worst approved output and paid-tool limits.
- **A07** Failed, repaired, retried, fallback and Engineer/Leader work all create receipts; uncertain billing stays reserved or explicitly conservatively charged, never silently zero.
- **A08** Native cache categories/reasoning-output usage normalize without double counting; invoice reconciliation exposes unexplained differences.

### Execution and recovery

- **A09** Duplicate events, repeated tasks and replayed graph nodes do not create a second local intent/fill/journal posting.
- **A10** An order accepted and filled before a lost acknowledgement enters UNKNOWN and is recovered from history/fills without a second submission.
- **A11** A cancel/fill race and partial-fill cancel/replace never sell more than owned inventory or replace the original full quantity.
- **A12** Instrument rules, minimums, rounding, fee reserves and unsupported order features produce correct typed rejection without unauthorized resizing.
- **A13** Stale/out-of-order/gapped data cannot trigger a fresh increase; deterministic protection/reconciliation remains available.
- **A14** Process death before submission, during response handling and after a fill recovers from database/outbox/broker state with consistent reservations.
- **A15** Every pause profile in 04 has tested entry/achieved states; owner pauses cannot be lifted by Leader, and flatten is not declared complete before verified.
- **A16** Exchange outage and disk/DB failure do not produce fictitious completion or unjournaled orders; startup reconciles before increasing exposure.

### Models, graph and evidence

- **A17** Both provider adapters satisfy structured-output/tool/usage fixtures, including refusal, truncation, bad schema, unsupported settings, timeouts and rate limits.
- **A18** Schema-repair and transport retries share total attempt/root limits; a fallback is priced and explicitly attributed.
- **A19** Task leases, ownership, deduplication, cursor commits and missed-run coalescing work across restart; no duplicated scheduler starts from a second web worker.
- **A20** Root delegation/step/cost limits stop recursive task creation, including cycles introduced by an attempted graph configuration change.
- **A21** A Trader can submit a valid order without Leader approval, and can explicitly experiment; no hidden confidence/consensus requirement prevents valid action.
- **A22** A hold/no-action decision differs from unavailable model/data/budget; no-trade evidence and scoped counterfactuals are retained.
- **A23** Research sources preserve publication/retrieval/available-at times and expiry; stale findings are not represented as fresh, and only relevant deltas enter context.
- **A24** A lesson links evidence and counterexamples, can be revised/contradicted/retired, survives restart and remains tied to the versions that generated its cases.
- **A25** A winning trade with an invalid process and a losing trade with a valid thesis are not mechanically classified as good and bad respectively.
- **A26** Historical replay and selected no-trade evaluation use no future data, optimistic same-bar fills or hindsight-picked entry rules.

### Engineering, trust boundaries and visibility

- **A27** An authorized Engineer task writes a real artifact in a worktree and produces independent test evidence; an advice-only response cannot finish the task.
- **A28** Attempts to edit budgets, ledger definitions, receipt collection, protected paths, test gates or production permissions are rejected by software.
- **A29** Malicious source text cannot issue operational instructions, expose keys, visit private metadata endpoints or escape the fetch/sandbox boundary.
- **A30** Invalid/over-budget/failed candidates leave the active version unchanged; their costs and evidence remain visible.
- **A31** Activation uses tested content hashes and baseline compare-and-set; stale candidates require revalidation.
- **A32** Rollback keeps all financial events/positions and restores graph artifacts only; old opening and new closing decisions retain their separate version attribution.
- **A33** Candidate tests cannot replace the trusted harness or write fake attestations accepted by the controller.
- **A34** Dashboard portfolio, fee, expense, order and activity values reconcile to authoritative APIs; paused/degraded/uncertain states are clearly distinct.
- **A35** Owner configuration endpoints require authentication/authorization, reject CSRF where applicable and never return secrets; role identities cannot call owner-only writes.
- **A36** Consistent backup/restore recovers state and references; truncated or corrupt data is detected rather than silently ignored.
- **A37** Public repository and CI artifacts contain synthetic data only; secret scans reject credential/private-runtime leakage.
- **A38** The offline full-loop scenario completes a trade, a lesson, a real implemented change, activation, a subsequent new-version decision and recovery; a rejected change is also demonstrated.

P6 adds **A39** independent-process kernel isolation under malicious mutable code, **A40** sandbox egress/resource/host-mount protection, **A41** pure-plugin determinism/capability checks, and **A42** staging/live code rollout with a controller independent of candidate/model health. Test required capabilities on the actual deployment platform, not just in a mocked permissions class.

## Offline integration fixture

Create a deterministic replay containing: a warm-up feature window, a relevant sourced research fixture, a valid buy decision, a partial fill then remaining fill, a later reducing decision, an intentionally lost acknowledgement that resolves without duplicate submission, synthetic provider receipts, a tentative lesson with counterevidence, a context-policy improvement, activation and a second-version decision. Add an invalid Engineer patch and budget exhaustion to a separate branch of the fixture.

The script emits a machine-readable report with ledger checks, task/decision/order/lesson/version IDs and actual test results. It must be runnable without keys. A scripted Trader may intentionally create a loss; functional success does not require profit. No credentials are needed to demonstrate architecture, idempotency or the improvement loop.

## Forward-paper and economic validation

After offline gates, run a credentialed paper soak with a declared owner budget, actual provider receipts and current public market data. Start with a short operational smoke/soak, then gather enough independent decisions and market regimes to evaluate the hypotheses. A suggested 30-day observation window is a starting collection target, not sufficient evidence by itself; sparse or correlated samples need longer or a narrower claim.

Predeclare the baseline, target net-of-friction and net-economic metrics, drawdown tolerance, turnover assumptions, permissible model/configuration changes and stop/review conditions. Report sensitivity to higher fees/slippage, additional model calls and missed fills. Record each experiment and keep a held-out forward window; repeatedly choosing the best version on the same history is not out-of-sample validation.

T18 returns a report with `supported`, `not_supported` or `insufficient_evidence`, not a forced positive verdict. It must show the complete cost burden and actual differences between paper assumptions and observed venue conditions. The Leader may continue bounded paper experiments after a negative result; it cannot edit the scoring definition or claim that a backtest proved profitability.

## Live enablement checklist

Live mode is disabled by default and requires an explicit owner action, separate from completing code tasks. Required conditions: current legal/account eligibility; funded account and deliberate loss-capital allocation; correct key permissions with withdrawals absent; verified pair/rule/fee metadata; successful read-only reconciliation; broker conformance and order-uncertainty tests; tested pause/protection behaviour; reliable host/backup/alerts; and a declared all-in operating budget.

For economic progression, require credible after-cost paper evidence or an explicitly labeled owner-approved diagnostic pilot whose purpose is measuring execution differences, not asserting a profitable strategy. The dashboard must display an insufficient-evidence status for the latter. An owner who elects such a pilot accepts a limited possibility of loss; the Leader still cannot enlarge that allocation.

Start with the smallest practical eligible spot size that satisfies venue minimums, one instrument and no leverage. Compare real fills/fees/latency/rejections with paper assumptions. Expand only under the owner's existing envelope and a recorded review. If minimum sizes make EUR100 impractical, report that rather than quietly increasing capital. The same core services operate in both modes; only the bound broker/data adapters and mode-specific permissions change.

## Handoff discipline

For each task record status, owner, branch, commit, commands, results, artifact references and remaining limitations in planning/progress.json and IMPLEMENTATION-STATUS.md. Do not mark a credentialed check passed from a mocked fixture. Preserve partial work and exact next steps on interruption. The task selector is a planning convenience; it does not replace human/agent review of gate evidence or authorize live operation.
