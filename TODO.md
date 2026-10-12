## TODOS

Focus only on linux for now just polish things and make linux usable first

Tools
- [x] Organize tools by category
- [x] connect tools to api
- [x] single adapter pattern to register all of the tools as well to access

DB
- [x] create schema for admin, workspaces, apps, permissions, network_permissions, file_permissions, command_permissions, runs
- [x] add crud routes for all of these
- [x] Backups

Docker
- [x] lifecycle methods for creating, stopping, starting, destroying the containers
- [x] sync the status to db
- [x] monitoring service to monitor all of the docker containers running
- [x] allow users to spin sandboxes on other machines, on the same network or another one

API
- [x] auth api
- [x] admins api
- [x] api keys
- [x] monitoring api

UI
- [x] build dashboards
- [x] move to tanstack start instead of the react version

PACKAGE
- [x] a python package for agents (sdk/python)
- [x] a package for cua agents with an agent loop

MISC
- [x] allow users to connect domains to their dashboards
- [x] rerouting and also certificates
- [x] spin up clusters on multiple machines as well
- [x] file system backups as well
- [x] open telemetry for tracing all requests, tool calls, db queries
- [x] logs with grafana
- [x] sync data to other machines as well
- [x] secret manager
- [x] embed profiles in the applications in these containers
- [x] add support for just code execution sandboxes, browser tools

