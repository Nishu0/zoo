---
title: HTTPS, backups and observability
description: Public TLS, independent native node endpoints and offline database restoration.
---

## Public endpoints and trust

With `--domain`, Docker uses Caddy; Kubernetes uses ingress/cert-manager. Configure
`ZOO_API_URL` as the public HTTP(S) API root, including any deployment path prefix.
Public `/guest/connect` traffic routes to the native gateway guest listener on 8001.

Private gateway RPC 7444 and peer HTTP 8002 use authenticated TLS. Native host streams use
mTLS on 7443, on a separately reachable node address if needed. `ZOO_NODE_HOST` selects that
public hostname/IP without a port; it is not a URL or the internal peer-service DNS name.

VM guests and hosts need their own working DNS and trust configuration. Offline/self-signed
certificates must be trusted by those clients explicitly. A working browser does not prove
a guest can connect. Guest addresses are derived, not independently configurable.

## What to back up

Retain Postgres contents, wrapping keys/KMS access, object-store credentials and data,
runtime home/snapshot data and native slot/template dependencies. The node CA and VM-host
identity are database-backed. Never restore a database while discarding the keys that
decrypt its stored secrets and identities.

Scheduled backup work is separate from runtime-specific snapshots. CSI snapshots and
saved memory do not become a portable full-system backup merely because a database dump
exists.

## List database backups

```bash
zoo backups --json
```

This is an authenticated administrator request to `GET /v1/admin/backups`. There is no
`zoo` restore command and no live destructive restore API.

## Offline database restore

Drain workload activity and **stop the API, every worker and every gateway**. Keep the
database/object store available to the offline administrator operation. Supply the
matching release environment, database URL, object-store access and wrapping keys.

For Docker, with `BACKUP_ID` set to a listed backup key:

```bash
docker compose stop api worker gateway
docker compose run --rm --no-deps --entrypoint python api -m server.backups restore "$BACKUP_ID"
```

Inspect the restore result before intentionally starting the control plane again:

```bash
docker compose up -d api worker gateway
```

On Kubernetes, quiesce all API/worker/gateway replicas and their autoscalers/controllers
first; ensure none can restart during restoration. Run the same `python -m server.backups
restore ID` operation from a matching offline administrator environment with database and
object-store access. Record replicas, controllers and versions before stopping them;
restore them only after the database operation and compatibility checks succeed.

Do not delete a namespace or its home claims as a restore technique. Database restoration
does not by itself restore every VM disk, saved state, CSI snapshot or profile.

## Observability and measurement

SSE and webhooks carry durable sandbox/job/state events. Monitoring numbers may poll.
OpenTelemetry and the optional Grafana stack help inspect lifecycle/tool failures and
resource use. Logs/audit entries record sandbox operations, not built-in model tasks.

[Performance](/docs/reference/performance/) distinguishes the planned measurements from
actual results. No new runtime, chaos or benchmark execution has been performed for this
implementation work.
