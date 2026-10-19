---
title: Kubernetes
description: Explicit opt-in k3s/Kata installation, local TopoLVM storage and native gateway peers.
---

## Opt in deliberately

The default install is Docker on one host. Kubernetes is an explicit choice:

```bash
curl -fsSL https://github.com/chann44/zoo/releases/latest/download/install.sh | sudo bash -s -- --runtime kube --admin-email you@example.com --domain zoo.example.com --device /dev/EMPTY_DEVICE
```

The selected block device must be empty, unmounted and dedicated to Zoo. A development
loop file can replace it with `--disk-gb GB`; it is not durable production storage.

The bootstrap pins k3s, kata-deploy, TopoLVM, snapshot controller, cert-manager and the
Zoo chart. It does not make a host without virtualization eligible.

## Supported node requirements

Use amd64 nodes with working KVM for the pinned Kata 4.2.0 Go Cloud Hypervisor package.
RuntimeClass `kata` selects handler `kata-clh`. Enable KSM persistently. The drop-in
uses virtio-fs, free-page reporting and no preallocation; templating is intentionally
disabled and DAX is not enabled.

`zoo-local` is the TopoLVM thin-volume StorageClass. `zoo-snapshots` is the configured
CSI VolumeSnapshotClass. Every eligible node needs its own local capacity and egress
daemon. Native ARM Lima cannot supply this pinned CLH package.

## Data and placement

The control plane chooses an eligible node before binding a home claim. TopoLVM affinity
keeps the data on that node. The spare-claim pool keeps unbound claims ready; a spare is
not permission to schedule a home on a different node.

CSI snapshots are not copies that automatically travel with a pod. Supported moves and
exports require explicit data transfer, compatible destination storage and object storage.
Provision sufficient space for live volumes, thin-pool metadata and snapshots.

## Gateway and public endpoints

The native gateway is a separate image, not a Python role. `ZOO_GATEWAY_PEERS` is the
headless peer-service DNS name. `ZOO_GATEWAY_IP` comes from the pod's downward-API IP.
RPC 7444 and peer HTTP 8002 use private authenticated TLS. Guest HTTP/websockets use 8001.

Public API ingress routes `/guest/connect` to the guest listener. Enrolled hosts use a
separate mTLS node endpoint on 7443. If its load balancer has a different hostname, set
`gateway.nodes.host`; this is a hostname/IP without a port, not the peer-service name.

## Chart values and operations

Configure external database/object-storage secrets or provision the database mode's
operator first. `objectStorage.url` is required. Keep encryption keys stable across upgrades.

```bash
helm upgrade --install zoo oci://registry-1.docker.io/chann44/zoo --namespace zoo --create-namespace \
  --version "$VERSION" -f values.yaml --set-string image.tag="$VERSION" --wait
```

The chart's pre-pull DaemonSet keeps desktop/code images referenced on eligible nodes.
`sandboxes.prepull` controls selectors, tolerations, resource requests and enablement.
Do not disable egress or change the runtime to compensate for a failed prerequisite.

True VM pause additionally needs a matching patched Kata shim and VM-control controller.
The custom rootful Go shim includes a Cloud Hypervisor adapter; stock Kata is insufficient. Runtime checks,
CSI recovery and performance acceptance are deferred; Helm rendering is static validation.

For source development on local k3s, use
[the development guide](https://github.com/chann44/zoo/blob/main/docs/development.md).
