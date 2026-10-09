# Live owner dashboard

Open <http://localhost:8000/>. Unauthenticated HTML navigation opens sign-in;
protected API requests still return JSON 401. Use the current private owner
session. On Windows, `scripts/copy_dashboard_session.ps1` copies that session to
the local clipboard without displaying it. If Windows treats the checkout as remote,
run `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/copy_dashboard_session.ps1`;
this applies only to that process. Paste into sign-in. It changes no
session, worker or account state.

The home separates operational health from economic evidence. A current worker
heartbeat and RUNNING control do not prove successful application by every role
or profitable trading. Department cards distinguish native generation from
applied task outcomes and show due times, failures, retries and linked records.
Reported token subtotals count native attempts once; missing fields stay unknown.
Shared account quota is an independently timestamped retained observation, not
graph token usage or a promise about the next invocation.

Money is calculated on the server with Decimal. The economic view uses account
inception through its displayed cutoff in one reporting currency. Native paper
capital, exposure, trading fees, FX basis and benchmark remain available under
the account-details disclosure. EUR result can include USD cash FX movement;
the cash benchmark and strategy alpha separate that movement. Spread, slippage
and fill fees already affect trading result and are not deducted twice.

Enter actual subscription/other bills under Expense evidence. Retain an opaque
private evidence reference, whole native bill, service period, graph fraction,
explicit allocation policy and department weights summing exactly to one.
The graph share is prorated by the exact overlap with the reporting period and
converted using recorded incurred-at FX. The full bill remains visible beside
that share. Department amounts are owner allocations, not per-call charges.
Duplicate bill/evidence identities and overlapping bills for one billing scope
are rejected. Owner evidence does not enlarge an API allowance or consume paper
cash. Missing/stale FX and unresolved receipts stay provisional.

After entering every attributable bill for a period, an owner may record a
scoped completeness declaration. A later overlapping bill requires a new
declaration. Use a completed reporting cutoff to compare a closed interval;
a declaration ending yesterday cannot certify today's expenses. No declaration
means unknown, including when no bill has yet been entered. With multiple
potentially overlapping accounts, a deployment graph allocation does not invent
individual portfolio weights; selected-account totals remain unknown.

Download review snapshot exports complete retained safe records for the selected
account/deployment plus shared public inputs. Database projections and records
use one SQLite transaction; external resource and quota observations retain
their own timestamps. Export includes all retained attempts, usage/cost coverage,
financial results, schedules, failures and recovery limitations. Credentials,
sessions, raw requests/responses/conversations and protected capabilities are
excluded. An oversized export fails explicitly; it does not truncate records.
Keep downloaded reviews private. Opening, refreshing and exporting initiate no
inference or quota CLI probes.

Home refreshes every 30 seconds while visible and idle, preserves open details,
and shows stale/offline status on failure. The last snapshot remains readable.
Resource observations are cached for 30 seconds. Controller start/resume and
native dispatch/retry retain their independent fresh quota admission checks.
Owner forms retain a request identity across uncertain network retries.

Recovery gaps remain explicit. Account inception retains earlier records;
the recovery's new evaluation boundary resets neither capital nor expenses.
Economic evidence, operational success and live authorization remain separate.
