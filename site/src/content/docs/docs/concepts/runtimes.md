---
title: Isolation
description: Kata VMs on both Linux backends and native VMs on Mac/Windows hosts.
---

## A VM boundary for every sandbox

Docker and Kubernetes are deployment choices, not isolation levels. Both Linux backends
use Kata and require hardware virtualization. Docker uses `io.containerd.kata.v2`;
Kubernetes uses RuntimeClass `kata`. The pinned Kubernetes Cloud Hypervisor configuration
is amd64-only; Docker's supported QEMU shim also serves arm64.

macOS runs through Virtualization.framework on an enrolled Apple Silicon host. Windows
runs through Hyper-V on an enrolled host. Linux capacity is never borrowed from those hosts.

## What the boundary protects

Sandbox processes may have broad privileges inside their own VM without receiving the
host kernel or control-plane credentials. Guest tools and the VNC path pass through the
authenticated gateway; desktop ports are not published directly.

A VM boundary is not a promise that every hypervisor, kernel or device has no vulnerability.
Keep host, shim, guest images and helper binaries patched. Access to runtime sockets,
VM-control credentials and VM disks remains a privileged administrative boundary.

## Policy is outside the guest

Linux egress enforcement runs on the Docker host or each Kubernetes node. VM hosts use
their native firewall/ACL enforcement plus the host proxy. Denied traffic must fail even
when a guest process is compromised. Do not grant runtime sockets or the egress controller
to a sandbox.

## Storage and paused memory

Persistent homes, snapshots and secret handling differ by backend; see [Architecture](/docs/concepts/architecture/).
Stopping is not a data backup. A paused VM may still occupy RAM; free-page reporting,
actual memory pressure and measured backend behavior determine reclamation.

Cloud Hypervisor uses virtio-fs without DAX or VM templating. Whole-VM pause needs a
matching patched rootful Go shim/controller, not a workload signal. The custom shim includes
QEMU and Cloud Hypervisor adapters; stock Kata does not expose this Zoo-specific protocol.

## Validation boundary

Real escape-resistance, pause/resume, disk recovery and saved-state compatibility checks
are deferred. Static checks and target configuration are not runtime isolation certification.
