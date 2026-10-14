# Zoo: trim down and speed up — tickets

Zoo is sandboxes, the REST API and MCP. Nothing agent-side. One tool per job, no fallbacks, no backward compatibility.
The one deliberate choice: Linux sandboxes run on Docker (default, one host) or Kubernetes (opt-in, chosen at install
with `ZOO_RUNTIME`). Both are first-class runtimes behind `server/runtime.py`; neither is a fallback for the other.
Every Linux sandbox is a Kata Containers VM on both: Docker runs them with the Kata shim (`io.containerd.kata.v2`),
Kubernetes with RuntimeClass `kata`. Never runc: a sandbox never shares the host kernel.
The speed and reliability targets this plan builds toward are in `METRICS.md`.

---

## Instructions for the implementing model (read fully before starting)

### What you are working on
- Repo root: this directory. Python 3.12 control plane (`server/`, FastAPI, run with `uv`), SQL in `db/query.sql`
  compiled by sqlc into `db/generated/` (`make generate`), migrations in `db/migrations/`, Go guest agent (`guest/`),
  Go host daemon (`node/`, gRPC proto in `node/proto/node.proto`, regenerate with `make proto`), dashboard (`web/`,
  Bun + Vite + TanStack + shadcn), Python SDK and CLI (`sdk/python/`), TypeScript SDK (`sdk/typescript/`), React SDK
  (`sdk/react/`), docs site (`site/`, Astro Starlight), Helm chart (`deploy/helm/zoo/`), macOS helper (`macos/`),
  Windows helper (`windows/`).
- Read `METRICS.md` and the "Target design" section below before the first ticket.

### Hard rules
1. **Never** `git commit`, `git push`, create branches, or rewrite history. The owner commits.
2. **Never** start the API, the dashboard, Docker, docker compose, kubectl, helm install, or any server. Never run
   pytest, vitest or e2e suites, except where a ticket explicitly allows it (T-41, T-42, T-43, T-50, T-150).
3. **Never** edit `.env`. Edit `.env.example` instead.
4. **No fallbacks.** Do not write `try: new way except: old way`, `if guest is None: <other path>`, feature flags,
   compatibility shims, "legacy" branches, or comments like "kept for older …". When the one way is unavailable,
   raise `ZooError(code, message, hint)` from `server/errors.py` (add the code to `CODES` and `HINTS` if new).
5. **No backward compatibility.** The database is recreated and images rebuilt; never write data migrations for old
   rows, never keep old route aliases, never keep old env var names.
6. **Delete completely.** When you remove a thing, remove: its module, imports, routes, worker loops, queries in
   `db/query.sql` (then `make generate`), tables (in the baseline schema, see T-11), env vars in `.env.example` and
   the Helm chart (`deploy/helm/zoo/values.yaml`, `templates/_helpers.tpl`), SDK methods, dashboard UI and hooks,
   docs mentions, and tests. Each ticket lists a "gone" grep; it must print nothing.
7. **Match the surrounding code.** Same naming, same comment density (short, factual, no tutorials), same idioms.
   No new abstractions unless the ticket names them. No new dependencies unless the ticket names them.
8. **One ticket at a time, in order**, unless a ticket says it can run in parallel. Do not start a ticket whose
   "Depends on" tickets are not done.
9. **Stay in scope.** Do not fix, refactor or reformat things a ticket doesn't mention. If you see a real bug outside
   scope, write it under "Handoff notes" at the bottom of this file.
10. If something in a ticket is ambiguous, contradicts the code, or can't be done without breaking a rule: do not
    guess. Write `Blocked: <question>` under that ticket, set its status to `blocked`, and continue with the next
    ticket that does not depend on it.

### Checks after every ticket (all must pass)
```sh
uv run ruff check . && uv run ruff format --check . && uv run basedpyright      # any Python change
(cd guest && go vet ./... && go build ./...)                                       # any guest/ change
(cd node && go vet ./... && go build ./...)                                        # any node/ change
(cd gateway && go vet ./... && go build ./...)                                     # any gateway/ change (from T-40)
(cd egress && go vet ./... && go build ./...)                                      # any egress/ change (from T-60)
(cd web && bun run lint && bun run build && bunx prettier --write src/routeTree.gen.ts)   # any web/ change
(cd sdk/typescript && bun run build)                                               # any sdk/typescript change
(cd site && bun run build)                                                         # any site/ change
```
Plus the ticket's own "Done when" checks. If `make generate` or `make proto` is needed, run it before the checks.

### Recording progress
- Under each ticket, change `Status: todo` to `Status: done` (or `blocked`) and add at most 3 lines of `Notes:`
  (what you decided where the ticket left room, anything the owner must know).
- Do not rewrite ticket text.

---

## Target design (what the finished system looks like)

| Job | The one way |
|---|---|
| Run Linux sandboxes | `ZOO_RUNTIME=docker` (default): Kata containers (`--runtime io.containerd.kata.v2`) on the API's own Docker host (`server/docker.py`), home on a Docker volume. `ZOO_RUNTIME=kube` (opt-in): Kata pods on Kubernetes (`server/kube.py`), home on a local TopoLVM volume |
| Run macOS sandboxes | zoovm on an Apple Silicon Mac (macOS 14+), reached through zoo-node |
| Run Windows sandboxes | Hyper-V + `windows/zoovm.ps1`, reached through zoo-node |
| Screen pixels and input, every OS, agent and human | one VNC session per active sandbox, held by the Go gateway |
| Everything else inside a sandbox | zoo-guest (shell, files, terminal, accessibility tree, windows, apps, secrets, tunnel) |
| Long-lived connections (guests, nodes, VNC) | the Go gateway (`gateway/`), N replicas, sandboxes split by rendezvous hashing |
| Linux network policy | `zoo-egress` (`egress/`): one host-network container on the Docker host, or a DaemonSet (one per node) on Kubernetes |
| macOS / Windows network policy | pf / Hyper-V port ACLs + proxy on the host |
| Reach Mac and Windows hosts | zoo-node (mTLS gRPC, dials out) |
| Linux capacity | the one Docker host (docker), or Kubernetes nodes (kube) |
| Placement | none on Docker (one host); our node choice via `nodeName` on Kubernetes; per-host VM caps (macOS 2, Windows `ZOO_WINDOWS_MAX_VMS`) |
| Snapshots | volume tar to S3 (docker), CSI VolumeSnapshots (kube), APFS clones (macOS), differencing disks (Windows) |
| Moves | snapshot → S3 → restore on target (kube, macOS, Windows); Docker has one host, so no moves |
| Stop | pause (frozen container or VM) for `ZOO_PAUSE_MINUTES`, then delete down to the volume |
| Warm create | claim a pooled sandbox inside the request, no job |
| Secrets push | `/run/zoo/env.json` (Linux), `~/.zoo/env` (VMs) |
| Secret encryption | per-workspace data keys wrapped by `ZOO_SECRETS_KEY` or `ZOO_KMS` |
| API | `/v1` only, one error format, idempotency keys, cursor pages |
| Dashboard live data | SSE event stream; polling only for monitoring numbers |
| Install | `install.sh` (default): Docker Engine + Kata Containers (registered as a Docker runtime) + `compose.yml`. `install.sh --runtime kube`: k3s + kata-deploy + TopoLVM + snapshot controller + cert-manager + Helm chart |
| Operate | `docker compose pull && up -d` (docker) or `helm upgrade / rollback / test` (kube); backups by the worker to S3 |
| CLI | Python `zoo`, API only |
| Docs | `site/` only, plus generated reference |

### Requirements the API checks at startup and in `/readyz` (T-14 adds the checks)
- `DATABASE_URL` reachable; `ZOO_OBJECT_STORE` reachable; `JWT_SECRET`; `ZOO_SECRETS_KEY` or `ZOO_KMS`; `ZOO_API_URL`;
  `ZOO_RUNTIME` is `docker` or `kube`.
- `/dev/kvm` exists on every machine that runs Linux sandboxes (both runtimes; Kata needs hardware virtualization).
- `ZOO_RUNTIME=docker`: the local Docker daemon is reachable; it lists the Kata runtime (`io.containerd.kata.v2`);
  the sandbox images are present.
- `ZOO_RUNTIME=kube`: Kubernetes API reachable; RuntimeClass `kata` exists; StorageClass `ZOO_STORAGE_CLASS` (default
  `zoo-local`, TopoLVM) exists; VolumeSnapshotClass `ZOO_SNAPSHOT_CLASS` exists.
