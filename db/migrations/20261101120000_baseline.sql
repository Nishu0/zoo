-- +goose Up
-- The whole schema in one baseline. The database is created from this file; Zoo never migrates old rows.

CREATE TABLE users (
    id TEXT PRIMARY KEY NOT NULL,
    email TEXT NOT NULL UNIQUE,
    name TEXT,
    password TEXT NOT NULL,
    avatar_url TEXT,
    -- when the user finished or skipped the dashboard's first-run steps (web /welcome); NULL shows them
    onboarded_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE secret_keys (
    id TEXT PRIMARY KEY NOT NULL,
    scope TEXT NOT NULL UNIQUE,
    wrapped TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    rotated_at TIMESTAMPTZ
);

CREATE TABLE leases (
    name TEXT PRIMARY KEY NOT NULL,
    holder TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE node_authority (
    id BIGINT PRIMARY KEY NOT NULL CHECK (id = 1),
    certificate TEXT NOT NULL,
    key_ciphertext TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE workspaces (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- idle auto-stop and maximum lifetime defaults (server/sandbox_api.py, expire); null: never
    default_idle_timeout_minutes BIGINT CHECK (default_idle_timeout_minutes > 0),
    default_max_lifetime_minutes BIGINT CHECK (default_max_lifetime_minutes > 0),
    -- when storage was last metered, so each hour is counted once
    storage_metered_at TIMESTAMPTZ
);

-- workspaces own what users made; user_id / created_by stay, as who made the row
CREATE TABLE servers (
    id TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    docker_url TEXT NOT NULL,
    bind_address TEXT NOT NULL,
    created_by TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    platform TEXT NOT NULL DEFAULT 'linux',
    capabilities TEXT NOT NULL DEFAULT ''
);

CREATE TABLE profiles (
    id TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    app TEXT NOT NULL,
    size_bytes BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    encrypted BOOLEAN NOT NULL DEFAULT false,
    platform TEXT NOT NULL DEFAULT 'linux'
);

CREATE TABLE domains (
    id TEXT PRIMARY KEY NOT NULL,
    hostname TEXT NOT NULL UNIQUE,
    created_by TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE vault_secrets (
    id TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    ciphertext TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    rotate_every_days BIGINT,
    rotated_at TIMESTAMPTZ,
    UNIQUE (workspace_id, name)
);

CREATE TABLE workspace_members (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('invited', 'active', 'suspended')),
    joined_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id)
);

CREATE TABLE workspace_invitations (
    id TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member', 'viewer')),
    invited_by TEXT NOT NULL REFERENCES users(id),
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sandbox_images (
    id TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    description TEXT,
    is_public BOOLEAN NOT NULL DEFAULT false,
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, slug)
);

CREATE TABLE apps (
    id TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    description TEXT,
    install_config TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- the whole workspace, one sandbox, or the sandboxes with some labels (an end user's); read-only keys only read
CREATE TABLE api_keys (
    id TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    created_by TEXT NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    key_prefix TEXT NOT NULL,
    scopes TEXT NOT NULL DEFAULT '[]',
    expires_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sandbox_id TEXT REFERENCES sandboxes(id) ON DELETE CASCADE,
    read_only BOOLEAN NOT NULL DEFAULT false,
    label_selector TEXT
);

CREATE TABLE pool_settings (
    id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL,
    server_id TEXT REFERENCES servers(id) ON DELETE CASCADE,
    size BIGINT NOT NULL DEFAULT 0 CHECK (size >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- rows are claimed oldest first; seq breaks ties on equal created_at
CREATE TABLE pool_sandboxes (
    id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL,
    server_id TEXT REFERENCES servers(id) ON DELETE CASCADE,
    image TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'booting' CHECK (status IN ('booting', 'idle', 'failed')),
    runtime_id TEXT,
    runtime_host TEXT,
    access_url TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    seq BIGINT GENERATED ALWAYS AS IDENTITY
);
CREATE UNIQUE INDEX idx_pool_sandboxes_seq ON pool_sandboxes(seq);

CREATE TABLE profile_versions (
    id TEXT PRIMARY KEY NOT NULL,
    profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    version BIGINT NOT NULL,
    size_bytes BIGINT NOT NULL,
    encrypted BOOLEAN NOT NULL DEFAULT true,
    sandbox_id TEXT,
    created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (profile_id, version)
);

-- a join token makes its server in the workspace it was created in
CREATE TABLE node_tokens (
    id TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    secret_hash TEXT NOT NULL UNIQUE,
    created_by TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    server_id TEXT REFERENCES servers(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE nodes (
    id TEXT PRIMARY KEY NOT NULL,
    server_id TEXT NOT NULL UNIQUE REFERENCES servers(id) ON DELETE CASCADE,
    serial TEXT NOT NULL,
    cert_expires_at TIMESTAMPTZ NOT NULL,
    version TEXT NOT NULL DEFAULT '',
    os TEXT NOT NULL DEFAULT '',
    arch TEXT NOT NULL DEFAULT '',
    hostname TEXT NOT NULL DEFAULT '',
    drivers TEXT NOT NULL DEFAULT '[]',
    targets TEXT NOT NULL DEFAULT '[]',
    cpus BIGINT,
    memory_total BIGINT,
    memory_available BIGINT,
    disk_total BIGINT,
    disk_free BIGINT,
    load DOUBLE PRECISION,
    sandboxes TEXT NOT NULL DEFAULT '[]',
    checks TEXT NOT NULL DEFAULT '[]',
    seen_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sandbox_image_versions (
    id TEXT PRIMARY KEY NOT NULL,
    image_id TEXT NOT NULL REFERENCES sandbox_images(id) ON DELETE CASCADE,
    version TEXT NOT NULL,
    image_uri TEXT NOT NULL,
    image_digest TEXT,
    build_config TEXT NOT NULL DEFAULT '{}',
    default_resources TEXT NOT NULL DEFAULT '{}',
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (image_id, version)
);

CREATE TABLE sandboxes (
    id TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    image_version_id TEXT NOT NULL REFERENCES sandbox_image_versions(id),
    created_by TEXT NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'provisioning', 'running', 'stopped', 'failed', 'deleting', 'deleted')),
    runtime TEXT NOT NULL DEFAULT 'docker',
    runtime_id TEXT,
    runtime_host TEXT,
    access_url TEXT,
    resources TEXT NOT NULL DEFAULT '{}',
    config TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    started_at TIMESTAMPTZ,
    stopped_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ,
    server_id TEXT REFERENCES servers(id),
    kind TEXT NOT NULL DEFAULT 'desktop',
    unreachable_since TIMESTAMPTZ,
    base_version TEXT,
    boot_seconds DOUBLE PRECISION,
    recovered_at TIMESTAMPTZ,
    -- what each sandbox may use; disk_gb null: no disk limit beyond the host's (server/sizes.py)
    cpus DOUBLE PRECISION NOT NULL DEFAULT 2,
    memory_mb BIGINT NOT NULL DEFAULT 2048,
    disk_gb BIGINT,
    last_activity_at TIMESTAMPTZ,
    idle_timeout_minutes BIGINT CHECK (idle_timeout_minutes > 0),
    max_lifetime_minutes BIGINT CHECK (max_lifetime_minutes > 0)
);

CREATE TABLE sandbox_members (
    sandbox_id TEXT NOT NULL REFERENCES sandboxes(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member', 'viewer')),
    granted_by TEXT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (sandbox_id, user_id)
);

CREATE TABLE sandbox_app_permissions (
    id TEXT PRIMARY KEY NOT NULL,
    sandbox_id TEXT NOT NULL REFERENCES sandboxes(id) ON DELETE CASCADE,
    app_id TEXT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    effect TEXT NOT NULL DEFAULT 'deny' CHECK (effect IN ('allow', 'deny')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (sandbox_id, app_id, action)
);

CREATE TABLE sandbox_network_policies (
    id TEXT PRIMARY KEY NOT NULL,
    sandbox_id TEXT NOT NULL UNIQUE REFERENCES sandboxes(id) ON DELETE CASCADE,
    default_action TEXT NOT NULL DEFAULT 'deny' CHECK (default_action IN ('allow', 'deny')),
    allow_dns BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sandbox_permissions (
    id TEXT PRIMARY KEY NOT NULL,
    sandbox_id TEXT NOT NULL REFERENCES sandboxes(id) ON DELETE CASCADE,
    permission TEXT NOT NULL,
    action TEXT NOT NULL,
    effect TEXT NOT NULL DEFAULT 'deny' CHECK (effect IN ('allow', 'deny')),
    rules TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (sandbox_id, permission, action)
);

CREATE TABLE sandbox_secrets (
    id TEXT PRIMARY KEY NOT NULL,
    sandbox_id TEXT NOT NULL REFERENCES sandboxes(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    secret_ref TEXT NOT NULL,
    injection_config TEXT NOT NULL DEFAULT '{}',
    enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (sandbox_id, name)
);

CREATE TABLE sandbox_vault_secrets (
    sandbox_id TEXT NOT NULL REFERENCES sandboxes(id) ON DELETE CASCADE,
    secret_id TEXT NOT NULL REFERENCES vault_secrets(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    PRIMARY KEY (sandbox_id, secret_id)
);

CREATE TABLE audit_logs (
    id TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    actor_id TEXT REFERENCES users(id),
    sandbox_id TEXT REFERENCES sandboxes(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tool_executions (
    id TEXT PRIMARY KEY NOT NULL,
    sandbox_id TEXT NOT NULL REFERENCES sandboxes(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    input TEXT NOT NULL DEFAULT '{}',
    output TEXT,
    error_message TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- seq breaks ties on equal created_at
CREATE TABLE snapshots (
    id TEXT PRIMARY KEY NOT NULL,
    sandbox_id TEXT NOT NULL REFERENCES sandboxes(id) ON DELETE CASCADE,
    server_id TEXT REFERENCES servers(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    size_bytes BIGINT NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'creating' CHECK (state IN ('creating', 'ready', 'failed')),
    error TEXT,
    created_by TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    seq BIGINT GENERATED ALWAYS AS IDENTITY
);
CREATE UNIQUE INDEX idx_snapshots_seq ON snapshots(seq);

-- claimed oldest first; seq breaks ties on equal created_at
CREATE TABLE jobs (
    id TEXT PRIMARY KEY NOT NULL,
    sandbox_id TEXT NOT NULL REFERENCES sandboxes(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('boot', 'stop', 'delete', 'move', 'snapshot', 'restore')),
    state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    args TEXT NOT NULL DEFAULT '{}',
    attempts BIGINT NOT NULL DEFAULT 0,
    max_attempts BIGINT NOT NULL DEFAULT 3,
    run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
    deadline TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    seq BIGINT GENERATED ALWAYS AS IDENTITY
);
CREATE UNIQUE INDEX idx_jobs_seq ON jobs(seq);

CREATE TABLE sandbox_network_rules (
    id TEXT PRIMARY KEY NOT NULL,
    policy_id TEXT NOT NULL REFERENCES sandbox_network_policies(id) ON DELETE CASCADE,
    rule_type TEXT NOT NULL,
    value TEXT NOT NULL,
    effect TEXT NOT NULL CHECK (effect IN ('allow', 'deny')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- single-use websocket tickets (server/tickets.py), shared by every API process; only the ticket's hash is stored
CREATE TABLE tickets (
    ticket_hash TEXT PRIMARY KEY NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_tickets_expires ON tickets(expires_at);

-- what a workspace may use at once (server/quotas.py); a null limit is no limit
CREATE TABLE workspace_quotas (
    workspace_id TEXT PRIMARY KEY NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    max_running_sandboxes BIGINT,
    max_cpus DOUBLE PRECISION,
    max_memory_mb BIGINT,
    max_storage_gb BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

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

-- labels on sandboxes (server/labels.py), e.g. end_user=alice: for filtering, scoping API keys and label quotas
CREATE TABLE sandbox_labels (
    sandbox_id TEXT NOT NULL REFERENCES sandboxes(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (sandbox_id, key)
);
CREATE INDEX idx_sandbox_labels_key_value ON sandbox_labels(key, value);

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

-- what happened, per workspace, for GET /events/stream and webhooks (server/events.py). Rows are written by the
-- triggers below, in the transaction that made the change, and each one notifies zoo_event with its workspace id.
CREATE TABLE events (
    id TEXT PRIMARY KEY NOT NULL DEFAULT gen_random_uuid()::text,
    seq BIGINT NOT NULL GENERATED ALWAYS AS IDENTITY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    sandbox_id TEXT,
    type TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_events_seq ON events(seq);
CREATE INDEX idx_events_workspace ON events(workspace_id, seq);
CREATE INDEX idx_events_created ON events(created_at);

-- +goose StatementBegin
CREATE FUNCTION zoo_event(workspace TEXT, sandbox TEXT, kind TEXT, payload JSONB) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO events (workspace_id, sandbox_id, type, data)
    VALUES (workspace, sandbox, kind, payload::text);
    PERFORM pg_notify('zoo_event', workspace);
END;
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE FUNCTION sandbox_events() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status IS DISTINCT FROM NEW.status THEN
        PERFORM zoo_event(NEW.workspace_id, NEW.id, 'sandbox.state',
            jsonb_build_object('status', NEW.status, 'previous', OLD.status, 'error', NEW.error_message));
    END IF;
    RETURN NULL;
END;
$$;
-- +goose StatementEnd
CREATE TRIGGER sandboxes_events AFTER UPDATE OF status ON sandboxes
    FOR EACH ROW EXECUTE FUNCTION sandbox_events();

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

-- metered usage in hourly buckets (server/metering.py). sandbox_id is not a foreign key: usage outlives its
-- sandbox, and so do the labels copied into usage_labels when the sandbox is labelled. Storage that belongs to the
-- workspace rather than a sandbox (profiles) is metered under sandbox_id ''.
CREATE TABLE usage (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    sandbox_id TEXT NOT NULL,
    metric TEXT NOT NULL CHECK (metric IN ('cpu_minutes', 'memory_mb_minutes', 'storage_gb_hours')),
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

CREATE INDEX idx_sandboxes_workspace ON sandboxes(workspace_id);
CREATE INDEX idx_sandboxes_status ON sandboxes(status);
CREATE INDEX idx_sandboxes_created_by ON sandboxes(created_by);
CREATE INDEX idx_sandboxes_workspace_page ON sandboxes(workspace_id, created_at DESC, id DESC)
    WHERE deleted_at IS NULL;
CREATE INDEX idx_apps_workspace ON apps(workspace_id);
CREATE INDEX idx_network_rules_policy ON sandbox_network_rules(policy_id);
CREATE INDEX idx_tool_executions_sandbox ON tool_executions(sandbox_id, created_at);
CREATE INDEX idx_audit_logs_workspace ON audit_logs(workspace_id, created_at DESC, id DESC);
CREATE INDEX idx_sandbox_vault_secrets_secret ON sandbox_vault_secrets(secret_id);
CREATE INDEX idx_pool_sandboxes_claim ON pool_sandboxes(kind, status, created_at);
CREATE INDEX snapshots_sandbox ON snapshots (sandbox_id, created_at);
CREATE INDEX idx_jobs_state ON jobs(state, run_after);
CREATE INDEX idx_jobs_sandbox ON jobs(sandbox_id, created_at);
CREATE INDEX idx_servers_workspace ON servers(workspace_id);
CREATE INDEX idx_profiles_workspace ON profiles(workspace_id);
CREATE INDEX idx_api_keys_workspace ON api_keys(workspace_id);

-- +goose Down
DROP TABLE IF EXISTS idempotency_keys CASCADE;
DROP TABLE IF EXISTS usage_labels CASCADE;
DROP TABLE IF EXISTS usage CASCADE;
DROP TABLE IF EXISTS webhook_deliveries CASCADE;
DROP TABLE IF EXISTS webhooks CASCADE;
DROP TRIGGER IF EXISTS sandboxes_events ON sandboxes;
DROP FUNCTION IF EXISTS sandbox_events();
DROP FUNCTION IF EXISTS zoo_event(TEXT, TEXT, TEXT, JSONB);
DROP TABLE IF EXISTS events CASCADE;
DROP TABLE IF EXISTS label_quotas CASCADE;
DROP TABLE IF EXISTS sandbox_labels CASCADE;
DROP TABLE IF EXISTS viewer_links CASCADE;
DROP TABLE IF EXISTS workspace_quotas CASCADE;
DROP TABLE IF EXISTS tickets CASCADE;
DROP TABLE IF EXISTS sandbox_network_rules CASCADE;
DROP TABLE IF EXISTS jobs CASCADE;
DROP TABLE IF EXISTS snapshots CASCADE;
DROP TABLE IF EXISTS tool_executions CASCADE;
DROP TABLE IF EXISTS audit_logs CASCADE;
DROP TABLE IF EXISTS sandbox_vault_secrets CASCADE;
DROP TABLE IF EXISTS sandbox_secrets CASCADE;
DROP TABLE IF EXISTS sandbox_permissions CASCADE;
DROP TABLE IF EXISTS sandbox_network_policies CASCADE;
DROP TABLE IF EXISTS sandbox_app_permissions CASCADE;
DROP TABLE IF EXISTS sandbox_members CASCADE;
DROP TABLE IF EXISTS sandboxes CASCADE;
DROP TABLE IF EXISTS sandbox_image_versions CASCADE;
DROP TABLE IF EXISTS nodes CASCADE;
DROP TABLE IF EXISTS node_tokens CASCADE;
DROP TABLE IF EXISTS profile_versions CASCADE;
DROP TABLE IF EXISTS pool_sandboxes CASCADE;
DROP TABLE IF EXISTS pool_settings CASCADE;
DROP TABLE IF EXISTS api_keys CASCADE;
DROP TABLE IF EXISTS apps CASCADE;
DROP TABLE IF EXISTS sandbox_images CASCADE;
DROP TABLE IF EXISTS workspace_invitations CASCADE;
DROP TABLE IF EXISTS workspace_members CASCADE;
DROP TABLE IF EXISTS vault_secrets CASCADE;
DROP TABLE IF EXISTS domains CASCADE;
DROP TABLE IF EXISTS profiles CASCADE;
DROP TABLE IF EXISTS servers CASCADE;
DROP TABLE IF EXISTS workspaces CASCADE;
DROP TABLE IF EXISTS node_authority CASCADE;
DROP TABLE IF EXISTS leases CASCADE;
DROP TABLE IF EXISTS secret_keys CASCADE;
DROP TABLE IF EXISTS users CASCADE;
