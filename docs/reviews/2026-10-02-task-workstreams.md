# Task workstreams — 2026-10-02

The owner requested one workstream per remaining task and active orchestration.
These are separate agent threads in this session, with isolated Git worktrees;
the assistant cannot create chats in the owner's chat list.

Integration owner: `codex-orchestrator`, branch `codex/orchestrator-continuation`.
Start checkpoint: `4bf8ef74eaca7d76be8eb49aecb0755f8ad4d0db`.
Task status records acceptance progress; `execution_state` records whether an
agent is currently working. A running preparation agent does not remove the
task's dependencies or grant production authority.

| Task | Agent thread | Branch | Authorized workstream |
|---|---|---|---|
| T17 | `/root/t17_operations` | `codex/t17-continuation` | Public network diagnosis, FX provenance and bounded transport, deployment/backup verification |
| T18 | `/root/t18_evaluation` | `codex/t18-continuation` | Immutable evaluation evidence snapshots and retained expense/attempt sealing integrity |
| T19 | `/root/t19_adapter` | `codex/t19-continuation` | Offline wire conformance, stable order ownership and native ledger identity |
| T20 | `/root/t20_pilot_gate` | `codex/t20-continuation` | Closed live gate and scoped advisory evidence verification; no pilot |
| T21 | `/root/t21_kernel` | `codex/t21-continuation` | Actual protected business service/controller, durable scoped RPC and confined mutable worker |
| T22 | `/root/t22_engineer` | `codex/t22-continuation` | Offline immutable pure-plugin staging and repeated confined replay; no production promotion |

Each worktree is `/workspace/trade-graph-tNN-continuation`. The root alone owns
integration, schema migrations, dependencies/lockfile, CLI changes, shared
planning/status records and final acceptance. Agents agree cross-task contracts
before implementing interfaces. Older worktrees and branches remain preserved.

T21 runs independently of T17. T18/T19 completion requires T17; T20 requires
T18/T19 and explicit live authority; T22 requires T18/T21 and an explicit
broader code-class grant. Dependency-gated agents may implement preparation,
but cannot substitute fixtures or self-attestation for genuine prerequisites.

Paid providers, private venue requests, real orders, infrastructure purchases,
production deployment and broader deployed Engineer authority are not granted
by repository development. No such actions form part of these workstreams.

## Initial verified findings

Public Kraken and Frankfurter probes failed with `ProxyError` through the
configured proxy. Probe reports retain sanitized failures in private temporary
storage; proxy values and credentials are not printed or committed. T17 funded
and actual intended-host verification remains pending.

Migration `0010` adds protected release admission, runtime generations and
unique one-use capability/request records in the trusted financial database.
It passed 31 scheduler/recovery tests and targeted Ruff checks at commit
`3e0b02f`. This is a schema checkpoint, not proof of the production boundary.

Final integrated commits, results and remaining gates will be recorded here
after the individual workstreams finish and root verification completes.
