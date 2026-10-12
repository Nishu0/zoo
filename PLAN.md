# Zoo: first five minutes — execution plan

Target: install, get a key and drive a desktop with an agent, reading nothing but the quickstart.

Do the steps in order. Finish each step's "Done when" before starting the next.

## Rules (read before every step)

1. **Postgres and object storage only.** New state goes in a goose migration in `db/migrations/` and queries in
   `db/query.sql` (`sqlc generate`; never hand-edit `db/generated/`).
2. **No fallbacks.** Missing config → fail at startup or a clear 4xx.
3. **Workspace rules apply.** New routes use `current_user` + `require` (`server/auth_api.py`), writes are audited, and
   scoped API keys (sandbox, labels, read-only) keep working.
4. **No new unit tests yet.** Build first; tests come in the last part.
5. **Do not commit, push or branch.** The user commits.
6. **No browser testing.** UI steps end at `cd web && bun run lint && bun run build`.
7. Match the surrounding code style.
8. After every step: `uv run ruff check . && uv run ruff format --check . && uv run basedpyright`.

## Already done (from the previous plan)

- Webhooks for sandbox state and agent completion (`server/webhooks.py`: `sandbox.state`, `run.finished`)
- SSE events stream (`server/events.py`: `GET /events/stream`)
- API keys stored as hash + prefix; the full key is returned once (`server/auth_api.py`)

---

## Part A — API quality (the SDKs and CLI build on it)

### Step 1. One error format
- Every error body is `{"error": {"code", "message", "hint", "docs_url"}}`. `code` is a stable snake_case string
  (`sandbox_not_found`, `quota_exceeded`, `validation_failed`, `internal_error`, …).
- Exception handlers in `server/server.py` for `HTTPException`, `RequestValidationError` and `Exception`. A 500 logs the
  traceback with a request id and returns only `internal_error` + the request id; never exception text.
- A `ZooError(status, code, message, hint=None)` helper in `server/errors.py`; existing `HTTPException(detail=...)` calls
  map to a code by status unless they pass one. `docs_url` points at `/docs/reference/errors#<code>`.
- Done when: a 404, a 422 and a forced 500 all return the shape; the errors page lists every code.

### Step 2. `/v1`
- Every public route is served under `/v1`. Unprefixed paths keep working and answer with `Deprecation: true` and
  `Link: </v1/...>; rel="successor-version"`. The web app, SDKs, MCP config and docs use `/v1`.
- Deprecation policy in the docs: a version is supported 12 months after its successor ships; removals are announced in
  the changelog and by the `Deprecation`/`Sunset` headers.
- Done when: the OpenAPI schema lists `/v1` paths only, and an old path returns the headers.

### Step 3. Idempotency keys
- Migration: `idempotency_keys (workspace_id, key, method, path, request_hash, status, response JSONB, created_at,
  PK (workspace_id, key))`, purged after 24 h.
- `Idempotency-Key` header on `POST /v1/sandboxes`, snapshots, viewer links, webhooks, API keys, agent runs, exports and
  benchmarks: a repeat with the same body returns the stored response; a different body returns 409
  `idempotency_key_reused`; one still in flight returns 409 `idempotency_key_in_progress`.
- Done when: two identical creates with one key make one sandbox.

### Step 4. Pagination
- Cursor pagination on `GET /v1/sandboxes`, `/v1/sandboxes/{id}/executions`, `/v1/audit` (and webhooks deliveries):
  `?limit=` (default 50, max 200) and `?cursor=`; responses `{"data": [...], "next_cursor": str | null}`.
- Cursor = opaque base64 of `(created_at, id)`; queries use keyset order, not OFFSET.
- Done when: walking all pages returns every row once; the web app follows cursors.

### Step 5. Dashboard on events
- Replace the 5-second `refetchInterval` polling in `web/src` with one `EventSource` on `/v1/events/stream` that
  invalidates the affected queries; keep polling only where no event exists.
- Done when: no `refetchInterval` remains for sandbox status; build passes.

## Part B — SDKs

### Step 6. Python SDK (`sdk/python`, `zoo-sdk`)
- `Zoo` and `AsyncZoo` clients (httpx), `py.typed`, full type hints, models matching the API.
- Every call has a timeout (default 30 s, per-call override); retries with exponential backoff + jitter on connect
  errors, 429 and 5xx for idempotent calls, and on creates via an automatic `Idempotency-Key`.
- `with zoo.sandbox(kind="desktop") as box:` / `async with` — always deletes on exit, also on error.
- `box.exec(cmd, stream=True)` yields output as it arrives (server streaming endpoint added if missing).
- Errors raise `ZooError` subclasses carrying `code`, `message`, `hint`, `docs_url`.
- GitHub Actions workflow to build and publish to PyPI on a `sdk-py-v*` tag (trusted publishing). The user publishes.
- Done when: `uv build` makes a wheel; a script creates, execs (streamed) and cleans up against compose.

