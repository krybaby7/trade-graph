# Normal paper resume after recovery — 2026-10-08

## Actual outcome

The owner separately authorized normal paper AI on the recovered installation.
The operator completed the [documented handover](../NORMAL-PAPER-OPERATION.md)
through the existing authenticated controls. Owner Resume returned HTTP 200 and
RUNNING after reconciliation at 17:55:41 UTC. Normal schedules remain enabled,
with no finite-tick or one-cycle limit.

The first existing scheduled Trader task was created at 17:55:45 UTC, used a
17:55:46 UTC point-in-time snapshot, completed inference at 17:56:02 UTC, and
persisted its decision at 17:56:04 UTC. One completed application attempt yielded a validated, persisted `no_action` decision
and report. This verifies the repaired Trader input path in actual normal paper
operation. No order, fill or new runtime task error was observed in this first
post-resume result. This is functional evidence, not evidence of profitability,
a new acceptance gate or live authorization.

Executable source remains `780dce1`; immutable image remains
`sha256:4d0ee615aa0e15d535aa2025422404891ab6acc9fdef6f9ea90d6f99ec9ed1e7`,
using Python 3.12.15 and SQLite 3.53.4. No source, image, policy or schedule
replacement was needed for resume.

## Graceful ownership handover and preserved state

The operator verified the management subprocess's PID birth identity and exact
`manage-subscription` command before sending SIGTERM only to that subprocess.
Its run became STOPPED; both service leases disappeared and the exclusive inode
lock was released. The existing 15 subscription attempts and 13 invocations
remained unchanged through the handover.

Authenticated Start Trading at 17:53:09 UTC launched a new NORMAL CLI worker,
rather than attaching to the management worker. The owner remained in MANAGE_ONLY
while the continuous worker initialized and readiness was checked. No lease was
manually deleted and no finite tick count was introduced.

Before resume, the 17:54:44 UTC financial verification passed full SQLite
integrity, foreign-key, protected-prefix and retained terminal-AI checks. The
database inode remained the same. The owner-approved recovery's eleven known
historical public/reporting gaps, unknown exact observation times, possible
additional reporting loss and new evaluation-period boundary remain recorded.
Recovery did not certify complete historical reporting or reset the account,
prior failures, expenses or witness commitments.

Only after the worker and readiness checks passed did authenticated Owner Resume
use the current owner revision. Reconciliation completed and the current revision
was accepted. Exact private identities, owner controls and evidence remain
outside Git.

## Actual model, quota and input checks

Protected configuration still selects only `gpt-6.1-sol` with
`fallback_profiles: []`. An isolated official native app-server model-list
check included that exact model. It was a metadata check, not another inference.
The actual attempt is attributed to the configured pin; the provider did not
supply an independent model echo.

Fresh official quota metadata at 17:55:41 UTC admitted the configured route:
the reported weekly allowance exceeded 40%, ordinary included allowance was
available and spendable credits were zero. The shorter/secondary quota window
was unavailable and remains disclosed. Every dispatch and retry retains the
existing requirement for metadata no more than 30 seconds old and strictly more
than 40% remaining in every reported supported window: 30 percentage points of
reserve plus 10 points of headroom. Native internal retries, shared-account
consumption and unreported windows prevent an exact post-call floor guarantee.

Public BTC/USD and ETH/USD quotes were fresh. Both symbols had completed hourly
history through 17:00 UTC and all ten required features ready. Reporting FX was
dated October 8. The Trader received the active slow-trend-pullback artifact,
ready SMA20/SMA50 and pullback features, and the repaired separation of native
financial state from reporting currency, staleness and provisional valuation.

Both symbols' SMA20 values were below SMA50. The Trader chose `no_action` using
the supplied trend and pullback evidence; the validated decision and report were
persisted. No fresh Research findings were supplied for this run, and retained
lesson context was present. The decision is an actual model result, rather than
an inference drawn from zero orders.