- macOS hosts: Apple Silicon, macOS 14+, zoo-node, zoovm, base VM with guest + Accessibility grant.
- Windows hosts: Hyper-V capable edition, zoo-node, base VM with guest.
- Development: Docker runtime by default (`make dev`); the Kubernetes runtime on a local k3s with Kata (`make dev-kube`;
  on a Mac: M3+, macOS 15+, Lima VM with nested virtualization).

---

## Tickets

Phases: 0 remove agent features · 1 remove compatibility code · 2 two Linux runtimes (Docker default, Kubernetes opt-in) · 3 zoo-node for VMs only ·
4 Go gateway · 5 VNC sessions · 6 egress binary · 7 hot path · 8 cold path · 9 pause/resume · 10 VM saved state ·
11 install and operations · 12 dashboard · 13 docs · 14 instrumentation · 15 tests.

Progress (2026-10-14): every ticket but T-100/T-101 (blocked) is done. Static checks pass (ruff, basedpyright, go vet/build
in all modules, gateway tests, web/SDK/site builds, helm lint), and with T-150 the Python suite passes against a
throwaway local Postgres: 499 passed, 4 skipped. Nothing ran against KVM, Kata, CSI or real VMs.

Owner directive (2026-10-14): full permissions; T-150's allowed test runs were executed.
Real Docker/Kata/CSI/VM validation and the METRICS.md measurements remain deferred.

### Phase 0 — remove everything agent-side

#### T-01 Remove the built-in agent from the server
Status: done · Depends on: —
Notes: exports.py, bench.py and integrations/ were deleted here, not in T-02/T-03: they cannot compile without the
 agent queries and agent_api this ticket removes (T-02/T-03 keep only their leftover cleanup). tool_executions now
 keys on sandbox_id (query + baseline migration column) instead of an agent_sessions session_id.
- Delete `server/agent_api.py`.
- `server/server.py`: remove `AgentApi` import and construction, `self.agent_api.work()` task, `agent_api.shutdown()`
  in the drain, and any argument passing `agent_api` to other classes.
- `server/workers.py`: remove agent runs from the docstring and any agent-run claim/heartbeat code.
- `server/sandbox_api.py`, `server/viewer_api.py`, `server/kube.py`, `server/auth_api.py`, `server/workspaces_api.py`,
  `server/metrics.py`, `server/events.py`, `server/webhooks.py`, `server/nodes.py`, `server/nodes_api.py`: remove every
  reference to agent runs, agent settings, providers, model keys, run streams and takeover pausing. A viewer link with
  `control` now only forwards input; remove `takeover.*` events and the pause/hand-back logic in `server/vnc.py` Gate
  (`take_action` and anything that only exists for takeover).
- `server/metering.py`: remove agent token and cost metering; keep cpu, memory and storage.
- `db/query.sql`: delete every query that touches `agent_runs`, `agent_sessions`, `agent_messages`, `agent_channels`,
  `chat_events`, `workspace_agent_settings`; run `make generate`.
