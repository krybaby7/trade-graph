"""T44: the news alarm settings start with Jev off and the recommended defaults (D138 item 10)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from trade_graph.adapters.decisions.jev import JEV_PRICE_CARD
from trade_graph.contracts.news import RECOMMENDED_FEEDS, NewsAlarmSettings

ROOT = Path(__file__).resolve().parents[2]


def test_the_example_settings_are_the_recommended_defaults_with_jev_off():
    settings = NewsAlarmSettings.model_validate_json((ROOT / "config" / "news-alarm.example.json").read_text())
    assert settings == NewsAlarmSettings()
    assert settings.jev.calls_enabled is False and settings.jev.model == "jev-1.13.0"
    assert settings.poll_interval_seconds == 300 and settings.check_window_seconds == 3600
    assert settings.feeds == RECOMMENDED_FEEDS and settings.extra_watch_coins == ()
    assert settings.rule.urgent_threshold == Decimal("0.5") and settings.testing_alert_usd == Decimal("5")


def test_the_synthetic_fixture_settings_only_add_the_invented_coin():
    settings = NewsAlarmSettings.model_validate_json(
        (ROOT / "tests" / "fixtures" / "news" / "settings.synthetic.json").read_text())
    assert settings.extra_watch_coins == ("EXMPL",)
    assert settings.model_copy(update={"extra_watch_coins": ()}) == NewsAlarmSettings()


@pytest.mark.parametrize("change", [
    {"jev": {"model": "jev-latest"}},
    {"jev": {"model": "jev-preview"}},
    {"jev": {"fx_buffer": "0.9"}},
    {"jev": {"api_key": "never in a settings file"}},
    {"feeds": [{"feed_id": "plain", "publisher": "x", "url": "http://example.com/feed", "kind": "news_outlet",
                "format": "rss"}]},
    {"feeds": []},
    {"poll_interval_seconds": 5},
    {"rule": {"urgent_threshold": "0"}},
    {"testing_alert_usd": "0"},
    {"extra_watch_coins": ["not a coin"]},
    {"wake_trader_on_every_headline": True},
])
def test_settings_refuse_aliases_keys_plain_http_and_unknown_fields(change):
    with pytest.raises(ValidationError):
        NewsAlarmSettings.model_validate_json(json.dumps({"schema_version": 1, **change}))


def test_a_price_card_in_the_settings_must_name_typesafe_and_the_pinned_model():
    card = JEV_PRICE_CARD.model_dump(mode="json")
    assert NewsAlarmSettings.model_validate({"jev": {"price_card": card}}).jev.price_card == JEV_PRICE_CARD
    with pytest.raises(ValidationError):
        NewsAlarmSettings.model_validate({"jev": {"price_card": {**card, "model": "jev-1.14.0"}}})
    with pytest.raises(ValidationError):
        NewsAlarmSettings.model_validate({"jev": {"price_card": {**card, "provider": "openrouter"}}})
