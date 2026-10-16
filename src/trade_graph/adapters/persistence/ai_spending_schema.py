"""The owner's AI spending records (migration 0032, T40): prepaid credit readings and testing alert amounts.

One append-only row per owner record. ``prepaid_credit`` is a balance the owner read on the provider's own
account page at ``observed_at``; HQ shows the spending recorded since then beside it. ``testing_alert`` replaces
the default testing alert amount (USD20 for Perplexity, D131; USD5 for Jev, D138 item 10). The newest record of
each kind wins; older ones stay as history.

Both are information, never a cap: nothing reads these rows to admit, refuse or pause any call (D116, D125).
A monthly money limit is owner policy (``PerplexitySpendPolicy``), not a row here. These rows are not a bill
and hold no money of the account: real costs stay in receipts and the owner's expense records.
Only the owner command ``trade-graph ai-spending-record --confirm`` writes them; no agent context reads them.
"""

from typing import Final

VERSION: Final = "0032"
TABLE: Final = "ai_spending_owner_records"

STATEMENTS: Final[list[str]] = [
    f"""CREATE TABLE {TABLE} (
        sequence     INTEGER PRIMARY KEY AUTOINCREMENT,
        record_id    TEXT NOT NULL UNIQUE,
        service      TEXT NOT NULL CHECK (service IN ('perplexity','jev')),
        kind         TEXT NOT NULL CHECK (kind IN ('prepaid_credit','testing_alert')),
        amount_usd   TEXT NOT NULL CHECK (length(amount_usd) BETWEEN 1 AND 32
                                          AND amount_usd GLOB '[0-9]*'
                                          AND amount_usd NOT GLOB '*[^0-9.]*'
                                          AND amount_usd NOT GLOB '*.*.*'
                                          AND amount_usd NOT GLOB '*.'),
        observed_at  TEXT NOT NULL,
        set_by       TEXT NOT NULL CHECK (set_by = 'owner'),
        created_at   TEXT NOT NULL,
        CHECK (kind <> 'testing_alert' OR amount_usd GLOB '*[1-9]*')
    )""",
    f"CREATE INDEX {TABLE}_latest ON {TABLE} (service, kind, sequence)",
    f"""CREATE TRIGGER {TABLE}_no_update BEFORE UPDATE ON {TABLE}
        BEGIN SELECT RAISE(ABORT, 'AI spending records are append-only'); END""",
    f"""CREATE TRIGGER {TABLE}_no_delete BEFORE DELETE ON {TABLE}
        BEGIN SELECT RAISE(ABORT, 'AI spending records are append-only'); END""",
]


def migration() -> tuple[str, list[str]]:
    """The ``(version, statements)`` entry that ``migrate.STATEMENTS`` registers."""
    return VERSION, STATEMENTS
