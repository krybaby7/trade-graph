"""Freeze optional exact FX provenance before provider dispatch.

Legacy NULL values stay explicitly unlinked. Source bytes describe retained
runtime facts; they do not authenticate a provider or currency-rate publisher.
"""

STATEMENTS = [
    "ALTER TABLE budget_reservations ADD COLUMN fx_rate_id TEXT",
    "ALTER TABLE budget_reservations ADD COLUMN fx_rate_value TEXT",
    "ALTER TABLE budget_reservations ADD COLUMN fx_source_json TEXT",
    "ALTER TABLE usage_receipts ADD COLUMN fx_rate_id TEXT",
    "ALTER TABLE usage_receipts ADD COLUMN fx_rate_value TEXT",
    "ALTER TABLE usage_receipts ADD COLUMN fx_source_json TEXT",
]
