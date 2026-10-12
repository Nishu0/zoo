-- +goose Up
-- benchmark runs (server/bench.py): a task suite run against models in parallel sandboxes. suite is the suite as
-- JSON, models a JSON list of {provider, model}.
CREATE TABLE benchmarks (
    id TEXT PRIMARY KEY NOT NULL,
    seq BIGINT NOT NULL GENERATED ALWAYS AS IDENTITY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    created_by TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    suite TEXT NOT NULL,
    models TEXT NOT NULL,
    parallel BIGINT NOT NULL CHECK (parallel > 0),
    state TEXT NOT NULL DEFAULT 'queued'
        CHECK (state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    worker TEXT,
    heartbeat_at TIMESTAMPTZ,
    cancel_requested BOOLEAN NOT NULL DEFAULT false,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX idx_benchmarks_seq ON benchmarks(seq);
CREATE INDEX idx_benchmarks_workspace ON benchmarks(workspace_id, created_at DESC);

CREATE TABLE benchmark_results (
    benchmark_id TEXT NOT NULL REFERENCES benchmarks(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL,
    model TEXT NOT NULL,
    run_id TEXT,
    sandbox_id TEXT,
    passed BOOLEAN,
    score DOUBLE PRECISION,
    steps BIGINT NOT NULL DEFAULT 0,
    tokens BIGINT NOT NULL DEFAULT 0,
    cost DOUBLE PRECISION NOT NULL DEFAULT 0,
    seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    error TEXT,
    finished_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (benchmark_id, task_id, model)
);

-- +goose Down
DROP TABLE IF EXISTS benchmark_results;
DROP TABLE IF EXISTS benchmarks;
