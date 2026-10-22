# Cloud Agent-Sandbox Providers: Speed & Isolation Techniques (state as of October 2026)

Research scope: which latency/isolation techniques each commercial provider actually uses — warm pools, memory-snapshot resume, snapshot-fork, guest-agent transport direction, batch exec, host-side egress, first-class pause/resume + auto-pause, and macOS. Status markers: **CONFIRMED** (official docs/changelog/repo), **SECONDARY** (competitor blog or third party), **ABSENT** (searched, nothing found), **UNKNOWN** (not researched to ground / ambiguous).

## Key Question 0: Cross-provider matrix (summary of the 8-technique checklist)

### Takeaway
By late 2026 every major provider uses VM/container snapshot resume somewhere, but only Daytona and OpenSandbox document **true warm pools** (pre-created running sandboxes claimed at create time); only E2B, Morph, Modal, CodeSandbox, TensorLake, Blaxel, and OpenSandbox do **memory-inclusive** snapshot resume; **host-side, deny-capable egress** is documented by E2B, Cloudflare, TensorLake, and OpenSandbox; **batch multi-command exec over one round trip was found at NO provider**; **macOS is served only by Cua**.

### Cited Findings (matrix; details + URLs in sections below)

| Provider | 1. Warm pools | 2. Mem-snapshot resume | 3. Snapshot-fork | 4. Agent transport direction | 5. Batch exec | 6. Host-side egress | 7. Pause/resume + auto-pause | 8. macOS |
|---|---|---|---|---|---|---|---|---|
| E2B | ABSENT (snapshot-restore ~80ms instead) | CONFIRMED (~80ms template load; ~1s resume) | CONFIRMED (fork API, in-place snapshot) | CONFIRMED dial-IN (envd gRPC/REST :49983) | ABSENT (fork has batch-count only) | CONFIRMED (allowOut/denyOut firewall + BYO SOCKS5 proxy, host-side) | CONFIRMED (pause/resume, indefinite retention; auto-pause per 3rd party) | ABSENT |
| Daytona | CONFIRMED (docs + APIs, replenishment) | CONFIRMED (memory snapshots; pause/resume/fork) | CONFIRMED | UNKNOWN | UNKNOWN | UNKNOWN | CONFIRMED (auto-stop/auto-pause/auto-delete/TTL) | ABSENT (Linux + Windows VMs only) |
| Modal | ABSENT | CONFIRMED (Memory Snapshots Alpha) | CONFIRMED (filesystem + memory experimental) | UNKNOWN | ABSENT | UNKNOWN | ABSENT (snapshot w/ TTL instead) | ABSENT |
| Runloop | ABSENT | ABSENT (disk snapshots only) | PARTIAL (disk-snapshot branching) | UNKNOWN (SSH/CLI/IDE dial-in) | ABSENT | UNKNOWN | PARTIAL (Suspend/Resume, paid tier) | ABSENT — **company winding down Nov 13, 2026** |
| Blaxel | VARIANT (per-sandbox perpetual "standby", not shared pool) | CONFIRMED (25ms resume, memory intact) | ABSENT | UNKNOWN | ABSENT | UNKNOWN (competitor claims no egress gateway) | CONFIRMED (auto-standby after ~15s idle, scale-to-zero) | ABSENT |
| TensorLake | ABSENT | CONFIRMED (snapshot = filesystem + memory) | CONFIRMED (restore-from-snapshot) | CONFIRMED dial-IN (proxy → in-sandbox daemon; SSH proxy; WS PTY) | ABSENT | CONFIRMED (allow_out/deny_out, live-updatable) | CONFIRMED (suspend/resume APIs) | ABSENT (Linux desktop) |
| Morph | ABSENT | CONFIRMED (running-process state preserved) | CONFIRMED (Infinibranch, sub-250ms) | UNKNOWN | ABSENT | UNKNOWN | PARTIAL (snapshot + TTL; no explicit pause API found) | ABSENT |
| CodeSandbox | ABSENT | CONFIRMED (Firecracker mmap; 0.5–2s product resume) | CONFIRMED (clone <2s; bootupType=FORK) | UNKNOWN (connect() dials in) | ABSENT | UNKNOWN | CONFIRMED (hibernate/resume first-class) | ABSENT |
| Replit | CONFIRMED (container pooling since ≥2018) | ABSENT (block-device CoW + git, no VM memory) | PARTIAL (constant-time FS/DB forks) | UNKNOWN | ABSENT | UNKNOWN | ABSENT (always-on; checkpoints instead) | ABSENT |
| Cloudflare | ABSENT (starts on first use) | ABSENT (R2 dir backups only) | ABSENT | CONFIRMED dial-IN (DO → Container API → sandbox-shim) | ABSENT | CONFIRMED (enableInternet:false; Worker-controlled; globalOutbound:null) | ABSENT (sleep clears state; backup/restore) | ABSENT |
| OpenSandbox (Alibaba/opensandbox-group) | CONFIRMED (pre-warmed Firecracker pools, ~80ms admission) | CONFIRMED (pause/resume w/ memory+disk) | PARTIAL (checkpoint→artifact store; no fork branding) | CONFIRMED dial-IN (ingress gateway → execd) | ABSENT | CONFIRMED (per-sandbox egress; rules invisible/immutable from guest) | CONFIRMED (pause/resume first-class) | ABSENT |
| Cua (entrant) | CONFIRMED (Fleets warm pools of machines) | PARTIAL (snapshot fork of desktops) | CONFIRMED ("fork from snapshots") | CONFIRMED dial-IN (cua-spacesd :3211; legacy SSH) | ABSENT | UNKNOWN | PARTIAL (pool reclaim; no pause API found) | **CONFIRMED (macOS Sequoia/Tahoe on Apple Silicon, cloud fleets)** |

---

## Key Question 1: Warm pools / pre-warmed capacity (create = admission into an already-running sandbox)

### Takeaway
Only **Daytona** and **OpenSandbox** publish real warm-pool mechanics as a product feature (per-snapshot pools, claim-on-create, background replenishment). **Blaxel** achieves a similar effect differently: every individual sandbox auto-returns to a memory-resident "standby" rather than a shared pool. **Replit** has used container pooling since at least 2018, and **Cua** offers fleet-level warm pools of desktop machines. Everyone else: boot/restore-on-create.

