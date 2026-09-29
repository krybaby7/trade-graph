"""Trade Graph runtime. Paper mode and paid calls are disabled unless the owner configures them."""

from decimal import Decimal

__version__ = "0.1.0"

MODE = "paper"
PAID_CALLS_ENABLED = False
LIVE_ENABLED = False
CAPITAL = Decimal("10000")
CAPITAL_CURRENCY = "USD"
REPORTING_CURRENCY = "EUR"
