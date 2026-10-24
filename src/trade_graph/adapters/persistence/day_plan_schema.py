"""Trader day plans (migration 0034, T34; D115, D117, D119, D138 item 5).

- ``day_plans``: one row per installed plan: the Trader's document and its hash, the coin, the trading day, the
  practice-range strategy version with the reports that existed when it was installed, the owner-policy and
  mandate revisions, and the earlier plan of the same coin it replaced. ``(portfolio_id, request_id)`` is unique,
  so a retried installation after a crash returns the same plan instead of resting its entries twice. Each coin of
  a portfolio has one chain of plans: a new plan must replace the coin's latest plan, and a plan is replaced once.
- ``day_plan_entries``: the resting limit buys of a plan, each bound to the authorized order intent that was
  persisted (with its reservation and outbox row) in the same transaction as the plan, before anything was sent.
- ``day_plan_exits``: the protective exits software attached to filled parts of an entry, each bound to its
  protective-exit order intent (a sell stop with an optional take-profit, one cancelling the other).
- ``day_plan_events``: what happened to a plan afterwards: installed, replaced, an entry cancelled (and why) or a
  protective exit that could not be placed yet.

Every table is append-only; amounts are exact decimal text. These are execution controls and evidence: the order
intents, fills and the ledger stay the authoritative financial state.
"""

from typing import Final

VERSION: Final = "0034"
TABLES: Final = ("day_plans", "day_plan_entries", "day_plan_exits", "day_plan_events")
EVENT_KINDS: Final = ("installed", "superseded", "entry_cancelled", "protection_deferred")
CANCEL_REASONS: Final = ("pause", "expired", "replaced")


def _plain(column: str, *, nullable: bool = False) -> str:
    """A positive plain decimal string (no sign, exponent or trailing dot)."""
    check = (f"(length({column}) BETWEEN 1 AND 48 AND {column} GLOB '[0-9]*' AND {column} NOT GLOB '*[^0-9.]*' "
             f"AND {column} NOT GLOB '*.*.*' AND {column} NOT GLOB '*.' AND {column} GLOB '*[1-9]*')")
    return f"({column} IS NULL OR {check})" if nullable else check


def _append_only(table: str, words: str) -> list[str]:
    return [f"""CREATE TRIGGER {table}_no_{action} BEFORE {action.upper()} ON {table}
        BEGIN SELECT RAISE(ABORT, '{words} are append-only'); END""" for action in ("update", "delete")]


_HEX64 = "length({0}) = 64 AND {0} NOT GLOB '*[^0-9a-f]*'"

