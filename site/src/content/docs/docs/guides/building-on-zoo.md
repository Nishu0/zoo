---
title: Building on Zoo
description: Embed and share sandbox screens, take over from the agent, separate end users with labels, follow events and webhooks, meter usage, export trajectories and benchmark models.
---

For apps that give their own customers sandboxes and agents: embedding a sandbox's screen, keeping each end user's
sandboxes apart, following what happens, billing for it, and turning agent runs into training and evaluation data.

## Embedding and sharing the screen

A viewer link opens a sandbox's screen without a Zoo account. Make one on your server, with your API key, and give the
URL to the browser:

```bash
curl -X POST $ZOO/sandboxes/$ID/viewer-links -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"mode": "control", "expires_in_minutes": 60, "end_user": "alice@example.com"}'
# {"id": ..., "url": "https://zoo.example.com/embed/zv_...", "token": "zv_...", "expires_at": ...}
```

- `view` links only watch. The API drops their keyboard and mouse input itself, whatever the page does.
- `control` links can type and click. Acting while the agent runs takes the screen over from it (below).
- Links last up to `ZOO_MAX_VIEWER_LINK_HOURS` (24). `DELETE /sandboxes/{id}/viewer-links/{link_id}` revokes one, and
  its open viewers are disconnected within 15 seconds.
- Only the origins in `ZOO_EMBED_ORIGINS` (space-separated) may put the page in an iframe; with it unset, none may.

The URL works on its own or in an iframe:

```html
<iframe src="https://zoo.example.com/embed/zv_..." style="width:100%;height:600px;border:0"></iframe>
```

The page posts `{source: "zoo", type}` messages to its parent (`connect`, `disconnect`, `takeover`, `release`, and
`agent` with `state`: `idle`, `running` or `paused`) and takes `{source: "zoo", type: "takeover" | "release"}`. In React,
`@zoo/react` (`sdk/react`) wraps it:

```tsx
import { ZooViewer } from "@zoo/react"

<ZooViewer ref={viewer} url={link.url} onEvent={(e) => console.log(e)} />
// viewer.current.takeover(), viewer.current.release()
```

The dashboard makes links under a sandbox's **Share** tab.

## Taking over from the agent

Anyone who can use the sandbox, or holds a control link, can pause a running agent and use the screen themselves:

- `POST /sandboxes/{id}/agent/takeover` and `/agent/release` (or `POST /embed/{token}/takeover` and `/release`)
- in the dashboard, **Take over** / **Hand back** next to the agent's Stop button
- or just act on the screen through a control link: a key press or a click while the agent runs takes it over

The agent stops before its next action and waits; the run's time limit doesn't count the wait. When the screen is
handed back it takes a fresh look and carries on. The run shows `paused_at` and `paused_by` meanwhile.

## End users: labels, scoped keys and label quotas

Label sandboxes with whatever identifies your customers:

```bash
curl -X POST $ZOO/sandboxes -d '{"kind": "desktop", "labels": {"end_user": "alice"}}' ...
curl "$ZOO/sandboxes?label=end_user=alice" ...          # filter (repeat label= to match all)
curl -X PATCH $ZOO/sandboxes/$ID -d '{"labels": {"end_user": "bob"}}' ...   # replace them
```

Keys are 1-63 of `a-z 0-9 _ . / -`; values 1-255 characters; at most 20 labels. A sandbox carries them into usage,
events and exports.

An API key can be limited to a label selector (`POST /api-keys` with `"label_selector": {"end_user": "alice"}`, or
**Labelled sandboxes** in Settings → API keys). Such a key sees only Alice's sandboxes (others are 404), puts her
label on every sandbox it makes (a request for another value is 403), and reaches only sandboxes, `/usage` and
`/events`. Give each end user's backend session its own key.

Workspace admins set limits per label value, or a default for every value of a key with `"value": "*"`:

```bash
curl -X PUT $ZOO/workspaces/$WS/label-quotas \
  -d '{"key": "end_user", "value": "*", "max_running_sandboxes": 1, "max_cpus": 2}' ...
```

A create or start that would pass one returns 409 naming the label. Workspace quotas still apply on top.

## Events and webhooks

Every workspace has an event log:

| Event | Data |
| --- | --- |
| `sandbox.state` | `status`, `previous`, `error` |
| `run.started` | `source`, `model` |
| `run.step` | `message_id`, `action` |
| `run.finished` | `state`, `error`, `steps`, `tokens`, `cost` |
| `takeover.started`, `takeover.ended` | `by`: a user id, or `link:<viewer link id>` |

`GET /events?after=<seq>` pages through it; `GET /events/stream` follows it as server-sent events. Each event's SSE id is
its `seq`, so an `EventSource` that reconnects (or `?after=`) gets what it missed. Events are kept
`ZOO_EVENT_RETENTION_DAYS` (7).

