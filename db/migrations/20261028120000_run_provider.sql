-- +goose Up
-- the provider a run was started with (server/agent_api.py, PROVIDERS); null: the workspace's agent settings
ALTER TABLE agent_runs ADD COLUMN provider TEXT;
ALTER TABLE agent_runs ADD COLUMN api_base TEXT;

-- +goose Down
ALTER TABLE agent_runs DROP COLUMN api_base;
ALTER TABLE agent_runs DROP COLUMN provider;
