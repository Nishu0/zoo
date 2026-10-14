---
title: Architecture
description: Two Linux runtimes, native connection ownership, persistent data and external clients.
---

## Components

| Component | Responsibility |
| --- | --- |
| Python API | Versioned REST, MCP, authorization, policies and tool dispatch |
| Python worker | Lifecycle jobs, warm-pool refill, retention and backups |
| Native Go gateway | Guest/node streams, active VNC sessions and replica routing |
| zoo-guest | Shell, files, terminal, accessibility, windows, apps, secrets and tunnels inside a sandbox |
| zoo-node | Dial-out macOS/Windows host daemon; authenticated VM-helper operations |
| zoo-egress | Host-enforced network policy, outside Linux sandboxes and on VM hosts |
| Postgres | Authoritative state, envelope keys, node CA, host credentials, jobs and events |
| Object storage | Backups, portable profiles and runtime-specific exported data |

Zoo contains no model provider, planner, built-in task execution service or chat connector.
External clients decide what to do and call REST, MCP or the SDK.

## The two Linux runtimes

`ZOO_RUNTIME=docker` uses one local Docker host, with a Kata VM per sandbox and a Docker
home volume. Linux capacity is that host's capacity. Docker sandboxes do not move between hosts.

`ZOO_RUNTIME=kube` uses Kata pods on Kubernetes, with local TopoLVM home claims and CSI
snapshots. Placement selects an eligible node and binds storage to it; local storage does
not automatically follow a pod to another machine. Supported moves export/restore data
through object storage and require compatible destination capacity.

Mac and Windows hosts provide only their native VM types. They are not extra Docker
capacity and never receive a host Docker tunnel.

## Connection ownership and ports

`ZOO_GATEWAY_PEERS` is a DNS name, not a URL: `gateway` in Compose or a headless service
name in Kubernetes. Replica ownership uses rendezvous hashing over DNS members. Each
replica's `ZOO_GATEWAY_IP` must identify its own member; Kubernetes supplies the pod IP.

| Listener | Default | Boundary |
| --- | --- | --- |
| API/worker gateway RPC | 7444 | Private authenticated TLS |
| Gateway peer HTTP | 8002 | Private authenticated TLS relay traffic |
| Guest websocket/health | 8001 | Internal listener; public ingress routes guest connections |
| Native node stream | 7443 | Host-facing mTLS, separate from HTTP ingress |

Guests derive their address from the deployment and public API URL. There is no configurable
guest-URL interface. Native node enrollment may use a different public hostname:
`ZOO_NODE_HOST` is a hostname/IP without a port and otherwise defaults to the API hostname.

The gateway holds one VNC session for an active sandbox and serves screen pixels/input to
tools and viewers. A disconnected required guest produces `guest_unavailable`; there is
no second tool path through the container runtime.

## Lifecycle, pool and stop

Cold provisioning and storage work run as jobs. A ready warm sandbox is claimed in the
create request and receives its identity, policy and secrets before the request reports
it ready. An empty or incompatible pool does not justify bypassing policy checks.

Stopping a Linux sandbox pauses it first: on Kubernetes its Kata VM through VM-control, on
Docker its container (`docker pause`, which freezes the sandbox's processes; the VM keeps
its memory). `resumable` marks that case while the public state remains stopped, and the
next start resumes it. After `ZOO_PAUSE_MINUTES` (default 30), or sooner on a node with
less than 10% of its memory free (oldest first), the sandbox is deleted down to its home
volume and the next start is a cold boot. A process freeze does not release memory. Native platform saved-state reuse also has identity/host restrictions.

## Persistence and events

The database stores the node CA and VM-host identity; filesystem key copies are not the
authoritative identity store. API caches invalidate through database notifications.
Durable state transitions produce SSE/webhook events; only monitoring numbers use polling.

Static/build checks do not establish runtime recovery or speed. Saved-state slot reuse,
whole-VM stop and [performance targets](/docs/reference/performance/) require the deferred
real-platform validation.