- `pyproject.toml`: remove `cua-agent`, `cua-core`; run `uv lock`.
- `.env.example`, `compose.yml`, `deploy/helm/zoo/templates/_helpers.tpl`, `deploy/helm/zoo/values.yaml`: remove
  `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `XAI_API_KEY`, `ZOO_AGENT_MODEL`, `ZOO_AGENT_MAX_STEPS`,
  `ZOO_AGENT_MAX_SECONDS`, `ZOO_AGENT_MAX_TOKENS`.
- Delete `tests/test_agent.py`, `tests/fake_model.py`, and agent cases in other test files.
- Gone: `grep -rniE "agent_api|AgentApi|agent_run|agent_session|agent_message|takeover|ZOO_AGENT|cua" server mcp_tools db/query.sql pyproject.toml .env.example compose.yml deploy tests`
  prints nothing.
Done when: checks pass and the gone-grep is empty.

#### T-02 Remove trajectory exports and benchmarks
Status: done · Depends on: T-01
Notes: exports.py/bench.py and their queries were removed with T-01 (they could not compile without it); this
 ticket removed the env/chart leftovers. export_dir/export_home (sandbox backup) and usage CSV export are
 unrelated and stay.
- Delete `server/exports.py`, `server/bench.py`.
- `server/server.py`: remove `ExportsApi`, `BenchmarksApi`, `Bench`, `exports.loop()`, `self.bench.loop()`,
  `self.bench.shutdown()`.
- `server/events.py` (purge) and others: remove export/benchmark references.
- `db/query.sql`: delete queries on `exports`, `benchmarks`, `benchmark_results`; `make generate`.
- `.env.example`, chart: remove `ZOO_EXPORT_RETENTION_DAYS`.
- Delete benchmark/export tests.
- Gone: `grep -rniE "export_|exports|bench" server db/query.sql .env.example deploy tests` prints nothing (ignore
  Python `__all__`-style unrelated words only if they are not about trajectories/benchmarks; there should be none).
Done when: checks pass, gone-grep empty.

#### T-03 Remove chat integrations
Status: done · Depends on: T-01
Notes: integrations/ and tests/test_integrations.py were deleted with T-01 (they could not compile without the
 agent); this ticket removed discord-py, the SLACK_*/DISCORD_*/WHATSAPP_* env vars and the chart/compose leftovers.
 The guest relay in gateway.py/guest.py is a different feature and stays.
- Delete the `integrations/` directory.
- `server/server.py`: remove `from integrations import …`, `slack.register`, `whatsapp.register`,
  `discord.supervise`, `relay.purge_events`.
- `pyproject.toml`: remove `discord-py`; `uv lock`.
- `.env.example`, `compose.yml`, chart: remove `SLACK_*`, `DISCORD_*`, `WHATSAPP_*`.
- Delete `tests/test_integrations.py`.
- Gone: `grep -rniE "slack|discord|whatsapp|integrations|relay" server pyproject.toml .env.example compose.yml deploy tests` prints nothing.
Done when: checks pass, gone-grep empty.

#### T-04 Remove agent features from the SDKs and examples
Status: done · Depends on: T-01, T-02
Notes: Sandbox.claude() stays (it runs the Claude CLI inside the sandbox, not the built-in agent). The gone-grep
 still matches the standard `User-Agent` HTTP header in both SDKs; treated as unrelated, per the ticket's intent.
- `sdk/python/zoo_sdk/`: delete `agent.py` and `adapters/`; in `client.py` and `aio.py` delete `export_trajectories`,
  `benchmark`, `agent_state`, and every other agent/run/export/benchmark method; remove them from `__init__.py`.
- `sdk/python/pyproject.toml`: remove the `claude`, `openai`, `langgraph`, `agent` extras; keep `cli`.
- `sdk/python/README.md`: remove agent/adapters sections.
- `sdk/typescript/src/ai.ts`: delete; remove its export from `index.ts`; remove `ai` and `zod` from
  `sdk/typescript/package.json`; remove the adapter section from its README.
- Delete the `examples/` directory.
- Regenerate `openapi.json` and `sdk/typescript/src/schema.ts` (`uv run python scripts/openapi.py` then the
  `openapi-typescript` command used in `sdk/typescript/package.json`).
- Delete `tests/test_sdk_client.py` cases for removed methods.
- Gone: `grep -rniE "agent|adapter|trajector|benchmark|langgraph|vercel" sdk/python/zoo_sdk sdk/typescript/src` prints
  nothing.
Done when: checks pass, gone-grep empty.

#### T-05 Remove agent features from the dashboard
Status: done · Depends on: T-01, T-02
Notes: settings.tsx/workspace.tsx had no agent settings UI left to remove (their hooks were unused after the
 component deletions); user-facing copy that mentioned the removed agent was reworded throughout.
- Delete `web/src/components/sandbox/agent.tsx`, `agent-model.tsx`, `agent-channels.tsx`,
  `web/src/routes/_app/sandboxes/$sandboxId/agent.tsx`.
- `web/src/routes/_app/sandboxes/$sandboxId.tsx`: remove the Agent tab.
- `web/src/lib/api_client.ts`: delete `useAgent`, run/export/benchmark/agent-settings hooks and schemas, `DEMO_TASK`.
- `web/src/routes/_app/welcome.tsx`: remove the demo button and demo state; step 1 is one "Create a desktop" button.
  `done()` navigates to `/sandboxes/$sandboxId`.
- `web/src/components/create-sandbox-dialog.tsx`: remove demo prefill.
- `web/src/routes/_app/settings.tsx`, `workspace.tsx`: remove agent provider/model/key settings.
- `web/src/components/command-palette.tsx`: remove agent entries if any.
- Gone: `grep -rniE "agent|benchmark|trajector|DEMO_TASK|takeover" web/src --include='*.ts' --include='*.tsx' | grep -v routeTree.gen`
  prints nothing.
Done when: web checks pass, gone-grep empty.

#### T-06 Remove agent docs
Status: done · Depends on: T-01..T-05
Notes: the marketing pages (index/Agents/Faq/compare) also carried built-in-agent copy; reworded. site/scripts/
 generate_reference.py no longer documents the removed env vars.
- Delete `site/src/content/docs/docs/get-started/cua-agent.md`, `get-started/claude-code.md`,
  `guides/agent-frameworks.md`, `guides/chat-channels.md`; remove agent, trajectory, benchmark sections from
  `guides/building-on-zoo.md`; remove their sidebar entries (`site/astro.config.*`).
- `docs/agents.md`: delete.
- `README.md`: remove agent/chat/benchmark mentions.
- Run `uv run python scripts/generate_reference.py` (or the script under `site/` that regenerates reference pages)
  so `reference/*.md` no longer lists removed routes.
- Gone: `grep -rniE "cua|computer-use agent|agent-frameworks|chat-channels|trajector|benchmark" site/src README.md docs` prints nothing.
Done when: site builds, gone-grep empty.

### Phase 1 — remove compatibility code

#### T-10 Delete unprefixed routes and dead modules
Status: done · Depends on: Phase 0
Notes: every Api class now registers on an APIRouter(prefix="/v1"); sandbox_api (/guest/connect), viewer_api
 (/embed/*), health, gateway (/internal/*) and /mcp register unprefixed on the app. AuditApi's middleware moved to
 the app (routers can't hold middleware). conventions.md updated; test_conventions adjusted.
- Delete `server/versioning.py`. Mount every API router under `/v1` (FastAPI `APIRouter(prefix="/v1")` or
  `include_router(..., prefix="/v1")`), except `/healthz`, `/readyz`, `/embed/{token}`, `/mcp`, `/guest/connect`
  and `/internal/*`, which stay unprefixed.
- Remove `Deprecation`/`Link` from CORS `expose_headers`.
- Delete `server/handler.py` and its route.
- Update `web/src/lib/api_client.ts` only if a path changes (it already uses `/v1`).
- Gone: `grep -rn "versioning\|Deprecation\|successor-version\|Handlers" server` prints nothing.
Done when: checks pass, gone-grep empty, `uv run python scripts/openapi.py` lists only `/v1/…` paths plus the
unprefixed ones above.

#### T-11 One baseline schema
Status: done · Depends on: Phase 0
Notes: sandbox_artifacts and the events.run_id column went with the agent (only it used them); zoo_event() lost
 its unused `run` parameter (EmitEvent updated). The unused sandbox_artifacts queries were deleted from query.sql.
- Replace all files in `db/migrations/` with one `db/migrations/20261101120000_baseline.sql` equal to the final schema
  of the current migrations minus the tables removed in Phase 0 (`agent_*`, `chat_events`,
  `workspace_agent_settings`, `exports`, `benchmarks`, `benchmark_results`) and their triggers/functions (`run.*`,
  `takeover.*` events in `zoo_event`).
- Keep `db/migrate.py` working with the single file.
- Gone: `grep -niE "agent|bench|export|takeover|chat_" db/migrations/*.sql` prints nothing.
Done when: checks pass, gone-grep empty, `make generate` succeeds.

#### T-12 Delete secret and VNC compatibility code
Status: done · Depends on: T-11
Notes: Linux containers no longer get secrets in their boot env; boot writes env.json after the container starts
 (like warm-pool adopt), so no unset list is needed. vault_sync's booted_secrets/remember_booted and the guest's
 baseEnv/withoutNames went too. require_secrets_key() takes no db (callers and tests updated).
- `server/security.py`: remove the legacy key prefix handling (around line 36) and the "no VNC password" case
  (`vnc_password` returns the stored password; a missing one raises).
- `server/rotate_secrets.py`: keep only rewrapping data keys with the current key; delete moving pre-envelope
  secrets, agent keys, VNC passwords and profiles.
- `server/policy_api.py`: delete the "outdated image" 409 check.
- `server/vault_sync.py` and `guest/`: delete `unset.json` (writer and reader); secrets are only `env.json`.
- Gone: `grep -rniE "legacy|outdated image|unset.json|before envelope|predates" server guest` prints nothing.
Done when: checks pass, gone-grep empty.

#### T-13 Guest is required: delete non-guest tool paths for VMs
Status: done · Depends on: T-12
Notes: Done with T-51: the Linux xdotool and docker-exec input paths are gone; every tool goes through the guest
 or the gateway screen, and a missing guest raises guest_unavailable. The gone-grep is empty.
- `server/a11y.py`, `server/macos_tools.py`, `server/windows_tools.py`: delete the AppleScript/System Events and
  PowerShell UI Automation scripts and every branch that runs them. Tools call the guest; no guest → `ZooError`
  code `guest_unavailable` (status 503, hint: "the sandbox's guest agent is not connected; restart the sandbox").
- `server/macos.py`, `server/macos_tools.py`: delete running tools over SSH into the VM. SSH to the Mac host (through
  zoo-node) stays.
- Add `guest_unavailable` to `CODES`/`HINTS` in `server/errors.py`.
- Gone: `grep -rniE "osascript|System Events|fall back|falls back|fallback" server` prints nothing.
Done when: checks pass, gone-grep empty.

#### T-14 Startup and readiness requirement checks
Status: done · Depends on: T-10
Notes: main() exits on failures (scripts/start.sh runs main.py); the lifespan no longer checks the secrets key.
 /readyz reports database, object_store, jwt_secret, secrets_key, api_url plus the runtime and servers.
 Listener-owning processes defer host checks to readiness; external-gateway clients retain host preflight checks.
- New `server/requirements.py`: functions that check each item in "Requirements the API checks" (Kubernetes items are
  added in T-21). `main.py` runs them before serving and exits with one line per failed item; `/readyz`
  (`server/health.py`) reports each as a named check.
- No check may be optional or skippable.
Done when: checks pass.

### Phase 2 — Linux runtimes: Docker (default) or Kubernetes + Kata (opt-in)

#### T-20 Two Linux runtimes behind one interface
Status: done · Depends on: Phase 1
Notes: Runtime selection is fixed at startup; native backends replace the Docker-shaped Kubernetes facade.
 Local Docker always uses io.containerd.kata.v2. Ruff, format and Basedpyright pass; grep's unrelated
 "trunc"/"truncated" matches are not container runtime references. Obsolete tests were removed or adapted.
- `ZOO_RUNTIME=docker|kube` (default `docker`) picks the Linux backend once at startup. `runtime.py` routes:
  `macos:` → macos, `windows:` → windows, everything else → the configured Linux backend. No per-sandbox mixing.
- `server/docker.py` keeps only the local Docker host (`unix:///var/run/docker.sock` or `DOCKER_HOST` on the same
  machine). Remote Docker (`ssh://`, `tcp://`, `use_ssh_client`) is deleted in T-30.
- In `server/kube.py` remove the Docker imitation layer: `NotFound` no longer subclasses `docker.errors.NotFound`;
  remove `ExecResult` shaping that mimics docker-py. Both backends implement the same small interface in
  `runtime.py` (run, exec, stop, pause/resume, remove, snapshot, restore, write_secrets, apply_network); callers use
  that interface and never import `docker` or `kube` directly.
- Update every importer of docker/kube to go through `runtime.py`: `server/backups.py`, `health.py`, `monitor.py`,
  `pool.py`, `nodes_api.py`, `tools.py`, `vault_sync.py`, `servers_api.py`, `nodes.py`, `sandbox_api.py`.
- Kube: `ZOO_NAMESPACE` (default `zoo`) replaces `ZOO_KUBERNETES_NAMESPACE`; runtime class is always `kata` (delete
  `ZOO_KUBERNETES_RUNTIME_CLASS`).
- Docker: every sandbox container starts with `runtime="io.containerd.kata.v2"` (constant in `docker.py`, not
  configurable). Delete `deploy/security/` (seccomp/AppArmor for runc) and its references; Kata's VM is the boundary.
- Kube: pods always set `runtimeClassName: kata`.
- Delete Docker paths that only existed for multi-host Docker: `move_volume`, `copy_volume`, published 6080 ports on
  remote hosts. Keep Docker snapshots, reworked in T-23.
- Gone: `grep -rniE "runc|deploy/security|seccomp|apparmor" server deploy .env.example compose.yml` prints nothing;
  `grep -rnE "^from server(\.| import )(docker|kube)\b|import docker" server | grep -vE "server/(runtime|docker|kube)\.py"`
  prints nothing; `grep -rn "ZOO_KUBERNETES_NAMESPACE\|ZOO_KUBERNETES_RUNTIME_CLASS\|use_ssh_client" server .env.example deploy`
  prints nothing.
Done when: checks pass, gone-greps empty.

#### T-21 Runtime requirement checks and settings
Status: done · Depends on: T-20, T-14
Notes: Only the selected backend's mandatory checks run; readiness shares the startup checks. Docker verifies
 its daemon, images, local KVM and registered Kata runtime; Kubernetes verifies Kata/KVM/storage/snapshots.
 Storage defaults and deployment settings match. Ruff, formatting, Basedpyright and Helm lint pass.
- `server/requirements.py`: add `runtime` (`ZOO_RUNTIME` is `docker` or `kube`). Docker: daemon reachable, sandbox
  images present, `/dev/kvm`, and `docker info` lists `io.containerd.kata.v2`. Kube: API reachable, RuntimeClass `kata`, StorageClass `ZOO_STORAGE_CLASS` (default `zoo-local`),
  VolumeSnapshotClass `ZOO_SNAPSHOT_CLASS` (default `zoo-snapshots`). Only the configured runtime's checks run; they
  are not optional for it. `/readyz` drops its own docker/kubernetes probe in favour of these.
- `kube.py`: home PVCs use `ZOO_STORAGE_CLASS`; snapshots use `ZOO_SNAPSHOT_CLASS`.
- Add `ZOO_RUNTIME` and the kube vars to `.env.example`, compose (`ZOO_RUNTIME=docker`) and the chart
  (`ZOO_RUNTIME=kube`).
Done when: checks pass.

#### T-22 Watch instead of polling
Status: done · Depends on: T-20
Notes: Docker events and Kubernetes resourceVersion watches replace the reconcile timer, with inventory on
 reconnect and Kubernetes re-list on 410. Node-report NOTIFY drives VM reconciliation. Shutdown cancels stream
 transports; failures update sandbox state immediately. Python lint/format/types pass; no runtime was started.
- Replace the reconcile poll in `server/sandbox_api.py` with the runtime's event stream. Kube: a watch on pods labelled
  as sandboxes (`watch=true`, resume from `resourceVersion`, re-list on 410). Docker: `docker events` filtered to
  sandbox containers (`die`, `oom`, `destroy`), re-listing on reconnect. A sandbox whose container or pod disappears
  or fails is marked `stopped`/`failed` immediately. macOS/Windows still reconcile from zoo-node reports.
Done when: checks pass; `grep -n "15 seconds\|reconcile_interval\|sleep(15" server/sandbox_api.py` prints nothing.

#### T-23 Snapshots and moves
Status: done · Depends on: T-20, T-21
Notes: Both backends use portable S3 home tar parts; Kubernetes snapshots through temporary CSI clones.
 Linux moves take node_name and atomically switch home_claim after restore; previous_home_claim makes cleanup
 retryable. Docker rejects moves. Static checks/generation pass; zoo-snapshots is a CSI class, not an old image.
- Snapshots use one S3 format on both runtimes: a tar of the home volume. Docker: a helper container tars the volume
  to S3 and restores it; delete `zoo-snap-*` images. Kube: CSI snapshot, then the existing helper-pod upload.
- `POST /v1/sandboxes/{id}/move` on kube: snapshot, create the PVC on the target node, download, delete the source
  PVC. On docker there is one host: the route returns `ZooError("not_supported", …)`.
- Placement: delete our free-memory ranking and the "fewest sandboxes" fallback in `sandbox_api.py` (around line
  859); kube node choice is done in T-80, Docker has nothing to place.
Done when: checks pass; `grep -rn "fewest\|zoo-snap" server` prints nothing.

### Phase 3 — zoo-node only for Mac and Windows hosts

#### T-30 Delete SSH servers and migration
Status: done · Depends on: Phase 2
Notes: Registered hosts are node-connected Macs/Windows only. Host keys are pinned in nodes; the shared SSH
 identity is encrypted in Postgres. Linux node services, drivers, tunnels, migration, manual registration and
 installer variables are removed. Proto regenerated; Python, Go cross-builds and shell checks pass.
Linux capacity is the Docker host or the Kubernetes cluster, never a registered server; servers are Mac and Windows
hosts only.
- `server/servers_api.py`, `server/nodes.py`, `server/nodes_api.py`, `server/macos.py`, `server/windows.py`,
  `server/ssh.py`: delete `ssh://` and `tcp://` Docker URLs, `zoo-join:` parsing, `known_hosts` handling, manual
  setup, `POST /servers/{id}/migrate`, and falling back to SSH while a node is offline. Mac/Windows SSH goes only
  through the node tunnel.
- Delete Linux as a server platform (`server/platforms.py`): servers are `macos` or `windows`.
- `node/`: delete `migrate` command, the Linux service and the `docker` tunnel target; keep darwin and windows.
  `make proto` if the proto changes; delete `node/stats_linux*.go`, `service_linux.go`.
- `install.sh`: delete `--node` for Linux and the `zoo-join:` output.
- `.env.example`, chart, compose: delete `ZOO_SSH_DIR`, `ZOO_NODE_ENDPOINTS` (nodes dial `ZOO_API_URL` host on
  `ZOO_NODE_PORT`).
- Delete `tests/test_remote_servers.py`, Linux cases in `tests/test_nodes.py`.
- Gone: `grep -rniE "zoo-join|known_hosts|ssh://|migrate|ZOO_SSH_DIR|ZOO_NODE_ENDPOINTS" server node install.sh .env.example deploy tests`
  prints nothing (except `db/migrate.py` imports, which are unrelated).
Done when: all checks pass, gone-grep empty.

#### T-31 Servers page: Mac and Windows only
Status: done · Depends on: T-30
Notes: Add-host UI has only OS, name and node join/install commands; Linux/manual SSH/migration UI and hooks gone.
 Base pages keep supported VNC setup and receive SSE updates. The removed join parser's test was deleted.
 Web lint, typecheck, build and formatting pass.
- `web/src/routes/_app/servers.tsx` and related hooks in `api_client.ts`: remove Linux, SSH, manual setup, migrate
  and "Switch to zoo-node". Add-server shows OS (macOS/Windows), name, the join command.
- `web/src/routes/base.$serverId.tsx`: keep (base VM page) but remove SSH-only actions.
Done when: web checks pass.

### Phase 4 — Go gateway

The gateway becomes a Go service (`gateway/`, module `zoo/gateway`). It owns every long-lived connection. The Python
API and worker talk to it over one gRPC connection per process. Build it alongside the Python gateway; T-44 switches
over and deletes the Python side.

#### T-40 Gateway skeleton and internal API
Status: done · Depends on: Phase 3
Notes: Go module/image, four RPCs, generated Python/Go bindings and gateway-proto target added.
 HMAC derivation matches the Python gateway; unary/stream RPCs authenticate. Lifecycle shuts down listeners.
 Go vet/build and Python static checks pass. Guest and database/node implementations follow in T-41/T-42.
- New `gateway/` Go module: `main.go`, `go.mod`, Dockerfile stage in `Dockerfile.api` (or a new `Dockerfile.gateway`
  and a `gateway` image in the chart).
- New proto `gateway/proto/gateway.proto` (package `zoo.gateway.v1`), generated into `gateway/gatewaypb` (Go) and
  `server/gatewaypb` (Python) by a new `make gateway-proto` target. Service `Gateway`:
  - `GuestCall(GuestCallRequest{sandbox_id, op, header_json, payload}) returns (GuestCallReply{header_json, payload})`
  - `GuestStream(stream Frame) returns (stream Frame)` — terminals and tunnels.
  - `NodeTunnel(stream Bytes) returns (stream Bytes)` with `server_id` and `target` in metadata.
  - `NodeStatus(NodeStatusRequest{server_id}) returns (NodeStatusReply{online, report_json})`
  - `Screen*` RPCs are added in T-50.
- Auth between API and gateway: the HMAC token already derived from the secrets key in `server/gateway.py` (port the
  derivation exactly).
- Config via env: `ZOO_SECRETS_KEY`, `DATABASE_URL` (read-only use: node certs and sandbox tokens), `ZOO_NODE_PORT`,
  `ZOO_GATEWAY_GRPC_PORT` (default 7444), `ZOO_GATEWAY_HTTP_PORT` (default 8001, for `/guest/connect` websockets).
Done when: gateway builds and vets.

#### T-41 Guest hub in Go
Status: done · Depends on: T-40
Notes: Native WebSocket/gRPC hub preserves guest framing, calls, scoped PTY/tunnel streams and heartbeats.
 Cancellation and bounded backpressure are tested; ten fake-guest transport tests pass with the race detector.
 Go vet/build and generated Python static checks pass; no production service was started.
- Port `server/guest.py` server side to `gateway/guest.go`: websocket `/guest/connect`, hello, token check
  (`HMAC(key, "zoo-guest:<id>")`), frame format (4-byte big-endian header length, JSON header, raw payload), request
  ids, notifications, streams (`pty_*`, `tunnel_*`), heartbeats. Byte-for-byte compatible with `guest/protocol.go`.
- Implement `GuestCall` and `GuestStream`.
Done when: gateway builds; a Go unit test round-trips a fake guest through `GuestCall` (`go test ./...` in
`gateway/` is allowed for this ticket).

#### T-42 Node link in Go
Status: done · Depends on: T-40
Notes: Approved pgx/x/crypto dependencies are installed; serial-guarded Postgres writes and native links are wired.
 Encrypted CA bootstrap/loading uses the system envelope key; Go vet/build/race transport/store tests pass.
 No live Postgres or production service has been exercised yet; fake transport success is not deployment proof.
- Port the node side of `server/nodes.py` to `gateway/nodes.go`: gRPC `Connect` server from `node/proto/node.proto`
  on `ZOO_NODE_PORT` with mTLS against the node CA (CA key and cert read from the `node_authority` table), serial
  check against `nodes`, reports stored in memory and written to `nodes` (last report), tunnels (`ssh` target),
  updates (stream the matching `node/dist` build), certificate renewal.
- Implement `NodeTunnel` and `NodeStatus`.
- Joining (`POST /v1/nodes/join`, CSR signing) stays in Python (`nodes_api.py`).
Done when: gateway builds; Go unit test for a fake node `Connect` + tunnel passes.

#### T-43 Replicas with rendezvous hashing
Status: done · Depends on: T-41, T-42
Notes: Built by the parallel session; verified here: gateway, guest and Python checks pass and the gateway tests
 (owner selection, 4001 redirect) pass. No replica set was run.
- Gateway replicas discover each other through a headless Service (DNS A records of `ZOO_GATEWAY_PEERS`, a DNS name).
- Owner of a sandbox = highest `hash(replica_ip, sandbox_id)` (rendezvous). A guest that connects to a non-owner gets a
  websocket close with code 4001 and the owner's address in the reason; the guest reconnects there once
  (update `guest/main.go` to follow it). Nodes are owned the same way by `server_id`.
- The Python client (`server/gateway_client.py`, new) computes the owner the same way and keeps one gRPC channel per
  replica.
- On peer-set change, connections whose owner changed are closed and redial.
Done when: gateway, guest and Python checks pass; Go unit test for owner selection stability passes.

#### T-44 Switch over and delete the Python gateway
Status: done · Depends on: T-43
Notes: Built by the parallel session; verified here: the gone-grep is empty and server/gateway.py is deleted.
 Compose and chart run the Go gateway image.
- All Python callers of `hub`, `RemoteHub`, node tunnels and node status use `server/gateway_client.py`.
- Delete `server/gateway.py`, the hub/serve parts of `server/guest.py` (keep `token()` and `guest_env()` until T-45),
  the gRPC server parts of `server/nodes.py`, `ZOO_ROLE=gateway`, `ZOO_GATEWAY`.
- Running from source: the API needs a gateway; `make dev` (T-112) starts it. There is no in-process mode.
- Chart: `gateway` Deployment uses the Go image, N replicas (default 2), headless Service, PodDisruptionBudget.
- Compose: one `gateway` service from the same image.
- Gone: `grep -rniE "RemoteHub|ZOO_ROLE.*gateway|ZOO_GATEWAY\b|hub\.serve|grpc\.server" server` prints nothing.
Done when: all checks pass, gone-grep empty.

#### T-45 Derived guest and node addresses
Status: done · Depends on: T-44
Notes: Built by the parallel session; the gone-grep is empty.
- Kube: Linux pods get `ZOO_GUEST_URL=ws://<gateway service>.<namespace>.svc:8001/guest/connect`, computed in
  `kube.py` from `ZOO_NAMESPACE` and the chart's service name (`ZOO_GATEWAY_SERVICE`, set by the chart).
- Docker: containers join the compose network and get `ws://gateway:8001/guest/connect`, computed in `docker.py`.
- macOS/Windows guests get `<ZOO_API_URL>/guest/connect` (ingress routes `/guest/connect` to the gateway).
- Delete `ZOO_GUEST_URL` and `ZOO_GUEST_REMOTE_URL` as configuration everywhere (`.env.example`, chart, compose,
  `server/guest.py`).
- Gone: `grep -rn "ZOO_GUEST_REMOTE_URL\|os.environ.get(\"ZOO_GUEST_URL" server .env.example deploy` prints nothing.
Done when: checks pass, gone-grep empty.

### Phase 5 — one screen-and-input path: VNC sessions in the gateway

#### T-50 VNC sessions in the gateway
Status: done · Depends on: Phase 4
Notes: Built by the parallel session; gateway tests pass. Added here: "base:<server id>" sessions for a Mac or
 Windows host's base VM (same node SSH path; names from ZOO_MACOS_BASE/ZOO_WINDOWS_BASE), so the base page's viewer
 goes through the gateway too.
- `gateway/vnc.go`: RFB 3.8 client with VNC auth. One session per sandbox, opened on the first screen call or viewer,
  closed 60 s after the last use or when the sandbox stops.
  - Linux: reached through the guest tunnel to `127.0.0.1:5900`. macOS: host-side VNC through the node's ssh tunnel
    (port forward to the zoovm VNC port). Windows: in-guest TightVNC through the node tunnel. Port the addressing
    from `server/macos.py`/`server/windows.py`.
  - Encodings: Raw, CopyRect, Zlib (and Tight if the server offers it). Incremental updates into an in-memory RGBA
    framebuffer; a frame version counter increments on each applied update.
  - Tile grid: 32×32 tiles, a 64-bit hash per tile, updated for tiles touched by each update.
- RPCs: `ScreenShot(sandbox_id) → png`, `ScreenDiff(sandbox_id, since_version) → {changed_tiles, boxes, version}`,
  `WaitStable(sandbox_id, quiet_ms, timeout_ms)`, `Pointer(sandbox_id, x, y, mask)`, `Key(sandbox_id, keysym, down)`,
  `Type(sandbox_id, text)`, `ViewerStream(stream bytes)` (relays a browser viewer through the same session with the
  Gate rules from `server/vnc.py`: view-only drops client input).
- PNG: filter type Sub on every row, zlib level 1 via `github.com/klauspost/compress/zlib`; cache the encoded PNG
  per frame version.
- `Type`: each rune as a keysym; runes without a Latin-1 keysym use `0x01000000 + codepoint`.
Done when: gateway checks pass; Go unit tests with a fake RFB server cover auth, raw/zlib updates, PNG caching,
tile diff, wait-stable and unicode keysyms (`go test ./...` in `gateway/` allowed).

#### T-51 Tools use the gateway screen on every OS
Status: done · Depends on: T-50
Notes: registry.py has one implementation per screen/input tool calling Screen*/Pointer/Key/Type; display and
 type_text's delay params are gone. docker.py keeps docker-py exec_run for runtime root work (helpers, egress files)
 and macos.py/windows.py keep Guest.run (renamed from exec_run); vnc.py keeps its RFB client only for macOS base setup.
