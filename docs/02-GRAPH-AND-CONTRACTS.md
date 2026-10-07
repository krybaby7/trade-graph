# 02 — Graph execution, departmental contracts and tools

**Owner update, 2026-10-07:** Normal local paper operation supersedes the earlier diagnostic and manual-only restrictions. Enable configured departmental schedules and Optimisation, ordinary configurable execution limits, supported retries and compatible verified existing-subscription fallback. A first Research success or single paper cycle is not a startup gate, and the service does not pause automatically after its first cycle. Preserve accounting, contract validation, credential isolation and order reconciliation. Separately billed APIs, paid extras, purchases and real-money trading remain disabled. Paper operation is independent of live commissioning. See [the normal paper runbook](NORMAL-PAPER-OPERATION.md).


## Event-driven organisation

Use durable application events and tasks rather than agents passing unrestricted chat transcripts. The scheduler is ordinary software. It runs maintenance continuously, checks persisted due times, coalesces related events and invokes a short graph only when useful. Each graph ends; organisational cycles continue through new persisted tasks. The financial ledger and execution service do not depend on a model remaining available.

```text
market observation -> deterministic features / event filter
                                      |
               relevant event or scheduled decision
                                      v
snapshot + budget reservation -> Trader -> validated intent -> execution outbox
              ^                                              |
              |                                  status / fills / reconciliation
research cache <--- Research                                  |
              |                                               v
              +--------------------------------------- evidence journal
                                                              |
                                             Learning + Optimisation
                                                              |
                                  Secretary digest -> Leader mandate / task
                                                              |
                                              Improvement Engineer
                                                              |
                                     independent validation + artifact
                                                              |
                                   Leader activation -> version controller
                                                              |
                                              subsequent operation
```

This diagram describes information flow, not simultaneous permanent model workers. Mandatory account reconciliation, price updates, budgets, fees and recording occur through software services at every applicable step.

## Authoritative task lifecycle

A task contains `task_id`, `root_task_id`, `parent_id`, role, objective, typed input references, due time, priority, deadline, deduplication key, expected version, allocated spend, maximum steps/attempts, lease owner/expiry, status, output references and completion evidence. Statuses are `QUEUED`, `LEASED`, `RUNNING`, `WAITING_EXTERNAL`, `SUCCEEDED`, `FAILED`, `CANCELLED`, `BLOCKED_BUDGET` and `DEAD_LETTER`. Use a separate `PROPOSED` record for an unapproved engineering task; it becomes runnable only after Leader authorisation.

The scheduler atomically claims a due task and establishes a renewable lease. Expired leases can be reclaimed; completed side effects are discovered from durable records, not repeated from memory. A lease alone is insufficient for financial exclusion: ledger/intent transactions also check account revision and reservation constraints. Only one Trader task per portfolio may commit an exposure-changing decision at a time.

Schedule records retain `last_due_at`, `next_due_at`, missed-run policy and the last processed evidence/event cursor. On restart, coalesce missed strategy opportunities into one current decision rather than replaying yesterday's trading orders. Do replay unreconciled execution events and unfinished accounting exactly once at the application-record level. Use an injected clock for deterministic tests.

Default task bounds: three delegation levels, twelve descendants per root task, and a shared root monetary ceiling; an Engineer task may use up to ten model attempts within that ceiling. Ordinary role tasks allow at most three total paid attempts, including a maximum of one schema-repair attempt. These limits are example configuration; the owner sets hard upper bounds and the Leader may allocate less. Configure supported CLI/SDK-internal retries explicitly and retain the attempts the provider exposes; distinguish them from journaled application dispatches. Subscription work uses the existing plan allowance rather than inventing an API expense allocation. Unknown usage or cost remains unknown. Rate-limit waits and retries are delayed durable tasks, not tight loops.

## Invocation policy