### Step 7. TypeScript SDK (`sdk/typescript`, `@zoo/sdk`)
- Types generated from `/v1/openapi.json` with `openapi-typescript`; a hand-written `Zoo` client and `Sandbox` class
  with the same methods, timeouts, retries and errors as Python.
- `await using box = await zoo.sandbox({ kind: "desktop" })` (`Symbol.asyncDispose`) plus `zoo.withSandbox(fn)`.
- Streaming exec as an async iterator. ESM + CJS build; npm publish workflow on `sdk-ts-v*`.
- Done when: `bun run build` passes and the same script as step 6 runs in TS.

### Step 8. Framework adapters
- `zoo_sdk.adapters`: Claude Agent SDK (tools / in-process MCP server), OpenAI Agents SDK (`function_tool`s + computer
  tool), LangGraph (LangChain tools). `@zoo/sdk/ai`: Vercel AI SDK `tool()`s. Optional extras; nothing imported unless
  used.
- One example per framework in `examples/`.
- Done when: each example typechecks.

## Part C — CLI

### Step 9. `zoo` user commands
- Console script `zoo` in `zoo-sdk` (Typer): `login` (stores URL + key in `~/.config/zoo/config.toml`, 0600), `create
  --kind desktop`, `ls`, `exec <id> -- cmd` (streamed, exit code passed through), `shell <id>` (terminal WebSocket, raw
  TTY), `view <id>` (opens a viewer link in the browser), `cp` (`<id>:path` either way), `logs <id> [-f]`, `rm`.
- `--json` on every read command; `ZOO_URL`/`ZOO_API_KEY` override the config.
- Done when: each command works against compose (checked with the shell, not a browser, for `view`).

### Step 10. Operator commands
- Same binary: `zoo server upgrade` (pull images, run migrations, restart), `zoo server backup` / `restore`
  (`pg_dump` + object store, matching the existing backup), `zoo server doctor` (Postgres, object store, runtimes,
  KVM/Docker, ports, versions; one line each, non-zero exit on failure).
- Done when: `doctor` passes on compose and fails clearly with Postgres down.

## Part D — First run

### Step 11. Onboarding
- Migration: `users.onboarded_at`. First login without it lands on `/welcome`: 1) create a sandbox, 2) create and copy
  an API key (shown once, with the warning), 3) copy an MCP config with the key and `/v1` URL filled in. Skip and
  finish both set `onboarded_at`.
- Done when: build passes; `PUT /v1/me/onboarded` works.

### Step 12. Connect
- "Connect" button on each sandbox: a dialog with tabs MCP JSON, curl, Python, TypeScript, each with the sandbox id and
  URL filled in and a key placeholder (or a new key on request), and a copy button.
- Done when: build passes.

### Step 13. Seeded demo
- On a workspace's first sandbox create from onboarding, offer "Start with a demo": a desktop sandbox labelled
  `demo=true` and a suggested task ("Open Firefox, search for …, summarise the top result") pre-filled in the Agent
  tab. No sandbox is created without the user asking.
- Done when: build passes; the API path for it works with curl.

## Part E — Dashboard polish

### Step 14. Split the sandbox page
- `web/src/routes/_app/sandboxes/$sandboxId.tsx` (2,573 lines) becomes a layout route plus one route per tab
  (`overview`, `screen`, `terminal`, `files`, `agent`, `executions`, `snapshots`, `share`, `settings`), shared pieces in
  `web/src/components/sandbox/`. URLs per tab.
- Done when: no file over ~500 lines; build passes.

### Step 15. Every state shown
- Provisioning shows its steps (from `sandbox.state` events and the job's stage); failed shows the reason and Retry;
  an unreachable server shows a banner with retry; loading and empty states everywhere.
- Done when: build passes.

### Step 16. Palette, shortcuts, empty states
- `⌘K` command palette (go to sandbox/page, create sandbox, copy id, open Connect); shortcuts (`c` create, `g s`
  sandboxes, `?` help); every empty list says what to do next with a button.
- Done when: build passes.

### Step 17. Keys shown once
- The created-key dialog shows the full key once with a "you won't see this again" warning and copy; lists show the
  prefix only (server side already does).
- Done when: build passes.

## Part F — Finish

### Step 18. Quickstart, docs, tests
- Rewrite the quickstart around the five-minute path: install, `zoo login`, create, connect an agent.
- Reference pages: errors, versioning, idempotency, pagination, SDKs, CLI.
- Unit tests for steps 1–4 and the SDK retry/cleanup logic; `scripts/e2e.sh` covers the CLI path.
- Done when: the full suite and e2e pass.
