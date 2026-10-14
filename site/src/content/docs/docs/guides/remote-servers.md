---
title: Mac and Windows hosts
description: Enroll native VM hosts with zoo-node; Linux capacity stays in the selected Linux backend.
---

## Supported hosts

Only Apple Silicon Macs running macOS 14+ and Hyper-V-capable Windows hosts enroll as
additional VM hosts. They run their native sandbox kind, not Linux Docker capacity.
Linux capacity is either the one local Docker host or the selected Kubernetes nodes.

## Enroll from the dashboard

Open **Servers → Add server**, choose Mac or Windows and copy the generated command.
It contains a one-time join token, the public control-plane URL and the API-provided host
key. Run it on the intended host under the VM-owning account, with required privileges.

Mac installation uses `install.sh --node --token ... --control-plane ... --key ...`.
Windows uses the generated elevated PowerShell installer command with its token and key.
Do not construct an old operator command or paste a private key into the dashboard.

## Connectivity and identity

zoo-node dials the native gateway's mTLS endpoint on port 7443. The public node hostname
may differ from the API hostname; configure `ZOO_NODE_HOST` for that address. Permit outbound
access and ensure the API URL is reachable by the host and by its VM guests.

Node CA material and host identity are database-backed. Gateway peers use their own
private DNS membership, TLS RPC and peer-relay listeners. The public node address is not
the peer DNS name, and ordinary HTTP ingress does not forward node gRPC.

The node reports host capacity and carries scoped VM-helper operations. It does not expose
a host Docker daemon. Update binaries from the matching release and retain the persisted
CA/enrollment identity.

## Capacity, saved state and moves

Mac capacity is capped at two running macOS VMs per host, including a running base VM.
Windows uses the configured per-host VM cap. A saved-state slot has its own hardware identity,
disk and state; slot reuse is not a license to duplicate one running state into arbitrary
new VM identities or across hosts.

Disk/profile transfer and saved-memory portability are different operations. Follow the
native-platform guides; saved-state acceptance and real cross-host recovery are deferred.
