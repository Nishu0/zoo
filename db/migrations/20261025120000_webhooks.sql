-- +goose Up
-- webhooks (server/webhooks.py): each event a webhook wants gets a delivery row in the event's own transaction,
-- which workers send and retry. types is a JSON array of event types ([] = all); label_selector a JSON object that
-- the event's sandbox's labels must match (null = any).
CREATE TABLE webhooks (
    id TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    secret_ciphertext TEXT NOT NULL,
    types TEXT NOT NULL DEFAULT '[]',
    label_selector TEXT,
    enabled BOOLEAN NOT NULL DEFAULT true,
    created_by TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_webhooks_workspace ON webhooks(workspace_id);

CREATE TABLE webhook_deliveries (
    id TEXT PRIMARY KEY NOT NULL DEFAULT gen_random_uuid()::text,
    seq BIGINT NOT NULL GENERATED ALWAYS AS IDENTITY,
    webhook_id TEXT NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'delivered', 'failed')),
    attempts BIGINT NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error TEXT,
    response_status BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX idx_webhook_deliveries_seq ON webhook_deliveries(seq);
CREATE INDEX idx_webhook_deliveries_due ON webhook_deliveries(next_attempt_at) WHERE status = 'pending';
CREATE INDEX idx_webhook_deliveries_webhook ON webhook_deliveries(webhook_id, created_at DESC);

-- +goose StatementBegin
CREATE FUNCTION webhook_deliveries_for() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO webhook_deliveries (webhook_id, event_id)
    SELECT w.id, NEW.id FROM webhooks w
    WHERE w.workspace_id = NEW.workspace_id
      AND w.enabled
      -- a test event goes to the one webhook it tests (server/webhooks.py, test)
      AND NEW.type <> 'webhook.test'
      AND (w.types::jsonb = '[]'::jsonb OR w.types::jsonb ? NEW.type)
      AND (w.label_selector IS NULL OR NOT EXISTS (
          SELECT 1 FROM jsonb_each_text(w.label_selector::jsonb) wanted
          WHERE NOT EXISTS (
              SELECT 1 FROM sandbox_labels l
              WHERE l.sandbox_id = NEW.sandbox_id AND l.key = wanted.key AND l.value = wanted.value
          )
      ));
    RETURN NULL;
END;
$$;
-- +goose StatementEnd
CREATE TRIGGER events_webhooks AFTER INSERT ON events
    FOR EACH ROW EXECUTE FUNCTION webhook_deliveries_for();

-- +goose Down
DROP TRIGGER IF EXISTS events_webhooks ON events;
DROP FUNCTION IF EXISTS webhook_deliveries_for();
DROP TABLE IF EXISTS webhook_deliveries;
DROP TABLE IF EXISTS webhooks;
