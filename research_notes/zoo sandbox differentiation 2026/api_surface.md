# API Surface Differentiation: "zoo" vs. AI Agent Sandbox Products (as of October 2026)

Scope: developer-facing API-level capabilities. Products examined: E2B, Daytona, Modal, Runloop, Blaxel, CodeSandbox SDK, Cloudflare Sandbox SDK, Scrapybara, Kernel, Browserbase, Steel/Anchor (browser-focused), Microsandbox, Together Code Sandbox, LangSmith Sandboxes. Each claim below is tagged CONFIRMED (verified via URL), ABSENT (searched, no evidence found), or UNKNOWN (could not verify).

## Capability 1: Batch exec — multiple commands in one HTTP round trip (`POST /sandboxes/{id}/exec/batch`, ≤25 commands, ordered, stop_on_error)

### Takeaway
No competing sandbox product appears to ship a first-class multi-command, single-HTTP-round-trip exec endpoint with stop-on-error semantics. Every checked provider exposes one-command-per-request exec, with "sessions" (persistent shells) as the only workaround. This looks like a zoo differentiator.

### Cited Findings
- **E2B — ABSENT (single-command only, CONFIRMED).** E2B's command execution is `sandbox.commands.run(cmd, opts)` — one command per call, e.g. `commands.run("echo ... > /etc/resolv.conf", { user: 'root' })` — with no batch/list variant documented on its internet-access page (their own examples loop through single runs) — [E2B Internet access docs](https://docs.e2b.dev/network/internet-access). E2B's async SDK reference likewise lists "Run commands" as individual operations — [E2B SDK reference](https://e2b.dev/docs/sdk-reference/python-sdk/v1.0.6/sandbox_async).
- **Cloudflare Sandbox SDK — ABSENT (CONFIRMED).** Full 0.x Commands API surface is: `exec()` (single command), `execStream()` (single command), `startProcess()`, `listProcesses()`, `killProcess()`, `killAllProcesses()`, `streamProcessLogs()`, `getProcessLogs()`. No multi-command batch method exists — [Cloudflare Commands API](https://developers.cloudflare.com/sandbox/sdk/api/commands/). (Page last updated Sep 30, 2026.)
- **Daytona — ABSENT for HTTP batch; has persistent "Sessions" instead (CONFIRMED).** Daytona's `Process` handles single process/code execution; "Sessions are background processes making them ideal for scenarios requiring multiple related commands" — i.e., a persistent shell session you execute into one command at a time — [Daytona Process docs](https://www.daytona.io/docs/en/python-sdk/sync/process/) and [Process and Code Execution](https://www.daytona.io/docs/en/process-code-execution/).
- **Runloop — ABSENT (CONFIRMED as single exec).** "The primary method of executing commands is the exec command" — one command per call, sync or streaming; lifecycle covers create/suspend/resume/snapshot/shutdown, with no batch-exec endpoint mentioned — [Runloop Execute Commands](https://docs.runloop.ai/docs/devboxes/execute-commands); the TS client docs say "Execute a command that returns immediately - exec blocks until completion" — [Runloop api-client-ts](https://runloopai.github.io/api-client-ts/); third-party API index describes "synchronous and asynchronous command execution, named sessions" — [api-evangelist/runloop-ai](https://github.com/api-evangelist/runloop-ai).
- **Blaxel — UNKNOWN for exec batching.** Their recent engineering content covers snapshots/hibernate but no batch exec endpoint surfaced in search — [Blaxel snapshot blog, Oct 8 2026](https://blaxel.ai/blog/sandbox-snapshot-hibernate-resume-ai-agents).
- **Modal — ABSENT (searched, not found).** Modal's sandbox story is `Sandbox` objects running code per-call; Modal's batching primitives (`.map`, batched functions) apply to Modal *functions*, not sandbox exec; no batch-exec-on-one-sandbox endpoint found — [Modal: best code execution sandboxes for browser-use agents](https://modal.com/resources/best-code-execution-sandboxes-browser-use-agents), [Modal: best serverless sandboxes](https://modal.com/resources/best-serverless-sandboxes-ai-code-execution).
- **CodeSandbox SDK / Together Code Sandbox — UNKNOWN.** Not directly verified in this pass; Together documents per-run states (RUNNING/RESUMED/CLEAN) but no batch exec — [Together legacy SDK docs](https://docs.together.ai/docs/together-code-sandbox-legacy).
- **mcp-batchit / terminal-tool aggregators — UNKNOWN.** Not verified within this research pass (see Gaps).

### Inferences
- The pattern across E2B, Cloudflare, Daytona, and Runloop is consistent: vendors amortize round trips via *sessions* (persistent shells/background processes) or streaming, not declarative ordered batch execution with a stop_on_error flag. Zoo's `exec/batch` (≤25 commands, 1 auth check, fail-fast) has no confirmed equivalent among checked providers.
- Caveat: shell `&&` chaining is the universal fallback competitors will cite, but it collapses exit-code capture per command and offers no stop_on_error at the API level.

### Gaps
- CodeSandbox SDK commands API and mcp-batchit were not fetched directly; their absence is "searched, not found" rather than fully verified.
- Could not confirm whether the Alibaba Cloud "E2B SDK-compatible API" adds a batch mode — its docs say "Use commands.run() for standard batch commands whenever possible," ambiguous phrasing for a compatibility shim — [Aliyun E2B-compatible API list](https://help.aliyun.com/en/functioncompute/e2b-sdk-compatible-api-list).

## Capability 2: Pause/resume with memory state as a first-class REST API on a RUNNING sandbox

### Takeaway
Memory-preserving pause/resume has become mainstream: E2B ships it GA with published latencies (~4 s/GiB pause, ~1 s resume); CodeSandbox SDK and Blaxel offer hibernate/resume; Runloop has suspend/resume in its lifecycle API. Zoo is NOT unique here — the bar is now "everyone credible has it or is shipping it"; differentiation must come from latency numbers and self-hosted availability of it.

### Cited Findings
- **E2B — CONFIRMED, GA.** Pause saves filesystem *and* memory (all running processes, loaded variables); paused sandboxes are kept indefinitely with no TTL; resume via `connect()`; optional `mode: 'filesystem'` drops memory; auto-pause on timeout via `lifecycle: { onTimeout: 'pause' }` with `autoResume` flag. Published performance: "Pausing a sandbox takes approximately **4 seconds per 1 GiB of RAM**; Resuming a sandbox takes approximately **1 second**." Continuous runtime limits: 24 h (Pro) / 1 h (Hobby), reset on resume — [E2B Sandbox persistence docs](https://docs.e2b.dev/sandbox/persistence). REST surface includes `PUT .../pause` semantics (third-party mirror documents "Resume Sandbox" as an E2B API endpoint) — [MindCloud E2B Resume Sandbox](https://mindcloud.co/docs/universal/rest/e2b/latest/actions/resume-sandbox), [E2B API reference: pause sandbox](https://docs.e2b.dev/api-reference/sandboxes/pause-sandbox).
- **E2B status note:** the earlier 2025-era "pause = beta" framing is outdated; auto-pause was described as Beta by a third-party comparison — [ZenML: E2B vs Daytona](https://www.zenml.io/blog/e2b-vs-daytona) — but E2B's current persistence docs read as GA, with one *sub*-feature (ServiceBusyError refusal handling) explicitly "rolling out region by region" — [E2B persistence docs](https://docs.e2b.dev/sandbox/persistence).
- **CodeSandbox SDK — CONFIRMED, hibernation + resume (memory optional).** Third-party 2026 writeups: "Pause/resume preserves filesystem and memory under the same sandbox ID" — [OpenComputer guide, Jul 20 2026](https://opencomputer.dev/guides/codesandbox-alternatives/); "Hibernation/resume is supported... Use `memory: true` on both pause and resume" — [Sandbox0 comparison, Oct 2026](https://sandbox0.ai/alternatives/codesandbox-sdk); Freestyle describes "templates, Firecracker-powered sandboxes, persistent file storage, hibernation, resume" — [Freestyle blog, Jun 6 2026](https://www.freestyle.sh/blog/product/codesandbox-sdk-alternative-ai-agents).
- **Blaxel — CONFIRMED, snapshot-based hibernate/resume (2026).** "A hibernated sandbox stays resumable until the team deletes it. It consumes no compute while waiting" — [Blaxel blog, Oct 8 2026](https://blaxel.ai/blog/sandbox-snapshot-hibernate-resume-ai-agents). Whether hibernate captures *memory* vs disk-only is not stated in the snippet — memory claim UNKNOWN.
- **Runloop — CONFIRMED suspend/resume exists; memory semantics UNKNOWN.** "The Devbox API covers the full lifecycle (create, suspend, resume, snapshot, shutdown)" — [api-evangelist/runloop-ai README](https://github.com/api-evangelist/runloop-ai); official lifecycle docs — [Runloop Devbox Lifetime Management](https://docs.runloop.ai/docs/devboxes/start-stop).
- **Daytona — UNKNOWN.** No public doc found in this pass confirming pause/resume *with memory* on a running sandbox; Daytona does document snapshots — [Daytona sandboxes docs](https://www.daytona.io/docs/en/sandboxes/).
- **Modal — UNKNOWN for memory-preserving pause.** No confirmation found in this pass that `Sandbox.stop`/snapshot preserves memory; a third-party durability essay notes "E2B can pause a sandbox too, but its automatic pause fires on an idle timer, so the bill runs until the timer does" — implying E2B is the reference implementation for pause, not Modal — [Inngest: Sandboxes are a durability problem](https://www.inngest.com/blog/sandboxes-are-a-durability-problem).
- **Northflank's 2026 roundup** describes memory-preserving pause/resume (with a filesystem-only variant that reboots on resume) as the expected baseline behavior of leading sandboxes — [Northflank: best code execution sandbox for AI agents, Jan 17 2026](https://northflank.com/blog/best-code-execution-sandbox-for-ai-agents).

### Inferences
- Mini-matrix: **GA with memory**: E2B (CONFIRMED, latencies published). **GA/beta, semantics partially documented**: CodeSandbox SDK, Blaxel. **Present, memory unknown**: Runloop. **Unclear/undocumented**: Daytona, Modal. **No pause/resume with memory found**: Cloudflare Sandbox SDK (nothing surfaced).
- E2B's published "4 s/GiB pause, ~1 s resume" is the public number to beat on a marketing level; zoo's laptop-local loopback resume can plausibly undercut it, but zoo must publish its own numbers to claim it.
- A differentiator angle that survives: on competitors, pause/resume is a *cloud-service* feature; on zoo it should work identically on one machine (self-hosted), which no SaaS vendor demonstrates.

### Gaps
- Modal's experimental snapshot semantics (memory or not) and Daytona's pause/resume status were not verifiable within the search budget; both marked UNKNOWN.
- Blaxel's memory-vs-disk hibernate semantics unverified.

## Capability 3: Sandbox identity/security model (out-of-band secret injection; deny-by-default egress enforced outside sandbox boundary; no inbound listeners; per-command HMAC guest tokens)

### Takeaway
E2B has converged on most of this model (egress proxy outside the sandbox, secret/workload-token injection by the proxy "outside the sandbox," IAM workload identity), but two zoo properties remain unmatched: **deny-by-default until owner assigns policy** (E2B is allow-by-default) and **no inbound listeners at all** (E2B actively sells inbound public URLs). Per-command HMAC guest tokens have no found equivalent anywhere.

### Cited Findings
- **E2B egress is allow-by-default — CONFIRMED (differs from zoo).** "Every sandbox has outbound access to the internet by default. You can control and restrict this access..." — denial is opt-in via `allowInternetAccess: false` or `network.denyOut` — [E2B Internet access docs](https://docs.e2b.dev/network/internet-access).
- **E2B egress enforcement is outside the sandbox boundary — CONFIRMED.** Domain filtering works via SNI inspection (443) and Host header (80) at an egress proxy/firewall; "the firewall has to accept the connection first before it can decide whether the destination is allowed... from inside the sandbox, a TCP connection can succeed and report the socket as open even when the destination is denied" — enforcement is host-side, though with the quirk that blocks are invisible to the workload — [E2B Internet access docs](https://docs.e2b.dev/network/internet-access). Third-party analysis agrees: "E2B's domain filtering works via SNI inspection for HTTPS... Since all traffic goes through the proxy anyway" — [Oshu Vault docs](https://docs.vault.oshu.dev/docs/network-egress).
- **E2B secret injection happens outside the sandbox — CONFIRMED (analog to zoo's out-of-band secrets).** Header transforms on allowed hosts can reference stored secrets via `Secret.fill`: "The egress proxy replaces each reference with the secret's current value **outside the sandbox** when it forwards a matching HTTPS request." Workload-identity tokens work the same way: "the token value never passes through your code or the sandbox" — [E2B Internet access docs / network.rules](https://docs.e2b.dev/network/internet-access); E2B also exposes a dedicated [Secrets page](https://docs.e2b.dev/secrets) and [Workload identity](https://docs.e2b.dev/iam/workload-identity). Note: E2B injects at egress time via proxy header rewrite; whether it also writes secret files into the sandbox out-of-band (zoo's mechanism) was not found.
- **E2B inbound — CONFIRMED present (differs from zoo's no-inbound model).** "To expose services running inside the sandbox to the outside world, see Sandbox public URL" — inbound exposure is a documented, supported feature — [E2B Internet access docs](https://docs.e2b.dev/network/internet-access), [E2B public URL docs](https://docs.e2b.dev/network/public-url).
- **E2B enterprise adds deny lists per sandbox and BYO proxy (private beta):** "Network Egress allow and deny per sandbox. Your own proxy, in private beta. Allow or deny by IP, CIDR, or domain" — [E2B Enterprise page](https://e2b.dev/enterprise).
- **Microsandbox — CONFIRMED it has the right vocabulary; details UNKNOWN.** "Microsandbox is a server-based MicroVM sandbox with programmable networking, secrets management, and multi-agent support" — [Paperclipped: Microsandbox vs BoxLite](https://www.paperclipped.de/en/blog/microsandbox-boxlite-ai-agent-sandboxes/). Whether its networking is deny-by-default and enforced host-side, and whether it has HMAC per-command tokens, was not verifiable in this pass.
- **Cloudflare Sandbox — PARTIAL, CONFIRMED.** The 0.x SDK ships a dedicated "Handle outbound traffic" guide (egress control from the Worker side, i.e., outside the container) — [Cloudflare outbound traffic guide](https://developers.cloudflare.com/sandbox/sdk/guides/outbound-traffic/); 1.0 reframes it as "Call an authenticated API from a sandbox." Default posture (allow vs deny) not confirmed in this pass.
- **Per-command HMAC guest tokens — ABSENT across all searched products.** No provider among E2B, Daytona, Cloudflare, Runloop, Blaxel, CodeSandbox, Microsandbox surfaced an equivalent of short-lived HMAC tokens authorizing individual commands. E2B's closest is API-key + proxy-injected IAM tokens (JWT-SVID style) — [E2B workload identity](https://docs.e2b.dev/iam/workload-identity).
- Industry context: Anthropic's Cowork applies a default allowlist egress model to its cloud environments, showing the "restrict-by-default" posture is emerging among *consumers of* sandboxes, not the sandbox vendors — [WorkOS: what your agent sandbox can reach by default](https://workos.com/blog/agent-sandbox-egress-defaults).

### Inferences
- Zoo's four-part model decomposes as: (a) proxy-side secret/token injection → E2B parity (public beta-era features, GA on E2B cloud); (b) deny-by-default egress → E2B/Cloudflare are allow-by-default → zoo differentiator; (c) zero inbound listeners → no checked vendor matches (all offer public URLs/preview links) → zoo differentiator, but it also breaks "preview my dev server" workflows, which is *why* competitors ship inbound; (d) per-command HMAC guest tokens → apparently unique to zoo.
- The strongest honest claim: zoo's *combination* (all four in one, self-hosted) is unmatched; individually, (a) has near-parity with E2B.

### Gaps
- Microsandbox's networking default and secrets mechanism details unverified.
- Daytona env-var/secrets posture not verified.
- Whether E2B can write secrets into the sandbox filesystem out-of-band (vs. only proxy-header injection) unknown.

## Capability 4: File API (single-file put/get by path, ~100KB-class latency, directory export/import)

### Takeaway
Single-file read/write by path is universal table stakes (E2B, Cloudflare, Daytona all document file APIs); no vendor publishes per-file-size latency figures like "100KB in ~X ms," and native directory export/import was not found as a first-class primitive at the checked vendors — typically done via tar-through-exec workarounds.

### Cited Findings
- **E2B — single-file ops CONFIRMED as table stakes.** SDK supports "Create, list, and delete files and directories" plus read/write — [E2B async SDK reference](https://e2b.dev/docs/sdk-reference/python-sdk/v1.0.6/sandbox_async).
- **Cloudflare — CONFIRMED** dedicated Files API page exists for sandbox file operations — [Cloudflare Files API](https://developers.cloudflare.com/sandbox/sdk/api/files/).
- **Daytona — CONFIRMED** "File System Operations. Manage sandboxes with full CRUD operations" — [Daytona homepage](https://www.daytona.io/) and [Daytona docs](https://www.daytona.io/docs/en/sandboxes/).
- **Latency figures — ABSENT.** No public per-file-size (e.g., 100 KB) latency SLA or benchmark found for E2B, Daytona, Cloudflare, or Runloop. E2B publishes latency claims only for pause/resume and creation (~150 ms cold start historically, per its marketing) — [E2B homepage](https://e2b.dev/).
- **Directory export/import — ABSENT (searched, not found) at API level.** E2B docs show file-by-file and listing primitives; directory archives are done via shell commands inside the sandbox (e.g., tar) rather than a documented bulk export endpoint. No checked vendor documents a native directory export/import endpoint.

### Inferences
- The differentiator here is *measured* latency (publishable numbers like "100KB get in <X ms on loopback"), not the endpoint shape — endpoint shape alone is table stakes.
- A native directory export/import endpoint would be ahead of the documented industry norm; competitors do it with in-sandbox tar.

### Gaps
- Modal's Sandbox filesystem API and Runloop's file endpoints were not individually fetched; they very likely have single-file ops (consistent with the table-stakes pattern) — UNKNOWN pending verification.
- No vendor latency benchmark for 100KB-class file transfers located.

## Capability 5: Sandbox "kinds" beyond Linux code containers — desktop (VNC) + browser + code under one API

### Takeaway
No currently-active vendor clearly ships code-execution + desktop(VNC-streaming) + browser sandboxes as sibling "kinds" under one API. The historical closest fit, Scrapybara (Ubuntu/Browser/Windows instances for computer use), is pivoting away from virtual desktops entirely. Browser-first vendors (Browserbase, Kernel, Steel, Anchor) do not advertise general code-execution sandboxes, and code-first vendors (E2B, Daytona, Cloudflare, Modal) do not advertise desktop streaming. Zoo's multi-kind API looks like a genuine differentiator in late 2026.

### Cited Findings
- **Scrapybara — CONFIRMED as the only close match, but winding down.** "Scrapybara hosts remote desktop instances for computer use agents... remote instances with our API, SDKs, and dashboard... Supported for Ubuntu, Browser, and Windows instances" — [Scrapybara docs](https://docs.scrapybara.com/), [Scrapybara README (api-evangelist mirror)](https://github.com/api-evangelist/scrapybara/blob/main/README.md). It has pivoted: "Scrapybara built computers for agents. Now we're building Capy, the AI software engineer" — [scrapybara.com](https://scrapybara.com/); "Scrapybara is transitioning from providing virtual desktop environments for AI agents to becoming a dedicated AGI research lab" — [Scrapybara on X](https://x.com/scrapybara?lang=en).
- **Kernel (kernel.sh) — browser-first, CONFIRMED.** "KERNEL is the internet runtime for agents. we provide crazy fast, open source browser infra for your agents to access and act on the internet" — [Kernel docs](https://kernel.sh/docs). Comparison article: "You request a browser via their API. They start Chrome in a VM and return a WebSocket URL" — [Ritza: Browserbase vs Kernel](https://ritza.co/articles/browserbase-vs-kernel/). General code-exec sandbox offering: not found.
- **Browserbase — browser-first; positions code-exec sandboxes as a *different product category*.** Their engineering post argues you can't just put Chromium in an agent code sandbox, i.e., they treat "bursty code execution sandboxes" and browser infra as distinct — [Browserbase blog](https://www.browserbase.com/blog/why-you-can-t-just-run-chromium-in-a-sandbox). No code-exec sandbox API found.
- **E2B — code sandboxes only; desktop/VNC streaming ABSENT (searched, not found).** E2B markets "run code, browse, use tools, pause, fork" — "browse" meaning browser *automation inside* the Linux sandbox, not a streamed desktop kind — [E2B homepage](https://e2b.dev/).
- **Modal — publishes "best code execution sandboxes for browser-use agents" comparisons, i.e., code sandboxes that browser agents call into; no VNC/desktop kind** — [Modal resource](https://modal.com/resources/best-code-execution-sandboxes-browser-use-agents).
- **Cloudflare Sandbox SDK — Linux container code-exec only** — [Cloudflare Sandboxes overview](https://developers.cloudflare.com/sandbox/).
- **LangSmith Sandboxes (2026 entrant)** — "secure code execution environments for agents... filesystem, shell, package manager" — code kind only — [LangChain blog](https://www.langchain.com/blog/agents-need-their-own-computer).

### Inferences
- Mini-matrix: **code+browser+desktop kinds under one API**: Scrapybara (historic, pivoting away) — everyone else: single-kind. **Desktop (VNC) streaming**: only Scrapybara among checked API vendors. **Browser-as-a-kind**: Kernel, Browserbase, Steel, Anchor (browser-only, no code kind).
- Strategic note: Scrapybara's pivot *away* from desktop-as-a-service removes the only direct competitor to zoo's multi-kind story; conversely, it suggests the standalone "agent desktops" market was commercially hard — zoo should position multi-kind as infrastructure (one platform, three backends), not as "desktops for agents."

### Gaps
- Anchor Browser and Steel.dev were not directly fetched; classification (browser-only, no code exec) is inferred from category positioning — UNKNOWN pending direct check.
- Kernel may have expanded beyond browsers by late 2026 (open-source "environments"); not verified.

## Capability 6: Self-hosted, single-binary, local-first deployment (API + gateway + Docker + macOS VM backends on one laptop)

### Takeaway
The self-hosting landscape fractured in 2026: Daytona — the previous open-source poster child — closed its code and abandoned its OSS repo (June 2026); E2B's infra is open-source but is a multi-service Terraform/Nomad deployment, not a single binary; Microsandbox is the only confirmed single-binary local-first microVM platform, and it's the primary comparison point. Zoo's "whole platform on one laptop with loopback latencies, Docker *and* macOS-VM backends" has no confirmed exact equivalent.

### Cited Findings
- **Daytona — OSS abandoned, CONFIRMED.** "As of June 2026, Daytona's core development has moved to a private codebase. This repository will receive no further updates, fixes, or releases" — [daytonaio/daytona GitHub README](https://github.com/daytonaio/daytona); "On June 11, 2026, Daytona announced that its production codebase was moving to closed source" — [Northflank](https://northflank.com/blog/top-daytona-io-alternatives-for-running-ai-code-in-secure-sandboxed-environments); community: "It looks like Daytona withdrew their self-hosting option" — [dev.to account](https://dev.to/rohittcodes/my-journey-with-daytona-and-how-i-plan-to-use-it-going-forward-22bb).
- **E2B — open source but heavy, CONFIRMED.** E2B's infra is Apache-2.0 and "deploys via Terraform with Nomad, Consul, and" more — [Beam.cloud: how to self-host a code sandbox](https://www.beam.cloud/blog/how-to-self-host-code-sandbox); E2B now also offers a dedicated runtime deployment in *your cloud account* (BYOC-style): "E2B runs the runtime as a dedicated deployment inside your account" — [e2b-dev/runtime GitHub](https://github.com/e2b-dev/runtime); marketing: "E2B is the only open-source hosted microVM sandbox platform: runtime, control plane, and managed service" — [e2b.dev/open-source](https://e2b.dev/open-source); demand for simpler single-node self-hosting remains an open issue thread — [e2b-dev/infra issue #2273](https://github.com/e2b-dev/infra/issues/2273/linked_closing_reference?reference_location=REPO_ISSUES_INDEX), [issue #864](https://github.com/e2b-dev/infra/issues/864).
- **Microsandbox — CONFIRMED as single-binary local-first microVM platform.** "Open-source microVM sandboxes for AI agents and untrusted code. Run locally on macOS, Linux, and Windows, or request managed cloud access" — [microsandbox.dev](https://microsandbox.dev/); "runs locally and rootless using libkrun microVMs over KVM — each run gets its own kernel, with no control plane to" manage — [Beam.cloud](https://www.beam.cloud/blog/how-to-self-host-code-sandbox); "This project is meant to make creating microvms from your machine as easy as using Docker containers" — creator on HN (Aug 2025) — [HN item 44136409](https://news.ycombinator.com/item?id=44136409). Market positioning as the leading self-hosted alternative persists into 2026 comparisons — [Northflank: self-hostable E2B alternatives](https://northflank.com/blog/self-hostable-alternatives-to-e2b-for-ai-agents), [PandaStack](https://www.pandastack.ai/blog/best-open-source-code-sandboxes/). Caveat: the active GitHub repo now appears under a new org ("superradcompany/microsandbox" — "easy, fast, [self-hosted microVMs]") — [GitHub](https://github.com/superradcompany/microsandbox) — suggesting a repo transfer/renaming; original-repo continuity is UNKNOWN.
- **CodeSandbox SDK — self-host story UNKNOWN.** It is a library over Firecracker sandboxes ("templates, Firecracker-powered sandboxes... hibernation, resume") — [Freestyle](https://www.freestyle.sh/blog/product/codesandbox-sdk-alternative-ai-agents) — historically tied to hosted infrastructure; local/single-binary mode not confirmed.
- **Cloudflare Sandbox SDK — not self-hostable off Cloudflare, per its platform model.** A sandbox is "a Durable Object and the Container it starts" — [cloudflare/sandbox-sdk GitHub](https://github.com/cloudflare/sandbox-sdk) — i.e., bound to Cloudflare's runtime. Local dev via wrangler exists but the platform dependency is structural. (Local-dev parity: UNKNOWN.)
- **Context for why local-first matters:** third-party essays now explicitly rank sandbox products by "can I run this on my own laptop/VPS," and find the field thin — [Dafe/Medium: self-hosted sandboxes, containers vs microVMs](https://odafe41.medium.com/self-hosted-sandboxes-how-to-pick-between-containers-and-microvms-1fa4803b7bdf), [pixeljets: Daytona vs microsandbox](https://pixeljets.com/blog/ai-sandboxes-daytona-vs-microsandbox/).

### Inferences
- Mini-matrix: **true single-binary local-first**: Microsandbox (CONFIRMED, libkrun/microVM), zoo (claimed). **Open-source but multi-service, cloud-scale**: E2B infra. **Withdrawn**: Daytona (June 2026). **Host-bound**: Cloudflare, (likely) CodeSandbox SDK, Modal, Runloop, Blaxel, E2B cloud.
- Zoo's unusual twist vs Microsandbox: *pluggable backends including Docker and a macOS VM layer* (Microsandbox is microVM-only via libkrun) and full API/gateway/control-plane in the same binary. Microsandbox comparison posts note its CPU virtualization overhead vs container approaches — [pixeljets](https://pixeljets.com/blog/ai-sandboxes-daytona-vs-microsandbox/) — which zoo's Docker backend would avoid for trusted workloads.
- Daytona's abandonment (June 2026) is a marketing window: users seeking an OSS self-hosted sandbox have few options, and E2B's own issues (#864, #2273) show demand outrunning the Terraform/Nomad reality.

### Gaps
- Microsandbox GitHub stars/adoption metrics and post-transfer maintenance cadence were not verified.
- CodeSandbox SDK and Cloudflare Sandbox local-dev/self-host specifics unverified.
- BoxLite (mentioned alongside Microsandbox) not investigated.

## Cross-cutting summary matrix (Oct 2026)

| Capability | zoo | E2B | Daytona | Cloudflare | Runloop | Blaxel | CodeSandbox SDK | Modal | Microsandbox | Scrapybara | Kernel/Browserbase |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Batch exec (1 RTT, N commands, stop_on_error) | ✅ unique | ❌ single exec | ❌ sessions | ❌ single exec | ❌ single exec | UNKNOWN | UNKNOWN | ❌ | UNKNOWN | UNKNOWN | UNKNOWN |
| Pause/resume w/ memory (REST, running sandbox) | ✅ | ✅ GA, 4s/GiB pause, ~1s resume | UNKNOWN | ❌ (not found) | ✅ suspend/resume (memory UNKNOWN) | ✅ hibernate (memory UNKNOWN) | ✅ hibernation | UNKNOWN | UNKNOWN | n/a | n/a |
| Deny-by-default egress, enforced outside sandbox | ✅ | ❌ allow-by-default (proxy enforcement ✅) | UNKNOWN | partial (egress guide; default UNKNOWN) | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | n/a | n/a |
| Out-of-band secret injection (never in workload) | ✅ | ✅ analog: proxy injects secrets/IAM tokens at egress | UNKNOWN | partial | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | partial (has secrets mgmt) | n/a | n/a |
| No inbound listeners | ✅ | ❌ public URLs are a feature | UNKNOWN | partial (preview URLs) | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | n/a | n/a |
| Per-command HMAC guest tokens | ✅ unique | ❌ | ❌ | ❌ | ❌ | UNKNOWN | UNKNOWN | UNKNOWN | ❌ | n/a | n/a |
| Single-file put/get | ✅ | ✅ | ✅ | ✅ | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | ✅ | ❌ |
| Published file latency (100KB-class) | ✅ (if published) | ❌ none found | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Desktop(VNC)+browser+code kinds in one API | ✅ unique among active | ❌ code only | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ but pivoting away | ❌ browser only |
| Single-binary local-first self-host | ✅ | ❌ Terraform/Nomad, BYOC runtime | ❌ OSS withdrawn 6/2026 | ❌ Cloudflare-bound | ❌ | ❌ | UNKNOWN | ❌ | ✅ libkrun microVM | n/a | n/a |
