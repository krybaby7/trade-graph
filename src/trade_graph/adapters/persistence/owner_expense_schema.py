"""Immutable owner bills and scoped completeness evidence, separate from API accruals."""

STATEMENTS = [
    """CREATE TABLE owner_expense_evidence (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        record_id TEXT NOT NULL UNIQUE,
        deployment_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        record_type TEXT NOT NULL CHECK (record_type IN ('expense','completeness')),
        bill_id TEXT,
        billing_scope TEXT,
        expense_kind TEXT CHECK (expense_kind IN ('subscription','other')),
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL CHECK (period_end > period_start),
        incurred_at TEXT,
        evidence_ref TEXT NOT NULL,
        document_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (deployment_id,request_id),
        UNIQUE (deployment_id,bill_id),
        CHECK ((record_type='expense' AND bill_id IS NOT NULL AND billing_scope IS NOT NULL
                AND expense_kind IS NOT NULL AND incurred_at IS NOT NULL)
               OR (record_type='completeness' AND bill_id IS NULL AND billing_scope IS NULL
                   AND expense_kind IS NULL AND incurred_at IS NULL))
    )""",
    """CREATE UNIQUE INDEX owner_expense_bill_evidence_identity
        ON owner_expense_evidence (deployment_id,evidence_ref) WHERE record_type='expense'""",
    """CREATE TRIGGER owner_expense_evidence_no_overlap BEFORE INSERT ON owner_expense_evidence
        WHEN NEW.record_type='expense' AND EXISTS (
          SELECT 1 FROM owner_expense_evidence WHERE deployment_id=NEW.deployment_id
          AND record_type='expense' AND billing_scope=NEW.billing_scope
          AND period_start < NEW.period_end AND period_end > NEW.period_start)
        BEGIN SELECT RAISE(ABORT, 'overlapping owner bill scope'); END""",
    """CREATE TRIGGER owner_expense_evidence_no_update BEFORE UPDATE ON owner_expense_evidence
        BEGIN SELECT RAISE(ABORT, 'owner expense evidence is immutable'); END""",
    """CREATE TRIGGER owner_expense_evidence_no_delete BEFORE DELETE ON owner_expense_evidence
        BEGIN SELECT RAISE(ABORT, 'owner expense evidence is immutable'); END""",
]
