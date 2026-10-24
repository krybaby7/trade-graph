# Paper repair checkpoint — 2026-10-08

## Current outcome

All five requested source repairs and a reproduced SQLite-lock defect repair
are integrated at `7be13b1` in the registered operating checkout. Equivalent
publication source is `4570876` on `codex/paper-repair-2026-10-08`. Deployment and normal paper resume
are blocked by an unexpected corruption of the current authoritative database.
No financial history, witness, owner setting or failed attempt has been reset.
The existing old image is retained; its graph worker exited and the old dashboard
was stopped after the database became unreadable. Public proxy containers remain
available, but no running collector, management worker or functional dashboard is
claimed now.

## Hold and preservation

The existing authenticated owner pause endpoint confirmed MANAGE_ONLY, followed
by achieved `managing` and a fresh heartbeat. A consistent SQLite backup passed
quick/full integrity checks; the witness, protected owner bundle, original
container inspection and all 15 subscription attempts were copied to private
storage. All attempts were terminal at the hold. The pause did not erase failures
or start replacement work. Original service schedules, owner authority, model
selection, quota controls and financial rows were not edited.

## Implemented slices

- History: UTC hour alignment, per-symbol bounded retry after 30 seconds, and a
  history refresh check before scheduled role inputs. This also handles a poll
  that starts before an hour boundary and finishes afterward. Completed-candle
  exclusion, actual receipt timestamps and prior snapshots remain unchanged.
- Research: `findings[].source_ref` must be copied from the supplied snapshot's
  `sources[].source_ref`; general evidence references alone are ineligible.
  Schema guidance and fixed instructions agree with existing strict validation;
  mixed invalid publication remains atomic.
- Trader: reporting valuation has its own currency/stale/provisional fields;
  native cash, inventory, reservations and retained order uncertainty have
  separate fields. Context does not manufacture verified venue balances or
  restrict discretionary `no_action` decisions.
- Diagnostics: bounded safe feed stage/category/symbol/recurrence/recovery facts,
  and native exit code/category/allowlisted error code/verified schema path.
  Raw output, private IDs, bodies and credentials are excluded. The original
  Leader cause was not retained and is still unknown.
- Financial continuity: supported offline inspect/apply commands require an exact
  protected owner approval, original key/inode, owner MANAGE_ONLY for all paper
  portfolios and exclusive worker ownership. They preserve every witness scope,
  prior checkpoint and financial commitment; bind exact current native/subscription
  attempt rows; retire old authority; and recover only identical authenticated
  interrupted publication. Scan bounds and expiry are enforced through commit.
  Older checkpoints cannot retroactively prove identity commitments they never
  recorded. See [operator commands and limitations](../FINANCIAL-TRANSITION.md).
- SQLite locking: retain auxiliary main-file descriptors across every local
  Database owner, Database-managed SQLite connection and ownership lease. Status probes, normal
  service release, protected controller release and transition identity checks
  no longer close a main-file descriptor while SQLite holds a transaction lock.
  Separate owner handles preserve nonblocking single-worker ownership. This source
  defect is reproduced and fixed in actual Linux lock regressions.

## Storage incident and limits

Initial host and fresh-container full integrity checks were `ok`. The existing
long-lived dashboard later reported a malformed database; fresh reads eventually
reported an invalid database header after the graph worker exited. Frozen copies,
error logs, hashes and the healthy backup are retained privately. No surviving
WAL was available at the first failed-database capture. A private candidate made
by replacing only the damaged header fails full integrity checks, so it cannot
be used for deployment or called a repaired installation.

Investigation also found unsafe raw main-database descriptor open/close operations
in ownership/identity checks. [SQLite documents](https://www.sqlite.org/howtocorrupt.html#_posix_advisory_locks_canceled_by_a_separate_thread_doing_close_)
that closing such a descriptor can release other SQLite connection locks within
that process. Deterministic regression tests reproduce the defect and verify the
retained-handle repair. The old image also uses SQLite 3.46.1, which predates the
[documented WAL-reset fix](https://www.sqlite.org/wal.html#walresetbug). Neither observation
proves the original corruption cause. A verified storage recovery procedure and
reviewed SQLite runtime are prerequisites to restarting this installation.

## Verification

- Baseline history/context/subscription/financial checkpoint selection: 93 passed.
- Integrated new history/feed/context/native regressions: 48 passed.
- Broader selected application/quota/history/departments/owned-service checks:
  155 passed. Runs overlap and are not summed.
- Retained-handle author selection: 116 passed. Financial/lock transition author
  selection: 155 passed; the complete 34-case transition selection also passed.
- Final integrated selection at `7be13b1`: **363 passed**, zero failures/errors/
  skips (130.339 seconds). The published source tree at `4570876` is identical
  across source/tests/migrations/config/deploy/prompts/scripts and dependency files.
- Final independent continuity selection: **108 passed** in 54.67 seconds, with no
  unresolved findings. The reviewer exercised transition/checkpoint/budget
  continuity and actual Linux inode-lock cases. Source checks use Python 3.12.14 / SQLite 3.53.1;
  this does not establish that the old installed SQLite runtime is repaired.

Independent review required header-first bounded payload reads and reproduced an
expiry-boundary race that could commit a self-invalidating transition. Both were
fixed with regression evidence. One validated seal timestamp plus a final
precommit expiry check preserve refusal/rollback and already-committed recovery.
The approval is for source and synthetic behavior, not the damaged installation.

Reproduce the independent selection in the reviewed Python 3.12 environment:

```bash
PYTHONPATH=src python -m pytest -q \
  tests/integration/test_financial_transition.py \
  tests/integration/test_financial_checkpoint.py \
  tests/integration/test_protected_budget_continuity.py \
  tests/integration/test_database_locks.py
```

The final integrated checks use `PYTHONPATH=src python -m pytest` over selected history,
context, subscription, service, lock and financial transition/checkpoint suites.
`python -m ruff check src tests`, `python scripts/check_plan.py`,
`python scripts/test_planning.py` and
`python scripts/check_repository_hygiene.py --staged` all passed for source and
publication. Planning validates 23 acyclic tasks; 10 planning tests passed.
The newer remote manual review schedule is preserved byte for byte.
No monolithic full-suite or repaired installed-image pass is claimed.

No fresh repaired Research publication or feature-ready postdeployment Trader
snapshot is claimed. Prior successes and all historical failed results remain
retained. Native usage/shared subscription costs stay unknown where not reported.
No billed API, paid extra, real exchange order, withdrawal or purchase occurred.


## Deployment and next gate

The owner pause was confirmed before the incident. The old worker later exited;
its dashboard container is stopped. Two pre-existing proxy containers remain
running, with no collector, management worker or functional dashboard claimed.
The frozen damaged main file still matches the post-stop evidence byte for byte;
its inode has not been replaced. No WAL survived at first capture. The hold-time
backup still passes full integrity and retains 15 attempts / 13 invocations,
including prior failures and unknown costs. Originals and all private evidence
remain outside Git.

A storage recovery decision is required before replacing/reconstructing the current
DB under the owner's explicit preservation instruction. Preparing a recovery for
review is not authorization to restore it. After reviewed recovery and SQLite
runtime validation, use the supported protected transition with single-worker
ownership; then require fresh quota admission and authenticated resume. Only a
real subsequent Research publication and a feature-ready Trader snapshot can
satisfy postdeployment verification. Normal schedules and legitimate no-trade
choices remain configured; none was forced or cleared for a demonstration.
