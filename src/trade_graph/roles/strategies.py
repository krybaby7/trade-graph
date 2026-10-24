"""Two unproven paper hypotheses. A template suggestion is not an order."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyTemplate:
    strategy_id: str
    hypothesis: str
    features: tuple[str, ...]
    entry_rule: str
    exit_rule: str
    invalidation: str
    sizing_rule: str


TEMPLATES: dict[str, StrategyTemplate] = {
    "slow-trend-pullback": StrategyTemplate(
        strategy_id="slow-trend-pullback",
        hypothesis="A rising higher-timeframe drift can resume after a shallow pullback.",
        features=("hourly_return", "four_hour_drift", "highest_high_20"),
        entry_rule="Drift is positive and latest_close sits a shallow step below highest_high_20.",
        exit_rule="Drift flips or latest_close falls well below highest_high_20.",
        invalidation="Drift is no longer positive at the decision snapshot.",
        sizing_rule="Use the mandate quantity. Do not increase size to meet a minimum.",
    ),
    "range-reversion": StrategyTemplate(
        strategy_id="range-reversion",
        hypothesis="Price inside a recent range can revert toward its midpoint.",
        features=("range_high", "range_low", "midpoint"),
        entry_rule="Price is near the range edge and the range is still intact.",
        exit_rule="Price reaches the midpoint or leaves the range.",
        invalidation="The range high or low breaks before entry.",
        sizing_rule="Use the mandate quantity. The template does not size risk.",
    ),
}


def template(strategy_id: str) -> StrategyTemplate | None:
    return TEMPLATES.get(strategy_id)


def counterfactual(strategy_id: str) -> str:
    item = template(strategy_id)
    if item is None:
        return "no template suggestion"
    return f"unproven {item.strategy_id}: entry would require {item.entry_rule}"
