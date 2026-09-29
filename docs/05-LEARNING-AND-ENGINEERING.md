# 05 — Learning, experiments and autonomous implementation

## Learning is evidence revision, not outcome rationalization

The Learning Analyst reads point-in-time decisions, research, market regimes, fills/friction, no-trade opportunities and organisational changes. Its goal is to identify what should be tested and what the evidence supports. The Optimisation Analyst uses the same records to evaluate whether work was useful, redundant, slow or too expensive. Neither may change production trading behaviour directly.

A losing trade can reflect a reasonable risk that happened not to pay off; a winner can reflect an unsound decision rescued by an unexpected event. Evaluate whether the original thesis used information available then, respected its stated invalidation, priced friction realistically and chose a defensible size. Separate market loss, model reasoning error, data/research error, execution failure and operational/coordination failure. Do not label every negative return an execution bug.

Retain lesson revisions with observation, supporting cases, counterexamples, possible explanations, scoped applicability, proposed improvement, validation and subsequent results. Suggested states are tentative -> testing -> supported, with contradicted/retired transitions and reopening allowed. Never overwrite the earlier claim. Retrieval prefers current relevant revisions but must disclose meaningful contradictory evidence.

A weekly batch can be too small or too correlated to establish a pattern. Include sample size, observation window, regime mix, selection method and dependence caveats. Supported status requires predeclared evidence criteria suitable for the hypothesis; one successful trade does not qualify. This is persistent knowledge/configuration improvement, not foundation-model weight training.

## Evaluation and opportunity records

Record scheduled and event-driven opportunities even when no order is placed. Distinguish deliberate abstention from budget exhaustion, missing data, refusal, timeout and exchange minimums. For selected no-trade decisions, simulate a predeclared counterfactual using only information available then and the same costs/fill assumptions. Label this simulated, including its unobserved fill limitations; never search hindsight for the best possible entry and call that a missed profit.

Benchmark a strategy against cash and simple buy-and-hold, and compare the agent with a deterministic baseline using the same data, fees, size constraints and capital-flow accounting. Report both trading-only and net economic results, turnover, drawdown, exposure, opportunity coverage, cost per useful decision and operational errors. Do not select a model solely because it issues more trades or produces more confident explanations.

Use chronological development, validation and untouched test windows, walk-forward evaluation and a separate forward-paper stream. Purge overlapping trade/outcome horizons at split boundaries where appropriate. Record every attempted strategy/configuration trial to expose selection bias. Reserve final test data outside the Engineer's readable context; repeated optimization against the same evaluation set makes it development data. Statistical uncertainty and regime change remain even with a positive test result.

Keep causal claims modest: simultaneous strategy, model and scheduling changes make attribution difficult. Prefer one material change per experiment or explicitly label a bundled intervention. Learning may conclude that evidence is insufficient, but should propose a practical next test rather than automatically adding another restriction.

## R1 Engineer: actual artifact implementation

R1 permits automatic changes to validated parameter files, small role prompts, schedule settings, report templates, context-selection limits and assignments among already-approved model/provider entries. Graph connections may change only among registered nodes with validated edge rules and bounded task policies. No arbitrary expressions, executable configuration, new tools or permission expansion.

Each authorized task names a baseline version, exact editable paths/classes, objective, evidence, expected benefit, invariants, maximum spend/attempts, tests, expiry, activation criteria and rollback criteria. Example limits are five changed files, 200 changed lines, ten paid model attempts and a sandbox CPU/wall-time quota. Limits are enforced by the runner; the Leader can choose smaller tasks. The operator may configure different bounds within the owner policy.

The Engineer receives a sanitized source snapshot and read-only/synthetic evidence. Through small tools it writes a real patch in an isolated Git worktree, runs approved checks and submits a content-addressed artifact. It does not merely explain how a human could edit a file. Failed tests may lead to a bounded repair attempt; every attempt and rejected candidate still has a usage receipt and retained result.

## Worked automatic improvement lifecycle

Suppose Optimisation observes repeated injection of eight mostly irrelevant lessons into every Trader context. Learning supplies representative decisions and critical lesson IDs. The Leader authorizes an experiment changing a context policy from eight general lessons to five relevance-ranked lessons while always including active safety/mandate obligations.

