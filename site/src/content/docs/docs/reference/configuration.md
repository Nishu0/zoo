---
title: Configuration
description: Canonical deployment settings from Python and native components.
---

:::note
Generated from the code by `site/scripts/generate_reference.py` — do not edit by hand.
Regenerate with `bun run docs:gen` from `site/`.
:::

Canonical deployment settings, parsed from Python and native source. Removed interfaces
are not configurable: guest addresses are derived, and the gateway is a native service.
Set deployment values
in `/opt/zoo/.env` (the installer writes `JWT_SECRET` and `ZOO_SECRETS_KEY`) or in the
chart's documented values on Kubernetes. Peer RPCs use TLS on 7444; peer HTTP uses
TLS on 8002; guest websockets use 8001; native node enrollment uses mTLS on 7443.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ADMIN_EMAILS` | `—` | Comma-separated emails allowed to use `/admin/*` and Domains. |
| `AWS_ACCESS_KEY_ID` | `—` | Credentials for `ZOO_OBJECT_STORE` (with `AWS_SECRET_ACCESS_KEY`). |
| `AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE` | `—` | Internal setting. |
| `AWS_CONTAINER_CREDENTIALS_FULL_URI` | `—` | Internal setting. |
| `AWS_DEFAULT_REGION` | `—` | Internal setting. |
| `AWS_REGION` | `—` | Internal setting. |
| `AWS_ROLE_ARN` | `—` | Internal setting. |
| `AWS_ROLE_SESSION_NAME` | `zoo` | Internal setting. |
| `AWS_SECRET_ACCESS_KEY` | `—` | Credentials for `ZOO_OBJECT_STORE` (with `AWS_ACCESS_KEY_ID`). |
| `AWS_SESSION_TOKEN` | `—` | Internal setting. |
| `AWS_WEB_IDENTITY_TOKEN_FILE` | `—` | Internal setting. |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000` | Allowed dashboard origins. |
| `DATABASE_URL` | `—` | A `postgresql://` URL (required). |
| `DOCKER_HOST` | `unix:///var/run/docker.sock` | Internal setting. |
| `GOOGLE_APPLICATION_CREDENTIALS` | `—` | Google Cloud credentials, for the `gcp:` KMS. |
| `HOST` | `127.0.0.1` | API server bind address. The API image sets `0.0.0.0`. |
| `JWT_SECRET` | `—` | Signs sessions. |
| `KUBECONFIG` | `—` | Kubeconfig used outside a cluster. |
| `KUBERNETES_SERVICE_HOST` | `—` | Internal setting. |
| `KUBERNETES_SERVICE_PORT` | `443` | Internal setting. |
| `NODE_NAME` | `—` | Internal setting. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `—` | Enables OpenTelemetry traces, metrics and logs. |
| `OTEL_SERVICE_NAME` | `zoo-api` | Service name in traces. |
| `PORT` | `self.port` | API server port. |
| `RELOAD` | `1` | Dev autoreload. The API image sets `0`. |
| `VAULT_ADDR` | `—` | HashiCorp Vault address, for the `vault:` KMS. |
| `VAULT_NAMESPACE` | `—` | HashiCorp Vault namespace, for the `vault:` KMS. |
| `VAULT_TOKEN` | `—` | HashiCorp Vault token, for the `vault:` KMS. |
| `ZOO_API_URL` | `—` | API URL the dashboard calls, read at container start. Also baked into installer commands and join tokens. |
| `ZOO_API_WORKERS` | `—` | API processes (uvicorn workers); defaults to the CPU count. Ignored while reloading from source. |
| `ZOO_BACKUP_INTERVAL_HOURS` | `24` | Internal setting. |
| `ZOO_BACKUP_KEEP` | `7` | Internal setting. |
| `ZOO_BROWSER_HOME` | `https://duckduckgo.com` | Start page for `browser` sandboxes. |
| `ZOO_CODE_IMAGE` | `zoo-code:latest` | Image for `code` sandboxes. |
| `ZOO_DOCS_URL` | `https://github.com/chann44/zoo/blob/main/site/src/content/docs/docs/reference/errors.md` | Base of the errors page that error responses link to as `docs_url` (`<url>#<code>`). |
| `ZOO_DOMAIN` | `—` | Always-allowed domain for Caddy. |
| `ZOO_EMBED_ORIGINS` | `—` | Origins (space-separated) that may put a viewer link's page in an iframe; none when unset. |
| `ZOO_EVENT_RETENTION_DAYS` | `7` | How long events (`GET /events`, webhooks) and their deliveries are kept. |
| `ZOO_GATEWAY_GRPC_PORT` | `7444` | Authenticated TLS RPC port shared by API clients and gateway replicas; default 7444. |
| `ZOO_GATEWAY_HTTP_PORT` | `8001` | Guest websocket and health listener; default 8001. Public ingress routes /guest/connect here. |
| `ZOO_GATEWAY_IP` | `—` | This gateway replica's own IP; must occur in peer DNS. Use the downward pod IP on Kubernetes or explicit loopback for source development. |
| `ZOO_GATEWAY_NODE_PORT` | `7443` | Internal setting. |
| `ZOO_GATEWAY_PEERS` | `gateway` | Gateway peer DNS name, not a URL: gateway in Compose, headless service DNS on Kubernetes, localhost for source development. |
| `ZOO_GATEWAY_PEER_HTTP_PORT` | `8002` | Private TLS HTTP listener for gateway-to-gateway relays; default 8002. Do not expose it as a public API. |
| `ZOO_GATEWAY_SERVICE` | `zoo-gateway` | Internal setting. |
| `ZOO_GUEST_DARWIN_BINARY` | `computed` | Path of the darwin zoo-guest build to install into macOS VMs. |
| `ZOO_GUEST_WINDOWS_BINARY` | `computed` | Internal setting. |
| `ZOO_IDLE_MEMORY_MB_CODE` | `250` | Idle memory request for code pods in MiB; defaults to 250. |
| `ZOO_IDLE_MEMORY_MB_DESKTOP` | `700` | Idle memory request for desktop/browser pods in MiB; defaults to 700. |
| `ZOO_INSTALL_URL` | `https://github.com/chann44/zoo/releases/latest/download` | Where installers download release assets from. |
| `ZOO_KMS` | `—` | Wrap workspace data keys with an external KMS: `aws:<key ARN>`, `gcp:…` or `vault:<mount>/<key>`. |
| `ZOO_KUBERNETES_CONTEXT` | `—` | Kubeconfig context to use in-cluster-less setups. |
| `ZOO_KUBERNETES_EGRESS_NAMESPACE` | `—` | Namespace of the egress daemon (default: the sandbox namespace). |
| `ZOO_KUBERNETES_EGRESS_SELECTOR` | `app.kubernetes.io/component=egress` | Label selector of the egress daemon's pods. |
| `ZOO_KUBERNETES_HELPER_IMAGE` | `—` | Helper image for snapshot and move jobs. |
| `ZOO_KUBERNETES_HOME_SIZE` | `10Gi` | Size of each home claim. |
| `ZOO_KUBERNETES_IMAGE_PULL_SECRET` | `—` | Pull secret for sandbox and helper pods. |
| `ZOO_KUBERNETES_PROBE_IMAGE` | `registry.k8s.io/pause:3.10` | What the requirements check starts on each node. |
| `ZOO_KUBERNETES_START_TIMEOUT` | `600` | Seconds to wait for a sandbox pod to start. |
| `ZOO_MACOS_BASE` | `zoo-macos-base` | zoovm VM that macOS sandboxes are cloned from. |
| `ZOO_MACOS_CPUS` | `4` | CPUs of each macOS sandbox. |
| `ZOO_MACOS_MEMORY_MB` | `8192` | Memory of each macOS sandbox. |
| `ZOO_MACOS_USER` | `admin` | Guest user for macOS sandboxes. |
| `ZOO_MAX_SANDBOX_CPUS` | `8` | Internal setting. |
| `ZOO_MAX_SANDBOX_DISK_GB` | `200` | Internal setting. |
| `ZOO_MAX_SANDBOX_MEMORY_MB` | `16384` | Internal setting. |
| `ZOO_MAX_SNAPSHOTS` | `10` | Snapshots kept per sandbox. |
| `ZOO_MAX_VIEWER_LINK_HOURS` | `24` | Longest a viewer link may last. |
| `ZOO_METRICS_PORT` | `—` | Serves Prometheus metrics on this port at `/metrics`. |
| `ZOO_NAMESPACE` | `zoo` | Namespace for Kubernetes sandbox pods and home claims. |
| `ZOO_NETWORK` | `—` | Docker network shared by the API and sandboxes. Compose sets it to `zoo`. |
| `ZOO_NODE_DIR` | `—` | Internal setting. |
| `ZOO_NODE_DIST` | `—` | zoo-node builds served to installers and pushed to nodes on another version. |
| `ZOO_NODE_HOST` | `—` | Public node endpoint hostname or IP without a port; defaults to the API URL's hostname. Set separately for a node load balancer. |
| `ZOO_NODE_PORT` | `7443` | Native gateway's public zoo-node mTLS port; default 7443. Distinct from private gateway RPCs. |
| `ZOO_OBJECT_STORE` | `—` | Required. `s3://<bucket>[/<prefix>]`: profiles, portable home snapshots and database backups. |
| `ZOO_PAUSE_MINUTES` | `30` | How long a stopped Linux sandbox stays paused (resumable) before it is deleted to its volume; defaults to 30. |
| `ZOO_PUBLIC_IP` | `—` | This server's public address(es), used to check that a domain's DNS points here. |
| `ZOO_ROLE` | `—` | Python process role: all, api or worker. The gateway is a separate native Go process. |
| `ZOO_RUNTIME` | `docker` | Linux backend: `docker` (default) or `kube`. Both use Kata virtual machines. |
| `ZOO_S3_ENDPOINT` | `—` | S3-compatible endpoint for `ZOO_OBJECT_STORE` (MinIO, R2). |
| `ZOO_S3_REGION` | `—` | Region for `ZOO_OBJECT_STORE`. |
| `ZOO_SANDBOX_DISK_GB` | `20` | Internal setting. |
| `ZOO_SANDBOX_IMAGE` | `zoo-sandbox:latest` | Image for `desktop` and `browser` sandboxes. |
| `ZOO_SECRETS_KEY` | `—` | Wraps workspace keys that encrypt secrets, app profiles and VNC passwords. Back it up. |
| `ZOO_SECRETS_KEY_PREVIOUS` | `—` | Old keys, comma-separated, kept only until `make rotate-secrets` has run. |
| `ZOO_SNAPSHOT_CLASS` | `zoo-snapshots` | CSI snapshot class used before home archives are uploaded; defaults to `zoo-snapshots`. |
| `ZOO_SPARE_VOLUMES` | `4` | Unbound default-size home claims kept ready per Kubernetes sandbox node; defaults to 4. |
| `ZOO_STORAGE_CLASS` | `zoo-local` | StorageClass of home claims; defaults to `zoo-local`. |
| `ZOO_VM_CONTROL_CLIENT_CERT` | `—` | Controller leaf certificate file with the exact api or worker URI matching ZOO_ROLE; do not reuse one role's leaf for the other. |
| `ZOO_VM_CONTROL_CLIENT_KEY` | `—` | Private key file for the process's VM-control controller leaf. |
| `ZOO_VM_CONTROL_DAEMONSET` | `—` | Expected VM-control DaemonSet owner name. |
| `ZOO_VM_CONTROL_DOCKER_ENROLL_TOKEN` | `—` | Internal setting. |
| `ZOO_VM_CONTROL_DOCKER_NAMESPACE` | `—` | Actual Docker containerd namespace, normally moby; not the Zoo sandbox network. |
| `ZOO_VM_CONTROL_DOCKER_URL` | `—` | Explicit HTTPS frontend URL for the authenticated single-host VM controller. |
| `ZOO_VM_CONTROL_NAMESPACE` | `—` | Kubernetes sandbox/VM-control namespace used for authenticated discovery. |
| `ZOO_VM_CONTROL_NODE_CA` | `—` | Public node/server CA file used to verify VM-control frontends; not the gateway CA. |
| `ZOO_VM_CONTROL_NODE_CA_KEY` | `—` | Node signing key file mounted only in the dedicated issuer, not normal API/worker or frontend pods. |
| `ZOO_VM_CONTROL_NODE_NAME` | `—` | Provisioned single-Docker-host node identity name. |
| `ZOO_VM_CONTROL_NODE_UID` | `—` | Immutable generation UUID for the single Docker host. |
| `ZOO_VM_CONTROL_SELECTOR` | `—` | Exact installation VM-control DaemonSet Pod label selector. |
| `ZOO_VM_CONTROL_SERVER_NAME` | `—` | Expected VM-control frontend DNS SAN; node URI/generation is also verified. |
| `ZOO_VM_CONTROL_SERVICE_ACCOUNT` | `—` | Expected VM-control node enrollment/discovery ServiceAccount. |
| `ZOO_VM_CONTROL_TRUST_DOMAIN` | `—` | Installation-specific SPIFFE trust domain; separate from the node-enrollment CA. |
| `ZOO_WEBHOOK_ALLOW_PRIVATE` | `—` | `1` lets webhooks reach private addresses. |
| `ZOO_WEBHOOK_BACKOFF_SECONDS` | `60,300,1800,7200,21600` | Seconds between a failed webhook delivery's retries, comma-separated; it fails after the last. |
| `ZOO_WINDOWS_BASE` | `zoo-windows-base` | Hyper-V VM that Windows sandboxes are cloned from. |
| `ZOO_WINDOWS_CPUS` | `4` | CPUs of each Windows sandbox. |
| `ZOO_WINDOWS_MAX_VMS` | `4` | Windows sandboxes running at once on each server. |
| `ZOO_WINDOWS_MEMORY_MB` | `8192` | Memory of each Windows sandbox. |

Undocumented in this table: `DOCKER_HOST`, `KUBERNETES_SERVICE_HOST`, `KUBERNETES_SERVICE_PORT`, `NODE_NAME`, `ZOO_BACKUP_INTERVAL_HOURS`, `ZOO_BACKUP_KEEP`, `ZOO_GATEWAY_NODE_PORT`, `ZOO_GATEWAY_SERVICE`, `ZOO_GUEST_WINDOWS_BINARY`, `ZOO_MAX_SANDBOX_CPUS`, `ZOO_MAX_SANDBOX_DISK_GB`, `ZOO_MAX_SANDBOX_MEMORY_MB`, `ZOO_NODE_DIR`, `ZOO_SANDBOX_DISK_GB`, `ZOO_VM_CONTROL_DOCKER_ENROLL_TOKEN`.

