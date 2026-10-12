-- +goose Up
-- what happened, per workspace, for GET /events/stream and webhooks (server/events.py). Rows are written by the
-- triggers below, in the transaction that made the change, and each one notifies zoo_event with its workspace id.
CREATE TABLE events (
    id TEXT PRIMARY KEY NOT NULL DEFAULT gen_random_uuid()::text,
    seq BIGINT NOT NULL GENERATED ALWAYS AS IDENTITY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    sandbox_id TEXT,
    run_id TEXT,
    type TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_events_seq ON events(seq);
CREATE INDEX idx_events_workspace ON events(workspace_id, seq);
CREATE INDEX idx_events_created ON events(created_at);

-- +goose StatementBegin
CREATE FUNCTION zoo_event(workspace TEXT, sandbox TEXT, run TEXT, kind TEXT, payload JSONB) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO events (workspace_id, sandbox_id, run_id, type, data)
    VALUES (workspace, sandbox, run, kind, payload::text);
    PERFORM pg_notify('zoo_event', workspace);
END;
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE FUNCTION sandbox_events() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status IS DISTINCT FROM NEW.status THEN
        PERFORM zoo_event(NEW.workspace_id, NEW.id, NULL, 'sandbox.state',
            jsonb_build_object('status', NEW.status, 'previous', OLD.status, 'error', NEW.error_message));
    END IF;
    RETURN NULL;
END;
$$;
-- +goose StatementEnd
CREATE TRIGGER sandboxes_events AFTER UPDATE OF status ON sandboxes
    FOR EACH ROW EXECUTE FUNCTION sandbox_events();

-- +goose StatementBegin
CREATE FUNCTION agent_run_events() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    workspace TEXT;
BEGIN
    SELECT workspace_id INTO workspace FROM sandboxes WHERE id = NEW.sandbox_id;
    IF OLD.started_at IS NULL AND NEW.started_at IS NOT NULL THEN
        PERFORM zoo_event(workspace, NEW.sandbox_id, NEW.id, 'run.started',
            jsonb_build_object('source', NEW.source, 'model', NEW.model));
    END IF;
    IF OLD.state IN ('queued', 'running') AND NEW.state IN ('succeeded', 'failed', 'cancelled') THEN
        PERFORM zoo_event(workspace, NEW.sandbox_id, NEW.id, 'run.finished',
            jsonb_build_object('state', NEW.state, 'error', NEW.error, 'steps', NEW.steps, 'tokens', NEW.tokens,
                'cost', NEW.cost));
    END IF;
    IF OLD.paused_at IS NULL AND NEW.paused_at IS NOT NULL THEN
        PERFORM zoo_event(workspace, NEW.sandbox_id, NEW.id, 'takeover.started',
            jsonb_build_object('by', NEW.paused_by));
    ELSIF OLD.paused_at IS NOT NULL AND NEW.paused_at IS NULL THEN
        PERFORM zoo_event(workspace, NEW.sandbox_id, NEW.id, 'takeover.ended',
            jsonb_build_object('by', OLD.paused_by));
    END IF;
    RETURN NULL;
END;
$$;
-- +goose StatementEnd
CREATE TRIGGER agent_runs_events AFTER UPDATE ON agent_runs
    FOR EACH ROW EXECUTE FUNCTION agent_run_events();

-- +goose StatementBegin
CREATE FUNCTION agent_step_events() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    workspace TEXT;
BEGIN
    IF NEW.kind = 'action' AND NEW.run_id IS NOT NULL THEN
        SELECT workspace_id INTO workspace FROM sandboxes WHERE id = NEW.sandbox_id;
        PERFORM zoo_event(workspace, NEW.sandbox_id, NEW.run_id, 'run.step',
            jsonb_build_object('message_id', NEW.id, 'action', NEW.content));
    END IF;
    RETURN NULL;
END;
$$;
-- +goose StatementEnd
CREATE TRIGGER agent_messages_events AFTER INSERT ON agent_messages
    FOR EACH ROW EXECUTE FUNCTION agent_step_events();

-- +goose Down
DROP TRIGGER IF EXISTS agent_messages_events ON agent_messages;
DROP FUNCTION IF EXISTS agent_step_events();
DROP TRIGGER IF EXISTS agent_runs_events ON agent_runs;
DROP FUNCTION IF EXISTS agent_run_events();
DROP TRIGGER IF EXISTS sandboxes_events ON sandboxes;
DROP FUNCTION IF EXISTS sandbox_events();
DROP FUNCTION IF EXISTS zoo_event(TEXT, TEXT, TEXT, TEXT, JSONB);
DROP TABLE IF EXISTS events;
