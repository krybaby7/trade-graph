"""Protected evidence for interrupted owner commands; never replay external effects."""

STATEMENTS = [
    """CREATE TABLE dashboard_command_evidence (
        command_id TEXT PRIMARY KEY REFERENCES dashboard_commands(command_id),
        action TEXT NOT NULL,
        portfolio_id TEXT NOT NULL,
        deployment_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 0),
        phase TEXT NOT NULL CHECK (phase IN ('LOCAL_COMMITTED', 'EFFECT_COMMITTED', 'RECOVERED')),
        effect_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
]