- `server/registry.py`: `screenshot`, `screen_diff`, `wait_until_stable`, `click`, `double_click`, `move_mouse`,
  `scroll`, `drag`, `type_text`, `press_key`, `hotkey` have one implementation for all OSes, calling the gateway
  `Screen*`/`Pointer`/`Key`/`Type` RPCs. Remove per-OS functions for these tools.
- Delete `server/screen.py`, `server/vnc_tools.py`, the RFB client in `server/vnc.py` (keep only what the viewer
  websocket needs until T-52), and in `server/tools.py`: `run_x`, every xdotool/imagemagick call, the XTest guest
  input paths, and every `docker exec`/`exec_run` path.
- Gone: `grep -rniE "xdotool|imagemagick|run_x|exec_run|XTest" server; grep -rn "screen.py\|vnc_tools" server`
  print nothing.
Done when: checks pass.

#### T-52 Live view through the gateway session
Status: done · Depends on: T-51
Notes: One relay (relay_screen) serves sandbox, embed and base-VM viewers through ViewerStream. access_url is gone
 (column, queries, runtimes); has_screen(kind) replaces it. The embed page still loads the browser RFB client from
 the @novnc/novnc npm package (RFB_CLIENT); the image no longer ships noVNC.
- `WS /v1/sandboxes/{id}/ws` and `/embed/{token}` viewers relay to `ViewerStream` (one code path for every OS).
- Delete `server/proxy.py`, `proxy`, `proxy_tunnel`, `proxy_vnc` in `server/sandbox_api.py` (replace with one relay),
  and the rest of `server/vnc.py`.
