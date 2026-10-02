"""Protected release admission and one-use business RPC state.

These records live in the trusted financial database. They are never mounted
into or directly writable by the confined mutable process.
"""

STATEMENTS = [
    """CREATE TABLE protected_runtime_instances (
        instance_id TEXT PRIMARY KEY,
        manifest_sha256 TEXT NOT NULL,
        active_release_id TEXT,
        previous_release_id TEXT,
        generation INTEGER NOT NULL DEFAULT 0 CHECK (generation >= 0),
        status TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE protected_mutable_releases (
        release_id TEXT PRIMARY KEY,
        manifest_sha256 TEXT NOT NULL,
        source_sha256 TEXT NOT NULL,
        source_text TEXT NOT NULL,
        build_digest TEXT NOT NULL,
        admitted_by TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE protected_rpc_requests (
        request_id TEXT PRIMARY KEY,
        instance_id TEXT NOT NULL REFERENCES protected_runtime_instances(instance_id),
        capability_sha256 TEXT NOT NULL UNIQUE,
        scope_json TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        state TEXT NOT NULL,
        request_sha256 TEXT,
        response_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX protected_rpc_pending ON protected_rpc_requests (instance_id, state)",
]
