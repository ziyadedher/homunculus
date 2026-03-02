-- Recreate auth_sessions without CHECK constraint on flow_type
-- (app-level validation via SERVICE_SCOPES dict instead)
DROP TABLE IF EXISTS auth_sessions;
CREATE TABLE auth_sessions (
    session_id TEXT PRIMARY KEY,
    flow_type TEXT NOT NULL,
    state TEXT NOT NULL UNIQUE,
    email TEXT,
    credentials_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_state ON auth_sessions(state);

-- Recreate google_credentials with (email, service) composite PK
DROP TABLE IF EXISTS google_credentials;
CREATE TABLE google_credentials (
    email TEXT NOT NULL,
    service TEXT NOT NULL,
    credentials_json TEXT NOT NULL,
    scopes TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (email, service)
);
