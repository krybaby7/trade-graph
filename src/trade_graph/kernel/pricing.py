"""Price-card arithmetic. Rates come from dated cards, not hardcoded business rules."""

from __future__ import annotations

from decimal import Decimal

from trade_graph.contracts.models import ModelUsage, PriceCard

MILLION = Decimal("1000000")


def usage_cost(card: PriceCard, usage: ModelUsage) -> Decimal:
    cache_rate = card.cache_read_per_million or card.input_per_million
    input_cost = Decimal(usage.uncached_input_tokens) / MILLION * card.input_per_million
    cache_tokens = usage.cache_read_tokens + usage.cache_write_tokens
    cache_cost = Decimal(cache_tokens) / MILLION * cache_rate
    output_cost = Decimal(usage.billed_output_tokens) / MILLION * card.output_per_million
    tool_rate = card.search_per_call or Decimal("0")
    return input_cost + cache_cost + output_cost + Decimal(usage.tool_units) * tool_rate


def worst_case_cost(card: PriceCard, max_input: int, max_output: int, max_tools: int) -> Decimal:
    usage = ModelUsage(
        uncached_input_tokens=max_input,
        cache_read_tokens=0,
        cache_write_tokens=0,
        billed_output_tokens=max_output,
        reasoning_tokens=0,
        tool_units=max_tools,
    )
    return usage_cost(card, usage)