The completed attempt reported 14,626 input tokens and 285 output tokens.
Uncached/cache-write breakdowns were not reported. Native internal retry counts,
unreported usage and attributable shared subscription costs remain unknown.
Recorded API expenses do not establish free subscription work.

## Post-task verification

Post-task SQLite integrity, foreign keys and protected financial prefixes passed.
Every original subscription attempt and invocation matched the prestart snapshot
by every field. Protected owner files, session and database identity remained
unchanged. A consistent private checkpoint passed integrity and foreign-key
checks with a stable witness and private file permissions.

Independent read-only verification through 18:01:30 UTC found exactly one NORMAL
worker with matching PID birth identity, one owner of both service leases,
exclusive inode ownership, a current heartbeat and owner RUNNING. Fresh official
post-task quota remained above the admission threshold; no new wrong-model or
duplicate task/attempt was found. Authenticated dashboard pages returned HTTP 200,
Windows loopback login returned HTTP 200, and the overview rendered the explicit
historical-observation gap banner and retained both evaluation periods. There was
no account reset. Financial reporting remains provisional because historical
reporting and unknown cost limitations remain visible; native accounting checks
passed. No current blocking runtime error was found.

A later recovery-panel read briefly returned verification unavailable because
another protected controller held the financial-witness lock. A direct retry at
18:06:22 UTC verified the same accepted incident; full integrity and foreign keys
still passed. Final observation at 18:07:12 UTC confirmed the gap banner, one
NORMAL worker, fresh heartbeat, owner RUNNING, current completed 18:00 UTC
features and 80% reported weekly allowance. No new runtime failure event, order
or fill was recorded. The intermittent recovery-panel warning is a display
limitation; financial history and controls were not changed to hide it.

## Department evidence and remaining verification

The retained history and new Trader result distinguish completed inference from
successful validated application:

| Department | Actual retained applied evidence | Remaining limit |
| --- | --- | --- |
| Trader | Nine successful tasks and persisted `no_action` decisions/reports, including the first repaired-image run above. | No order or fill path was exercised by this no-trade result. |
| Research | One historical success with two persisted attributed findings and outcome/journal reports. A later completed inference failed source validation. | The deployed source-reference guidance still needs a fresh successful publication. |
| Learning | One historical success with a persisted tentative lesson and outcome/journal reports. | No fresh post-recovery Learning run has completed. |
| Leader | One failed invocation across three failed native attempts; no applied Leader decision. | Safer diagnostics are deployed, but the original cause remains unknown and actual success is unverified. |
| Optimisation | Historical inference completed, but citation validation rejected its applied result; no proposal was applied. | Explicit eligible-citation guidance is deployed; successful application remains unverified. |
| Engineer | No actual task or engineering attempt in this installation. | Actual installation execution remains unverified. |

The historical Optimisation failure cited evidence outside its eligible persisted
context; the later Research failure cited a source outside its persisted snapshot.
Strict validation remains in place. The original Leader CLI details were not
retained, so a suspected cause is not presented as a confirmed diagnosis.

After the new Trader task, retained totals are 16 subscription attempts and 14
invocations. All prior failed outcomes and unknown costs remain. No historical
task was cleared, replayed or relabeled to demonstrate a repair. Existing
schedules determine subsequent work; this resume did not force department
retries or require a per-trade committee.

## Operating boundaries

The owner-authorized operation remains local paper simulation using public
market data and the existing subscription route. Live trading, separately billed
APIs, paid extras, alternate model/provider fallback and purchases remain
disabled. The recovery's reporting limitations and separate economic/live
acceptance work remain unchanged.

If quota, metadata or model readiness blocks new AI, retain MANAGE_ONLY
reconciliation and paper-order/position management, the dashboard and uncertain
attempt records. Pausing does not erase fills or justify replay. Detailed runtime
and owner evidence remains private; no credentials, private financial records
or raw conversations are included here.
