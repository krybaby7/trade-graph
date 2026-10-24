"""Read-only durable intent ownership for a protected, account-bound broker."""

from __future__ import annotations

import json
import re
import uuid

from trade_graph.adapters.persistence.db import Database
from trade_graph.contracts.models import AuthorizedOrderIntent, Mode
from trade_graph.domain.errors import ValidationFailure


def _native_identity(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValidationFailure("broker ownership identity is invalid")
    return value


def _client_identity(value: object) -> str:
    client = _native_identity(value)
    if len(client) in {32, 36}:
        try:
            return uuid.UUID(client).hex
        except ValueError:
            pass
    return client


class DurableBrokerIdentity:
    """Resolve both identities consistently within one immutable broker binding.

    A supplied client identity must come from the adapter's validated unique order
    lookup. A cold terminal history can instead resolve its persisted venue ID.
    Unbound history without a verified client identity remains unowned.
    """

    def __init__(self, database: Database, *, venue: str, account_id: str, mode: Mode) -> None:
        if not all(isinstance(value, str) and 0 < len(value) <= 256 for value in (venue, account_id)):
            raise ValueError("broker ownership requires an explicit venue/account binding")
        if mode not in {"paper", "replay", "live"}:
            raise ValueError("broker ownership requires an explicit mode")
        self._database = database
        self._binding = venue, account_id, mode

    def __call__(self, client_order_id: str | None, venue_order_id: str) -> str | None:
        venue_order = _native_identity(venue_order_id)
        client = _client_identity(client_order_id) if client_order_id is not None else None
        rows = self._database.execute(
            """SELECT intent_id, portfolio_id, client_order_id, payload_json FROM order_intents
            WHERE json_extract(payload_json, '$.venue') = ?
              AND json_extract(payload_json, '$.account_id') = ?
              AND json_extract(payload_json, '$.mode') = ?""",
            self._binding,
        ).fetchall()
        by_venue, by_client = [], []
        client_counts = {}
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
                fields = {key: payload[key] for key in AuthorizedOrderIntent.model_fields if key in payload}
                intent = AuthorizedOrderIntent.model_validate(fields)
                intent_client = _client_identity(intent.client_order_id)
                stored_client = _client_identity(row["client_order_id"])
                stored_venue = payload.get("venue_order_id")
                if stored_venue is not None:
                    stored_venue = _native_identity(stored_venue)
            except (ValueError, TypeError, KeyError):
                raise ValidationFailure("durable broker ownership record is invalid") from None
            if (
                (intent.venue, intent.account_id, intent.mode) != self._binding
                or intent.intent_id != row["intent_id"]
                or intent.portfolio_id != row["portfolio_id"]
                or intent_client != stored_client
            ):
                raise ValidationFailure("durable broker ownership record is inconsistent")
            client_counts[intent_client] = client_counts.get(intent_client, 0) + 1
            if stored_venue == venue_order:
                by_venue.append((intent, intent_client))
            if client is not None and intent_client == client:
                by_client.append((intent, stored_venue))
        if len(by_venue) > 1 or len(by_client) > 1:
            raise ValidationFailure("durable broker ownership is ambiguous")
        if by_venue:
            intent, stored_client = by_venue[0]
            if client_counts[stored_client] != 1:
                raise ValidationFailure("durable broker ownership is ambiguous")
            if client is not None and client != stored_client:
                raise ValidationFailure("broker client and venue identities conflict")
            return intent.intent_id
        if by_client:
            intent, stored_venue = by_client[0]
            if stored_venue is not None and stored_venue != venue_order:
                raise ValidationFailure("broker client and venue identities conflict")
            return intent.intent_id
        return None
