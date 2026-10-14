<p align="center">
  <img src="assets/logo.png" alt="Zoo" width="420">
</p>

<p align="center">
  Self-hosted <b>sandboxes</b> for <b>AI agents</b> 🤖🖥️🐧
</p>

<p align="center">
  <a href="https://github.com/chann44/zoo/releases/latest"><img src="https://img.shields.io/github/v/release/chann44/zoo?color=4a8a9e&label=" alt="release"></a>
  <a href="https://hub.docker.com/r/chann44/zoo-api"><img src="https://img.shields.io/docker/pulls/chann44/zoo-api?color=4a8a9e&label=pulls" alt="docker pulls"></a>
  <a href="https://github.com/chann44/zoo"><img src="https://img.shields.io/github/stars/chann44/zoo?style=social" alt="GitHub stars"></a>
</p>

Zoo gives AI agents their own computers: Linux, macOS or Windows desktops, browsers and
shells: Linux uses Kata VMs, macOS uses Virtualization.framework and Windows uses Hyper-V.
External agents drive them over MCP, REST or a Python SDK. You
watch, and take over when needed, from the dashboard.

## Quickstart

On an Ubuntu or Debian server with KVM:

```bash
# 1. install — Docker, Kata microVMs and the dashboard
curl -fsSL https://github.com/chann44/zoo/releases/latest/download/install.sh | sudo bash -s -- --admin-email you@example.com
```

2. Open `http://<server ip>:3000` and sign up. The first run walks you through creating a desktop sandbox,
   copying an API key and copying an MCP config with the key filled in.
3. Paste that config into Claude Desktop, Claude Code or Cursor, or drive sandboxes from code:

```bash
pip install 'zoo-sdk[cli]'
zoo login && zoo create --kind desktop && zoo ls
```

```python
from zoo_sdk import Zoo

with Zoo().sandbox(kind="desktop") as box:  # deleted when the block ends
    print(box.exec("uname -a")["stdout"])
```

Docker is the default, local-only Linux runtime using Kata QEMU. Opt-in Kubernetes uses
Cloud Hypervisor on amd64 with node-local storage. Both require KVM. Mac/Windows hosts
join for native guests; saved state belongs to a reserved unique slot, not an arbitrary clone.
The whole path is in the
[quickstart](site/src/content/docs/docs/get-started/quickstart.md).

## Documentation

**[docs](site/src/content/docs/docs/index.mdx)** — built with the site at `/docs`:

- **Get started**: [Quickstart](site/src/content/docs/docs/get-started/quickstart.md) · [Install](site/src/content/docs/docs/get-started/install.md) · [Your first sandbox](site/src/content/docs/docs/get-started/first-sandbox.md) · [Connect Claude Code or Claude Desktop](site/src/content/docs/docs/get-started/mcp.md)
- **Guides**: Mac and Windows hosts, native sandboxes, [policies](site/src/content/docs/docs/guides/policies.md), vault, HTTPS and backups, upgrading, Kubernetes, [building on Zoo](site/src/content/docs/docs/guides/building-on-zoo.md) (embedding, end users, events and webhooks, metering)
- **Concepts**: architecture, security model, runtimes
- **Reference** (generated from the code): REST API, tools, SDK, CLI, configuration, errors. [Performance targets](site/src/content/docs/docs/reference/performance.md) are not measured results

Development and contributing: [docs/development.md](docs/development.md).
