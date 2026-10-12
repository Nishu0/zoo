-- +goose Up
-- a person took over the screen from a running agent (server/agent_api.py, takeover); the run stays 'running' and
-- waits before its next action until it is handed back. paused_by is a user id or link:<viewer link id>.
ALTER TABLE agent_runs ADD COLUMN paused_at TIMESTAMPTZ;
ALTER TABLE agent_runs ADD COLUMN paused_by TEXT;

-- +goose Down
ALTER TABLE agent_runs DROP COLUMN paused_by;
ALTER TABLE agent_runs DROP COLUMN paused_at;
