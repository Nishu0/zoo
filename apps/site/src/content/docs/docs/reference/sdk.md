---
title: Python SDK
description: zoo_sdk classes and methods, generated from the SDK's docstrings.
---

:::note
Generated from the code by `apps/site/scripts/generate_reference.py` — do not edit by hand.
Regenerate with `bun run docs:gen` from `site/`.
:::

```bash
pip install zoo-sdk            # or: pip install ./sdk/python
pip install 'zoo-sdk[cli]'     # plus the zoo command
```

```python
from zoo_sdk import Zoo

zoo = Zoo()  # reads ZOO_API_KEY and ZOO_URL
with zoo.sandbox(kind="desktop") as box:  # deleted on exit, also on error
    for chunk in box.exec("uname -a", stream=True):
        print(chunk.data, end="")
```

`AsyncZoo` is the asyncio twin (`async with await zoo.sandbox(kind=...) as box`). Every call has a timeout
(30 s unless it passes `timeout=`) and is retried with exponential backoff when that is safe: connection failures
and 429 always; 5xx and timeouts for reads and for creates, which carry an automatic `Idempotency-Key`. Errors
are `ZooError` subclasses with the API's [error code](/docs/reference/errors/). The TypeScript SDK
(`npm install @zoo/sdk`) has the same methods in camelCase; see `sdk/typescript/README.md`.

## `zoo_sdk.aio.AsyncZoo`

### `AsyncZoo.__init__`

```python
AsyncZoo(self, api_key: str | None=None, base_url: str | None=None, *, timeout: float | None=None, max_retries: int | None=None, workspace: str | None=None, http_client: httpx.AsyncClient | None=None)
```


## `zoo_sdk.aio.AsyncSandbox`

### `AsyncSandbox.__init__`

```python
AsyncSandbox(self, client: AsyncZoo, data: SandboxData, owned: bool=False)
```

