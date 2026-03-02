CREATE TABLE IF NOT EXISTS auth_sessions (
    session_id TEXT PRIMARY KEY,
    flow_type TEXT NOT NULL CHECK (flow_type IN ('identity', 'calendar')),
    state TEXT NOT NULL UNIQUE,
    email TEXT,
    credentials_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_state ON auth_sessions(state);

CREATE TABLE IF NOT EXISTS google_credentials (
    email TEXT PRIMARY KEY,
    credentials_json TEXT NOT NULL,
    scopes TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
