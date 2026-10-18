---
title: Isolation
description: Docker isolation modes, Kata on Kubernetes, and native Mac/Windows VMs.
---

## Linux isolation

Docker supports three `ZOO_ISOLATION` modes: `auto` prefers Kata when available and otherwise
uses ordinary Linux containers; `kata` requires runtime `io.containerd.kata.v2`; `runc` uses
ordinary containers. This applies to local and enrolled remote Docker engines. Ordinary
containers share their engine's Linux kernel. Docker Desktop runs that engine in a Linux
VM; it does not provide a separate VM boundary for each ordinary container.

Kubernetes uses RuntimeClass `kata` and requires hardware virtualization. The pinned
Kubernetes Cloud Hypervisor configuration is amd64-only; Docker's supported Kata QEMU shim
also serves arm64. Strict Kata mode requires a correctly configured engine and working KVM.

macOS runs through Virtualization.framework on an enrolled Apple Silicon host. Windows
runs through Hyper-V on an enrolled host. Both host types can also run Linux desktop, Firefox browser, and code sandboxes through a Linux Docker engine.

## What the boundary protects

Kata and native VM sandboxes have a separate guest kernel. Ordinary Docker containers
share the engine kernel and provide a different isolation boundary. Sandbox processes do
not receive runtime sockets or control-plane credentials. Guest tools and the VNC path pass through the
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