| Component | Wake condition | Initial cadence / coalescing | Output |
|---|---|---|---|
| Market/health services | Feed messages, heartbeat, persisted maintenance timers | Continuous software; quote freshness checked before execution | Normalized observations, health and relevant events |
| Trader | Decision interval, material research, invalidation trigger, mandate change, actionable order issue | Four-hour routine opportunities, target six calls/day; coalesce related events, five-minute ordinary debounce | Decision record and zero or more typed intents |
| Research | Daily market refresh, approaching expiry of a relied-on finding, Leader assignment, materially new event | One daily dossier initially; reuse strategy research until invalidated | Findings and testable strategy proposals |
| Learning | Sufficient new comparable decisions/outcomes or a material incident | Twice-weekly check, run a model only with useful evidence; incident review can be earlier | Lesson revisions, hypotheses and validation proposals |
| Optimisation | Configured schedule, Leader assignment, owner Start Optimisation request or useful usage/latency/error evidence | Weekly default; coalesce active work and retain ordinary configured task limits | Workflow/cost improvement proposals under protected acceptance/deployment |
| Leader | Startup, scheduled digest, material incident, completed improvement awaiting activation | Weekly routine review plus bounded exceptions | Mandate/config changes, tasks, pause profile, activation decision |
| Engineer | Authorised task with budget and permitted change class | On demand only | Actual versioned patch/artifact, tests, attestation and summary |
| Secretary | New reports, tasks, deadlines, priority events | Software at event time; synthesis is opt-in and charged | Digest, routing, overdue/blocked work, escalation |

The six Trader calls/day are a planning average, not a requirement to ignore an invalidated position until the next interval. Deterministic protective order management is not debounced. If extra discretionary calls would exhaust funding, the pre-agreed manage-only policy applies. A lack of evidence should suppress a Learning call, not produce a fabricated pattern. Avoid both forced trading and forced inactivity.

## Shared envelope and validation

Export JSON Schema from typed Pydantic models during implementation. All records share: `schema_version`, `record_id`, `created_at_utc`, `run_id`, `task_id`, `root_task_id`, `portfolio_id` where relevant, `mode`, `system_version_id`, `evidence_refs` and `trace_id`. Use UUIDs and decimal strings with explicit currencies/units. `mode` is a protected enum (`paper`, `replay`, `live`), not a model-controlled URL. Reject unknown operational fields and invalid references. Preserve the raw redacted model response separately from the validated business record.

A system version is a composite fingerprint: application commit/build, graph definition, prompts, strategy templates, parameters, model-routing configuration, data-adapter version and protected-kernel version. The owner-policy revision and mandate revision are also pinned on each decision; they are not hidden inside a prompt hash.

### Core business contracts

**Mandate:** universe, strategy/experiment IDs, decision horizons, allowed order types, exposure/sizing ranges, discretionary experiment allocation, required data freshness, execution requirements, review/expiry time and resource allocation. Its envelope must be a subset of owner permissions. A mandate expires into manage-only, not abandonment.

**Decision:** action (`enter`, `exit`, `hold`, `adjust_order`, `resize`, `no_action`), target instrument/position/order references, requested sizing in unambiguous units, optional limit/trigger price, time-in-force, execution deadline, concise rationale, supporting evidence, invalidation conditions, expected horizon, strategy/experiment ID, and snapshot/portfolio/mandate revisions. State uncertainty honestly; do not fabricate a numerical success probability. Any multi-leg plan declares dependency/order requirements; R1 normally uses one order per intent.

**ResearchFinding:** question, claim/summary, source URLs and publisher, publication/event/retrieval times where known, immutable source/excerpt hash, affected instruments, actionable relevance, uncertainty/counterevidence, validity horizon and invalidation triggers. Distinguish unknown publication time from retrieval time. A source refresh creates a new version; it does not rewrite what the Trader saw.

**StrategyProposal:** testable hypothesis, economic mechanism, features with availability times, explicit entry/exit/invalidation and sizing rules, required venue capabilities, estimated turnover/friction, applicable regimes, baseline, validation protocol, and conditions for retiring the hypothesis. Human-trader or AI-system success claims retain provenance and verification status.

