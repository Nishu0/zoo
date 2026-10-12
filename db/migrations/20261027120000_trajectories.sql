-- +goose Up
-- the accessibility tree each agent step saw, stored next to its screenshot (agent/<run id>/<name>.a11y.enc), and
-- whether a workspace captures it
ALTER TABLE agent_messages ADD COLUMN a11y TEXT;
ALTER TABLE workspaces ADD COLUMN capture_a11y BOOLEAN NOT NULL DEFAULT true;

-- trajectory exports (server/exports.py): run_ids is the JSON list chosen when it was asked for; a worker builds
-- the archive at exports/<id>.tar.gz
CREATE TABLE exports (
    id TEXT PRIMARY KEY NOT NULL,
    seq BIGINT NOT NULL GENERATED ALWAYS AS IDENTITY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    run_ids TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'running', 'succeeded', 'failed')),
    worker TEXT,
    claimed_at TIMESTAMPTZ,
    steps BIGINT NOT NULL DEFAULT 0,
    size_bytes BIGINT NOT NULL DEFAULT 0,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX idx_exports_seq ON exports(seq);
CREATE INDEX idx_exports_workspace ON exports(workspace_id, created_at DESC);

-- +goose Down
DROP TABLE IF EXISTS exports;
ALTER TABLE workspaces DROP COLUMN capture_a11y;
ALTER TABLE agent_messages DROP COLUMN a11y;
