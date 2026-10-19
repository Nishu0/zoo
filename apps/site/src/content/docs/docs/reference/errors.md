---
title: Errors
description: Every HTTP status the Zoo API returns, generated from its OpenAPI schema and handlers, with a fix for each.
---

:::note
Generated from the code by `apps/site/scripts/generate_reference.py` — do not edit by hand.
Regenerate with `bun run docs:gen` from `site/`.
:::

Every error the API returns has one shape:

```json
{"error": {"code": "sandbox_not_found", "message": "sandbox not found",
           "hint": "`GET /v1/sandboxes` lists this workspace's sandboxes; ids are case-sensitive.",
           "docs_url": "…/errors#sandbox_not_found", "request_id": "3f9c…"}}
```

Match on `code`: it is stable within an API version, while `message` may change. A `500` never carries
exception text; report its `request_id` (also in the `X-Request-Id` header) and the operator finds the
traceback in the logs. The SDKs raise a `ZooError` subclass per status (`NotFoundError`, `ConflictError`,
`RateLimitError`, …) carrying `code`, `message`, `hint` and `docs_url`.

## Codes

### `bad_request`

HTTP `400`. The request was malformed. Check the body against the endpoint's schema.

### `unauthenticated`

HTTP `401`. No or invalid credentials. Send `Authorization: Bearer <key>`.

Fix: Create an API key in Settings → API keys and send it as `Authorization: Bearer <key>`.

### `forbidden`

HTTP `403`. Your role, API key scope or a sandbox policy doesn't allow this.

### `not_found`

HTTP `404`. No such route or object.

### `sandbox_not_found`

HTTP `404`. No sandbox with that id in this workspace. `GET /v1/sandboxes` lists them.

Fix: `GET /v1/sandboxes` lists this workspace's sandboxes; ids are case-sensitive.

### `method_not_allowed`

HTTP `405`. The route exists but not with this method.

### `create_failed`

HTTP `503`. No warm sandbox could be handed over in time. Retry.

Fix: Retry the create; a cold create starts once no warm sandbox is left.

### `conflict`

HTTP `409`. The object's current state doesn't allow this, e.g. the sandbox isn't running.

### `quota_exceeded`

HTTP `409`. A workspace, member or label quota is used up.

Fix: Stop or delete sandboxes, or ask a workspace admin to raise the quota.

### `idempotency_key_reused`

HTTP `409`. This Idempotency-Key was used with a different request body.

Fix: Use a new Idempotency-Key for a different request.

### `idempotency_key_in_progress`

HTTP `409`. A request with this Idempotency-Key is still running.

Fix: Wait for the first request to finish, then retry.

### `gone`

HTTP `410`. The object existed but has expired or been revoked.

### `too_large`

HTTP `413`. The request body is too large.

### `validation_failed`

HTTP `422`. A field didn't match the schema; the message names it.

### `rate_limited`

HTTP `429`. Too many requests. Wait as long as the Retry-After header says.

Fix: Retry after the number of seconds in the Retry-After header.

### `internal_error`

HTTP `500`. Zoo failed. Retry; if it keeps failing, report the request id.

Fix: Retry. If it keeps failing, report the request id to your Zoo operator.

### `not_supported`

HTTP `501`. This runtime or deployment doesn't support it.

### `upstream_error`

HTTP `502`. A sandbox, node or provider Zoo talks to failed.

### `unavailable`

HTTP `503`. Zoo or a dependency is starting or overloaded. Retry with backoff.

Fix: Retry with exponential backoff.

### `guest_unavailable`

HTTP `503`. The sandbox's guest agent isn't connected, and every tool goes through it.

Fix: the sandbox's guest agent is not connected; restart the sandbox

### `timeout`

HTTP `504`. A sandbox, node or provider didn't answer in time.

Fix: Retry; long commands can pass a larger `timeout`.

## Statuses

Where each HTTP status is declared or raised.

| Status | Where it comes from |
| --- | --- |
| `200` | `GET /embed/{token}`, `GET /v1/admin/backups`, `GET /v1/admin/domains`, +93 more |
| `201` | `POST /v1/admin/backups`, `POST /v1/admin/domains`, `POST /v1/api-keys`, +10 more |
| `202` | `POST /v1/sandboxes/{sandbox_id}/snapshots`, `POST /v1/webhooks/{webhook_id}/test` |
| `204` | `DELETE /v1/admin/domains/{domain_id}`, `PUT /v1/auth/me/onboarded`, `DELETE /v1/profiles/{profile_id}`, +9 more |
| `400` | raised by the handlers |
| `401` | raised by the handlers |
| `403` | raised by the handlers |
| `404` | raised by the handlers |
| `409` | raised by the handlers |
| `422` | `GET /embed/{token}`, `GET /v1/admin/backups`, `POST /v1/admin/backups`, +120 more |
| `429` | raised by the handlers |
| `500` | raised by the handlers |
| `502` | raised by the handlers |
| `503` | raised by the handlers |