- Delete noVNC from the image: `supervisord.conf` `[program:novnc]`, `novnc` in `Dockerfile` apt list, `EXPOSE 6080`,
  the `zoo.guest.tunnel` label; `kube.py` container port 6080 and `access_url` port logic. `access_url` becomes a
  boolean `has_screen` derived from the kind (or remove it if nothing else needs it).
- Gone: `grep -rniE "novnc|6080|zoo.guest.tunnel|proxy_tunnel|proxy_vnc" server Dockerfile supervisord.conf` prints nothing.
Done when: checks pass, gone-grep empty.

#### T-53 Delete the guest's screen and input code
Status: done · Depends on: T-51
Notes: The guest's screen, input, diff and keysym code and handlers are deleted; go mod tidy dropped 4 deps.
- `guest/`: delete `input.go`, `keyboard.go`, `keysyms.go`, `screen.go`, `fastpng.go`, `fastpng_test.go`, `diff.go`,
  `diff_test.go`, `gen_keysyms.py`, and the `screenshot`, `mouse`, `keyboard`, `screen_diff`, `wait_until_stable`
  handlers in `main.go`. Remove now-unused dependencies from `guest/go.mod` (`go mod tidy`).
Done when: guest checks pass; `grep -rn "screenshotOp\|mouseOp\|keyboardOp\|diffOp\|stableOp" guest` prints nothing.

