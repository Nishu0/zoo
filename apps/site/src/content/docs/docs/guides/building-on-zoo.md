---
title: Building on Zoo
description: Embed and share sandbox screens, separate end users with labels, follow events and webhooks, and meter usage.
---

For apps that give their own customers sandboxes: embedding a sandbox's screen, keeping each end user's sandboxes
apart, following what happens, and billing for it.

## Embedding and sharing the screen

A viewer link opens a sandbox's screen without a Zoo account. Make one on your server, with your API key, and give the
URL to the browser:

```bash
curl -X POST $ZOO/v1/sandboxes/$ID/viewer-links -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"mode": "control", "expires_in_minutes": 60, "end_user": "alice@example.com"}'
# {"id": ..., "url": "https://zoo.example.com/embed/zv_...", "token": "zv_...", "expires_at": ...}
```

- `view` links only watch. The API drops their keyboard and mouse input itself, whatever the page does.
- `control` links can type and click.
- Links last up to `ZOO_MAX_VIEWER_LINK_HOURS` (24). `DELETE /v1/sandboxes/{id}/viewer-links/{link_id}` revokes one, and
  its open viewers are disconnected within 15 seconds.
- Only the origins in `ZOO_EMBED_ORIGINS` (space-separated) may put the page in an iframe; with it unset, none may.

The URL works on its own or in an iframe:

```html
<iframe
  src="https://zoo.example.com/embed/zv_..."
  style="width:100%;height:600px;border:0"
></iframe>
```

The page posts `{source: "zoo", type: "connect" | "disconnect"}` messages to its parent. In React, `@zoo/react`
(`sdk/react`) wraps it:

```tsx
import { ZooViewer } from "@zoo/react";

<ZooViewer url={link.url} onEvent={(e) => console.log(e)} />;
```

The dashboard makes links under a sandbox's **Share** tab.

## End users: labels, scoped keys and label quotas

Label sandboxes with whatever identifies your customers:

```bash
curl -X POST $ZOO/v1/sandboxes -d '{"kind": "desktop", "labels": {"end_user": "alice"}}' ...
curl "$ZOO/v1/sandboxes?label=end_user=alice" ...          # filter (repeat label= to match all)
curl -X PATCH $ZOO/v1/sandboxes/$ID -d '{"labels": {"end_user": "bob"}}' ...   # replace them
```

Keys are 1-63 of `a-z 0-9 _ . / -`; values 1-255 characters; at most 20 labels. A sandbox carries them into usage,
events and exports.

An API key can be limited to a label selector (`POST /v1/api-keys` with `"label_selector": {"end_user": "alice"}`, or
**Labelled sandboxes** in Settings → API keys). Such a key sees only Alice's sandboxes (others are 404), puts her
label on every sandbox it makes (a request for another value is 403), and reaches only sandboxes, `/v1/usage` and
`/v1/events`. Give each end user's backend session its own key.

Workspace admins set limits per label value, or a default for every value of a key with `"value": "*"`:

```bash
curl -X PUT $ZOO/v1/workspaces/$WS/label-quotas \
  -d '{"key": "end_user", "value": "*", "max_running_sandboxes": 1, "max_cpus": 2}' ...
```

A create or start that would pass one returns 409 naming the label. Workspace quotas still apply on top.

## Events and webhooks

Every workspace has an event log:

| Event           | Data                          |
| --------------- | ----------------------------- |
| `sandbox.state` | `status`, `previous`, `error` |

`GET /v1/events?after=<seq>` pages through it; `GET /v1/events/stream` follows it as server-sent events. Each event's SSE id is
its `seq`, so an `EventSource` that reconnects (or `?after=`) gets what it missed. Events are kept
`ZOO_EVENT_RETENTION_DAYS` (7).

Webhooks (Settings → Webhooks, or `POST /v1/webhooks` as an admin) get the same events as signed POSTs:

```bash
curl -X POST $ZOO/v1/webhooks -d '{"url": "https://example.com/zoo", "types": ["sandbox.state"]}' ...
# {"id": ..., "secret": "whsec_..."}   the secret is shown once
```

Check each delivery before trusting it:

```python
import hashlib, hmac, time


def verify(secret: str, headers, body: bytes) -> bool:
    timestamp = headers["Zoo-Timestamp"]
    expected = "v1=" + hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    fresh = abs(time.time() - int(timestamp)) < 300
    return fresh and hmac.compare_digest(expected, headers["Zoo-Signature"])
```

Answer 2xx within 10 seconds. Failures are retried after 1 minute, 5 minutes, 30 minutes, 2 hours and 6 hours, then
marked failed; `GET /v1/webhooks/{id}/deliveries` shows each try, and `POST /v1/webhooks/{id}/test` sends a `webhook.test`
event. Webhook URLs must resolve to public addresses unless `ZOO_WEBHOOK_ALLOW_PRIVATE=1`.

## Usage metering

Zoo meters, per sandbox and hour:

| Metric                             | What                                                                                   |
| ---------------------------------- | -------------------------------------------------------------------------------------- |
| `cpu_minutes`, `memory_mb_minutes` | a running sandbox's allocated size (not measured use), sampled every minute            |
| `storage_gb_hours`                 | each sandbox's disk and snapshots, and the workspace's profiles (sandbox `""`), hourly |

```bash
curl "$ZOO/v1/usage?from=2026-10-01T00:00:00Z&to=2026-11-01T00:00:00Z&group_by=label:end_user" ...
curl "$ZOO/v1/usage/export?from=...&to=...&group_by=day" ...   # CSV
```

`group_by` is `sandbox`, `day` or `label:<key>`; `metric=` narrows it. Usage stays after a sandbox is deleted, with its
labels. Admins see the workspace's; a key with a label selector sees its own.
