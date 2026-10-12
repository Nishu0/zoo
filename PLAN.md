# Zoo: embeddable viewer, end users, events, metering, trajectories, agents — execution plan

Do the steps in order. Finish each step's "Done when" before starting the next. Do not skip ahead.

## Rules (read before every step)

1. **Postgres and object storage only.** New state goes in a migration in `db/migrations/` (goose format) and queries in
   `db/query.sql` (`sqlc generate`, never hand-edit `db/generated/`). Frames and exports go to object
   storage through `server/objects.py`. No local-disk paths.
2. **No fallbacks.** Missing config or an unsupported runtime → fail at startup or return 400/409 with a clear message.
3. **Workspace rules apply.** Every new route uses `current_workspace` + `require` (`server/auth_api.py`), every write is
   covered by the audit middleware (`server/audit_api.py`), and scoped API keys keep working (sandbox scope, read-only).
4. **No new unit tests.** Each step ends with an end-to-end check (compose + curl) added to `scripts/e2e.sh`.
5. **Do not commit, push, or create branches.** Leave changes in the working tree. The user commits.
6. **Do not test in a browser.** UI steps: `cd web && bun run build` and stop. The user checks UI.
7. Match the existing code style: same naming, same comment density, docstrings like the surrounding files.
8. After every step run: `uv run ruff check . && uv run ruff format --check . && uv run basedpyright`. Fix what you broke.

## Map of the code you will touch

| Area | Files |
|---|---|
| Viewer | `server/vnc.py`, `server/tickets.py` (30 s user tickets), `vnc-ticket` / `terminal-ticket` routes in `server/sandbox_api.py`, `web/src/components/vnc-screen.tsx`, `web/src/routes/view.$sandboxId.tsx` |
| Agent runs | `server/agent_api.py` (`PROVIDERS`, `AgentSettingsRequest`, `Run.emit()`, `follow()`, `/agent/stream` SSE) |
| Screens | `server/screen.py`, `server/a11y.py`, `server/tools.py`, `server/executions.py` (`tool_executions`) |
| Workers | `server/workers.py` (`works()`, `hold()` leases), `server/jobs.py` |
| Limits | `server/quotas.py`, `server/sizes.py`, `server/limits.py` |
| Snapshots | `/sandboxes/{id}/snapshots` in `server/sandbox_api.py`, `snapshots/` keys in `server/docker.py` |
| SDK / examples | `sdk/python`, `examples/` |

---

## Part A — Embed and share

### Step 1. Signed viewer URLs

- New migration: `viewer_links (id TEXT PK, workspace_id FK, sandbox_id FK ON DELETE CASCADE, token_hash TEXT UNIQUE,
  mode TEXT CHECK (mode IN ('view', 'control')), end_user TEXT, created_by FK users, expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ, created_at)`.
- `POST /sandboxes/{id}/viewer-links` (member+; a scoped key may create links only for its sandbox, a read-only key only
  `view`) → `{url, token, expires_at}`. Max expiry `ZOO_MAX_VIEWER_LINK_HOURS` (default 24). Store only the SHA-256.
  `GET` lists, `DELETE .../{link_id}` revokes.
- `GET /embed/{token}`: a minimal page (no app shell, no login) that opens the VNC websocket with the token. The websocket
  accepts either a user ticket (today) or a viewer-link token. `view` mode drops every input message server-side in
  `server/vnc.py` — not only in the client.
- Headers on `/embed/*`: `Content-Security-Policy: frame-ancestors` from `ZOO_EMBED_ORIGINS` (unset = `'none'`, so
  embedding must be configured explicitly).

Done when: create a `view` link, open its websocket with a raw client, send a key event → no effect in the sandbox; a
`control` link moves the mouse; a revoked or expired link → 401 on connect; an open connection is closed within 60 s of
revoke.

### Step 2. React component and iframe

- New package `sdk/react` (`@zoo/react`): `<ZooViewer url mode onTakeover />` wrapping the same noVNC code as
  `web/src/components/vnc-screen.tsx` (move the shared code into the package and use it from `web/`).
- Iframe docs: `<iframe src="{ZOO_URL}/embed/{token}">` and the `ZOO_EMBED_ORIGINS` setting.
- Python SDK: `sandbox.viewer_link(mode, expires_in, end_user=None)`.

Done when: `bun run build` in `sdk/react` and `web/` passes. Stop; the user checks UI.

### Step 3. Share links and takeover

- Share link = viewer link (step 1) shown in the UI with a copy button and expiry picker, view or control.
- Takeover: a `control` viewer sending input while an agent run is `running` pauses the run.
  - `agent_runs.state` gets `paused`; `agent_runs` gets `paused_by TEXT` (user id or `link:<id>`) and `paused_at`.
  - The run loop checks the state between steps (same place it checks `cancel_requested`) and waits while paused.
  - `POST /sandboxes/{id}/agent/runs/{run_id}/takeover` and `/release` (member+, or a `control` link token). Release
    resumes the run with a message "the user changed the screen" added to its context.
