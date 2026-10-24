# Handoffs

Each work package in [the cloud build plan](../../docs/CLOUD-BUILD-PLAN.md)
writes one file here, named after its package (for example `W1-B.md`). The
integration session copies the status into `IMPLEMENTATION-STATUS.md` and
`planning/progress.json`, and the decisions into `docs/DECISIONS.md`. Packages
never edit those three files themselves.

Copy this template:

```markdown
# <package> <task>: <title>

For the owner: <three plain sentences: what you can now do or see, what is
still missing, and anything you need to decide or do.>

Branch: `<branch>`, head `<commit>`. Pull request: <link>.

## What was built
- <module or file>: <what it does>

## Decisions (reserved block D___–D___)
- **D___ (<date>, implementation default for <task>):** <decision and reason>

## Evidence
- Tests written first: <test file> failed before (<error>) and passes now.
- `uv run ruff check src tests`: <result>
- Related tests: <command>: <passed / failed / skipped>
- `python3 scripts/check_plan.py`, `python3 scripts/test_planning.py`,
  `python3 scripts/check_repository_hygiene.py`: <results>
- Full suite: `uv run pytest -p no:cacheprovider -rf` at `<commit>`, <minutes>
  min: <passed / failed / skipped>; failures compared with the known list:
  <same / new ones named>
- Real or synthetic: <what used recorded public data, what was synthetic, and
  that no paid or exchange call was made>

## Schema and dependencies
- State migration: <version or none>. Candle database: <version or none>.
- Dependencies added: <package and reason, or none>.

## Not done, open or for someone else
- <item>: <who: integrator, local step L_, owner, later package>
```
