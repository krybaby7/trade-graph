# Normal local paper operation

The 2026-10-07 owner instruction replaces the diagnostic first-cycle policy with
normal Windows/WSL paper operation using existing subscription allowance. Preserve
the existing database, owner settings, mandate, runtime records and private evidence.
The account remains USD10,000 virtual opening capital with EUR reporting. Do not
initialize or reset it as part of an ordinary start.

This runbook describes behavior, configuration and the current owner policy.
Actual provider attempts, validated results, accounting and dashboard observations belong in
[IMPLEMENTATION-STATUS.md](../IMPLEMENTATION-STATUS.md) and dated operating evidence;
configuration and synthetic tests do not establish an actual AI run.

## Market data and order hosting

Kraken's current [Advanced API FAQ](https://support.kraken.com/articles/advanced-api-faq)
describes an onboarded Spot REST/WebSocket/FIX test environment for qualified
clients. It does not establish an accessible self-service virtual-money Spot
account for this installation. Its `validate` order parameter checks an order
without returning an order identifier; this is not a hosted paper portfolio or
fill simulation. The separately described Futures demo is a derivatives service,
not a Spot paper account.

This path reads public Kraken Spot quotes, metadata and hourly candles. Every
paper order and fill is hosted by **Trade Graph's local simulator**, with local
order-state reconciliation and accounting. It sends no private Kraken request
and does not submit exchange-hosted orders. Existing owner-authorized read-only
exchange integrations retain their permissions independently; they are not a
prerequisite for the public-data paper path or a grant of trading permission.