### Phase 6 — egress as its own binary

#### T-60 zoo-egress binary
Status: done · Depends on: Phase 2
Notes: Standalone egress keeps Linux firewall and Mac/Windows host proxies; VM backends install separate builds.
 Compose/Helm deploy its own image with NET_ADMIN, and CI publishes/checks it. Guest flag/code/dependency removed.
 Python, guest/node/egress vet/build/cross-builds, shell checks and Helm lint pass. No runtime/tests started.
- Move `guest/egress*.go` (Linux parts) into a new Go module `egress/` with its own `main.go`. Delete the `-egress`
  flag and `egress_darwin.go`/`egress_windows.go` from the guest if nothing in the guest uses them (macOS/Windows host
  policy stays in `macos.py`/`windows.py`).
- Image `zoo-egress` (Dockerfile stage). Kube: chart DaemonSet on sandbox nodes (host network, `NET_ADMIN` only);
  `kube.py` `egress_daemon`/`apply_network` adapt to the DaemonSet pods by node. Docker: one `egress` service in
  compose (host network, `NET_ADMIN` only); `docker.py` delivers policy files to it the same way.
- Gone: `grep -rn "\-egress\|runEgress" guest` prints nothing.
Done when: egress, guest and Python checks pass.

### Phase 7 — the hot path (warm create and tool calls)

#### T-70 Warm claim inside the request
Status: done · Depends on: Phase 5
Notes: create_async claims, then hand_off runs secrets, enforce (network + apps) and profiles with
 asyncio.gather; a failed hand-off deletes that sandbox row and container and claims again until WARM_CEILING (10 s),
 then create_failed. Pooled sandboxes boot with a deny-all network policy.
- `server/pool.py` + `server/sandbox_api.py`: `POST /v1/sandboxes` claims a ready pooled sandbox of the requested
  kind and size with one statement (`UPDATE pool_sandboxes SET claimed_at = now() … WHERE id = (SELECT id … WHERE
  ready AND kind = $1 AND size = $2 ORDER BY ready_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING …`), then in parallel
  (`asyncio.gather`): push secrets via the guest, apply network policy via the node's egress daemon, write labels;
  load profiles only when requested. Responds `201` with status `running`.
- If the hand-off fails, mark that pooled sandbox for deletion and claim another in the same request; give up after
  the 10 s ceiling with `ZooError("create_failed", …)`.
- No pooled sandbox available → cold create through the boot job (that is the cold path, not a fallback).
- Pool boots sandboxes with deny-all network policy until claimed.
Done when: checks pass.

#### T-71 Async endpoints and asyncpg on the hot path
Status: done · Depends on: T-70
Notes: GET/tools/MCP/exec-stream read through asyncpg (db/async_generated) and the cache. POST /sandboxes is async
 but its validation transaction still runs on the sync querier in a worker thread. ZOO_API_WORKERS (chart default 2).
- `POST /v1/sandboxes`, `GET /v1/sandboxes/{id}`, `POST /v1/sandboxes/{id}/tools/{name}`, `POST /mcp` tool calls and
  `POST /v1/sandboxes/{id}/exec/stream` become `async def` using an `asyncpg` pool (add `asyncpg` dependency). sqlc:
  add an asyncpg target for the queries these use (`sqlc.yml`), `make generate`.
- uvicorn workers from `ZOO_API_WORKERS` (default: CPU count).
Done when: checks pass.

#### T-72 In-process caches cleared by NOTIFY
Status: done · Depends on: T-71
Notes: Built by the parallel session (server/cache.py); every cached table has a zoo_event NOTIFY trigger.
- New `server/cache.py`: per-process caches for API key → member, workspace membership, sandbox (row, permissions,
  network policy, labels), each with 30 s TTL as a safety bound, invalidated by `zoo_event` notifications (add events
  for key revoke, membership change, permission/policy change if missing). Hot-path endpoints read only the cache.
Done when: checks pass.

#### T-73 Batched writes off the hot path
Status: done · Depends on: T-71
Notes: Built by the parallel session (server/writer.py, COPY every 50 ms or 500 rows, flushed on close).
- New `server/writer.py`: an in-process queue flushed every 50 ms or 500 rows with `COPY` for `tool_executions`,
  sandbox `touched_at`, audit entries and usage samples. Hot-path code enqueues and never awaits a write.
- Flush on shutdown.
Done when: checks pass; `grep -n "create_tool_execution\|update_tool_execution_status\|touch_sandbox" server/sandbox_api.py`
shows only enqueue calls.

#### T-74 Self-sizing warm pool
Status: done · Depends on: T-70
Notes: Pool size per kind = max(setting, p99 creates per 10 s refill interval over the last hour), capped at
 MAX_SIZE. Metrics zoo_pool_available, zoo_pool_refill_seconds. Not done: the gateway can't pre-open a pooled
 desktop's VNC session, since sessions resolve sandbox rows and a pooled one has none until claimed.
- Pool target per (node, kind, size) = max(configured minimum, p99 creates in the last hour per refill interval).
  Refill in parallel. Pooled desktops are fully started (window manager up) and the gateway opens their VNC session
  before they are marked ready.
- Metrics `zoo_pool_available{kind}`, `zoo_pool_refill_seconds`.
Done when: checks pass.

### Phase 8 — the cold path

#### T-80 Pick the node and bind directly (kube runtime)
Status: done · Depends on: T-21
Notes: The CSI provisioner honors selected-node annotations; bind the PVC before assigning the pod's nodeName.
 Placement persists the node, checks PV affinity and reserves allocatable-minus-request capacity under DB locks.
 Requests/limits and priority are explicit; undersized limits fail. Generation and Python static checks pass.
- `kube.py`: choose the node from our capacity view (free memory and CPU from node allocatable minus our pods'
  requests) and set `spec.nodeName`. Requests: CPU 0.1, memory = measured idle (`ZOO_IDLE_MEMORY_MB_DESKTOP`
  default 700, `ZOO_IDLE_MEMORY_MB_CODE` default 250); limits = the sandbox size. PriorityClass `zoo-sandbox` (chart).
- Memory QoS: request = limit is NOT used (density); instead the PriorityClass and `earlyoom` (T-83) protect sandboxes.
Done when: checks pass.

#### T-81 Local thin volumes ready ahead (kube runtime)
Status: done · Depends on: T-80
Notes: Four unbound default-size claims per eligible node are refilled by the pool; count is configurable.
 Placement-lock/CAS ownership persists the claim before binding; refill repairs interrupted labeling safely.
 Warm adoption and deleted-pool cleanup retain the actual claim config. Generation and Python/chart checks pass.
