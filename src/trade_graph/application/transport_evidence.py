"""Trusted gateway's private wire journal; observed HTTP is not authentication."""

from __future__ import annotations

import hashlib

from trade_graph.adapters.models.transport import HttpxProviderHttp, provider_request_bytes
from trade_graph.domain.clock import utc_iso


class TransportJournal:
    def __init__(self, budget) -> None:
        self.database, self.clock = budget.database, budget.clock

    def start(self, reservation, invocation_id, request, adapter, body, transport, synthetic) -> None:
        self.database.execute("""INSERT INTO provider_transport_attempts
            (attempt_id,reservation_id,invocation_id,provider,endpoint_sha256,request_sha256,
             transport_basis,synthetic,outcome,started_at)
            VALUES (?,?,?,?,?,?,?,?,'DISPATCH_POSSIBLE',?)""", (
                reservation, reservation, invocation_id, request.provider,
                hashlib.sha256(adapter.endpoint.encode()).hexdigest(),
                hashlib.sha256(provider_request_bytes(body)).hexdigest(),
                "protected_httpx_observation" if type(transport) is HttpxProviderHttp else "unverified_transport",
                int(synthetic), utc_iso(self.clock.now())))

    def observe(self, attempt_id: str, facts: dict) -> None:
        # Only the owner-pinned built-in transport receives this callback.
        # Custom adapters cannot promote supplied dictionaries to wire proof.
        expected = {"outcome", "status_code", "response_sha256", "response_bytes", "error_category"}
        if type(facts) is not dict:
            raise ValueError("invalid bounded protected transport observation")
        digest = facts.get("response_sha256")
        if (set(facts) != expected
                or facts["outcome"] not in {"HTTP_RESPONSE", "UNCERTAIN", "REFUSED_RESPONSE"}
                or (facts["status_code"] is not None and (type(facts["status_code"]) is not int
                                                        or not 100 <= facts["status_code"] <= 599))
                or type(facts["response_bytes"]) is not int or not 0 <= facts["response_bytes"] <= 1048576
                or facts["error_category"] not in {None, "transport", "deadline", "response_bounds"}
                or (facts["outcome"] == "HTTP_RESPONSE" and digest is None)
                or (digest is not None and (type(digest) is not str or len(digest) != 64
                                          or any(c not in "0123456789abcdef" for c in digest)))):
            raise ValueError("invalid bounded protected transport observation")
        with self.database.immediate():
            changed = self.database.execute("""UPDATE provider_transport_attempts SET outcome=?,
                status_code=?,response_sha256=?,response_bytes=?,error_category=?,finished_at=?
                WHERE attempt_id=? AND outcome='DISPATCH_POSSIBLE'""", (
                    facts["outcome"], facts["status_code"], digest, facts["response_bytes"],
                    facts["error_category"], utc_iso(self.clock.now()), attempt_id))
            if changed.rowcount != 1:
                raise ValueError("transport observation lacks its unique pending protected attempt")

    def unresolved(self, attempt_id: str, outcome="UNVERIFIED_RESPONSE") -> None:
        with self.database.immediate():
            self.database.execute("""UPDATE provider_transport_attempts SET outcome=?, finished_at=?
                WHERE attempt_id=? AND outcome='DISPATCH_POSSIBLE'""",
                (outcome, utc_iso(self.clock.now()), attempt_id))
