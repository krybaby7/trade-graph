"""News alarm records and settings (T44, D137, D138 item 10). Headline text is data, never instructions.

The alarm runs in shadow mode: it logs whom a headline would have woken and wakes nobody. A verdict can only
name people to wake (D116); it has no way to delay, merge away or block a wake-up, an order or a setting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from trade_graph.contracts.models import PriceCard
from trade_graph.domain.money import parse_decimal

EvidenceLabel = Literal["live_public", "recorded_public_fixture", "synthetic_fixture"]
FeedKind = Literal["exchange_status", "exchange_announcements", "news_outlet"]
WakeRole = Literal["leader", "trader"]
SUBJECT_KRAKEN = "kraken"
SUBJECT_NONE = "none"
COIN_CODE = r"^[A-Z0-9]{1,16}$"
MAX_WATCHED_COINS = 253  # a Jev choice question allows 255 options; two are "kraken" and "none"
DEFAULT_KEY_DIRECTORY = "~/.local/share/trade-graph-owner/typesafe"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FeedSource(_Frozen):
    """An owner-approved public feed that needs no key or account."""

    feed_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,47}$")
    publisher: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=12, max_length=500)
    kind: FeedKind
    format: Literal["rss", "atom"]

    @field_validator("url")
    @classmethod
    def _https(cls, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("feeds must be plain https addresses")
        return url


# Checked when T44 was built (D138 item 10). The cloud build network refused every host, so the first
# recording of each feed is step L2; a feed that has moved is replaced in the owner's settings file.
RECOMMENDED_FEEDS: tuple[FeedSource, ...] = (
    FeedSource(feed_id="kraken-status", publisher="Kraken status page", url="https://status.kraken.com/history.atom",
               kind="exchange_status", format="atom"),
    FeedSource(feed_id="kraken-blog", publisher="Kraken blog", url="https://blog.kraken.com/feed",
               kind="exchange_announcements", format="rss"),
    FeedSource(feed_id="coindesk", publisher="CoinDesk", url="https://www.coindesk.com/arc/outboundfeeds/rss/",
               kind="news_outlet", format="rss"),
    FeedSource(feed_id="cointelegraph", publisher="Cointelegraph", url="https://cointelegraph.com/rss",
               kind="news_outlet", format="rss"),
    FeedSource(feed_id="decrypt", publisher="Decrypt", url="https://decrypt.co/feed", kind="news_outlet",
               format="rss"),
)


class Headline(_Frozen):
    """One stored headline: a dated source saved before Jev reads it."""

    headline_id: str = Field(min_length=1, max_length=64)
    feed_id: str
    publisher: str
    title: str = Field(min_length=1, max_length=1000)
    link: str = Field(min_length=1, max_length=2000)
    published_at_utc: AwareDatetime | None
    received_at_utc: AwareDatetime
    item_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence: EvidenceLabel


def coin_code(symbol: str) -> str:
    """``BTC/USD`` or ``BTC`` -> ``BTC``."""
    if not isinstance(symbol, str):
        raise ValueError("a coin is named by its code")
    code = symbol.split("/", 1)[0].strip().upper()
    if not re.fullmatch(COIN_CODE, code):
        raise ValueError("a coin code is 1 to 16 letters or digits")
    return code


CoinList = tuple[str, ...]


class WatchList(_Frozen):
    """Coins a headline can concern: held now, on the paper coin list, or the day's focus coins."""

    held: CoinList = ()
    listed: CoinList = ()
    focus: CoinList = ()

    @field_validator("held", "listed", "focus", mode="before")
    @classmethod
    def _codes(cls, values: Any) -> tuple[str, ...]:
        if isinstance(values, str):
            raise ValueError("a list of coins is required")
        return tuple(sorted({coin_code(value) for value in values}))

    @model_validator(mode="after")
    def _bounded(self) -> WatchList:
        if len(self.coins()) > MAX_WATCHED_COINS:
            raise ValueError(f"a Jev question can name at most {MAX_WATCHED_COINS} coins")
        return self

    def coins(self) -> tuple[str, ...]:
        return tuple(sorted({*self.held, *self.listed, *self.focus}))

    def held_coins(self) -> tuple[str, ...]:
        return self.held

    def subject_options(self) -> tuple[str, ...]:
        return (*self.coins(), SUBJECT_KRAKEN, SUBJECT_NONE)