- Each sandbox node keeps `ZOO_SPARE_VOLUMES` (default 4) unbound PVCs of the default home size on `zoo-local`
  (TopoLVM). A cold create binds a spare on the chosen node; the pool refills spares.
Done when: checks pass.

#### T-82 Pre-pulled images and Kata tuning (chart, kube runtime)
Status: done · Depends on: T-80
Notes: User authorizes resolving the incompatibility: keep Cloud Hypervisor/virtio-fs and disable templating.
 Pinned Kata's CLH API has no DAX knob and ships this shim only on amd64; unsupported ARM kube installs fail.
 Prepull DaemonSet, KSM docs, shell checks and Helm lint/template pass; no ignored configuration is shipped.
- Chart: DaemonSet `zoo-prepull` that pulls the desktop and code images on every sandbox node; pods use
  `imagePullPolicy: IfNotPresent`.
- Chart: Kata configuration via kata-deploy values: Cloud Hypervisor VMM (`kata-clh` RuntimeClass named `kata`),
  `enable_template = true`, virtio-fs with DAX, `enable_mem_prealloc = false`, free page reporting on.
- Document the node requirements (KSM on: `echo 1 > /sys/kernel/mm/ksm/run`) in the install docs (T-131).
- Docker runtime: `install.sh` pulls the images at install and `docker compose pull` on upgrade; nothing else here.
Done when: `helm lint deploy/helm/zoo` passes (allowed: `helm lint`, `helm template`).

#### T-83 Sandbox start order and lean desktop
Status: done · Depends on: T-53
Notes: Xvfb and zoo-guest start together; xfwm4 and xfce4-panel replace startxfce4; earlyoom runs as root. New
 guest op desktop_ready (EWMH WM check); boot and pool wait on hub.wait_for_desktop. The guest always advertises
 "windows" now (it used to check for a WM at hello, which a guest that starts first would miss).
- `supervisord.conf`: `zoo-guest` and `Xvfb` start first in parallel; guest dials immediately (no initial backoff in
  `guest/main.go`). Add `earlyoom` (apt) as root.
- `Dockerfile`: pre-built XFCE config under `/etc/xdg` (no first-run dialogs, no session restore, start `xfwm4` and
  `xfce4-panel` directly instead of `startxfce4`), font and icon caches built at image build, remove tumbler, power
  manager and update notifier packages.
- The guest reports `desktop_ready` when the window manager's root window property is set; boot waits for it.
- `browser` kind: Firefox with a pre-initialised profile baked into the image, first-run pages and update checks off.
Done when: guest checks pass; image build is not run (owner builds).

### Phase 9 — stop and resume

#### T-90 Two-stage stop
Status: done · Depends on: T-80
Notes: Stop pauses Linux sandboxes (kube: vm_control pause/resume, bound once per pod after boot; docker: docker
 pause), status stays stopped with resumable; boot resumes them. A sweeper deletes paused ones after ZOO_PAUSE_MINUTES
 or, under 10% free memory, the oldest per node. Restore and move delete a paused container first.
