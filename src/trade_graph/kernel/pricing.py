"""Price-card arithmetic. Cache categories are disjoint and explicitly priced."""

from __future__ import annotations

from decimal import Decimal

from trade_graph.contracts.models import ModelUsage, PriceCard

MILLION = Decimal("1000000")


def usage_cost(card: PriceCard, usage: ModelUsage) -> Decimal:
    priced_fields = {"uncached_input_tokens", "cache_read_tokens", "cache_write_tokens",
                     "billed_output_tokens", "tool_units"}
    if priced_fields.intersection(usage.unreported_fields):
        raise ValueError("priced usage is unresolved; retain the reservation")
    read_rate = card.input_per_million if card.cache_read_per_million is None else card.cache_read_per_million
    if usage.cache_write_tokens and card.cache_write_per_million is None:
        raise ValueError("cache-write pricing is unresolved; retain the reservation")
    if usage.tool_units and card.search_per_call is None:
        raise ValueError("tool pricing is unresolved; retain the reservation")
    write_rate = card.cache_write_per_million or Decimal("0")
    tool_rate = card.search_per_call or Decimal("0")
    return (
        Decimal(usage.uncached_input_tokens) * card.input_per_million
        + Decimal(usage.cache_read_tokens) * read_rate
        + Decimal(usage.cache_write_tokens) * write_rate
        + Decimal(usage.billed_output_tokens) * card.output_per_million
    ) / MILLION + Decimal(usage.tool_units) * tool_rate


def worst_case_cost(card: PriceCard, max_input: int, max_output: int, max_tools: int) -> Decimal:
    usage = ModelUsage(uncached_input_tokens=max_input, billed_output_tokens=max_output, tool_units=max_tools)
    # Any input token can be a cache write. Reserve the largest configured category,
    # never the discounted read rate. Missing write pricing is refused at settlement.
    maximum = max(card.input_per_million, card.cache_write_per_million or Decimal("0"),
                  card.cache_read_per_million or Decimal("0"))
    bound = card.model_copy(update={"input_per_million": maximum})
    return usage_cost(bound, usage)
