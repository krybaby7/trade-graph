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
- [ ] Run targeted/full appropriate verification, actual process/UI checks, secret scans; commit sanitized checkpoint.
- [ ] Fast-forward the clean WSL installation after consistent backups; preserve settings/session/evidence, restart dashboard only, and verify controls. No continuous model operation or live activity.

Owner scope supersedes proposal/approval pauses in workflow skills: this request explicitly specifies the product and authorizes concrete implementation. No additional design approval is required.

Verification source 09160e4: 245 scoped plus 48 final integration tests, native isolation, independent review, installed locked wheel, Ruff, JS syntax and planning checks passed. Full suite running in a persistent private temporary directory; operational subscription and live commissioning remain blocked.
