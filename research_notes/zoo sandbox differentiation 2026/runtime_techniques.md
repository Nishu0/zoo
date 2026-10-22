# Runtime techniques behind AI-agent sandboxes — survey as of October 2026

Research performed 2026-10-22. **Method note:** the session's web-search tooling was non-functional (all `websearch` calls returned empty; DuckDuckGo/Bing HTML search blocked). All findings below come from direct HTTP fetches of primary sources (GitHub raw/API endpoints, vendor doc pages) on 2026-10-22, unless explicitly marked otherwise. Every URL below was fetched successfully on 2026-10-22 unless tagged "(not fetched this session)". Coverage therefore skews toward primary docs of known vendors and may miss startup announcements that only circulate via blogs/news.

Notation: **[PROD]** = production system with public docs; **[OSS]** = open-source, runnable today; **[BETA/EXP]** = beta or experimental; **[RESEARCH]** = academic paper/lab work.

---

## Q1: Warm pools where each pooled container is booted ALREADY carrying its final sandbox identity (name, home volume, guest credentials pre-provisioned at pool-boot); a create just claims it and hands over secrets/network policy in parallel

### Takeaway
Pre-warmed capacity is now table stakes — OpenSandbox, E2B, AWS all restore/claim from pre-warmed pools — but the surveyed systems pre-provision only *capacity and network slots* at pool time; per-sandbox identity (name, home volume, guest credentials) is applied at or after admission. Zoo's "identity-at-pool-boot" model was not found in any public production or open-source system surveyed; the closest verified analogs are OpenSandbox's pre-provisioned per-sandbox network slots ("the attachment identity plus policy bindings ship with the admission") and its SDK-side pools of fully-prepared sandboxes.

