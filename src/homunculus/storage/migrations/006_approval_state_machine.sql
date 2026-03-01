-- Add 'completed' status to the approval state machine.
-- SQLite doesn't support altering CHECK constraints, so we recreate the table.

CREATE TABLE pending_approvals_new (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    request_description TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_input TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'denied', 'completed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT,
    response_text TEXT
);

INSERT INTO pending_approvals_new SELECT * FROM pending_approvals;
DROP TABLE pending_approvals;
ALTER TABLE pending_approvals_new RENAME TO pending_approvals;

CREATE INDEX IF NOT EXISTS idx_pending_approvals_status ON pending_approvals(status);
CREATE INDEX IF NOT EXISTS idx_pending_approvals_conversation ON pending_approvals(conversation_id);
