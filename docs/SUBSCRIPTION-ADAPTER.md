# Subscription inference adapter — 2026-10-06

The subscription implementation is fail closed. It is tested with synthetic CLI
results and actual native WSL filesystem isolation; no subscription inference or
continuous AI operation has been commissioned. There is no API-key route,
automatic provider fallback, application retry or output repair call.

## Current operator evidence and blocker

Earlier Windows Codex CLI 0.125.0 evidence verified ChatGPT login. That official
app-server check used `initialize`, `account/read` with `refreshToken:false`, and
`account/rateLimits/read` only. The selected CLI account returned an official
weekly allowance observation; its shorter window was unavailable. Exact account readings and reset details are
private evidence. This shared-account capacity is not usage attribution to
Research. Authentication tokens, account identifiers and raw responses were neither extracted nor committed.

The final version/authentication recheck still finds Windows Codex 0.125.0, but
`codex login status` fails parsing the current `ultra` reasoning configuration;
this version accepts only through `xhigh`. A command-line `high` override also
fails before status is available. Existing configuration/login files were left
unchanged. Current CLI login is therefore unverified, distinct from the earlier
successful check. Native WSL discovery finds only Windows interop Codex.

A later read-only signed-in Codex app metadata check reported available weekly
allowance, with the shorter window unavailable. Exact account readings, balance,
reset and observation details remain private. This shared-account capacity does
not establish a CLI route, Research usage or subscription fee attribution.

