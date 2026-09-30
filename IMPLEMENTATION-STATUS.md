# Implementation status — review recovery checkpoint

Updated: 2026-09-30. **Partial offline prototype; the autonomous R1 service is not complete.** Paid model calls and live trading remain disabled. Do not jump to the live pilot merely because the earlier task list said `done`.

## Saved work

- Repository: `krybaby7/trade-graph`.
- Integration branch: `cursor/trade-graph-r1-548a`; draft PR #1 remains unmerged.
- Previous review fixes preserved: `9e908d4ca22786d1ee49bff963ac98179c79e0b3`.
- New scheduler/migration fixes: `2e5f1d86c826ab60075301dbc7ed1cf7b1ddb2c0`.
- This document is a later documentation checkpoint on the same branch. Inspect the latest remote head before continuing; never reset it to one of these anchors.

The default stays USD10,000 virtual capital with EUR reporting. Actual operating expenses and any future live allocation are separate. No paid model calls, private exchange calls or real orders were made in this continuation.

## What the interrupted review had already fixed

The previous commit preserves FIFO and third-asset-fee accounting corrections, currency-aware aggregate exposure, atomic fill recording/replay, pause and cancellation recovery, provider usage/price-card/allocation checks, and explicit refusal of unsafe or unwired prototype controls. Those changes were not lost.

## What this continuation added

Expired LEASED/RUNNING tasks can be reclaimed after a crash without resetting their attempt count. Each claim has a unique fencing token; an old worker cannot renew, spend another recorded attempt or overwrite the result of a recovered task. Terminal and WAITING_EXTERNAL tasks are not automatically retried. Attempt exhaustion now commits DEAD_LETTER before raising an error.

Task deduplication, delegation ancestry/caps, schedule creation and schedule-to-task advancement are transactional. Deployment-wide tasks with a null portfolio now deduplicate. Five-minute occurrences no longer collapse into one hourly key. An overdue schedule creates one current opportunity rather than replaying obsolete opportunities.

Additive migration `0002` installs task lease tokens and an index. Migration application is atomic, including nested callers; a failed upgrade rolls back schema and version records together. Existing tasks and attempts survive upgrade. The Alembic bridge advances to `0002`.

**Worker integration is still required:** `Scheduler.claim` now returns `TaskLease`, and `renew`, `note_attempt`, and `succeed` require that token. Reclaiming a task is not proof that its last external call failed. Reconcile persisted orders/usage first; never blindly replay paid calls or submissions.

## Verification actually performed

| Check | Result |
|---|---|
| GitHub Actions Python 3.12, locked dependencies | Runtime run [36699717463](https://github.com/krybaby7/trade-graph/actions/runs/36699717463), job 109836047214: success |
| Ruff on `src tests` | Passed in that CI job |
| Full pytest suite | **113 passed; 0 failures, 0 errors, 0 skipped**; JUnit artifact 11089725511 downloaded and inspected |
| New scheduler/migration regressions | 31 cases included in the full suite |
| Local available Python 3.13 tests | 98 passed; full local suite was not run because LangGraph/Hypothesis were unavailable and dependency installation was network-blocked |
| Planning validation | 23-task DAG/reference checks passed; 10 planning tests passed |
| Database upgrade | Legacy-task preservation and failed-upgrade rollback tested; Alembic upgrade-to-head twice and application reopen verified at `0002` |
| Whitespace check | `git diff --check` passed |

Passing fixture/unit/integration tests is not evidence of paid provider access, exchange compatibility, economic performance, complete security isolation or full product delivery.

## Commands and their present limits

```bash
uv sync --frozen --group dev
uv run ruff check src tests
uv run pytest
python3 scripts/test_planning.py
python3 scripts/check_plan.py
python3 scripts/next_task.py --prompt
uv run trade-graph init --mode paper --capital 10000 --capital-currency USD --reporting-currency EUR
uv run trade-graph demo --offline
uv run trade-graph run --mode paper
uv run trade-graph pause --profile manage-only
uv run trade-graph backup --destination runtime/backup.sqlite
uv run trade-graph reconcile
```

`demo --offline` is a scripted scenario, not a continuously operating organization. `run --mode paper` performs one recovery/reconciliation pass and exits without generating new decisions. `doctor` and `report` currently return limited/static status rather than complete diagnostics or financial reporting. `run --mode live` refuses execution. The dashboard is partial; owner-budget and activation endpoints deliberately return not-implemented responses.

## Remaining implementation, not merely missing credentials

1. **Close T01 contracts/authority.** Pydantic schemas exist, but shared broker/provider Protocol interfaces and persisted policy/mandate authority are incomplete. `Execution.authorize` still receives exposure caps as arguments instead of loading the authoritative active mandate. Revalidate the retained T02/T03 foundations after this closure.
2. **Complete T04/T06 integrations.** Market normalization is not a public REST/WebSocket reconnect/backfill client. FX observations are injected, with no attributed FX network client. Both provider adapters currently parse supplied HTTP fixtures; wire real budgeted transports, model capabilities and tool continuations before any paid probe.
3. **Integrate T07–T12 into a working paper service.** Connect persisted scheduling, recovery, research, autonomous trading, learning, optimization and leadership. Keep decisions autonomous within the owner's mandate, without a per-trade approval committee. Distinguish fixture costs from actual receipts.
4. **Finish T13–T17 release acceptance.** Independently trusted artifact checks/attestations, activation/quiescence/rollback, complete dashboard controls and reporting, a production-equivalent offline full loop, then an explicitly owner-funded paper soak. A caller-supplied attestation label is not independent controller evidence.

Forward-paper economic evaluation and any live pilot come later. Broader executable Engineer/plugin support remains disabled: the multiprocessing protocol fixture is not an OS sandbox. T20 requires unfinished software prerequisites as well as separate owner eligibility, allocation and spending authorization.

## Task status interpretation

`planning/progress.json` supersedes the old all-but-T20-done claim. T00 remains accepted. T01–T19 and T21–T22 are reopened `todo` for missing deliverables or dependency revalidation; their existing source and historical test evidence remain intact. T20 stays `blocked`. Here `todo` means **remaining acceptance work, not starting over**. Each reopened task records its remaining work; the next-task utility now selects T01 rather than misleadingly directing the next agent to live trading.
