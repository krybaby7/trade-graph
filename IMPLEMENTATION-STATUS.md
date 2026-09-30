# Implementation status — Engineer continuation

Updated: 2026-10-01. **T00–T13 complete at their recorded application scopes; autonomous R1 is not complete.**
Repository: `krybaby7/trade-graph`; branch: `cursor/trade-graph-r1-548a`.
Implementation checkpoint: `a503cacd481f6f84c1e37723ebcfa2293c26cf07`.
Default USD10,000 virtual capital with EUR reporting. Paid calls, live trading and executable Engineer extensions stay disabled.

## Recovery and completed work

The onboarding checkout was still on planning-only `main` (`7770d17`). The latest implementation was recovered
from the existing remote branch at `9376d2d`; no newer work was reset. Its full 280-test baseline passed locally.
The saved task/status documents lagged the two latest Engineer foundation commits:
`f3231b7` added durable gateway invocations; `9376d2d` added the Linux seccomp data-only checker.

T12 was already complete at application-workflow scope. **T13 was unfinished and is now complete for registered,
allowlisted data artifacts.** Persisted Leader commissions now run through `EngineerHandler`, `RoleWorker` and
the real budget gateway. The Engineer generates actual files, independent checks attest them, bounded repairs
receive their own receipts, and completion evidence reaches the Secretary and Leader. Unknown model dispatches
wait for reconciliation without replay. Revoked authority cannot erase billing facts. Expired/replaced workers
cannot publish candidates. Local recovery preserves old work directories and stops after three recoveries per invocation.

The offline demo now uses this path for a rejected protected patch and its successful repair, then routes
the independently checked candidate through the Secretary to a gateway-backed Leader activation decision.
It no longer manufactures the Engineer's rejected-attempt receipt or supplies its successful files directly.

## Verified commands and results

```bash
uv sync --frozen --group dev
uv run ruff check src tests
uv run pytest
python3 scripts/check_plan.py
python3 scripts/test_planning.py
uv run trade-graph demo --offline --work <fresh-private-directory>
```

- Python **3.12.14**, unchanged `uv.lock`, installed successfully in this cloud machine.
- Full suite: **349 passed; zero failures, errors or skips**. JUnit counts verified.
- Ruff and all **10 planning tests** passed; planning references/DAG and cost calculation passed.
- **40 Engineer workflow regressions** and **29 HTTPX failure regressions** were added.
- Standalone CLI demo passed: two commissioned Engineer attempts/receipts, rejected patch, actual trusted checks,
  Secretary-routed Leader activation, four fills preserved across restart and zero restart submissions.
- Staged source/test hygiene scan passed. Runtime data, credentials and environments are not tracked.
- These are local, credential-free results. CI results for the new checkpoint were not retrieved: GitHub GraphQL
  access returned Forbidden. Earlier CI results remain historical evidence in `planning/progress.json`.

Use a fresh private demo directory: the demo deliberately retains its prior database and artifact evidence.
No provider API call, credentialed exchange operation or new public-market smoke was performed.
HTTPX tests use mocked HTTP responses. Synthetic receipts are not actual provider bills.

## Next dependency-ready task: T14

Implement artifact-consumer reload, controlled restart, deterministic observation health criteria and automatic rollback.
The demo's context selection still receives its policy explicitly; changing a pointer/version label does not prove
that a running consumer loaded the activated artifact. Existing quiescence, baseline CAS, attestation and financial
history preservation tests remain foundations for T14.

**T15:** complete financial/organisation/cost/change views, missing APIs, evidence navigation and owner controls.
**T16:** complete the acceptance fault catalogue and production-equivalent full-loop paths beyond the scripted demo.
**T17:** replace one-pass `run` and placeholder `doctor`/`report` with a continuous paper service, genuine diagnostics,
deployment/backup procedures, then separately funded credentialed paper verification.
**T18–T22:** forward-paper economic evaluation, live-adapter verification, separately authorized live pilot and
protected-kernel isolation before broader executable engineering. No live pilot or paid soak is authorized here.

## Scope and handoff

The independent checker covers owner-pinned registered data grammars, required obligations, paths, manifests,
content/harness identity and Linux data-pipe confinement. Task prose cannot replace the gates. Unsupported model
assignments/graph edges and executable classes stay rejected. Prompt changes require an explicit owner/commission
class; `artifact_config` retains historical compatibility for registered JSON data only.

This is not strategy-quality, token-savings, profitability or arbitrary-code isolation evidence. Those evaluations,
T14 observation, T17 operating-service integration and T21 protected-host boundaries remain distinct.
Read `AGENTS.md`, `IMPLEMENTATION-START-HERE.md`, task-referenced specs and
`docs/reviews/2026-10-01-engineer.md` before continuing. Inspect current Git state and remote head; never reset newer work.
