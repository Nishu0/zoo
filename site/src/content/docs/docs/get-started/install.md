---
title: Install
description: Docker by default, Kubernetes by explicit choice, with hardware virtualization required.
---

## Default: Docker on one Linux host

The installer prepares Docker Engine, the pinned Kata Go QEMU shim, KSM, release images,
Postgres, SeaweedFS, a native Go gateway, egress, the Python API/worker and the dashboard.
With `--domain`, Caddy provides the public HTTPS endpoint.

Installed deployments use published Docker images and require no source checkout or
Dockerfiles. Run `docker compose pull` and `docker compose up -d` in the installation
directory to update the configured release. Adding `--build` there has nothing to build;
source builds are available from a development checkout.

```bash
curl -fsSL https://github.com/chann44/zoo/releases/latest/download/install.sh | sudo bash -s -- --admin-email you@example.com
```

Use a dedicated Debian/Ubuntu systemd host. Both amd64 and arm64 Docker installations use
Kata VMs when configured with the Kata runtime. Additional Linux, Mac, and Windows
hosts can enroll through **Servers → Add server**. Each host with a working Linux Docker
engine can run Linux desktop, Firefox browser, and code sandboxes. See [Remote servers](/docs/guides/remote-servers/).

## Requirements checked by the API

- `DATABASE_URL` reachable; `ZOO_OBJECT_STORE` reachable; `JWT_SECRET`; `ZOO_SECRETS_KEY` or `ZOO_KMS`; `ZOO_API_URL`;
  `ZOO_RUNTIME` is `docker` or `kube`.
- Kata requires working hardware virtualization and `/dev/kvm`; ordinary Docker container mode does not.
- `ZOO_RUNTIME=docker`: the local Linux Docker daemon is reachable and sandbox images are present.
  `ZOO_ISOLATION=kata` additionally requires runtime `io.containerd.kata.v2`; `auto` falls back to ordinary containers.
- `ZOO_RUNTIME=kube`: Kubernetes API reachable; RuntimeClass `kata` exists; StorageClass `ZOO_STORAGE_CLASS` (default
  `zoo-local`, TopoLVM) exists; VolumeSnapshotClass `ZOO_SNAPSHOT_CLASS` exists.
- macOS hosts: Apple Silicon, macOS 14+, zoo-node, zoovm, base VM with guest + Accessibility grant.
- Windows hosts: Hyper-V capable edition, zoo-node, base VM with guest.
- Development: Docker runtime by default (`make dev`); the Kubernetes runtime on a local k3s with Kata (`make dev-kube`;
  on a Mac: M3+, macOS 15+, Lima VM with nested virtualization).

Development uses `make dev` on the same Linux KVM host as Docker. M3+ Macs on macOS 15+
can use a nested-virtualization Lima guest for Docker development. **Architecture restriction:** the pinned Kubernetes
Cloud Hypervisor deployment requires amd64; native ARM Lima is not an equivalent setup.
See [development documentation](https://github.com/chann44/zoo/blob/main/docs/development.md).

Startup collects named requirement failures before serving. `/readyz` exposes readiness;
a passing static build does not establish host/runtime readiness.

## Configuration and options

The installer creates its own configuration under the chosen directory, normally
`/opt/zoo`. Back up both the database and encryption keys. Do not reuse placeholder secrets.

- `--runtime docker|kube`: choose the Linux runtime; Docker is the default.
- `--version VERSION`, `--dir PATH`, `--admin-email EMAIL`, `--domain HOST`.
- `--check`: preflight only; do not assume it provisions missing capabilities.
- Kubernetes storage: `--device EMPTY_BLOCK_DEVICE` or `--disk-gb GB` for a development loop file.
- `--bundle PATH`: consume a matching architecture/release archive or its split-parts index.

A loop file is for development, not durable storage. Device preparation is destructive:
supply only the explicitly selected empty block device. For Kubernetes prerequisites and
storage behavior, see [Kubernetes](/docs/guides/kubernetes/).

## Offline bundles

Release bundles contain pinned runtime binaries, charts, manifests and component images,
not just Zoo images. Required host tools must already exist: Python, OpenSSL, zstd, tar,
systemd, iptables and networking tools; Kubernetes also needs LVM/thin-provisioning tools.
Bundle version, architecture, component pins and checksums must match the installer.

Offline HTTPS certificates are not automatically trusted. Browsers, enrolled hosts and
VM guests must trust the issuing CA or use a certificate from an already trusted issuer.
Their DNS must resolve the public API and native node endpoint; host-only address entries
do not configure a guest VM's DNS.

## KSM, supported Kata tuning and whole-VM pause

Enable KSM on every sandbox node and persist that setting at reboot:

```bash
echo 1 | sudo tee /sys/kernel/mm/ksm/run
```

The Kubernetes deployment pins Kata 4.2.0's Go `clh` shim, with RuntimeClass name `kata`
and handler `kata-clh`, on amd64. Its drop-in sets `shared_fs = "virtio-fs"`,
`enable_mem_prealloc = false`, `reclaim_guest_freed_memory = true` and
`enable_template = false`. Disabling templating is an intentional correctness requirement,
not an alternate implementation. The current Cloud Hypervisor filesystem API does not
support DAX/cache windows. Docker keeps the upstream-supported Go QEMU shim.

KSM merges eligible private anonymous pages, not shared file-backed guest RAM. Free-page
reporting reclaims pages a running guest frees; freezing a VM does not itself prove memory
reclamation. Pre-pulling keeps desktop/code image references on eligible nodes.

True VM pause requires the matching Zoo-patched Kata shim and authenticated node-local
VM-control service. Stock workload freezing is not whole-VM pause. The source-pinned rootful
Go shim contains QEMU and Cloud Hypervisor adapters. Matching shim/controller wiring,
generation-bound certificates and real lifecycle validation are
required before advertising resumable VM stop on a backend.

## Validation status

Installer code and static artifacts are not deployment certification. Real installation,
runtime isolation, disk/snapshot recovery, VM pause/resume, saved-state slots and the
[performance targets](/docs/reference/performance/) remain to be validated.
