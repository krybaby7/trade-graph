# Implementation status

Updated: 2026-09-29. **Paper runtime is implemented. Paid calls and live trading stay disabled.**

## Branch and commit

- Branch: `cursor/trade-graph-r1-548a`
- Runtime commit: `a4bdce6c3bfad834d0044c8dcc443e2818e8d581`
- Base: `main` at `7770d17`
- Owner for claimed tasks: `cursor-agent`
- Integration: one branch and one draft pull request. Do not merge from this status file.

## Default

Paper capital remains USD 10,000 with EUR reporting. Real operating spend is a separate ledger. `paid_calls_enabled` and `live_enabled` default to false. No real orders, withdrawals, API spending, or infrastructure purchases were made.

## Task disposition

`planning/progress.json` records T00–T19 and T21–T22 as `done` with the pytest evidence below. **T20 is `blocked`.** The bounded live pilot has not started because owner live authorization and a real operating budget are absent.

Credentialed OpenAI, Anthropic, and Kraken checks were not run. A fixture or fake transport is not a passed paid-provider or exchange test.

## Functioning commands

```bash
uv sync --frozen --group dev
uv run ruff check src tests
uv run pytest
python3 scripts/test_planning.py
python3 scripts/check_plan.py
uv run trade-graph doctor
uv run trade-graph init --mode paper --capital 10000 --capital-currency USD --reporting-currency EUR
uv run trade-graph demo --offline
uv run trade-graph run --mode paper
uv run trade-graph pause --profile manage-only
uv run trade-graph backup --destination runtime/backup.sqlite
uv run trade-graph reconcile
uv run trade-graph report --format json
```

`run` performs one paper recovery pass. It does not enable paid calls, live trading, or new model decisions. `run --mode live` exits with an error.

## Results from this checkout

- `uv run ruff check src tests`: passed.
- `uv run pytest -q`: 34 passed. No credentials used.
- `python3 scripts/test_planning.py`: 10 passed.
- `python3 scripts/check_plan.py`: plan valid, 23 tasks.
- Offline demo (`tests/e2e/test_offline.py`): finding does not create an order; model timeout is not a hold; a loss can stay `valid_thesis` and a win can stay `invalid_process`; activated artifact hash differs from the baseline and the later hold uses it; a protected kernel edit is rejected and its receipt remains; budget exhaustion does not stop reconciliation; restart does not submit again; duplicate fill is ignored; A43 equity is EUR 9000 at 0.90 and EUR 9100 at 0.91 with alpha 0; A44 paper reset and synthetic receipts do not change the real remaining budget; leader trade approvals are 0; evaluation verdict is `insufficient_evidence` (below the 30-decision minimum).
- CLI (`tests/unit/test_cli_ops.py`): init, manage-only pause, checksummed backup, reconcile, and paper run succeed. Live run is refused.
- Kraken adapter: submit raises `LiveDisabled` unless live is enabled and a key is present. The test transport is fake. No authenticated Kraken call ran.
- Provider adapters: OpenAI Responses and Anthropic Messages fixtures parse structured output, rate limit, truncation, timeout, and refusal. The gateway does not call the network. Credentialed probes are pending.
- Isolation: a separate process denies secret, withdrawal, and budget operations. Plugin source that imports OS or network modules is rejected. Promotion requires a trusted-controller attestation.

## Failures

No failing tests in the commands above. These product gaps are recorded rather than marked passed:

- No HTTP client path is wired for live OpenAI or Anthropic calls. Fixture parsing is not a credentialed probe.
- No Kraken public WebSocket reconnect/backfill client and no network smoke.
- FX rates are injected observations. There is no Frankfurter client.
- Exposure comparison still treats USD notional and EUR equity as the same number (D16).
- `POST /api/v1/leader/activate` refuses the caller. Activation is `VersionController`, used by the offline demo.
- Mandate rows are not loaded inside `authorize`; caps are arguments.
- Alembic is present; the running path applies `migrate.py` when a database opens.
- No container image or cgroup attestation (D15).

## Missing credentials and owner decisions

- No OpenAI or Anthropic API key. Paid smoke and a credentialed forward-paper window are pending.
- No Kraken trading key. Authenticated or real-order tests were not run.
- No owner live authorization and no real operating budget. T20 stays blocked. Paper USD 10,000 is not a live allocation.
- Broader Engineer code classes are not granted. Plugin and process controls refuse unattested promotion.

## Deviations

D12 httpx-shaped REST bodies, D13 temporary allowlisted git sandbox, D14 fixed maker 0.004 / taker 0.008 fee tier, D15 process isolation without a container image, D16 incomplete USD/EUR exposure conversion. See `docs/DECISIONS.md`.

## Next work

T20: owner live eligibility, an explicit live allocation that is not the paper USD 10,000, and a real operating budget, before any pilot. Separately, an owner-funded credential configuration is required before a paid paper soak can be reported as anything other than pending.

## Earlier planning checkpoint

Planning validation on `main` at `dab766f` remains historical. GitHub Actions run `https://github.com/krybaby7/trade-graph/actions/runs/36618799789` checked the plan, not this runtime. Do not reset this branch to that checkpoint.
