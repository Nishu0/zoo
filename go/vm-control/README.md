# Native Kata VM control foundation

This is a new Zoo-specific protocol, not an upstream Kata management API. It
requires the source-pinned Kata 4.2.0 **Go, rootful QEMU or Cloud Hypervisor** shim built by
`scripts/build-kata-vm-control.sh`. Stock Kata, runtime-rs, other VMMs, and
rootless installations are rejected. There is no process-freeze fallback.

The implementation has been statically checked and cross-compiled only. Fake
unit tests are included and compiled, but must not execute before T150.
The Python client, enrollment issuer, image and separate Helm template are supplied.
Parent work still owns runtime routing, SQL lifecycle state, sweeper, install,
controller credential mounts and enrollment router wiring. This foundation alone does not
implement the complete two-stage stop.

## Ownership and security

- Run the broker as UID 0 with all capabilities dropped except `CHOWN`, no privilege
  escalation, a read-only root filesystem, no host PID/network namespace, and
  a private writable directory for its Unix socket and node-local credentials.
  `CHOWN` is needed only to assign the private socket's frontend UID and the
  credential files' frontend GID. Credential files remain UID 0, mode 0640;
  the dedicated frontend UID and GID must match. No `DAC_OVERRIDE`, `SYS_ADMIN`,
  `NET_ADMIN`, host PID or host networking is required.
- Run the HTTPS frontend as a dedicated non-root UID, for example 10001. Give
  it only TLS credentials and the broker socket directory; never mount Docker,
  containerd, `/run/vc`, QMP, or host `/proc` into the frontend or egress.
- Broker access is a privileged trust boundary even with read-only socket
  mounts. The broker only performs fixed inventory reads and the narrow shim
  operations, but compromise of code holding raw runtime sockets is dangerous.
- Broker `SO_PEERCRED` must match the configured frontend UID. Its socket is
  mode 0600, owned by that UID, inside a root-owned, non-writable directory
  traversable by the frontend. A dedicated emptyDir is suitable. The socket
  directory must not be shared with workloads using that UID.
- The frontend requires TLS 1.3 and a verified client certificate with exactly
  one explicitly allowlisted controller URI. Do not share the Linux/VM node
  enrollment CA or accept all certificates signed by a CA.
- Node certificate URI:
  `spiffe://<installation-trust-domain>/vm-control/node/<node-name>/<node-uid>`.
  Controller URIs:
  `spiffe://<installation-trust-domain>/vm-control/controller/api` and/or
  `spiffe://<installation-trust-domain>/vm-control/controller/worker`.
  Use separate server/client issuance profiles and appropriate EKUs.
- Parent clients must verify the server chain, DNS SAN, **and exact node URI**,
  including the immutable node generation UUID. Route through node-selected
  DaemonSet Pod addresses; do not round-robin to a different node or accept
  caller-supplied endpoints. Bind configuration to the actual Kubernetes node
  UID, or a provisioned immutable UUID on the single Docker host.
- No request accepts a socket path, shell command, QMP command, endpoint,
  namespace override, or executable. Request bodies are bounded and strict.

## Required configuration

Both processes require `-mode`, `-node-name`, `-node-uid`, `-trust-domain`,
`-controllers`, and matching `-broker-socket`. All identities are explicit;
the service does not trust caller-supplied identities. With `-credential-dir`,
the root-owned enrollment result supplies `-node-uid`; explicit values must match.
Kubernetes enrollment derives it from authenticated API Node metadata, not Pod UID.

The broker additionally requires `-frontend-uid`, `-runtime`,
`-runtime-socket`, and `-namespace`:

- Kubernetes: socket `/run/k3s/containerd/containerd.sock` for this install,
  `-namespace` is the installation sandbox namespace, and `-runtime-handler`
  is the exact installed Go CRI handler (T82 defaults to `kata-clh`). The
  containerd namespace is fixed to `k8s.io`. CRI inventory must match Pod name,
  UID, installation labels, handler, and ready state with exactly one match.
- Docker: socket is the explicitly configured local Docker HTTP API socket,
  normally `/var/run/docker.sock`. `-namespace` is Docker's configured
  containerd namespace, normally `moby`. Inspection requires a full container
  ID, exact Zoo sandbox label/name, Kata runtime, and live non-restarting
  state. Docker's guest-container pause flag must not be used for VM pause.
