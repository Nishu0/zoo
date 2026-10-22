<p align="center">
  <img src="assets/logo.png" alt="Zoo" width="320">
</p>

<p align="center">
  Self-hosted sandboxes for AI agents.
</p>

<p align="center">
  <a href="https://github.com/chann44/zoo/releases/latest"><img src="https://img.shields.io/github/v/release/chann44/zoo?color=4a8a9e&label=" alt="release"></a>
  <a href="https://hub.docker.com/r/chann44/zoo-api"><img src="https://img.shields.io/docker/pulls/chann44/zoo-api?color=4a8a9e&label=pulls" alt="docker pulls"></a>
</p>

Run Linux, macOS and Windows sandboxes. Connect agents through MCP, REST or an SDK;
watch and control their desktops from your browser.

## Quickstart

Install on Debian/Ubuntu, macOS or Windows. Mac and Windows installations need
[Docker Desktop](https://www.docker.com/products/docker-desktop/) running.

**Linux / macOS**

```bash
curl -fsSL https://github.com/chann44/zoo/releases/latest/download/install.sh | sudo bash -s -- --admin-email you@example.com
```

**Windows** — run PowerShell as administrator:

```powershell
powershell -ExecutionPolicy Bypass -c "& ([scriptblock]::Create((irm https://github.com/chann44/zoo/releases/latest/download/install.ps1))) -AdminEmail 'you@example.com'"
```

Open the dashboard URL the installer prints. Sign in with the printed credentials
or create an account, then create a sandbox. Add more hosts under **Remote Servers**.

Linux desktop, Firefox browser and code sandboxes use a Linux Docker engine.
Native macOS sandboxes require Apple Silicon; Windows sandboxes require Hyper-V.

## Connect an agent

Copy the dashboard's MCP configuration into your agent client, or use the CLI:

```bash
pip install 'zoo-sdk[cli]'
zoo login
zoo create --kind desktop
zoo ls
```

SDKs: [Python](sdk/python/README.md) · [TypeScript](sdk/typescript/README.md) · [React](sdk/react/README.md)

## Performance

Built to agent-latency budgets, measured against the public sandbox benchmark
suites ([methodology + numbers](benchmarks/README.md)):

- Warm tool calls round-trip in ~14 ms; sandbox create to first command
  ≈ 125 ms on Docker.
- macOS sandboxes suspend to a saved state on stop and **resume in seconds —
  memory, files and the guest agent come back intact** (`ZOO_MACOS_SUSPEND=0`
  restores plain restarts; `ZOO_MACOS_MEMORY_MB` trades speed for capacity).
- `POST /v1/sandboxes/{id}/exec/batch` runs up to 25 commands in order over one
  HTTP round trip, `stop_on_error` halts on the first failure.
- Two code images: `zoo-sandbox-code` (full coding-agent stack) and
  **`zoo-sandbox-code-slim`** (379 MB, tool-call-only workloads) — select per
  deployment with `ZOO_CODE_IMAGE`.

Keep Docker disk usage lean with `make hygiene`.

## Documentation

[Quickstart](site/src/content/docs/docs/get-started/quickstart.md) ·
[Installation](site/src/content/docs/docs/get-started/install.md) ·
[MCP setup](site/src/content/docs/docs/get-started/mcp.md) ·
[All docs](site/src/content/docs/docs/index.mdx)

[Development](docs/development.md) · [Components](docs/components.mmd) ·
[Architecture](docs/architecture-map.md) · [Security](SECURITY.md)
