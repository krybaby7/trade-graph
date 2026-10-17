"""The Trader's position actions on day-plan positions (migration 0044, W8-B / C4; D245-D249).

- ``day_plan_actions``: one row per action the Trader asked for: close the coin's day-plan position now, or move its
  safety exit to a new stop and optional take-profit; the request, its document and hash, and the owner-policy and
  mandate revisions in force. ``(portfolio_id, request_id)`` is unique, so a retried request returns the same action.
  A coin of a portfolio has at most one action still running.
- ``day_plan_action_steps``: the recorded sequence of one action: the intents it is about to cancel (written before
  any cancel request), what reconciliation said, the market sell or the new exits it placed, the old exits it put
  back after a refused or unknown step, and exactly one end (``done``, ``refused`` or ``failed``) after which
  nothing more is recorded.
- ``day_plan_action_orders``: the orders an action placed, per day-plan entry: a close's market sell (one sell may
  cover several entries, each with its part), a move's new exits, and old exits put back.

The index ``valuation_marks_newest`` serves every "newest mark of a coin" read (execution, the daily loss limit,
HQ, the scorecard), which scanned the whole table before (W5-C's finding).

Every table is append-only; amounts are exact decimal text. The order intents, fills and the ledger stay the
authoritative financial state; these tables are execution controls and evidence.
"""

from typing import Final

VERSION: Final = "0044"
TABLES: Final = ("day_plan_actions", "day_plan_action_steps", "day_plan_action_orders")
ACTIONS: Final = ("close", "move_exit")
STEP_KINDS: Final = ("cancelling", "cancelled", "unsettled", "sell_placed", "sell_sent", "exits_attached",
                     "exits_restored", "done", "refused", "failed")
TERMINAL_STEPS: Final = ("done", "refused", "failed")
ORDER_ROLES: Final = ("sell", "moved_exit", "restored_exit")
VALUATION_MARK_INDEX: Final = "valuation_marks_newest"


def _plain(column: str, *, nullable: bool = False) -> str:
    """A positive plain decimal string (no sign, exponent or trailing dot)."""
    check = (f"(length({column}) BETWEEN 1 AND 48 AND {column} GLOB '[0-9]*' AND {column} NOT GLOB '*[^0-9.]*' "
             f"AND {column} NOT GLOB '*.*.*' AND {column} NOT GLOB '*.' AND {column} GLOB '*[1-9]*')")
    return f"({column} IS NULL OR {check})" if nullable else check


def _append_only(table: str, words: str) -> list[str]:
    return [f"""CREATE TRIGGER {table}_no_{action} BEFORE {action.upper()} ON {table}
        BEGIN SELECT RAISE(ABORT, '{words} are append-only'); END""" for action in ("update", "delete")]


_HEX64 = "length({0}) = 64 AND {0} NOT GLOB '*[^0-9a-f]*'"
_TERMINAL = repr(TERMINAL_STEPS)

