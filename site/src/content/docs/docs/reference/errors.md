---
title: Errors
description: Every HTTP status the Zoo API returns, generated from its OpenAPI schema and handlers, with a fix for each.
---

:::note
Generated from the code by `site/scripts/generate_reference.py` — do not edit by hand.
Regenerate with `bun run docs:gen` from `site/`.
:::

Every HTTP status the API can return, and where it is declared or raised. The SDK
raises `zoo_sdk.ZooError` with `"<status>: <detail>"` for any of them.

| Status | Where it comes from |
| --- | --- |
| `200` | `GET /`, `POST /auth/login`, `GET /auth/me`, +112 more |
| `201` | `POST /auth/signup`, `POST /api-keys`, `POST /sandboxes`, +12 more |
| `202` | `POST /sandboxes/{sandbox_id}/snapshots`, `POST /webhooks/{webhook_id}/test`, `POST /agent/exports`, +1 more |
| `204` | `DELETE /sandboxes/{sandbox_id}/snapshots/{snapshot_id}`, `DELETE /sandboxes/{sandbox_id}/agent`, `POST /sandboxes/{sandbox_id}/agent/stop`, +15 more |
| `400` | raised by the handlers |
| `401` | raised by the handlers |
| `403` | raised by the handlers |
| `404` | raised by the handlers |
| `409` | raised by the handlers |
| `422` | `POST /auth/signup`, `POST /auth/login`, `GET /auth/me`, +148 more |
| `429` | raised by the handlers |
| `500` | raised by the handlers |
| `502` | raised by the handlers |
| `503` | raised by the handlers |
| `504` | raised by the handlers |

## What each one means, and the fix

### `400` — The request was malformed

Check the body against the endpoint's schema in the [API reference](/docs/reference/api/).

### `401` — No or invalid credentials

Send `Authorization: Bearer <key>`; create a key under **Profile → API keys**. Session cookies only work on the dashboard origin.

### `403` — Denied by a policy, or not an admin

Tool calls: check the sandbox's **Permissions** tab (`shell.exec`, `screen.read`, `input.control`, `files.read`, `files.write`). `/admin/*` and Domains need `ADMIN_EMAILS`.

### `404` — Unknown sandbox, server or object

`GET /sandboxes` to list what exists; ids are case-sensitive.

### `409` — Conflict with the sandbox's current state

Typical causes: the name is taken, a `macOS` sandbox hit *Mac full* (wait, or create with `"queue": false`), or an app is running while a profile loads (quit the app, then load).

### `422` — Validation failed

FastAPI's response names the field that didn't match the schema; compare with the endpoint's request body in the [API reference](/docs/reference/api/).

### `429` — Rate limited

Honor `Retry-After`. Limits: 20 login attempts a minute per IP, 10 an hour for signups, 600 requests a minute per API key.

### `500` — The API hit an unexpected error

Check the API logs (and Grafana, if enabled); open an issue with the trace id if it persists.

### `502` — A hop in front of the API failed

Check the reverse proxy (Caddy) and that the API container is up.

### `503` — The sandbox or guest is unreachable

The sandbox may still be starting, its server offline, or (on remote sandboxes) `ZOO_GUEST_REMOTE_URL` unset. Retry once the sandbox is running.

### `504` — A call timed out behind a proxy

Long tool calls can outlive proxy timeouts; raise the proxy's read timeout, or retry.

