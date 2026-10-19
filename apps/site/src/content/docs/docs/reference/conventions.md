---
title: API conventions
description: Versions, errors, idempotency keys, pagination and events — how every Zoo endpoint behaves.
---

## Versions

The API is served under `/v1`: `https://zoo.example.com/v1/sandboxes`.

Within a version, changes only add: new endpoints, new optional fields, new error codes. Anything else ships as a new
version. Clients should ignore fields they don't know.

Health checks (`/healthz`, `/readyz`), the embed page (`/embed/...`) and the guest connection
(`/guest/connect`) are not versioned. MCP uses `/mcp/`.

## Errors

Every error is `{"error": {"code", "message", "hint", "docs_url", "request_id"}}`; `code` is stable. A 500 never
includes exception text, only a `request_id` (also the `X-Request-Id` header) to look up in the logs. Every code is
listed in [Errors](/docs/reference/errors/).

## Idempotency keys

Send `Idempotency-Key: <unique string>` (up to 255 characters) with a `POST` to make retrying it safe: the first
response is kept for 24 hours and a repeat gets it again, with `Idempotent-Replayed: true`, instead of creating a
second sandbox.

| Repeat | Answer |
| --- | --- |
| same key, same body, first one done | the first response |
| same key, different body or path | `409 idempotency_key_reused` |
| same key while the first still runs | `409 idempotency_key_in_progress` |

Keys belong to the caller (its credentials and workspace). Responses with a 5xx status, streams and binary responses
aren't kept, so retrying those runs the request again. The SDKs add a key to every create
(`POST /v1/sandboxes`, snapshots, viewer links, webhooks, API keys) automatically.

## Pagination

`GET /v1/sandboxes`, `/v1/sandboxes/{id}/executions`, `/v1/audit-logs` and `/v1/webhooks/{id}/deliveries` return a
page:

```json
{ "data": [ ... ], "next_cursor": "MjAyNi0xMC0xMlQ..." }
```

Pass `?limit=` (1–200, default 50) and `?cursor=<next_cursor>` for the next page; `next_cursor` is `null` on the last.
Lists are newest first, and a cursor is a position, not an offset, so rows added meanwhile don't shift pages.

## Events

`GET /v1/events/stream` is a server-sent event stream of the workspace's events (including `sandbox.state`, server/node changes, pool changes and job progress); reconnect with `Last-Event-ID` to get what you
missed. The same events go to [webhooks](/docs/guides/building-on-zoo/). The dashboard uses this stream instead of
polling.

## Timeouts and retries

The SDKs time out every call (30 s unless the call passes its own) and retry with exponential backoff and jitter:
connection failures and `429` (honouring `Retry-After`) always; `5xx` and timeouts only for `GET`/`PUT`/`DELETE` and
for creates carrying an idempotency key. Do the same in your own clients.
