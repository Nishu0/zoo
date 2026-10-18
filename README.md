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

Zoo gives AI agents their own computers: Linux, macOS or Windows desktops, browsers and shells. Linux sandboxes run in
Kata VMs or ordinary Docker containers, macOS in Virtualization.framework and Windows in Hyper-V. Agents drive them over MCP, REST or the Python SDK;
you watch, and take over when needed, from the dashboard.

## Quickstart

1. Install Zoo. On Linux (Ubuntu or Debian) or macOS:

   ```bash
   curl -fsSL https://github.com/chann44/zoo/releases/latest/download/install.sh | sudo bash -s -- --admin-email you@example.com
   ```

   On Windows, in PowerShell:

   ```powershell
   powershell -ExecutionPolicy Bypass -c "& ([scriptblock]::Create((irm https://github.com/chann44/zoo/releases/latest/download/install.ps1))) -AdminEmail 'you@example.com'"
   ```

   On Linux with KVM, Linux sandboxes run as Kata micro-VMs. Without KVM, and on macOS and Windows (which need
   [Docker Desktop](https://www.docker.com/products/docker-desktop/) running), they run as plain Docker containers.
   On a Mac or Windows machine, the installer also adds that machine for macOS or Windows sandboxes and prints the
   admin's password; on Windows, run it as administrator (or pass `-NoNode` to skip). Add remote Linux, Mac or
   Windows servers under Servers > Add server.

2. Open the dashboard address the installer prints (port 3000) and sign up. The first run walks you through creating a desktop sandbox, copying an
   API key and copying an MCP config with the key filled in.
3. Paste that config into Claude Desktop, Claude Code or Cursor, or drive sandboxes from code:

   ```bash
   pip install 'zoo-sdk[cli]'
   zoo login && zoo create --kind desktop && zoo ls
   ```

   ```python
   from zoo_sdk import Zoo

   with Zoo().sandbox(kind="desktop") as box:
       print(box.exec("uname -a")["stdout"])
   ```

Docker is the default Linux runtime. Kubernetes is opt-in and runs Cloud Hypervisor on amd64 with node-local storage;
Kata needs KVM. Linux, Mac, and Windows hosts join as nodes; hosts with Linux Docker engines add
Linux desktop, Firefox browser, and code capacity alongside supported native VMs. The full walkthrough is in the
[quickstart](site/src/content/docs/docs/get-started/quickstart.md).

## Documentation

The [docs](site/src/content/docs/docs/index.mdx) are built with the site at `/docs`:

- **Get started**: [Quickstart](site/src/content/docs/docs/get-started/quickstart.md) · [Install](site/src/content/docs/docs/get-started/install.md) · [Your first sandbox](site/src/content/docs/docs/get-started/first-sandbox.md) · [Connect Claude Code or Claude Desktop](site/src/content/docs/docs/get-started/mcp.md)
- **Guides**: Linux, Mac, and Windows hosts, native sandboxes, [policies](site/src/content/docs/docs/guides/policies.md), vault, HTTPS and backups, upgrading, Kubernetes, [building on Zoo](site/src/content/docs/docs/guides/building-on-zoo.md)
- **Concepts**: architecture, security model, runtimes
- **Reference**: REST API, tools, SDK, CLI, configuration, errors, [performance targets](site/src/content/docs/docs/reference/performance.md)

Development and contributing: [docs/development.md](docs/development.md). Security: [SECURITY.md](SECURITY.md).

Repository walkthroughs: [Components and their uses](docs/components.mmd) · [API, tools, and security map](docs/architecture-map.md).
