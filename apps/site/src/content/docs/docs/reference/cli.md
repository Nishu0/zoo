---
title: CLI
description: Sandbox commands and flags generated from the Python SDK CLI.
---

:::note
Generated from the code by `apps/site/scripts/generate_reference.py` — do not edit by hand.
Regenerate with `bun run docs:gen` from `site/`.
:::

Install with `pip install 'zoo-sdk[cli]'`, then run `zoo login`.
`ZOO_URL` and `ZOO_API_KEY` override the stored login; `ZOO_WORKSPACE` selects a workspace.

## `zoo login`

Store the server URL and an API key in ~/.config/zoo/config.toml.

```text
zoo login [--url VALUE] [--api-key VALUE]
```

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `--url` | `str` | `'http://localhost:8000'` | The API's URL. |
| `--api-key` | `str` | `''` | From Settings → API keys. |

## `zoo create`

Create a sandbox.

```text
zoo create [--kind VALUE] [--name VALUE] [--label VALUE] [--wait/--no-wait] [--json]
```

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `--kind` | `str` | `'desktop'` | desktop, code, browser, macos or windows. |
| `--name` | `str \| None` | `None` | A name; one is made up by default. |
| `--label` | `list[str]` | `[]` | key=value; repeat for more. |
| `--wait/--no-wait` | `bool` | `True` | Wait until it runs. |
| `--json` | `bool` | `False` | Print JSON. |

## `zoo ls`

List sandboxes.

```text
zoo ls [--label VALUE] [--json]
```

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `--label` | `list[str]` | `[]` | Only those with key=value; repeat for more. |
| `--json` | `bool` | `False` | Print JSON. |

## `zoo exec`

Run a command in a sandbox, streaming its output; exits with the command's exit code.

```text
zoo exec SANDBOX_ID -- COMMAND... [--timeout VALUE]
```

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `SANDBOX_ID` | `str` | `required` |  |
| `COMMAND...` | `list[str]` | `required` | The command, after --. |
| `--timeout` | `int` | `300` | Seconds before it is stopped (at most 300). |

## `zoo shell`

An interactive shell in a sandbox (Linux).

```text
zoo shell SANDBOX_ID
```

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `SANDBOX_ID` | `str` | `required` |  |

## `zoo view`

Open a sandbox's live screen in the browser (through a short-lived viewer link).

```text
zoo view SANDBOX_ID [--control/--no-control] [--minutes VALUE] [--print]
```

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `SANDBOX_ID` | `str` | `required` |  |
| `--control/--no-control` | `bool` | `True` | Allow typing and clicking, not just watching. |
| `--minutes` | `int` | `60` | How long the link works. |
| `--print` | `bool` | `False` | Print the link instead of opening it. |

## `zoo cp`

Copy a file into (./a <id>:b) or out of (<id>:b ./a) a sandbox. Remote paths are relative to its home.

```text
zoo cp SOURCE DESTINATION
```

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `SOURCE` | `str` | `required` |  |
| `DESTINATION` | `str` | `required` |  |

## `zoo logs`

A sandbox's recent tool calls and, with -f, its events (state changes) as they happen.

```text
zoo logs SANDBOX_ID [--follow] [--limit VALUE] [--json]
```

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `SANDBOX_ID` | `str` | `required` |  |
| `--follow, -f` | `bool` | `False` | Then follow its events. |
| `--limit` | `int` | `20` | How many recent tool calls. |
| `--json` | `bool` | `False` | Print JSON. |

## `zoo rm`

Delete sandboxes.

```text
zoo rm SANDBOX_IDS... [--wait/--no-wait]
```

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `SANDBOX_IDS...` | `list[str]` | `required` |  |
| `--wait/--no-wait` | `bool` | `False` | Wait until it's gone. |

## `zoo backups`

List database backups in object storage (admin only).

```text
zoo backups [--json]
```

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `--json` | `bool` | `False` | Print JSON. |

## Deployment and backups

`zoo backups [--json]` lists existing backups through `GET /v1/admin/backups` with an admin key.
There is no restore CLI or live destructive restore API. Restore remains a deliberate offline
administrator operation with API, worker and gateway stopped; see [backups](/docs/guides/domain-https-backups/).
Use `docker compose` or `helm` directly for deployment and upgrades, not sandbox CLI server commands.
See [upgrading](/docs/guides/upgrading/) and the [install guide](/docs/get-started/install/).
