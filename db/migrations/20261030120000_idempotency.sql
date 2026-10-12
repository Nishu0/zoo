-- +goose Up
-- Idempotency-Key requests (server/idempotency.py): the first response to a key is kept 24 h and replayed to repeats.
-- owner is a hash of the caller's credentials and workspace, so keys of different callers never meet. A row
-- without a status is a request still running.
CREATE TABLE idempotency_keys (
    owner TEXT NOT NULL,
    key TEXT NOT NULL,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status INTEGER,
    content_type TEXT,
    response TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (owner, key)
);
CREATE INDEX idx_idempotency_keys_created ON idempotency_keys(created_at);

CREATE INDEX IF NOT EXISTS idx_sandboxes_workspace_page ON sandboxes(workspace_id, created_at DESC, id DESC)
    WHERE deleted_at IS NULL;

-- +goose Down
DROP INDEX IF EXISTS idx_sandboxes_workspace_page;
DROP TABLE idempotency_keys;
