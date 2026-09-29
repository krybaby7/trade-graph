#!/usr/bin/env python3
"""Tests for planning utilities only, not for the future trading runtime."""
from copy import deepcopy
from decimal import Decimal
import unittest
from check_plan import ROOT, load_json, validate
from next_task import ready_tasks
from cost_model import calculate, amount

class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.plan = load_json(ROOT / 'planning/tasks.json')
        self.actual_progress = load_json(ROOT / 'planning/progress.json')
        self.progress = {'tasks': {t['id']: {'status': 'todo', 'evidence': []} for t in self.plan['tasks']}}
        self.prices = load_json(ROOT / 'planning/model-prices.json')
        self.assumptions = load_json(ROOT / 'planning/cost-assumptions.json')

    def test_task_dag(self):
        self.assertEqual(validate(self.plan, self.actual_progress), [])
        self.assertEqual(len(self.plan['tasks']), 23)

    def test_initial_ready(self):
        self.assertEqual([t['id'] for t in ready_tasks(self.plan, self.progress)], ['T00'])

    def test_done_requires_evidence(self):
        self.progress['tasks']['T00']['status'] = 'done'
        self.assertTrue(any('without evidence' in e for e in validate(self.plan, self.progress)))

    def test_cycle_rejected(self):
        self.plan['tasks'][0]['deps'] = ['T01']
        self.assertTrue(any('cyclic' in e for e in validate(self.plan, self.progress)))

    def test_unknown_dependency(self):
        self.plan['tasks'][0]['deps'] = ['MISSING']
        self.assertTrue(any('unknown dependency' in e for e in validate(self.plan, self.progress)))

    def test_next_after_evidenced_completion(self):
        self.progress['tasks']['T00'].update(status='done', evidence=['synthetic-test-only'])
        self.assertEqual([t['id'] for t in ready_tasks(self.plan, self.progress)], ['T01'])

    def test_cost_totals(self):
        result = calculate(self.prices, self.assumptions)
        self.assertEqual(result['ai_search_with_buffer_usd'], Decimal('3.4848'))
        self.assertEqual(result['ai_search_eur'], Decimal('3.13632'))
        self.assertEqual(result['capital_eur_hypothetical'], Decimal('9000'))
        self.assertEqual(result['illustrative_break_even_percent'], Decimal('6.834848'))

    def test_cadence_sensitivity(self):
        a = deepcopy(self.assumptions)
        a['roles'][0]['calls'] = 8640
        self.assertEqual(calculate(self.prices, a)['ai_search_eur'], Decimal('10.44576'))

    def test_model_sensitivity(self):
        a = deepcopy(self.assumptions)
        a['roles'][0]['model'] = 'openai:gpt-6.1-sol'
        self.assertEqual(calculate(self.prices, a)['ai_search_eur'], Decimal('6.09120'))

    def test_invalid_money(self):
        for invalid in [float('nan'), 'NaN', '-1', True, 'Infinity']:
            with self.assertRaises(ValueError):
                amount(invalid)

if __name__ == '__main__':
    unittest.main()