Windows: making it solid
Windows stays a first-class platform. The work is the same as macOS: setup automation, recovery and parity.
- [x] Package windows/*.ps1 as a versioned release asset; the API uploads the version that matches it and checks the hash
- [x] zoo node install --windows: turns on Hyper-V and OpenSSH, installs the base VM from an ISO unattended (already partly done)
- [x] Template versioning: each frozen template gets a version, recorded on every sandbox
- [x] Differencing disks for new VMs and report clone time; target under 60 s to a usable desktop
- [x] Recovery: detect a hung VM or a stuck guest session (no VNC frames, no agent heartbeat) and restart it
- [x] Replace SSH and agent.ps1 with zoo-guest (Phase 3)
- [x] Licensing guidance in the docs (evaluation ISO, volume licensing), because teams will ask
- [x] Close matrix gaps: app profiles (Phase 4), monitoring (Phase 3), move between hosts (Phase 5), Store app blocking (Phase 4)
- [x] Host-side network policy through the Hyper-V switch (Phase 4)
- [x] Recorded-fixture tests in CI plus a nightly end-to-end run on a real Hyper-V host
- [x] Docs: supported Windows versions, Hyper-V requirements, troubleshooting

Agent, chat integrations, vault and profiles: making them solid
CUA agent
- [x] Agent runs move into the job worker, so they survive an API restart and resume or fail cleanly
- [x] Per-run limits: steps, wall-clock time, token spend; shown in the Agent tab
- [x] Store each step's screenshot reference with the run, for replay later
- [x] Pin the cua-agent version and run a scripted agent test against a fake model in CI
- [x] Model and provider settings per workspace, with keys from the vault
Slack, Discord, WhatsApp
- [x] Verify signatures on every webhook (Slack and WhatsApp paths have tests)
- [x] Retries with backoff and deduplication of repeated platform events
- [x] Allowlist of users who can command a sandbox from a channel
- [x] Discord bot runs in the job worker, not inside each API process, so replicas don't double-reply
- [x] Contract tests with recorded payloads for each platform
Vault and secrets
- [x] Secret changes apply to running sandboxes without a restart, through the guest agent
- [x] Usage view: which sandboxes use each secret, last used
- [x] Rotation reminders and expiry dates
App profiles
- [x] Profile versions: saving creates a new version, loading picks one
- [x] Lock check: refuse to load into a running app
- [x] macOS and Windows support
Remote servers, domains, observability
- [x] Remote Linux servers keep working through Docker over SSH (tests)
- [x] Migration command from Docker over SSH to zoo-node (`zoo-node migrate`, **Switch to zoo-node**)
- [x] Domains: tests for Caddy's on-demand TLS check and DNS verification
- [x] Observability: a default Grafana dashboard shipped with the observability profile

zoo-node and storage
A node daemon on every host replaces Docker over SSH. Kata Containers stays the Linux runtime (Firecracker dropped).
zoo-node
- [x] Daemon on each Linux, macOS and Windows host; dials out to the API over gRPC with mTLS, so hosts need no inbound ports and work behind NAT
- [x] Join flow: zoo node join <token> from a one-time token created in the dashboard
- [x] Runtime drivers behind one interface: kata (containerd shim through Docker), runc, zoovm, hyperv; the API's backends drive them through the node's tunnels
- [x] Reports capacity, health and running sandboxes; the scheduler places by real free capacity
- [x] Auto-update of zoo-node matched to the API version
- [x] Migration: zoo node migrate <server> converts an SSH-based remote server in place; Docker over SSH keeps working until 2.0
Moving and storage
- [x] Moves go through object storage, not the API host; works for Linux, macOS and Windows disks
- [x] Home disk snapshots replace tar-based backups (tar stays as an export format)

Kubernetes
Kubernetes is an added deployment option for teams that already run clusters. Single host and zoo-node stay the default and the fastest path.
Run Zoo on Kubernetes
- [x] Helm chart: API Deployment with HPA, job worker, gateway, web, Ingress, ServiceMonitor
- [x] Postgres through CloudNativePG or a managed database; object storage through any S3 API
- [x] Secrets through External Secrets Operator; ZOO_SECRETS_KEY from KMS
- [x] Publish the chart as an OCI artifact to Docker Hub with each release
Run sandboxes on Kubernetes
- [x] k8s runtime driver: one Pod per sandbox, runtimeClassName: kata, a PVC for /home, zoo-guest inside
- [x] Evaluate kubernetes-sigs/agent-sandbox (Sandbox and warm-pool resources) before writing our own operator
- [x] Egress: Cilium or Calico NetworkPolicy plus the egress proxy as a sidecar or node service
- [x] Warm pools as paused pods, because pod start takes seconds
- [x] macOS and Windows on Kubernetes clusters: zoo-node on Mac and Hyper-V hosts outside the cluster registers with the same control plane
- [x] Node requirements check: KVM on nodes, Kata RuntimeClass installed
Testing
- [x] kind cluster in CI with the runc RuntimeClass for every PR touching the driver
- [x] Nightly run on a real cluster with Kata

State, teams, limits (PLAN.md)
- [x] Postgres only; SQLite removed (no migrate-db: installs start from a fresh database)
- [x] Tickets, container hosts and agent run events out of process (LISTEN/NOTIFY); api, worker and gateway in compose
- [x] Object storage required: profiles, screenshots, Linux snapshots, backups
- [x] Workspaces, roles (owner, admin, member, viewer), invitations, shared sandboxes, secrets, profiles, servers
- [x] Scoped API keys (sandbox, read-only, expiry) and an audit log of every write with CSV export
- [x] Sandbox sizes, workspace quotas, idle auto-stop and maximum lifetime
- [x] Scheduled backups to object storage, restore command, restore tested in CI

Building on Zoo (PLAN.md)
- [x] Embeddable viewer: signed view/control links, an iframe page and @zoo/react
- [x] Share links with expiry; taking over from the agent and handing back
- [x] Labels on sandboxes, label-scoped API keys, quotas per label
- [x] Events (SSE, resumable) and signed webhooks with retries: run steps, completion, takeover
- [x] Usage metering per sandbox and label: CPU and memory minutes, storage, agent steps, tokens and cost
- [x] Trajectory export: JSONL with screenshots and accessibility trees
- [x] Provider per run, xAI Grok added
- [x] Benchmark harness: suites in parallel sandboxes, command or judge checks, per-model scores