- `-shim-root` defaults to `/run/vc/sbs`. Mount only that root-owned state
  directory read-only, not `/run/vc/vm` or any QMP socket. The derived private
  `zoo-vm-control.sock` must be mode 0600 and its peer must be UID 0.
- `-host-memory` defaults to `/host/proc/meminfo`; mount the host's
  `/proc/meminfo` read-only at that exact path. Do not substitute container
  limits or Kubernetes allocatable values for host MemAvailable.
- `-shim-manifest` and `-shim-binary` default to the corresponding `/opt/kata`
  installed paths. Broker startup verifies root ownership, source/protocol/architecture,
  supported VMMs and the manifest's binary SHA-256. Mount only those two files
  read-only. A stock or missing shim is an explicit startup failure.

The frontend additionally requires `-tls-cert`, `-tls-key`, `-controller-ca`,
and optionally `-listen` (default `:9444`). It refuses UID 0. Provision a server
certificate with the required URI and the DNS SAN parent clients verify.

## Controller API

`POST /v1/vm/state` queries actual state using a JSON `target`:

```json
{
  "target": {
    "node_name": "node-a",
    "node_uid": "33333333-3333-4333-8333-333333333333",
    "sandbox_id": "11111111-1111-4111-8111-111111111111",
    "pod_namespace": "zoo",
    "pod_name": "zoo-sandbox-11111111-1111-4111-8111-111111111111",
    "pod_uid": "22222222-2222-4222-8222-222222222222"
  }
}
```

For Docker replace the three `pod_*` fields with `container_id`, the full
64-character lowercase hexadecimal ID. Responses contain `target` and `vm`;
`vm.sandbox_id` is the locally resolved **Kata/CRI sandbox ID**, not the Zoo
UUID or Pod UID. `vm.namespace` is `k8s.io` or the configured Docker namespace.

`PUT /v1/vm/state` additionally requires `desired_state` (`paused` or
`running`), `operation_id` (16–128 ASCII alphanumeric/underscore/hyphen
characters), and `expected_revision` from the query. A UUID is a suitable
operation ID. Mutations also require `target.native_id`, the full Kata/CRI
ID obtained at initial binding, plus `target.generation` (the random 64-hex shim
epoch returned by the live state query). A restarted CRI sandbox with the same Pod UID
cannot receive a mutation intended for its predecessor. Queries may omit it
only when creating the initial binding. Even a Docker restart that reuses a
container ID cannot reuse an old revision/operation intent across a shim epoch.
Epoch reset is an explicit conflict requiring deliberate lifecycle recovery,
not silent rebinding. The shim checks protocol, source revision,
runtime identity, and actual VMM state before success. Mutations and lifecycle RPCs share the shim's
existing lifecycle mutex; guest-health probes use a separate drain/gating lock.

Replay the **same ID and parameters** after a lost response. Reusing an ID
with different parameters is rejected. The most recent 256 operations are
remembered for the shim epoch; older replays fail their stale revision.
A replay after a newer operation returns current actual state and revision,
never re-applies the old desired state. Inspect the returned state before
committing SQL; do not treat HTTP success alone as confirmation of your desired
state. Persist operation intent before sending, then CAS the lifecycle pointer
after confirmation. Reconcile unknown outcomes by querying this same identity.

Guest health checks are drained before pause and suppressed only while pause
is expected/unknown. VMM checks continue. Guest-dependent shim operations
fail promptly while paused, rather than blocking resume behind the lifecycle
mutex. Native termination resumes first, verifies it, then continues teardown.
CRI/container task status remains running; Zoo owns the internal paused field.

`GET /v1/host/memory` returns node identity, `available_bytes`, and
`total_bytes`, using MemAvailable rather than MemFree.

VM pause retains guest RAM and Pod requests. The parent sweeper must remove
expired/pressure-selected paused containers/Pods, oldest first, retain the
persisted home volume/PVC, and wait for runtime termination before counting
memory released. Resume and deletion must not race through independent SQL
lifecycle decisions. Readiness must require the authenticated service and
patched shim protocol; it must not silently use Docker task pause or guest
signals when control is unavailable.

## Build and release hooks

The root makefile is intentionally unchanged. Parent can include
`vm-control/build.mk` or invoke it with `make -f vm-control/build.mk`.

