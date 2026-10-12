---
title: zoo CLI
description: Sandboxes from the terminal — login, create, exec, shell, view, cp, logs — and the operator commands.
---

```bash
pip install 'zoo-sdk[cli]'      # or: uv tool install 'zoo-sdk[cli]'
zoo login                       # asks for the URL and an API key; stored in ~/.config/zoo/config.toml (0600)
```

`ZOO_URL`, `ZOO_API_KEY` and `ZOO_WORKSPACE` override the stored login. Read commands take `--json`.

| Command | What it does |
| --- | --- |
| `zoo create --kind desktop [--name N] [--label k=v] [--no-wait]` | Create a sandbox and wait until it runs |
| `zoo ls [--label k=v]` | List sandboxes |
| `zoo exec <id> -- <command>` | Run a command, streaming its output; exits with its exit code (`--timeout`, at most 300 s) |
| `zoo shell <id>` | An interactive shell (Linux sandboxes) |
| `zoo view <id> [--print]` | Open the live screen in the browser through a short-lived viewer link |
| `zoo cp ./file <id>:path` / `zoo cp <id>:path ./file` | Copy files in or out; remote paths are relative to the home folder |
| `zoo logs <id> [-f]` | Recent tool calls; with `-f`, then its events as they happen |
| `zoo rm <id>...` | Delete sandboxes |

## Operator commands

| Command | What it does |
| --- | --- |
| `zoo server doctor` | Check the API, database, object store, runtime, API key, Docker and KVM; exits 1 on a failure |
| `zoo server upgrade [--version X.Y.Z]` | Pull new images and restart; the API migrates as it starts |
| `zoo server backup` / `zoo server backups` | Take a backup now / list them |
| `zoo server restore <key>` | Replace the database with a backup |

On a server set up by `install.sh`, `/usr/local/bin/zoo` is the operator script (`zoo upgrade`, `zoo backup`,
`zoo doctor`, …, with automatic rollback on upgrade). The two hand commands to each other: the operator script runs
`zoo login`, `zoo exec` and the other sandbox commands through the Python CLI when it's installed, and
`zoo server ...` runs through the operator script when it's there. Elsewhere, `zoo server` works next to Zoo's
`compose.yml`, and `doctor`, `backup` and `backups` work against any installation through the API.