STATEMENTS: Final[list[str]] = [
    f"""CREATE TABLE day_plan_actions (
        sequence            INTEGER PRIMARY KEY AUTOINCREMENT,
        action_id           TEXT NOT NULL UNIQUE CHECK (length(action_id) = 35 AND action_id GLOB 'da-*'
                                                        AND substr(action_id, 4) NOT GLOB '*[^0-9a-f]*'),
        portfolio_id        TEXT NOT NULL REFERENCES portfolios (portfolio_id),
        request_id          TEXT NOT NULL CHECK (length(request_id) BETWEEN 1 AND 128),
        symbol              TEXT NOT NULL CHECK (length(symbol) BETWEEN 3 AND 24 AND symbol GLOB '*/*'),
        action              TEXT NOT NULL CHECK (action IN {ACTIONS!r}),
        stop_price          TEXT CHECK {_plain('stop_price', nullable=True)},
        take_profit_price   TEXT CHECK {_plain('take_profit_price', nullable=True)},
        task_id             TEXT CHECK (task_id IS NULL OR length(task_id) BETWEEN 1 AND 128),
        policy_revision     TEXT CHECK (policy_revision IS NULL OR length(policy_revision) BETWEEN 1 AND 128),
        mandate_revision    TEXT CHECK (mandate_revision IS NULL OR length(mandate_revision) BETWEEN 1 AND 32),
        document_json       TEXT NOT NULL CHECK (json_valid(document_json)),
        document_sha256     TEXT NOT NULL CHECK ({_HEX64.format('document_sha256')}),
        requested_at        TEXT NOT NULL,
        UNIQUE (portfolio_id, request_id),
        CHECK ((action = 'close') = (stop_price IS NULL)),
        CHECK (action = 'move_exit' OR take_profit_price IS NULL)
    )""",
    f"""CREATE TABLE day_plan_action_steps (
        sequence            INTEGER PRIMARY KEY AUTOINCREMENT,
        action_id           TEXT NOT NULL REFERENCES day_plan_actions (action_id),
        kind                TEXT NOT NULL CHECK (kind IN {STEP_KINDS!r}),
        reason              TEXT CHECK (reason IS NULL OR length(reason) BETWEEN 1 AND 500),
        document_json       TEXT NOT NULL DEFAULT '{{}}' CHECK (json_valid(document_json)),
        created_at          TEXT NOT NULL,
        CHECK (kind NOT IN ('refused', 'failed', 'unsettled') OR reason IS NOT NULL)
    )""",
    f"""CREATE UNIQUE INDEX day_plan_action_ends_once ON day_plan_action_steps (action_id)
        WHERE kind IN {_TERMINAL}""",
    """CREATE UNIQUE INDEX day_plan_action_places_once ON day_plan_action_steps (action_id, kind)
        WHERE kind IN ('sell_placed', 'exits_attached')""",
    f"""CREATE TRIGGER day_plan_action_steps_end_last BEFORE INSERT ON day_plan_action_steps
        BEGIN SELECT RAISE(ABORT, 'a finished action records no further step')
        WHERE EXISTS (SELECT 1 FROM day_plan_action_steps WHERE action_id = NEW.action_id
                      AND kind IN {_TERMINAL}); END""",
    f"""CREATE TRIGGER day_plan_actions_one_running_per_coin BEFORE INSERT ON day_plan_actions
        BEGIN SELECT RAISE(ABORT, 'an earlier action for this coin is still running')
        WHERE EXISTS (SELECT 1 FROM day_plan_actions a WHERE a.portfolio_id = NEW.portfolio_id
                      AND a.symbol = NEW.symbol AND NOT EXISTS (
                          SELECT 1 FROM day_plan_action_steps s WHERE s.action_id = a.action_id
                          AND s.kind IN {_TERMINAL})); END""",
    *_append_only("day_plan_actions", "day-plan actions"),
    *_append_only("day_plan_action_steps", "day-plan action steps"),
    f"""CREATE TABLE day_plan_action_orders (
        sequence            INTEGER PRIMARY KEY AUTOINCREMENT,
        action_id           TEXT NOT NULL REFERENCES day_plan_actions (action_id),
        entry_id            TEXT NOT NULL REFERENCES day_plan_entries (entry_id),
        intent_id           TEXT NOT NULL REFERENCES order_intents (intent_id),
        role                TEXT NOT NULL CHECK (role IN {ORDER_ROLES!r}),
        quantity            TEXT NOT NULL CHECK {_plain('quantity')},
        created_at          TEXT NOT NULL,
        UNIQUE (action_id, entry_id, intent_id)
    )""",
    """CREATE TRIGGER day_plan_action_sells_are_market_sells_of_the_coin BEFORE INSERT ON day_plan_action_orders
        WHEN NEW.role = 'sell'
        BEGIN SELECT RAISE(ABORT, 'a close sells the entry''s coin at the market')
        WHERE NOT EXISTS (
            SELECT 1 FROM order_intents i JOIN day_plan_entries e ON e.entry_id = NEW.entry_id
                     JOIN order_intents b ON b.intent_id = e.intent_id
                     JOIN day_plan_actions a ON a.action_id = NEW.action_id
            WHERE i.intent_id = NEW.intent_id AND i.portfolio_id = b.portfolio_id AND i.symbol = b.symbol
              AND a.portfolio_id = b.portfolio_id AND a.symbol = b.symbol AND a.action = 'close'
              AND json_extract(i.payload_json, '$.side') = 'sell'
              AND json_extract(i.payload_json, '$.order_type') = 'market'); END""",
    """CREATE TRIGGER day_plan_action_exits_are_day_plan_exits_of_the_entry BEFORE INSERT ON day_plan_action_orders
        WHEN NEW.role <> 'sell'
        BEGIN SELECT RAISE(ABORT, 'an action attaches a day-plan exit of the entry''s coin')
        WHERE NOT EXISTS (
            SELECT 1 FROM day_plan_exits x JOIN order_intents i ON i.intent_id = x.intent_id
                     JOIN day_plan_actions a ON a.action_id = NEW.action_id
            WHERE x.intent_id = NEW.intent_id AND x.entry_id = NEW.entry_id AND i.portfolio_id = a.portfolio_id
              AND i.symbol = a.symbol AND (NEW.role = 'restored_exit' OR a.action = 'move_exit')); END""",
    *_append_only("day_plan_action_orders", "day-plan action orders"),
    f"CREATE INDEX {VALUATION_MARK_INDEX} ON valuation_marks (portfolio_id, asset, observed_at)",
]


def migration() -> tuple[str, list[str]]:
    """The ``(version, statements)`` entry that ``migrate.STATEMENTS`` registers."""
    return VERSION, STATEMENTS
