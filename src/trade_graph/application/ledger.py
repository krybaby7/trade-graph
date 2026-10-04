"""Durable ledger. Calculations come from kernel books replayed from append-only events."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict
from decimal import Context, Decimal, Inexact, localcontext

from trade_graph.adapters.persistence.db import Database
from trade_graph.contracts.models import FillRecord
from trade_graph.domain.clock import Clock, parse_utc, utc_iso
from trade_graph.domain.errors import DuplicateRecord, StaleState, ValidationFailure
from trade_graph.domain.money import canonical_decimal
from trade_graph.kernel.books import (
    Books,
    EquityView,
    Expense,
    FxRate,
    Mark,
    Performance,
    Posting,
    _apply_group,
    _convert,
    add_expense,
    apply_fill,
    deposit,
    internal_transfer,
    mark_equity,
    performance,
    withdraw,
)


def _json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


REPLAY_KIND = "fill_chronological_replay"
REPLAY_VERSION = "chronological_replay_v1"
MAX_REPLAY_ROWS = 4096
MAX_REPLAY_SOURCE_BYTES = 8 * 1024 * 1024


def _projection_sha256(books: Books) -> str:
    def native(value):
        if isinstance(value, Decimal):
            return canonical_decimal(value)
        if isinstance(value, dict):
            return {key: native(item) for key, item in value.items()}
        if isinstance(value, list):
            return [native(item) for item in value]
        return value

    projection = native(
        {
            "cash": books.cash,
            "lots": [asdict(item) for item in books.lots],
            "flows": [asdict(item) for item in books.flows],
            "expenses": [asdict(item) for item in books.expenses],
        }
    )
    return hashlib.sha256(_json(projection).encode()).hexdigest()


def _source_manifest(rows) -> list[dict]:
    if len(rows) > MAX_REPLAY_ROWS or sum(len(row["payload_json"].encode()) for row in rows) > MAX_REPLAY_SOURCE_BYTES:
        raise ValidationFailure("chronological replay exceeds protected source bounds")
    fields = ("event_id", "portfolio_id", "sequence", "kind", "payload_json", "effective_at", "external_ref")
    return [
        {
            "event_id": row["event_id"],
            "sha256": hashlib.sha256(
                _json({key: row[key] for key in fields}).encode(),
            ).hexdigest(),
        }
        for row in rows
    ]


def _native_totals(books: Books) -> dict[tuple[str, str], Decimal]:
    totals = {}
    for group in books.groups:
        for posting in group:
            key = posting.account, posting.asset
            totals[key] = totals.get(key, Decimal("0")) + posting.amount
    return totals


class Ledger:
    def __init__(self, database: Database, clock: Clock) -> None:
        self.database = database
        self.clock = clock

    def now(self) -> str:
        return utc_iso(self.clock.now())

    def create_portfolio(
        self,
        *,
        reporting_currency: str,
        mode: str = "paper",
        experiment_id: str | None = None,
        reset_of: str | None = None,
        portfolio_id: str | None = None,
    ) -> str:
        portfolio_id = portfolio_id or str(uuid.uuid4())
        experiment_id = experiment_id or portfolio_id
        with self.database.transaction() as conn:
            conn.execute(
                """INSERT INTO portfolios
                (portfolio_id, mode, reporting_currency, experiment_id, status, reset_of, created_at)
                VALUES (?, ?, ?, ?, 'open', ?, ?)""",
                (portfolio_id, mode, reporting_currency, experiment_id, reset_of, self.now()),
            )
        self._activity(portfolio_id, "portfolio_opened", {"reset_of": reset_of, "mode": mode})
        return portfolio_id

    def deposit(self, portfolio_id: str, asset: str, amount: Decimal, ref: str) -> None:
        self._append(portfolio_id, "deposit", {"asset": asset, "amount": canonical_decimal(amount)}, ref)

    def withdraw(self, portfolio_id: str, asset: str, amount: Decimal, ref: str) -> None:
        self._append(portfolio_id, "withdraw", {"asset": asset, "amount": canonical_decimal(amount)}, ref)

    def internal_transfer(self, portfolio_id: str, asset: str, amount: Decimal, ref: str) -> None:
        self._append(
            portfolio_id,
            "internal",
            {"asset": asset, "amount": canonical_decimal(amount)},
            ref,
        )

    def apply_fill(
        self,
        portfolio_id: str,
        fill: FillRecord,
        *,
        base_asset: str,
        quote_asset: str,
    ) -> str:
        lot_id = str(uuid.uuid4())
        payload = {
            "fill": fill.model_dump(mode="json"),
            "base_asset": base_asset,
            "quote_asset": quote_asset,
            "lot_id": lot_id,
        }
        ref = f"{fill.venue}:{fill.account_id}:{fill.trade_id}"
        self._append(portfolio_id, "fill", payload, ref)
        return lot_id

    def apply_late_fill(
        self,
        portfolio_id: str,
        fill: FillRecord,
        *,
        base_asset: str,
        quote_asset: str,
    ) -> str:
        """Append the late original fact and a verified restatement atomically."""
        at = self.now()
        if (
            fill.filled_at_utc.tzinfo is None
            or fill.filled_at_utc > parse_utc(at)
            or fill.symbol != f"{base_asset}/{quote_asset}"
        ):
            raise ValidationFailure("late fill has no valid bounded native time or asset scope")
        lot_id = str(uuid.uuid4())
        ref = f"{fill.venue}:{fill.account_id}:{fill.trade_id}"
        payload = {
            "fill": fill.model_dump(mode="json"),
            "base_asset": base_asset,
            "quote_asset": quote_asset,
            "lot_id": lot_id,
            "projection_deferred": REPLAY_VERSION,
        }
        with self.database.immediate() as conn:
            budget = conn.execute(
                """SELECT count(*) AS n,COALESCE(sum(length(CAST(payload_json AS BLOB))),0) AS bytes,
                max(effective_at) AS latest FROM ledger_events WHERE portfolio_id=?""",
                (portfolio_id,),
            ).fetchone()
            if (
                budget["n"] + 1 > MAX_REPLAY_ROWS
                or budget["bytes"] + len(_json(payload).encode()) > MAX_REPLAY_SOURCE_BYTES
            ):
                raise ValidationFailure("chronological replay exceeds protected source bounds")
            if budget["latest"] is not None and parse_utc(budget["latest"]) >= parse_utc(at):
                raise ValidationFailure(
                    "late fill requires chronological ledger replay: "
                    "record time must strictly follow known native sources"
                )
            rows = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM ledger_events WHERE portfolio_id=? ORDER BY sequence LIMIT ?",
                    (portfolio_id, MAX_REPLAY_ROWS),
                )
            ]
            if any(row["external_ref"] == ref for row in rows):
                raise DuplicateRecord(ref)
            before = self._replay_rows(rows)
            new = {
                "event_id": str(uuid.uuid4()),
                "portfolio_id": portfolio_id,
                "sequence": len(rows) + 1,
                "kind": "fill",
                "payload_json": _json(payload),
                "effective_at": at,
                "external_ref": ref,
            }
            sources = rows + [new]
            manifest = _source_manifest(sources)
            # A new native tie cannot prove its relative execution ordering from
            # the truncated shared timestamp. Existing stable ties stay stable.
            if any(
                row["kind"] == "fill"
                and FillRecord.model_validate(
                    json.loads(row["payload_json"])["fill"],
                ).filled_at_utc
                == fill.filled_at_utc
                for row in rows
            ):
                raise ValidationFailure("late fill requires chronological ledger replay with precise tie evidence")
            try:
                corrected = self._chronological_books(sources)
                with localcontext(Context(prec=28)) as context:
                    context.traps[Inexact] = True
                    old_totals, new_totals = _native_totals(before), _native_totals(corrected)
                    delta = [
                        Posting(
                            account,
                            asset,
                            new_totals.get((account, asset), Decimal("0"))
                            - old_totals.get((account, asset), Decimal("0")),
                        )
                        for account, asset in sorted(old_totals.keys() | new_totals.keys())
                        if new_totals.get((account, asset), Decimal("0"))
                        != old_totals.get((account, asset), Decimal("0"))
                    ]
            except (ArithmeticError, ValueError):
                raise ValidationFailure(
                    "late fill requires chronological ledger replay with valid native sources"
                ) from None
            correction = {
                "schema_version": 1,
                "projection_version": REPLAY_VERSION,
                "source_manifest": manifest,
                "previous_projection_sha256": _projection_sha256(before),
                "current_projection_sha256": _projection_sha256(corrected),
                "new_fill_event_id": new["event_id"],
                "postings": [
                    {"account": item.account, "asset": item.asset, "amount": canonical_decimal(item.amount)}
                    for item in delta
                ],
            }
            correction_ref = f"chronological-replay:{new['event_id']}"
            receipt = {
                "event_id": str(uuid.uuid4()),
                "portfolio_id": portfolio_id,
                "sequence": len(sources) + 1,
                "kind": REPLAY_KIND,
                "payload_json": _json(correction),
                "effective_at": at,
                "external_ref": correction_ref,
            }
            # Verify the exact receipt through the same reader before committing.
            self._replay_row(before, receipt, sources)
            for event in (new, receipt):
                conn.execute("INSERT INTO ledger_events VALUES (?,?,?,?,?,?,?)", tuple(event.values()))
            if delta:
                txn = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO journal_transactions VALUES (?,?,?,?,?)",
                    (txn, portfolio_id, REPLAY_KIND, correction_ref, at),
                )
                conn.executemany(
                    "INSERT INTO journal_postings VALUES (?,?,?,?,?,?)",
                    [
                        (str(uuid.uuid4()), txn, portfolio_id, item.account, item.asset, canonical_decimal(item.amount))
                        for item in delta
                    ],
                )
        return lot_id

    def add_expense(
        self,
        portfolio_id: str,
        *,
        expense_id: str,
        native_amount: Decimal,
        native_currency: str,
        reporting_amount: Decimal,
        reporting_currency: str,
        embedded: bool,
        source: str,
        settles: str | None = None,
    ) -> None:
        payload = {
            "expense_id": expense_id,
            "native_amount": canonical_decimal(native_amount),
            "native_currency": native_currency,
            "reporting_amount": canonical_decimal(reporting_amount),
            "reporting_currency": reporting_currency,
            "embedded": embedded,
            "source": source,
            "settles": settles,
        }
        self._append(portfolio_id, "expense", payload, expense_id)

    def observe_fx(
        self,
        *,
        base: str,
        quote: str,
        rate: Decimal,
        source: str,
        kind: str,
        stale: bool,
        rate_id: str | None = None,
    ) -> str:
        rate_id = rate_id or str(uuid.uuid4())
        now = self.now()
        with self.database.transaction() as conn:
            conn.execute(
                """INSERT INTO fx_rates
                (rate_id, base, quote, rate, source, observed_at, valid_as_of, retrieved_at, kind, stale)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rate_id,
                    base,
                    quote,
                    canonical_decimal(rate),
                    source,
                    now,
                    now,
                    now,
                    kind,
                    1 if stale else 0,
                ),
            )
        return rate_id

    def observe_mark(
        self,
        portfolio_id: str,
        asset: str,
        price: Decimal,
        quote: str,
        *,
        source: str,
        stale: bool = False,
        convention: str = "mid",
    ) -> str:
        mark_id = str(uuid.uuid4())
        now = self.now()
        with self.database.transaction() as conn:
            conn.execute(
                """INSERT INTO valuation_marks
                (mark_id, portfolio_id, asset, quote_currency, mark, convention, observed_at, stale, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    mark_id,
                    portfolio_id,
                    asset,
                    quote,
                    canonical_decimal(price),
                    convention,
                    now,
                    1 if stale else 0,
                    source,
                ),
            )
        return mark_id

    def books(self, portfolio_id: str, at: str | None = None) -> Books:
        return self._books(portfolio_id, at or self.now())

    def equity(self, portfolio_id: str, at: str | None = None) -> EquityView:
        at = at or self.now()
        books = self._books(portfolio_id, at)
        reporting = self._reporting(portfolio_id)
        return mark_equity(
            books,
            reporting=reporting,
            marks=self._marks(portfolio_id, at),
            rates=self._rates(at),
            at=at,
        )

    def reporting_value(self, portfolio_id: str, amount: Decimal, currency: str) -> Decimal:
        at = self.now()
        value, stale = _convert(amount, currency, self._reporting(portfolio_id), self._rates(at), at)
        if value is None or stale:
            raise StaleState(f"missing or stale reporting FX for {currency}")
        return value

    def performance(self, portfolio_id: str, start: str, end: str) -> Performance:
        reporting = self._reporting(portfolio_id)
        start_books = self._books(portfolio_id, start)
        end_books = self._books(portfolio_id, end)
        start_view = mark_equity(
            start_books,
            reporting=reporting,
            marks=self._marks(portfolio_id, start),
            rates=self._rates(start),
            at=start,
        )
        end_view = mark_equity(
            end_books,
            reporting=reporting,
            marks=self._marks(portfolio_id, end),
            rates=self._rates(end),
            at=end,
        )
        return performance(
            end_books,
            reporting=reporting,
            marks=self._marks(portfolio_id, end),
            rates=self._rates(end),
            start=start,
            end=end,
            start_view=start_view,
            end_view=end_view,
        )

    def journal_balanced(self, portfolio_id: str) -> bool:
        rows = self.database.execute(
            """SELECT asset, amount FROM journal_postings WHERE portfolio_id = ?""",
            (portfolio_id,),
        ).fetchall()
        totals: dict[str, Decimal] = {}
        for row in rows:
            totals[row["asset"]] = totals.get(row["asset"], Decimal("0")) + Decimal(row["amount"])
        return all(total == 0 for total in totals.values())

    def _append(self, portfolio_id: str, kind: str, payload: dict, external_ref: str) -> None:
        at = self.now()
        try:
            with self.database.transaction() as conn:
                duplicate = conn.execute(
                    "SELECT 1 FROM ledger_events WHERE portfolio_id = ? AND external_ref = ?",
                    (portfolio_id, external_ref),
                ).fetchone()
                if duplicate is not None:
                    raise DuplicateRecord(external_ref)
                books = self._books_conn(conn, portfolio_id, "9999")
                before = len(books.groups)
                self._mutate(books, kind, payload, at, external_ref)
                new_groups = books.groups[before:]
                sequence_row = conn.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 AS n FROM ledger_events WHERE portfolio_id = ?",
                    (portfolio_id,),
                ).fetchone()
                event_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO ledger_events
                    (event_id, portfolio_id, sequence, kind, payload_json, effective_at, external_ref)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (event_id, portfolio_id, sequence_row["n"], kind, _json(payload), at, external_ref),
                )
                for group in new_groups:
                    txn_id = str(uuid.uuid4())
                    conn.execute(
                        """INSERT INTO journal_transactions
                        (transaction_id, portfolio_id, kind, external_ref, created_at)
                        VALUES (?, ?, ?, ?, ?)""",
                        (txn_id, portfolio_id, kind, external_ref, at),
                    )
                    for posting in group:
                        conn.execute(
                            """INSERT INTO journal_postings
                            (posting_id, transaction_id, portfolio_id, account, asset, amount)
                            VALUES (?, ?, ?, ?, ?, ?)""",
                            (
                                str(uuid.uuid4()),
                                txn_id,
                                portfolio_id,
                                posting.account,
                                posting.asset,
                                canonical_decimal(posting.amount),
                            ),
                        )
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise DuplicateRecord(external_ref) from exc
            raise

    def _mutate(self, books: Books, kind: str, payload: dict, at: str, ref: str) -> None:
        if kind == "deposit":
            deposit(books, payload["asset"], Decimal(payload["amount"]), at, ref)
        elif kind == "withdraw":
            withdraw(books, payload["asset"], Decimal(payload["amount"]), at, ref)
        elif kind == "internal":
            internal_transfer(books, payload["asset"], Decimal(payload["amount"]), at, ref)
        elif kind == "fill":
            if payload.get("projection_deferred") is not None:
                if payload["projection_deferred"] != REPLAY_VERSION:
                    raise ValidationFailure("unknown chronological projection version")
                return
            fill = FillRecord.model_validate(payload["fill"])
            apply_fill(
                books,
                fill,
                base_asset=payload["base_asset"],
                quote_asset=payload["quote_asset"],
                lot_id=payload["lot_id"],
                at=utc_iso(fill.filled_at_utc),
            )
        elif kind == "expense":
            add_expense(
                books,
                Expense(
                    payload["expense_id"],
                    Decimal(payload["native_amount"]),
                    payload["native_currency"],
                    Decimal(payload["reporting_amount"]),
                    payload["reporting_currency"],
                    bool(payload["embedded"]),
                    payload["source"],
                    "accrued",
                    at,
                    payload.get("settles"),
                ),
            )
        else:
            raise ValueError(kind)

    def _chronological_books(self, rows) -> Books:
        source_rows = [row for row in rows if row["kind"] != REPLAY_KIND]

        def effective(row):
            payload = json.loads(row["payload_json"])
            recorded = parse_utc(row["effective_at"])
            if row["kind"] != "fill":
                return recorded
            native = FillRecord.model_validate(payload["fill"]).filled_at_utc
            if native.tzinfo is None or native > recorded:
                raise ValidationFailure("chronological replay native fill was not yet available at record time")
            return native

        ordered = sorted(source_rows, key=lambda row: (effective(row), row["sequence"]))
        books = Books()
        with localcontext(Context(prec=28)):
            for row in ordered:
                payload = json.loads(row["payload_json"])
                payload.pop("projection_deferred", None)
                self._mutate(books, row["kind"], payload, row["effective_at"], row["external_ref"])
        return books

    def _replay_row(self, books: Books, row, prior_rows) -> None:
        """Replay one original event or verify a bounded prefix restatement."""
        payload = json.loads(row["payload_json"])
        previous_deferred = bool(
            prior_rows
            and prior_rows[-1]["kind"] == "fill"
            and json.loads(prior_rows[-1]["payload_json"]).get("projection_deferred")
        )
        if previous_deferred and row["kind"] != REPLAY_KIND:
            raise ValidationFailure("deferred fill is missing its immediate chronological replay")
        if row["kind"] != REPLAY_KIND:
            self._mutate(books, row["kind"], payload, row["effective_at"], row["external_ref"])
            return
        expected_keys = {
            "schema_version",
            "projection_version",
            "source_manifest",
            "previous_projection_sha256",
            "current_projection_sha256",
            "new_fill_event_id",
            "postings",
        }
        if (
            set(payload) != expected_keys
            or type(payload["schema_version"]) is not int
            or payload["schema_version"] != 1
            or payload["projection_version"] != REPLAY_VERSION
            or payload["source_manifest"] != _source_manifest(prior_rows)
            or payload["previous_projection_sha256"] != _projection_sha256(books)
            or not prior_rows
            or any(
                source["sequence"] != index + 1
                or source["portfolio_id"] != row["portfolio_id"]
                or parse_utc(source["effective_at"]) >= parse_utc(row["effective_at"])
                for index, source in enumerate(prior_rows[:-1])
            )
            or prior_rows[-1]["sequence"] != len(prior_rows)
            or prior_rows[-1]["portfolio_id"] != row["portfolio_id"]
            or row["sequence"] != len(prior_rows) + 1
            or prior_rows[-1]["event_id"] != payload["new_fill_event_id"]
            or prior_rows[-1]["kind"] != "fill"
            or json.loads(prior_rows[-1]["payload_json"]).get("projection_deferred") != REPLAY_VERSION
            or row["effective_at"] != prior_rows[-1]["effective_at"]
            or row["external_ref"] != f"chronological-replay:{payload['new_fill_event_id']}"
        ):
            raise ValidationFailure("chronological replay source or previous projection differs")
        corrected = self._chronological_books(prior_rows)
        new_time = FillRecord.model_validate(json.loads(prior_rows[-1]["payload_json"])["fill"]).filled_at_utc
        if any(
            source["kind"] == "fill"
            and FillRecord.model_validate(
                json.loads(source["payload_json"])["fill"],
            ).filled_at_utc
            == new_time
            for source in prior_rows[:-1]
        ):
            raise ValidationFailure("chronological replay requires precise native tie evidence")
        if payload["current_projection_sha256"] != _projection_sha256(corrected):
            raise ValidationFailure("chronological replay current projection differs")
        with localcontext(Context(prec=28)) as context:
            context.traps[Inexact] = True
            old, new = _native_totals(books), _native_totals(corrected)
            delta = [
                Posting(
                    account, asset, new.get((account, asset), Decimal("0")) - old.get((account, asset), Decimal("0"))
                )
                for account, asset in sorted(old.keys() | new.keys())
                if new.get((account, asset), Decimal("0")) != old.get((account, asset), Decimal("0"))
            ]
            expected = [
                {"account": item.account, "asset": item.asset, "amount": canonical_decimal(item.amount)}
                for item in delta
            ]
            if payload["postings"] != expected:
                raise ValidationFailure("chronological replay native adjustment differs")
            if delta:
                _apply_group(books, delta)
        books.cash, books.lots, books.flows, books.expenses = (
            corrected.cash,
            corrected.lots,
            corrected.flows,
            corrected.expenses,
        )

    def _replay_rows(self, rows) -> Books:
        books = Books()
        prefix = []
        with localcontext(Context(prec=28)):
            for row in rows:
                self._replay_row(books, row, prefix)
                prefix.append(row)
        if rows and rows[-1]["kind"] == "fill" and json.loads(rows[-1]["payload_json"]).get("projection_deferred"):
            raise ValidationFailure("deferred fill is missing its chronological replay receipt")
        return books

    def _books(self, portfolio_id: str, at: str) -> Books:
        return self._books_conn(self.database.connection, portfolio_id, at)

    def _books_conn(self, conn, portfolio_id: str, at: str) -> Books:
        rows = conn.execute(
            """SELECT * FROM ledger_events
            WHERE portfolio_id = ? AND effective_at <= ? ORDER BY sequence""",
            (portfolio_id, at),
        ).fetchall()
        return self._replay_rows(rows)

    def _reporting(self, portfolio_id: str) -> str:
        row = self.database.execute(
            "SELECT reporting_currency FROM portfolios WHERE portfolio_id = ?",
            (portfolio_id,),
        ).fetchone()
        if row is None:
            raise KeyError(portfolio_id)
        return row["reporting_currency"]

    def _marks(self, portfolio_id: str, at: str) -> list[Mark]:
        rows = self.database.execute(
            """SELECT asset, quote_currency, mark, observed_at, stale, source FROM valuation_marks
            WHERE portfolio_id = ? AND observed_at <= ?""",
            (portfolio_id, at),
        ).fetchall()
        return [
            Mark(
                row["asset"],
                Decimal(row["mark"]),
                row["quote_currency"],
                row["observed_at"],
                bool(row["stale"]),
                row["source"],
            )
            for row in rows
        ]

    def _rates(self, at: str) -> list[FxRate]:
        rows = self.database.execute(
            """SELECT rate_id, base, quote, rate, observed_at, valid_as_of, retrieved_at, stale, source
            FROM fx_rates WHERE observed_at <= ? AND valid_as_of <= ? AND retrieved_at <= ?
            ORDER BY observed_at, retrieved_at, rowid""",
            (at, at, at),
        ).fetchall()
        return [
            FxRate(
                row["base"],
                row["quote"],
                Decimal(row["rate"]),
                max(row["observed_at"], row["valid_as_of"]),
                bool(row["stale"]),
                row["source"],
                row["rate_id"],
                row["retrieved_at"],
            )
            for row in rows
        ]

    def _activity(self, portfolio_id: str | None, kind: str, payload: dict, *, created_at: str | None = None) -> None:
        body = _json(payload)
        with self.database.transaction() as conn:
            prev = conn.execute("SELECT hash FROM activity_events ORDER BY rowid DESC LIMIT 1").fetchone()
            prev_hash = prev["hash"] if prev else ""
            digest = hashlib.sha256((prev_hash + body).encode()).hexdigest()
            conn.execute(
                """INSERT INTO activity_events
                (event_id, portfolio_id, kind, payload_json, created_at, hash, prev_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), portfolio_id, kind, body, created_at or self.now(), digest, prev_hash or None),
            )

    def activity_intact(self) -> bool:
        rows = self.database.execute(
            "SELECT payload_json, hash, prev_hash FROM activity_events ORDER BY rowid"
        ).fetchall()
        previous = ""
        for row in rows:
            if (row["prev_hash"] or "") != previous:
                return False
            digest = hashlib.sha256((previous + row["payload_json"]).encode()).hexdigest()
            if digest != row["hash"]:
                return False
            previous = row["hash"]
        return True
