---
title: Security model
description: What each layer enforces, and what each OS can and can't guarantee.
---

Policies are set per sandbox in the dashboard or through the API, and enforced **outside**
the sandbox wherever it matters. Every tool call is stored with its input, output and
status, and shown in the Activity tab.

| Control | How it is enforced |
| --- | --- |
| Tool permissions (`shell.exec`, `screen.read`, `input.control`, `files.read`, `files.write`) | Checked by the API before every tool call. A denied call returns 403. |
| Network policy (default allow/deny, DNS on/off, domain, IP and CIDR rules) | On the host, outside the sandbox: one `zoo-egress` container per Docker host runs nftables rules for each sandbox, a proxy that decides HTTP and TLS by Host header and server name (so domain rules hold when IPs change), and a DNS resolver that only answers allowed names. Sandboxes have no `NET_ADMIN` or raw sockets, so even root inside can't change or get around it. Stopping the daemon leaves its rules in place, so traffic fails closed. |
| App policy | A root service in the sandbox (`zoo-guest -apps`) reads the policy from a root-only file, makes denied programs root-only and kills running copies. |
| Secrets | Envelope encryption: each workspace has its own data key, wrapped by `ZOO_SECRETS_KEY` or an external KMS (`ZOO_KMS`: AWS KMS, Google Cloud KMS or Vault transit). Injected as environment variables when the sandbox starts, and pushed to running sandboxes through the guest agent when they change. Secrets can carry an expiry date and a rotation interval; the Vault page, `GET /vault/reminders` and the worker's log flag the ones expiring or due. |
| Chat channels | Webhook signatures are verified on every request. Only the user IDs on a channel's allowlist can command its sandbox. |
| Kernel | Kata Containers puts each sandbox in its own VM. On the runc fallback, sandboxes get a tighter seccomp filter and, on AppArmor hosts, the `zoo-sandbox` profile (`deploy/security/`). |
| Live view | The dashboard opens the VNC websocket with a single-use ticket that expires after 30 seconds (`POST /sandboxes/{id}/vnc-ticket`), never the session token. x11vnc listens only inside the sandbox and has a per-sandbox password that only the API holds; the API logs in with it and offers the browser no auth. |
| Rate limits | `/auth/login`: 20 a minute per IP and 10 a minute per email. `/auth/signup`: 10 an hour per IP. Each API key: 600 requests a minute. Over the limit returns 429 with `Retry-After`. Counts are kept in memory, per API process. |
| Audit | Every tool call is stored with its input, output and status, and shown in the Activity tab. |

Policy changes apply immediately to a running sandbox and are applied again on every
start. Secret changes apply right away too: new commands and terminals see them; programs
already running keep the environment they started with. A sandbox that can't be reached
gets the change on its next start.

## What each OS guarantees

Each OS draws the line in a different place. This is the honest list of what holds and
what doesn't.

### Linux (Kata)

Each sandbox is its own microVM with its own kernel — a break out of the sandbox's kernel
is a break into a throwaway VM, not the host. No `NET_ADMIN`, no raw sockets, `no-new-privileges`,
2 GB memory / 2 CPUs / 1024 processes, unprivileged `zoo` user: even root inside can't
undo the host-enforced network policy. On the runc fallback there is no kernel boundary —
containers share the host's — so sandboxes get a tighter seccomp filter and, on AppArmor
hosts, the `zoo-sandbox` profile instead.

### macOS

The VM runs as a standard user without sudo, so an agent can't undo the VM's pf rules or
app policy, and the Mac enforces the real network policy with pf and a proxy outside the
VM entirely. **Admin sandboxes** (opt-in) get sudo back for tasks that need it — with it,
an agent can change the VM's address, which the Mac's pf rules match, so give admin
sandboxes the strictest policy on that Mac, or a Mac of their own.

### Windows

The guest user **is an administrator**, so an agent with `shell.exec` can undo app policy
and the VM's own firewall rules. It **can't** undo the host's port ACLs and proxy, so
network policy holds. Clones share the base VM's computer name and machine SID — don't
join them to a domain.

### Kubernetes

Sandboxes are pods under the Kata RuntimeClass with the same limits; `NET_RAW` is dropped
and privilege escalation is off. On `runc` clusters, sandboxes get the runtime's default
seccomp profile rather than Zoo's stricter one and its AppArmor profile, and there is no
per-sandbox pids limit (the kubelet's `podPidsLimit` applies per node).

## Things to know

- Domain network rules are resolved to IPs when the rule is applied; if a site changes
  IPs, re-apply the policy or restart the sandbox.
- On remote servers the noVNC port is published on the address you configure; x11vnc
  behind it asks for the sandbox's password, which only the API has.
- Secrets are visible to `execute_command` and terminals, not to GUI apps.

See [known limitations](https://github.com/chann44/zoo/blob/main/docs/limitations.md) for
the current short list, and the [comparison page](/compare) for how this stacks against
hosted sandboxes.
