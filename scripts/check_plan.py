#!/usr/bin/env python3
"""Validate the planning DAG and repository references; no network or paid work."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
STATUSES = {'todo', 'in_progress', 'blocked', 'done'}

def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'{path}: expected a JSON object')
    return value

def validate(plan: dict, progress: dict, root: Path | None = None) -> list[str]:
    errors: list[str] = []
    tasks = plan.get('tasks', [])
    if not isinstance(tasks, list) or not tasks:
        return ['tasks must be a nonempty list']
    ids = [t.get('id') for t in tasks if isinstance(t, dict)]
    if len(ids) != len(tasks) or any(not isinstance(i, str) for i in ids):
        return ['each task requires a string id']
    if len(set(ids)) != len(ids):
        errors.append('duplicate task id')
    by_id = {t['id']: t for t in tasks}
    states = progress.get('tasks', {})
    if not isinstance(states, dict):
        return errors + ['progress.tasks must be an object']
    if set(states) != set(ids):
        errors.append('progress IDs do not match task IDs')
    for t in tasks:
        i = t['id']
        for field in ('title', 'phase', 'release', 'specs', 'deliverables', 'acceptance'):
            if not t.get(field):
                errors.append(f'{i}: missing {field}')
        deps = t.get('deps', [])
        if not isinstance(deps, list):
            errors.append(f'{i}: deps must be a list')
            continue
        for d in deps:
            if not isinstance(d, str) or d not in by_id:
                errors.append(f'{i}: unknown dependency {d!r}')
        state = states.get(i, {})
        if not isinstance(state, dict):
            errors.append(f'{i}: invalid progress record')
            continue
        if state.get('status') not in STATUSES:
            errors.append(f'{i}: invalid progress status')
        if state.get('status') == 'done':
            if not state.get('evidence'):
                errors.append(f'{i}: done without evidence')
            if any(states.get(d, {}).get('status') != 'done' for d in deps if isinstance(d, str)):
                errors.append(f'{i}: done before dependencies')
        if root is not None:
            for spec in t.get('specs', []):
                path = (root / spec).resolve()
                if not path.is_relative_to(root.resolve()) or not path.is_file():
                    errors.append(f'{i}: missing or unsafe spec path {spec}')
    seen: set[str] = set()
    active: set[str] = set()
    def visit(i: str) -> None:
        if i in active:
            errors.append(f'cyclic dependency at {i}')
            return
        if i in seen:
            return
        active.add(i)
        deps = by_id[i].get('deps', [])
        for d in deps if isinstance(deps, list) else []:
            if isinstance(d, str) and d in by_id:
                visit(d)
        active.remove(i)
        seen.add(i)
    for i in ids:
        visit(i)
    return errors

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        plan = load_json(args.root / 'planning/tasks.json')
        progress = load_json(args.root / 'planning/progress.json')
        errors = validate(plan, progress, args.root)
        config = load_json(args.root / 'config/defaults.example.json')
        prices = load_json(args.root / 'planning/model-prices.json')
        costs = load_json(args.root / 'planning/cost-assumptions.json')
        if config.get('virtual_account') != {'amount': '10000', 'currency': 'USD'}:
            errors.append('default virtual account must be USD10000')
        if config.get('reporting_currency') != 'EUR' or config.get('paid_calls_enabled') is not False:
            errors.append('example must report EUR and disable paid calls')
        if config.get('mode') != 'paper' or config.get('owner_policy_example', {}).get('live_enabled') is not False:
            errors.append('example must disable live trading')
        for role in costs.get('roles', []):
            if role.get('model') not in prices.get('models', {}):
                errors.append(f'unpriced model: {role.get("model")}')
        for path in ('AGENTS.md', 'README.md', 'IMPLEMENTATION-START-HERE.md', 'IMPLEMENTATION-STATUS.md', 'docs/90-SOURCES.md'):
            if not (args.root / path).is_file():
                errors.append(f'missing entrypoint: {path}')
        if errors:
            print('\n'.join(f'ERROR: {e}' for e in errors), file=sys.stderr)
            return 1
        print(f'Plan valid: {len(plan["tasks"])} tasks; acyclic dependencies; references/configuration checked.')
        print('This validates planning structure, not runtime implementation or trading profitability.')
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
