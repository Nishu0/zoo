# Development

Both development flows run separate API and job-worker processes plus the Bun dashboard from this checkout, with
reload enabled. Docker isolation follows `ZOO_ISOLATION`: `auto` uses Kata when available,
`kata` requires Kata, and `runc` uses ordinary Linux containers. Kubernetes requires Kata.
The helpers do not install Docker, k3s, Kata or storage drivers.
Both flows use the native Go gateway and the shared database-persisted CA. The source API
initializes its async pool, writer and cache before gateway clients and host requirements.

## Prerequisites

Run a Linux host with readable/writable `/dev/kvm`, KSM enabled, Python 3.12+, uv, Go 1.26,
goose, Bun and Docker Compose 2.24.4+ (the development overlay uses `!override`/`!reset`).
Run `uv sync --locked` first. Run the checkout and Docker on the same host; a remote Docker
daemon is not a supported development target. macOS workstations: see
[`compose.local.yml`](../compose.local.yml) below.

```bash
ls -l /dev/kvm
echo 1 | sudo tee /sys/kernel/mm/ksm/run
```

KSM needs `CONFIG_KSM` and persistent host configuration after reboot. It does not guarantee
deduplication of shared Cloud Hypervisor guest RAM. The Kubernetes setup deliberately disables
VM templating with virtio-fs; current Cloud Hypervisor does not support virtio-fs DAX.

## Docker: `make dev`

Register Kata's Go QEMU shim as `io.containerd.kata.v2` in this host's Docker before starting.
Use a dedicated development host: the sandbox network and egress daemon have the required
names `zoo` and `zoo-egress`, so do not share the host with a production Zoo installation.

```bash
make dev
```

The target builds the Linux/macOS/Windows `node-dist` binaries with the checkout's `VERSION`, then
builds the sandbox, native Go gateway and egress images. Its Compose project is `zoo-dev`;
only Postgres, SeaweedFS, its bucket initializer, the native gateway, egress and a local HTTP
proxy run in containers. Migrations run before the gateway, API and dashboard start.

- Dashboard: `http://localhost:3000`.
- Public API: `http://localhost:8000` (the source Python listener is loopback port 8003).
- Source worker: role `worker`, loopback port 8004; the public proxy does not route to it.
- Guest websockets: `/guest/connect` routes to the native gateway on loopback port 8001.
- Source API gateway RPCs: `ZOO_GATEWAY_PEERS=localhost`, gRPC port 7444.
- Gateway peer HTTP: TLS on port 8002, with the same database-persisted CA.
- The single containerized gateway advertises peers `localhost` and its own IP `127.0.0.1`
  for the source client. Sandboxes on the Docker network dial `ws://gateway:8001/guest/connect`.
- Native node enrollment: port 7443; `ZOO_DEV_NODE_HOST` optionally supplies a public hostname/IP
  without a port. Otherwise the node host is the generated API URL's hostname.

`data/dev/dev.env` holds persistent development-only secrets; the helpers never write the
checkout's `.env`; source processes disable automatic `.env` loading. API and gateway
share these secrets and the database-persisted node CA;
do not add certificate files or disable TLS verification. `ZOO_DEV_API_URL=http://HOST:8000`
sets an address that other machines and VM guests can reach. The local HTTP proxy is not a
production TLS endpoint.

Ctrl-C stops the source API/dashboard. To stop dependencies without deleting Docker volumes:

```bash
make devdown
```

Delete running development sandboxes first if they still hold the `zoo` network. Docker
development data and the CA survive restarts; do not delete the secrets file while retaining
encrypted database data.

## macOS host: `compose.local.yml`

`make dev` targets Linux. On a macOS workstation use the standalone local stack
instead — the infrastructure services without the API/worker containers, so the
API and dashboard run from source:

```bash
docker compose -f compose.local.yml up -d   # postgres, seaweedfs, gateway, egress, caddy
make up                                     # migrations (DATABASE_URL comes from .env)
uv run python main.py                       # API on :8003
cd apps/web && bun run dev                  # dashboard on :3000
```

Caddy fronts port 8000 as the public entry: `/guest/connect` routes to the
gateway (8001), everything else to the API on 8003 — macOS VMs reach it through
their NAT gateway `192.168.64.1`, so guest agents can phone home. Keep
`ZOO_GATEWAY_PEERS=127.0.0.1` in both the compose file and `.env`: macOS
resolves `localhost` to `::1` as well, which splits the gateway's ownership
hash and surfaces as `503: gateway ownership changed`.

Native macOS sandboxes work on this stack: the first boot clones and verifies a
base VM (`~/.zoovm/preparation/zoo-macos-base/state` tracks the phase), and
later stops suspend to a saved state (see `ZOO_MACOS_SUSPEND` in
[PLAN-macos-speed.md](../PLAN-macos-speed.md)).

Benchmarks against the published sandbox suites live in
[benchmarks/](../benchmarks/README.md) — `uv run python benchmarks/bench.py
--kind code` after minting a local key.

## Kubernetes: `make devkube`

