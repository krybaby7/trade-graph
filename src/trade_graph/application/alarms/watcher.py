"""The price watcher: software that checks every armed alarm against every price update and completed candle.

It never calls a model, so watching costs no tokens (D115). How it is driven (wiring is W5-A / W6-D):

- ``observe_quote(observation)`` for every public price update the feed delivers (quote alarms);
- ``tick()`` after candles are stored and on a short timer (candle and indicator alarms, and feed health).

Rules:

- **Completed candles only.** Candles are read only through ``CandleStore.window`` in point-in-time mode as of the
  clock, so a candle is used only after it closed *and* was received; the still-forming candle is never stored and a
  live quote never stands in for a candle. Indicator values come from the T27 engine (``compute_indicator`` and
  ``compare_to_level``); no indicator formula is written here (D118).
- **Every completed candle, in order.** A candle alarm keeps a cursor. Each tick evaluates, oldest first, every
  candle that closed after the cursor (or after arming), so candles missed while the watcher was down are still
  seen. It waits at a candle that has not arrived; it steps over a missing candle, saying so, only once a later
  candle is present.
- **One fire per arming.** A fire goes through ``AlarmBook.fire``, which the store makes unique per arming, so a
  restart or a repeated tick cannot fire twice. A crossing keeps the side of its level it last saw, so it survives a
  restart too.
- **Feed loss is reported, never a quiet market.** For each feed an armed alarm depends on, a quote feed with no
  update for ``quote_stale_after``, or a candle series with no candle that should have arrived ``candle_grace`` ago,
  is recorded as lost and every owning department gets a notice; its return is recorded and noticed the same way.
- **No limits** on how many alarms are watched or how often they fire (D116).
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from trade_graph.application.alarms.book import AlarmBook, FeedSeries, series_of
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.indicators.engine import compare_to_level, compute_indicator
from trade_graph.contracts.alarms import (
    AlarmDelivery,
    AlarmRecord,
    CandlePriceCondition,
    IndicatorCondition,
    QuotePriceCondition,
)
from trade_graph.contracts.candles import CandleSlot, align_close_time
from trade_graph.contracts.models import Observation
from trade_graph.domain.clock import Clock, parse_utc, utc_iso
from trade_graph.domain.errors import TradeGraphError, ValidationFailure
from trade_graph.domain.money import canonical_decimal

# One tick evaluates at most this many candles per alarm; the rest wait for the next tick. This bounds the work of
# one call (and one store read, whose own limit is 100,000 slots); it never skips a candle or limits fires.
CATCH_UP_SLOTS = 5_000
_QUOTE_KINDS = ("quote", "trade")
_REASON_LENGTH = 500
_CROSSING_SIDES = {"crosses_above": ("at_or_below", "above"), "crosses_below": ("at_or_above", "below")}


@dataclass(frozen=True, slots=True)
class WatchPolicy:
    """When a feed counts as lost. These are health thresholds, not limits on alarms or fires."""

    # The paper feed polls every 10 seconds, so two minutes without an update is a lost feed, not a pause.
    quote_stale_after: timedelta = timedelta(seconds=120)
    # A completed candle normally arrives within a minute or two of its close; ten minutes late means lost.
    candle_grace: timedelta = timedelta(minutes=10)

    def __post_init__(self) -> None:
        for name in ("quote_stale_after", "candle_grace"):
            value = getattr(self, name)
            if not isinstance(value, timedelta) or value <= timedelta(0):
                raise ValidationFailure(f"{name} must be a positive duration")


@dataclass(frozen=True, slots=True)
class WatchReport:
    """What one tick saw: fires, feed notices, feed health and the alarms that cannot see their prices."""

    at: datetime
    fired: tuple[AlarmDelivery, ...]
    feed_notices: tuple[AlarmDelivery, ...]
    candles_evaluated: int
    feeds: Mapping[str, str] = field(default_factory=dict)
    blind_alarms: tuple[str, ...] = ()
    errors: tuple[tuple[str, str], ...] = ()  # (alarm id, what failed): reported, retried next tick


@dataclass(frozen=True, slots=True)
class _Reading:
    """The newest quote a crossing alarm has seen: which side of its level, the price and its receipt time."""

    generation: int
    symbol: str
    side: str
    value: Decimal
    at: datetime


@dataclass(frozen=True, slots=True)
class _Outcome:
    satisfied: bool
    reason: str
    observed: dict[str, Any]


def _text(value: Decimal | None) -> str | None:
    return None if value is None else canonical_decimal(value)


def _relation_holds(relation: str, value: Decimal, previous: Decimal | None, level: Decimal) -> bool:
    if relation == "above":
        return value > level
    if relation == "below":
        return value < level
    if relation == "crosses_above":
        return previous is not None and previous <= level < value
    return previous is not None and previous >= level > value


class PriceWatcher:
    """Watches armed alarms; fires them through the book. Safe to call from the feed and maintenance threads."""

    def __init__(self, book: AlarmBook, store: CandleStore, clock: Clock, *,
                 policy: WatchPolicy | None = None) -> None:
        self.book = book
        self.store = store
        self.clock = clock
        self.policy = policy or WatchPolicy()
        self.started_at = clock.now()
        self._last_quote: dict[str, datetime] = {}
        self._latest: dict[str, _Reading] = {}
        self._lock = threading.RLock()

    # --- price updates ------------------------------------------------------------------------------------------

    def observe_quotes(self, observations: Iterable[Observation]) -> list[AlarmDelivery]:
        fired: list[AlarmDelivery] = []
        for observation in observations:
            fired.extend(self.observe_quote(observation))
        return fired

    def observe_quote(self, observation: Observation) -> list[AlarmDelivery]:
        """Check every armed quote alarm of the observation's symbol. Returns the deliveries of the alarms it fired."""
        if not isinstance(observation, Observation):
            raise ValidationFailure("an Observation is required")
        if observation.kind not in _QUOTE_KINDS:
            return []
        received = observation.available_at_utc
        if received.tzinfo is None or received.utcoffset() is None:
            raise ValidationFailure("an observation needs a timezone-aware receipt time")
        with self._lock:
            now = self.clock.now()
            if received > now:
                raise ValidationFailure("an observation received in the future cannot be watched")
            symbol = observation.symbol
            previous_quote = self._last_quote.get(symbol)
            if previous_quote is None or received > previous_quote:
                self._last_quote[symbol] = received
            if self.book.feed_status(FeedSeries("quote", symbol)) == "lost":
                self.book.record_feed_transition(FeedSeries("quote", symbol), "restored",
                                                 {"first_update_at": utc_iso(received)})
            fired: list[AlarmDelivery] = []
            armed = self.book.armed(basis="quote", symbol=symbol)
            for alarm in armed:
                delivery = self._quote_alarm(alarm, observation, previous_quote)
                if delivery is not None:
                    fired.append(delivery)
            current = {alarm.alarm_id for alarm in armed}
            for alarm_id in [key for key, held in self._latest.items() if held.symbol == symbol and key not in current]:
                del self._latest[alarm_id]  # spent or cancelled elsewhere
            return fired

    def _quote_alarm(self, alarm: AlarmRecord, observation: Observation,
                     previous_quote: datetime | None) -> AlarmDelivery | None:
        condition = alarm.condition
        assert isinstance(condition, QuotePriceCondition)
        value = self._price(observation, condition.price)
        received = observation.available_at_utc
        if value is None or received < parse_utc(alarm.armed_at):
            return None
        previous_value: Decimal | None = None
        previous_at: datetime | None = None
        if condition.needs_previous:
            start_side, target_side = _CROSSING_SIDES[condition.relation]
            hit = value > condition.level if condition.relation == "crosses_above" else value < condition.level
            side = target_side if hit else start_side
            held = self._held_reading(alarm)
            if held is not None and received < held.at:
                return None  # older than the reading the alarm already holds
            if held is None or held.side == side or side == start_side:
                if held is None or held.side != side:  # persist only a change of side: it survives a restart
                    self.book.set_baseline(alarm.alarm_id, alarm.generation, side=side, value=_text(value),
                                           at=utc_iso(received))
                self._latest[alarm.alarm_id] = _Reading(alarm.generation, condition.symbol, side, value, received)
                return None
            previous_value, previous_at = held.value, held.at
        elif not _relation_holds(condition.relation, value, None, condition.level):
            return None
        gap = None if previous_quote is None else (received - previous_quote).total_seconds()
        observed = {
            "basis": "quote", "observation_id": observation.observation_id, "venue": observation.venue,
            "source": observation.source, "kind": observation.kind,
            "event_time_utc": utc_iso(observation.event_time_utc), "available_at_utc": utc_iso(received),
            "price": condition.price, "value": _text(value), "bid": _text(observation.bid),
            "ask": _text(observation.ask), "last": _text(observation.last),
            "previous_value": _text(previous_value),
            "previous_at": None if previous_at is None else utc_iso(previous_at),
            "seconds_since_previous_quote": gap,
        }
        self._latest.pop(alarm.alarm_id, None)
        return self.book.fire(alarm.alarm_id, alarm.generation, observed=observed)

    def _held_reading(self, alarm: AlarmRecord) -> _Reading | None:
        """The newest reading a crossing alarm holds: in memory, or after a restart the side it last persisted."""
        latest = self._latest.get(alarm.alarm_id)
        if latest is not None and latest.generation == alarm.generation:
            return latest
        baseline = self.book.baseline(alarm.alarm_id, alarm.generation)
        if baseline is None:
            return None
        side, value, at = baseline
        return _Reading(alarm.generation, alarm.condition.symbol, side, Decimal(value), parse_utc(at))

    @staticmethod
    def _price(observation: Observation, price: str) -> Decimal | None:
        if price == "mid":
            if observation.bid is None or observation.ask is None:
                return None
            return (observation.bid + observation.ask) / Decimal(2)
        return {"bid": observation.bid, "ask": observation.ask, "last": observation.last}[price]

    # --- completed candles and feed health ----------------------------------------------------------------------

    def tick(self) -> WatchReport:
        """Evaluate every armed candle alarm on the candles completed since its cursor, then check feed health."""
        with self._lock:
            now = self.clock.now()
            fired: list[AlarmDelivery] = []
            errors: list[tuple[str, str]] = []
            evaluated = 0
            for alarm in self.book.armed(basis="candle"):
                try:
                    deliveries, count = self._candle_alarm(alarm, now)
                except TradeGraphError as error:
                    # One alarm that cannot be evaluated must not blind the others. Its cursor stays where it was,
                    # so the same candle is tried again next tick, and the failure is reported, not swallowed.
                    reason = f"evaluation failed ({error.code}): {error}"[:_REASON_LENGTH]
                    errors.append((alarm.alarm_id, reason))
                    self.book.advance(alarm.alarm_id, alarm.generation, last_reason=reason)
                    continue
                fired.extend(deliveries)
                evaluated += count
            feeds, notices, blind = self._feed_health(now)
            return WatchReport(at=now, fired=tuple(fired), feed_notices=tuple(notices), candles_evaluated=evaluated,
                               feeds=feeds, blind_alarms=tuple(blind), errors=tuple(errors))

    def _candle_alarm(self, alarm: AlarmRecord, now: datetime) -> tuple[list[AlarmDelivery], int]:
        condition = alarm.condition
        assert isinstance(condition, CandlePriceCondition | IndicatorCondition)
        step = timedelta(minutes=condition.interval_minutes)
        after = parse_utc(alarm.evaluated_through or alarm.armed_at)
        first = align_close_time(after, condition.interval_minutes) + step  # the first close strictly after
        newest = align_close_time(now, condition.interval_minutes)
        if first > newest:
            return [], 0
        last = min(newest, first + step * (CATCH_UP_SLOTS - 1))
        window = self.store.window(condition.symbol, condition.interval_minutes, condition.source,
                                   end_close_time=last, count=(last - first) // step + 1, as_of=now)
        present = [index for index, slot in enumerate(window.slots) if slot.stored is not None]
        if not present:
            return [], 0  # nothing has arrived yet; feed health says whether that is late
        evaluated = 0
        for slot in window.slots[:present[-1] + 1]:
            close = slot.close_time_utc
            if slot.stored is None:
                if not self.book.advance(alarm.alarm_id, alarm.generation, evaluated_through=utc_iso(close),
                                         last_reason=f"no candle closing at {utc_iso(close)}; a later one exists"):
                    break
                continue
            evaluated += 1
            outcome = self._evaluate(condition, slot, now)
            if outcome.satisfied:
                delivery = self.book.fire(alarm.alarm_id, alarm.generation, observed=outcome.observed)
                return ([delivery] if delivery is not None else []), evaluated
            if not self.book.advance(alarm.alarm_id, alarm.generation, evaluated_through=utc_iso(close),
                                     last_reason=outcome.reason):
                break  # cancelled or re-armed meanwhile
        return [], evaluated

    def _evaluate(self, condition: CandlePriceCondition | IndicatorCondition, slot: CandleSlot,
                  now: datetime) -> _Outcome:
        if isinstance(condition, IndicatorCondition):
            return self._evaluate_indicator(condition, slot.close_time_utc, now)
        return self._evaluate_candle_price(condition, slot, now)

    def _evaluate_candle_price(self, condition: CandlePriceCondition, slot: CandleSlot, now: datetime) -> _Outcome:
        stored = slot.stored
        assert stored is not None
        value: Decimal = getattr(stored.candle, condition.price)
        previous: Decimal | None = None
        if condition.needs_previous:
            pair = self.store.window(condition.symbol, condition.interval_minutes, condition.source,
                                     end_close_time=slot.close_time_utc, count=2, as_of=now)
            before = pair.slots[0]
            if before.stored is None:
                return _Outcome(False, "a crossing needs the previous candle; none closes at "
                                       f"{utc_iso(before.close_time_utc)}", {})
            previous = getattr(before.stored.candle, condition.price)
        satisfied = _relation_holds(condition.relation, value, previous, condition.level)
        candle = stored.candle
        observed = {
            "basis": "candle", "kind": "candle_price", "symbol": condition.symbol,
            "interval_minutes": condition.interval_minutes, "source": condition.source, "price": condition.price,
            "close_time_utc": utc_iso(slot.close_time_utc), "value": _text(value), "previous_value": _text(previous),
            "candle": {"revision": stored.revision, "run_id": stored.run_id, "content_hash": candle.content_hash,
                       "available_at_utc": utc_iso(candle.available_at_utc)},
            "as_of_utc": utc_iso(now),
        }
        return _Outcome(satisfied, "compared", observed)

    def _evaluate_indicator(self, condition: IndicatorCondition, close: datetime, now: datetime) -> _Outcome:
        result = compute_indicator(self.store, condition.indicator, condition.parameters, symbol=condition.symbol,
                                   interval_minutes=condition.interval_minutes, source=condition.source,
                                   end_close_time=close, as_of=now, count=2 if condition.needs_previous else 1)
        comparison = compare_to_level(result, condition.level, condition.relation, output=condition.output)
        if not comparison.evaluated:
            missing = len(result.missing_close_times_utc)
            detail = f" ({missing} candles missing)" if missing and result.status == "gapped" else ""
            return _Outcome(False, f"{comparison.reason}{detail}", {})
        observed = {
            "basis": "candle", "kind": "indicator", "symbol": condition.symbol,
            "interval_minutes": condition.interval_minutes, "source": condition.source,
            "close_time_utc": utc_iso(close), "value": _text(comparison.value),
            "previous_value": _text(comparison.previous_value), "level": _text(condition.level),
            "relation": condition.relation,
            "indicator": {
                "name": result.indicator, "parameters": dict(result.parameters), "output": comparison.output,
                "status": result.status, "spec_version": result.spec_version, "library": dict(result.library),
                "mode": result.mode, "as_of_utc": utc_iso(result.as_of_utc),
                "required_candles": result.required_candles, "start_dependent": result.start_dependent,
                "input_sha256": result.input_sha256,
            },
        }
        return _Outcome(comparison.satisfied, comparison.reason, observed)

    def _feed_health(self, now: datetime) -> tuple[dict[str, str], list[AlarmDelivery], list[str]]:
        watched: dict[str, tuple[FeedSeries, list[str]]] = {}
        for alarm in self.book.armed():
            series = series_of(alarm)
            watched.setdefault(series.key, (series, []))[1].append(alarm.alarm_id)
        feeds: dict[str, str] = {}
        notices: list[AlarmDelivery] = []
        blind: list[str] = []
        for key in sorted(watched):
            series, alarm_ids = watched[key]
            recorded = self.book.feed_status(series)
            if series.feed == "quote":
                alive, detail = self._quote_alive(series, now, recorded)
            else:
                alive, detail = self._candles_alive(series, now)
            if alive is None:  # no evidence either way yet: keep what is recorded
                alive = recorded != "lost"
            feeds[key] = "ok" if alive else "lost"
            if not alive:
                blind.extend(alarm_ids)
            if alive == (recorded == "lost"):  # a change: lost now and not recorded, or back after a recorded loss
                notices.extend(self.book.record_feed_transition(series, "restored" if alive else "lost", detail))
        return feeds, notices, blind

    def _quote_alive(self, series: FeedSeries, now: datetime, recorded: str | None) -> tuple[bool | None, dict]:
        last = self._last_quote.get(series.symbol)
        limit = self.policy.quote_stale_after
        if last is not None:
            detail = {"last_update_at": utc_iso(last), "stale_after_seconds": limit.total_seconds()}
            return now - last <= limit, detail
        detail = {"last_update_at": None, "watching_since": utc_iso(self.started_at),
                  "stale_after_seconds": limit.total_seconds()}
        if recorded == "lost":
            return None, detail  # still lost after a restart until an update actually arrives
        return now - self.started_at <= limit, detail

    def _candles_alive(self, series: FeedSeries, now: datetime) -> tuple[bool, dict]:
        interval = series.interval_minutes
        assert interval is not None and series.source is not None
        step = timedelta(minutes=interval)
        due = align_close_time(now - self.policy.candle_grace, interval)  # the newest close that must be here
        newest = align_close_time(now, interval)
        detail: dict[str, Any] = {"due_close_time_utc": utc_iso(due), "checked_through_utc": utc_iso(newest),
                                  "grace_seconds": self.policy.candle_grace.total_seconds()}
        try:
            window = self.store.window(series.symbol, interval, series.source, end_close_time=newest,
                                       count=(newest - due) // step + 1, as_of=now)
        except TradeGraphError as error:  # candles that cannot be read are not a quiet market either
            return False, {**detail, "read_error": f"{error.code}: {error}"[:_REASON_LENGTH]}
        return any(slot.stored is not None for slot in window.slots), detail


__all__ = ["CATCH_UP_SLOTS", "PriceWatcher", "WatchPolicy", "WatchReport"]
