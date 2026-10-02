"""Kraken spot normalization and bounded read-only reconciliation foundations.

Orders remain closed by default. An explicitly enabled broker also needs an
explicitly write-enabled protected transport and independent owner/live gates.
No authenticated venue acceptance is implied by the synthetic contract tests.
"""

from __future__ import annotations

import base64
import inspect
import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import wraps

from trade_graph.adapters.brokers.kraken_transport import KrakenApiError, check_response
from trade_graph.contracts.models import (
    AuthorizedOrderIntent,
    BalanceSnapshot,
    BrokerCapabilities,
    CancelRequest,
    CancelResult,
    FillPage,
    FillRecord,
    InstrumentRules,
    OrderLookup,
    OrderLookupResult,
    OrderSnapshot,
    SubmitResult,
)
from trade_graph.domain.clock import Clock, SystemClock
from trade_graph.domain.errors import AuthorityDenied, LiveDisabled, UncertainExternal, ValidationFailure
from trade_graph.domain.money import canonical_decimal, parse_decimal

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
PAGE_SIZE = 50


def _safe_read(method):
    @wraps(method)
    async def guarded(*args, **kwargs):
        try:
            return await method(*args, **kwargs)
        except (KeyError, TypeError, ValueError, ArithmeticError):
            raise ValidationFailure("Kraken response fields could not be normalized safely") from None

    return guarded


def _decimal(value, *, positive: bool = False) -> Decimal:
    try:
        number = parse_decimal(value)
        if positive and number <= 0:
            raise ValueError("positive value required")
        return number
    except (ValueError, ArithmeticError):
        raise ValidationFailure("Kraken numeric field could not be normalized safely") from None


def _integer(value, *, minimum: int = 0) -> int:
    number = _decimal(value)
    if number != number.to_integral_value() or number < minimum:
        raise ValidationFailure("Kraken integer field could not be normalized safely")
    return int(number)


def _epoch(value: datetime) -> Decimal:
    delta = value.astimezone(UTC) - EPOCH
    return Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / 1_000_000