1. **Commission:** persist ChangeTask, expected baseline hash, task budget and untouched validation set identifiers. Do not let the candidate choose easier acceptance criteria.
2. **Prepare:** runner creates an isolated worktree at that baseline; no production DB, API keys, SSH agent, owner-policy file or Docker socket is exposed.
3. **Implement:** Engineer edits the allowed context-policy artifact and any allowed prompt text, producing a real diff and manifest. R1 rejects source-code changes for this task class.
4. **Validate:** independent runner checks JSON schemas, forbidden paths/capabilities, deterministic budget/execution invariants, representative context cases, evidence provenance and token counts. Replay/shadow evaluations check retention of required lessons and decision-quality regression under a predeclared tolerance. Functional tests do not prove trading alpha.
5. **Attest:** runner, not Engineer prose, records commands, exit codes, fixture/test hashes, results, resource receipts and candidate content hash. A candidate cannot replace the validation harness.
6. **Decide:** Leader receives the concise result and evidence and chooses activate, reject or commission another bounded change. Its activation tool checks owner-permitted class, budget, required attestations and baseline compatibility in software.
7. **Activate:** controller reaches a quiescent decision boundary, records old/new version, atomically switches the active artifact pointer and restarts/resumes the mutable graph. Pending decisions with incompatible snapshots are invalidated. Reconciliation and protection remain running.
8. **Observe:** subsequent decisions carry the new fingerprint. Track realized token savings and selected quality metrics over the predeclared horizon. Old positions retain their original opening provenance and management policy unless explicitly migrated.
9. **Rollback:** on a health/quality trigger the controller restores the previous artifact pointer and restarts the graph. It retains all orders, fills, expenses and evidence accumulated meanwhile. A rollback never restores an old portfolio database.

The same workflow applies to a parameter or strategy-template experiment, with stronger economic evaluation where appropriate. A cheaper output alone is not sufficient when relevant context was lost. A technically passing but economically weak change can stay in shadow mode without blocking ordinary trading.

## Activation and deployment state

Candidate states: `AUTHORIZED`, `DEVELOPING`, `VALIDATING`, `FAILED`, `READY`, `APPROVED`, `ACTIVATING`, `ACTIVE`, `OBSERVING`, `REJECTED`, `ROLLED_BACK`, `SUPERSEDED`. Store results even after failure. The active pointer changes with a compare-and-set against the tested baseline. If another version activated meanwhile, rebase and revalidate; do not apply a stale patch automatically.

R1 artifacts must not need database migrations. Later code changes use compatible expand/contract migrations, rehearsal on a snapshot and an explicitly tested rollback path; destructive migrations or protected-kernel changes require owner-controlled release work. A health check validates loaded artifact hashes, schema compatibility, worker lease and reconciliation readiness, not merely HTTP 200.

An emergency kernel rollback/deployment controller cannot require the same failing model or graph version it is recovering. It must operate deterministically from owner-pinned policy. Leadership can request restart; the controller performs it and records the outcome.

## Broader engineering authority roadmap

**Class A / R1:** allowlisted data artifacts and prompts within existing capabilities. No arbitrary production code, new dependencies or infrastructure mutations.

**Class B / later:** pure strategy/feature plugins executed in an unprivileged process with numeric snapshot input, bounded output and no network, filesystem mutation or production credentials. Independent tests include deterministic replay, resource limits and conformance. The protected kernel still validates every order.

**Class C / later:** selected application/UI/workflow modules, built into a new immutable image and tested in staging. First separate the credentialed accounting/execution/authority kernel into a protected process with scoped RPC; a module boundary inside one privileged Python interpreter is not sufficient. Candidate changes cannot alter trusted tests, permission policy, deployment keys, CI workflows or the controller.

Owner grants a broader class once its isolation and validation gates are demonstrated. The Leader can then commission and activate routine changes within that class automatically; a permanent human approval committee is not required. New paid vendors, new live venues, higher budgets and protected-kernel changes remain owner decisions.

## Development integration and security

Local Git worktrees and a sandbox runner are sufficient for R1 artifact development. Optional GitHub mirroring stores permitted code/artifact diffs, test summaries and PRs, never private account journals. Use a separate deployment integration identity; the Engineer receives a narrow tool proxy, not a broad GitHub token. Repository Contents permission is not path-level isolation: enforce allowed paths in the trusted service, keep protected branch/ruleset administration outside its identity, and use a separate protected repository/image when needed. GitHub App installation authentication is documented in [S16].

Treat research pages and candidate code as untrusted. Fetch service blocks private/link-local/metadata addresses, rechecks redirects and DNS results, bounds size/time and strips active content. A page claiming to be a new system instruction cannot change roles or authorize trades. Tools independently validate action origins. Engineer sandboxes use non-root identities, read-only base images, bounded CPU/memory/disk, denied egress by default and no writable host/production mounts. Controlled dependency acquisition happens through a reviewed build proxy, not an arbitrary shell curl command.