```sh
make -f vm-control/build.mk vm-control-static VM_CONTROL_ARCH=amd64
make -f vm-control/build.mk vm-control-static VM_CONTROL_ARCH=arm64
make -f vm-control/build.mk kata-vm-control VM_CONTROL_ARCH=amd64
make -f vm-control/build.mk kata-vm-control VM_CONTROL_ARCH=arm64
```

`VM_CONTROL_OUT` defaults to `/tmp/zoo-vm-control-artifacts`. Set `GNU_MAKE=gmake`
on macOS. The script downloads only the locked archive, checks SHA-256 before
applying the lifecycle patch/overlay, generates upstream config metadata,
vets affected packages, builds the shim, and **only compiles** their tests
with `go test -c`. No compiled binary is executed.

Outputs are `zoo-vm-control-linux-<arch>` and
`zoo-kata-shim-4.2.0-vm-control-1-linux-<arch>.tar.gz` with a checksum and
provenance manifest. The shim archive overlays
`opt/kata/bin/containerd-shim-kata-v2` in the existing pinned Go Kata payload;
it does not replace QEMU/CLH, guest images, kernels, or runtime configuration.
Parent's release/bundle hook must overlay it **before** computing payload
checksums, include the control binary, and configure Docker plus kata-deploy
to use this patched Go binary, preserving T82's Go `clh` Kubernetes default.
Do not choose `clh-runtime-rs`. Do not overwrite a running node's shim
in place; installation/rollout belongs to the parent and is not run here.

Packaging uses Python 3's standard library to produce root-owned archive
entries and stable timestamps (`SOURCE_DATE_EPOCH`, default 0), independent of
the build user's UID. The release manifest fingerprints both the lifecycle
patch and overlay, as well as the compiled binary and pinned upstream source.

## Native CLH source verification

The pinned upstream `src/runtime/virtcontainers/clh.go` already implements
`PauseVM(ctx)` through the shim-owned `clhClient.VmPausePut(ctx)` and `ResumeVM(ctx)`
through `clhClient.ResumeVM(ctx)`. Both derive bounded API contexts from the
caller. The new `ZooVMStatus(ctx)` calls that same client's `VmInfoGet(ctx)` and
accepts only native `Running` or `Paused`. The generated client uses the private
CLH Unix HTTP endpoints `PUT /api/v1/vm.pause`, `PUT /api/v1/vm.resume`, and
`GET /api/v1/vm.info`; it is not a new exposed control route. No competing VMM
socket connection or cached `clh.state` is used. The same serialized lifecycle
and pause-aware health gate applies to both Go VMMs. QEMU uses its existing
live shim-owned QMP `stop`, `cont`, and `query-status` connection.

Both the service and patched shim compile for Linux amd64/arm64. Kubernetes
deployment remains amd64, matching T82. CLH lifecycle/state fakes are source
tests only; compile success is not a runtime pause/resume verification.

## Python integration contract

`server/vm_control.py` exposes synchronous bounded functions:

- `binding(sandbox_id, runtime_id, config)` returns a new binding. Call after
  native boot, then persist it as `config["vm_control"]` before any mutation.
  It refuses to replace an existing binding. It records the runtime ID, full
  target (node name/UID plus Pod name/namespace/UID or Docker container ID),
  actual Kata native ID, and shim epoch `generation`. Never clear/rebind merely to make a failed pause
  succeed; delete the old generation and boot a new one deliberately.
- `current_state(sandbox_id, runtime_id, config)` returns `VMState` with
  `state`, unsigned `revision`, `native_id`, `namespace`, `generation` and `operation_id`.
- `pause(...)` and `resume(...)` additionally require keyword-only
  `operation_id` and `expected_revision`. Persist intent before calling, retry
  the identical intent on lost responses, and CAS SQL only after comparing
  returned actual state. An idempotent replay can legitimately report a newer
  state. The client never rebases or automatically creates operation IDs.

