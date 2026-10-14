---
title: Upgrading
description: Use Compose or Helm directly; database restoration is an offline administrator operation.
---

## Before changing a deployment

Read the release notes, take a database backup, retain the matching encryption keys and
record image/chart versions. Check schema compatibility before rolling back. Neither a
container restart nor Helm rollback is a database rollback.

The Python CLI lists backups only. It does not upgrade a deployment or expose a live
destructive restore command.

## Docker

Use the release-matched Compose/Caddy files and update the installed configuration's
`ZOO_VERSION` deliberately. From the installation directory:

```bash
docker compose pull
docker compose up -d --no-build
docker compose ps
```

The public API, native gateway ports and guest ingress must stay coherent. Preserve the
database, object-store credentials, node CA and encryption keys across the rollout.

## Kubernetes

Keep the chart and image tag on the same release:

```bash
helm get values zoo --namespace zoo > values-current.yaml
helm upgrade zoo oci://registry-1.docker.io/chann44/zoo --namespace zoo \
  --version "$VERSION" --reuse-values --set-string image.tag="$VERSION" --wait
helm status zoo --namespace zoo
```

A known-compatible chart rollback is an operator choice, not an automatic recovery promise:

```bash
helm history zoo --namespace zoo
helm rollback zoo "$REVISION" --namespace zoo --wait
```

Do not roll application code back against an incompatible migrated database.

## Native hosts and slots

Keep zoo-node, zoo-guest, VM helpers and saved-state slot versions consistent. Drain slot
allocations before changing the base or capturing new state. Slot uniqueness and disk/state
compatibility still apply after an update.

## Offline database restore

See [Backups](/docs/guides/domain-https-backups/#offline-database-restore). Stop the API,
every worker and every gateway before invoking the existing administrator-only
`python -m server.backups restore ID` operation in a matching release environment.
Restore is not an HTTP endpoint and is not a CLI deployment fallback.

Real upgrade/rollback/recovery acceptance remains deferred; static manifests alone are
not evidence of a recovered deployment.
