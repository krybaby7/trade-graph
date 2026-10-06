# Subscription and owner startup implementation plan

Goal: complete the three requested runtime slices with live disabled and preserve the owner installation.
Architecture: subscription transport is independently admitted and isolated; a durable owner controller starts one locked service and queues manual optimisation; live composition reuses deterministic accounting/execution with protected commissioning gates. The integration owner alone changes CLI, migrations, dependencies and task/status records.

- [x] Inspect both checkouts, existing branches and runtime; preserve newer source.
- [x] Create four isolated WSL worktrees based on 937437e; baseline paper/Kraken tests: 170 passed.
- [x] Verify official subscription CLI versions/authentication, retry controls and billing; implement bounded validated adapter/readiness. Do not infer when admission is blocked.
- [x] Write failing lifecycle/owner-only optimisation tests; implement singleton start/attach, interruption recovery, authenticated controls and observable outcomes. Remove automatic optimisation schedules including artifact and delegated routes.
- [x] Write failing live assembly/recovery tests; implement protected admission before private transport, deterministic allocation, uncertain-order reconciliation and independent maintenance with scripted broker.
- [x] Integrate source and CLI with no API billing fallback; review tests and requirements.
- [x] Run exactly one public-data Research diagnostic only if subscription and OS isolation admission succeeds; otherwise record exact refusal and zero inference.
- [x] Run targeted/full appropriate verification, actual process/UI checks and secret scans; save sanitized local checkpoints.
- [x] Fast-forward the clean WSL installation after consistent backups; preserve settings/session/evidence, restart dashboard only, and verify controls. No continuous model operation or live activity.

Owner scope supersedes proposal/approval pauses in workflow skills: this request explicitly specifies the product and authorizes concrete implementation. No additional design approval is required.

Tested source b1ca4c5: 435 combined regressions, independent review, native isolation,
installed frozen-dependency wheel, Ruff, JS syntax and planning checks pass. Frozen
90d4f55 full run passed 2,905 cases before a stale proxy-test expectation; correction
98af9a4 passes 227 unit/funded tests. Collection/XML comparison covers all 2,998 final
cases across these runs, without claiming a clean monolithic final-source full run.
Clean WSL fast-forward/postflight confirms all original financial/progress rows and
30 protected-file contents/modes unchanged except additive migration 0018. Subscription
and live commissioning remain blocked; actual Research inference count is zero.
