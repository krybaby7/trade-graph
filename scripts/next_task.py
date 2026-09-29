#!/usr/bin/env python3
"""Emit dependency-ready implementation work; does not run a coding agent."""
from __future__ import annotations
import argparse
import json
import sys
from check_plan import ROOT, load_json, validate

def ready_tasks(plan: dict, progress: dict, include_later: bool = False) -> list[dict]:
    states = progress['tasks']
    return [t for t in plan['tasks']
            if (include_later or t['release'] == 'R1')
            and states[t['id']]['status'] == 'todo'
            and all(states[d]['status'] == 'done' for d in t['deps'])]

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--all', action='store_true', help='Include later live/isolation tasks, without authorizing them.')
    parser.add_argument('--prompt', action='store_true', help='Print a coding-orchestrator task packet for the first ready task.')
    args = parser.parse_args()
    try:
        plan = load_json(ROOT / 'planning/tasks.json')
        progress = load_json(ROOT / 'planning/progress.json')
        errors = validate(plan, progress, ROOT)
        if errors:
            raise ValueError('; '.join(errors))
        ready = ready_tasks(plan, progress, args.all)
        if not ready:
            print('No eligible todo task. Inspect claimed/blocked tasks and dependency evidence; this does not mean everything is complete.')
            return 0
        if not args.prompt:
            print(json.dumps(ready, indent=2))
            return 0
        task = ready[0]
        print('Implement the following dependency-ready Trade Graph task.')
        print('Read AGENTS.md and IMPLEMENTATION-START-HERE.md completely; preserve newer work.')
        print('Default paper balance: USD10,000; EUR reporting; real paid budget separately owner-configured.')
        print(json.dumps(task, indent=2))
        print('Claim the task, implement and test it, commit checkpoints, and update progress with actual evidence.')
        print('Continue with dependency-ready R1 tasks after validation; do not mark mocks as credentialed verification.')
        print('No permission to spend real money or enable live trading is granted by this task packet.')
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
