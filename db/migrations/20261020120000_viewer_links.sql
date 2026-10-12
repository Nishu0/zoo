-- +goose Up
-- signed links to a sandbox's screen, for embedding and sharing (server/viewer_api.py); only the token's hash is
-- stored. A view link's input is dropped by the proxy; a control link's input reaches the sandbox.
CREATE TABLE viewer_links (
    id TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    sandbox_id TEXT NOT NULL REFERENCES sandboxes(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL CHECK (mode IN ('view', 'control')),
    end_user TEXT,
    created_by TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_viewer_links_sandbox ON viewer_links(sandbox_id, created_at DESC);

-- +goose Down
DROP TABLE IF EXISTS viewer_links;
