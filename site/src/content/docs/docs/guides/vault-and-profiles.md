---
title: Vault, secrets and app profiles
description: Envelope-encrypted secrets with optional KMS, live secret pushes, and app profiles that carry logins between sandboxes.
---

## The vault

Stored secrets are envelope-encrypted: each workspace has its own data key, wrapped by
`ZOO_SECRETS_KEY` or an external KMS (`ZOO_KMS`: `aws:<key ARN>`,
`gcp:projects/…/cryptoKeys/<key>`, or `vault:<transit mount>/<key>`). Losing
`ZOO_SECRETS_KEY` loses every stored secret — back it up; the installer writes it to
`/opt/zoo/.env`.

Secrets are written when the sandbox starts, and pushed to running sandboxes through the
guest agent when they change. On Linux the guest reads `/run/zoo/env.json` for every
command; on macOS and Windows every command sources `~/.zoo/env` (or `env.ps1`). New commands and terminals see the change immediately; programs already
running keep the environment they started with. GUI apps don't see secrets.

Secrets can carry an expiry date and a rotation interval; the Vault page,
`GET /v1/vault/reminders` and the worker's log flag the ones expiring or due.

```python
box.set_secret("GITHUB_TOKEN", "ghp_...")  # a sandbox-level secret
```

Rotating the wrapping key or moving to a KMS: `make rotate-secrets` re-wraps every key —
run it after changing `ZOO_SECRETS_KEY` (with the old key in
`ZOO_SECRETS_KEY_PREVIOUS`) or `ZOO_KMS`.

## App profiles

Save an app's profile directory — logins, cookies, settings — from a running sandbox and
load it into others:

| App | Directory |
| --- | --- |
| `firefox` | `~/.mozilla` |
| `chromium` | `~/.config/chromium` |
| `chrome` | `~/.config/google-chrome` |
| `vscode` | `~/.config/Code` |

macOS and Windows sandboxes have their own apps (Safari, Chrome, Edge, Firefox, VS Code on
macOS; Chrome, Edge, Firefox, VS Code on Windows). A profile only loads into a sandbox of
the OS it came from.

- Save and load in the sandbox's **Profiles** tab, or pick a profile in the create dialog
  to have it loaded before the desktop starts.
- Profiles are stored encrypted as tar files in object storage.
- **Versioned**: saving under a name you've used for that app adds a version; the last 10
  are kept. Loading takes the latest unless you pick one; a new sandbox gets the latest.
- **Loading into a running sandbox is refused (409)** while the app is running there — the
  app holds its profile open and would overwrite or corrupt it. Quit the app, then load.

```python
box.save_profile("work", app="firefox")  # adds a version under "work"
other.apply_profile(profile_id)  # latest version
other.apply_profile(profile_id, version=2)  # a specific one
```

:::note
On macOS, Safari profiles (`Library/Containers/com.apple.Safari`) need Full Disk Access
for `/bin/zsh` in the base VM. On Windows, native app encryption can bind a profile to the
account and machine identity of its reserved slot; do not assume it loads into a different
identity or onto unrelated disks.
:::
