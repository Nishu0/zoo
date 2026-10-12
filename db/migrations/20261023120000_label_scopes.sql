-- +goose Up
-- a key with a label selector ({"end_user": "alice"}) sees only sandboxes carrying those labels and puts them on
-- every sandbox it makes
ALTER TABLE api_keys ADD COLUMN label_selector TEXT;

-- limits per label value (server/quotas.py); value '*' is the default for every value of the key without a row of
-- its own. A null limit is no limit.
CREATE TABLE label_quotas (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    max_running_sandboxes BIGINT,
    max_cpus DOUBLE PRECISION,
    max_memory_mb BIGINT,
    max_storage_gb BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, key, value)
);

-- +goose Down
DROP TABLE IF EXISTS label_quotas;
ALTER TABLE api_keys DROP COLUMN label_selector;
