# Zoo API

The Python API and job worker serve versioned REST (`/v1`) and the unprefixed MCP endpoint (`/mcp/`). A separate native Go gateway owns guest/node connections and viewer transport. `ZOO_RUNTIME=docker` uses the local Docker socket, while enrolled hosts add Linux Docker engines through the node tunnel; Docker isolation follows `ZOO_ISOLATION` (Kata or ordinary containers); `kube` uses Kubernetes and amd64 Cloud Hypervisor Kata pods. State lives in Postgres and S3-compatible object storage. The deployment migration job completes before gateway and API startup.

Part of [Zoo](https://github.com/chann44/zoo), self-hosted sandboxes for AI agents. Install a whole stack with:

```bash
curl -fsSL https://github.com/chann44/zoo/releases/latest/download/install.sh | sudo bash
```

## Tags

| Tag | Points at |
| --- | --- |
| `1.4.2`, `1.4`, `1`, `latest` | releases |
| `edge` | the latest commit on `main`, rebuilt nightly with patched base images |
| `sha-<commit>` | one commit on `main` |

Images are built for `linux/amd64` and `linux/arm64` and mirrored to `ghcr.io/chann44/zoo-api`.

## Environment

| Variable | Purpose |
| --- | --- |
| `JWT_SECRET` | signs sessions |
| `ZOO_SECRETS_KEY` | encrypts stored secrets; required for new installs |
| `ZOO_RUNTIME` | `docker` (default) or `kube`; Kubernetes requires Kata |
| `ZOO_SANDBOX_IMAGE`, `ZOO_BROWSER_IMAGE`, `ZOO_CODE_IMAGE` | sandbox images to launch |

## Verify

Every tag is signed with cosign (keyless, from the release workflow) and carries an SBOM and build provenance:

```bash
cosign verify docker.io/chann44/zoo-api:latest \
  --certificate-identity-regexp '^https://github.com/chann44/zoo/\.github/workflows/images\.yml@' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
docker buildx imagetools inspect docker.io/chann44/zoo-api:latest --format '{{ json .SBOM }}'
```
