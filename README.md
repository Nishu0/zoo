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
shells, each in its own microVM. Agents drive them over MCP, REST or a Python SDK. You
watch, and take over when needed, from the dashboard.

## Quickstart

On an Ubuntu or Debian server with KVM:

```bash
# 1. install — Docker, Kata microVMs, the dashboard and the zoo CLI
curl -fsSL https://github.com/chann44/zoo/releases/latest/download/install.sh | sudo bash -s -- --admin-email you@example.com

# 2. connect your agent — any MCP client gets every sandbox tool
cat > .mcp.json <<'JSON'
{ "mcpServers": { "zoo": { "type": "http", "url": "http://localhost:8000/mcp/",
    "headers": { "Authorization": "Bearer zoo_YOUR_KEY" } } } }
JSON

# 3. drive a sandbox from Python
pip install ./sdk/python
python -c "from zoo_sdk import Zoo; box = Zoo().create('hello'); print(box.exec('uname -a')['stdout'])"
```

Open `http://<server ip>:3000` to watch. No KVM? The installer falls back to `runc` and
tells you.

## Documentation

**[docs](site/src/content/docs/docs/index.mdx)** — built with the site at `/docs`:

- **Get started**: [Install](site/src/content/docs/docs/get-started/install.md) · [Your first sandbox](site/src/content/docs/docs/get-started/first-sandbox.md) · [Connect Claude Code or Claude Desktop](site/src/content/docs/docs/get-started/mcp.md) · [A computer-use agent in 5 minutes](site/src/content/docs/docs/get-started/cua-agent.md) · [Run Claude Code in a sandbox](site/src/content/docs/docs/get-started/claude-code.md)
- **Guides**: remote servers, macOS and Windows sandboxes, [policies](site/src/content/docs/docs/guides/policies.md), vault, chat channels, HTTPS and backups, upgrading, Kubernetes, [building on Zoo](site/src/content/docs/docs/guides/building-on-zoo.md) (embedding, end users, events and webhooks, metering, trajectories, benchmarks)
- **Concepts**: architecture, security model, runtimes
- **Reference** (generated from the code): REST API, tools, SDK, configuration, errors

Development and contributing: [docs/development.md](docs/development.md).
