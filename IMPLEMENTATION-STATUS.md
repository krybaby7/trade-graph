# Implementation status — T16 complete

Updated: 2026-10-01. **T00–T16 complete at their recorded scopes; autonomous R1 operation is not complete.**
Branch: `cursor/trade-graph-r1-548a`; verified source: `1cddb349492a8b83f83ba1424f1709533e95d687`.
Source tree: `f8c609b673d61e88b7fe722340ceb36781d4aedf`. This handoff adds documentation after that tested checkpoint.
Default USD10,000 virtual capital with EUR reporting. Paid calls and live trading remain disabled.

## Completed T16

The offline demo now sends entry, exit and lost-acknowledgement decisions through the actual Trader handler,
model gateway and durable role worker. Research precedes entry; typed Learning and Optimisation records
retain decision links, revisions and counterevidence. All six roles produce synthetic gateway receipts.
The commissioned Engineer rejects and repairs a candidate, the Secretary routes actual Leader activation,
and consumers load tested bytes before further operation, restart, observation and rollback. The private
`evidence.json` records 14 checked outcomes plus request, snapshot, receipt and version provenance.
The supplied shared bill is a synthetic fixture, not a provider charge.

Trader requests use one authoritative SQLite read snapshot with native balances, inventory, held reservations,
open orders and explicitly stale/provisional reporting. Market inputs are filtered by venue, event time and
availability time. Future or retired lessons cannot silently become current evidence. Durable root attempts
have a protected 40-step default cap across restart. Provider output/tool validation uses complete bounded
Draft 2020-12 JSON Schema with external retrieval denied and supplied usage retained on failure.

The fault suite exercises actual SQLite rollback, full-disk, commit and journal failures, abrupt process death,
UNKNOWN execution through an outage, reconciliation, cancel/replace races, broker capability/fee refusals,
public-feed degradation, atomic Secretary routing, budget limits and evidence provenance. Backup restoration
validates the exact staged database and atomically replaces an offline target; failed restoration preserves
prior data. Generated accounting tests check conservation against independent calculations.

All **40 applicable offline criteria (A01–A38, A43–A44)** have concrete executable mappings in
`planning/offline-acceptance.json`. The acceptance runner binds actual collected JUnit outcomes to source,
lockfile and Python identity. Missing, skipped, interrupted or partial evidence cannot complete the gate.
Its child pytest process receives an allowlisted environment and denies Python socket connections; this is
application-local verification, not OS-wide isolation. CI runs that gate and scans generated reports before upload.
Read [the T16 review](docs/reviews/2026-10-01-offline.md) and D41–D44 for details. Earlier evidence remains in
[the T15 review](docs/reviews/2026-10-01-dashboard.md) and [the T14 review](docs/reviews/2026-10-01-activation.md).

## Verified commands and evidence

```bash
uv sync --frozen --group dev --python 3.12
uv run ruff check src tests scripts/check_repository_hygiene.py scripts/verify_offline_acceptance.py
uv run pytest --junitxml=/tmp/t16-final-results.xml
uv run python scripts/verify_offline_acceptance.py --report /tmp/trade-graph-t16-final-acceptance.json
python3 scripts/check_repository_hygiene.py --staged --artifact /tmp/t16-final-results.xml --artifact /tmp/trade-graph-t16-final-acceptance.json
python3 scripts/check_plan.py
python3 scripts/test_planning.py
python3 scripts/next_task.py --prompt
python3 scripts/cost_model.py
```

- Python 3.12.14: **808 tests passed, zero failures, errors or skips**, independently counted in JUnit.
  The recovered T15 baseline was 534 tests. Two property tests cover 200 deterministic generated examples.
- The mapped run passed **465 tests**; all 40 criteria were accepted and `gate_complete=true`.
  The report binds a clean source checkout above and lock SHA256
  `1ac5a01f8beae2c1156273feb5e664e500d6672349264e2838dd823f1c67ae39`.
- A fresh checkout installed its frozen environment from the package cache and ran the standalone offline CLI:
  all 14 checks passed, four fills survived restart, and restart submitted zero orders.
- Ruff, ten planning tests, DAG/reference checks, cost illustration and repository hygiene passed.
  Exact JUnit, acceptance JSON and fresh demo JSON passed the generated-artifact scan.
- The lockfile adds `jsonschema` 4.26.0, `referencing` 0.37.0 and their dependencies for complete validation.
  Previously locked versions remain unchanged. Runtime databases and reports stay outside Git.

Run a fresh local demonstration:

```bash
uv run trade-graph demo --offline --work /tmp/trade-graph-demo-new
```

Use a new work directory; reuse is refused to preserve prior evidence. The report is written to its
`evidence.json`. Failed verification raises rather than reporting successful completion.

Run the authenticated dashboard:

```bash
uv run trade-graph init --database runtime/trade_graph.sqlite
uv run trade-graph dashboard --database runtime/trade_graph.sqlite
```

Open `http://127.0.0.1:8000/login` and use `session_token` from private `runtime/owner-session.json`.
New runtime directories use mode 0700 and session files use 0600; existing database directories must be private.
The dashboard starts one web worker and no scheduler or paid provider.

These are local credential-free results. No new CI conclusion is claimed; the GitHub GraphQL read returned
`Forbidden`. No paid-provider probe, authenticated exchange test, real order or profitability claim is made.

## Next task and remaining boundaries

**T17 is next:** assemble continuous paper scheduling, persisted model-routing consumers, real diagnostics
and reports, deployment/backup procedures and interrupted dashboard-command recovery. A hard-crashed owner
command remains visibly `PROCESSING`; ordinary writes stay blocked while emergency manage-only remains
available. `run --mode paper` still performs one recovery pass. `doctor` and `report` remain placeholders.
Credentialed paper verification needs a separately configured owner budget and credentials.

Legacy cross-currency receipts retain their stored conversion but lack linked FX source IDs; the dashboard
discloses incomplete provenance. Drawdown is sampled trading drawdown; historical economic drawdown cannot
be reconstructed without historical cost-allocation timestamps. Neither label claims continuous observation.
T16 validates those disclosures without inventing missing historical data.

**T18–T22:** forward economic evaluation, live adapter/pilot and protected-host isolation before broader code.
T20 remains blocked. Functional health, a dashboard and paper capital do not authorize spending or live trading.
Inspect Git state before continuing and preserve historical evidence in progress.json/reviews.
