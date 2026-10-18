---
title: Your first sandbox
description: Create, view and operate an isolated sandbox through the dashboard and external clients.
---

Open the printed dashboard URL and sign up. Emails in `ADMIN_EMAILS` have administrator
abilities; an API key is shown only once.

## Choose a kind

- `desktop`: Linux XFCE desktop with screen/input, shell and file tools.
- `browser`: Firefox-only Linux view with tabs and an address bar, using the dedicated browser image.
- `code`: Linux shell/files without a graphical desktop.
- `macos` / `windows`: the native kind on a prepared, enrolled VM host.

Linux kinds run on the selected Linux Docker engine or Kubernetes runtime. Docker isolation follows `ZOO_ISOLATION`; Kata provides a separate guest kernel, while ordinary containers share their engine kernel. Homes persist on the selected runtime's home
volume/claim; native data has platform-specific disk/state dependencies. Do not assume a
Linux home path, UID or resource default applies to every OS.

## Use it

Live view and tools share the gateway's VNC session. The terminal and other in-sandbox
tools require zoo-guest. Policies and permissions apply to every request; missing guest
capabilities fail rather than switching execution paths.

Use **Activity** for tool records, **Monitoring** for measured resource numbers,
**Snapshots** for runtime-specific data snapshots, and **Profiles** for portable app data.
Zoo does not run a built-in model or task planner; connect an external client through
[REST, SDK or MCP](/docs/get-started/mcp/).

## Stop and resume

A confirmed supported VM pause can leave a stopped sandbox marked resumable until the
configured deadline. Later teardown keeps home data for a cold start. Unsupported or
unverified VM-control wiring must not be presented as a successful whole-VM pause.
Deleting a sandbox removes its associated data according to the runtime's lifecycle.

Timing, recovery and platform saved-state acceptance are deferred; do not infer a startup
guarantee from a ready warm-pool item or static checks.
