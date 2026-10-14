---
title: Quickstart
description: Install a Docker/Kata host, create a sandbox, and connect an external client.
---

## 1. Prepare a Linux KVM host

Use a dedicated Debian/Ubuntu Linux host with working hardware or nested virtualization,
writable `/dev/kvm`, systemd and KSM. Missing virtualization is a hard error, not a weaker
isolation mode. See [Install](/docs/get-started/install/) for the complete requirements.

```bash
curl -fsSL https://github.com/chann44/zoo/releases/latest/download/install.sh | sudo bash -s -- --admin-email you@example.com
```

Docker is the default Linux runtime. Kubernetes is an explicit install choice, not an
automatic migration or alternate path on failure.

## 2. Create a sandbox

Open the printed dashboard URL, normally `http://HOST:3000`, and sign up. Emails in
`ADMIN_EMAILS` may use administrator endpoints. Create a desktop, browser or code sandbox,
then create an API key under your profile. macOS and Windows need enrolled VM hosts.

## 3. Use the sandbox API

```bash
pip install 'zoo-sdk[cli]'
zoo login --url http://localhost:8000
zoo create --kind desktop
zoo ls
```

```python
from zoo_sdk import Zoo

with Zoo().sandbox(kind="desktop") as box:
    image = box.screenshot()
```

The SDK reads `ZOO_API_KEY` and `ZOO_URL`, and adds the `/v1` API prefix. For external
MCP clients, use [the MCP guide](/docs/get-started/mcp/). Zoo supplies sandboxes and tools;
it does not host a model, planner, task runner or chat integration.

## Before relying on a deployment

This implementation is not a runtime acceptance or benchmark report. Real Docker/Kata,
CSI snapshots, VM saved-state slots, pause/resume and performance have not been validated on
real infrastructure.
[Performance](/docs/reference/performance/) lists targets, not achieved results.