Use a thread offload when calling these from async routes. Each operation has
a 20-second discovery/transport budget, bounded inventory calls, strict node
URI plus TLS 1.3 chain/DNS verification, no proxy or redirect, and an 8 KiB
response bound. Failures raise existing `ZooError` codes. Kubernetes discovers
exactly one ready owned DaemonSet Pod on the persisted node, verifies current
Pod/Node UID and immutable native ID, then connects directly to its Pod IP.
Docker uses the single configured host IP and generation; the broker performs
local Docker inventory verification before every request. No Docker pause,
CRI pause, guest signal, SSH, or runtime-rs alternative is attempted.

Required API/worker environment (paths identify files, never inline key data):

| Environment | Meaning |
|---|---|
| `ZOO_ROLE` | `api` or `worker`, matched to the client certificate URI |
| `ZOO_VM_CONTROL_TRUST_DOMAIN` | Installation-specific canonical SPIFFE domain |
| `ZOO_VM_CONTROL_CLIENT_CERT`, `ZOO_VM_CONTROL_CLIENT_KEY` | This role's separate certificate/key |
| `ZOO_VM_CONTROL_NODE_CA` | Public node-server CA PEM file |
| `ZOO_VM_CONTROL_SERVER_NAME` | TLS DNS SAN, Kubernetes `<fullname>-vm-control.<sandbox-namespace>.svc.cluster.local` |
| `ZOO_VM_CONTROL_NAMESPACE` | Controller/sandbox namespace (Kubernetes) |
| `ZOO_VM_CONTROL_SELECTOR` | Explicit equality labels including release instance and component `vm-control` |
| `ZOO_VM_CONTROL_DAEMONSET` | Exact `<fullname>-vm-control` name (Kubernetes) |
| `ZOO_VM_CONTROL_SERVICE_ACCOUNT` | Dedicated controller SA, also `<fullname>-vm-control` |
| `ZOO_VM_CONTROL_DOCKER_URL` | Trusted `https://<host-IP>:9444`; never persisted/user-supplied |
| `ZOO_VM_CONTROL_NODE_NAME`, `ZOO_VM_CONTROL_NODE_UID` | Single Docker host's immutable name/generation UUID |
| `ZOO_VM_CONTROL_DOCKER_NAMESPACE` | Explicit Docker containerd namespace, normally `moby` |

Issuer-only API environment additionally requires `ZOO_VM_CONTROL_NODE_CA_KEY`.
For Docker it also requires `ZOO_VM_CONTROL_DOCKER_ENROLL_TOKEN`, a path to this
single host's unique opaque enrollment credential. Do not mount either issuer
secret into workers, broker/frontend or egress; only the single-host enrollment
credential (not the CA key) is shared with that host's broker. For Docker mount
`enrollment_router()` under `/v1` on the HTTPS API only. Kubernetes ships a
dedicated issuer API deployment using `issuer_app()`, an issuer-specific SA
and the existing API image. It has no DB/migration/host preflight or runtime
startup, avoiding an API-readiness/enrollment dependency cycle. Its route authenticates
Pod-bound Kubernetes TokenReview audience `zoo-vm-control-enroll`, exact dedicated
SA, Pod name/UID, owned DaemonSet, scheduling node, and actual Node UID. It does
not trust token node extras as proof. Docker enrollment requires constant-time
verification of its unique credential and exact configured node name/UID.
CSRs must be signed P-256; requested SANs/privileges are ignored. Issuance is
serverAuth-only, exact node URI/DNS, 12-hour expiry.

## PKI and shipping hooks

These are implementation hooks, not commands executed during validation:

1. On the installation authority, call
   `python -m server.vm_control bootstrap --directory <new-private-directory> --trust-domain <domain> --issuer-server-name <issuer-DNS>`.
   It refuses existing output and creates separate node and controller CAs,
   distinct API/worker client keys and issuer HTTPS identity (90-day certificates), and the Docker
   enrollment credential. All output starts mode 0600 inside a 0700 directory.
   Keep controller CA signing material offline; refresh the controller role
   and issuer HTTPS certificates before expiry, rolling the issuer pod after
   replacing its TLS Secret. Only the HTTPS API issuer receives the node CA
   signing key. These are separate from zoo-node/gateway enrollment PKI.
