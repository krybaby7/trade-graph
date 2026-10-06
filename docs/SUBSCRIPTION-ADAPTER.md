# Subscription inference adapter — 2026-10-06

The subscription implementation is fail closed. It is tested with synthetic CLI
results and actual native WSL filesystem isolation; no subscription inference or
continuous AI operation has been commissioned. There is no API-key route,
automatic provider fallback, application retry or output repair call.

## Current operator evidence and blocker

Windows Codex CLI 0.125.0 is signed in through ChatGPT. A fresh official
app-server check used `initialize`, `account/read` with `refreshToken:false`, and
`account/rateLimits/read` only. The selected CLI account reported 9% used / 91%
remaining in its 10,080-minute weekly window, reset timestamp 1791822316; its
shorter window was unavailable and credit balance was `0`. This is shared account
quota at the check time, not usage attribution to Research. Authentication tokens,
account identifiers and raw responses were neither extracted nor committed.

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

Run from an isolated WSL checkout with its locked dependencies:

```bash
PYTHONPATH=src /home/adami/trade-graph/.venv/bin/python -m pytest \
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
