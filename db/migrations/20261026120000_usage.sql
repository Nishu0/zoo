-- +goose Up
-- metered usage in hourly buckets (server/metering.py). sandbox_id is not a foreign key: usage outlives its
-- sandbox, and so do the labels copied into usage_labels when the sandbox is labelled. Storage that belongs to the
-- workspace rather than a sandbox (profiles) is metered under sandbox_id ''.
CREATE TABLE usage (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    sandbox_id TEXT NOT NULL,
    metric TEXT NOT NULL CHECK (metric IN (
        'cpu_minutes', 'memory_mb_minutes', 'storage_gb_hours', 'agent_steps', 'agent_tokens', 'agent_cost'
    )),
    period_start TIMESTAMPTZ NOT NULL,
    quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (workspace_id, sandbox_id, metric, period_start)
);
CREATE INDEX idx_usage_workspace ON usage(workspace_id, period_start);

CREATE TABLE usage_labels (
    sandbox_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (sandbox_id, key)
);
CREATE INDEX idx_usage_labels_key_value ON usage_labels(key, value);

-- when storage was last metered, per workspace, so each hour is counted once
ALTER TABLE workspaces ADD COLUMN storage_metered_at TIMESTAMPTZ;

-- +goose Down
ALTER TABLE workspaces DROP COLUMN storage_metered_at;
DROP TABLE IF EXISTS usage_labels;
DROP TABLE IF EXISTS usage;