2. Kubernetes: publish only `node-ca.pem`, `controller-ca.pem`, and the HTTPS
   issuer's public root `issuer-ca.pem` in a ConfigMap in the sandbox namespace.
   The dedicated issuer uses the generated `issuer.pem`/`issuer-key.pem` as
   Secret `tls.crt`/`tls.key`; its HTTPS root is `node-ca.pem`, which is copied
   as public `issuer-ca.pem`. Generate its DNS SAN as
   `<fullname>-vm-control-issuer.<release-namespace>.svc.cluster.local`.
   Create a separate issuer-only Secret containing `node-ca.pem` and
   `node-ca-key.pem`; nodes receive neither the private key nor that Secret.
   `deploy/helm/zoo/templates/vm-control.yaml` selects only these three keys.
   Set `vmControl.trustDomain`, `publicCAConfigMap`, `issuerTLSSecret`, `nodeCASecret`, and
   `runtimeHandler` (`kata-clh` for T82). Rendering fails if they are missing;
   there is no opt-out/default task pause. The init container generates a fresh
   per-Pod/node key locally and enrolls before starting the root broker and
   non-root frontend. The broker renews every 30 minutes using a rotated projected
   600-second audience-bound token. The frontend reloads the node certificate
   for each TLS handshake and fails readiness when credentials expire.
3. Docker initial enrollment must happen before API host-readiness preflight:
   use broker `-mode=csr -node-name=... -node-uid=... -trust-domain=...`
   with `-credential-dir=<node-private-directory> -frontend-uid=10001` on that
   host. Only its public `node.csr` goes to the authority. There invoke
   `python -m server.vm_control issue-node` with `--csr`, `--node-name`,
   `--node-uid`, `--node-ca`, `--node-ca-key`, `--trust-domain`, `--server-name`
   and a new `--output` certificate path. Return only the public leaf to the host.
   Broker `-mode=install-certificate` uses the same immutable identifiers,
   `-credential-dir`, `-frontend-uid`, `-tls-cert=<leaf>`, `-node-ca=<public-CA>`
   and `-server-name` to verify/import it. Mount the generated node-local files
   read-only into that frontend; do not distribute its private key to any other
   node. For automatic renewal, the broker takes `-runtime=docker`, immutable
   `-node-uid`, `-enroll-url`, `-enroll-ca`, `-enroll-token` (unique host
   credential), `-node-ca`, and `-server-name`, just as Kubernetes uses the
   projected token. This path does not depend on Docker task pause.
4. Build/publish `docker/Dockerfile.vm-control` as `zoo-vm-control:<release>` and include
   the service binary plus patched shim archive in the release bundle. Parent
   must wire the new Docker image into compose and the build/release workflow,
   mount role-specific API/worker credentials, mount issuer signing material
   only in the issuer API, and configure the Docker issuer route before its
   automatic renewal. Kubernetes issuer and enrollment wiring is self-contained
   in the new template; normal API/worker mounts and runtime routing remain parent work.
   The Helm template supplies services, dedicated node SA with no API RBAC,
   issuer/discovery RBAC, isolated broker/frontend, root-only raw host mounts,
   public-only PKI mounts, projected token and installation-scoped ingress.
   The release must install the patched Go shim and its manifest in the
   kata-deploy payload, not let a later stock kata-deploy copy overwrite it.
   No install, CA generation, service start, enrollment request or deployment
   is performed here. Existing values/helpers/install/compose/main are parent-owned.

## Changed paths in this integration

New: `server/vm_control.py`, `Dockerfile.vm-control`,
`deploy/helm/zoo/templates/vm-control.yaml`, `vm-control/provision_linux.go`,
`vm-control/bootstrap_linux.go`, `vm-control/installation_linux.go`,
`vm-control/provision_linux_test.go` (source/compile only).

Updated owned foundation: `vm-control/main_linux.go`, `vm-control/frontend_linux.go`,
`vm-control/protocol.go`, `vm-control/broker_linux.go`, `vm-control/control_test.go`,
`vm-control/README.md`, `scripts/build-kata-vm-control.sh`,
`patches/kata/4.2.0/overlay/src/runtime/virtcontainers/zoo_vm_control.go`,
`patches/kata/4.2.0/overlay/src/runtime/virtcontainers/zoo_vm_control_test.go`,
`patches/kata/4.2.0/overlay/src/runtime/pkg/containerd-shim-v2/zoo_vm_control_linux.go`.
The adjacent `zoo_vm_control_linux_test.go` is adapted for mandatory epoch binding.
No files deleted; no parent runtime, SQL, defaults or existing chart files edited.
