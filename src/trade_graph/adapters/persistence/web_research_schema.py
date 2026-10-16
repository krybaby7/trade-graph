"""Web research evidence (migration 0028, T36): runs, software-run searches and stored results.

Every table is append-only. A search call row is written in the same transaction as its
budget reservation and pre-dispatch transport intent, before the request leaves. Each
returned result becomes a ``web_search_sources`` row before the model reads it, and a
finding may cite only a ``source_ref`` stored for its own run (D124). Search text is
data: no column here is ever executed or treated as an instruction.

No key, header, request body or raw provider response is stored. Agent-step rows keep
the provider request id and Perplexity's own reported total so step L2 can compare it
with the receipt priced from the dated card.
"""

from typing import Final

VERSION: Final = "0028"

_APPEND_ONLY = ("web_research_runs", "web_research_steps", "web_search_calls", "web_search_outcomes",
                "web_search_sources", "web_research_outcomes")

STATEMENTS: Final[list[str]] = [
    """CREATE TABLE web_research_runs (
        run_id TEXT PRIMARY KEY,
        deployment_id TEXT NOT NULL,
        portfolio_id TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('research', 'leader')),
        task_id TEXT NOT NULL,
        root_task_id TEXT NOT NULL,
        system_version_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        agent_price_card_id TEXT NOT NULL,
        search_price_card_id TEXT NOT NULL,
        policy_json TEXT NOT NULL,
        question TEXT NOT NULL CHECK (length(question) BETWEEN 1 AND 2000),
        started_at TEXT NOT NULL
    )""",
    """CREATE TABLE web_research_steps (
        reservation_id TEXT PRIMARY KEY REFERENCES budget_reservations(reservation_id),
        run_id TEXT NOT NULL REFERENCES web_research_runs(run_id),
        sequence INTEGER NOT NULL CHECK (sequence >= 1),
        outcome TEXT NOT NULL CHECK (outcome IN ('tool_calls', 'output', 'failed')),
        failure TEXT,
        provider_request_id TEXT,
        provider_model TEXT,
        reported_total_cost_usd TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (run_id, sequence)
    )""",
    """CREATE TABLE web_search_calls (
        call_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES web_research_runs(run_id),
        sequence INTEGER NOT NULL CHECK (sequence >= 1),
        reservation_id TEXT NOT NULL UNIQUE REFERENCES budget_reservations(reservation_id),
        query_json TEXT NOT NULL,
        synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
        created_at TEXT NOT NULL,
        UNIQUE (run_id, sequence)
    )""",
    """CREATE TABLE web_search_outcomes (
        call_id TEXT PRIMARY KEY REFERENCES web_search_calls(call_id),
        outcome TEXT NOT NULL CHECK (outcome IN ('results', 'malformed', 'failed', 'uncertain')),
        failure TEXT,
        provider_request_id TEXT,
        result_count INTEGER NOT NULL CHECK (result_count >= 0),
        receipt_id TEXT,
        completed_at TEXT NOT NULL,
        CHECK (outcome IN ('results', 'malformed') OR receipt_id IS NULL)
    )""",
    """CREATE TABLE web_search_sources (
        source_ref TEXT PRIMARY KEY,
        call_id TEXT NOT NULL REFERENCES web_search_calls(call_id),
        run_id TEXT NOT NULL REFERENCES web_research_runs(run_id),
        rank INTEGER NOT NULL CHECK (rank BETWEEN 1 AND 20),
        url TEXT NOT NULL,
        content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
        document_json TEXT NOT NULL,
        retrieved_at TEXT NOT NULL,
        UNIQUE (call_id, rank)
    )""",
    "CREATE INDEX web_search_sources_run ON web_search_sources (run_id, call_id, rank)",
    """CREATE TABLE web_research_outcomes (
        run_id TEXT PRIMARY KEY REFERENCES web_research_runs(run_id),
        status TEXT NOT NULL CHECK (status IN ('completed', 'paused_at_limit', 'rejected', 'failed',
                                               'uncertain', 'disabled')),
        document_json TEXT NOT NULL,
        completed_at TEXT NOT NULL
    )""",
    *(statement for table in _APPEND_ONLY for statement in (
        f"""CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table}
            BEGIN SELECT RAISE(ABORT, 'web research evidence is append-only'); END""",
        f"""CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
            BEGIN SELECT RAISE(ABORT, 'web research evidence is append-only'); END""",
    )),
]
