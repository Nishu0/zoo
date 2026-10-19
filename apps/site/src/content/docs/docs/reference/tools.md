---
title: Tools
description: Every tool a sandbox exposes over REST, MCP and the SDK, generated from apps/server/registry.py.
---

:::note
Generated from the code by `apps/site/scripts/generate_reference.py` — do not edit by hand.
Regenerate with `bun run docs:gen` from `site/`.
:::

Every tool is registered once in `apps/server/registry.py` and exposed the same way over
REST, MCP and the SDK. `GET /v1/tools?kind=desktop` returns the same data live.

Sandbox kinds: `desktop`, `browser`, `code`, `macos`, `windows`. A `desktop`, `macos` or `windows` sandbox offers every
category; `browser` and `code` restrict them (columns below).

## apps

| Tool | Arguments | Permission | Kinds |
| --- | --- | --- | --- |
| `close_app` | `target: str, display: str = ':1'` | `input.control` — Control mouse, keyboard, windows and apps | `desktop`, `macos`, `windows` |
| `installed_apps` | `—` | `screen.read` — Take screenshots and list apps | `desktop`, `macos`, `windows` |
| `open_app` | `command: str, display: str = ':1', timeout: float = 4.0` | `input.control` — Control mouse, keyboard, windows and apps | `desktop`, `macos`, `windows` |

## browser

| Tool | Arguments | Permission | Kinds |
| --- | --- | --- | --- |
| `open_url` | `url: str, display: str = ':1'` | `input.control` — Control mouse, keyboard, windows and apps | `desktop`, `browser`, `macos`, `windows` |

## files

| Tool | Arguments | Permission | Kinds |
| --- | --- | --- | --- |
| `copy_file` | `source: str, destination: str` | `files.write` — Write, move and delete files | `desktop`, `code`, `macos`, `windows` |
| `create_directory` | `path: str` | `files.write` — Write, move and delete files | `desktop`, `code`, `macos`, `windows` |
| `delete_file` | `path: str` | `files.write` — Write, move and delete files | `desktop`, `code`, `macos`, `windows` |
| `get_file_info` | `path: str` | `files.read` — Read files | `desktop`, `code`, `macos`, `windows` |
| `list_files` | `path: str = '.'` | `files.read` — Read files | `desktop`, `code`, `macos`, `windows` |
| `move_file` | `source: str, destination: str` | `files.write` — Write, move and delete files | `desktop`, `code`, `macos`, `windows` |
| `read_file` | `path: str` | `files.read` — Read files | `desktop`, `code`, `macos`, `windows` |
| `write_file` | `path: str, content: str` | `files.write` — Write, move and delete files | `desktop`, `code`, `macos`, `windows` |

## keyboard

| Tool | Arguments | Permission | Kinds |
| --- | --- | --- | --- |
| `hotkey` | `keys: list` | `input.control` — Control mouse, keyboard, windows and apps | `desktop`, `browser`, `macos`, `windows` |
| `press_key` | `key: str` | `input.control` — Control mouse, keyboard, windows and apps | `desktop`, `browser`, `macos`, `windows` |
| `type_text` | `text: str` | `input.control` — Control mouse, keyboard, windows and apps | `desktop`, `browser`, `macos`, `windows` |

## mouse

| Tool | Arguments | Permission | Kinds |
| --- | --- | --- | --- |
| `click` | `x: int, y: int, button: str = 'left'` | `input.control` — Control mouse, keyboard, windows and apps | `desktop`, `browser`, `macos`, `windows` |
| `double_click` | `x: int, y: int, button: str = 'left'` | `input.control` — Control mouse, keyboard, windows and apps | `desktop`, `browser`, `macos`, `windows` |
| `drag` | `start_x: int, start_y: int, end_x: int, end_y: int, button: str = 'left', duration: float = 0.5` | `input.control` — Control mouse, keyboard, windows and apps | `desktop`, `browser`, `macos`, `windows` |
| `move_mouse` | `x: int, y: int` | `input.control` — Control mouse, keyboard, windows and apps | `desktop`, `browser`, `macos`, `windows` |
| `scroll` | `direction: str, amount: int = 3, x: int \| None = None, y: int \| None = None` | `input.control` — Control mouse, keyboard, windows and apps | `desktop`, `browser`, `macos`, `windows` |

## observe

| Tool | Arguments | Permission | Kinds |
| --- | --- | --- | --- |
| `accessibility_tree` | `app: str = '', title: str = '', max_nodes: int = 300` | `screen.read` — Take screenshots and list apps | `desktop`, `browser`, `macos`, `windows` |
| `screen_diff` | `session: str = 'default', format: str = 'png', scale: float = 1.0, quality: int = 80` | `screen.read` — Take screenshots and list apps | `desktop`, `browser`, `macos`, `windows` |
| `screenshot` | `format: str = 'png', scale: float = 1.0, quality: int = 80` | `screen.read` — Take screenshots and list apps | `desktop`, `browser`, `macos`, `windows` |
| `wait_until_stable` | `timeout: float = 5.0, quiet_ms: int = 500, threshold: float = 0.0` | `screen.read` — Take screenshots and list apps | `desktop`, `browser`, `macos`, `windows` |

## shell

| Tool | Arguments | Permission | Kinds |
| --- | --- | --- | --- |
| `execute_command` | `command: str, timeout: int = 30` | `shell.exec` — Run shell commands | `desktop`, `code`, `macos`, `windows` |

## web

| Tool | Arguments | Permission | Kinds |
| --- | --- | --- | --- |
| `fetch_url` | `url: str, timeout: int = 20` | `shell.exec` — Run shell commands | `desktop`, `code`, `macos`, `windows` |

## windows

| Tool | Arguments | Permission | Kinds |
| --- | --- | --- | --- |
| `window_close` | `window_id: str, display: str = ':1'` | `input.control` — Control mouse, keyboard, windows and apps | `desktop`, `browser`, `macos`, `windows` |
| `window_focus` | `window_id: str, display: str = ':1'` | `input.control` — Control mouse, keyboard, windows and apps | `desktop`, `browser`, `macos`, `windows` |
| `window_maximize` | `window_id: str, display: str = ':1'` | `input.control` — Control mouse, keyboard, windows and apps | `desktop`, `browser`, `macos`, `windows` |
| `window_minimize` | `window_id: str, display: str = ':1'` | `input.control` — Control mouse, keyboard, windows and apps | `desktop`, `browser`, `macos`, `windows` |
| `window_restore` | `window_id: str, display: str = ':1'` | `input.control` — Control mouse, keyboard, windows and apps | `desktop`, `browser`, `macos`, `windows` |
| `window_unmaximize` | `window_id: str, display: str = ':1'` | `input.control` — Control mouse, keyboard, windows and apps | `desktop`, `browser`, `macos`, `windows` |
| `windows_list` | `display: str = ':1'` | `screen.read` — Take screenshots and list apps | `desktop`, `browser`, `macos`, `windows` |

## Permissions

Checked by the API before every tool call; a denied call returns `403`.

| Permission | Covers |
| --- | --- |
| `shell.exec` | Run shell commands |
| `screen.read` | Take screenshots and list apps |
| `input.control` | Control mouse, keyboard, windows and apps |
| `files.read` | Read files |
| `files.write` | Write, move and delete files |
