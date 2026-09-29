#!/usr/bin/env python3
"""Calculate dated illustrative costs with Decimal. No API calls or trading."""
from __future__ import annotations
import argparse
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
D = Decimal

def amount(value: object) -> Decimal:
    if isinstance(value, (float, bool)):
        raise ValueError('Use decimal strings or integers, not floats/bools.')
    result = D(value)
    if not result.is_finite() or result < 0:
        raise ValueError('Amounts must be finite and nonnegative.')
    return result

def request_cost(prices: dict, model: str, count: object, inputs: object, outputs: object) -> Decimal:
    p = prices['models'][model]
    return amount(count) * (amount(inputs) * amount(p['input_per_million']) + amount(outputs) * amount(p['output_per_million'])) / D(1000000)

def calculate(prices: dict, assumptions: dict) -> dict:
    a = assumptions
    fx = amount(a['fx_eur_per_usd'])
    capital = amount(a['capital_usd'])
    if not fx or not capital:
        raise ValueError('FX and capital must be greater than zero.')
    role_costs = {r['role']: request_cost(prices, r['model'], r['calls'], r['input_tokens'], r['output_tokens']) for r in a['roles']}
    subtotal = sum(role_costs.values(), D(0))
    search = amount(a['search_calls']) * amount(prices['search_per_call_usd'])
    paid = (subtotal + search) * (1 + amount(a['extra_work_buffer']))
    host = amount(a['hosting_eur'])
    optional_host = amount(a['optional_hosting_eur'])
    t = a['trading']
    principal_total = amount(t['round_trips']) * amount(t['principal_usd'])
    fees = principal_total * 2 * amount(prices['reference_taker_rate'])
    slippage = principal_total * amount(t['round_trip_spread_slippage_bps']) / D(10000)
    s = a['setup']
    setup = request_cost(prices, s['model'], s['calls'], s['input_tokens'], s['output_tokens']) * (1 + amount(a['extra_work_buffer']))
    return dict(role_costs_usd=role_costs, model_subtotal_usd=subtotal, search_usd=search,
                ai_search_with_buffer_usd=paid, ai_search_eur=paid * fx,
                fixed_with_optional_host_eur=paid * fx + host + optional_host,
                capital_usd=capital, capital_eur_hypothetical=capital * fx,
                ai_cost_percent_of_capital=paid / capital * 100,
                turnover_usd=principal_total * 2, constant_tier_fees_usd=fees,
                assumed_slippage_usd=slippage, illustrative_total_cost_usd=paid + fees + slippage + host / fx,
                illustrative_break_even_percent=(paid + fees + slippage + host / fx) / capital * 100,
                with_optional_host_break_even_percent=(paid + fees + slippage + (host + optional_host) / fx) / capital * 100,
                hypothetical_setup_eur=setup * fx)

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        prices = json.loads((ROOT / 'planning/model-prices.json').read_text())
        a = json.loads((ROOT / 'planning/cost-assumptions.json').read_text())
        result = calculate(prices, a)
        fast = deepcopy(a)
        next(r for r in fast['roles'] if r['role'] == 'Trader')['calls'] = a['days_per_month'] * 24 * 12
        stronger = deepcopy(a)
        next(r for r in stronger['roles'] if r['role'] == 'Trader')['model'] = 'openai:gpt-6.1-sol'
        result['five_minute_trader_ai_eur'] = calculate(prices, fast)['ai_search_eur']
        result['sol_trader_ai_eur'] = calculate(prices, stronger)['ai_search_eur']
        result['notes'] = ['Prices verified ' + prices['verified_at'],
                           'USD10,000 is virtual; EUR0.90/USD is hypothetical; hosting is an allowance.',
                           'Constant fee-tier stress example overstates fees when lower volume tiers apply.',
                           'Simulation profits do not pay actual bills. No profitability forecast.']
        print(json.dumps(result, indent=2, default=str))
        return 0
    except (OSError, ValueError, KeyError, TypeError, InvalidOperation) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
