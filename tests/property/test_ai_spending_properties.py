"""Property tests for the AI spending read model (T40) over random sequences of synthetic call outcomes.

Whatever happens to the calls, every department line adds up to the service total, the known part equals the
receipts, every unresolved call stays counted (never free) and test calls never enter the real totals.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.spending_support import Spending

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.ai_spending import spending_report
from trade_graph.domain.clock import FrozenClock

ZERO = Decimal("0")
CALL = st.tuples(
    st.sampled_from(["research", "leader", "trader", "news_alarm"]),
    st.sampled_from(["committed", "uncertain", "conservative", "reserved"]),
    st.booleans(),  # a software-run search (Perplexity only)
    st.booleans(),  # synthetic
    st.integers(min_value=0, max_value=50_000),
    st.integers(min_value=0, max_value=5_000),
)


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(CALL, max_size=14))
def test_lines_add_up_and_unknown_is_never_free(calls) -> None:
    with tempfile.TemporaryDirectory() as folder:
        database = Database(Path(folder) / "state.sqlite")
        try:
            env = Spending(database, FrozenClock(datetime(2026, 10, 16, 12, tzinfo=UTC)))
            env.run("unlimited_testing")
            expected = {"perplexity": [0, 0, 0], "jev": [0, 0, 0]}  # real calls, unresolved, synthetic
            for role, outcome, search, synthetic, input_tokens, output_tokens in calls:
                service = "jev" if role == "news_alarm" else "perplexity"
                usage = {} if search and service == "perplexity" else {"input_tokens": input_tokens,
                                                                     "output_tokens": output_tokens}
                env.call(role, outcome, service=service, search=search and service == "perplexity",
                         synthetic=synthetic, **(usage if outcome == "committed" else {}))
                counts = expected[service]
                counts[2 if synthetic else 0] += 1
                counts[1] += int(not synthetic and outcome in ("uncertain", "reserved"))
            report = spending_report(database, env.clock.now())
            for service in report.services():
                lines = list(service.departments.values())
                total = service.testing
                assert sum((line.actual for line in lines), ZERO) == total.actual
                assert sum((line.estimated for line in lines), ZERO) == total.estimated
                assert sum((line.unresolved for line in lines), ZERO) == total.unresolved
                assert sum(line.calls for line in lines) == total.calls == expected[service.service][0]
                assert total.unresolved_calls == expected[service.service][1]
                assert service.synthetic_calls == expected[service.service][2]
                assert (total.unresolved > ZERO) == (total.unresolved_calls > 0)  # held, never free
                assert total.total == total.known + total.unresolved
            roles = ("research", "leader", "trader")
            assert report.perplexity.testing.known == env.receipts_usd(roles=roles)
            assert report.jev.testing.known == env.receipts_usd(roles=("news_alarm",))
        finally:
            database.close()