### Cited Findings
- **Daytona — CONFIRMED, mechanics published.** "Warm pools keep a configured number of pre-created, running sandboxes built from a snapshot in a specific region. When you create a sandbox that matches a pool, Daytona hands over a warm sandbox instead of provisioning a new one." Claim requires snapshot + region match; "Each time a request claims a warm sandbox, Daytona creates a new warm sandbox in the background to bring the pool back to its configured size." Full CRUD APIs: create pool (snapshot, pool size ≥1, region), list (reports desired `pool` vs `currentSize`, `errorReason` when quota-blocked), resize (converges in background, destroys newest unclaimed when shrinking, 0 drains), delete. Warm sandboxes count against org quota. Creating a second pool for same snapshot+region → 409. — [Warm Pools | Daytona docs](https://www.daytona.io/docs/en/warm-pools/) (current, Oct 2026)
- **Daytona headline latency:** "Sub 90ms sandbox creation from code to execution" on homepage (fetched Oct 20, 2026); earlier launch-era claim "Start in ~27ms. End-to-end latency under 90ms." — [daytona.io](https://www.daytona.io/); [Fastest-Growing Infra Company in History (Jul 2025)](https://www.daytona.io/dotfiles/fastest-growing-infra-company-in-history)
- **OpenSandbox — CONFIRMED, metrics published.** "Pre-warmed, Firecracker-backed pools give constant-time admission and ~80ms startup" (May 14, 2026 mirror of repo README); README features table: "Firecracker creation: 64 ms P50 (serial) / 125.4 ms P99 (10 concurrent)"; an older README snapshot says "Reference warm Firecracker creation: 97 ms P50 (serial) / 308 ms P99 (10 concurrent)". Warm-pool plumbing is visible in SDKs: `poolredis` Go module merged into the sandbox module and `RedisPoolStateStore` exported from the npm SDK. — [OpenSandbox README](https://github.com/opensandbox-group/OpenSandbox/blob/main/README.md); [mirror, May 2026](https://github.com/alibaba/opensandbox); [older README snapshot](https://github.com/q-qp-p/alibaba-OpenSandbox); [npm @alibaba-group/opensandbox](https://www.npmjs.com/package/@alibaba-group/opensandbox)
- **Blaxel — VARIANT (per-sandbox standby, not a shared pool).** "persistent microVM runtime that boots in milliseconds, suspends to zero when idle, and resumes in 25ms with full memory and filesystem intact"; "Standby starts automatically after 15 seconds of network inactivity"; docs: "Sandboxes resume from standby in under 25 milliseconds and automatically scale to zero after a few seconds of inactivity. Standby keeps a sandbox instantly resumable, memory included, and you keep paying for the storage of its snapshot." Initial creation "typically ~200–600ms" from template. — [Sandboxes | Blaxel](https://blaxel.ai/platform/sandboxes); [Blaxel Sandboxes docs](https://docs.blaxel.ai/Sandboxes/Overview); [Blaxel blog, AI agent runtime comparison (2026)](https://blaxel.ai/blog/ai-agent-runtime-tools-production-workloads)
- **Replit — CONFIRMED (historical, for its consumer containers).** "we have previously implemented a pooling mechanism on our infrastructure so that containers are ready and hot when you need them... when you land on a React framework environment, we grab a container for you that's already running" — [Replit Preboot blog (Jun 2018)](https://replit.com/blog/preboot). No current warm-pool API documentation found.
- **Cua — CONFIRMED (fleet-level).** "Cua Fleets: Elastic infrastructure that scales sandboxes into warm pools of machines you claim on demand for evals, RL loops, data generation"; "A Fleet maintains sandbox capacity; your code claims a desktop from a pool." — [cua.ai](https://cua.ai/); [Cua docs](https://cua.ai/docs/llms-full.txt)
- **E2B — ABSENT.** No warm-pool docs; speed comes from template snapshots: "We call this sandbox snapshot a sandbox template... This allows us to load the sandbox in ~80ms any time later with all the processes already running." — [E2B Templates: How it works](https://docs.e2b.dev/template/how-it-works) (current; kernel table shows templates from ≥ Nov 27, 2025)
- **Modal — ABSENT.** No warm-pool documentation found; sandboxes created on demand from images/snapshots. — [Modal sandbox snapshots docs](https://modal.com/docs/guide/sandbox-snapshots)
- **Runloop — ABSENT.** Markets "10k+ parallel sandboxes, 10GB image startup time in <2s" on a "custom bare-metal hypervisor" but documents no pool. — [runloop.ai](https://runloop.ai/)
- **Cloudflare — ABSENT.** "A sandbox starts its container on first use" (0.x docs); Containers GA April 13, 2026. — [Cloudflare Sandbox SDK 0.x](https://developers.cloudflare.com/sandbox/sdk/); [GA changelog (Apr 13, 2026)](https://developers.cloudflare.com/changelog/post/2026-04-13-containers-sandbox-ga)
- **CodeSandbox, Morph, TensorLake — ABSENT.** No warm-pool documentation found in SDK/docs pages reviewed (CodeSandbox speed is snapshot-resume based: template boot "1–3 seconds" — [Core Concepts](https://codesandbox.io/docs/sdk/core-concepts)).

### Inferences
- Daytona is currently the only commercial provider that exposes warm pools as a first-class, quota-integrated API surface with documented replenishment semantics — a direct template for "create = admission into a running sandbox."
- OpenSandbox (open source, CNCF-listed) publishes the most precise create-latency numbers (P50/P99 under concurrency), which no commercial provider matches in granularity.
- Blaxel's "perpetual standby" is functionally warm *per tenant+sandbox*, shifting cost to snapshot storage; a shared warm pool is cheaper for bursty multi-tenant traffic but Blaxel's model gives per-sandbox memory warmth, which pools of *pristine* sandboxes do not.

### Gaps
- Whether E2B, Modal, Runloop, or Blaxel run internal (undocumented) warm pools could not be verified; absence of documentation ≠ absence of technique.
- Daytona does not publish warm-claim p50/p99 or how pool sandboxes are pre-created (booted vs snapshot-restored).

---

## Key Question 2: Memory snapshots / VM snapshot resume (Firecracker/CROSVM/QEMU/CRIU) with published latencies

### Takeaway
Firecracker memory-snapshot resume is the dominant technique in 2026 (E2B, CodeSandbox, Blaxel, Morph, TensorLake, OpenSandbox; Modal uses gVisor-based snapshots in Alpha). Published resume latencies range from Blaxel's "<25ms" marketing claim, to E2B's "~80ms template load / ~1s pause-resume", to CodeSandbox's engineering-level 200–300ms raw Firecracker resume (0.5–2s product-level). Runloop is the outlier: disk snapshots only.

### Cited Findings
- **E2B — CONFIRMED.** Firecracker microVMs (open-source infra; envd has a `--isnotfc` flag "skipping Firecracker MMDS polling" — [k8s agent-sandbox envd example](https://pkg.go.dev/sigs.k8s.io/agent-sandbox@v1.0.6/examples/envd-sandbox)). Template = "Snapshots are saved running sandboxes. We serialize and save the whole sandbox's filesystem together with all the running processes... load the sandbox in ~80ms." — [How it works](https://docs.e2b.dev/template/how-it-works). Pause/resume: "pausing resets the runtime window, and your sandbox's full state is preserved indefinitely"; persistence docs: pause saves "not only state of the sandbox's filesystem but also the sandbox's memory... all running processes, loaded variables." — [Sandbox lifecycle](https://docs.e2b.dev/sandbox); [persistence docs](https://github.com/joe-yeomans/e2b-docs/blob/main/docs/sandbox/persistence.mdx). Latency: "Pausing takes about 4 seconds per GiB of RAM; resuming takes about 1 second"; paused sandboxes "persist indefinitely" — [Blaxel comparison blog (Aug 22, 2026 data)](https://blaxel.ai/blog/code-execution-sandboxes-for-ai-agents) (SECONDARY, citing E2B docs). Fork docs independently confirm memory capture (see Q3).
- **CodeSandbox — CONFIRMED, deepest engineering detail.** Hibernation creates a memory snapshot by default; resume table: Memory/Disk 0.5–2s, Disk-only 5–20s (re-runs setup), Archived 20–60s; hibernated sandboxes kept on disk 7 days, then archived. — [Core Concepts](https://codesandbox.io/docs/sdk/core-concepts); [Resuming Sandboxes](https://codesandbox.io/docs/sdk/resume). Engineering blog: Firecracker `create_snapshot` yields snapshot.snap + memory.snap; resume uses mmap so "resume only requires 300-400MB of memory... results in resume timings within ~200-300ms"; snapshot write ≈1s/GB; their clone method "reduced our timings tenfold." — [How we clone a running VM in 2 seconds (Sep 2022)](https://codesandbox.io/blog/how-we-clone-a-running-vm-in-2-seconds). GitHub README: "snapshot restore times within 1 second." — [codesandbox-sdk repo](https://github.com/codesandbox/codesandbox-sdk)
- **Blaxel — CONFIRMED (marketing precision only).** "Resume from standby in 25 milliseconds with full memory state restored, at any time"; "Sandboxes are persisted forever across sessions with filesystem & memory snapshots"; Firecracker-based microVM per their own comparison table. Archiving = filesystem-only ("memory is not preserved"). — [platform page](https://blaxel.ai/platform/sandboxes); [docs Overview](https://docs.blaxel.ai/Sandboxes/Overview); [blog comparison table](https://blaxel.ai/blog/ai-agent-runtime-tools-production-workloads). No methodology/percentiles published.
- **Morph — CONFIRMED.** Infinibranch: "take a snapshot of a running computation at any point, and instantly create multiple copies that all continue from exactly the same point" — docs demo resumes a running process (counter keeps incrementing) from snapshot; snapshot of running instance via `instance.snapshot()`. — [Morph docs](https://cloud.morph.so/docs/developers); [Boot Instance from Snapshot](https://cloud.morph.so/docs/documentation/instances/boot-from-snapshot). Stack/latency (SECONDARY): "Infinibranch microVM (Firecracker) sandbox infrastructure... sub-250ms branch, snapshot, and restore" — [api-evangelist/morph-labs profile](https://github.com/api-evangelist/morph-labs)
- **TensorLake — CONFIRMED.** "Snapshot Sandbox: Capture the current filesystem and memory state of a running sandbox"; "Restore Sandbox: Start a new sandbox from a previously created snapshot"; PyPI: "The Sandbox API creates MicroVM sandboxes." — [API v2 introduction](https://docs.tensorlake.ai/api-reference/v2/introduction); [PyPI tensorlake](https://pypi.org/project/tensorlake/0.5.103). No resume latency published.
- **Modal — CONFIRMED (Alpha).** "Memory Snapshots... copies of a Sandbox's entire state, both in memory and on the filesystem... restored later to create a new Sandbox, which is an exact clone of the original Sandbox... All running processes will still be running." API: `_experimental_enable_snapshot` + `sb._experimental_snapshot()` → `SandboxSnapshot`; restore via `Sandbox._experimental_from_snapshot(snapshot)`. Filesystem snapshots (`sb.snapshot_filesystem()`) produce an Image usable as a base; mountable via `mount_image`. Expiry: memory snapshots kept 7 days, filesystem 30 days by default (SECONDARY — [Blaxel comparison](https://blaxel.ai/blog/code-execution-sandboxes-for-ai-agents)). Isolation: gVisor by default per [Blaxel vs E2B vs Modal](https://blaxel.ai/blog/blaxel-vs-e2b-vs-modal) (SECONDARY). — [Modal sandbox snapshots](https://modal.com/docs/guide/sandbox-snapshots)
- **OpenSandbox — CONFIRMED.** "Firecracker sandboxes support pause/resume with memory and disk state preserved"; FastSandbox "pause/resume checkpoints state to the artifact store" (May 2026). — [README](https://github.com/opensandbox-group/OpenSandbox/blob/main/README.md); [alibaba mirror](https://github.com/alibaba/opensandbox)
- **Runloop — ABSENT for memory.** "Currently only disk snapshots are supported." Suspend/Resume is a paid-tier feature. — [Devbox Snapshots docs](https://docs.runloop.ai/docs/devboxes/snapshots); [pricing](https://runloop.ai/pricing)
- **Cloudflare — ABSENT.** No VM snapshot APIs; persistence via DirectoryBackup to R2 ("Create point-in-time snapshots of directories and restore them") — filesystem only. Blaxel's table (SECONDARY): "State cleared on sleep; backup/restore APIs." — [Cloudflare API reference](https://developers.cloudflare.com/sandbox/api/); [Blaxel comparison](https://blaxel.ai/blog/code-execution-sandboxes-for-ai-agents)
- **Replit — ABSENT (different architecture).** No VM memory snapshots; instead Network-block-device storage (16 MiB immutable chunks in GCS, manifest-based CoW) + git checkpoints + forkable databases. — [Inside Replit's Snapshot Engine (Dec 17–18, 2025)](https://replit.com/blog/inside-replits-snapshot-engine)
- **Daytona — CONFIRMED (feature-listed).** Sandboxes docs list "Fork / Pause/resume / Memory snapshots" for the Linux VM class. — [Daytona Sandboxes docs](https://www.daytona.io/docs/en/sandboxes/). Third-party analysis (SECONDARY): "Warm microVM pools and memory-snapshot restore, not a cold Firecracker boot per request." — [canaicopyit.com/daytona](https://canaicopyit.com/daytona). No published resume latency.

### Inferences
- Blaxel's "25ms" and E2B's "~80ms" are not comparable: Blaxel's number is standby-resume of a memory-resident sandbox (storage rehydration only), E2B's ~80ms is template-load from a saved snapshot; CodeSandbox's 200–300ms is raw Firecracker LoadSnapshot+Resume, before their orchestration overhead. Apples-to-apples latency comparison is impossible from public data.
- CodeSandbox is the only provider publishing per-stage mechanics (mmap on-demand paging, working-set ~300–400MB, 1s/GB snapshot-write cost) — useful as the public reference implementation of Firecracker snapshot resume.

### Gaps
- No provider except CodeSandbox/OpenSandbox publishes percentile latencies or measurement methodology; Blaxel's 25ms and Morph's 250ms are vendor figures without methodology.
- Daytona's memory-snapshot implementation (Firecracker vs other) and latency are undocumented publicly.
- Modal's memory-snapshot backend (gVisor checkpoint? QEMU? "VM Sandboxes in alpha") is not explained in the retrieved docs.

---

## Key Question 3: Snapshot-fork / fork-from-snapshot of a fully initialized sandbox

### Takeaway
Snapshot-fork is now table stakes: E2B, Morph, Modal, CodeSandbox, TensorLake, Daytona, and Cua all support forking a running/initialized sandbox state; E2B and Morph have the most explicit fork semantics, Modal's is explicitly experimental. OpenSandbox checkpoints to an artifact store but does not brand "fork."

### Cited Findings
- **E2B — CONFIRMED (explicit in-place semantics).** "Forking lets you create running copies of a sandbox in a single call. The sandbox is snapshotted in place — paused, captured with its full filesystem and memory state, and resumed — and new sandboxes boot from that snapshot. Each fork is an independent sandbox with its own ID and timeout. It starts with the exact state the original had at the moment of the fork — files, running processes." API: "All forks boot from the same snapshot, so the snapshot is captured once regardless of count." — [Sandbox forking docs](https://docs.e2b.dev/sandbox/fork); [Fork sandbox API](https://e2b.dev/docs/api-reference/sandboxes/fork-sandbox); [CLI fork](https://docs.e2b.dev/cli/fork-sandbox)
- **Morph — CONFIRMED (signature feature, "Infinibranch").** Snapshot a running VM → start N instances that "all continue from exactly the same point" (running process state preserved; demo counter continues incrementing); instances boot from snapshot with TTL options. — [Morph docs](https://cloud.morph.so/docs/developers); [Boot from snapshot](https://cloud.morph.so/docs/documentation/instances/boot-from-snapshot); [Morph: Snapshot and Fork VMs for AI Agents](https://www.morphllm.com/fork-vm-for-ai-agents); sub-250ms figure (SECONDARY): [api-evangelist/morph-labs](https://github.com/api-evangelist/morph-labs)
- **Modal — CONFIRMED (experimental).** "Start multiple Sandboxes from the same snapshot... Each fork starts from the snapshotted state" (both filesystem snapshots as Images and memory snapshots via `_experimental_from_snapshot`); snapshot TTLs apply and re-snapshotting resets lifetime; `NotFoundError` handling documented for expired snapshots. — [Modal sandbox snapshots](https://modal.com/docs/guide/sandbox-snapshots)
- **CodeSandbox — CONFIRMED.** "Cloning VMs & Snapshots within 2 seconds" (README); SDK exposes `bootupType`: `RUNNING`, `CLEAN`, `RESUME` or `FORK` on created sandboxes. — [codesandbox-sdk](https://github.com/codesandbox/codesandbox-sdk); [Creating Sandboxes](https://codesandbox.io/docs/sdk/create)
- **TensorLake — CONFIRMED (as restore).** "Restore Sandbox: Start a new sandbox from a previously created snapshot" (snapshot includes memory). — [API v2 introduction](https://docs.tensorlake.ai/api-reference/v2/introduction)
- **Daytona — CONFIRMED (feature-listed).** "Fork" listed as a Sandbox capability alongside pause/resume and memory snapshots. — [Sandboxes docs](https://www.daytona.io/docs/en/sandboxes/)
- **Cua — CONFIRMED.** "Fork from snapshots, reproduce failures" for computer-use fleets. — [cua.ai](https://cua.ai/)
- **OpenSandbox — PARTIAL.** Pause/resume "checkpoints state to the artifact store" and Fast Sandbox provisions from golden-image templates; no fork-from-live-sandbox API documented. — [alibaba/OpenSandbox](https://github.com/alibaba/opensandbox)
- **Runloop — PARTIAL (disk only).** "Snapshot and branch from sandbox disk state" — no memory state in forks. — [runloop.ai](https://runloop.ai/); [snapshots doc](https://docs.runloop.ai/docs/devboxes/snapshots)
- **Blaxel — ABSENT.** No fork API found; context sharing is via "Blaxel Agent Drive" instead ("Reuse the context persisted in one sandbox across other workloads"). — [platform page](https://blaxel.ai/platform/sandboxes)
- **Cloudflare, Replit — ABSENT** as VM-level fork (Cloudflare has R2 directory restore into any container "including one on a newer image" — [sandbox-sdk README](https://github.com/cloudflare/sandbox-sdk); Replit forks are filesystem/database-level CoW — [snapshot engine blog](https://replit.com/blog/inside-replits-snapshot-engine)).

### Inferences
- E2B's fork implementation (pause → capture memory+fs → resume original → boot forks from that snapshot, captured once regardless of fork count) is the most completely specified public design; it doubles as evidence that E2B's fork is memory-inclusive, complementing its pause/resume.
- Morph's Infinibranch remains the branding standard for "fork a live running computation"; its Firecracker basis plus sub-250ms fork is the benchmark competitors are compared against.

### Gaps
- Daytona fork latency and whether forks share CoW backing storage are undocumented.
- Whether Cua's snapshot-fork preserves running process state (memory) or only disk is not stated in retrieved pages.

---

## Key Question 4: Guest agent connection direction (agent dials OUT + persistent websocket vs platform dials IN per command)

### Takeaway
Every provider that documents its transport uses **dial-IN**: an in-sandbox daemon/server exposes an API and the platform or SDK connects into it (E2B envd, TensorLake proxy→daemon, Cloudflare DO→sandbox-shim, OpenSandbox ingress→execd, Cua cua-spacesd). **No provider was found documenting the opposite design** (in-sandbox agent dialing OUT and holding a persistent websocket). This direction is rarely documented explicitly, so most providers are UNKNOWN.

### Cited Findings
- **E2B — CONFIRMED dial-IN.** "envd — Daemon that runs inside a sandbox that allows interacting with the sandbox via calls from the SDK" (official README). envd "exposes an E2B-compatible REST and gRPC API on port 49983" with filesystem/process APIs, `/health /metrics /init /envs /freeze /unfreeze`, and port auto-forwarding (k8s agent-sandbox packaging). The SDK imports `e2b.envd.process`, creates gRPC clients to envd; the platform's changelog shows auth hardening where "Calls to the sandbox controller (envd) must send the returned envdAccessToken in the X-Access-Token header, or envd returns 401" — i.e., the caller reaches envd over the network (inbound), with a k8s NetworkPolicy named `envd-deny-ingress` in reference deployments. — [e2b-dev/runtime envd README](https://github.com/e2b-dev/runtime/blob/main/packages/envd/README.md); [agent-sandbox envd example](https://pkg.go.dev/sigs.k8s.io/agent-sandbox@v1.0.6/examples/envd-sandbox); [E2B Python SDK command.py](https://github.com/e2b-dev/E2B/blob/main/packages/python-sdk/e2b/sandbox_async/commands/command.py); [E2B changelog](https://docs.e2b.dev/changelog)
- **TensorLake — CONFIRMED dial-IN.** "The sandbox proxy also exposes runtime endpoints for each running sandbox. These requests are routed through the sandbox proxy to the daemon inside the sandbox." Runtime APIs served at `https://<sandbox-id>.sandbox.tensorlake.ai/api/v1/...` (processes, PTY over WebSocket, TCP tunnels over WebSocket); "SSH Lifecycle: Inspect or manage the in-sandbox SSH daemon used by Tensorlake's SSH proxy." — [Tensorlake API v2 introduction](https://docs.tensorlake.ai/api-reference/v2/introduction); [Commands & Processes](https://docs.tensorlake.ai/sandboxes/commands)
- **Cloudflare — CONFIRMED dial-IN.** "The Durable Object starts the instance and runs commands with the Container API on `this.ctx.container`"; in-container helper "sandbox-shim, the Linux helper that Files, S3Mount, and DirectoryBackup run" is invoked by the platform. — [cloudflare/sandbox-sdk README](https://github.com/cloudflare/sandbox-sdk)
- **Cua — CONFIRMED dial-IN.** Images ship "cua-spacesd (port 3211)" and exec goes through it ("exec runs through cua-spacesd"); older macOS 15 image: "there exec runs over SSH (lume ssh)." — [Create a local macOS sandbox](https://cua.ai/docs/start-here/create-a-local-macos-sandbox); [Cua docs index](https://cua.ai/docs/llms-full.txt)
- **OpenSandbox — CONFIRMED dial-IN (architecturally).** "Route inbound traffic through a unified ingress gateway"; components include `execd` (in-sandbox execution daemon) and node "agent delegation" via a host unix socket; Fast Sandbox exposes a "FastPath v2 gRPC control plane." — [OpenSandbox README](https://github.com/opensandbox-group/OpenSandbox/blob/main/README.md); [Fast Sandbox Firecracker architecture](https://open-sandbox.ai/architecture/fast-sandbox/firecracker); [release 1.1.0 (Sep 21, 2026)](https://github.com/opensandbox-group/OpenSandbox/releases/tag/release-1.1.0)
- **Runloop — UNKNOWN (usage implies dial-in).** "develop on sandboxes with SSH, CLI, and IDE connections" — [runloop.ai](https://runloop.ai/)
- **Daytona, Modal, Morph, Blaxel, CodeSandbox, Replit — UNKNOWN.** No official documentation of agent transport direction found. (CodeSandbox's `sandbox.connect()` implies inbound connection into the VM — [Resuming Sandboxes](https://codesandbox.io/docs/sdk/resume) — but the transport is unspecified.)
- **Nobody found documenting outbound-agent transport.** A targeted search for in-sandbox agents dialing out over persistent websockets returned no provider documentation at any of the 11 providers.

### Inferences
- Dial-in through a proxy/gateway (TensorLake, OpenSandbox, E2B) is the dominant published pattern; it requires inbound routing infrastructure (port mapping, hostname-per-sandbox) which is itself a latency and multi-tenancy cost center.
- If a self-hosted platform uses an outbound-dialing guest agent with a persistent connection, that would be *structurally different* from everything documented publicly — but it cannot be claimed as unique against providers whose transport is simply undocumented (Daytona, Modal, Morph, Blaxel, Runloop).

### Gaps
- Transport direction for Daytona, Modal, Morph, Blaxel, Runloop, CodeSandbox, Replit is undocumented publicly; this is the single largest evidence gap in the checklist. E2B's envd does support streaming/websocket-ish gRPC connections but the *initiation direction* is clearly inbound.

---

## Key Question 5: Batch/multi-command exec over a single HTTP round trip (N commands, stop-on-error)

### Takeaway
**Not found at any of the 11+ providers.** All documented exec APIs are one-command-per-request (optionally streaming via SSE/WebSocket). The closest analogues are E2B's batch *fork* (N forks per call) and Cloudflare's session/terminal multiplexing — neither is multi-command batch execution.

### Cited Findings
- **E2B — ABSENT for batch exec.** SDK exposes `sandbox.commands.run/exec` per command (with streaming stdout/stderr) — [SDK reference](https://e2b.dev/docs/sdk-reference/js-sdk/v1.1.1/sandbox). Closest: fork API takes a count — "Number of forked sandboxes to create. All forks boot from the same snapshot, so the snapshot is captured once regardless of count." — [Fork sandbox API](https://e2b.dev/docs/api-reference/sandboxes/fork-sandbox)
- **TensorLake — ABSENT.** "Commands and processes share one API. Use run/exec when you want to run something and wait for the result; use start_process..." — one process per POST `/api/v1/processes`; output streaming via SSE (`/stdout/follow`). — [Commands & Processes](https://docs.tensorlake.ai/sandboxes/commands)
- **Modal — ABSENT.** `Sandbox.exec("bash","-c",...)` per call; process objects per exec. — [sandbox snapshots examples](https://modal.com/docs/guide/sandbox-snapshots)
- **Cloudflare — ABSENT.** `container.exec([...])` per command returning process handles; 1.0 preview uses `exec(argv)` process handles; has *Sessions* ("isolated execution contexts within a sandbox... own shell state") and a browser Terminal over WebSocket — session reuse, not batch. — [Cloudflare API reference](https://developers.cloudflare.com/sandbox/api/); [containers concept](https://developers.cloudflare.com/sandbox/concepts/containers)
- **OpenSandbox — ABSENT.** "Execute commands, manage files" via built-in APIs; command execution & streaming per command in the TS SDK. — [README](https://github.com/opensandbox-group/OpenSandbox/blob/main/README.md); [npm SDK](https://www.npmjs.com/package/@alibaba-group/opensandbox)
- **Runloop — ABSENT.** Markets "ultra fast command execution at 100ms" (per command); no batch endpoint found in site/docs snippets. — [runloop.ai](https://runloop.ai/)
- **Daytona — ABSENT.** `sandbox.process.exec("echo 'Hello, World!'")` per command in README example; no batch docs found. — [daytona.io](https://www.daytona.io/)
- **Morph — ABSENT.** "command exec (including SSE)" per command (SECONDARY profile) — [api-evangelist/morph-labs](https://github.com/api-evangelist/morph-labs)
- **Blaxel, CodeSandbox, Replit — ABSENT** in reviewed docs (Blaxel per-process SDK methods; CodeSandbox `connect()`-based command run; Replit has no public sandbox exec API — it's an internal platform).

### Inferences
- A single-round-trip, N-command, stop-on-error batch exec endpoint appears genuinely uncommon-to-nonexistent among commercial providers as of Oct 2026 — a plausible differentiation candidate, though its value depends on RTT-dominated workloads (agent tool loops).
- Vendors reduce effective round trips instead via streaming output (SSE/WebSocket everywhere), session reuse (Cloudflare sessions, shell-state), and warm pools — i.e., they attack the same latency problem with different mechanisms.

### Gaps
- Negative findings are per reviewed docs/SDK surfaces; deep API references for Daytona (full REST spec), Blaxel, and Morph were not exhaustively enumerated, so a hidden batch endpoint can't be fully ruled out.

---

## Key Question 6: Per-sandbox network egress policy enforced OUTSIDE the sandbox (host-side, deny-by-default)

### Takeaway
**CONFIRMED at E2B, Cloudflare, TensorLake, and OpenSandbox** — each documents egress control that is explicit about (or clearly implies) enforcement outside the guest. E2B's is the strongest statement: firewall + BYO SOCKS5 proxy "applied on the host... so code inside the sandbox cannot see or route around it," failing closed. Daytona, Modal, Morph, Runloop, Blaxel, CodeSandbox, Replit: UNKNOWN/undocumented.

### Cited Findings
- **E2B — CONFIRMED, explicitly host-side.** Internet-access docs: "Every sandbox has outbound access to the internet by default. You can control and restrict this access... from a simple on/off switch to fine-grained allow and deny lists... Due to firewall design, blocked connections may appear successful from inside the sandbox. The firewall has to accept the connection first before it can decide whether the destination is allowed." Changelog (2026): "Sandbox.create accepts a `network.egressProxy` option that tunnels every outbound TCP connection through a SOCKS5 proxy you operate, **applied on the host after allowOut and denyOut so code inside the sandbox cannot see or route around it**. updateNetwork swaps the proxy on a running sandbox, and **egress fails closed when the proxy is unreachable**." — [Internet access docs](https://e2b.dev/docs/network/internet-access.md); [E2B changelog](https://docs.e2b.dev/changelog). Note tension: a Blaxel comparison blog (Sep 2026) lists "No dedicated egress gateway or static outbound IP support" as an E2B con — [AI agent runtime tools](https://blaxel.ai/blog/ai-agent-runtime-tools-production-workloads) — the changelog's BYO-proxy model postdates/muddies this.
- **Cloudflare — CONFIRMED, Worker/host-side.** Container start option `enableInternet: false`; "Your Worker decides who gets a sandbox, which hosts it can reach, and which credentials stay out of it"; example "Choose which hosts a sandbox can reach | Control network access | outbound-workspace"; for Dynamic Workers (JS/Python/WASM sandboxes) "blocks its outbound requests... `globalOutbound: null`"; S3Mount signs each storage request in the Worker "so the credentials never enter the sandbox." — [cloudflare/sandbox-sdk README](https://github.com/cloudflare/sandbox-sdk); [Sandbox overview](https://developers.cloudflare.com/sandbox/)
- **TensorLake — CONFIRMED (API-level; enforcement point implied host-side).** `NetworkConfig(allowInternetAccess=False, allow_out=["api.example.com"], deny_out=[...])` applied via `sandbox.update(network=...)` on a **running** sandbox, clearable with `CLEAR_NETWORK_POLICY` — runtime-mutable policy strongly implies host/proxy enforcement. Also `allow_unauthenticated_access` per exposed port. — [TensorLake SDK reference](https://docs.tensorlake.ai/sandboxes/sdk-reference)
- **OpenSandbox — CONFIRMED, explicitly guest-immutable.** "control outbound access with per-sandbox egress policies"; Fast Sandbox topology: "egress container (--network container:fastlet) — shared control plane — policy + vault per VM"; "Tamper resistance: guests have no NET_ADMIN in the pod network namespace; **enforcement rules are invisible and immutable from inside the guest**." Plus a Credential Vault injecting credentials for outbound requests without exposing real secrets. — [OpenSandbox README](https://github.com/opensandbox-group/OpenSandbox/blob/main/README.md); [Fast Sandbox Firecracker](https://open-sandbox.ai/architecture/fast-sandbox/firecracker); [OpenSandbox overview](https://open-sandbox.ai/overview/home)
- **Daytona — UNKNOWN.** No egress-policy documentation surfaced in this research.
- **Modal — UNKNOWN.** Sandbox networking restrictions not found in reviewed docs.
- **Morph, Runloop, Blaxel, CodeSandbox, Replit — UNKNOWN/ABSENT.** No egress-policy documentation found. (Blaxel's own marketing does not claim an egress gateway; its comparison criticizes others' egress instead.)

### Inferences
- The state of the art (E2B changelog + OpenSandbox) combines three properties: deny/allow domains, live mutability on a running sandbox, and guest-immutability (invisible rules, fail-closed). A self-hosted platform doing "deny-by-default until assigned, enforced outside the sandbox" matches or exceeds E2B (allow-by-default with opt-in restriction) and OpenSandbox (policy per sandbox but default posture not stated in retrieved pages).
- Cloudflare's model is the most architecturally different: egress control lives in the Worker (the app) rather than a sandbox-network layer, and Dynamic Workers can have outbound fully nulled.

### Gaps
- Default posture (deny-by-default vs allow-by-default) is only explicit for E2B ("outbound access... by default"). Daytona/Modal/Morph/Runloop/Blaxel egress stances remain unknown — searched, not found.

---

## Key Question 7: Pause/resume as first-class API with disk+memory persistence, and idle auto-pause

### Takeaway
First-class pause/resume with memory: CONFIRMED at E2B, Daytona, TensorLake, CodeSandbox (hibernate/resume), Blaxel (standby), OpenSandbox, and Modal (experimental snapshots). Idle auto-pause/auto-suspend: CONFIRMED at Daytona (auto-pause interval), Blaxel (auto-standby after ~15s network idle), E2B (auto-pause option per third party). Runloop offers paid Suspend/Resume (disk-based). Cloudflare, Modal, Replit: absent as memory-pause APIs.

### Cited Findings
- **E2B — CONFIRMED.** "Pause — a fast, secure Linux VM... which you can pause and resume as needed"; "Pausing a sandbox saves both its filesystem and its memory, and paused sandboxes are kept indefinitely"; pause/resume "resets the runtime window"; runtime caps 1h Hobby / 24h Pro. "connect()... If the sandbox is paused, it will be automatically resumed." Auto-pause: "An auto-pause option pauses idle sandboxes instead of killing them" (SECONDARY — Blaxel blog, Aug 2026 data). — [E2B docs home](https://docs.e2b.dev/); [Sandbox lifecycle](https://docs.e2b.dev/sandbox); [FAQ](https://docs.e2b.dev/faq); [persistence docs](https://github.com/joe-yeomans/e2b-docs/blob/main/docs/sandbox/persistence.mdx); [Blaxel comparison](https://blaxel.ai/blog/code-execution-sandboxes-for-ai-agents)
- **Daytona — CONFIRMED, richest lifecycle automation.** "Automated lifecycle management... user-defined deadlines": auto-stop (stops running sandbox even with internal processes), **auto-pause ("the amount of time after which an idle VM sandbox is automatically paused")**, auto-delete (stopped-time based), wall-clock TTL that "destroys the sandbox in any state: started, stopped, paused, or archived," archive state (deleted-on-archive; `sandbox.recoverable` / `recover()` API), and an explicit "update last activity" API to feed the idle clock. — [Daytona Sandboxes docs](https://www.daytona.io/docs/en/sandboxes/)
- **Blaxel — CONFIRMED (auto-standby is the default posture).** "suspends to zero when idle"; "Standby starts automatically after 15 seconds of network inactivity"; no explicit start/stop API for standby ("Blaxel does not expose a start or stop API for standby" — official cookbook); standby preserves memory+processes; archiving = long-term filesystem-only tier ("memory is not preserved; all processes... are restarted automatically"); external connections not assumed to survive standby. — [platform page](https://blaxel.ai/platform/sandboxes); [docs Overview](https://docs.blaxel.ai/Sandboxes/Overview); [cookbook lifecycle doc](https://github.com/blaxel-ai/blaxel-sandbox-code-generation-cookbook/blob/main/docs/lifecycle-and-errors.md)
- **CodeSandbox — CONFIRMED.** "Hibernation and resume with full state preservation"; hibernation triggered via SDK when session ends; resume via `sdk.sandboxes.resume(id)` restoring "file system, memory, and running processes"; `bootupType === 'CLEAN'` signals fallback to setup tasks when no memory snapshot remains. — [Core Concepts](https://codesandbox.io/docs/sdk/core-concepts); [Resuming Sandboxes](https://codesandbox.io/docs/sdk/resume)
- **TensorLake — CONFIRMED.** "suspend or resume them" in the Sandbox API; naming a sandbox "makes it non-ephemeral and enables suspend/resume." — [API v2 introduction](https://docs.tensorlake.ai/api-reference/v2/introduction); [SDK reference](https://docs.tensorlake.ai/sandboxes/sdk-reference)
- **OpenSandbox — CONFIRMED.** "Firecracker sandboxes support pause/resume with memory and disk state preserved"; Fast Sandbox 1.1.0 ships "pause/resume... in all five SDKs." — [README](https://github.com/opensandbox-group/OpenSandbox/blob/main/README.md); [release 1.1.0](https://github.com/opensandbox-group/OpenSandbox/releases/tag/release-1.1.0)
- **Modal — PARTIAL.** No pause/resume API; persistence = filesystem snapshots (Image) + Alpha memory snapshots with TTLs (7d memory / 30d fs per SECONDARY). — [sandbox snapshots](https://modal.com/docs/guide/sandbox-snapshots); [Blaxel comparison](https://blaxel.ai/blog/code-execution-sandboxes-for-ai-agents)
- **Runloop — PARTIAL (disk-level, paid).** "Snapshots: Save, suspend and resume activity for your Devboxes"; "Suspend and Resume" gated to Pro ($250/mo); snapshots disk-only. — [Runloop docs](https://docs.runloop.ai/docs/overview/what-is-runloop); [pricing](https://runloop.ai/pricing). **Critical 2026 fact: "Runloop is winding down its services. Support for existing customers continues through November 13, 2026 at 11:59 PM PT."** — [docs notice](https://docs.runloop.ai/docs/overview/what-is-runloop)
- **Morph — PARTIAL.** Snapshot + TTL + stop/start of instances; no explicit pause/resume API found in reviewed docs. — [Morph docs](https://cloud.morph.so/docs/developers)
- **Cloudflare — ABSENT.** No pause/resume; "Sandbox lifetime: Understand what keeps a Linux sandbox running, what stops it" (sleep clears state); persistence via DirectoryBackup/restore. — [Sandbox overview](https://developers.cloudflare.com/sandbox/); [API reference](https://developers.cloudflare.com/sandbox/api/)
- **Replit — ABSENT (always-on model).** Sandboxes run continuously; Agent creates checkpoints (git + CoW storage) rather than pausing VMs. — [snapshot engine](https://replit.com/blog/inside-replits-snapshot-engine); [defense in depth](https://replit.com/blog/defense-in-depth-how-replit-secures-every-layer-of-the-vibe-coding-stack) (Dec 4, 2025)

### Inferences
- Daytona's lifecycle doc is the most complete public spec of idle-driven lifecycle automation (auto-stop/auto-pause/auto-delete/TTL + activity timestamp API); it treats auto-pause as a quota/cost tool distinct from snapshot resume.
- Blaxel inverts the model: instead of pause-on-idle as an option, auto-standby is the default and the only state a sandbox rests in — with resume (25ms) much cheaper than a pause-resume cycle elsewhere (E2B ~1s resume, 4s/GiB pause).
- E2B's "paused sandboxes kept indefinitely" + runtime-window reset is a deliberate differentiator vs the 2025-era 30-day deletions that Blaxel publicly mocks ("unlike competitors that delete sandboxes after 30 days (like E2B, or Daytona)") — [Blaxel blog, Dec 12, 2025](https://blaxel.ai/blog/ai-sandbox) (note: this predates E2B's indefinite-retention change; dates matter).

### Gaps
- Whether Daytona auto-pause preserves memory (vs its separate "Memory snapshots" feature) is implied but not byte-level specified in the retrieved snippet.
- Modal idle behavior (auto-terminate vs TTL expiry) not documented in reviewed pages.

---

## Key Question 8: macOS sandbox offerings

### Takeaway
**Only one provider found: Cua** — hosted macOS (Sequoia/Tahoe) VM fleets on Apple Silicon for computer-use agents, plus local macOS VMs via Apple's Virtualization.framework. None of the 11 named providers offers macOS sandboxes; Daytona explicitly ships Linux + **Windows** VM sandboxes (the only Windows found).

### Cited Findings
- **Cua — CONFIRMED.** "MACOS: Fleets on Apple Silicon... Run Sequoia and Tahoe on Cua's patented virtualization stack, built to scale to thousands of machines"; "Cua Fleets: Elastic infrastructure that scales sandboxes into warm pools of machines"; local: "macOS sandbox environments run native macOS virtual machines on Apple Silicon using Apple's Virtualization Framework"; images `ghcr.io/trycua/macos:26` (Tahoe) and `macos:sequoia`; first create pulls 23GB, later creates "clone it and are ready in about 25 s"; also Windows 11/Server 2025 and Android VMs; cloud pricing ~$0.044625/vCPU-hr. — [cua.ai](https://cua.ai/); [macOS Sandbox docs](https://cua.ai/docs/cua/reference/desktop-sandbox/macos); [create local macOS sandbox](https://cua.ai/docs/start-here/create-a-local-macos-sandbox); [docs index](https://cua.ai/docs/llms-full.txt); cloud sandbox launch [blog (May 28, 2025)](https://cua.ai/blog/introducing-cua-cloud-containers)
- **Daytona — ABSENT for macOS, CONFIRMED for Windows.** Sandboxes docs list "Windows: Windows OS runtime in a virtual machine for running Windows applications and tooling" alongside Linux VM/container classes — the only Windows-sandbox offering found among providers. — [Daytona Sandboxes docs](https://www.daytona.io/docs/en/sandboxes/)
- **E2B, Modal, Runloop, Blaxel, TensorLake, Morph, CodeSandbox, Cloudflare, Replit, OpenSandbox — ABSENT.** No macOS sandbox offering found in any reviewed docs/marketing (E2B Desktop = Linux desktop; OpenSandbox supports gVisor/Kata/Firecracker on Linux/K8s). — respective docs pages cited above
- Historical context for Mac hosting exists (MacStadium et al. for CI), but no provider of *agent-oriented macOS sandbox APIs* other than Cua surfaced in this research.

### Inferences
- macOS agent sandboxes are effectively a one-vendor market (Cua) as of Oct 2026 — and Cua's macOS is ARM-only (Apple Silicon), local-first with cloud fleets; its guest agent (cua-spacesd) and warm fleet pools mirror the Linux providers' playbook.
- If a self-hosted platform hosts macOS VMs, it would match Cua, not the Firecracker crowd; nothing in the Firecracker ecosystem supports macOS guests.

### Gaps
- Whether Cua's cloud macOS fleets support memory-snapshot resume (vs the ~25s local clone time) is not documented in retrieved pages.
- Any 2026 entrants specifically selling macOS VM *time-sharing with snapshot restore* (beyond Cua) were not found.

---

## Notable entrants & provider-status notes (cross-cutting)

### Takeaway
Two 2025–2026 entrants materially matter: **OpenSandbox** (Alibaba-originated, now opensandbox-group, CNCF-listed, Apache-2.0 — the open-source price/performance leader with published P50/P99s) and **Cua** (YC, computer-use fleets incl. macOS). One incumbent is exiting: **Runloop winds down Nov 13, 2026**.

### Cited Findings
- **OpenSandbox**: repo created Dec 17, 2025; unified 1.1.0 release Sep 21, 2026 ("First release of the Fast Sandbox integration: a Firecracker microVM runtime behind the FastPath fleets backend, with pause/resume, ingress FastPath routing... in all five SDKs"; gRPC FastPath v2; migrates to agents.x-k8s.io/v1beta1 required by agent-sandbox v1.0.0 — i.e., builds on the Kubernetes SIG **agent-sandbox** project). Supports Docker + Kubernetes runtimes, gVisor/Kata/Firecracker secure runtimes, Credential Vault, unified ingress gateway. — [repo](https://github.com/opensandbox-group/OpenSandbox); [release 1.1.0](https://github.com/opensandbox-group/OpenSandbox/releases/tag/release-1.1.0); [overview](https://open-sandbox.ai/overview/home); [Firecracker architecture](https://open-sandbox.ai/architecture/fast-sandbox/firecracker)
- **Cua**: YC-backed, SOC 2 Type I, BYOC/on-prem available, open-core (Driver/Lume/Bench MIT; Spaces FSL); fleets priced per vCPU/GiB-hour. — [cua.ai](https://cua.ai/)
- **Runloop**: "Runloop is winding down its services. Support for existing customers continues through November 13, 2026 at 11:59 PM PT. Transition guides." — [docs](https://docs.runloop.ai/docs/overview/what-is-runloop) (accessed Oct 2026)
- **Cloudflare**: Containers + Sandbox SDK GA on Workers Paid plan April 13, 2026; Sandbox SDK 1.0 in preview (`@cloudflare/sandbox@next`), 0.x in maintenance; both microVM-based ("Each instance is a microVM with its own kernel and network"). — [changelog](https://developers.cloudflare.com/changelog/post/2026-04-13-containers-sandbox-ga); [overview](https://developers.cloudflare.com/sandbox/); [reference](https://developers.cloudflare.com/sandbox/reference)
- **E2B** positions as "The Enterprise AI Agent Cloud... Battle-tested across 1B+ sandboxes... SOC 2 Type II and HIPAA," still open-source infra (Apache, e2b-dev/infra + e2b-dev/runtime); enterprise BYOC from ~$3k/mo (SECONDARY). — [e2b.dev](https://e2b.dev/); [Blaxel comparison](https://blaxel.ai/blog/code-execution-sandboxes-for-ai-agents)
- **CodeSandbox** SDK now also distributed as "Together Code Sandbox" (SECONDARY listing, May 2026): microVMs, "~500 ms snapshot start," up to 64 vCPU — [community sandbox list gist](https://gist.github.com/wincent/2752d8d97727577050c043e4ff9e386e). Additional names from the same third-party list (not independently researched here): Beam (containers w/ GPUs), Gitpod Flex/Coder, Perplexity Sandbox API, Bunnyshell, Fly.io Machines, Vercel Sandbox (Blaxel compares against it — [Blaxel vs Vercel Sandbox](https://blaxel.ai/blog/code-execution-sandboxes-for-ai-agents)).

### Inferences
- The open-source layer (OpenSandbox, Kubernetes SIG agent-sandbox, E2B infra, Cloudflare sandbox-sdk, Cua) is converging on the same technique stack: template/golden-image + CoW rootfs + Firecracker snapshot resume + ingress/egress gateways + SDK-level warm-pool state stores. A self-hosted platform must differentiate on *combination* or *operational property* (e.g., true deny-by-default host-side egress, batch exec, or outbound-agent transport), since each individual technique is now publicly documented somewhere.
- Provider churn (Runloop's wind-down within weeks of Oct 2026) means differentiation claims should be re-verified quarterly.

### Gaps
- Fly.io Machines and Vercel Sandbox were named as comparators but not researched within this assignment's call budget; they plausibly use Firecracker snapshot resume (Fly Machines have documented auto-stop/start) and would be worth a follow-up pass.
- Together Code Sandbox's "~500ms snapshot start" is from a third-party gist; primary Together docs not fetched.