| Method | |
| --- | --- |
| [`id()`](#asyncsandboxid) |  |
| [`name()`](#asyncsandboxname) |  |
| [`kind()`](#asyncsandboxkind) |  |
| [`status()`](#asyncsandboxstatus) |  |
| [`error_message()`](#asyncsandboxerror_message) |  |
| [`labels()`](#asyncsandboxlabels) |  |
| [`exec()`](#asyncsandboxexec) |  |
| [`exec()`](#asyncsandboxexec) |  |
| [`exec()`](#asyncsandboxexec) | `await box.exec(cmd)` for the result, or `async for chunk in box.exec(cmd, stream=True)`. |

### `AsyncSandbox.id` {#asyncsandboxid}

```python
id(self) -> str
```


### `AsyncSandbox.name` {#asyncsandboxname}

```python
name(self) -> str
```


### `AsyncSandbox.kind` {#asyncsandboxkind}

```python
kind(self) -> str
```


### `AsyncSandbox.status` {#asyncsandboxstatus}

```python
status(self) -> str
```


### `AsyncSandbox.error_message` {#asyncsandboxerror_message}

```python
error_message(self) -> str | None
```


### `AsyncSandbox.labels` {#asyncsandboxlabels}

```python
labels(self) -> dict[str, str]
```


### `AsyncSandbox.exec` {#asyncsandboxexec}

```python
exec(self, command: str, timeout: int=30, *, stream: Literal[False]=False) -> 'asyncio.Future[ExecResult]'
```


### `AsyncSandbox.exec` {#asyncsandboxexec}

```python
exec(self, command: str, timeout: int=30, *, stream: Literal[True]) -> AsyncIterator[ExecChunk]
```


### `AsyncSandbox.exec` {#asyncsandboxexec}

`await box.exec(cmd)` for the result, or `async for chunk in box.exec(cmd, stream=True)`.

```python
exec(self, command: str, timeout: int=30, *, stream: bool=False) -> 'asyncio.Future[ExecResult] | AsyncIterator[ExecChunk]'
```


## `zoo_sdk.client.Zoo`

### `Zoo.__init__`

```python
Zoo(self, api_key: str | None=None, base_url: str | None=None, *, timeout: float | None=None, max_retries: int | None=None, workspace: str | None=None, http_client: httpx.Client | None=None)
```

| Method | |
| --- | --- |
| [`close()`](#zooclose) |  |
| [`request()`](#zoorequest) | Sends a request to /v1`path`, retrying when safe, and raises a ZooError subclass for an error response. A `stream`ed response must be closed by the caller (or read to the end). |
| [`sandboxes()`](#zoosandboxes) | The workspace's sandboxes, only those with all of `labels` when given. |
| [`iter_sandboxes()`](#zooiter_sandboxes) | The workspace's sandboxes, newest first, a page at a time. |
| [`get()`](#zooget) |  |
| [`sandbox()`](#zoosandbox) |  |
| [`sandbox()`](#zoosandbox) |  |
| [`sandbox()`](#zoosandbox) | `sandbox(id)` is an existing sandbox; `sandbox(kind=...)` creates one, which `with` deletes on exit: |
| [`create()`](#zoocreate) | Creates a sandbox and, by default, waits until it runs. If it fails to start it is deleted, and the error raised. |
| [`servers()`](#zooservers) |  |
| [`profiles()`](#zooprofiles) |  |
| [`tools()`](#zootools) |  |
| [`events()`](#zooevents) | Follows the workspace's events (sandbox.state) as they happen, from after `after` (a seq) or from now. Pass the last event's seq back to resume after a disconnect. |
| [`usage()`](#zoousage) | Metered usage between two ISO times, by sandbox, day or a label (group_by="label:end_user"). |

### `Zoo.close` {#zooclose}

```python
close(self)
```


### `Zoo.request` {#zoorequest}

Sends a request to /v1`path`, retrying when safe, and raises a ZooError subclass for an error response.
A `stream`ed response must be closed by the caller (or read to the end).

```python
request(self, method: str, path: str, *, json: Any=None, params: Any=None, content: bytes | None=None, headers: dict[str, str] | None=None, timeout: float | None=None, stream: bool=False) -> httpx.Response
```


### `Zoo.sandboxes` {#zoosandboxes}

The workspace's sandboxes, only those with all of `labels` when given.

```python
sandboxes(self, labels: dict[str, str] | None=None) -> list['Sandbox']
```


### `Zoo.iter_sandboxes` {#zooiter_sandboxes}

The workspace's sandboxes, newest first, a page at a time.

```python
iter_sandboxes(self, labels: dict[str, str] | None=None, page_size: int=100) -> Iterator['Sandbox']
```


### `Zoo.get` {#zooget}

```python
get(self, sandbox_id: str) -> 'Sandbox'
```


### `Zoo.sandbox` {#zoosandbox}

```python
sandbox(self, sandbox_id: str, /) -> 'Sandbox'
```


### `Zoo.sandbox` {#zoosandbox}

```python
sandbox(self, sandbox_id: None=None, /, *, kind: SandboxKind='desktop', name: str | None=None, labels: dict[str, str] | None=None, server_id: str | None=None, profile_ids: list[str] | None=None, wait: bool=True, timeout: float=180) -> 'Sandbox'
```


### `Zoo.sandbox` {#zoosandbox}

`sandbox(id)` is an existing sandbox; `sandbox(kind=...)` creates one, which `with` deletes on exit:

```python
sandbox(self, sandbox_id: str | None=None, /, **create: Any) -> 'Sandbox'
```


### `Zoo.create` {#zoocreate}

Creates a sandbox and, by default, waits until it runs. If it fails to start it is deleted, and the error
raised.

```python
create(self, name: str | None=None, kind: SandboxKind='desktop', server_id: str | None=None, profile_ids: list[str] | None=None, wait: bool=True, timeout: float=180, labels: dict[str, str] | None=None) -> 'Sandbox'
```


### `Zoo.servers` {#zooservers}

```python
servers(self) -> list[dict]
```


### `Zoo.profiles` {#zooprofiles}

```python
profiles(self) -> list[dict]
```


### `Zoo.tools` {#zootools}

```python
tools(self) -> list[dict]
```


### `Zoo.events` {#zooevents}

Follows the workspace's events (sandbox.state) as they happen, from after `after` (a seq) or from now.
Pass the last event's seq back to resume after a disconnect.

```python
events(self, after: int | None=None, sandbox_id: str | None=None, type: str | None=None) -> Iterator[dict]
```


### `Zoo.usage` {#zoousage}

Metered usage between two ISO times, by sandbox, day or a label (group_by="label:end_user").

```python
usage(self, since: str, until: str, group_by: str='sandbox', metric: str | None=None) -> list[dict]
```


## `zoo_sdk.client.Sandbox`

One sandbox. Tools without a method of their own are attributes: `box.click(x=10, y=20)` calls the click
tool. Used with `with`, a sandbox this client created is deleted on exit.

### `Sandbox.__init__`

```python
Sandbox(self, client: Zoo, data: SandboxData, owned: bool=False)
```

| Method | |
| --- | --- |
| [`id()`](#sandboxid) |  |
| [`name()`](#sandboxname) |  |
| [`kind()`](#sandboxkind) |  |
| [`status()`](#sandboxstatus) |  |
| [`server_id()`](#sandboxserver_id) |  |
| [`error_message()`](#sandboxerror_message) |  |
| [`labels()`](#sandboxlabels) |  |
| [`refresh()`](#sandboxrefresh) |  |
| [`wait()`](#sandboxwait) | Waits until the sandbox runs; raises if it fails or takes longer than `timeout` seconds. |
| [`tool()`](#sandboxtool) | Calls a tool by name with its arguments; `request_timeout` (seconds) is for the HTTP call. |
| [`exec()`](#sandboxexec) |  |
| [`exec()`](#sandboxexec) |  |
| [`exec()`](#sandboxexec) | Runs a shell command (at most `timeout` seconds). Returns its exit code and output, or with stream=True yields its output as it arrives, ending with an `exit` chunk. |
| [`claude()`](#sandboxclaude) |  |
| [`screenshot()`](#sandboxscreenshot) |  |
| [`viewer_link()`](#sandboxviewer_link) | A link to this sandbox's screen for someone without a Zoo account: "view" only watches, "control" can type and click. Its "url" opens in a browser or an iframe; expires_in is in minutes. |
| [`set_labels()`](#sandboxset_labels) | Replaces the sandbox's labels. |
| [`executions()`](#sandboxexecutions) | The latest tool calls on this sandbox, newest first. |
| [`hotkey()`](#sandboxhotkey) |  |
| [`start()`](#sandboxstart) |  |
| [`stop()`](#sandboxstop) | Stops the sandbox. Stopping runs as a job on the server, so by default this waits until it has stopped. |
| [`delete()`](#sandboxdelete) | Deletes the sandbox. Deleting runs as a job on the server, so by default this waits until it is gone. |
| [`set_secret()`](#sandboxset_secret) |  |
| [`set_permission()`](#sandboxset_permission) |  |
| [`set_network()`](#sandboxset_network) |  |
| [`add_rule()`](#sandboxadd_rule) |  |
| [`read_file()`](#sandboxread_file) | A file from the sandbox (absolute, or relative to the home folder). |
| [`write_file()`](#sandboxwrite_file) | Writes a file into the sandbox, making its folder. |
| [`backup()`](#sandboxbackup) | Exports the home folder as a tar file; snapshots (`snapshot()`) are the faster way to keep a copy. |
| [`restore()`](#sandboxrestore) |  |
| [`snapshots()`](#sandboxsnapshots) |  |
| [`snapshot()`](#sandboxsnapshot) | Snapshots the home disk on the sandbox's server (a VM must be stopped). Waits until it is ready. |
| [`restore_snapshot()`](#sandboxrestore_snapshot) | Replaces the home disk with a snapshot's. The sandbox must be stopped. |
| [`delete_snapshot()`](#sandboxdelete_snapshot) |  |
| [`move()`](#sandboxmove) |  |
| [`save_profile()`](#sandboxsave_profile) | Saves the app's profile from this sandbox. Saving under an existing name (or a profile_id) adds a version. |
| [`apply_profile()`](#sandboxapply_profile) | Loads a profile, the latest version unless one is given. The app must not be running (409). |

### `Sandbox.id` {#sandboxid}

```python
id(self) -> str
```


### `Sandbox.name` {#sandboxname}

```python
name(self) -> str
```


### `Sandbox.kind` {#sandboxkind}

```python
kind(self) -> str
```


### `Sandbox.status` {#sandboxstatus}

```python
status(self) -> str
```


### `Sandbox.server_id` {#sandboxserver_id}

```python
server_id(self) -> str | None
```


### `Sandbox.error_message` {#sandboxerror_message}

```python
error_message(self) -> str | None
```


### `Sandbox.labels` {#sandboxlabels}

```python
labels(self) -> dict[str, str]
```


### `Sandbox.refresh` {#sandboxrefresh}

```python
refresh(self) -> 'Sandbox'
```


### `Sandbox.wait` {#sandboxwait}

Waits until the sandbox runs; raises if it fails or takes longer than `timeout` seconds.

```python
wait(self, timeout: float=120) -> 'Sandbox'
```


### `Sandbox.tool` {#sandboxtool}

Calls a tool by name with its arguments; `request_timeout` (seconds) is for the HTTP call.

```python
tool(self, name: str, request_timeout: float | None=None, **args: Any) -> Any
```


### `Sandbox.exec` {#sandboxexec}

```python
exec(self, command: str, timeout: int=30, *, stream: Literal[False]=False) -> ExecResult
```


### `Sandbox.exec` {#sandboxexec}

```python
exec(self, command: str, timeout: int=30, *, stream: Literal[True]) -> Iterator[ExecChunk]
```


### `Sandbox.exec` {#sandboxexec}

Runs a shell command (at most `timeout` seconds). Returns its exit code and output, or with stream=True
yields its output as it arrives, ending with an `exit` chunk.

```python
exec(self, command: str, timeout: int=30, *, stream: bool=False) -> ExecResult | Iterator[ExecChunk]
```


### `Sandbox.claude` {#sandboxclaude}

```python
claude(self, prompt: str, cwd: str='~/work', timeout: int=900, args: str='') -> dict
```


### `Sandbox.screenshot` {#sandboxscreenshot}

```python
screenshot(self) -> bytes
```


### `Sandbox.viewer_link` {#sandboxviewer_link}

A link to this sandbox's screen for someone without a Zoo account: "view" only watches, "control" can
type and click. Its "url" opens in a browser or an iframe; expires_in is in
minutes.

```python
viewer_link(self, mode: str='view', expires_in: int=60, end_user: str | None=None) -> dict
```


### `Sandbox.set_labels` {#sandboxset_labels}

Replaces the sandbox's labels.

```python
set_labels(self, labels: dict[str, str]) -> 'Sandbox'
```


### `Sandbox.executions` {#sandboxexecutions}

The latest tool calls on this sandbox, newest first.

```python
executions(self, limit: int=50) -> list[dict]
```


### `Sandbox.hotkey` {#sandboxhotkey}

```python
hotkey(self, *keys: str) -> dict
```


### `Sandbox.start` {#sandboxstart}

```python
start(self, wait: bool=True) -> 'Sandbox'
```


### `Sandbox.stop` {#sandboxstop}

Stops the sandbox. Stopping runs as a job on the server, so by default this waits until it has stopped.

```python
stop(self, wait: bool=True, timeout: float=120) -> 'Sandbox'
```


### `Sandbox.delete` {#sandboxdelete}

Deletes the sandbox. Deleting runs as a job on the server, so by default this waits until it is gone.

```python
delete(self, wait: bool=True, timeout: float=120)
```


### `Sandbox.set_secret` {#sandboxset_secret}

```python
set_secret(self, name: str, value: str) -> dict
```


### `Sandbox.set_permission` {#sandboxset_permission}

```python
set_permission(self, permission: str, action: str, effect: str) -> dict
```


### `Sandbox.set_network` {#sandboxset_network}

```python
set_network(self, default_action: str='allow', allow_dns: bool=True) -> dict
```


### `Sandbox.add_rule` {#sandboxadd_rule}

```python
add_rule(self, rule_type: str, value: str, effect: str='allow') -> dict
```


### `Sandbox.read_file` {#sandboxread_file}

A file from the sandbox (absolute, or relative to the home folder).

```python
read_file(self, path: str, timeout: float | None=300) -> bytes
```


### `Sandbox.write_file` {#sandboxwrite_file}

Writes a file into the sandbox, making its folder.

```python
write_file(self, path: str, data: bytes, timeout: float | None=300) -> dict
```


### `Sandbox.backup` {#sandboxbackup}

Exports the home folder as a tar file; snapshots (`snapshot()`) are the faster way to keep a copy.

```python
backup(self, path: str)
```


### `Sandbox.restore` {#sandboxrestore}

```python
restore(self, path: str)
```


### `Sandbox.snapshots` {#sandboxsnapshots}

```python
snapshots(self) -> list[dict]
```


### `Sandbox.snapshot` {#sandboxsnapshot}

Snapshots the home disk on the sandbox's server (a VM must be stopped). Waits until it is ready.

```python
snapshot(self, name: str='', wait: bool=True, timeout: float=1800) -> dict
```


### `Sandbox.restore_snapshot` {#sandboxrestore_snapshot}

Replaces the home disk with a snapshot's. The sandbox must be stopped.

```python
restore_snapshot(self, snapshot_id: str, wait: bool=True, timeout: float=1800) -> 'Sandbox'
```


### `Sandbox.delete_snapshot` {#sandboxdelete_snapshot}

```python
delete_snapshot(self, snapshot_id: str)
```


### `Sandbox.move` {#sandboxmove}

```python
move(self, server_id: str | None) -> 'Sandbox'
```


### `Sandbox.save_profile` {#sandboxsave_profile}

Saves the app's profile from this sandbox. Saving under an existing name (or a profile_id) adds a version.

```python
save_profile(self, name: str, app: str='firefox', profile_id: str | None=None) -> dict
```


### `Sandbox.apply_profile` {#sandboxapply_profile}

Loads a profile, the latest version unless one is given. The app must not be running (409).

```python
apply_profile(self, profile_id: str, version: int | None=None) -> dict
```


## `zoo_sdk.errors.ZooError`

An error from the Zoo API, or the SDK giving up on a request.

### `ZooError.__init__`

```python
ZooError(self, message: str, *, status: int | None=None, code: str='error', hint: str | None=None, docs_url: str | None=None, request_id: str | None=None)
```


## `zoo_sdk.errors.AuthenticationError`

401: no or invalid API key.


## `zoo_sdk.errors.PermissionDeniedError`

403: the key's role or scope, or a sandbox policy, doesn't allow it.


## `zoo_sdk.errors.NotFoundError`

404: no such sandbox (or other object) in this workspace.


## `zoo_sdk.errors.ConflictError`

409: the object's state doesn't allow it, a quota is used up, or an idempotency key clashed.


## `zoo_sdk.errors.ValidationError`

422: a field didn't match the schema.


## `zoo_sdk.errors.RateLimitError`

429: too many requests; retried automatically before this is raised.


## `zoo_sdk.errors.ServerError`

5xx: Zoo, or a sandbox it talks to, failed.


## `zoo_sdk.errors.APIConnectionError`

The server couldn't be reached.


## `zoo_sdk.errors.APITimeoutError`

The server didn't answer within the timeout.


## `zoo_sdk.types.SandboxData`


## `zoo_sdk.types.ExecResult`


## `zoo_sdk.types.ExecChunk`

A piece of a streamed command: output as it arrives (`stdout`/`stderr`, with `data`), then `exit`.

| Method | |
| --- | --- |
| [`timed_out()`](#execchunktimed_out) |  |

### `ExecChunk.timed_out` {#execchunktimed_out}

```python
timed_out(self) -> bool
```


## Errors

Any non-2xx response raises `zoo_sdk.ZooError` with the status code and the API's
stable `code`, `message`, `hint`, `docs_url` and `request_id`; see [Errors](/docs/reference/errors/). Any tool can be called as a method
(`box.window_focus(window_id=...)`) or with `box.tool(name, **args)`.
