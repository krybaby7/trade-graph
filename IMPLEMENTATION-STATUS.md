# Implementation status — public market data

Updated: 2026-09-30. **Partial offline prototype; the autonomous R1 service is not complete.** Paid model calls and live trading remain disabled. T00–T11 are accepted. The next dependency-ready work is Leader and Secretary routing (T12), not a live pilot.

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

Pause advance now applies the persisted profile. `PAUSE_DECISIONS` and `MANAGE_ONLY` leave resting orders in place. `NO_NEW_EXPOSURE` cancels buys and keeps a protective sell. `CANCEL_ALL` cancels every outstanding order, including protection, and does not call the portfolio flat. `FLATTEN` cancels those orders, submits one reduce-only exit, and stays `flattening` until that fill is recorded and inventory is gone. `STOPPED` stays blocked while inventory or an outstanding order remains. An unknown cancel is not recorded as cleared. A lost acknowledgement can be cancelled from broker state without a second submit. Paper fill history now ends on an empty page so reconciliation can finish after a fill. No live venue outage was run.

One role worker holds the `role-worker` process lease. A second owner does not claim work while that lease is live. Reclaiming an expired task runs the supplied reconciliation before the role handler. A task whose expected version does not match the worker is dead-lettered with no attempt and no snapshot. A matching task writes a context snapshot, then counts one attempt. Handlers are injected. This worker does not call a model provider.

Research ingest accepts only public HTTPS, strips scripts, and stores publication, retrieval, availability, expiry and a source hash. A later fetch inserts a new finding and leaves the old document unchanged. Stale and untrusted pages stay out of the fresh context. The Trader can enter or record a hold from a validated payload without a Leader approval, including an explicit experiment. A low confidence value does not block a valid enter. A model timeout is stored as a model failure and does not add a hold. The two strategy templates are unproven. No search provider was called.

Learning appends lesson revisions and refuses a revision that drops a prior counterexample. The stored grade follows the process assessment. A no-trade mark uses only quotes whose event and availability times are at or before the decision. A bar that becomes available later is not an entry price. Optimisation can record a proposal and cannot turn reconciliation off. This is not a forward-paper profitability result.

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
| Local Python 3.12 `uv run pytest` after T08 | **142 passed; 0 failed, 0 skipped** |
| Local Python 3.12 `uv run pytest` after T09 | **144 passed; 0 failed, 0 skipped** |
| Local Python 3.12 `uv run pytest` after T10 | **147 passed; 0 failed, 0 skipped** |
| Local Python 3.12 `uv run pytest` after T11 | **150 passed; 0 failed, 0 skipped** |
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

1. **Integrate T12 into a working paper service.** Research, Trader, Learning and Optimisation records now exist. Leader and Secretary routing, mandates and schedule allocation are still not implemented. Keep decisions autonomous within the owner's mandate, without a per-trade approval committee.
2. **Finish T13–T17 release acceptance.** Independently trusted artifact checks/attestations, activation/quiescence/rollback, complete dashboard controls and reporting, a production-equivalent offline full loop, then an explicitly owner-funded paper soak. A caller-supplied attestation label is not independent controller evidence.

Forward-paper economic evaluation and any live pilot come later. Broader executable Engineer/plugin support remains disabled: the multiprocessing protocol fixture is not an OS sandbox. T20 requires unfinished software prerequisites as well as separate owner eligibility, allocation and spending authorization.

## Task status interpretation

`planning/progress.json` supersedes the old all-but-T20-done claim. T00–T11 are accepted. T12–T19 and T21–T22 stay `todo`; their existing source remains intact and is not a fresh start. T20 stays `blocked`. The next-task utility selects T12. Lesson grades are not a forward-paper study, research fixtures are not a credentialed search, and scripted provider HTTP is not a live pilot.