- UI: Share dialog on the sandbox page; Take over / Hand back buttons in the viewer (build only).

Done when: start an agent run, call `takeover` → run state `paused` and no new steps; `release` → steps continue; both
show in the audit log.

---

## Part B — End users

### Step 4. Labels on sandboxes

- New migration: `sandbox_labels (sandbox_id FK CASCADE, key TEXT, value TEXT, PRIMARY KEY (sandbox_id, key))` with an
  index on `(key, value)`. This table is the only place labels live. Limits, validated in the request model: ≤ 20 keys,
  keys `[a-z0-9_./-]{1,63}`, values ≤ 255 chars.
- `POST /sandboxes` and `PATCH /sandboxes/{id}` take `labels`; `GET /sandboxes?label=key=value` (repeatable, AND).
- Agent runs and tool executions inherit the sandbox's labels at read time (join), not copies.

Done when: create sandboxes with `end_user=alice` and `end_user=bob`; `GET /sandboxes?label=end_user=alice` returns only
Alice's.

### Step 5. Keys and quotas per label

- `api_keys` gets `label_selector TEXT` (JSON object, e.g. `{"end_user": "alice"}`; null = none). A key with a selector:
  sees only sandboxes whose labels match, gets 404 on others, and every sandbox it creates gets the selector's labels
  forced on (a request trying to set a different value → 403).
- Quotas: `label_quotas (workspace_id FK, key TEXT, value TEXT, max_running_sandboxes, max_cpus, max_memory_mb,
  max_storage_gb, PRIMARY KEY (workspace_id, key, value))`, plus `label_quota_defaults (workspace_id, key, ...)` applied
  to every value of a key that has no explicit row. Enforce in the same transaction as the workspace quota in
  `server/quotas.py` (workspace lock first, then label usage). Over quota → 409 naming the label.
- API: `PUT/GET/DELETE /workspaces/{id}/label-quotas` (admin+).

Done when: a key for `end_user=alice` can't see Bob's sandbox; a default of 1 running sandbox per `end_user` → Alice's
second create is 409 while Bob can still create one.

---

## Part C — Events and usage

### Step 6. Event model and SSE

- New migration: `events (id TEXT PK, seq BIGINT IDENTITY UNIQUE, workspace_id FK, sandbox_id, run_id, type TEXT,
  data TEXT, created_at)`, index `(workspace_id, seq)`. Types: `run.started`, `run.step`, `run.finished`
  (`succeeded|failed|cancelled` in data), `takeover.started`, `takeover.ended`, `sandbox.state` (running, stopped,
  deleted).
- One function `events.emit(...)` inserts the row and `pg_notify('zoo_event', workspace_id)` in the same transaction.
  Call it from `Run.emit()`, the takeover routes and the sandbox state changes in the job handlers.
- `GET /events/stream?sandbox_id=&type=` (SSE; scoped keys see only what they may see). `Last-Event-ID` = `seq`
  resumes. Same LISTEN pattern as `follow()` in `server/agent_api.py`.
- Purge events older than `ZOO_EVENT_RETENTION_DAYS` (default 7) in a worker loop under `hold("events", ...)`.

Done when: `curl -N /events/stream` shows run.started → run.step… → run.finished for a run; disconnect, reconnect with
`Last-Event-ID` → missed events arrive, none duplicated.

### Step 7. Webhooks

- New migration: `webhooks (id, workspace_id FK, url TEXT, secret_encrypted TEXT, types TEXT, label_selector TEXT,
  enabled BOOLEAN, created_at)` and `webhook_deliveries (id, webhook_id FK CASCADE, event_id FK, attempts BIGINT,
  next_attempt_at TIMESTAMPTZ, status TEXT CHECK IN ('pending','delivered','failed'), last_error TEXT, response_status
  BIGINT)`.
- `events.emit` inserts a `pending` delivery per matching webhook (same transaction). A worker loop claims deliveries
  with `FOR UPDATE SKIP LOCKED`, POSTs JSON with headers `Zoo-Event-Id`, `Zoo-Timestamp`, `Zoo-Signature: v1=<HMAC-SHA256
  of "{timestamp}.{body}">`, 10 s timeout. Non-2xx → retry with backoff (1 m, 5 m, 30 m, 2 h, 6 h), then `failed`.
- Webhook URLs go through the existing egress/SSRF checks (no private IPs unless `ZOO_WEBHOOK_ALLOW_PRIVATE=1`).
- API (admin+): CRUD on `/webhooks`, `GET /webhooks/{id}/deliveries`, `POST /webhooks/{id}/test`. Secret shown once.
- UI: Settings → Webhooks (build only).

Done when: point a webhook at a local receiver (`python -m http.server`-style script in `scripts/`), run an agent → the
receiver gets each event with a signature that verifies; stop the receiver → deliveries retry and show `failed` after
the last attempt (shorten backoff with an env var for the test).

