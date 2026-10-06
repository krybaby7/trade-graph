# First Kraken-data paper operating checkpoint — 2026-10-07

Tested source `643f185`, branch `codex/first-paper-cycle-2026-10-07`, descends from
preserved checkpoint `e36e447`. **The operating milestone is pending owner native
subscription login; no actual Research analysis or Trader cycle is claimed.**

## Actual operating evidence

| Check | Observed result |
|---|---|
| Native subscription route | Official native WSL Claude Code 2.1.292, signed manifest and binary checksum verified; separate configuration signed out |
| Requested / actual model | `claude-sonnet-5-5` / none |
| Inference attempts | Diagnostic Research 0; cycle Research 0; Trader 0; Leader 0; CLI internal attempts 0 |
| Research / Trader result | None; no validated Research and no Trader decision. Zero orders is not a model no-trade decision |
| Paper broker | Zero order intents, fills and new decisions; one original USD10,000 portfolio, EUR reporting, all cash |
| Accounting | Original financial journal, expenses, budgets and mandate preserved. Recorded API expense remains EUR0; subscription/preparation/shared fee attribution unknown |
| Stop state | Owner-requested `MANAGE_ONLY`, achieved `managing`; no open paper orders or positions require a running daemon |
| Dashboard | Six authenticated read endpoints return HTTP200; EUR8873.90 cash/equity, zero orders/Research/decisions; service stopped, management-only pause |
| Forbidden effects | Private Kraken requests, model API billing, real orders, withdrawals and infrastructure purchases: zero |

At **2026-10-06 22:26:17 UTC**, final stored Kraken BTC/USD and ETH/USD quotes
were **19.625036s / 18.9596s** old, within the existing 30-second mandate limit.
These REST references use receipt time; an independent exchange publication time
is unavailable. Both completed hourly collections contain **168 candles**, from
**2026-09-29 22:00 UTC** through latest close **2026-10-06 22:00 UTC**, with
**zero missing slots/gaps**. Their actual receipt times were
22:25:53.851519 / 22:25:54.283655 UTC. The new latest candle was appended; prior
history was retained. Kraken's incomplete final candle was excluded.

The stored **USD/EUR 0.88739** reporting reference is explicitly Frankfurter/ECB,
valid **2026-10-06**, retrieved **22:25:59.088322 UTC**. Daily reference FX is not
an intraday executable rate. Freshness is measured at the stated observation,
not guaranteed after this checkpoint or across interactive login.

## Protected runtime and checks

Final image:
`sha256:1b5b0fa0155178b45820b197029e0c23f4b9027b277e0619fe2eb354fdbb6b36`.
Installed package/interpreter digest:
`d553534223cd4d91518ee2805b88ffa231951e599abaec4c0fdee9c81e29dc85`.

- Actual final-image bwrap probe passed all nine checks: private/Windows files,
  host processes and API environment denied; descendants killed; tools disabled;
  direct egress denied; provider and market listeners restricted. This was a
  credential-free process test. Tool evidence comprises real native help and
  offline positive/negative parser checks, not a model tool trial.
- Separate production inspection verified nonroot UID/GID10001, read-only image
  and owner mount, exactly two production mounts, no capabilities/privileged
  mode, resource limits, pinned deny-default seccomp and dedicated internal
  network. The synthetic Windows fixture and test stdlib bind existed only in
  the separate adversarial probe.
- Actual final-image public HTTP conformance fetched Kraken quotes and hourly
  data plus ECB FX through the fixed market proxy. No provider HTTP request was
  made. The app has no direct internet route; provider and market listeners have
  different exact host allowlists, bounded CONNECT/TLS-SNI validation and no
  credential collection or request-content logging.
- An earlier image failed HTTPX CONNECT compatibility with HTTP400. The final
  proxy permits only the fixed `Accept: */*` value and passed actual HTTPX and
  public transport checks. Docker29 reordered its masked-path list; comparison
  now requires exact unique members while permitting order differences.
- Final selected source checks: **445 passed, 0 failed/errors/skipped in 47.190s**;
  Ruff on source/tests/build/probe scripts passed; 23-task DAG and 10 planning
  checks passed. These are local checks, not full acceptance or profitability.
- A model-free `manage-subscription` launch continuously retains existing paper
  orders/positions through quota/login failures. Tests include a synthetic paper
  stop fill, ownership conflict, public outage and signal cleanup. It never
  claims model tasks. A cycle opening exposure must leave this manager running.

The inference envelope is at most three journaled native invocations across the
one diagnostic and one Research/Trader cycle: each 120s, 4096 output tokens, one
turn and 256KiB combined output. Transport/timeout retries are explicitly zero;
structured attempts are one total. The owner's revised bounded retry allowance
is respected; this selected route does not need additional retries. Application
retry, repair, provider/model fallback, API configuration, fast mode and tools
remain disabled. Repeated/interrupted commands cannot replay an invocation.
A successful diagnostic gates the normal cycle; quotes refresh before Trader.
No Leader call is required by the existing revision1 mandate.

## Preservation, usage and remaining work

Consistent private SQLite backups preceded market writes. All **95 pre-existing
private files** remain byte/mode-identical, and the progress sidecar is unchanged.
Only public market/valuation tables, service-run records and the explicitly
requested pause control changed. No portfolio reset, owner budget/mandate write,
financial fill or expense mutation occurred. Existing Windows local changes and
both original checkouts were preserved; implementation used isolated worktrees.

At **2026-10-06 22:23:48 UTC**, read-only Codex account metadata reported **78%
weekly remaining**, reset **2026-10-12 16:25:15 UTC**; the short window was
unavailable. This is shared Codex capacity, not Claude quota or a task-cost
receipt. Claude capacity and subscription cost remain unknown while signed out.

Private evidence is retained under the existing runtime's
`first-paper-cycle-20261007/` directory and root-controlled
`/opt/trade-graph-subscription/`; none is committed. Receipts retain the earlier
failed preparation attempts and final successful checks. The final admission
probe verifies native/image/egress pins and refuses inference with the official
login absent and extra-usage verification false.

Next: owner completes [the exact native subscription login and spending checks](../FIRST-PAPER-CYCLE.md#owner-login-and-included-usage-check).
Then provision the opaque official login file without inspecting tokens, make a
consistent protected operating-state copy of the same portfolio, explicitly
resume only this task's pause for its authorized one cycle, refresh data, run the
diagnostic and, only if valid, the bounded cycle. Preserve stronger or unrelated
owner halts. Record actual results and reapply management-only protection.
T17 and later acceptance/live gates remain unchanged.