**LessonRevision:** observation -> evidence and counterexamples -> possible explanation -> proposed improvement -> validation method -> subsequent result. Include scope/regime, sample selection, competing explanations, confidence category, linked decisions and executions, status (`tentative`, `testing`, `supported`, `contradicted`, `retired`), version and superseded revision. A supported label requires explicit evidence criteria, not model enthusiasm.

**OptimisationProposal:** observed workflow issue, quantified resources and useful outputs, evidence interval, proposed modification, expected benefit, possible quality loss and validation metrics. Distinguish recommendations to eliminate redundant work from recommendations to stop necessary reconciliation.

**LeaderDecision:** evidence considered, brief rationale, intended outcome, approved mandate/task/pause/version changes, resources committed, authority check result and review criteria. The Leader may ask one department a targeted question through a bounded task. Direct consultation does not imply an unlimited chat session.

**ChangeTask / ChangeResult:** objective, baseline version, allowed artifact classes and paths, interfaces that must remain unchanged, maximum spend/steps, test plan, success and rollback criteria; then patch/artifact hashes, changed files, test attestations, actual usage, known limits and an activation candidate. A textual suggestion with no working artifact is not a completed ChangeResult.

**UsageReceipt / ActivityEvent:** defined in 03. Every role uses the same receipt/journal gateway; no privileged role bypasses it.

## Small explicit tool surfaces

| Role | Read tools | Write/action tools | Forbidden access |
|---|---|---|---|
| Leader | Digests, targeted evidence, current policy summary, budgets, change reports | `assign_task`, `set_mandate`, `allocate_within_budget`, `set_schedule`, `set_pause_profile`, `activate_candidate`, `resume_own_pause` | Raise owner allowance, withdrawals, rewrite ledger/gates |
| Secretary | Tasks, reports, schedules, health | Deterministic routing/digests and status updates | Trading, code execution, policy edits |
| Research | Approved search/fetch, market summary, research cache | `save_finding`, `submit_strategy`, notify relevant Trader task | Exchange keys, raw shell, operational instructions from pages |
| Trader | Market snapshot, portfolio/open orders, current mandate, selected research/lessons | `propose_order_intent`, `request_cancel`, `request_adjustment`, save hold/no-action | Raw exchange API, arbitrary URLs, owner policy, direct ledger writes |
| Learning | Time-bounded decisions/outcomes/context, existing lessons, validation results | `append_lesson_revision`, `propose_experiment` | Changing trading controls directly, deleting contrary evidence |
| Optimisation | Usage receipts, activity traces, task outcomes/latency | `submit_optimisation_proposal` | Disabling expense capture or necessary maintenance |
| Engineer | Authorised task, permitted source snapshot, synthetic/read-only evidence | `apply_patch_in_worktree`, `run_approved_check`, `submit_candidate` | Production keys/DB, unbounded shell/network, gate or kernel modification |

These are application capabilities, not model-authored function names with arbitrary server access. Every call is authenticated to a role/task, validated, limited, journaled and attributed. Tool availability comes from the owner-approved registry; graph rewiring cannot manufacture a new capability.

## Context efficiency

Build context in software: current mandate, current authoritative portfolio/orders, recent market/feature snapshot, relevant active research, a small set of applicable lesson revisions and changes since this role's committed cursor. Always include current critical state even when it did not change. Use identifiers and concise tables rather than raw histories. Store long source text in an artifact store and retrieve bounded excerpts only when needed. SQL/FTS plus tags are sufficient initially; add embeddings only after a measured retrieval need.

Pin the context snapshot before inference and record its as-of time. Before accepting an action, re-read owner permissions, mandate validity, pause state, available balances, reservations and market freshness. If material state changed, reject the stale intent with a typed reason and schedule at most one coalesced reconsideration; do not silently reinterpret its sizing. Nonfinancial reports may be accepted with an explicit historical as-of timestamp.

Provider truncation, refusal or malformed output is an operational outcome, not an intentional `hold`. Save the failure, account for paid work and follow the pre-agreed management policy. Do not pad model contexts with prior provider reasoning traces; retain concise rationale, evidence and structured outputs instead.
