"""Durable artifact bytes, generation fencing, reloads and rollout observations."""

STATEMENTS = [
    "ALTER TABLE active_versions ADD COLUMN generation INTEGER NOT NULL DEFAULT 0",
    """CREATE TABLE artifact_bundles (
        artifact_hash TEXT PRIMARY KEY, files_json TEXT NOT NULL,
        manifest_json TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
    """CREATE TABLE version_rollouts (
        rollout_id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL,
        candidate_id TEXT NOT NULL, generation INTEGER NOT NULL,
        target_hash TEXT NOT NULL, previous_hash TEXT NOT NULL,
        previous_version_id TEXT NOT NULL, state TEXT NOT NULL,
        policy_json TEXT NOT NULL, activated_at TEXT NOT NULL,
        UNIQUE (portfolio_id, generation)
    )""",
    """CREATE TABLE consumer_loads (
        portfolio_id TEXT NOT NULL, consumer_id TEXT NOT NULL,
        version_id TEXT NOT NULL, artifact_hash TEXT NOT NULL,
        generation INTEGER NOT NULL, manifest_sha256 TEXT NOT NULL,
        reconciled_at TEXT NOT NULL, PRIMARY KEY (portfolio_id, consumer_id)
    )""",
    """CREATE TABLE version_observations (
        rollout_id TEXT NOT NULL, observation_id TEXT NOT NULL,
        document_json TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY (rollout_id, observation_id)
    )""",
]