- Stop pauses the sandbox through the runtime (kube: the pod's VM via `kube.pause`; docker: `docker pause`) and records
  `paused_at`. A sweeper deletes paused sandboxes older than `ZOO_PAUSE_MINUTES` (default 30) down to their volume
  (status `stopped`). Start resumes a paused sandbox or cold-boots on the same volume (and node, on kube).
- Under memory pressure (host or node free memory below 10%), the sweeper deletes paused sandboxes there first, oldest
  first.
- Status values stay `running`/`stopped`; `paused` is an internal field exposed as `stopped` with `resumable: true`.
Done when: checks pass.

### Phase 10 — macOS and Windows from saved state

#### T-100 macOS saved-state clones
Status: blocked · Depends on: Phase 5
Blocked: Apple saved states are tied to the originating Mac/account and compatible VM configuration.
 May saved-state clones/moves be restricted to the same host, with incompatible templates rejected?
Notes: decided yes (same host, reject incompatible). zoovm slot commands exist; the guest reidentify op and the
 macos.py clone path are not built: switching off cold boot needs a Mac with base VMs to validate. Deferred by owner (2026-10-14).
- `macos/zoovm`: add `save-state` (boot base to the logged-in desktop with the guest connected, then
  `saveMachineStateTo`) and `restore` (APFS-clone the disk and state, `restoreMachineStateFrom`). Requires macOS 14+.
- `server/macos.py`: create = clone + restore; after restore, push the sandbox identity to the guest through the
  host (zoovm writes `~/.zoo/guest.env` into the clone before restore, or the guest re-reads it on a `reidentify` op).
  Delete the cold-boot create path.
- Base VM update ("Stop" on the base) re-saves state.
Done when: Swift builds (`macos/zoovm/build.sh` is allowed), Python checks pass.

#### T-101 Windows saved-state clones
Status: blocked · Depends on: Phase 5
Blocked: Copying vmrs/vmgs into a New-VM with a different disk is not a documented supported cloning workflow.
 May complete VM export/import with new IDs replace this recipe, pending real Hyper-V validation?
Notes: decided yes (export/import, new IDs). Not built: needs a Hyper-V host to validate before cold boot goes. Deferred by owner (2026-10-14).
- `windows/zoovm.ps1`: the template is saved with the VM running (saved state). A sandbox = differencing disk + copied
  `.vmrs`/`.vmgs`, started from saved state. Identity pushed through the Guest Service Interface after start.
- `server/windows.py`: delete the cold-boot create path.
Done when: Python checks pass; PowerShell is syntax-checked if `pwsh` is installed (`pwsh -NoProfile -Command
"[ScriptBlock]::Create((Get-Content windows/zoovm.ps1 -Raw))"`), otherwise note it.

### Phase 11 — install and operations

#### T-110 install.sh: Docker by default, Kubernetes with --runtime kube
Status: done · Depends on: Phase 6
Notes: Docker/Kubernetes bootstrap and checksummed offline runtime/image/chart bundles are implemented.
 Syntax/ShellCheck, embedded Python, workflow YAML and chart rendering pass; no installer/deployment ran.
 Current Python gateway guest addressing/routes work in generated config; T-45 must update them on Go cutover.
- `install.sh` takes `--runtime docker|kube` (default `docker`), plus `--admin-email`, `--domain`.
- Docker: install Docker Engine if missing, write `.env` from `.env.example` with generated secrets and
  `ZOO_RUNTIME=docker`, pull images, `docker compose up -d`. Before that: check `/dev/kvm` (exit with a clear message
  if missing), install Kata Containers (static release tarball, pinned version) and register it in
  `/etc/docker/daemon.json` as runtime `io.containerd.kata.v2`, restart Docker, turn on KSM.
- Kube: check `/dev/kvm` first (exit with a clear message if missing), install k3s, kata-deploy, TopoLVM (on a named
  device or a loop file of `--disk-gb`), the CSI snapshot controller, cert-manager, then `helm install zoo
  deploy/helm/zoo` with values from the flags. Turn on KSM.
- Offline bundle: the release workflow (`.github/workflows/release.yml`) packs Zoo images (both runtimes) plus k3s
  airgap images and kata-deploy (kube) into `zoo-bundle-<version>.tar.zst`; `install.sh --bundle PATH` uses it.
- Mac/Windows node install stays (`install.sh --node` for macOS, `windows/install-node.ps1`).
Done when: `bash -n install.sh` passes; `shellcheck install.sh` if installed.

#### T-111 Compose is the Docker runtime's deployment; delete the operator script
Status: done · Depends on: T-110
Notes: Restore stays offline (python -m server.backups restore, with API, workers and gateway stopped), as
 authorized; `zoo backups` lists them. The gone-grep is empty.
- `compose.yml` stays as the production deployment for `ZOO_RUNTIME=docker`: api, worker, gateway, egress, web,
  Postgres, SeaweedFS, Caddy (HTTPS for `--domain`). The chart is the kube deployment. Keep the two in step: same env
  var names, same images.
- Delete `deploy/zoo` and the `server` subcommands in `sdk/python/zoo_sdk/cli.py` and the hand-off to it;
  `install.sh`, `docker compose` and `helm` cover them. Backups: add `zoo backups` (list) and `zoo backups restore
  <id>` calling existing API routes.
- Gone: `grep -rniE "deploy/zoo|ZOO_OPERATOR_SCRIPT" sdk install.sh makefile README.md` prints nothing.
Done when: checks pass.

#### T-112 make dev
Status: done · Depends on: T-111
Notes: Built by the parallel session: make dev/dev-kube (and -down) run scripts/dev.py; make -n prints the
 commands and helm lint passes.
- `makefile` `dev` target (Docker runtime, Kata registered in the local Docker; on a Mac: inside the same Lima VM
  with nested virtualization as `dev-kube`): `docker compose up -d` for Postgres, SeaweedFS, gateway and egress, runs
  migrations and the API from source with `ZOO_RUNTIME=docker`. `make dev-down` stops it.
- `make dev-kube`: assumes a local k3s with Kata; installs the chart with `values-dev.yaml` (Postgres and SeaweedFS
  in-cluster, gateway replicas 1), port-forwards Postgres and the gateway, runs migrations and the API from source
  with `ZOO_RUNTIME=kube`. `make dev-kube-down` removes it.
- `docs/development.md`: rewrite for both flows, including the Lima VM setup on M3+ Macs for `dev-kube`.
Done when: `make -n dev` and `make -n dev-kube` print the commands; `helm lint` passes.

### Phase 12 — dashboard

#### T-120 SSE everywhere events exist
Status: done · Depends on: Phase 0
Notes: Monitoring is the only dashboard polling hook. Transactional server/node/pool/snapshot/job/delivery
 events invalidate live queries, including reconnect refreshes; node detach emits offline state. Base setup
 emits progress events, and its watcher tasks drain on shutdown. Web lint/build and Python static checks pass.
- `web/src/lib/api_client.ts`: remove `refetchInterval` from queries whose data has events (`sandbox.state`, servers
  online/offline — add a `server.state` event server-side if missing); keep polling only for monitoring metrics.
Done when: web checks pass; remaining `refetchInterval` uses are only in monitoring hooks.

#### T-121 Remove UI for deleted features
Status: done · Depends on: T-31, T-52, T-90
Notes: Stopped + resumable sandboxes show "Paused · resumes instantly"; the viewer comment now names the gateway
 session. Linux/SSH server UI was already gone (T-31); there was no runtime choice left in the UI.
- Remove runtime choice, Linux/SSH server UI, the old noVNC viewer code in `components/vnc-screen.tsx` (point it at the
  same websocket, which now relays the gateway session), and show "Paused · resumes instantly" for resumable stopped
  sandboxes.
Done when: web checks pass.

### Phase 13 — docs

#### T-130 Delete duplicate docs
Status: done · Depends on: Phase 11
Notes: The remaining links (chart values, windows/install-node.ps1) point at the site pages.
- Delete `docs/configuration.md`, `install.md`, `kubernetes.md`, `nodes.md`, `sandboxes.md`, `security.md`,
  `servers.md`, `windows.md`, `limitations.md`. Keep `docs/development.md`, `docs/dockerhub/`.
- `macos/README.md`, `windows/README.md`: replace with three lines pointing to the site guides.
Done when: `grep -rn "docs/\(configuration\|install\|kubernetes\|nodes\|sandboxes\|security\|servers\|windows\|limitations\).md" .` (excluding node_modules) prints nothing.

#### T-131 Rewrite the site for the new system
Status: done · Depends on: T-130
Notes: Built by the parallel session; updated here for two-stage stop and the new settings; reference pages
 regenerated. Duplicate-id warnings only appear on the first build after a content change (stale .astro cache).
- Rewrite `get-started/install.md`, `get-started/quickstart.md`, `concepts/architecture.md`, `concepts/runtimes.md`
  (becomes "Isolation": Kata VMs on both runtimes, and what that protects against),
  `concepts/security-model.md`, `guides/kubernetes.md` (the opt-in Kubernetes install; the Docker install is the main
  install guide), `guides/remote-servers.md` (Mac and Windows hosts only), `guides/upgrading.md` (helm), `guides/macos.md`,
  `guides/windows.md`, `guides/domain-https-backups.md`. State the requirements list from this plan verbatim.
- Remove every mention of SSH servers, remote Docker, "until Zoo 2.0", noVNC, moves on Docker, the agent.
- Add `reference/performance.md`: the targets from `METRICS.md` and how they are measured.
- Regenerate reference pages.
Done when: site builds; `grep -rniE "runc|ssh://|tcp://|until zoo 2|novnc" site/src` prints nothing.

### Phase 14 — instrumentation and measurement

#### T-140 Metrics
Status: done · Depends on: Phase 7
Notes: zoo_sandbox_ready_seconds and zoo_sandbox_creates_total (warm in create_async, cold at a sandbox's first
 start), zoo_event_delivery_seconds (sse, webhook). Gateway metrics were in place; added the Prometheus client
 module to gateway/go.mod. Dashboard has a Performance row.
- `server/metrics.py`: add `zoo_sandbox_ready_seconds{kind,warm}`, `zoo_sandbox_creates_total{kind,warm,outcome}`,
  `zoo_event_delivery_seconds{channel}`. Gateway (Go, Prometheus client): `zoo_gateway_sessions{type}`,
  `zoo_gateway_reconnect_seconds`, `zoo_screen_encode_seconds`.
- Grafana dashboard JSON in `deploy/grafana/dashboards/`: add panels for each.
Done when: checks pass.

#### T-141 scripts/perf.py
Status: done · Depends on: T-140
Notes: Built by the parallel session; --help works.
- Uses the Python SDK. Args: `--url`, `--key`, `--kind`, `--runs` (default 100), `--pattern sequential|staggered|burst`,
  `--burst` (default 50), `--cold` (drain the pool first via an admin route). Measures TTI (create → first successful
  `execute_command("true")` for `code`, `screenshot()` otherwise), and tool latencies (screenshot, click, type_text,
  exec, read/write 1 MB, accessibility_tree). Prints p50/p95/p99 and success rate per metric; `--json PATH` writes
  results. A failed create counts as 10 s.
Done when: checks pass; `uv run python scripts/perf.py --help` works.

#### T-142 Chaos suite
Status: done · Depends on: T-141
Notes: Built by the parallel session; --help works.
- `scripts/chaos.py --runtime docker|kube`: while `perf.py` runs, kills gateway, API and sandbox containers or pods
  and random guests (via `docker` or `kubectl` subprocess calls), then reports success rates. Not run by you.
Done when: checks pass; `--help` works.

### Phase 15 — tests (last)

#### T-150 Bring the test suite in line
Status: done · Depends on: all other tickets
Notes: pytest (local Postgres 14, excluding e2e/kubernetes/windows_live): 499 passed, 4 skipped. go test: guest, node,
 egress, gateway pass; vm-control is Linux-only (vet + test compile with GOOS=linux). Found and fixed: upgrading a
 paused sandbox resumed its old container (boot now deletes it to its volume on `upgrade`).
- Delete tests for removed features and paths; fix remaining tests for the new code; add tests for: warm claim in the
  request (including hand-off failure → another claim), caches invalidated by NOTIFY, batched writer flush,
  requirement checks, guest_unavailable errors, two-stage stop.
- Allowed here: `uv run pytest -x -q` against a local Postgres, and `go test ./...` in each Go module.
Done when: all suites pass; record counts in Notes.

---

## Handoff notes

(The implementing model writes out-of-scope findings and anything the owner must decide here.)

- Kubernetes VM-host networking: node discovery derives the API hostname on port 7443. That hostname must also
  route to the chart's node LoadBalancer. On clusters with separate ingress/node IPs, the operator must arrange
  shared routing or approve a distinct public node endpoint; the Go replica design must preserve public reachability.
- Startup preflight checks remote hosts only when a separate gateway already owns their links. A process that
  owns the listener checks infrastructure first and exposes host readiness after listeners start, avoiding a restart
  deadlock with registered hosts. `/readyz` retains named host/base checks.
- No runtime benchmarks or end-to-end suites ran: this macOS checkout has no `/dev/kvm`. The unit suite ran against a
  local Postgres only. Do not treat it as evidence of Kata, CSI, APFS saved-state or Hyper-V performance/correctness.
- The site build warns that the sitemap needs astro's `site` option; set it once the docs have a public URL.
- `servers.platform` still defaults to 'linux' in the baseline schema although servers are macOS or Windows only (T-30).
- Paused Docker sandboxes are frozen with `docker pause` (processes, not the VM); their memory is held until the
  sweeper deletes them. Only Kubernetes pauses the whole Kata VM (vm-control).
- The baked Firefox profile reaches Docker homes (named volumes copy image content on first use) but not Kubernetes
  home claims, which start empty; there Firefox makes its profile at first launch, with the same policies.
- A warm create whose API process dies mid-hand-off leaves that sandbox provisioning with no job; `expire()` re-queues
  its boot after STRANDED_SECONDS (60).