def _unit_interval(value: Any) -> Decimal:
    result = parse_decimal(value)
    if not Decimal("0") < result <= Decimal("1"):
        raise ValueError("the alarm threshold is above 0 and at most 1")
    return result


class NewsAlarmRule(_Frozen):
    """The would-wake rule. An alarm setting, not a Trader confidence gate: it can only add wake-ups."""

    version: Literal["news-alarm-rule-v1"] = "news-alarm-rule-v1"
    urgent_threshold: Decimal = Decimal("0.5")

    @field_validator("urgent_threshold", mode="before")
    @classmethod
    def _threshold(cls, value: Any) -> Decimal:
        return _unit_interval(value)


@dataclass(frozen=True)
class WouldWakeVerdict:
    """Whom a headline would have woken. Shadow mode sends nothing."""

    rule_version: str
    question_set_version: str
    urgent_threshold: Decimal
    would_wake: bool
    wake_roles: tuple[WakeRole, ...]
    subjects: tuple[str, ...]
    held_subjects: tuple[str, ...]
    event_type: str
    holder_yes: Decimal

    def document(self) -> dict[str, Any]:
        return {"rule_version": self.rule_version, "question_set_version": self.question_set_version,
                "urgent_threshold": str(self.urgent_threshold), "would_wake": self.would_wake,
                "wake_roles": list(self.wake_roles), "subjects": list(self.subjects),
                "held_subjects": list(self.held_subjects), "event_type": self.event_type,
                "holder_yes": str(self.holder_yes)}


class JevSettings(_Frozen):
    """Owner settings for Jev calls. Calls stay off until the owner installs a key and switches them on."""

    calls_enabled: bool = False
    model: str = Field(default="jev-1.13.0", pattern=r"^jev-\d{1,3}\.\d{1,3}\.\d{1,3}$")
    key_directory: str = Field(default=DEFAULT_KEY_DIRECTORY, min_length=1, max_length=500)
    deployment_id: str = Field(default="deployment", min_length=1, max_length=128)
    price_card: PriceCard | None = None
    fx_rate: Decimal = Decimal("1")
    fx_buffer: Decimal = Decimal("1.02")
    fx_rate_id: str | None = Field(default=None, min_length=1, max_length=128)
    timeout_seconds: int = Field(default=10, ge=1, le=60)

    @field_validator("fx_rate", "fx_buffer", mode="before")
    @classmethod
    def _fx(cls, value: Any, info) -> Decimal:
        result = parse_decimal(value)
        if result <= 0 or (info.field_name == "fx_buffer" and result < 1):
            raise ValueError("positive FX rate and a buffer of at least 1 are required")
        return result

    @model_validator(mode="after")
    def _card_matches(self) -> JevSettings:
        if self.price_card is not None and (self.price_card.provider != "typesafe"
                                            or self.price_card.model != self.model):
            raise ValueError("the Jev price card must name TypeSafe and the pinned model")
        return self


class NewsAlarmSettings(_Frozen):
    """Recommended defaults (D138 item 10). The owner's private settings file can change them."""

    schema_version: Literal[1] = 1
    feeds: tuple[FeedSource, ...] = Field(default=RECOMMENDED_FEEDS, min_length=1, max_length=32)
    poll_interval_seconds: int = Field(default=300, ge=60, le=86_400)
    check_window_seconds: int = Field(default=3_600, ge=300, le=86_400)
    max_attempts_per_headline: int = Field(default=3, ge=1, le=10)
    extra_watch_coins: CoinList = ()
    rule: NewsAlarmRule = Field(default_factory=NewsAlarmRule)
    jev: JevSettings = Field(default_factory=JevSettings)
    testing_alert_usd: Decimal = Decimal("5")

    @field_validator("feeds")
    @classmethod
    def _unique_feeds(cls, feeds: tuple[FeedSource, ...]) -> tuple[FeedSource, ...]:
        if len({feed.feed_id for feed in feeds}) != len(feeds):
            raise ValueError("feed ids must be unique")
        return feeds

    @field_validator("extra_watch_coins", mode="before")
    @classmethod
    def _coins(cls, values: Any) -> tuple[str, ...]:
        return tuple(sorted({coin_code(value) for value in values}))

    @field_validator("testing_alert_usd", mode="before")
    @classmethod
    def _alert(cls, value: Any) -> Decimal:
        result = parse_decimal(value)
        if result <= 0:
            raise ValueError("the testing alert is a positive amount")
        return result

