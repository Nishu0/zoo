# Zoo: metrics and targets

The goal is to beat every sandbox platform on measured numbers, not claimed ones, and to publish numbers for desktops, which nobody else does.

## How we measure

- **Time to interactive (TTI):** `create()` → the first successful call inside the sandbox. For `code` that call is `execute_command("true")`; for `desktop` and `browser` it is `screenshot()`. Same definition as the [ComputeSDK leaderboard](https://computesdk.com/benchmarks/sandboxes) ([methodology](https://computesdk.com/methodology)).
- **Three patterns:** sequential (one at a time), staggered (one every 200 ms), burst (50 at once).
- **Statistics:** p50, p95 and p99 over at least 100 runs per pattern, from a client in the same region as the cluster.
- **Success rate** is reported with every timing. A failed or timed-out create counts against the timing too, at the 10 s ceiling.
- **Warm** means a sandbox was waiting in the warm pool. **Cold** means the pool was empty. Both are published.
- **Tool:** `scripts/perf.py` on a real Kata cluster before every release. Release notes carry the results.

## Competitors (measured, where measurements exist)

| Product | Claimed | Measured (ComputeSDK, TTI median) |
|---|---|---|
| Declaw | | 0.19 s |
| Daytona | "sub-90 ms" | 0.34 s |
| Tensorlake | | 0.57 s |
| E2B | ~150 ms | 0.44–0.69 s |
| Isorun (burst) | | 0.06 s |
| Modal | "sub-second" | |
| Vercel Sandbox, Cloudflare Sandbox, Namespace | "milliseconds" / "seconds" | |
| Browserbase, Cua, Scrapybara (desktops/browsers) | "instant" | none published |

These figures change daily. Recheck them before quoting.

## Targets

### 1. Startup (TTI)

| Kind | Pattern | p50 | p95 | p99 | Leader to beat |
|---|---|---|---|---|---|
| `code`, warm | sequential | **≤ 150 ms** | ≤ 300 ms | ≤ 500 ms | Declaw 0.19 s |
| `code`, warm | staggered | ≤ 150 ms | ≤ 300 ms | ≤ 500 ms | |
| `code`, warm | burst ×50 | **≤ 50 ms** | ≤ 250 ms | ≤ 500 ms | Isorun 0.06 s |
| `code`, cold | sequential | ≤ 1.5 s | ≤ 2.5 s | ≤ 4 s | |
| `desktop` / `browser`, warm | sequential | **≤ 400 ms** | ≤ 800 ms | ≤ 1.2 s | nobody publishes |
| `desktop` / `browser`, warm | burst ×50 | ≤ 500 ms | ≤ 1 s | ≤ 2 s | |
| `desktop` / `browser`, cold | sequential | ≤ 3 s | ≤ 5 s | ≤ 8 s | |
| any, stopped → running | sequential | ≤ 1.5 s | ≤ 3 s | ≤ 5 s | Namespace "seconds" |
| `macos` / `windows` VM | boot from clone | ≤ 30 s | ≤ 45 s | ≤ 60 s | nobody offers both |

What this needs:
- A warm claim does no boot at all, only database work, the guest handoff and secrets. Burst targets therefore need the pool at least as deep as the burst.
- The pool refills in parallel.
- A guest that has been handed its sandbox must answer within 10 ms.

### 2. Tool latency (server-side round trip, 1280×720)

| Tool | p50 | p99 |
|---|---|---|
| `screenshot` (PNG) | **≤ 30 ms** | ≤ 100 ms |
| `click`, `move_mouse`, `press_key` | **≤ 10 ms** | ≤ 40 ms |
| `type_text` (20 chars) | ≤ 30 ms | ≤ 100 ms |
| `screen_diff` | ≤ 10 ms | ≤ 30 ms |
| `wait_until_stable` | returns ≤ 50 ms after the last change | |
| `execute_command("true")` | **≤ 15 ms** | ≤ 50 ms |
| `read_file` / `write_file` (1 MB) | ≤ 30 ms | ≤ 100 ms |
| `accessibility_tree` | ≤ 150 ms | ≤ 500 ms |
| Live view: input → frame in browser | ≤ 60 ms | ≤ 150 ms |
| Terminal: keystroke → echo | ≤ 30 ms | ≤ 100 ms |

### 3. Reliability

| Metric | Target |
|---|---|
| Create success rate (reaches running within the 10 s ceiling, warm) | **≥ 99.95%** |
| Create success rate, cold | ≥ 99.9% |
| Tool call success rate (no 5xx or timeout, excluding the caller's own errors) | ≥ 99.99% |
| Unexpected sandbox stops | ≤ 1 per 10,000 sandbox-hours |
| API availability (`/readyz`) | ≥ 99.95% |
| All guests and nodes reconnected after a gateway restart | ≤ 5 s |
| Sandbox state change → webhook or SSE event | p50 ≤ 100 ms, p99 ≤ 1 s |

### 4. Density and cost (Kata, per 32 GB / 8-core node)

| Metric | Target |
|---|---|
| Idle `desktop` sandboxes per node | **≥ 16** |
| Idle `code` sandboxes per node | ≥ 40 |
| Memory per idle desktop (guest + VM overhead, actually used) | ≤ 1.2 GB |
| Memory per idle `code` sandbox | ≤ 400 MB |
| Control plane at rest (API + worker + gateway) | ≤ 768 MB RAM |
| Gateway capacity | ≥ 5,000 sandboxes per core |
| Memory per open VNC session in the gateway | ≤ 4 MB |

### 5. Developer experience

| Metric | Target |
|---|---|
| Signup → first successful tool call (hosted control plane) | **≤ 3 min** |
| `install.sh` on a fresh KVM machine → dashboard ready | ≤ 10 min, one command |
| Code to create a sandbox and take a screenshot | 2 lines |
| Errors carrying `code`, `hint` and `docs_url` | 100% |
| SDK and MCP tool parity with the REST API | 100% |

## How we hit them

Each target below names the bottleneck it faces and the design that removes it. Where the trim-down in `PLAN.md` already does it, the step is noted.

### A. The hot path: create, then the first call

Today a warm create goes: request → `jobs` row → a worker picks up the job → adopt → the client polls until running (about 1 s). Every stage below is removed or made concurrent.

1. **Claim inside the request.** `POST /sandboxes` claims a pooled sandbox in one statement:
   - `UPDATE pool_sandboxes … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING …`, on the node that already holds a ready one of that kind and size;
   - no job, no worker, no polling;
   - the response is already `running`.
2. **Hand-off in parallel, all async.** In one `asyncio.gather` over connections that are already open:
   - push secrets through the guest;
   - apply the network policy through the node's egress daemon (pooled sandboxes wait on deny-all);
   - write labels.
   - Profiles load only when one was asked for.
3. **Async all the way.**
   - The create and tool endpoints are `async def` with an asyncpg pool. No thread pool, so a burst of 50 doesn't queue behind 40 threads.
   - Several uvicorn workers per pod, and the API scales out horizontally.
4. **No database reads on the hot path.**
   - API keys, memberships, sandbox permissions and policies are cached in each process.
   - The cache is cleared by the `zoo_event` `NOTIFY` that already fires on change.
5. **No synchronous writes on the hot path.**
   - `tool_executions`, the sandbox "touch", audit entries and usage samples go into an in-process queue.
   - That queue is flushed with `COPY` every 50 ms or 500 rows.
   - The last 500 ms of logging is lost on a crash. Accepted: it is a log, not state.
6. **One persistent connection from each API process to the gateway** (gRPC, multiplexed), not a request per tool call.
7. **The pool sizes itself.**
   - Depth per (node, kind, size) is the larger of the configured minimum and the p99 creates per refill interval.
   - So a burst of 50 finds 50 waiting; claims spread across nodes; refills run in parallel.
   - Exported as `zoo_pool_available`; an empty pool is an alert.
8. **Pooled desktops are fully up.** XFCE has started, the guest is connected and the gateway's VNC session is already open, so the first screenshot only encodes a screen the gateway already holds.

Targets this serves: warm TTI ≤ 150 ms, burst ≤ 50 ms, desktop warm ≤ 400 ms.

### B. Cold boots (the pool is empty)

A cold boot is the pool's refill path and the miss case, so its speed sets how deep the pool must be.

1. **Pick the node ourselves.** Set `nodeName` from our own capacity view, skipping the Kubernetes scheduler round.
2. **Images are already on every node.**
   - A DaemonSet pre-pulls the sandbox images, and kubelet's `imagePullPolicy: IfNotPresent` never waits on a pull.
   - The images are kept small: one layer for the app, fonts and font caches baked in.
3. **Fast storage, never attached on the critical path.**
   - Home volumes are **local NVMe thin LVM volumes** (TopoLVM, which supports CSI snapshots), not network volumes.
   - Creating one is a thin-volume metadata operation (milliseconds), not a cloud disk attach (seconds).
   - Each node also keeps a few unbound volumes ready.
   - Durability and moves: snapshots are shipped to S3 and restored on the target node. This replaces "network storage follows the pod" in `PLAN.md`.
4. **Kata tuned for boot time.**
   - Cloud Hypervisor as the VMM, a minimal guest kernel, and the guest image on virtio-fs/DAX.
   - **Kata VM templating** (`enable_template`): new VMs clone a pre-booted VM, which cuts both boot time and per-VM memory.
5. **Start order inside the sandbox.**
   - zoo-guest and Xvfb start first, in parallel, with no `supervisord` priority chain waiting on XFCE.
   - The guest dials the gateway the moment it starts, with no backoff on the first attempt.
6. **A desktop session with nothing to do at start.**
   - XFCE with a pre-built configuration: no first-run dialogs, no session restore, the font and icon caches already built.
   - The window manager and panel start directly, without the session manager.
   - "Desktop ready" is the guest seeing the window manager's root window, not a timer.
7. **Browser kind:** Firefox starts from a pre-warmed profile (`startupCache` and an already-initialised profile baked into the image), with first-run pages and update checks off.

Targets this serves: code cold ≤ 1.5 s, desktop cold ≤ 3 s.

### C. Stop and resume

1. **Stop has two stages in one lifecycle.**
   - Stop first **pauses** the sandbox: the VM's vCPUs are frozen and it stays on its node.
   - After `ZOO_PAUSE_MINUTES` (default 30) it is deleted down to its home volume.
   - Resuming a paused sandbox takes ≤ 100 ms. Starting one that has been deleted down to its volume is a cold boot (section B) on the same node and volume, ≤ 1.5 s.
2. **Paused sandboxes give their memory back.** Free page reporting / virtio-balloon plus KSM (below) return most of a frozen VM's memory to the node. Under memory pressure the node deletes paused sandboxes first, oldest first.

Target this serves: stopped → running ≤ 1.5 s (≤ 100 ms while paused).

### D. Tool latency

1. **The gateway is rewritten in Go** (plan step 1 builds the VNC session there). It holds the guest connections, the node connections and the VNC sessions with no Python global interpreter lock, and it runs several replicas, each owning a share of the sandboxes (below).
2. **Screenshot**
   - The gateway keeps the screen in memory, updated by the VNC server's incremental updates.
   - Encoding is a **PNG with one filter type and zlib level 1** (SIMD deflate, `klauspost/compress`): about 8–12 ms for 1280×720.
   - The encoded PNG is **cached per frame version**, so a screenshot of an unchanged screen costs 0 ms.
3. **Click / key**
   - Sent on the open session, a single message, and the call returns once it is written.
   - VNC has no acknowledgement; correctness comes from the agent's next screenshot, as with every computer-use API.
4. **`screen_diff` / `wait_until_stable`**
   - The gateway keeps a 32×32-pixel tile hash grid of the previous frame (≈ 1 KB), not a second copy of the screen.
   - The diff compares hashes.
   - `wait_until_stable` waits for the VNC server to go quiet, so there is no polling.
5. **`execute_command`**
   - The guest keeps a pre-forked `sh` per user, and short commands go to it instead of a fresh fork and exec.
   - The output streams back on the same frame.
6. **`accessibility_tree`**
   - The guest subscribes to AT-SPI / AX / UI Automation change events and **keeps the tree up to date in memory**.
   - A call serialises the cached tree, which is fast however large Firefox's page is.
   - The first call after a big change rebuilds only the affected subtree.
7. **Live view**
   - The browser's VNC stream is relayed from the gateway's own session (Gate in between), never a second connection to x11vnc.
   - x11vnc runs with `-noxdamage` off and a 10 ms poll, and Xvfb uses `-fbdir` shared memory, so updates are cheap.
8. **Terminal**: the PTY bytes are relayed straight through, with Nagle off on every hop.

Targets this serves: screenshot ≤ 30 ms, click ≤ 10 ms, exec ≤ 15 ms, accessibility tree ≤ 150 ms, live view ≤ 60 ms, terminal ≤ 30 ms.

### E. Reliability

1. **Gateway with no single point of failure.**
   - N replicas behind one Service, with sandboxes spread across them by rendezvous hashing on the sandbox id.
   - A guest that dials the wrong replica is redirected once.
   - When a replica dies, its sandboxes go to the survivors, and guests reconnect with 100 ms jittered backoff.
   - That gives ≤ 5 s for every guest and node reconnect.
2. **Calls survive a guest blip.**
   - A tool call made while the guest is reconnecting waits up to 2 s for it, instead of failing.
   - Only operations that are safe to repeat (reads, screenshots, diffs) are re-sent after a disconnect mid-call; input and exec report the disconnect.
3. **Sandboxes don't get killed.**
   - Memory requests match measured idle use and sandboxes carry the highest PriorityClass, so the kubelet evicts other pods first. Requests are not set equal to limits, which would cost the density targets.
   - Sandboxes have a high PriorityClass.
   - A PodDisruptionBudget plus node drains that wait for the pool to absorb the load first.
   - Inside the VM, `earlyoom` kills the largest app before the kernel would kill the guest.
4. **Creates that can't fail half-way.** A claim is one transaction. If the hand-off fails, the sandbox goes back to the pool (or is scrapped) and a different one is claimed in the same request, within the 10 s ceiling.
5. **Events are pushed.** Webhook workers are woken by `NOTIFY` instead of polling, and SSE pushes as the notification arrives.
6. **Proof.**
   - A chaos suite kills gateway replicas, API pods, nodes and guests during `scripts/perf.py`.
   - Success rates are measured under it, not only on a calm cluster.

Targets this serves: create ≥ 99.95%, tools ≥ 99.99%, ≤ 1 unexpected stop per 10,000 h, reconnect ≤ 5 s, events ≤ 100 ms.

### F. Density

1. **CPU:**
   - Request 0.1 CPU with a limit of the sandbox's size (2 by default), so idle sandboxes don't reserve cores.
   - Quotas still count the size.
2. **Memory:**
   - Request what an idle sandbox actually uses (measured: desktop ≈ 700 MB, code ≈ 250 MB), with the limit at its size.
   - Kata with **virtio-mem / free page reporting** returns freed guest memory to the node.
3. **KSM on every node.**
   - Identical desktops share kernel, X, XFCE and library pages across VMs.
   - Kata VM templating shares the kernel and initrd memory outright.
4. **Nothing idle runs.** No Firefox at start in `desktop` (only in `browser`), no XFCE background services (tumbler, update notifier, power manager), and x11vnc only polls while a session is open.
5. **The gateway holds VNC sessions only for active sandboxes.** A session opens on the first screen call or viewer and closes 60 s after the last one. One screen (3.7 MB) plus the tile hash grid stays ≤ 4 MB.
6. **Control plane:** the Go gateway (about 50 MB) plus API and worker in Python, with lazy imports and no OpenTelemetry SDK loaded unless an endpoint is set. ≤ 768 MB in total.

Targets this serves: ≥ 16 desktops and ≥ 40 code sandboxes per 32 GB node, ≤ 1.2 GB per desktop, ≤ 768 MB control plane, ≤ 4 MB per VNC session, ≥ 5,000 sandboxes per gateway core.

### G. macOS and Windows boot ≤ 30 s

1. **macOS:**
   - Boot the base once to the logged-in desktop with the guest connected, then save its state (Virtualization.framework `saveMachineStateTo`, macOS 14+).
   - Each sandbox is an APFS clone of the disk **and** the saved state, and is resumed with `restoreMachineStateFrom`: seconds, not a boot.
   - After the resume the guest gets its new identity from the host.
2. **Windows:**
   - The template is saved running (Hyper-V saved state).
   - A sandbox is a differencing disk plus a copy of that state, started straight from it.
   - Identity is pushed through the Guest Service Interface after the resume.

The target holds with room to spare (≤ 10 s expected).

### H. Developer experience

1. **`install.sh` ≤ 10 min:** an offline bundle (k3s, kata-deploy and the Zoo images) in the release, so the install doesn't wait on registries. It checks KVM in the first second.
2. **Signup → first call ≤ 3 min:** the welcome flow (create a sandbox, copy a key, copy the MCP config) on a warm pool, so the first sandbox is ready before the key is copied.

## What changes in PLAN.md

These designs add to or replace parts of the trim-down:

| Change | Replaces |
|---|---|
| Gateway rewritten in Go, several replicas with rendezvous hashing | the Python gateway, the "exactly one" gateway |
| Warm claim inside the request; async endpoints with asyncpg | the boot job for warm creates, sync endpoints |
| Process-local caches cleared by `NOTIFY`; batched execution, audit and usage writes | database reads and writes per call |
| Local NVMe thin volumes (TopoLVM), snapshots shipped to S3 for moves | network storage following the pod |
| Kata with Cloud Hypervisor and VM templating; KSM; free page reporting | default Kata settings |
| Two-stage stop (pause, then delete down to the volume) | stop = delete the pod |
| Saved-state resume for macOS and Windows | a cold boot from the clone |
| Accessibility tree cached in the guest | walking the tree on every call |

## Instrumentation

Metrics (OpenTelemetry, Prometheus names):
- `zoo_sandbox_ready_seconds{kind, warm}`: create → first successful call.
- `zoo_sandbox_creates_total{kind, warm, outcome}`.
- `zoo_tool_duration_seconds{tool, os}` (exists).
- `zoo_job_duration_seconds` and `zoo_job_wait_seconds` (exist).
- `zoo_pool_available{kind}` and `zoo_pool_refill_seconds`.
- `zoo_gateway_sessions{type}` (guest, node, vnc) and `zoo_gateway_reconnect_seconds`.
- `zoo_event_delivery_seconds{channel}` (sse, webhook).

Publishing:
- `scripts/perf.py` runs the three patterns, warm and cold, for every kind. It prints p50, p95, p99 and the success rate, and writes JSON for the site.
- The site has a live benchmarks page with these numbers and the competitor table.
- Get listed on ComputeSDK.
