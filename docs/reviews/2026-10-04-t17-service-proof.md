# T17 exact service binding and local restart producer

This checkpoint adds an exact fixed systemd/unit/process observation interface and
an actual local paper-process restart producer. It does not complete intended-host,
funded-paper, invoice, off-host recovery, delivered-alert or live acceptance.

## Implemented source and scope

`ServiceUnitBinding` binds the fixed paper unit, console-script, protected package
and interpreter, database/configuration paths and private configuration bytes.
Only the supplied simple unit profile is supported. Extra hooks, environment
wrappers, expanded or duplicate directives, unexpected service commands and byte
drift refuse. Actual observation checks the fixed bounded `systemctl show` result,
effective fragment/drop-ins/environment/hardening/daemon freshness, then the actual
service process's user, executable and complete arguments. The collector must use
the same installed wheel/interpreter as the running service. Process environment
inspection checks only names and refuses Python/LD loader substitution; raw values
are never decoded or retained. Procfs reads loop to bounded EOF so a short read
cannot hide a later forbidden loader name. This workspace has no running systemd
installation; positive systemd deployment is not claimed.

`LocalPaperRestartCollector` uses only fixed trusted package entry points and new
private synthetic storage. A real `PaperService` process holds exclusive flock,
submits one simulated paper order, loses the acknowledgement, persists one broker
fill and an owner `MANAGE_ONLY` pause, and exits abruptly. The authoritative intent
remains `UNKNOWN`, the ledger fill remains absent, and both service leases survive.
Two normal paper CLI subprocesses then recover this effect, acquire exclusive
flock, preserve the owner pause, and stop through SIGTERM. The producer checks one
submission attempt/one ledger fill, zero paid receipts/reservations, drained leases
and equal complete financial projections across the second boot. Snapshot hashes,
backup checksums, command hashes, bounded private process logs, current source and
configuration hashes are retained and authenticated. Verification rechecks retained
records without starting another process or contacting a network.

`HostObservationCollector` and the installed preflight pure APIs accept the exact
optional `ServiceUnitBinding` and `LocalRestartSource` objects. A local restart
source appears as `local_restart_rehearsal`, explicitly scoped to
`new_synthetic_paper_fixture`. It cannot promote the operating database's restart
result or systemd/intended-host/funded results. Direct host-report verification
and retained drill reads now require current-owner private, single-link regular
storage with descriptor-based ancestor verification before reading.

## Actual verification

The final scoped suite passed **195 tests**, with zero failures, errors or skips:

```bash
PYTHONPATH=src /workspace/trade-graph/.venv/bin/pytest -q \
  tests/integration/test_service_binding.py \
  tests/integration/test_service_proof.py \
  tests/integration/test_operations_evidence.py \
  tests/integration/test_operations_preflight.py \
  tests/integration/test_live_upstream.py \
  tests/integration/test_paper_service.py \
  --junitxml=/tmp/trade-graph-r4-t17-service-complete.xml
```

The suite ran on Python 3.12.14 from the existing frozen root environment, with
this isolated worktree's source. JUnit records 43.559 seconds and an actual start
of `2026-10-04T13:08:10.498950+00:00`. Source/tests passed the scoped Ruff check.
Negative cases cover byte/source/key/digest tampering, unsupported authority claims,
signed inventory/command alterations, symlinks/ancestor aliases/hardlinks/FIFOs,
unsafe modes, duplicate JSON fields, effective systemd configuration drift,
missing/duplicate/oversized status responses and partial procfs reads.

A separate actual installed-module interface exercise used the worktree source:

```bash
PYTHONPATH=src /workspace/trade-graph/.venv/bin/python \
  -m trade_graph.application.service_proof capture \
  --report /tmp/trade-graph-r4-t17-process-proof/proof.json \
  --key /tmp/trade-graph-r4-t17-process-proof/observation.key
```

It returned exit status 0 with evidence digest
`0bc9af4206eb382818587326b06c56260cc822d7c4965ba55690c11f27799d50`,
`restart_reconciliation: observed`, synthetic scope, paid/live false and funded
acceptance false. This exercises the packagable module from source; final integrated
wheel/image verification remains the orchestrator's separate checkpoint.

Initial checking exposed an actual startup transition where a writer created WAL
after the read-only diagnostic had classified the database as idle. The readiness
loop now retries only the two exact operational diagnostic messages for that
startup transition until its existing deadline; integrity/financial failures still
refuse. Two earlier failures came from test fixtures (inherited private umask and
a duplicate-field insertion that matched no bytes); both fixtures were corrected.
No successful output is inferred from those failed runs.

## Remaining actual gates

Run exact observations and actual persistent-account restart/reconciliation on the
owner-designated installation. Configure separately approved model routes/current
prices/real allowance/paid permissions, then collect actual paid paper work and
invoice corroboration. Restore approved public connectivity; earlier configured
proxy probes remain failures. Verify owner-controlled off-host copies and actual
authorized alert delivery. Broader protected-host admission and live authorization
remain independent gates. No new public network, paid-provider, private venue,
withdrawal or live operation was performed by this checkpoint.
