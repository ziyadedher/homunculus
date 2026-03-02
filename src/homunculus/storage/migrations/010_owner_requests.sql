CREATE TABLE owner_requests (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    request_type TEXT NOT NULL CHECK (request_type IN ('approval', 'options', 'freeform')),
    description TEXT NOT NULL,
    tool_name TEXT NOT NULL DEFAULT '',
    tool_input TEXT NOT NULL DEFAULT '{}',
    options TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'denied', 'resolved', 'completed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT,
    response_text TEXT
);
CREATE INDEX idx_owner_requests_status ON owner_requests(status);
CREATE INDEX idx_owner_requests_conversation ON owner_requests(conversation_id);

-- Migrate existing data
INSERT INTO owner_requests (id, conversation_id, request_type, description, tool_name, tool_input, status, created_at, resolved_at, response_text)
SELECT id, conversation_id, 'approval', request_description, tool_name, tool_input,
       CASE WHEN status = 'completed' THEN 'completed'
            WHEN status = 'approved' THEN 'approved'
            WHEN status = 'denied' THEN 'denied'
            ELSE 'pending' END,
       created_at, resolved_at, response_text
FROM pending_approvals;
DROP TABLE pending_approvals;

UPDATE conversations SET status = 'awaiting_owner' WHERE status = 'awaiting_approval';