### Cited Findings
- **[PROD] Alibaba OpenSandbox / fast-sandbox** schedules sandboxes into pools of pre-warmed "Fastlet" pods: "creating a sandbox is a constant-time admission into capacity that already exists, instead of a fresh scheduling round" — [scheduling.md](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/architecture/fast-sandbox/scheduling.md) (fetched 2026-10-22)
- **OpenSandbox pre-provisions the network identity slot at pool time, but not the sandbox identity**: "The **network slot exists before the runtime**: netns, veth, and masquerading are pre-provisioned per sandbox, and the attachment identity plus policy bindings ship with the admission. A sandbox is never reachable before its policy is." The sandbox entrypoint "is delivered as a task to the sandbox rather than baked into the pod, which is what allows a pre-warmed Fastlet to serve arbitrary templates." — [scheduling.md](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/architecture/fast-sandbox/scheduling.md)
- **OpenSandbox client-side pools (OSEP-0005, 2025-2026 era)**: an SDK-local `SandboxPool` acquires "ready sandboxes with predictable latency"; pooled sandboxes are "created through standard lifecycle create APIs" and held in an idle buffer; **OSEP-0021** describes an async warmup pipeline of create → health check → prepare → post-prepare validation → renew → idle commit — i.e., pooled sandboxes are fully prepared *before* acquire, but the pool lives in the client SDK and each slot is a whole dedicated sandbox, not an identity-bearing slot inside a pooled container — [OSEP-0005](https://github.com/opensandbox-group/OpenSandbox/blob/main/oseps/0005-client-side-sandbox-pool.md), [OSEP-0021](https://github.com/opensandbox-group/OpenSandbox/blob/main/oseps/0021-scalable-asynchronous-client-side-pool-warmup.md)
- **[PROD] E2B** does not pool pre-booted sandboxes per identity; instead a sandbox "is a **resumed snapshot**. Templates are pre-booted VM snapshots (memory + disk + VM state) stored in object storage. 'Creating' a sandbox means restoring a snapshot" — identity/secrets are bound after resume via control plane — [E2B ARCHITECTURE.md](https://github.com/e2b-dev/infra/blob/main/docs/ARCHITECTURE.md)
- **[PROD] AWS EC2 Auto Scaling warm pools** (long-standing, generic compute, not sandboxes): keeps pre-initialized instances to "decrease latency for applications with long boot times," with optional hibernation; page title/canonical verified — [AWS docs](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html). Warm-pool instances are generic capacity; workload identity/credentials are applied when pulled into service (body text beyond title not retrievable in this session — see Gaps).
- **[PROD] Wasmer SDK** benchmark measures "Sandbox.create()" cold-start at p50 25.05 ms — it creates sandboxes on demand rather than claiming from a pool — [RESULTS-2026-09-01.md](https://github.com/wasmerio/wasmer-sdk/blob/main/benchmarks/sandboxes/RESULTS-2026-09-01.md)

### Inferences
- Three distinct pool designs exist in production: (a) snapshot-resume pools (E2B: restore frozen VM image on demand), (b) capacity pools with pre-provisioned network slots (OpenSandbox fast-sandbox), (c) client-side pools of fully-created sandboxes (OpenSandbox SDK, conceptually also GitHub-Actions-style warm runner pools). Zoo's design — pooled containers booted already carrying final name/home-volume/guest-credentials, with only secrets + network policy handed over at claim — matches none of the verified public designs and appears **novel or at least undocumented publicly**.
- Zoo's "hand over secrets and network policy in parallel" claim maps almost exactly onto OpenSandbox's stated invariant "policy delivery can be late, never early-open" (fail-closed egress until the subject's data plane registers) — the *pattern* of deferring policy/secret binding to claim time while keeping create O(1) is confirmed as the state of the art direction — [fast-sandbox index.md](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/architecture/fast-sandbox/index.md)

### Gaps
- No public docs found (in this session) for Morph, Daytona, Hopx, AgentSphere, Blaxel or other 2025-2026 sandbox startups' pool designs; their use of identity-at-pool-time could not be checked.
- AWS warm-pools body text (hibernation details) sits deeper in the page than fetch slices reached; the title/canonical verified but details are background knowledge, unverified here.
- RunsOn (self-hosted GitHub Actions runners) advertises warm runners but its README greps surfaced only scale stats ("24.7 fresh runners per second sustained") — warm-pool mechanics unverified — [runs-on README](https://github.com/runs-on/runs-on)

---

## Q2: Agent dials OUT over a persistent websocket to a gateway; all exec/terminal/file traffic rides one connection; sandbox keeps no inbound control listeners

### Takeaway
The AI-sandbox runtimes surveyed (E2B, OpenSandbox, Modal, Wasmer-remote) all use **inbound** control paths (proxy/gateway → in-VM daemon), so zoo's dial-out architecture is differentiated within the AI-sandbox category — though it mirrors the long-standing CI-runner/dev-workspace agent pattern (self-hosted runners dial out), which I could not re-verify with primary docs in this session.

### Cited Findings
- **[OSS] E2B**: control/exec traffic goes client → edge routing → node proxy (port 5007) → **envd (the in-VM agent) on port 49983**; i.e., the control plane connects *into* the VM — [ARCHITECTURE.md](https://github.com/e2b-dev/infra/blob/main/docs/ARCHITECTURE.md) ("the in-VM agent"; "orchestrator gRPC :5008, proxy :5007"; "ENVD :49983")
- **[PROD] OpenSandbox**: sandboxes are reached through a gateway ("Warm SDK `/ping` through the gateway … p50 0.92 ms") with per-VM veth/gateway inside the Fastlet pod netns and a shared "egress process" controlling outbound traffic — [performance.md](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/architecture/fast-sandbox/performance.md), [firecracker.md](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/architecture/fast-sandbox/firecracker.md). Direction of initiation for exec traffic is gateway → subject (inbound reachability gated by policy), not a sandbox-dialed control channel.
- **[OSS] libkrun TSI (Transparent Socket Impersonation)** is the verified dial-*out* networking analog in the VM runtime space: "Hooks guest socket syscalls over virtio-vsock to share host network connectivity without a virtual network interface" — the host VMM makes outbound connections on the guest's behalf; no inbound listener needed — [libkrun README](https://github.com/libkrun/libkrun) (fetched 2026-10-22; repo `containers/libkrun` redirects to `libkrun/libkrun`, main branch = 2.0 under development)
- **[PROD] Wasmer SDK** benchmarks "command transport" (Wasmer p50 5.57 ms) but was measured locally on-device (M5 Max), so its transport model is not a remote-gateway comparison — [RESULTS-2026-09-01.md](https://github.com/wasmerio/wasmer-sdk/blob/main/benchmarks/sandboxes/RESULTS-2026-09-01.md)
- **[PROD] AWS Lambda SnapStart / Firecracker** execution environments resume from snapshot and are driven by the Lambda service — control model not directly comparable (function-as-a-service, not exec gateway) — [Lambda SnapStart docs](https://docs.aws.amazon.com/lambda/latest/dg/snapstart.html)

### Inferences
- Zoo's dial-out design resembles the CI/CD runner model (GitHub Actions runner, Buildkite agent, AWS SSM agent, Coder agent — all documented as making outbound-only connections per their vendor docs; **not re-verified this session**, see Gaps). No surveyed AI-sandbox vendor advertises this. Security benefit (no inbound ports in sandbox, NAT/firewall-friendly) is real but the cost is gateway-side session state.
- Combining dial-out with snapshot resume has a nontrivial interaction (a restored VM must re-establish its outbound channel with fresh identity), which none of the surveyed docs address — a plausible zoo-specific design point worth documenting.

### Gaps
- Primary-source verification of outbound-only agent architectures (GitHub Actions runner long-poll, Buildkite wss outbound, SSM agent outbound endpoints) failed in this session (docs.github.com and buildkite.com pages are JS-rendered; raw fetches 404'd). Treat those precedents as background knowledge pending verification.
- Could not verify whether any 2025-2026 AI-sandbox startup (e.g., Morph, E2B competitors) has adopted dial-out; no such claim surfaced in any fetched doc.

---

## Q3: macOS VMs via Apple Virtualization.framework — APFS clonefile clones from a verified base; stop = pause + full memory save-state to disk; start = restore in ~10 s for 8 GB; snapshots = `cp -cR` clones

### Takeaway
The building blocks are all shipping in production open-source macOS tooling: OpenAI now maintains Tart (VZ-based), whose `tart clone` uses APFS copy-on-write and whose `tart suspend` implements save-state suspend/resume; Apple's own Containerization boots VZ micro-VMs in sub-second. Zoo's specific restore-latency engineering (~10 s for 8 GB) has no public comparator — nobody publishes save-state restore numbers for macOS.

### Cited Findings
- **[OSS/PROD] Tart is now owned by OpenAI**: `cirruslabs/tart` redirects to **`openai/tart`**; README instructs `brew install openai/tools/tart`; latest release **2.41.1 published 2026-10-15** by the openai org — [repo API](https://api.github.com/repos/openai/tart), [releases](https://github.com/openai/tart/releases), [README](https://github.com/openai/tart) (fetched 2026-10-22)
- **Tart uses Apple Virtualization.framework** ("near-native performance"; push/pull VMs from OCI registries) — [Tart README](https://github.com/openai/tart)
- **APFS copy-on-write cloning is exactly how Tart clones work**: "Due to copy-on-write magic in Apple File System, a cloned VM won't actually claim all the space right away. Only changes to a cloned disk will be written and claim new space. This also speeds up clones enormously." — [Clone.swift](https://github.com/openai/tart/blob/main/Sources/tart/Commands/Clone.swift)
- **Save-state suspend/resume ships in Tart**: `tart suspend` ("Suspend a VM") signals the `tart run` process (SIGUSR1) to suspend the VM to disk; run then resumes it — [Suspend.swift](https://github.com/openai/tart/blob/main/Sources/tart/Commands/Suspend.swift)
- **[PROD] Apple Containerization / `container` (announced June 2025, requires macOS 26)**: "executes each Linux container inside of its own lightweight virtual machine" using Virtualization.framework (`VZVirtualMachineManager`), "sub-second start times using an optimized Linux kernel configuration and a minimal root filesystem with a lightweight init system," dedicated IP per container — [apple/container README](https://github.com/apple/container), [apple/containerization README](https://github.com/apple/containerization)
- **[OSS] libkrun also runs on macOS**: uses HVF (Apple Hypervisor.framework) on macOS/ARM64 (plus KVM on Linux, WHP on Windows upcoming); GPU VMs on macOS via krunkit — [libkrun README](https://github.com/libkrun/libkrun)
- Zoo's **~10 s restore for 8 GB RAM**: no public macOS save-state restore benchmark found anywhere in this survey (Tart publishes none; Apple publishes none).

### Inferences
- Zoo's Q3 stack (VZ + clonefile + save-state restore) is architecturally common — Tart demonstrates clone-COW and suspend/resume as production features — but the *aggressive use of resume-from-save-state as the primary create path for agent sandboxes at 8 GB scale* is not documented anywhere else. Differentiation is in degree and latency, not in category.
- OpenAI owning Tart (since ~2025) signals that major AI labs are standardizing on VZ for macOS agent infrastructure — validates the zoo approach and raises the competitive baseline.

### Gaps
- "**AVMSaveState**": no identifier containing save/restore/snapshot was found in Apple's Virtualization framework docs index fetched this session (`virtualization.json`, 34 KB — possibly only a partial landing-page index). The exact public API behind Tart's suspend (e.g., a macOS 26 save-state API) could not be confirmed; treat "AVMSaveState" as unverified.
- **EC2 Mac instances / MacStadium Orka** approaches (snapshot support, warm pools of macOS VMs) were not verified this session (doc fetches not completed) — notable gap since Orka is the main production macOS-VM platform.
- Whether Tart's `tart run` resume path handles 8 GB-class VMs in ~10 s is untested publicly; no benchmark found.

---

## Q4: Batch exec — up to 25 commands in order over ONE HTTP round trip with stop_on_error

### Takeaway
No surveyed AI-sandbox runtime publishes a multi-command batch-exec API over a single round trip with stop_on_error. Verified nearest analogs are sandbox *creation* batching (OpenSandbox) and single-command exec APIs (E2B, Wasmer). This appears novel in the agent-sandbox category.

### Cited Findings
- **[PROD] OpenSandbox** advertises "batch creation" of sandboxes for "agent evaluation and RL training workloads" — batching applies to sandbox lifecycle, not command execution — [OpenSandbox README](https://github.com/opensandbox-group/OpenSandbox/blob/main/README.md)
- **[OSS] E2B** exposes per-call primitives (create/list/kill/pause/resume/connect/timeout/metrics/logs; envd in-VM agent serving exec) — no batch-exec endpoint appears in its architecture docs — [ARCHITECTURE.md](https://github.com/e2b-dev/infra/blob/main/docs/ARCHITECTURE.md)
- **[PROD] Wasmer** SDK: `Sandbox.create()` + exec measured as separate calls; benchmark explicitly complains that "The first-exec penalty must remain a first-class" metric — i.e., each command is its own round trip, which is exactly the cost zoo's batching attacks — [RESULTS-2026-09-01.md](https://github.com/wasmerio/wasmer-sdk/blob/main/benchmarks/sandboxes/RESULTS-2026-09-01.md)
- OpenSandbox's SDK create flow uses "polls readiness every 10 ms (SDK default: 200 ms)" against the gateway — per-operation polling, not multiplexed command batches — [performance.md](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/architecture/fast-sandbox/performance.md)

### Inferences
- Batch-exec-with-stop_on-error has deep prior art *outside* agent sandboxes (AWS Systems Manager RunCommand documents with onFailure semantics, CI workflow steps, Ansible playbooks — background knowledge, not verified this session), so the idea is proven but its application to sandbox exec APIs appears unclaimed as of Oct 2026.

### Gaps
- Did not verify OpenSandbox's full SDK surface (Python/Kotlin) for a batch/exec-script endpoint; their README mentions "execute commands and scripts" without batch semantics visible in fetched excerpts.
- Anthropic/OpenAI first-party sandbox tool APIs (e.g., code-execution tools) were not surveyed — single-command-per-call is the known shape but unverified here.

---

## Q5: Firecracker snapshots (snap-start) — status, latency figures, who builds on them

### Takeaway
Firecracker snapshot-resume is the *dominant* production technique for fast agent-sandbox creation in 2025-2026: E2B's open-source runtime is built on it, AWS Lambda SnapStart is its flagship production deployment, and OpenSandbox builds its 64 ms-p50 create path on Firecracker 1.16.1 templates. Firecracker's own docs do not publish a resume-latency figure; all public numbers are third-party.

### Cited Findings
- **[OSS] Firecracker snapshotting docs** (main branch, fetched 2026-10-22): full/diff snapshots, snapshot versioning, "network-for-clones" (network namespace/state handling for cloned microVMs), "random-for-clones" (RNG uniqueness for clones), "handling-page-faults-on-snapshot-resume" (lazy memory loading via userfaultfd: "Each time the guest touches a page that is not already in Firecracker's process memory, a page fault occurs") — [snapshotting dir](https://github.com/firecracker-microvm/firecracker/tree/main/docs/snapshotting), [page-faults doc](https://github.com/firecracker-microvm/firecracker/blob/main/docs/snapshotting/handling-page-faults-on-snapshot-resume.md)
- **[PROD] AWS Lambda SnapStart** (production since Dec 2022, expanded 2024-2025): "Lambda takes a Firecracker MicroVM snapshot of the memory and disk state of the initialized execution environment, encrypts the snapshot, and intelligently caches it… Lambda maintains several copies of each snapshot. Lambda automatically patches snapshots… Lambda resumes new execution environments from the cached snapshot"; advertised as "as low as sub-second startup performance" — [Lambda SnapStart docs](https://docs.aws.amazon.com/lambda/latest/dg/snapstart.html) (fetched 2026-10-22)
- **[OSS/PROD] E2B Runtime** (Apache-2.0, Go 1.26): "Firecracker microVMs that resume from a snapshot, run untrusted agent code, and pause when the agent stops"; CoW overlay filesystem so "only touched data is ever fetched" — [E2B Runtime README](https://github.com/e2b-dev/infra), [ARCHITECTURE.md](https://github.com/e2b-dev/infra/blob/main/docs/ARCHITECTURE.md)
- **[PROD] OpenSandbox fast-sandbox**: Firecracker v1.16.1 is the pinned default runtime; "warm create path admits work into pre-warmed capacity and restores a snapshot instead of booting a kernel"; measured phase timings below (Q8) — [firecracker.md](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/architecture/fast-sandbox/firecracker.md), [performance.md](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/architecture/fast-sandbox/performance.md)
- **[PROD] Modal** ships "Memory Snapshots [which] can dramatically reduce the cold start latency of Modal Functions by skipping initialization work on most container boots" (docs page; body JS-rendered, meta verified) — [Modal docs](https://modal.com/docs/guide/memory-snapshot). Whether Modal uses Firecracker or gVisor underneath could not be verified this session (see Gaps).
- **Latency figures (third-party, not official Firecracker numbers)**: E2B end-to-end "new sandbox + CPython + no-op command" = **807.4 ms p50 / 900.6 ms p95**, of which first-exec additional cost ≈ 205.4 ms — [Wasmer RESULTS-2026-09-01.md](https://github.com/wasmerio/wasmer-sdk/blob/main/benchmarks/sandboxes/RESULTS-2026-09-01.md). OpenSandbox: full template-create path 64 ms p50 with the Firecracker "VM state load/resume" phase itself at **0.233 ms p50** (hot template cache) — [OpenSandbox performance.md](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/architecture/fast-sandbox/performance.md)

### Inferences
- Zoo does not need Firecracker because it targets macOS (VZ) — but note that OpenSandbox's 0.23 ms vmstate-load and 50 ms runtime-create show Linux-side snapshot restore has become essentially free; the bottleneck moved to orchestration/readiness, exactly where zoo's identity-at-pool-time design also attacks.

### Gaps
- No official Firecracker resume-latency benchmark exists in the current docs directory (benchmarks.md absent/empty); the commonly cited "~125 ms" figure could not be sourced this session — treat as unsourced.
- Modal's underlying runtime (gVisor vs Firecracker) unverified; their "Modal V2 beta creation backend" was measured *slower* than standard in the Wasmer benchmark (1,228 ms vs 1,611 ms? see Q9 table) — attribution needs care.

---

## Q6: CROSVM snapshots; QEMU migration/savevm; CRIU and container checkpoints (Podman/Kubernetes)

### Takeaway
crosvm snapshots are explicitly "highly experimental" and not production-safe; Podman container checkpoint/restore via CRIU is mature and ships migration-grade features; gVisor has a documented checkpoint/restore path; QEMU savevm/migration was not re-verified this session (background knowledge).

### Cited Findings
- **[BETA/EXP] crosvm**: "Snapshotting is a **highly experimental** feature… It is 100% not [stable]" — snapshot format is "not stable," CBOR-encoded per-component files in a directory — [crosvm snapshotting.md](https://github.com/google/crosvm/blob/main/docs/book/src/architecture/snapshotting.md) (fetched 2026-10-22; linked as "(highly experimental)" from the book SUMMARY)
- **[OSS/PROD] Podman checkpoint/restore (CRIU)**: `podman container checkpoint` "checkpoints all the processes in one or more containers"; restore supported; `--export` produces compressed (zstd) checkpoint archives; can create a **checkpoint OCI image** pushable to registries for migration — [podman-container-checkpoint.1.md](https://github.com/containers/podman/blob/main/docs/source/markdown/podman-container-checkpoint.1.md) (fetched 2026-10-22). Caveat in doc: containers with systemd as entrypoint may not checkpoint.
- **[OSS] gVisor checkpoint/restore**: documented user-guide feature (Checkpoint/Restore page exists as canonical docs page) — https://gvisor.dev/docs/user_guide/checkpoint_restore/ (page title verified 2026-10-22)
- **[OSS] Kata Containers**: "standard implementation of lightweight Virtual Machines (VMs) that feel and [perform like containers]" — snapshot/fast-restore story not verified — [Kata README](https://github.com/kata-containers/kata-containers)
- **QEMU migration/savevm**: not re-verified this session (well-established background knowledge: live migration + `migrate`/`savevm` since ~2009).

### Inferences
- If zoo ever wants a Linux arm of the platform, CRIU/Podman checkpoint images and gVisor restore are the production-grade paths; crosvm is not.

### Gaps
- Kubernetes ContainerCheckpoint API milestones (alpha 1.25 / beta 1.30 per general knowledge) could not be verified: the KEP README fetch (kubernetes/enhancements sig-node/2003) and kubernetes.io pod-lifecycle grep both failed/missed this session.
- Kata Containers fast-boot/snapshot work (pre-created VM pools) not verified.

---

## Q7: Cloud Hypervisor, libkrun, Kata Containers, gVisor — 2025-2026 status

### Takeaway
Cloud Hypervisor has production-documented snapshot/restore and live migration; libkrun is rising (2.0 in development; the VMM behind macOS GPU micro-VMs and Podman's krun runtime); Kata and gVisor remain the container-isolation incumbents, with gVisor adding checkpoint/restore.

### Cited Findings
- **[OSS] Cloud Hypervisor**: `docs/snapshot_restore.md` — "take a snapshot of a previously paused virtual machine. This snapshot can be used as the base for creating new identical virtual machines, without the need to boot them from scratch… restored in a paused state"; `live_migration.md` also present in docs tree — [snapshot_restore.md](https://github.com/cloud-hypervisor/cloud-hypervisor/blob/main/docs/snapshot_restore.md), [docs dir](https://github.com/cloud-hypervisor/cloud-hypervisor/tree/main/docs) (fetched 2026-10-22)
- **[OSS] libkrun**: dynamic-library VMM; KVM/HVF/WHP backends; SEV/TDX confidential variants; virtio-vsock TSI networking; use cases: **crun/krun** (Podman `--runtime=krun`), **krunkit** (GPU VMs on macOS), **muvm** (Asahi Linux gaming); main branch = **libkrun 2.0** (breaking ABI, pre-stable); security-model doc warns the VM boundary does not restrict host resources exposed through devices — [libkrun README](https://github.com/libkrun/libkrun)
- **[OSS/PROD] Kata Containers**: lightweight-VM containers, active CI (nightly), katacontainers.io — [README](https://github.com/kata-containers/kata-containers)
- **[OSS/PROD] gVisor**: user-space kernel (Sentry); checkpoint/restore documented — https://gvisor.dev/docs/user_guide/checkpoint_restore/ (2026-10-22)

### Inferences
- The assignment's hypothesis that "libkrun (used by some 2025-2026 sandbox startups)" — libkrun's *verified* public users remain crun/krunkit/muvm; its macOS HVF support makes it the natural engine for Mac-hosted Linux sandboxes, which may explain startup adoption, but no specific startup's docs verified this session (gap).
- Which sandbox startup uses which VMM in production (Morph reportedly Cloud Hypervisor, E2B Firecracker verified, Modal unverified) remains the murkiest area — no primary sources fetched.

### Gaps
- "2025-2026 sandbox startups using libkrun": no primary source found/verified this session.
- QEMU savevm, Kata VM-pooling, gVisor restore performance numbers: unverified.

---

## Q8: Alibaba OpenSandbox (2025) — verification of "ready pools 12-16 ms", "Firecracker snapshot restore 35 ms", "reflink rootfs 0.7 ms"

### Takeaway
OpenSandbox is real, open source (Apache-style org `opensandbox-group`), and its fast-sandbox performance doc (measured **2026-10-08**) is public — but the assignment's three figures **do not match the current public docs**: docs report SDK create **64.0 ms p50** (warm serial), runtime create **33.5–50 ms p50**, rootfs reflink prep **~2.3 ms p50**, and VM state load/resume **0.233 ms p50**. The 12-16/35/0.7 figures may come from an older or internal measurement; treat them as unverified against public sources.

### Cited Findings
- **Repo**: `opensandbox-group/OpenSandbox` — "Secure, Fast, and Extensible Sandbox runtime for AI agents"; companion platform repo `opensandbox-group/fast-sandbox` — [repo](https://github.com/opensandbox-group/OpenSandbox) (fetched 2026-10-22)
- **README headline numbers**: "Firecracker creation: **64 ms P50 (serial)** / **125.4 ms P99 (10 concurrent)**. Firecracker sandboxes support pause/resume with memory and disk state preserved" — [README](https://github.com/opensandbox-group/OpenSandbox/blob/main/README.md)
- **performance.md** (ACK measurements, 2026-10-08, two-node ACK cluster, K8s 1.36.2-aliyun.1, Firecracker 1.16.1, AMD EPYC 9T95, XFS `reflink=1` StateRoot, pool = five Fastlets × four slots = 20 slots, workload = Alpine 3.19 + execd 1 vCPU/2 GiB; "engineering measurements, not a production latency guarantee"):
  - SDK create, warm serial baseline: **p50 64.0 ms** / p90 93.8 / p99 108.3 (100/100 success)
  - SDK create, concurrency 10: p50 102.7 ms / **p99 125.4 ms** (100/100); concurrency 20: p50 136.7 ms but **51/100 success** (capacity ceiling)
  - Phase breakdown (runtime logs, serial repeat): runtime create p50 **50.0 ms**, rootfs preparation p50 **2.31 ms**, VM state load/resume p50 **0.233 ms**; concurrency-20 runtime create p50 33.5 ms
  - Warm SDK `/ping` through the gateway: **p50 0.92 ms**
  - — [performance.md](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/architecture/fast-sandbox/performance.md)
- **Ready pools**: "Pools and Fastlets — the create hot path": `SandboxPool` CRD fixes runtime profile + resource shape; Fastlets are pre-warmed pods hosting many sandboxes; `warmImages` pre-pulls artifacts so "readiness never waits on a pull"; the network slot is pre-provisioned per sandbox before the runtime — [scheduling.md](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/architecture/fast-sandbox/scheduling.md)
- **Pause/resume/snapshot**: pause writes vmstate + memory (staged via `/dev/shm`) + SHA256SUMS manifest into a content-addressed artifact store, then releases the Fastlet (zero compute footprint); resume restores "on whichever Fastlet admission chooses — not necessarily the original host"; snapshots are published checkpoints; all async 202 Accepted — [checkpoints.md](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/architecture/fast-sandbox/checkpoints.md)
- **Other relevant OSEPs in tree**: OSEP-0024 "auto-pause and wake-on-access for fast-sandbox" — [oseps dir listing](https://github.com/opensandbox-group/OpenSandbox) (paths from git tree, 2026-10-22)

### Inferences
- OpenSandbox's "VM state load/resume p50 0.233 ms" confirms that with a hot template cache, Firecracker snapshot *loading* is effectively free and the 64 ms create is dominated by admission/orchestration and readiness — the same bottleneck layer zoo optimizes.
- Its 51/100 success at concurrency 20 on a 20-slot pool shows pool-exhaustion is the real availability risk of warm-pool designs — a differentiator dimension (elasticity) worth tracking for zoo.

### Gaps
- The specific "12-16 ms create via pool admission", "35 ms snapshot restore", "0.7 ms reflink" figures from the assignment were not found in the fetched public docs; no 2025 announcement blog/paper was located (search tooling down). If those numbers came from an Alibaba Cloud blog or the original 2025 launch, that source needs to be re-checked.
- Whether OpenSandbox is on Alibaba Cloud's production path (ACK service integration vs self-hosted) — docs describe ACK-based self-service deployment; production service status unverified.

---

## Q9: Wasmer wasmer-sdk sandbox benchmarks (RESULTS-2026-09-01)

### Takeaway
Wasmer publishes a rigorous, recent (2026-09-01) vendor benchmark on an Apple M5 Max comparing its WASM-sandbox SDK against Docker, Modal (standard + V2 beta), and E2B: Wasmer wins every phase by 1-3 orders of magnitude (end-to-end cold **25.05 ms p50** vs E2B 807 ms, Modal V2 1,229 ms, Modal 1,611 ms), but WASM sandboxes are a different isolation class than microVMs — the benchmark is self-interested and methodology caveats apply.

### Cited Findings
- **Benchmark file exists** at `wasmerio/wasmer-sdk` → `benchmarks/sandboxes/RESULTS-2026-09-01.md` (with `benchmark.py`, `build_e2b_template.py`) — [RESULTS-2026-09-01.md](https://github.com/wasmerio/wasmer-sdk/blob/main/benchmarks/sandboxes/RESULTS-2026-09-01.md) (fetched 2026-10-22)
- **Environment**: "Host: Apple M5 Max, arm64, 128 GB RAM, macOS 26.4; Wasmer 7.4.0; Docker 29.4.3 (Linux VM: 18 vCPU, 8.32 GB); Modal SDK 1.5.5 pinned us-west; Modal V2 = beta V2 creation backend; E2B Python SDK 2.46.0; 30 samples per group, sequential; client egress US, Cloudflare SJC edge"
- **Application startup** (new runtime process in existing sandbox): Wasmer **0.140 ms p50** / 0.161 p95; Docker CLI 205.952 ms; Modal V2 319.186 ms; Modal standard 335.645 ms; E2B custom template 517.160 ms p50 (1,567.845 p95)
- **Command transport** (exec round trip): Wasmer **5.569 ms**; Docker 49.011 ms; E2B 82.831 ms; Modal standard 103.326 ms; Modal V2 104.100 ms
- **End to end** (new sandbox + CPython startup + no-op command): Wasmer **25.049 ms p50** / 25.449 p95; Docker 281.121 ms; E2B 807.411 ms / 900.636 p95; Modal V2 1,228.539 ms; Modal standard 1,611.380 ms
- **Additional first-exec cost**: Wasmer ≈ 0 ms; Docker 19.719 ms; E2B 205.401 ms; Modal V2 800.549 ms; Modal standard 1,161.902 ms — the report argues "The first-exec penalty must remain a first-class" concern
- Definitions in the report: "'cold' is a newly allocated sandbox, while 'warm' reuses an existing sandbox"; a different measured metric had "`Sandbox.create()` has a 335.65 ms median" (Modal standard) "but a new sandbox's first completed Python command takes 1,611.38 ms end to end. Modal V2 improves this to 1,228.54 ms, while E2B takes 807.41 ms."

### Inferences
- Wasmer (WASM) is not directly comparable to microVM sandboxes on security, but its ~5.6 ms command transport sets the UX bar that remote VM sandboxes (E2B ~83 ms transport, Modal ~103 ms) are judged against — relevant context for zoo's single-websocket transport and 25-command batching.
- E2B's ~205 ms first-exec overhead even on a warm template is evidence that template restore + agent bring-up is still the dominant cost in Firecracker-based clouds — zoo's pre-warmed-identity claim attacks precisely this.

### Gaps
- All numbers are vendor-published (Wasmer sells the winning product); no independent replication found (search tooling down).

---

## Q10: Apple Virtualization.framework ecosystem — Tart, save-state tooling, EC2 Mac / MacStadium Orka

### Takeaway
2025-2026 turned Apple VZ into the mainstream path for agent compute: OpenAI took over Tart (active, 2.41.1 on 2026-10-15) with APFS-COW clone and suspend/resume, and Apple's own Containerization product runs per-container VZ micro-VMs in sub-second on macOS 26. Save-state restore tooling exists (tart suspend) but nobody publishes restore-latency engineering like zoo's ~10 s @ 8 GB.

### Cited Findings
- **Tart → OpenAI** (2025-2026): repo `openai/tart` (redirect from cirruslabs/tart); "Tart uses Apple's own Virtualization.Framework"; OCI registry push/pull of VMs; release 2.41.1 published 2026-10-15 — [repo](https://github.com/openai/tart), [releases API](https://api.github.com/repos/openai/tart/releases?per_page=8)
- **Tart clone = APFS CoW**: Clone.swift comment quoted in Q3 — [Clone.swift](https://github.com/openai/tart/blob/main/Sources/tart/Commands/Clone.swift)
- **Tart suspend/resume**: `Suspend.swift` — SIGUSR1 to `tart run` suspends the VM (save-state), resumed by run — [Suspend.swift](https://github.com/openai/tart/blob/main/Sources/tart/Commands/Suspend.swift)
- **Apple `container` / Containerization** (June 2025, macOS 26): per-container lightweight VMs, VZVirtualMachineManager, optimized kernel + minimal init → sub-second start, dedicated per-container IP — [apple/container](https://github.com/apple/container), [apple/containerization](https://github.com/apple/containerization)
- **libkrun on macOS**: HVF backend; krunkit for GPU micro-VMs on macOS — [libkrun README](https://github.com/libkrun/libkrun)

### Inferences
- OpenAI's stewardship of Tart plus Apple's Containerization indicate VZ save-state/clone patterns are becoming the *default* macOS agent-infrastructure substrate — zoo is early but no longer alone in the ecosystem.
- Zoo's snapshot-as-`cp -cR` (APFS clonefile of a suspended VM bundle) is the same primitive Tart uses for clone; using it for *snapshots of running agents* (memory included) goes beyond Tart's documented usage.

### Gaps
- AVMSaveState / Apple's save-state API surface: could not confirm any public API (see Q3 gaps). Whether macOS 26 exposes a supported VM save-state API (which Tart suspend presumably uses) is unverified.
- EC2 Mac instances (snapshot/AMI support, warm pools) and MacStadium Orka (snapshot/save features, Kubernetes-based orchestration): not verified this session.

---

## Q11: Fork-server / snapshot-fork research (SEUSS, Catalyzer, REAP, Pony) and anything newer

### Takeaway
The canonical fork/snapshot-boot serverless papers (Catalyzer NSDI'20, SEUSS EuroSys'23, REAP, Pony) are not on arXiv and could not be fetched/verified this session; no newer 2025-2026 open production system claiming "fork a pre-initialized VM" surfaced in fetched docs — OpenSandbox's Fastlet pools + snapshot restore is the closest *deployed* descendant of that literature.

### Cited Findings
- arXiv API searches this session: `ti:"SEUSS"` → no results; `all:"Catalyzer"` → only irrelevant physics papers — (arXiv API, 2026-10-22). These papers live in ACM/USENIX venues, not arXiv.
- Canonical citations (background knowledge, **not fetched this session**): Catalyzer: sub-millisecond startup for serverless (NSDI 2020, https://www.usenix.org/conference/nsdi20/presentation/du); SEUSS: snapshot + fast-forward boot (EuroSys 2023); REAP (EuroSys 2024) and Pony (2023-2024) on fork-based cold-start reduction — verify before quoting numbers.
- **[PROD] deployed descendants**: OpenSandbox's "restore a snapshot instead of booting a kernel" + constant-time admission into pre-warmed Fastlets — [performance.md](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/architecture/fast-sandbox/performance.md), [scheduling.md](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/architecture/fast-sandbox/scheduling.md); E2B "resume pre-booted snapshots instead of cold-booting" — [ARCHITECTURE.md](https://github.com/e2b-dev/infra/blob/main/docs/ARCHITECTURE.md); AWS Lambda SnapStart — [docs](https://docs.aws.amazon.com/lambda/latest/dg/snapstart.html)

### Inferences
- The research idea ("boot = fork/restore a pre-initialized state") has fully crossed into production via Firecracker snapshots + warm pools; the remaining research frontier (per the survey) is orchestration-layer latency and identity/policy binding — which is zoo's territory.

### Gaps
- No verification of SEUSS/REAP/Pony specifics or any 2025-2026 successor papers; USENIX/ACM pages not fetched (search tooling down). Explicitly unsourced in this document.

---

## Q12: Novelty scorecard for zoo (who does what, as of 2026-10-22)

| Zoo technique | Production implementations found | Beta/research only | Nobody (as far as verified) |
|---|---|---|---|
| Warm pool booted **carrying final sandbox identity** (name/home volume/credentials pre-provisioned at pool-boot) | None found. Closest: OpenSandbox pre-provisions *network slots + policy bindings* at pool time ([scheduling.md](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/architecture/fast-sandbox/scheduling.md)); its SDK pools pre-prepare whole sandboxes before acquire ([OSEP-0005](https://github.com/opensandbox-group/OpenSandbox/blob/main/oseps/0005-client-side-sandbox-pool.md)) | GitHub-style warm runner pools (generic identity, unverified here) | Pre-provisioning per-tenant final identity inside pooled containers appears undocumented anywhere public |
| Agent **dials out** over persistent websocket; no inbound listeners | Standard in CI-runner world (unverified this session); **no AI-sandbox vendor found doing it** — E2B (proxy→envd inbound) and OpenSandbox (gateway inbound, policy-gated) verified as inbound | — | Dial-out AI-sandbox control plane: no public example found |
| macOS VZ: APFS clonefile base + **pause/save-state + restore as create path** | Tart (OpenAI): APFS-COW clone ([Clone.swift](https://github.com/openai/tart/blob/main/Sources/tart/Commands/Clone.swift)) + `tart suspend` save-state ([Suspend.swift](https://github.com/openai/tart/blob/main/Sources/tart/Commands/Suspend.swift)); Apple Containerization sub-second boot (no save-state) | AVMSaveState API surface unverified | Publishing restore-latency engineering (~10 s @ 8 GB) or snapshot-of-running-agent as product feature: nobody found |
| **Batch exec**: 25 commands, one HTTP round trip, stop_on_error | None found in agent-sandbox APIs | OpenSandbox batches sandbox *creation*, not exec ([README](https://github.com/opensandbox-group/OpenSandbox/blob/main/README.md)) | Single-command APIs everywhere verified (E2B, Wasmer); batch-exec appears novel in this category |

### Cross-cutting latency context (for calibrating zoo's claims)
- Linux microVM sandboxes: OpenSandbox 64 ms p50 full create (snapshot-restore path) — [performance.md](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/architecture/fast-sandbox/performance.md); E2B 807 ms p50 cold end-to-end (third-party, remote cloud) — [Wasmer RESULTS](https://github.com/wasmerio/wasmer-sdk/blob/main/benchmarks/sandboxes/RESULTS-2026-09-01.md); Lambda SnapStart "as low as sub-second" — [AWS](https://docs.aws.amazon.com/lambda/latest/dg/snapstart.html)
- WASM sandboxes: Wasmer 25 ms p50 end-to-end, 5.6 ms transport (local) — [Wasmer RESULTS](https://github.com/wasmerio/wasmer-sdk/blob/main/benchmarks/sandboxes/RESULTS-2026-09-01.md)
- macOS VZ: Apple Containerization sub-second boot (no save-state) — [apple/containerization](https://github.com/apple/containerization); no public macOS save-state restore benchmark exists (gap)
