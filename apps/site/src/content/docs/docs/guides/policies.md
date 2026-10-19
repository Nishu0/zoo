---
title: Network and app policies
description: Tool permissions, network rules and app blocking per sandbox — enforced on the host, outside the sandbox — with recipes.
---

Policies are set per sandbox in the dashboard or through the API, and apply immediately to
a running sandbox. They are enforced **outside** the sandbox, so the agent inside can't
change or get around them.

## Tool permissions

| Permission | Covers |
| --- | --- |
| `shell.exec` | Run shell commands |
| `screen.read` | Take screenshots and list apps |
| `input.control` | Control mouse, keyboard, windows and apps |
| `files.read` | Read files |
| `files.write` | Write, move and delete files |

The API checks permissions before every tool call; a denied call returns `403`. Set them
in the sandbox's **Permissions** tab, or with `box.set_permission(permission, action, effect)`.

## Network policy

Options: a default of allow or deny, DNS on or off, and rules for domains, IPs and CIDRs,
each allow or deny.

Enforcement lives on the host, outside the sandbox:

- one `zoo-egress` container per Docker host runs nftables rules for each sandbox, a proxy
  that decides HTTP and TLS by Host header and server name (so domain rules hold when IPs
  change), and a DNS resolver that only answers allowed names;
- sandboxes have no `NET_ADMIN` or raw sockets, so even root inside can't change or get
  around it;
- stopping the daemon leaves its rules in place, so traffic **fails closed**.

On macOS the Mac's own pf and proxy enforce it (the guest's pf rules are a second layer);
on Windows, Hyper-V port ACLs and a proxy on the host, with Windows Firewall inside as a
second layer. Domain rules are resolved to IPs when the rule is applied — if a site
changes IPs, re-apply the policy or restart the sandbox.

```python
box.set_network("deny", allow_dns=True)
box.add_rule("domain", "github.com")
box.add_rule("ip", "10.0.0.5")
box.add_rule("cidr", "192.168.0.0/16", effect="deny")
```

## App policy

App policy denies specific programs. A root service in the sandbox reads the policy from a
root-only file, makes denied programs root-only and kills running copies — denying Firefox
while it runs kills it within a second and it won't start again as `zoo`. On macOS it
locks `/Applications/<App>.app`; on Windows it blocks the app's `.exe` through Image File
Execution Options, and Store apps with AppLocker (Enterprise and Education guests only).

## Recipe: deny-by-default for a coding agent

The default posture for an autonomous coding agent: no network except what the build
needs.

```python
box.set_network("deny")
box.add_rule("domain", "api.anthropic.com")  # Claude Code
box.add_rule("domain", "github.com")  # code
box.add_rule("domain", "pypi.org")  # Python packages
box.add_rule("domain", "registry.npmjs.org")  # npm packages
```

Keep `allow_dns=True` so names resolve through the filtering resolver — it only answers
allowed names anyway. This is the full posture of `examples/claude_code.py`.

## Recipe: research sandbox that can't phone home

Allow browsing, deny the data exfil targets:

```python
box.set_network("allow")
box.add_rule("domain", "competitor.example.com", effect="deny")  # a specific site
box.add_rule("cidr", "10.0.0.0/8", effect="deny")  # your intranet
```

## Recipe: a read-only observer

A sandbox whose agent watches but can't touch:

```python
box.set_permission("input", "control", "deny")
box.set_permission("files", "write", "deny")
box.set_permission("shell", "exec", "deny")
```

`screen.read` stays, so the agent can still screenshot and list apps.

## On Kubernetes

The same egress daemon runs as a DaemonSet on each node, plus a baseline NetworkPolicy
from the chart (sandboxes reach DNS, the gateway and the internet, but not cloud metadata
or each other). Cilium with eBPF host routing needs `bpf.hostLegacyRouting=true` so the
daemon sees pod traffic. See [Zoo on Kubernetes](/docs/guides/kubernetes/).
