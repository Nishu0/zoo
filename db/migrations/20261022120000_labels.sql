-- +goose Up
-- labels on sandboxes (server/labels.py), e.g. end_user=alice: for filtering, scoping API keys and label quotas
CREATE TABLE sandbox_labels (
    sandbox_id TEXT NOT NULL REFERENCES sandboxes(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (sandbox_id, key)
);
CREATE INDEX idx_sandbox_labels_key_value ON sandbox_labels(key, value);

-- +goose Down
DROP TABLE IF EXISTS sandbox_labels;