def _client_id(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationFailure("invalid Kraken client order identifier")
    if len(value) in {32, 36}:
        try:
            return uuid.UUID(value).hex
        except ValueError:
            pass
    if re.fullmatch(r"[A-Za-z0-9_-]{1,18}", value):
        return value
    raise ValidationFailure("invalid Kraken client order identifier")


class KrakenLiveBroker:
    def __init__(
        self,
        transport,
        *,
        live_enabled: bool = False,
        key_present: bool = False,
        account_id: str = "live",
        clock: Clock | None = None,
        symbols: list[str] | None = None,
        intent_resolver: Callable[[str | None, str], str | None] | None = None,
        history_start_utc: datetime | None = None,
        maximum_history_pages: int = 20,
        maximum_metadata_age_seconds: int = 300,
    ) -> None:
        if not 1 <= maximum_history_pages <= 100:
            raise ValueError("history page bound must be between 1 and 100")
        if not 1 <= maximum_metadata_age_seconds <= 3600:
            raise ValueError("metadata age bound must be between 1 and 3600 seconds")
        if history_start_utc is not None and history_start_utc.tzinfo is None:
            raise ValueError("history start must be timezone aware")
        self.transport, self.live_enabled, self.key_present = transport, live_enabled, key_present
        self.account_id, self.clock = account_id, clock or SystemClock()
        self.symbols, self.intent_resolver = symbols, intent_resolver
        self.history_start = _epoch(history_start_utc) if history_start_utc else Decimal("0")
        self.maximum_history_pages = maximum_history_pages
        self.maximum_metadata_age_seconds = maximum_metadata_age_seconds
        self._metadata_at: datetime | None = None
        self._fee_bound: Decimal | None = None
        self._assets: dict[str, str] = {}
        self._pairs: dict[str, str] = {}
        self._wire_pairs: dict[str, str] = {}
        self._rules: dict[str, InstrumentRules] = {}
        self.instrument_statuses: dict[str, str | None] = {}
        self._fees: dict[str, dict] = {}
        self._order_clients: dict[str, str | None] = {}
        self._history_cache: tuple[str, int, list[FillRecord]] | None = None
        self.balance_holds: dict[str, str] = {}
        self.available_balances: dict[str, str] = {}

    @property
    def fee_reserve_rate(self) -> Decimal | None:
        """No invented fee rate before metadata; cached public maximum thereafter."""
        return self._fee_bound

    async def _request(self, method: str, params: dict) -> dict:
        try:
            payload = self.transport(method, params)
            if inspect.isawaitable(payload):
                payload = await payload
            return check_response(payload)
        except KrakenApiError:
            raise
        except (TimeoutError, ConnectionError, OSError):
            raise KrakenApiError("temporary", uncertain=method in {"AddOrder", "CancelOrder"}) from None

    async def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            venue="kraken",
            mode="live",
            client_id_lookup=True,
            native_amend=False,
            native_stop=True,
            native_stop_tested=False,
            time_in_force=["gtc", "ioc"],
            reduce_only_flag=False,
            fills_pagination=True,
            cancel_behaviour="query-after-cancel; absence is uncertain",
            withdrawals=False,
        )

    def _asset(self, value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9.]{2,32}", value):
            raise ValidationFailure("Kraken asset code could not be normalized safely")
        if value in self._assets:
            return self._assets[value]
        if "." in value:
            base, suffix = value.split(".", 1)
            return self._asset(base) + "." + suffix.upper()
        return {"XBT": "BTC", "XXBT": "BTC", "XETH": "ETH", "ZUSD": "USD", "ZEUR": "EUR"}.get(
            value.upper(),
            value.upper(),
        )

    def _symbol(self, value: str) -> str:
        if value in self._pairs:
            return self._pairs[value]
        if isinstance(value, str) and value.count("/") == 1:
            base, quote = value.split("/")
            symbol = self._asset(base) + "/" + self._asset(quote)
            if symbol in self._rules:
                return symbol
        raise ValidationFailure("Kraken pair is absent from the normalized metadata registry")

    @_safe_read
    async def instruments(self) -> list[InstrumentRules]:
        assets = await self._request("Assets", {})
        aliases = {}
        for name, value in assets.items():
            if not isinstance(value, dict) or not isinstance(value.get("altname"), str):
                raise ValidationFailure("Kraken asset metadata is incomplete")
            alt = value["altname"]
            canonical = "BTC" if alt == "XBT" else alt.upper()
            aliases[name] = canonical
            aliases[alt] = canonical
        self._assets = aliases
        params = {}
        if self.symbols:
            params["pair"] = ",".join(symbol.replace("BTC", "XBT").replace("/", "") for symbol in self.symbols)
        pairs = await self._request("AssetPairs", params)
        rules, names, wire, statuses, fees = {}, {}, {}, {}, {}
        for name, info in pairs.items():
            if not isinstance(info, dict):
                raise ValidationFailure("Kraken pair metadata is malformed")
            base, quote = self._asset(info["base"]), self._asset(info["quote"])
            symbol = base + "/" + quote
            if symbol in rules:
                raise ValidationFailure("Kraken metadata contains ambiguous normalized pairs")
            price_step = (
                _decimal(info["tick_size"], positive=True)
                if info.get("tick_size") is not None
                else Decimal(10) ** -_integer(info["pair_decimals"])
            )
            quantity_step = Decimal(10) ** -_integer(info["lot_decimals"])
            if info.get("lot_multiplier") not in {None, 1, "1"}:
                raise ValidationFailure("non-unit Kraken lot multipliers require separate conformance")
            rules[symbol] = InstrumentRules(
                venue="kraken",
                symbol=symbol,
                base_asset=base,
                quote_asset=quote,
                price_increment=price_step,
                quantity_increment=quantity_step,
                min_quantity=_decimal(info["ordermin"], positive=True),
                min_notional=_decimal(info["costmin"], positive=True),
                synthetic=False,
            )
            for alias in (name, info.get("altname"), info.get("wsname"), symbol):
                if alias:
                    names[alias] = symbol
            wire[symbol] = name
            statuses[symbol] = info.get("status")
            if info.get("fees"):
                taker = max(_decimal(tier[1]) for tier in info["fees"]) / 100
                maker = max(_decimal(tier[1]) for tier in info.get("fees_maker", info["fees"])) / 100
                if min(taker, maker) < 0:
                    raise ValidationFailure("negative Kraken fee tiers require separate conformance")
                fees[symbol] = {
                    "maker": maker,
                    "taker": taker,
                    "source": "public conservative maximum",
                    "as_of": self.clock.now().isoformat(),
                }
        if not rules:
            raise ValidationFailure("Kraken returned no instrument metadata")
        self._rules, self._pairs, self._wire_pairs = rules, names, wire
        self.instrument_statuses, self._fees = statuses, fees
        self._metadata_at = self.clock.now()
        current = max((max(row["maker"], row["taker"]) for row in fees.values()), default=None)
        self._fee_bound = max(x for x in (self._fee_bound, current) if x is not None) if current is not None else None
        return list(rules.values())

    async def _metadata(self) -> None:
        if not self._rules:
            await self.instruments()

    @_safe_read
    async def fee_schedule(self, *, account_specific: bool = False) -> dict[str, dict]:
        await self._metadata()
        if account_specific:
            result = await self._request("TradeVolume", {"pair": ",".join(self._wire_pairs.values())})
            takers, makers = result.get("fees"), result.get("fees_maker", {})
            if not isinstance(takers, dict) or not isinstance(makers, dict):
                raise ValidationFailure("Kraken account fee schedule is incomplete")
            updated = {}
            for symbol, wire in self._wire_pairs.items():
                taker = _decimal(takers[wire]["fee"]) / 100
                maker = _decimal(makers.get(wire, takers[wire])["fee"]) / 100
                if min(taker, maker) < 0:
                    raise ValidationFailure("negative Kraken account fees require separate conformance")
                updated[symbol] = {
                    "maker": maker,
                    "taker": taker,
                    "source": "account TradeVolume",
                    "as_of": self.clock.now().isoformat(),
                }
            self._fees = updated
            current = max(max(row["maker"], row["taker"]) for row in updated.values())
            self._fee_bound = max(self._fee_bound or Decimal("0"), current)
        return {symbol: dict(row) for symbol, row in self._fees.items()}

    @_safe_read
    async def balances(self) -> BalanceSnapshot:
        await self._metadata()
        result = await self._request("BalanceEx", {})
        amounts = {}
        holds = {}
        for asset, value in result.items():
            if not isinstance(value, dict):
                raise ValidationFailure("Kraken balance response is malformed")
            if any(_decimal(value.get(field, "0")) != 0 for field in ("credit", "credit_used")):
                raise ValidationFailure("Kraken margin balances are outside the spot adapter")
            amount, held = _decimal(value["balance"]), _decimal(value["hold_trade"])
            if held < 0 or amount < held:
                raise ValidationFailure("Kraken held balance is inconsistent")
            canonical = self._asset(asset)
            if canonical in amounts:
                raise ValidationFailure("Kraken balances contain ambiguous duplicate asset aliases")
            amounts[canonical] = canonical_decimal(_decimal(amounts.get(canonical, "0")) + amount)
            holds[canonical] = canonical_decimal(_decimal(holds.get(canonical, "0")) + held)
        self.balance_holds = holds
        self.available_balances = {
            asset: canonical_decimal(_decimal(amount) - _decimal(holds[asset])) for asset, amount in amounts.items()
        }
        return BalanceSnapshot(venue="kraken", account_id=self.account_id, as_of_utc=self.clock.now(), amounts=amounts)

    def _order(self, txid: str, value: dict) -> OrderSnapshot:
        total, filled = _decimal(value["vol"], positive=True), _decimal(value["vol_exec"])
        if filled < 0 or filled > total:
            raise ValidationFailure("Kraken order fill quantity is inconsistent")
        status = value["status"]
        if status in {"pending", "open"}:
            normalized = "partially_filled" if filled else "open"
        elif status == "closed":
            if filled != total:
                raise ValidationFailure("Kraken closed order is not completely filled")
            normalized = "filled"
        elif status in {"canceled", "expired"}:
            normalized = "cancelled"
        else:
            raise ValidationFailure("Kraken order has an unsupported status")
        client = _client_id(value.get("cl_ord_id"))
        self._order_clients[txid] = client
        return OrderSnapshot(
            client_order_id=client or "external:" + txid,
            venue_order_id=txid,
            symbol=self._symbol(value["descr"]["pair"]),
            side=value["descr"]["type"],
            status=normalized,
            remaining_quantity=total - filled,
        )

    @_safe_read
    async def open_orders(self) -> list[OrderSnapshot]:
        await self._metadata()
        result = await self._request("OpenOrders", {"trades": "false"})
        opened = result.get("open")
        if not isinstance(opened, dict):
            raise ValidationFailure("Kraken open orders response is incomplete")
        return [self._order(txid, order) for txid, order in opened.items()]

    async def order_status(self, key: OrderLookup) -> OrderLookupResult:
        await self._metadata()
        try:
            expected_client = _client_id(key.client_order_id)
            if key.venue_order_id:
                orders = await self._request("QueryOrders", {"txid": key.venue_order_id, "trades": "false"})
            elif expected_client:
                opened = await self._request("OpenOrders", {"cl_ord_id": expected_client, "trades": "false"})
                closed = await self._request(
                    "ClosedOrders",
                    {
                        "cl_ord_id": expected_client,
                        "trades": "false",
                        "ofs": "0",
                        "consolidate_taker": "false",
                    },
                )
                if not isinstance(opened.get("open"), dict) or not isinstance(closed.get("closed"), dict):
                    raise KrakenApiError("malformed")
                orders = {**opened["open"], **closed["closed"]}
                if _integer(closed["count"]) > len(closed["closed"]):
                    return OrderLookupResult(status="unknown", error="lookup_not_supported")
            else:
                return OrderLookupResult(status="unknown", error="lookup_not_supported")
            matches = []
            for txid, value in orders.items():
                client = _client_id(value.get("cl_ord_id"))
                if key.venue_order_id and txid != key.venue_order_id:
                    continue
                if expected_client and client != expected_client:
                    continue
                order = self._order(txid, value)
                if order.symbol != self._symbol(key.symbol):
                    raise ValidationFailure("Kraken lookup symbol does not match the persisted intent")
                matches.append((order, _decimal(value["vol_exec"])))
            if len(matches) != 1:
                return OrderLookupResult(status="unknown", error="timeout_uncertain")
            order, filled = matches[0]
            return OrderLookupResult(status=order.status, filled_quantity=filled, venue_order_id=order.venue_order_id)
        except (KrakenApiError, ValidationFailure, KeyError, TypeError, ValueError):
            return OrderLookupResult(status="unknown", error="unavailable", filled_quantity="0")

    async def _history(self, end: str, expected_count: int | None = None) -> tuple[int, list[FillRecord]]:
        trades = {}
        count = None
        for page in range(self.maximum_history_pages):
            result = await self._request(
                "TradesHistory",
                {
                    "start": canonical_decimal(self.history_start),
                    "end": end,
                    "ofs": str(page * PAGE_SIZE),
                    "type": "all",
                    "consolidate_taker": "false",
                    "ledgers": "true",
                },
            )
            page_count = _integer(result["count"])
            if count is None:
                count = page_count
                if count > self.maximum_history_pages * PAGE_SIZE:
                    raise UncertainExternal("Kraken history exceeds the protected reconciliation page bound")
            if page_count != count or (expected_count is not None and count != expected_count):
                raise UncertainExternal(
                    "Kraken history changed during reconciliation; retry without replacement orders"
                )
            rows = result.get("trades")
            if not isinstance(rows, dict) or len(rows) > PAGE_SIZE or set(rows) & set(trades):
                raise ValidationFailure("Kraken history pagination is inconsistent")
            trades.update(rows)
            if len(trades) == count:
                break
            if len(rows) != PAGE_SIZE:
                raise UncertainExternal("Kraken history page is incomplete")
        if count is None or len(trades) != count:
            raise UncertainExternal("Kraken history is incomplete")
        ids = []
        for value in trades.values():
            ledger_ids = value.get("ledgers")
            if (
                not isinstance(ledger_ids, list)
                or not ledger_ids
                or any(not isinstance(x, str) for x in ledger_ids)
                or len(set(ledger_ids)) != len(ledger_ids)
            ):
                raise ValidationFailure("Kraken native fill fees require the requested ledger references")
            ids.extend(ledger_ids)
        ledgers = {}
        unique = list(dict.fromkeys(ids))
        for offset in range(0, len(unique), 20):
            ledgers.update(await self._request("QueryLedgers", {"id": ",".join(unique[offset : offset + 20])}))
        # Offset pages have no promised ordering. Sort the whole bounded frozen
        # history before applying FIFO, never feed newest sells before older buys.
        # Sort exact wire timestamps before the DTO's microsecond conversion.
        ordered = sorted(
            trades.items(),
            key=lambda item: (
                _decimal(item[1]["time"]),
                _integer(item[1].get("trade_id", "0")),
                item[0],
            ),
        )
        fills = [self._fill(txid, value, ledgers, _decimal(end)) for txid, value in ordered]
        return count, fills

    def _fill(self, txid: str, value: dict, ledgers: dict, end: Decimal) -> FillRecord:
        symbol = self._symbol(value["pair"])
        base, quote = symbol.split("/")
        quantity, price = _decimal(value["vol"], positive=True), _decimal(value["price"], positive=True)
        if _decimal(value["cost"]) != quantity * price:
            raise ValidationFailure("Kraken rounded trade cost cannot be represented by the shared fill contract")
        at = _decimal(value["time"])
        if not self.history_start <= at <= end:
            raise ValidationFailure("Kraken trade falls outside the requested point-in-time history")
        fees = {}
        legs = {}
        for ledger_id in value["ledgers"]:
            ledger = ledgers.get(ledger_id)
            if not isinstance(ledger, dict) or ledger.get("refid") != txid or ledger.get("type") != "trade":
                raise ValidationFailure("Kraken native ledger attribution is incomplete")
            amount, asset = _decimal(ledger["fee"]), self._asset(ledger["asset"])
            legs[asset] = legs.get(asset, Decimal("0")) + _decimal(ledger["amount"])
            if amount:
                fees[asset] = fees.get(asset, Decimal("0")) + amount
        sign = Decimal("1") if value["type"] == "buy" else Decimal("-1")
        if legs.get(base) != sign * quantity or legs.get(quote) != -sign * quantity * price:
            raise ValidationFailure("Kraken native trade legs disagree with the shared fill contract")
        if len(fees) > 1:
            raise ValidationFailure("multi-asset Kraken fill fees require an extended financial contract")
        asset, fee = next(iter(fees.items())) if fees else (quote, Decimal("0"))
        if fee < 0:
            raise ValidationFailure("Kraken fee rebates require separate ledger conformance")
        nominal = _decimal(value["fee"])
        if asset == quote and nominal != fee:
            raise ValidationFailure("Kraken trade and native ledger fees disagree")
        if type(value.get("maker")) is not bool:
            raise ValidationFailure("Kraken fill liquidity attribution is missing")
        order_id = value["ordertxid"]
        client = self._order_clients.get(order_id)
        intent_id = self.intent_resolver(client, order_id) if self.intent_resolver else None
        return FillRecord(
            venue="kraken",
            account_id=self.account_id,
            trade_id=txid,
            intent_id=intent_id,
            symbol=symbol,
            side=value["type"],
            quantity=quantity,
            price=price,
            fee_amount=fee,
            fee_asset=asset,
            liquidity="maker" if value["maker"] else "taker",
            filled_at_utc=EPOCH + timedelta(microseconds=int(at * 1_000_000)),
            heuristic=False,
            fee_identified_rate=nominal / fee if fee and asset not in {base, quote} else None,
        )

    @_safe_read
    async def fills_since(self, cursor: str | None) -> FillPage:
        await self._metadata()
        if cursor is None:
            end, offset, expected_count = canonical_decimal(_epoch(self.clock.now())), 0, None
        else:
            try:
                if not cursor.startswith("kraken-trades-v1:") or len(cursor) > 1024:
                    raise ValueError("unsupported cursor")
                value = json.loads(base64.urlsafe_b64decode(cursor.split(":", 1)[1]).decode())
                end, offset, expected_count = value["end"], _integer(value["offset"]), _integer(value["count"])
                if offset % PAGE_SIZE or offset >= expected_count or _decimal(end) > _epoch(self.clock.now()):
                    raise ValueError("invalid cursor")
            except (ValueError, KeyError, TypeError, ValidationFailure):
                raise ValidationFailure("invalid Kraken reconciliation cursor") from None
        cache = self._history_cache
        if cursor is None or cache is None or cache[:2] != (end, expected_count):
            count, fills = await self._history(end, expected_count)
            self._history_cache = (end, count, fills)
        else:
            _, count, fills = cache
        next_offset = offset + PAGE_SIZE
        next_cursor = None
        if next_offset < count:
            token = json.dumps({"end": end, "offset": next_offset, "count": count}, separators=(",", ":"))
            next_cursor = "kraken-trades-v1:" + base64.urlsafe_b64encode(token.encode()).decode()
        return FillPage(
            fills=[fill.model_copy(deep=True) for fill in fills[offset:next_offset]], next_cursor=next_cursor
        )

    def _writes(self) -> None:
        if not self.live_enabled or not self.key_present:
            raise LiveDisabled("live trading is not enabled")

    async def submit(self, intent: AuthorizedOrderIntent) -> SubmitResult:
        self._writes()
        # Readiness must precede execution's reservation/dispatch fee check.
        # Loading or changing fee bounds inside a write would bypass that check.
        age = None if self._metadata_at is None else (self.clock.now() - self._metadata_at).total_seconds()
        if age is None or not 0 <= age <= self.maximum_metadata_age_seconds or self.fee_reserve_rate is None:
            return SubmitResult(
                status="rejected", error="rejected", message="fresh preloaded Kraken readiness required"
            )
        if (intent.venue, intent.mode, intent.account_id) != ("kraken", "live", self.account_id):
            return SubmitResult(status="rejected", error="rejected", message="Kraken broker binding mismatch")
        if intent.order_type == "stop" or intent.stop_price is not None or intent.reduce_only:
            return SubmitResult(status="rejected", error="rejected", message="untested native protection is disabled")
        rule = self._rules.get(intent.symbol)
        if rule is None or self.instrument_statuses.get(intent.symbol) != "online" or intent.symbol not in self._fees:
            return SubmitResult(
                status="rejected", error="rejected", message="Kraken instrument/fee readiness incomplete"
            )
        if intent.quantity < rule.min_quantity or intent.quantity % rule.quantity_increment:
            return SubmitResult(
                status="rejected", error="rejected", message="Kraken quantity precision/minimum rejected"
            )
        if intent.order_type == "limit" and (
            intent.limit_price is None
            or intent.limit_price <= 0
            or intent.limit_price % rule.price_increment
            or intent.limit_price * intent.quantity < rule.min_notional
        ):
            return SubmitResult(status="rejected", error="rejected", message="Kraken price precision/minimum rejected")
        body = {
            "pair": self._wire_pairs[intent.symbol],
            "type": intent.side,
            "ordertype": intent.order_type,
            "volume": canonical_decimal(intent.quantity),
            "cl_ord_id": _client_id(intent.client_order_id),
            "timeinforce": intent.time_in_force.upper(),
            "oflags": "fciq",
        }
        if intent.limit_price is not None:
            body["price"] = canonical_decimal(intent.limit_price)
        try:
            result = await self._request("AddOrder", body)
            ids = result.get("txid")
            if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str) or not ids[0]:
                return SubmitResult(
                    status="uncertain", error="timeout_uncertain", message="Kraken order acknowledgement missing"
                )
            self._order_clients[ids[0]] = _client_id(intent.client_order_id)
            return SubmitResult(status="acknowledged", venue_order_id=ids[0])
        except KrakenApiError as exc:
            uncertain = exc.uncertain or exc.kind in {"temporary", "malformed"}
            return SubmitResult(
                status="uncertain" if uncertain else "rejected",
                error="timeout_uncertain" if uncertain else "rejected",
                message=str(exc),
            )
        except AuthorityDenied:
            return SubmitResult(
                status="rejected", error="rejected", message="Kraken protected transport refused writes"
            )

    async def cancel(self, request: CancelRequest) -> CancelResult:
        self._writes()
        body = (
            {"txid": request.venue_order_id}
            if request.venue_order_id
            else {"cl_ord_id": _client_id(request.client_order_id)}
        )
        try:
            result = await self._request("CancelOrder", body)
            if _integer(result["count"]) != 1:
                return CancelResult(
                    status="uncertain", error="timeout_uncertain", message="Kraken cancel outcome unresolved"
                )
            return CancelResult(status="cancel_pending")
        except (KrakenApiError, ValidationFailure, KeyError):
            return CancelResult(
                status="uncertain", error="timeout_uncertain", message="Kraken cancel requires reconciliation"
            )
        except AuthorityDenied:
            return CancelResult(
                status="rejected", error="rejected", message="Kraken protected transport refused writes"
            )