Use a local **amd64 k3s host**, with the source checkout on that same host. Its kubeconfig
must point to the local Kubernetes API. Configure Kata 4.2.0's `kata-clh` handler as the
RuntimeClass named `kata`, TopoLVM's `zoo-local` StorageClass and the `zoo-snapshots`
VolumeSnapshotClass first. The host must route to its local cluster's pod IPs.

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
make devkube
```

The helper renders `deploy/helm/zoo/values-dev.yaml` first and checks the native gateway
image, peer DNS and service ports before proceeding. It builds/imports local
images into k3s, provisions Postgres and SeaweedFS in `zoo-dev`, migrates through a loopback
Postgres port-forward, and installs one gateway plus sandbox egress/pre-pulling. API, worker
and dashboard chart replicas are zero because they run from source instead.

Postgres, S3 and the gateway's gRPC/guest/peer HTTP ports are forwarded to loopback 5432,
8333, 7444, 8001 and 8002. The source API uses `ZOO_RUNTIME=kube`, peers `localhost`, and sandbox namespace
`zoo-dev-sandboxes`; the gateway pod uses the chart's headless peer DNS and downward-API pod
IP. Guests use the cluster gateway HTTP service, not host loopback. Source processes load
the same CA from Postgres; no TLS or gateway substitution is made.
The native node service is forwarded on port 7443 for mutually authenticated host connections;
`ZOO_DEV_NODE_HOST` must name an address those hosts can reach.

The helper never adopts an unmarked namespace. Ctrl-C stops source processes and forwards;
the development release remains until teardown:

```bash
make devkubedown
```

This removes the development release and both development namespaces, **including their
database, object storage, sandbox home claims and snapshots**. It does not remove k3s,
Kata, TopoLVM or the cluster-wide StorageClasses/VolumeSnapshotClass. Docker and Kubernetes
flows cannot run together because their source ports are shared.
Keep the same development kubeconfig/context when running the teardown command.

The hyphenated aliases `dev-down`, `dev-kube` and `dev-kube-down` are also available.

## M3+ Macs: nested KVM inside Lima

[Lima's VZ driver](https://lima-vm.io/docs/config/vmtype/vz/) supports nested virtualization
on M3-or-newer Apple Silicon with macOS 15+. Create a Linux guest with nested virtualization
explicitly enabled, for example:

```yaml
base: template:ubuntu
vmType: vz
arch: aarch64
nestedVirtualization: true
cpus: 8
memory: 16GiB
disk: 120GiB
mounts:
  - location: "~"
    writable: true
```

Save that as `zoo-lima.yaml`, then:

```bash
limactl start --name=zoo zoo-lima.yaml
limactl shell zoo
```

Inside Linux, verify `/dev/kvm`, install the listed development tools, enable KSM and
register Docker's QEMU Kata shim. Keep Linux Python/Bun dependencies on the guest filesystem
rather than reusing macOS binaries; for a mounted checkout set
`UV_PROJECT_ENVIRONMENT="$HOME/.venvs/zoo-dev"` before `uv sync --locked`. Then run `make dev`
inside that guest, not on the Mac host.

**Current Kubernetes limitation:** the pinned Kata release ships the Go `clh` shim only for
amd64. Native ARM Lima cannot run this `devkube` configuration; an emulated amd64 guest
does not supply amd64 KVM. Use a real amd64 Linux k3s development host for `make devkube`.
Do not change the runtime to runc or silently swap Kubernetes to QEMU.

## VM-control provisioning boundary

Whole-VM pause requires the source-pinned patched rootful Go Kata shim, the authenticated
VM-control broker/frontend and generation-bound certificates. Stock Kata is insufficient.
The new production chart requires `vmControl.trustDomain`, `publicCAConfigMap`,
`issuerTLSSecret`, `nodeCASecret` and `runtimeHandler`; its dedicated issuer API has an
internal HTTPS enrollment URL. It also needs separate node/controller/issuer
trust, source API/worker client credentials, and a reachable enrollment route.

The current source helpers do not generate that PKI or substitute insecure credentials.
Until the coordinated production installer exposes development-compatible provisioning,
`devkube` fails at chart rendering rather than disable VM control. Docker source startup
also needs the production VM-control service and source client credential wiring before
whole-VM lifecycle operations can be validated. The source API uses role `api`; the source
worker uses role `worker` and needs its own matching controller certificate, not the API leaf.
Export its paths as `ZOO_DEV_VM_CONTROL_WORKER_CERT` and `ZOO_DEV_VM_CONTROL_WORKER_KEY`;
the helper maps these only into that worker's `ZOO_VM_CONTROL_CLIENT_CERT/KEY`.
This is an implementation integration
boundary, not runtime verification or a weaker development isolation mode.

## Schema changes and static checks

Add migrations with `make create name=...` and queries in `db/query.sql`. `make up` applies
migrations using your exported `DATABASE_URL` and regenerates sqlc output; generated files
under `db/generated` are not edited by hand.

```bash
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
make -n dev devdown devkube devkubedown
helm lint deploy/helm/zoo -f deploy/helm/zoo/values-dev.yaml
```

Tests and real runtime verification require explicitly provisioned test infrastructure;
the development targets do not run tests. CI/release workflows are under `.github/workflows`.

## Project layout

```text
apps/server/       Python API, worker, requirements and runtime backends
go/gateway/        native Go guest/node connection gateway
go/node/           Linux/macOS/Windows host daemon
go/guest/          in-sandbox agent
go/egress/         standalone cross-platform egress policy daemon
go/vm-control/     Kata vm-control frontend
db/                Postgres migrations, queries and generated client
apps/web/          Bun/TanStack dashboard
apps/site/         docs site
sdk/python/        Python SDK and sandbox CLI
macos/, windows/   platform VM helpers and guest setup
scripts/dev.py     source development supervisor and infrastructure commands
deploy/helm/zoo/   Kubernetes chart and values-dev.yaml
install.sh         pinned production runtime bootstrap and offline bundle consumption
```
