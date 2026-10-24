"""Venue rounding helpers that do not depend on a broker adapter."""

from __future__ import annotations

import uuid
from decimal import Decimal


def floor_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    if increment <= 0:
        raise ValueError("increment")
    steps = (value / increment).to_integral_value(rounding="ROUND_FLOOR")
    return steps * increment


def new_client_id() -> str:
    return uuid.uuid4().hex