STATEMENTS: Final[list[str]] = [
    f"""CREATE TABLE day_plans (
        sequence            INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id             TEXT NOT NULL UNIQUE CHECK (length(plan_id) = 35 AND plan_id GLOB 'dp-*'
                                                        AND substr(plan_id, 4) NOT GLOB '*[^0-9a-f]*'),
        portfolio_id        TEXT NOT NULL REFERENCES portfolios (portfolio_id),
        request_id          TEXT NOT NULL CHECK (length(request_id) BETWEEN 1 AND 128),
        symbol              TEXT NOT NULL CHECK (length(symbol) BETWEEN 3 AND 24 AND symbol GLOB '*/*'),
        trading_day_start   TEXT NOT NULL,
        trading_day_end     TEXT NOT NULL CHECK (trading_day_end > trading_day_start),
        strategy_ref        TEXT NOT NULL CHECK (length(strategy_ref) BETWEEN 5 AND 80 AND strategy_ref GLOB '*@*'),
        strategy_sha256     TEXT NOT NULL CHECK ({_HEX64.format('strategy_sha256')}),
        practice_reports    TEXT NOT NULL CHECK (json_valid(practice_reports)
                                                 AND json_type(practice_reports) = 'array'
                                                 AND json_array_length(practice_reports) >= 1),
        supersedes_plan_id  TEXT UNIQUE REFERENCES day_plans (plan_id),
        policy_revision     TEXT NOT NULL CHECK (length(policy_revision) BETWEEN 1 AND 128),
        mandate_revision    TEXT NOT NULL CHECK (length(mandate_revision) BETWEEN 1 AND 32),
        task_id             TEXT CHECK (task_id IS NULL OR length(task_id) BETWEEN 1 AND 128),
        contract_version    TEXT NOT NULL CHECK (contract_version = 'day-plan-v1'),
        document_json       TEXT NOT NULL CHECK (json_valid(document_json)),
        document_sha256     TEXT NOT NULL CHECK ({_HEX64.format('document_sha256')}),
        installed_at        TEXT NOT NULL CHECK (installed_at >= trading_day_start
                                                 AND installed_at < trading_day_end),
        UNIQUE (portfolio_id, request_id)
    )""",
    """CREATE TRIGGER day_plans_replace_the_same_coin BEFORE INSERT ON day_plans
        WHEN NEW.supersedes_plan_id IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'a plan replaces an earlier plan of the same portfolio and coin')
        WHERE NOT EXISTS (SELECT 1 FROM day_plans WHERE plan_id = NEW.supersedes_plan_id
                          AND portfolio_id = NEW.portfolio_id AND symbol = NEW.symbol); END""",
    """CREATE TRIGGER day_plans_one_chain_per_coin BEFORE INSERT ON day_plans
        BEGIN SELECT RAISE(ABORT, 'a new plan replaces the coin''s latest plan')
        WHERE EXISTS (SELECT 1 FROM day_plans p WHERE p.portfolio_id = NEW.portfolio_id AND p.symbol = NEW.symbol
                      AND p.plan_id <> COALESCE(NEW.supersedes_plan_id, '')
                      AND NOT EXISTS (SELECT 1 FROM day_plans n WHERE n.supersedes_plan_id = p.plan_id)); END""",
    *_append_only("day_plans", "day plans"),
    f"""CREATE TABLE day_plan_entries (
        sequence            INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id            TEXT NOT NULL UNIQUE CHECK (length(entry_id) BETWEEN 38 AND 48),
        plan_id             TEXT NOT NULL REFERENCES day_plans (plan_id),
        position            INTEGER NOT NULL CHECK (position BETWEEN 0 AND 63),
        intent_id           TEXT NOT NULL UNIQUE REFERENCES order_intents (intent_id),
        decision_id         TEXT NOT NULL UNIQUE,
        quantity            TEXT NOT NULL CHECK {_plain('quantity')},
        limit_price         TEXT NOT NULL CHECK {_plain('limit_price')},
        stop_price          TEXT NOT NULL CHECK {_plain('stop_price')},
        take_profit_price   TEXT CHECK {_plain('take_profit_price', nullable=True)},
        expires_at          TEXT NOT NULL,
        created_at          TEXT NOT NULL CHECK (expires_at > created_at),
        UNIQUE (plan_id, position)
    )""",
    """CREATE TRIGGER day_plan_entries_are_buy_limits_of_the_plan BEFORE INSERT ON day_plan_entries
        BEGIN SELECT RAISE(ABORT, 'a day-plan entry is a resting limit buy of the plan''s portfolio and coin')
        WHERE NOT EXISTS (
            SELECT 1 FROM order_intents i JOIN day_plans p ON p.plan_id = NEW.plan_id
            WHERE i.intent_id = NEW.intent_id AND i.portfolio_id = p.portfolio_id AND i.symbol = p.symbol
              AND json_extract(i.payload_json, '$.side') = 'buy'
              AND json_extract(i.payload_json, '$.order_type') = 'limit'); END""",
    *_append_only("day_plan_entries", "day-plan entries"),
    f"""CREATE TABLE day_plan_exits (
        sequence            INTEGER PRIMARY KEY AUTOINCREMENT,
        exit_id             TEXT NOT NULL UNIQUE CHECK (length(exit_id) BETWEEN 1 AND 64),
        entry_id            TEXT NOT NULL REFERENCES day_plan_entries (entry_id),
        intent_id           TEXT NOT NULL UNIQUE REFERENCES order_intents (intent_id),
        quantity            TEXT NOT NULL CHECK {_plain('quantity')},
        stop_price          TEXT NOT NULL CHECK {_plain('stop_price')},
        take_profit_price   TEXT CHECK {_plain('take_profit_price', nullable=True)},
        cause               TEXT NOT NULL CHECK (cause IN ('fill', 'sweep')),
        fill_trade_id       TEXT CHECK (fill_trade_id IS NULL OR length(fill_trade_id) BETWEEN 1 AND 256),
        created_at          TEXT NOT NULL,
        CHECK ((cause = 'fill') = (fill_trade_id IS NOT NULL))
    )""",
    """CREATE TRIGGER day_plan_exits_are_protective_sells_of_the_entry BEFORE INSERT ON day_plan_exits
        BEGIN SELECT RAISE(ABORT, 'a day-plan exit is a protective sell of the entry''s portfolio and coin')
        WHERE NOT EXISTS (
            SELECT 1 FROM order_intents i JOIN day_plan_entries e ON e.entry_id = NEW.entry_id
                     JOIN order_intents b ON b.intent_id = e.intent_id
            WHERE i.intent_id = NEW.intent_id AND i.portfolio_id = b.portfolio_id AND i.symbol = b.symbol
              AND json_extract(i.payload_json, '$.side') = 'sell'
              AND json_extract(i.payload_json, '$.order_type') = 'stop'
              AND json_extract(i.payload_json, '$.protective_exit.entry_id') = NEW.entry_id); END""",
    *_append_only("day_plan_exits", "day-plan exits"),
    f"""CREATE TABLE day_plan_events (
        sequence            INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id             TEXT NOT NULL REFERENCES day_plans (plan_id),
        entry_id            TEXT REFERENCES day_plan_entries (entry_id),
        kind                TEXT NOT NULL CHECK (kind IN {EVENT_KINDS!r}),
        reason              TEXT CHECK (reason IS NULL OR length(reason) BETWEEN 1 AND 500),
        document_json       TEXT NOT NULL DEFAULT '{{}}' CHECK (json_valid(document_json)),
        created_at          TEXT NOT NULL,
        CHECK (kind <> 'entry_cancelled' OR (entry_id IS NOT NULL AND reason IN {CANCEL_REASONS!r})),
        CHECK (kind NOT IN ('installed', 'superseded') OR entry_id IS NULL)
    )""",
    """CREATE UNIQUE INDEX day_plan_events_once ON day_plan_events (plan_id, kind)
        WHERE kind IN ('installed', 'superseded')""",
    """CREATE UNIQUE INDEX day_plan_entry_cancelled_once ON day_plan_events (entry_id)
        WHERE kind = 'entry_cancelled'""",
    *_append_only("day_plan_events", "day-plan events"),
]


def migration() -> tuple[str, list[str]]:
    """The ``(version, statements)`` entry that ``migrate.STATEMENTS`` registers."""
    return VERSION, STATEMENTS