### Step 8. Usage metering

- New migration: `usage (workspace_id FK, sandbox_id TEXT, metric TEXT CHECK IN ('cpu_minutes','memory_mb_minutes',
  'storage_gb_hours','agent_steps','agent_tokens','agent_cost'), quantity DOUBLE PRECISION, period_start TIMESTAMPTZ,
  PRIMARY KEY (sandbox_id, metric, period_start))` — hourly buckets. `sandbox_id` is not an FK: usage outlives the
  sandbox. Labels are copied into `usage_labels (sandbox_id, key, value)` when the sandbox is created so deleted
  sandboxes still roll up by label.
- Worker loop every 60 s under `hold("metering", ...)`: for each running sandbox add allocated `cpus` and `memory_mb`
  minutes (allocation, not measured use — document this); hourly add `storage_gb_hours` for disk + snapshots + profiles.
  Upsert with `ON CONFLICT ... DO UPDATE SET quantity = usage.quantity + EXCLUDED.quantity`.
- Agent steps/tokens/cost: add in the same place `agent_runs.steps/tokens/cost` are updated.
- API: `GET /usage?from=&to=&group_by=sandbox|label:<key>|day&metric=` (admin+, or a key with a label selector for its
  own label) and `GET /usage/export` (CSV).

Done when: run a sandbox 5 minutes with 2 CPUs → ≈10 `cpu_minutes`; group by `label:end_user` returns one row per end
user; after deleting the sandbox its usage is still there.

---

## Part D — Data

### Step 9. Trajectory export

- `POST /agent/runs/{run_id}/export` (or a filter: `sandbox_id`, label, time range) → job writes
  `exports/{export_id}/trajectory.jsonl` and `exports/{export_id}/frames/{step}.png` (+ `{step}.a11y.json`). One JSONL
  line per step: `{run_id, step, timestamp, instruction, screenshot, a11y_tree, action, result, model, tokens}`.
- Capture what is missing today: when a step runs, store the accessibility tree (`server/a11y.py`) next to the
  screenshot in object storage (`agent/{run_id}/{message_id}.a11y.json.enc`), gated by workspace setting
  `capture_a11y` (default on).
- Result: a presigned URL to a `.tar.gz` of the export. Exports expire after `ZOO_EXPORT_RETENTION_DAYS` (default 7).

Done when: export one run; untar; JSONL line count = run steps; every referenced frame exists.

---

## Part E — Agents

### Step 10. Provider per run, xAI added

- `PROVIDERS` in `server/agent_api.py`: add `xai` (Grok vision model through LiteLLM's `xai/` prefix; check cua
  supports it — if cua has no loop for it, use the generic vision loop like Qwen3-VL and say so in a comment).
- Run requests take optional `provider`, `model`, `api_base`; the workspace setting is the default. Store
  `agent_runs.provider`. The key comes from the vault as today (`AGENT_<PROVIDER>_API_KEY`); missing key → 400 naming
  the secret.
- `GET /agent/providers` lists providers, default models and whether the workspace has a key.

Done when: two runs in one sandbox with different providers both finish (needs two real keys; with one key, check the
second fails with the clear 400).

### Step 11. Benchmark harness

- Task suite format (YAML/JSON): `tasks: [{id, instruction, setup: {snapshot_id?, commands?}, check: {command,
  expect_exit_code} | {judge: prompt}, timeout}]`.
- New migration: `benchmarks (id, workspace_id, suite TEXT, models TEXT, state, created_at)`,
  `benchmark_results (benchmark_id, task_id, model, run_id, sandbox_id, passed BOOLEAN, score DOUBLE PRECISION,
  steps, tokens, cost, seconds, error)`.
- `POST /benchmarks` (suite + models + `parallel`): for each task × model create a sandbox (from the task's snapshot via
  the snapshot restore path, labelled `benchmark=<id>`), run setup, start an agent run with that model, run the check,
  delete the sandbox. Respect workspace quotas; `parallel` is capped by them.
- `GET /benchmarks/{id}` → per-model pass rate, mean steps/cost/time, and per-task results; `export` as CSV.
- CLI: `python -m server.bench suite.yaml --models a,b`.

Done when: a 3-task suite against 1 model with `parallel=3` runs 3 sandboxes at once and reports a pass rate.

---

## Part F — Finish

### Step 12. Full suite, e2e, docs

- `ZOO_TEST_DATABASE_URL=... uv run pytest -q`; `scripts/e2e.sh` against `docker compose up -d --build --scale
  worker=2`; ruff + basedpyright clean; `helm lint`/`template` with new env vars added to the chart.
- Docs in `docs/`: embedding and share links, labels and end users, events/SSE/webhooks (with signature verification
  example), usage metering, trajectory export, providers, benchmarks.
- Add these items to `TODO.md` and tick what this plan delivered.
