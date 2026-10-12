# Security model

## Security model

Policies are set per sandbox in the dashboard or through the API.

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

Policy changes apply immediately to a running sandbox and are applied again on every start. Secret changes (vault secrets changed, attached, detached or deleted, and a sandbox's own secrets) also apply right away: on Linux the guest reads `/run/zoo/env.json` for every command; on macOS and Windows every command sources `~/.zoo/env`. New commands and terminals see the change; programs already running keep the environment they started with. A sandbox that can't be reached gets the change on its next start.