The official npm registry reports Codex 0.160.1. Current official documentation
still prevents overriding built-in provider IDs. Retry settings apply to custom
providers, and the built-in provider retains request/stream defaults of 4/5 in
current source. Therefore an update does not establish the required zero-retry
subscription route. The adapter always rejects Codex dispatch, including a
caller-supplied optimistic readiness record. No update, token-based custom route
or undocumented endpoint was used. See [advanced Codex configuration](https://learn.chatgpt.com/docs/config-file/config-advanced),
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference),
and [provider source](https://github.com/openai/codex/blob/main/codex-rs/model-provider-info/src/lib.rs).

Windows Claude Code 2.1.280 remains signed out; neither provider has a native WSL
CLI installation. Launching a Windows executable from a Linux namespace would
escape the demonstrated boundary and is refused. Claude's current [billing
clarification](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
still permits `claude -p` to draw subscription limits. Native official login,
available subscription capacity and disabled extra usage/credits/purchases must
be verified by the owner before any invocation. No Claude subscription inference
has occurred.

## Implemented contracts

`SubscriptionAdapter.invoke(request, invocation_id=..., journal=..., cancel_event=...)`
accepts a protected `SubscriptionConfig`, a protected readiness record and an
executor. Roles receive only their explicit context-key allowlist. Requests with
private paths, credential/policy/database fields, provider/model mismatches,
external schema references, tools or excess input/time/token bounds are refused
before dispatch. Domain JSON Schema validation is local and never invokes a
repair model. Actual model identity must match the configured model; extra turns
and another model cause failure while validated usage facts are retained.
Model aliases and unreviewed model IDs are refused locally. The CLI receives an
exact-model allowlist and an empty fallback chain through supported settings.

Migration 0018 supplies `subscription_invocations` and
`subscription_provider_state`. The controller journals each attempt before an
external effect. Reusing an invocation ID returns the durable result; a crash,
deadline, cancellation or oversized output becomes uncertain and cannot replay
the call. Quota exhaustion pauses new AI work in durable provider state.
Deterministic financial reconciliation and protection do not use this adapter
and remain the responsibility of the independent service management path.

Subscription receipts carry requested/actual model, task/root/role/version,
outcome, quota snapshot and validated token counters. CLI cost estimates are not
actual expenses. Actual subscription cost and shared fee allocation remain
unknown; `actual_cost_native` stays null. An undispatched refusal records known
absence of an inference charge only. It does not say that subscription fees or
controller/preparation work are free. Neither the API expense ledger nor virtual
paper capital is modified by these receipts.

`native_subscription_status("codex" | "claude")` provides sanitized read-only
native CLI metadata without selecting a model, opening a thread or invoking
inference. `probe_subscription(config)` supports a protected selected-model
configuration. Unknown quota fields remain unknown. Metadata fields are bounded
and account identifiers, tokens and arbitrary provider messages are excluded.
Production commissioning uses `probe_isolated_claude` to check the exact pinned
binary and credential mount that the executor will use. A default host-home
login check cannot establish a different mounted login. Native binary parent
directories must also be root-owned and non-writable. Independent protected host
isolation evidence remains required; metadata success alone does not supply it.

## Native Claude executor and isolation

`LinuxSubscriptionExecutor` requires an owner-pinned, root-owned, non-writable
native ELF binary and the single private `.credentials.json` produced by an
official native Claude login. Application code never reads that file. Its
read-only mount is available to the trusted CLI; all model tools are disabled.
The login file is not copied into source, the database or an application config.
Windows mounts and interop executables cannot be used for this path.

Bubblewrap creates separate mount/PID/IPC/user namespaces, drops capabilities,
clears inherited environment and file descriptors, supplies a fresh home and
mounts only the pinned binary, system libraries, certificates and that official
login file. Host home directories, Windows mounts, database, Kraken files,
owner policy and protected repository source are absent. The provider CLI needs
network access; no model tools or MCP servers are exposed. Wall-clock, output and
token bounds apply; cancellation kills the namespace parent. Raw CLI stderr and
transcripts are discarded, while a sanitized quota category survives errors.

The prepared invocation uses current [official Claude CLI controls](https://code.claude.com/docs/en/cli-reference)
for one turn, structured output, restricted/safe operation, no tools/MCP and no
session persistence. [Official environment controls](https://code.claude.com/docs/en/env-vars)
disable request retries, structured retries beyond the first attempt,
nonstream timeout retries, streaming/refusal fallback, background work, automatic
memory/title calls, fast mode and nonessential traffic. Claude Code 2.1.285 or
later is required for the documented nonstream timeout control. Their complete
credentialed behaviour has not been verified on this installation. A protected
native installation/login, reviewed extra-usage settings and a bounded diagnostic
remain prerequisites; a Boolean readiness flag is not live authorization.

## Verification

Run from the root of an isolated WSL checkout with its locked dependencies and
reviewed virtual environment activated (`VIRTUAL_ENV` set):

```bash
PYTHONPATH=src "$VIRTUAL_ENV/bin/python" -m pytest \
  tests/unit/test_subscription_adapter.py \
  tests/security/test_subscription_process_boundary.py \
  tests/integration/test_startup_migration.py
```

The native security tests actually execute Bubblewrap on WSL2 and deny reads of
a synthetic private database, the host login path, Windows login path, host
process environment and protected source. They also exercise output flooding,
deadline/descendant termination, cancellation, changed pins and interop refusal.
On another platform those native tests explicitly skip and confer no host proof.
Synthetic result tests establish lifecycle/output/quota semantics only.

Real Research inference attempts: **0**. Actual Research model/result: **none**.
Separately billed model APIs, purchases, private Kraken requests, real orders and
withdrawals: **0**. Public history was not refreshed by this adapter work.


## Protected departmental integration

`assemble_subscription_handlers(office, secretary, engineer, artifact_runtime,
config, protected_runtime=..., workspace_root=..., adapter=...)` supplies all six
real roles through the same confined mutable graph request/application protocol.
`SubscriptionRuntimeConfig` fixes exactly one provider and exact model for the
run. Departmental profiles include all six role allowlists; a partial Research
profile cannot claim every department is ready. Each role receives only its
immutable role-specific context allowlist;
owner policy, budget controls, credentials, provider routes and database paths
stay in the trusted parent. Mutable routing artifacts cannot change the selected
subscription route. Output effects still pass existing authority, generation,
lease, schema and Engineer acceptance checks.

`ProtectedSubscriptionGateway` uses `subscription_invocations` exclusively.
It does not manufacture an API price card, reserve an API expense, deduct zero
cost or change virtual paper equity. Existing real-budget allocations remain
protected context/permission bounds; the shared subscription fee and actual cost
remain unresolved. The controller persists one invocation per task atomically,
including blocked, failed and uncertain work. New invocation IDs cannot repair
or retry the same task. Engineer schema, transport or acceptance failures end
that task after its first invocation. Cancellation reaches the bounded native
process. Recovery applies a retained valid result through the confined completion
stage, or waits for reconciliation without another model call.

`load_subscription_profile(protected_owner)` reads only root-distributed
`subscription-profile.json` and independently reviewed, hash-pinned
`subscription-isolation.json`. It returns `SubscriptionAdmission` with `config`,
`adapter`, `status` and `profile_sha256`. Missing/refused admission has no adapter
and lets the caller retain deterministic management. Its sanitized status records
`selected_provider`, readiness, exact admission blockers and zero inference
attempts. The caller must retain the profile pin and recheck it before work.

The profile binds the runtime configuration, native ELF path/hash, the path of
the official private `.credentials.json` login file, a strict Boolean owner review
that extra usage/credit spending is disabled, and the independent isolation proof
hash. The proof binds the installed protected package and native CLI hash with
actual host checks for private-file/Windows-mount/host-process/environment denial,
descendant termination and disabled model tools. Application code does not open
or copy authentication-file contents. Version/login status are checked without
inference inside the exact executor mounts. Codex profiles always remain blocked
while supported configuration cannot disable built-in retries.

This route also requires the established protected image boot environment.
Owner files or a native WSL Bubblewrap test do **not** commission the protected
Docker image. Its capabilities, seccomp policy and user-namespace configuration
must admit the pinned CLI and Bubblewrap boundary while preserving confinement.
An actual intended-image native metadata/isolation probe and bounded credentialed
Research diagnostic are still required. No such image/subscription commissioning
or real inference was performed here. Existing Engineer acceptance/deployment
controls remain authoritative; subscription access does not grant live authority.

Additional credential-free verification:

```bash
PYTHONPATH=src "$VIRTUAL_ENV/bin/python" -m pytest \
  tests/integration/test_subscription_departments.py \
  tests/unit/test_subscription_profile.py
```

Synthetic CLI results exercise all six real role handlers and confined graph
stages, durable result recovery, cancellation, quota pauses, unknown receipts,
new-ID replay refusal and one-attempt Engineer behavior. The profile tests prove
missing/unprotected/changed/boot-refused profiles fail closed and never invoke
inference. They do not confer credentialed native CLI or live verification.

Research, Learning and Optimisation journal envelopes retain the persisted
portfolio mode. Live Optimisation may analyze and record proposals; commissioned
Engineer changes remain paper-scoped under existing acceptance checks and require
separate owner-pinned promotion to a live deployment. No live artifact authority
was widened by the subscription composition.

## Startup binding and remaining network gate

Protected paper and live service bindings now consume this profile directly and
compose the six subscription handlers after exclusive financial ownership and
protected graph admission. An invalid or changed subscription profile blocks new
AI work and removes model handlers while reconciliation/protection continue.
A subscription profile rejects API credentials, transports, funded API profiles,
model routing and price cards. Dashboard readiness checks the same admission
once, then rechecks the profile pin without repeatedly probing login.

The standard protected image has no network; the current funded-paper launch
profile supplies API egress and is deliberately incompatible with subscription
operation. The live profile permits Kraken/FX traffic, not subscription-provider
traffic. The native executor currently uses its parent network and clears proxy
environment settings. Consequently this installation also requires an independently
reviewed subscription-specific protected egress/CLI assembly before continuous
subscription operation can be commissioned. This launch profile is not implemented
or verified here. Neither root-distributed login files nor the actual WSL
Bubblewrap tests remove this exact networking blocker.
