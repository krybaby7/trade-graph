# Paper review schedule: 7–8 October 2026

Owner request: manual reviews beginning at **12:30 p.m. local time on 7 October**.
No timezone offset is inferred from residence or bank details. These slots are
observation times, not changes to the graph's schedules. No automatic notification,
background review job or extra departmental cycle is created by this document.

| Local time | Purpose |
| --- | --- |
| 7 October, 12:30 | Establish current operating baseline. |
| 7 October, 16:30 | First four-hour comparison; inspect actual scheduled work. |
| 7 October, 20:30 | Second interval; inspect stability, decisions and consumption. |
| Before bed, for example 22:30 | Optional overnight readiness review. |
| 8 October, 12:30 | Compare the 24-hour review window and total actual run history. |

The graph's Trader cadence is normally four hours, but persisted due times determine
when it actually works. Report the last/next due times; do not force a decision to
align with a review slot. The next-day comparison is 24 hours from the baseline,
not necessarily 24 hours from the original service start.

## Context for fresh desktop chats

- WSL project: `/home/adami/trade-graph`; PC dashboard: `http://127.0.0.1:8000`.
- Read `IMPLEMENTATION-STATUS.md`, `docs/NORMAL-PAPER-OPERATION.md` and the relevant
  latest operating review. Last published operating checkpoint at preparation of
  this schedule: `72ccff3`; newer source or runtime evidence must be identified.
- Discover the **actual active database and image** through the existing owner
  launcher/service metadata. Do not assume the original checkout database or
  newest repository source is the running installation.
- Only `gpt-6.1-sol` is authorized for all departments/retries; fallback is empty.
  USD10,000 is virtual opening capital. Kraken supplies BTC/USD and ETH/USD public
  data; orders/fills are local paper simulations. Real trading/API billing/extras
  remain disabled.
- Current quota admission requires fresh weekly data and strictly more than 40%
  remaining in every reported supported window: 30% reserve plus 10 points of
  headroom. Unreported shorter windows, failed-call/internal-retry usage and
  attributable monetary costs remain unknown. There is no exact all-window floor.
- Initial Research, Trader and Learning succeeded; Trader returned `no_action`.
  Leader failed three attempts and Optimisation failed citation validation.
  Tested corrections were undeployed because financial manifest continuity was
  missing. Check for genuinely newer deployed evidence; do not infer deployment
  from source changes or repeat failed work manually.
- Reviews inspect runtime evidence without starting tasks, tests, retries, orders,
  deployments or service/configuration changes. Supported quota/status metadata
  checks are allowed; they are not diagnostic inference. Keep sensitive outputs
  private and summaries concise. The desktop review chat itself consumes shared
  Codex capacity, which must not be attributed automatically to graph work.
- Save redacted private review summaries, UTC observation times and the confirmed
  owner timezone under `/home/adami/.local/share/trade-graph-reviews/2026-10-07/`,
  with private permissions and outside Git. Do not retain credentials/raw auth
  output or change financial records. If timezone is unknown, label UTC explicitly.
  Missing earlier snapshots make deltas unavailable; never fabricate them.

## 12:30 baseline prompt

```text
Review my running Trade Graph paper test at the 7 October 2026, 12:30 local slot.
Project: /home/adami/trade-graph. Dashboard: http://127.0.0.1:8000.
Read IMPLEMENTATION-STATUS.md, docs/NORMAL-PAPER-OPERATION.md and the latest operating evidence. Discover the actual active database/image from the owner launcher; repository source may be newer than deployed code.
Inspect runtime only: no extra AI jobs, tests, retries, restarts, deployments, orders or configuration changes. Only gpt-6.1-sol is authorized.
Report service/heartbeat, data freshness, last/next departmental due times, actual successes/failures, attempt and decision counts, paper orders/fills, balances/accounting and current official quota with its timestamp. Verify the existing above-40% admission guard; disclose unavailable windows and costs.
Save a redacted private baseline under /home/adami/.local/share/trade-graph-reviews/2026-10-07/ with UTC time and confirmed local timezone. Preserve all runtime records.
Return a concise status, issues and next action. Treat valid no-trade separately from failure; no fills means fill behavior remains untested.
```

## 16:30 interval prompt