Webhooks (Settings → Webhooks, or `POST /webhooks` as an admin) get the same events as signed POSTs:

```bash
curl -X POST $ZOO/webhooks -d '{"url": "https://example.com/zoo", "types": ["run.finished"]}' ...
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
marked failed; `GET /webhooks/{id}/deliveries` shows each try, and `POST /webhooks/{id}/test` sends a `webhook.test`
event. Webhook URLs must resolve to public addresses unless `ZOO_WEBHOOK_ALLOW_PRIVATE=1`.

## Usage metering

Zoo meters, per sandbox and hour:

| Metric | What |
| --- | --- |
| `cpu_minutes`, `memory_mb_minutes` | a running sandbox's allocated size (not measured use), sampled every minute |
| `storage_gb_hours` | each sandbox's disk and snapshots, and the workspace's profiles (sandbox `""`), hourly |
| `agent_steps`, `agent_tokens`, `agent_cost` | as agent runs record them |

```bash
curl "$ZOO/usage?from=2026-10-01T00:00:00Z&to=2026-11-01T00:00:00Z&group_by=label:end_user" ...
curl "$ZOO/usage/export?from=...&to=...&group_by=day" ...   # CSV
```

`group_by` is `sandbox`, `day` or `label:<key>`; `metric=` narrows it. Usage stays after a sandbox is deleted, with its
labels. Admins see the workspace's; a key with a label selector sees its own.

## Choosing the model per run

A run can name its provider and model; otherwise it uses the workspace's agent settings:

```bash
curl -X POST $ZOO/sandboxes/$ID/agent -d '{"message": "...", "provider": "xai", "model": "grok-4"}' ...
```

Providers: `anthropic`, `openai`, `gemini`, `openrouter`, `ollama` (local vision models, with `api_base`) and `xai`.
The key comes from the workspace's vault secret `AGENT_<PROVIDER>_API_KEY`, else the server's environment
(`ANTHROPIC_API_KEY`, `XAI_API_KEY`, ...); a run with neither is refused with 400. `GET /agent/providers` lists them and
whether the workspace has a key for each.

## Trajectory exports

```bash
curl -X POST $ZOO/agent/exports -d '{"labels": {"end_user": "alice"}, "from": "2026-10-01T00:00:00Z"}' ...
curl $ZOO/agent/exports/$EXPORT ...                            # until "state": "succeeded"
curl -o runs.tar.gz $ZOO/agent/exports/$EXPORT/download ...
```

Pick one `run_id`, or finished runs by `sandbox_id`, `labels` and `from`/`to` (at most 1000). The archive holds:

- `trajectory.jsonl`, a line per step: `run_id`, `sandbox_id`, `step`, `timestamp`, `instruction`, `screenshot` and
  `a11y_tree` (paths in the archive), `action`, `result` (what the agent said after it), `model`, `provider`
- `runs.jsonl`, a line per run: its outcome, steps, tokens and cost
- `frames/<run id>/<step>.png` and `<step>.a11y.json`

Accessibility trees are recorded as the agent works, unless a workspace admin turns it off
(`PUT /workspaces/{id}/trajectories` with `{"capture_a11y": false}`). Exports are kept `ZOO_EXPORT_RETENTION_DAYS` (7).

## Benchmarks

Run a task suite against several models, each task in its own fresh sandbox:

```yaml
name: office basics
tasks:
  - id: save-a-file
    instruction: Open the text editor, type hello and save it as ~/hello.txt
    setup:
      commands: ["rm -f ~/hello.txt"]
    check:
      command: grep -q hello ~/hello.txt
  - id: dark-theme
    instruction: Switch the desktop to the dark theme
    setup:
      snapshot_id: 3f1c...          # start from a snapshot (Linux sandboxes)
    check:
      judge: Is the desktop using a dark theme?
    timeout: 300
```

```bash
ZOO_URL=... ZOO_API_KEY=... python -m server.bench suite.yaml \
  --models anthropic:claude-sonnet-5-5,openai:computer-use-preview --parallel 4
```

or `POST /benchmarks` with `{"suite": "<yaml>", "models": [{"provider": "anthropic"}], "parallel": 4}`, then
`GET /benchmarks/{id}` for each model's pass rate, mean steps, cost and time, and every task's result
(`/export` as CSV). A command check passes on its exit code (`expect_exit_code`, 0); a judge check asks the
workspace's agent model about the final screen. Sandboxes are labelled `benchmark=<id>` and deleted as each task
finishes; workspace quotas cap how many run at once. `POST /benchmarks/{id}/cancel` stops one.
