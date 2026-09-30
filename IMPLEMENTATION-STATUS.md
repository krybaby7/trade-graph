# Implementation status — public market data

Updated: 2026-09-30. **Partial offline prototype; the autonomous R1 service is not complete.** Paid model calls and live trading remain disabled. T00–T07 are accepted. The next dependency-ready work is pause, flatten and cancel-all workflows (T08), not a live pilot.

## Saved work

- Repository: `krybaby7/trade-graph`.
- Integration branch: `cursor/trade-graph-r1-548a`; draft PR #1 remains unmerged.
- Previous review fixes preserved: `9e908d4ca22786d1ee49bff963ac98179c79e0b3`.
- New scheduler/migration fixes: `2e5f1d86c826ab60075301dbc7ed1cf7b1ddb2c0`.
- Authority closure on the same branch, after `7e0aab1`. Inspect the latest remote head before continuing; never reset it to one of these anchors.

The default stays USD10,000 virtual capital with EUR reporting. Actual operating expenses and any future live allocation are separate. No paid model calls, private exchange calls or real orders were made in this continuation.

## What the interrupted review had already fixed

The previous commit preserves FIFO and third-asset-fee accounting corrections, currency-aware aggregate exposure, atomic fill recording/replay, pause and cancellation recovery, provider usage/price-card/allocation checks, and explicit refusal of unsafe or unwired prototype controls. Those changes were not lost.

## What this continuation added

T01 is closed. `Broker` and `InferenceAdapter` are provider-neutral protocols. `Execution` accepts a `Broker` and refuses a capability set that enables withdrawals, lacks client-id lookup, or names a different venue or mode. Order authorization loads the active owner-policy revision and mandate from SQLite. Exposure caps and quote age are the tighter of those documents. A decision that cites a stale revision is rejected. An expired mandate blocks new exposure and still allows a reduction. Tightening the active mandate rejects an unsent increase before submit and releases its reservation. Policy and mandate revisions are immutable. Leader and trader roles cannot write owner policy or enable withdrawals.

Public market data now has a Kraken REST and WebSocket client and a Frankfurter ECB reference-rate client. Both take an injectable transport. Scripted tests cover reconnect, REST backfill after a dropped socket, ignoring an older ticker, and hiding observations that were not yet available. A normalized public book can partially fill a paper order. The fixed paper fee tier is unchanged. The public smoke above is separate from those tests and did not submit an order.

Provider calls now use an injectable HTTP transport. OpenAI and Anthropic bodies include registered tools and tool results. The gateway continues only registered tools, reserves each step, and rejects an unlisted model or an unsupported sampling temperature before any post. An installation can enable one provider. A request with no API key writes synthetic receipts and does not reduce the real allowance. The supplied-fixture path used by the budget tests is unchanged and still non-synthetic. `HttpxProviderHttp` is implemented and was not called. No OpenAI or Anthropic credential was used.

Expense control now treats schema repair, a transport retry and a provider fallback as separate reservations that share the root-task limit. A fallback names its own price card and model. An uncertain timeout stays reserved. Invoice reconciliation stores the difference between a supplied invoice total and recorded non-synthetic receipts, and a second total for the same invoice id conflicts. Expense views group the same holds by role, task and system version. A paper deposit of EUR10,000 does not increase the operating allowance. Migration `0003` adds those columns and the reconciliation table. No provider bill was downloaded.

The scheduler recovery below remains in force.

Expired LEASED/RUNNING tasks can be reclaimed after a crash without resetting their attempt count. Each claim has a unique fencing token; an old worker cannot renew, spend another recorded attempt or overwrite the result of a recovered task. Terminal and WAITING_EXTERNAL tasks are not automatically retried. Attempt exhaustion now commits DEAD_LETTER before raising an error.

Task deduplication, delegation ancestry/caps, schedule creation and schedule-to-task advancement are transactional. Deployment-wide tasks with a null portfolio now deduplicate. Five-minute occurrences no longer collapse into one hourly key. An overdue schedule creates one current opportunity rather than replaying obsolete opportunities.

Additive migration `0002` installs task lease tokens and an index. Migration `0003` adds reservation version/attempt labels and an invoice-reconciliation table. Migration application is atomic, including nested callers; a failed upgrade rolls back schema and version records together. Existing tasks and attempts survive upgrade. The Alembic bridge advances to `0003`.

**Worker integration is still required:** `Scheduler.claim` now returns `TaskLease`, and `renew`, `note_attempt`, and `succeed` require that token. Reclaiming a task is not proof that its last external call failed. Reconcile persisted orders/usage first; never blindly replay paid calls or submissions.

## Verification actually performed

| Check | Result |
|---|---|
| GitHub Actions Python 3.12, locked dependencies | Runtime run [36699717463](https://github.com/krybaby7/trade-graph/actions/runs/36699717463), job 109836047214: success |
| Ruff on `src tests` | Passed in that CI job |
| Full pytest suite at the prior CI head `2e5f1d8` | **113 passed** in GitHub Actions run [36699717463](https://github.com/krybaby7/trade-graph/actions/runs/36699717463); JUnit artifact 11089725511 |
| Local Python 3.12 `uv run pytest` after T04/T05 | **129 passed; 0 failed, 0 skipped** |
| Local Python 3.12 `uv run pytest` after T06 | **133 passed; 0 failed, 0 skipped** |
| Local Python 3.12 `uv run pytest` after T07 | **136 passed; 0 failed, 0 skipped** |
| `uv run ruff check src tests` | Passed locally after the provider transport |
| Separately labeled public smoke | 2026-09-30: Kraken `AssetPairs` XBTUSD, REST ticker, one WebSocket v2 ticker snapshot, Frankfurter ECB USD/EUR 0.88067 dated 2026-09-29. No API key and no order. Live reconnect was not part of that smoke |
| New scheduler/migration regressions | 31 cases included in the prior CI suite and still present |
| Planning validation | 23-task DAG/reference checks passed; 10 planning tests passed |
| Database upgrade | Legacy-task preservation and failed-upgrade rollback tested; application reopen now includes `0003` |
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

1. **Complete T08 management and protection.** Persisted mandate checks are in place. `CANCEL_ALL`, `FLATTEN` and verified `STOPPED` workflows are not. Owner pauses already cannot be lifted by the Leader.
2. **Integrate T09–T12 into a working paper service.** Connect persisted scheduling, recovery, research, autonomous trading, learning, optimization and leadership. Keep decisions autonomous within the owner's mandate, without a per-trade approval committee. Distinguish fixture costs from actual receipts.
3. **Finish T13–T17 release acceptance.** Independently trusted artifact checks/attestations, activation/quiescence/rollback, complete dashboard controls and reporting, a production-equivalent offline full loop, then an explicitly owner-funded paper soak. A caller-supplied attestation label is not independent controller evidence.

Forward-paper economic evaluation and any live pilot come later. Broader executable Engineer/plugin support remains disabled: the multiprocessing protocol fixture is not an OS sandbox. T20 requires unfinished software prerequisites as well as separate owner eligibility, allocation and spending authorization.

## Task status interpretation

`planning/progress.json` supersedes the old all-but-T20-done claim. T00–T07 are accepted. T08–T19 and T21–T22 stay `todo`; their existing source remains intact and is not a fresh start. T20 stays `blocked`. The next-task utility selects T08. A supplied invoice difference is not a downloaded provider bill, and scripted provider HTTP is not a live pilot.