Hourly refresh defaults to 168 completed candles per configured symbol, initially
BTC/USD and ETH/USD. Record actual timestamps, returned coverage and missing slots;
requested coverage is not proof of acquired coverage. The [Kraken market-data
API](https://docs.kraken.com/api-reference/market-data/get-ohlc-data) retains a
bounded recent OHLC window, including its uncommitted current interval. This
collector accepts up to 719 completed hours, not arbitrary historical backfill.
[Frankfurter](https://frankfurter.dev/) supplies dated reference FX observations;
its daily rates must not be presented as live exchange quotes.

## Subscription connection

Owner model selection, 2026-10-07 ([D97](DECISIONS.md)): every AI department and
retry uses only **GPT-6.1 Sol**, exact ID `gpt-6.1-sol`, through the existing
Codex subscription. The protected primary profile selects `codex_subscription`
and `gpt-6.1-sol`, with `fallback_profiles: []`; its protected Codex catalog
contains only that model. No alternate-model/provider fallback is authorized.
If the model is unavailable, affected AI work pauses while necessary paper-order
and position management continues. This is an owner configuration choice, not
a change to the generic provider-neutral contracts. Verify it on the PC without
invoking inference merely to inspect configuration.

Use the explicitly configured, native Linux Codex CLI with ChatGPT subscription
authentication in the protected Linux image. The installed native binary and model
catalog are owner-pinned; Windows login state is not proof that the deployed WSL
credential directory is authenticated. The separate Claude adapter remains
implemented, but it is not an alternate route for the current owner policy.
Consult the official
[Codex CLI reference](https://developers.openai.com/codex/cli/reference),
[Windows/WSL guidance](https://developers.openai.com/codex/windows) and
[Claude Code authentication documentation](https://code.claude.com/docs/en/authentication).

If login is required, the owner runs the supplied native interactive command in
the designated private login directory. Never paste credentials into chat, extract
authentication tokens, or copy an unrelated user's credential directory. Codex's
supported credential refresh may write only its private authentication directory;
it does not give the CLI access to the financial database, owner policy or host.

The generic runtime supports compatible existing-subscription fallback, but the
current owner selection above disables it. If the owner changes that choice, each
route must pass its actual CLI/configuration/authentication/network checks and
support the original structured business contract. Authentication failure,
exhausted allowance and unsupported configuration are technical outcomes to
report. Never switch silently to API billing, paid extras or an unverified route.

## Current owner authorization

The earlier instruction to leave the graph stopped overnight is historical. The
2026-10-07 morning instruction authorizes normal paper startup after the updated
quota controls and protected configuration are installed and verified. Resume only
this task's explicit MANAGE_ONLY hold through the existing owner control; unrelated
owner halts remain in force. This authorization does not establish that startup,
model dispatch, a decision or a fill has occurred. Record actual operation in the
status and dated evidence after observing it.

The 2026-10-08 recovery hold superseded the earlier startup authorization.
A later explicit owner instruction authorized normal paper AI after the supported
handover and admission checks below. Authenticated Owner Resume succeeded at
17:55:41 UTC; [dated operating evidence](reviews/2026-10-08-normal-paper-resume.md)
records the first scheduled result and remaining department verification. Recovery
or public-data refresh alone does not authorize clearing a later owner hold.

## Quota reserve and watched first run

The morning reserve and startup decision is recorded in [D98](DECISIONS.md).

Keep 30 percentage points of included allowance in reserve and add 10 percentage
points of practical headroom. Before **every** application dispatch, including an
application retry, read fresh official native quota metadata while holding the
exclusive provider admission slot. A fresh weekly reading is required to verify
the owner's weekly reserve. Admit only when that reading and every other reported
native quota window have **strictly more than 40% remaining**. Preserve the slot through dispatch and its durable
outcome so another local invocation cannot pass the same quota check concurrently.
A stale snapshot, failed metadata refresh or reported remaining allowance at or
below 40% blocks new AI work. Recheck the same policy before any later retry.

This is a rule for admitting new work, not a guaranteed 30% balance after a call.
Codex exposes no supported per-call percentage cap. Task complexity, context,
reasoning, caching and native internal retries affect consumption, and other
clients can consume the same account allowance. An active turn can continue after
a provider usage limit. The 10-point cushion is not a measured worst-case bound.
[Official usage guidance](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan)
and [pricing](https://learn.chatgpt.com/docs/pricing) describe these limits.

List the windows actually reported. An unavailable weekly reading blocks new AI;
missing or unavailable shorter windows are disclosed and do not independently
block admission. Never substitute zero usage or claim a floor across all windows.
Some plans have no five-hour limit. Native metadata reports current allowance, not a reservation
for the forthcoming call. See the [official app-server quota contract](https://learn.chatgpt.com/docs/app-server).

Watch the first normal run and inspect a fresh quota observation after each
attempt. Stop new AI at 40% or below, on metadata failure, or after an unexpected
single-attempt drop greater than 10 percentage points in any reported window.
Keep the dashboard, controller, reconciliation and paper-order/position management
running under MANAGE_ONLY. Retain uncertain dispatched attempts and unknown costs;
pausing does not undo their consumption or justify replay. A demand for an exact
30% floor would require withholding inference until a supported provider-side
hard cap or reservation can enforce it; this practical policy makes no such claim.

## Normal launch and dashboard

Use the installed owner launcher with its pinned image, private state mount,
protected owner bundle and provider/public-data proxies. These are the available
commands **inside that protected image**; they are not instructions to bypass the
launcher or manufacture an unprotected Docker command:

```sh
python -I -B -m trade_graph.kernel.deployment_image check-subscription
python -I -B -m trade_graph.kernel.deployment_image boot-subscription-dashboard
```

Open the owner's loopback dashboard and use its existing private owner session.
**Start Trading** starts one normal paper service in the same container, preserving
process ownership and stop/recovery semantics. **Start Optimisation** is available
on demand; configured recurring Optimisation also runs normally. State and journal
records determine the dashboard status. A running web server is not evidence that
a provider has generated or a Trader has decided.

For operation without a dashboard, the launcher may select:

```sh
python -I -B -m trade_graph.kernel.deployment_image boot-subscription
```

It runs continuously by default. `--maximum-ticks <positive integer>` is an optional
owner operating control, not a required diagnostic limit. `manage-subscription`
provides continuous model-free reconciliation and paper-order management during
provider/login/allowance outages. The older `research-subscription` command remains
an optional diagnostic; its success is not required before normal startup.

## Configurable work and limits

The protected `paper-config.json` supports these ordinary settings; change only
intended fields and preserve unrelated owner settings:

```json
{
  "public_data_enabled": true,
  "automatic_schedule": true,
  "optimisation_manual_only": false,
  "public_history_enabled": true,
  "public_history_hours": 168,
  "public_history_interval_seconds": 3600,
  "optimisation_deadline_seconds": null
}
```

Default cadence is Trader every four hours, Research daily, Learning twice weekly,
Leader weekly and Optimisation weekly. `schedule_intervals` and activated permitted
artifacts may configure supported role intervals. New schedules are immediately
due; persisted schedules retain their due times. Active work is coalesced, not
multiplied each tick. Existing mandate authority is reused; a forced Leader
initialization or per-trade committee is unnecessary. A valid hold/no-trade is a
normal outcome, and a model/provider failure is never relabeled as a hold.

The subscription profile defaults to 600 seconds, a requested 16,384 output
tokens, eight turns and 2 MiB captured output per dispatch. Application dispatch
attempts default to three. Native Codex 0.160.1 enforces the process wall-clock
and captured-byte boundaries through the executor; its CLI has no supported
output-token or turn cap. Turn validation occurs after completion. Its documented
built-in defaults allow four request retries and five stream retries; the generic
Claude retry settings do not override those Codex defaults. The Claude adapter
has supported controls and defaults to two transport retries and three
structured-output attempts. Report these distinctions rather than claiming every
profile field limits native Codex usage. Configuration does not impose a universal
one-generation or Research-only policy. Department tools use the supported,
owner-approved interface; provider-side research does not grant host shell,
credential-file, private network or protected database access. The current installed
subscription profile uses `department_tools: {}`: hosted search has no supported
end-to-end result contract in this path yet. Research can use the supplied market
and history evidence. This is a missing tool capability to report, not an owner
policy prohibition on supported departmental tools.

Only known eligible failures may retry, always on gpt-6.1-sol under the current
owner policy. The protected profile must leave `fallback_profiles` empty.
A dispatched attempt with an uncertain outcome is retained for reconciliation
and is not blindly replayed. Record application dispatches in the durable
subscription attempt journal. CLI-internal provider attempts are a separate count;
report them only when exposed by actual provider evidence, otherwise mark unknown.

## Accounting, pauses and genuine blockers

Subscription capacity is separate from an API monetary allowance. A subscription
task's zero API allocation does not prove free service, zero subscription expense,
or known token usage. Preserve known per-attempt usage, unknown fields and unknown
costs; do not invent values or seed a budget to unlock work. The virtual portfolio
cannot replenish operating allowance. Shared expenses are allocated once.

Structured contracts, exact task/lease provenance, current owner authority,
Decimal/native-unit financial controls, persistent order intents/attempts and
reconciliation remain required. Protected process/filesystem/network controls
keep model workers away from credentials and financial authority. These existing
project controls remain distinct from obsolete diagnostic acceptance gates.

Historical first-cycle resume authorization does not clear a later recovery pause
or another owner halt. Clear only the specifically authorized current pause through
the authenticated owner control and its current revision after reconciliation and
readiness checks. There is no mandatory pause after an ordinary first cycle. When
AI is unavailable, disclose the blocker and retain deterministic paper-order and
position management.

For each remaining blocker, identify its source: obsolete earlier prompt policy,
existing project design, provider requirement or missing functionality. Remove
obsolete testing policy; repair actual login, configuration, networking and
implementation failures. Run checks appropriate to changed code. An unrelated
full test suite and live-account commissioning are not prerequisites for each
paper run. Real-money trading, separately billed APIs, paid extras and purchases
remain disabled throughout this operating path.


## Accepted partial historical reporting after storage recovery

A separately owner-approved [partial-history recovery](PARTIAL-HISTORY-RECOVERY.md)
can retain the same paper account while explicitly recording missing historical
price/reporting observations and a new evaluation-period boundary. Original run,
financial/AI identities, failed outcomes, uncertain costs, real operating budget
and original witness commitments remain retained. Old/inception reporting must
show the accepted gap; the new period needs fresh public valuation inputs and
complete later observations. Neither the period label nor recovery resumes AI.
For this recovered dashboard installation, a later authorized resume has this order:

1. Keep the dashboard running. Resolve the active management run's PID, verify its
   `/proc` birth identity and exact `manage-subscription` command, and send SIGTERM
   only to that subprocess inside the existing dashboard container.
2. Wait for graceful exit, both `paper-service` and `role-worker` leases to clear,
   its graph-service run to become terminal, and exclusive inode ownership to be
   released. Do not delete leases or force-kill to shortcut this gate.
3. Use authenticated **Start Trading** (`POST /api/v1/owner/start-trading`, fresh
   `request_id`, `mode: paper`) to launch the NORMAL service while the owner pause
   remains in force. Require a newly started worker rather than attachment to the
   management process, one live run/PID and one owner of the two service leases.
4. Verify protected isolation/readiness, reconciliation, fresh public data and
   Sol-only configuration with empty fallback. Quota metadata must be at most
   30 seconds old, include weekly allowance, and show strictly more than 40%
   remaining in every reported supported window, ordinary allowance available
   and no spendable credits. Preserve the existing guard.
5. Read the current owner revision, then use separate authenticated **Owner Resume**
   (`POST /api/v1/owner/resume`, fresh `request_id`, current `expected_revision`).
   A failed reconciliation or readiness barrier retains the pause.

Do not Resume while the management subprocess is alive. Start Trading alone
attaches to that worker, and the management worker would reinstate MANAGE_ONLY.
Real trading, separately billed APIs and paid extras remain disabled.
