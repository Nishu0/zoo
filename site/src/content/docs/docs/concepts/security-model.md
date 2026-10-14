---
title: Security model
description: VM boundaries, least privilege, native identity, secrets and explicit trust boundaries.
---

## Sandbox boundary

Every Linux sandbox has a Kata guest kernel. macOS and Windows have native VM boundaries.
The API never treats absence of a required guest as permission to execute tools another way.
Guest disconnection fails tool requests with `guest_unavailable`.

API keys, workspace membership, scoped permissions and sandbox policies authorize requests
before dispatch. Keep the public API behind trusted TLS, and keep private gateway RPC/peer
listeners off the public internet.

## Native host enrollment

Only macOS and Windows enroll through zoo-node. Join tokens are short-lived and single-use;
the token binds the control plane and its CA. The native node stream uses mTLS on 7443.
An HTTP ingress is not a substitute for that stream's public address.

The node CA, encrypted private material and VM-host identity live in Postgres. The API
supplies the host key needed by its VM helper; do not replace database identity with a
shared directory of private keys. Host helper operations travel over authenticated node
transport rather than opening a generic host backend.

## Secrets

Per-workspace data keys wrap stored secret values. Protect `ZOO_SECRETS_KEY`, prior keys
needed during rotation, external KMS access and database backups together. Losing wrapping
keys can make a restored database's secrets and identities unreadable.

Secrets are pushed through the required guest, not exposed in an image or public screen
connection. New commands can observe updates; already running processes may retain the
environment they started with.

## Egress and privileged controllers

The host egress daemon enforces policies outside the guest. Kubernetes also needs the
configured namespace network policy and compatible CNI routing. VM-control has a separate
privileged broker and narrowly authorized TLS frontend; its identity must not be
interchangeable with enrolled VM-host certificates.

Protect Docker/containerd sockets, hypervisor control sockets, CSI administration and
the host's VM disks. They can cross VM boundaries even when mounted read-only.

## Viewer links and audit

Viewer links have explicit view/control scope and expiry. Only allowed origins can embed
them; a view-only link does not gain input privileges because a browser sends an input event.
Tool/audit/event records describe sandbox operations, not recorded model reasoning or
replayable built-in tasks.

## What is not claimed

Implementation/static checks are not a penetration-test result. Isolation, controller
authorization under failure, saved-state handling and recovery need deferred runtime
validation. See [Performance](/docs/reference/performance/) for unmeasured targets.
