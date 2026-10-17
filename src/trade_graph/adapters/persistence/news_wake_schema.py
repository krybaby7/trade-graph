"""The news alarm's wake routes (migration 0042, T45; D137, D138 item 10, D116, D225-D229).

``news_alarm_wake_routes`` records, for every would-wake verdict of the news alarm (T44's ``news_alarm_verdicts``),
whom it woke or would have woken: one row per verdict, portfolio and department. The Leader is routed for every
would-wake verdict (``because`` = ``alarm``); the Trader too when the headline concerns a coin the portfolio holds
(``held_at_check``: held when Jev was asked; ``held_now``: held when the alarm was routed; ``kraken_while_holding``:
the headline is about Kraken itself while anything is held). ``mode`` says whether it was a real wake-up (``live``,
with the event engine trigger it added) or only recorded (``shadow``, no trigger): the owner's switch decides
(``OwnerPolicy.news_wakes``). ``note`` is what the woken department reads, ``reason_json`` the verdict's subjects,
holdings and Jev's answers as routed.

Rows are never changed or deleted. They are orchestration records, not financial state; nothing in them can place,
approve or block a trade. Nothing limits how many routes or wakes there are (D116).

Registration (``migrate.py``)::

    from trade_graph.adapters.persistence.news_wake_schema import migration as news_wake_migration
    STATEMENTS.append(news_wake_migration())  # 0042, W7-A (T45)
"""

from typing import Final

VERSION: Final = "0042"
TABLES: Final = ("news_alarm_wake_routes",)
# Frozen at the migration; ``application/news_wakes.py`` uses the same words and a test keeps them equal.
BECAUSE: Final = ("alarm", "held_at_check", "held_now", "kraken_while_holding")
_TRADER_BECAUSE = "('held_at_check', 'held_now', 'kraken_while_holding')"

STATEMENTS: Final[list[str]] = [
    f"""CREATE TABLE news_alarm_wake_routes (
        sequence        INTEGER PRIMARY KEY AUTOINCREMENT,
        route_id        TEXT NOT NULL UNIQUE CHECK (length(route_id) BETWEEN 1 AND 128),
        verdict_id      TEXT NOT NULL REFERENCES news_alarm_verdicts (verdict_id),
        headline_id     TEXT NOT NULL REFERENCES news_headlines (headline_id),
        portfolio_id    TEXT NOT NULL REFERENCES portfolios (portfolio_id),
        department      TEXT NOT NULL CHECK (department IN ('leader', 'trader')),
        mode            TEXT NOT NULL CHECK (mode IN ('shadow', 'live')),
        because         TEXT NOT NULL CHECK ((department = 'leader' AND because = 'alarm')
                                             OR (department = 'trader' AND because IN {_TRADER_BECAUSE})),
        rule_version    TEXT NOT NULL CHECK (length(rule_version) BETWEEN 1 AND 64),
        policy_revision TEXT CHECK (policy_revision IS NULL OR length(policy_revision) BETWEEN 1 AND 128),
        reason_json     TEXT NOT NULL CHECK (json_valid(reason_json) AND json_type(reason_json) = 'object'),
        note            TEXT NOT NULL CHECK (length(note) BETWEEN 1 AND 500),
        symbol          TEXT CHECK (symbol IS NULL OR length(symbol) BETWEEN 3 AND 33),
        source_ref      TEXT NOT NULL UNIQUE CHECK (length(source_ref) BETWEEN 1 AND 190),
        trigger_id      TEXT UNIQUE REFERENCES wake_triggers (trigger_id),
        alarm_at        TEXT NOT NULL,
        created_at      TEXT NOT NULL,
        UNIQUE (verdict_id, portfolio_id, department),
        CHECK ((mode = 'live') = (trigger_id IS NOT NULL))
    )""",
    "CREATE INDEX news_alarm_wake_routes_portfolio ON news_alarm_wake_routes (portfolio_id, alarm_at)",
    """CREATE TRIGGER news_alarm_wake_routes_no_update BEFORE UPDATE ON news_alarm_wake_routes
        BEGIN SELECT RAISE(ABORT, 'news wake routes are append-only'); END""",
    """CREATE TRIGGER news_alarm_wake_routes_no_delete BEFORE DELETE ON news_alarm_wake_routes
        BEGIN SELECT RAISE(ABORT, 'news wake routes are append-only'); END""",
]
NEWS_WAKE_STATEMENTS: Final = STATEMENTS


def migration() -> tuple[str, list[str]]:
    """The ``(version, statements)`` entry that ``migrate.STATEMENTS`` appends after ``0039``."""
    return VERSION, STATEMENTS