```text
Review my running Trade Graph paper test at the 7 October 2026, 16:30 local slot.
Project: /home/adami/trade-graph. Dashboard: http://127.0.0.1:8000.
Read IMPLEMENTATION-STATUS.md, docs/NORMAL-PAPER-OPERATION.md and private review summaries under /home/adami/.local/share/trade-graph-reviews/2026-10-07/. Verify the actual active database/image; do not assume source fixes are deployed.
Inspect runtime only: no extra AI jobs, tests, retries, restarts, deployments, orders or configuration changes. Only gpt-6.1-sol is authorized.
Compare with the 12:30 baseline: new role results/attempts, Trader decisions, paper orders/fills, accounting, data freshness and quota observations. Report actual last/next due times; do not force a Trader cycle. Separate shared-account quota movement from reported graph usage. Check the existing above-40% admission policy and disclose unknown windows/costs.
Save a redacted private timestamped summary. If baseline evidence is missing, state which comparisons are unavailable.
Return changes, failures, current health and the recommended next action, without applying changes.
```

## 20:30 interval prompt

```text
Review my running Trade Graph paper test at the 7 October 2026, 20:30 local slot.
Project: /home/adami/trade-graph. Dashboard: http://127.0.0.1:8000.
Read IMPLEMENTATION-STATUS.md, docs/NORMAL-PAPER-OPERATION.md and private reviews under /home/adami/.local/share/trade-graph-reviews/2026-10-07/. Verify the actual active database/image separately from repository source.
Inspect runtime only: no extra AI jobs, tests, retries, restarts, deployments, orders or configuration changes. Only gpt-6.1-sol is authorized.
Compare with 16:30 and the baseline: scheduled work, decisions, paper fills/positions, balanced accounting, quote/history freshness, errors and repeated retry patterns. State actual last/next due times. Report fresh supported quota readings and the existing above-40% guard, with unknown windows and unattributed consumption clearly separated.
Save a redacted private timestamped summary. Recommend continue, investigate or pause for my decision; do not alter controls. Explain whether the Leader/Optimisation failures changed using actual deployed/result evidence.
```

## Optional bedtime prompt

```text
Perform a bedtime readiness review of my running Trade Graph paper test on 7 October 2026.
Project: /home/adami/trade-graph. Dashboard: http://127.0.0.1:8000.
Read IMPLEMENTATION-STATUS.md, docs/NORMAL-PAPER-OPERATION.md and private reviews under /home/adami/.local/share/trade-graph-reviews/2026-10-07/. Verify the actual active database/image.
Inspect runtime only: do not start extra AI jobs/tests/retries, change schedules/configuration, deploy, restart, pause/resume, stop or place orders. Only gpt-6.1-sol is authorized.
Check service heartbeat, fresh data, next overnight tasks, current quota, the above-40% admission guard, recent failures/retries and open paper positions/order management. Explain which quota windows are unavailable and whether the overnight reserve can actually be relied upon. Report the PC sleep settings without changing them.
Recommend leave running or pause new AI, with reasons and the exact existing owner control I would use. Flag unknown monitoring/quota coverage honestly.
Save a redacted private timestamped summary. Await my decision rather than changing the run.
```

## Next-day 12:30 prompt

```text
Review my Trade Graph paper test at the 8 October 2026, 12:30 local slot.
Project: /home/adami/trade-graph. Dashboard: http://127.0.0.1:8000.
Read IMPLEMENTATION-STATUS.md, docs/NORMAL-PAPER-OPERATION.md and private reviews under /home/adami/.local/share/trade-graph-reviews/2026-10-07/. Verify the actual active database/image and original service start; separate the 24-hour review window from total run duration.
Inspect runtime only: no extra AI jobs/tests/retries, deployments, restarts, orders or configuration/control changes. Only gpt-6.1-sol is authorized.
Summarize actual role successes/failures, attempts, Trader decisions, paper orders/fills, exposure, accounting, downtime/data gaps and quota movement. Compare with the 12:30 baseline; preserve unknown costs/usage, and do not equate shared allowance movement with graph usage. State current guard status and unavailable windows.
Separate gross paper results from fees and unresolved all-in costs. No trades can be valid; no fills leaves fill behavior untested. One day does not establish profitability.
Save a redacted private report and recommend the next test or repair, without applying changes.
```
