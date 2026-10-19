---
title: REST API
description: Every HTTP endpoint the Zoo API serves, generated from its OpenAPI schema.
---

:::note
Generated from the code by `apps/site/scripts/generate_reference.py` — do not edit by hand.
Regenerate with `bun run docs:gen` from `site/`.
:::

Generated from `sdk/typescript/openapi.json`, the shared API/SDK schema artifact.
Regenerate that artifact after API changes before publishing this reference.
The API serves its schema at `/docs` on a running server. Every request is
authenticated according to its declared security requirements; auth and public health routes differ:
- **HTTPBearer**: http — 

The Python SDK wraps these endpoints; see [SDK](/docs/reference/sdk/).

## Sandboxes

| Endpoint | Purpose |
| --- | --- |
| [`GET /v1/sandboxes`](#get-v1sandboxes) | List Sandboxes |
| [`POST /v1/sandboxes`](#post-v1sandboxes) | Create Sandbox |
| [`GET /v1/sandboxes/{sandbox_id}`](#get-v1sandboxessandbox_id) | Get Sandbox |
| [`PATCH /v1/sandboxes/{sandbox_id}`](#patch-v1sandboxessandbox_id) | Update Sandbox |
| [`DELETE /v1/sandboxes/{sandbox_id}`](#delete-v1sandboxessandbox_id) | Delete Sandbox |
| [`GET /v1/sandboxes/{sandbox_id}/apps`](#get-v1sandboxessandbox_idapps) | List Apps |
| [`PUT /v1/sandboxes/{sandbox_id}/apps/{binary}`](#put-v1sandboxessandbox_idappsbinary) | Set App |
| [`GET /v1/sandboxes/{sandbox_id}/backup`](#get-v1sandboxessandbox_idbackup) | Backup |
| [`POST /v1/sandboxes/{sandbox_id}/exec`](#post-v1sandboxessandbox_idexec) | Execute |
| [`POST /v1/sandboxes/{sandbox_id}/exec/stream`](#post-v1sandboxessandbox_idexecstream) | Execute Streamed |
| [`GET /v1/sandboxes/{sandbox_id}/executions`](#get-v1sandboxessandbox_idexecutions) | List Executions |
| [`GET /v1/sandboxes/{sandbox_id}/files/content`](#get-v1sandboxessandbox_idfilescontent) | Download File |
| [`PUT /v1/sandboxes/{sandbox_id}/files/content`](#put-v1sandboxessandbox_idfilescontent) | Upload File |
| [`GET /v1/sandboxes/{sandbox_id}/guest`](#get-v1sandboxessandbox_idguest) | Guest Status |
| [`PUT /v1/sandboxes/{sandbox_id}/lifecycle`](#put-v1sandboxessandbox_idlifecycle) | Set Lifecycle |
| [`POST /v1/sandboxes/{sandbox_id}/move`](#post-v1sandboxessandbox_idmove) | Move Sandbox |
| [`GET /v1/sandboxes/{sandbox_id}/network`](#get-v1sandboxessandbox_idnetwork) | Get Network |
| [`PUT /v1/sandboxes/{sandbox_id}/network`](#put-v1sandboxessandbox_idnetwork) | Set Network |
| [`POST /v1/sandboxes/{sandbox_id}/network/rules`](#post-v1sandboxessandbox_idnetworkrules) | Add Rule |
| [`DELETE /v1/sandboxes/{sandbox_id}/network/rules/{rule_id}`](#delete-v1sandboxessandbox_idnetworkrulesrule_id) | Delete Rule |
| [`GET /v1/sandboxes/{sandbox_id}/permissions`](#get-v1sandboxessandbox_idpermissions) | List Permissions |
| [`PUT /v1/sandboxes/{sandbox_id}/permissions`](#put-v1sandboxessandbox_idpermissions) | Set Permission |
| [`POST /v1/sandboxes/{sandbox_id}/profiles`](#post-v1sandboxessandbox_idprofiles) | Capture Profile |
| [`POST /v1/sandboxes/{sandbox_id}/profiles/{profile_id}`](#post-v1sandboxessandbox_idprofilesprofile_id) | Apply Profile |
| [`POST /v1/sandboxes/{sandbox_id}/restore`](#post-v1sandboxessandbox_idrestore) | Restore |
| [`POST /v1/sandboxes/{sandbox_id}/screenshot`](#post-v1sandboxessandbox_idscreenshot) | Screenshot |
| [`GET /v1/sandboxes/{sandbox_id}/secrets`](#get-v1sandboxessandbox_idsecrets) | List Secrets |
| [`PUT /v1/sandboxes/{sandbox_id}/secrets`](#put-v1sandboxessandbox_idsecrets) | Set Secret |
| [`DELETE /v1/sandboxes/{sandbox_id}/secrets/{secret_id}`](#delete-v1sandboxessandbox_idsecretssecret_id) | Delete Secret |
| [`GET /v1/sandboxes/{sandbox_id}/snapshots`](#get-v1sandboxessandbox_idsnapshots) | List Snapshots |
| [`POST /v1/sandboxes/{sandbox_id}/snapshots`](#post-v1sandboxessandbox_idsnapshots) | Create Snapshot |
| [`DELETE /v1/sandboxes/{sandbox_id}/snapshots/{snapshot_id}`](#delete-v1sandboxessandbox_idsnapshotssnapshot_id) | Delete Snapshot |
| [`POST /v1/sandboxes/{sandbox_id}/snapshots/{snapshot_id}/restore`](#post-v1sandboxessandbox_idsnapshotssnapshot_idrestore) | Restore From Snapshot |
| [`POST /v1/sandboxes/{sandbox_id}/start`](#post-v1sandboxessandbox_idstart) | Start Sandbox |
| [`POST /v1/sandboxes/{sandbox_id}/stop`](#post-v1sandboxessandbox_idstop) | Stop Sandbox |
| [`POST /v1/sandboxes/{sandbox_id}/terminal-ticket`](#post-v1sandboxessandbox_idterminal-ticket) | Terminal Ticket |
| [`POST /v1/sandboxes/{sandbox_id}/tools/{name}`](#post-v1sandboxessandbox_idtoolsname) | Call Tool |
| [`POST /v1/sandboxes/{sandbox_id}/upgrade`](#post-v1sandboxessandbox_idupgrade) | Upgrade Sandbox |
| [`GET /v1/sandboxes/{sandbox_id}/vault-secrets`](#get-v1sandboxessandbox_idvault-secrets) | Attached Secrets |
| [`PUT /v1/sandboxes/{sandbox_id}/vault-secrets/{secret_id}`](#put-v1sandboxessandbox_idvault-secretssecret_id) | Attach Secret |
| [`DELETE /v1/sandboxes/{sandbox_id}/vault-secrets/{secret_id}`](#delete-v1sandboxessandbox_idvault-secretssecret_id) | Detach Secret |
| [`GET /v1/sandboxes/{sandbox_id}/viewer-links`](#get-v1sandboxessandbox_idviewer-links) | List Links |
| [`POST /v1/sandboxes/{sandbox_id}/viewer-links`](#post-v1sandboxessandbox_idviewer-links) | Create Link |
| [`DELETE /v1/sandboxes/{sandbox_id}/viewer-links/{link_id}`](#delete-v1sandboxessandbox_idviewer-linkslink_id) | Revoke Link |
| [`POST /v1/sandboxes/{sandbox_id}/vnc-ticket`](#post-v1sandboxessandbox_idvnc-ticket) | Vnc Ticket |

### `GET /v1/sandboxes`

List Sandboxes

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `label` | query | `string[]` | no | key=value; repeat to match all of them |
| `limit` | query | `integer` | no |  |
| `cursor` | query | `string or null` | no | next_cursor of the page before |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/sandboxes`

Create Sandbox

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `admin` | `boolean` | no |  |
| `cpus` | `number` | no |  |
| `disk_gb` | `integer or null` | no |  |
| `idle_timeout_minutes` | `integer or null` | no |  |
| `kind` | `string` | no |  |
| `labels` | `object` | no |  |
| `max_lifetime_minutes` | `integer or null` | no |  |
| `memory_mb` | `integer` | no |  |
| `name` | `string or null` | no |  |
| `profile_ids` | `string[]` | no |  |
| `queue` | `boolean` | no |  |
| `secret_ids` | `string[]` | no |  |
| `server_id` | `string or null` | no |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


### `GET /v1/sandboxes/{sandbox_id}`

Get Sandbox

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PATCH /v1/sandboxes/{sandbox_id}`

Update Sandbox

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `labels` | `object or null` | no |  |
| `name` | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `DELETE /v1/sandboxes/{sandbox_id}`

Delete Sandbox

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/sandboxes/{sandbox_id}/apps`

List Apps

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /v1/sandboxes/{sandbox_id}/apps/{binary}`

Set App

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `binary` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `effect` | `string` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/sandboxes/{sandbox_id}/backup`

Backup

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/sandboxes/{sandbox_id}/exec`

Execute

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `command` | `string` | yes |  |
| `timeout` | `integer` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/sandboxes/{sandbox_id}/exec/stream`

Runs a command and streams its output as server-sent events: `stdout` and `stderr` ({"data": text})
as it arrives, then `exit` ({"exit_code", "timed_out"}). Linux sandboxes only.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `command` | `string` | yes |  |
| `timeout` | `integer` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/sandboxes/{sandbox_id}/executions`

List Executions

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `limit` | query | `integer` | no |  |
| `cursor` | query | `string or null` | no | next_cursor of the page before |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/sandboxes/{sandbox_id}/files/content`

A file's bytes (Linux sandboxes).

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `path` | query | `string` | yes | absolute, or relative to the home directory |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /v1/sandboxes/{sandbox_id}/files/content`

Writes the request body to a file, making its folder (Linux sandboxes).

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `path` | query | `string` | yes | absolute, or relative to the home directory |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/sandboxes/{sandbox_id}/guest`

Guest Status

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /v1/sandboxes/{sandbox_id}/lifecycle`

Set Lifecycle

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `idle_timeout_minutes` | `integer or null` | no |  |
| `max_lifetime_minutes` | `integer or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/sandboxes/{sandbox_id}/move`

Move Sandbox

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `node_name` | `string or null` | no |  |
| `server_id` | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/sandboxes/{sandbox_id}/network`

Get Network

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /v1/sandboxes/{sandbox_id}/network`

Set Network

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `allow_dns` | `boolean` | yes |  |
| `default_action` | `string` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/sandboxes/{sandbox_id}/network/rules`

Add Rule

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `effect` | `string` | yes |  |
| `rule_type` | `string` | yes |  |
| `value` | `string` | yes |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


### `DELETE /v1/sandboxes/{sandbox_id}/network/rules/{rule_id}`

Delete Rule

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `rule_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/sandboxes/{sandbox_id}/permissions`

List Permissions

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /v1/sandboxes/{sandbox_id}/permissions`

Set Permission

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `action` | `string` | yes |  |
| `effect` | `string` | yes |  |
| `permission` | `string` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/sandboxes/{sandbox_id}/profiles`

Capture Profile

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `app` | `string` | yes |  |
| `name` | `string` | yes |  |
| `profile_id` | `string or null` | no |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


### `POST /v1/sandboxes/{sandbox_id}/profiles/{profile_id}`

Apply Profile

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `profile_id` | path | `string` | yes |  |
| `version` | query | `integer or null` | no |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/sandboxes/{sandbox_id}/restore`

Restore

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/sandboxes/{sandbox_id}/screenshot`

Screenshot

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `format` | query | `string` | no |  |
| `scale` | query | `number` | no |  |
| `quality` | query | `integer` | no |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/sandboxes/{sandbox_id}/secrets`

List Secrets

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /v1/sandboxes/{sandbox_id}/secrets`

Set Secret

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |
| `value` | `string` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `DELETE /v1/sandboxes/{sandbox_id}/secrets/{secret_id}`

Delete Secret

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `secret_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/sandboxes/{sandbox_id}/snapshots`

List Snapshots

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/sandboxes/{sandbox_id}/snapshots`

Snapshots the home disk on the sandbox's host. Linux sandboxes can be running; macOS and Windows VMs
must be stopped, since their whole disk is copied.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `202` | Successful Response |
| `422` | Validation Error |


### `DELETE /v1/sandboxes/{sandbox_id}/snapshots/{snapshot_id}`

Delete Snapshot

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `snapshot_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `POST /v1/sandboxes/{sandbox_id}/snapshots/{snapshot_id}/restore`

Restore From Snapshot

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `snapshot_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/sandboxes/{sandbox_id}/start`

Start Sandbox

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/sandboxes/{sandbox_id}/stop`

Stop Sandbox

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/sandboxes/{sandbox_id}/terminal-ticket`

Terminal Ticket

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/sandboxes/{sandbox_id}/tools/{name}`

Call Tool

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `name` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/sandboxes/{sandbox_id}/upgrade`

Restarts the sandbox on the current default image. Its home volume is kept.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/sandboxes/{sandbox_id}/vault-secrets`

Attached Secrets

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /v1/sandboxes/{sandbox_id}/vault-secrets/{secret_id}`

Attach Secret

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `secret_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `DELETE /v1/sandboxes/{sandbox_id}/vault-secrets/{secret_id}`

Detach Secret

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `secret_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/sandboxes/{sandbox_id}/viewer-links`

List Links

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/sandboxes/{sandbox_id}/viewer-links`

Create Link

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `end_user` | `string or null` | no |  |
| `expires_in_minutes` | `integer` | no |  |
| `mode` | `string` | no |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


### `DELETE /v1/sandboxes/{sandbox_id}/viewer-links/{link_id}`

Revoke Link

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `link_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `POST /v1/sandboxes/{sandbox_id}/vnc-ticket`

Vnc Ticket

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## Servers

| Endpoint | Purpose |
| --- | --- |
| [`GET /v1/servers`](#get-v1servers) | List Servers |
| [`DELETE /v1/servers/{server_id}`](#delete-v1serversserver_id) | Delete Server |
| [`GET /v1/servers/{server_id}/base`](#get-v1serversserver_idbase) | Base Status |
| [`POST /v1/servers/{server_id}/base/install`](#post-v1serversserver_idbaseinstall) | Base Install |
| [`POST /v1/servers/{server_id}/base/setup`](#post-v1serversserver_idbasesetup) | Base Setup |
| [`POST /v1/servers/{server_id}/base/start`](#post-v1serversserver_idbasestart) | Base Start |
| [`POST /v1/servers/{server_id}/base/stop`](#post-v1serversserver_idbasestop) | Base Stop |
| [`POST /v1/servers/{server_id}/base/vnc-ticket`](#post-v1serversserver_idbasevnc-ticket) | Base Vnc Ticket |
| [`GET /v1/servers/{server_id}/status`](#get-v1serversserver_idstatus) | Server Status |

### `GET /v1/servers`

List Servers

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `DELETE /v1/servers/{server_id}`

Delete Server

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `server_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `GET /v1/servers/{server_id}/base`

Base Status

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `server_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/servers/{server_id}/base/install`

Base Install

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `server_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/servers/{server_id}/base/setup`

Base Setup

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `server_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `POST /v1/servers/{server_id}/base/start`

Base Start

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `server_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/servers/{server_id}/base/stop`

Base Stop

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `server_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/servers/{server_id}/base/vnc-ticket`

Base Vnc Ticket

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `server_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/servers/{server_id}/status`

Server Status

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `server_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## App profiles

| Endpoint | Purpose |
| --- | --- |
| [`GET /v1/profiles`](#get-v1profiles) | List Profiles |
| [`PATCH /v1/profiles/{profile_id}`](#patch-v1profilesprofile_id) | Rename Profile |
| [`DELETE /v1/profiles/{profile_id}`](#delete-v1profilesprofile_id) | Delete Profile |
| [`GET /v1/profiles/{profile_id}/versions`](#get-v1profilesprofile_idversions) | List Profile Versions |
| [`DELETE /v1/profiles/{profile_id}/versions/{version}`](#delete-v1profilesprofile_idversionsversion) | Delete Profile Version |

### `GET /v1/profiles`

List Profiles

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PATCH /v1/profiles/{profile_id}`

Rename Profile

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `profile_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `DELETE /v1/profiles/{profile_id}`

Delete Profile

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `profile_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `GET /v1/profiles/{profile_id}/versions`

List Profile Versions

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `profile_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `DELETE /v1/profiles/{profile_id}/versions/{version}`

Delete Profile Version

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `profile_id` | path | `string` | yes |  |
| `version` | path | `integer` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## Vault and secrets

| Endpoint | Purpose |
| --- | --- |
| [`GET /v1/vault/activity`](#get-v1vaultactivity) | Activity |
| [`GET /v1/vault/reminders`](#get-v1vaultreminders) | Reminders |
| [`GET /v1/vault/secrets`](#get-v1vaultsecrets) | List Secrets |
| [`POST /v1/vault/secrets`](#post-v1vaultsecrets) | Create Secret |
| [`PATCH /v1/vault/secrets/{secret_id}`](#patch-v1vaultsecretssecret_id) | Update Secret |
| [`DELETE /v1/vault/secrets/{secret_id}`](#delete-v1vaultsecretssecret_id) | Delete Secret |

### `GET /v1/vault/activity`

Activity

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/vault/reminders`

Reminders

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/vault/secrets`

List Secrets

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/vault/secrets`

Create Secret

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `description` | `string or null` | no |  |
| `expires_at` | `string or null` | no |  |
| `name` | `string` | yes |  |
| `rotate_every_days` | `integer or null` | no |  |
| `value` | `string` | yes |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


### `PATCH /v1/vault/secrets/{secret_id}`

Update Secret

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `secret_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `description` | `string or null` | no |  |
| `expires_at` | `string or null` | no |  |
| `rotate_every_days` | `integer or null` | no |  |
| `value` | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `DELETE /v1/vault/secrets/{secret_id}`

Delete Secret

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `secret_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## API keys

| Endpoint | Purpose |
| --- | --- |
| [`GET /v1/api-keys`](#get-v1api-keys) | List Keys |
| [`POST /v1/api-keys`](#post-v1api-keys) | Create Key |
| [`DELETE /v1/api-keys/{key_id}`](#delete-v1api-keyskey_id) | Revoke Key |

### `GET /v1/api-keys`

List Keys

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/api-keys`

Create Key

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `expires_at` | `string or null` | no |  |
| `label_selector` | `object or null` | no |  |
| `name` | `string` | yes |  |
| `read_only` | `boolean` | no |  |
| `sandbox_id` | `string or null` | no |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


### `DELETE /v1/api-keys/{key_id}`

Revoke Key

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `key_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## Domains

| Endpoint | Purpose |
| --- | --- |
| [`GET /v1/domains/check`](#get-v1domainscheck) | Check Domain |

### `GET /v1/domains/check`

Caddy's on-demand TLS `ask`: a certificate is only issued for a hostname this answers 200 for, so
nobody can make the server request certificates for names it doesn't serve.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `domain` | query | `string` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## Admin

| Endpoint | Purpose |
| --- | --- |
| [`GET /v1/admin/backups`](#get-v1adminbackups) | List Backups |
| [`POST /v1/admin/backups`](#post-v1adminbackups) | Create Backup |
| [`GET /v1/admin/domains`](#get-v1admindomains) | List Domains |
| [`POST /v1/admin/domains`](#post-v1admindomains) | Add Domain |
| [`DELETE /v1/admin/domains/{domain_id}`](#delete-v1admindomainsdomain_id) | Delete Domain |
| [`POST /v1/admin/domains/{domain_id}/verify`](#post-v1admindomainsdomain_idverify) | Verify Domain |
| [`GET /v1/admin/sandboxes`](#get-v1adminsandboxes) | List Sandboxes |
| [`GET /v1/admin/users`](#get-v1adminusers) | List Users |

### `GET /v1/admin/backups`

List Backups

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/admin/backups`

Takes a backup now (server/backups.py): the database dump is done when this returns; the sandbox
snapshots are queued.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


### `GET /v1/admin/domains`

List Domains

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/admin/domains`

Add Domain

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `hostname` | `string` | yes |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


### `DELETE /v1/admin/domains/{domain_id}`

Delete Domain

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `domain_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `POST /v1/admin/domains/{domain_id}/verify`

Looks the domain up again, for after its DNS record was changed.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `domain_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/admin/sandboxes`

List Sandboxes

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/admin/users`

List Users

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## Auth

| Endpoint | Purpose |
| --- | --- |
| [`POST /v1/auth/login`](#post-v1authlogin) | Login |
| [`GET /v1/auth/me`](#get-v1authme) | Me |
| [`PUT /v1/auth/me/onboarded`](#put-v1authmeonboarded) | Onboarded |
| [`POST /v1/auth/signup`](#post-v1authsignup) | Register |

### `POST /v1/auth/login`

Login

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `email` | `string` | yes |  |
| `name` | `string or null` | no |  |
| `password` | `string` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/auth/me`

Me

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /v1/auth/me/onboarded`

Marks the first-run steps done (or skipped), so the dashboard stops showing them.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `POST /v1/auth/signup`

Register

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `email` | `string` | yes |  |
| `name` | `string or null` | no |  |
| `password` | `string` | yes |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


## Nodes

| Endpoint | Purpose |
| --- | --- |
| [`GET /v1/nodes/download/{os_name}/{arch}`](#get-v1nodesdownloados_namearch) | Download |
| [`POST /v1/nodes/join`](#post-v1nodesjoin) | Join |
| [`POST /v1/nodes/tokens`](#post-v1nodestokens) | Create Token |

### `GET /v1/nodes/download/{os_name}/{arch}`

This API's zoo-node build, for installers. The binary is the same for everyone, so it's public.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `os_name` | path | `string` | yes |  |
| `arch` | path | `string` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/nodes/join`

Called by `zoo-node join`, with no other credentials than the one-time token.

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `arch` | `string` | yes |  |
| `csr` | `string` | yes |  |
| `hostname` | `string` | no |  |
| `os` | `string` | yes |  |
| `ssh_host_key` | `string` | yes |  |
| `ssh_user` | `string` | yes |  |
| `token` | `string` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/nodes/tokens`

Create Token

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `platform` | query | `string` | no |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


## Tools

| Endpoint | Purpose |
| --- | --- |
| [`GET /v1/tools`](#get-v1tools) | List Tools |

### `GET /v1/tools`

List Tools

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `kind` | query | `string or null` | no |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## Monitoring

| Endpoint | Purpose |
| --- | --- |
| [`GET /v1/monitoring`](#get-v1monitoring) | Monitoring |

### `GET /v1/monitoring`

Monitoring

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## Audit-logs

| Endpoint | Purpose |
| --- | --- |
| [`GET /v1/audit-logs`](#get-v1audit-logs) | List Audit Logs |
| [`GET /v1/audit-logs/export`](#get-v1audit-logsexport) | Export Audit Logs |

### `GET /v1/audit-logs`

List Audit Logs

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `actor` | query | `string or null` | no |  |
| `action` | query | `string or null` | no |  |
| `resource_type` | query | `string or null` | no |  |
| `sandbox_id` | query | `string or null` | no |  |
| `since` | query | `string or null` | no |  |
| `until` | query | `string or null` | no |  |
| `limit` | query | `integer` | no |  |
| `cursor` | query | `string or null` | no | next_cursor of the page before |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/audit-logs/export`

Export Audit Logs

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `actor` | query | `string or null` | no |  |
| `action` | query | `string or null` | no |  |
| `resource_type` | query | `string or null` | no |  |
| `sandbox_id` | query | `string or null` | no |  |
| `since` | query | `string or null` | no |  |
| `until` | query | `string or null` | no |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## Embed

| Endpoint | Purpose |
| --- | --- |
| [`GET /embed/{token}`](#get-embedtoken) | Embed |
| [`GET /v1/embed/{token}/state`](#get-v1embedtokenstate) | Embed State |

### `GET /embed/{token}`

Embed

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `token` | path | `string` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/embed/{token}/state`

Embed State

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `token` | path | `string` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## Events

| Endpoint | Purpose |
| --- | --- |
| [`GET /v1/events`](#get-v1events) | List Events |
| [`GET /v1/events/stream`](#get-v1eventsstream) | Stream |

### `GET /v1/events`

List Events

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `after` | query | `integer` | no | the seq of the last event seen |
| `sandbox_id` | query | `string or null` | no |  |
| `type` | query | `string or null` | no |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/events/stream`

Stream

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `after` | query | `integer or null` | no | start after this seq; default: from now |
| `sandbox_id` | query | `string or null` | no |  |
| `type` | query | `string or null` | no |  |
| `last-event-id` | header | `string or null` | no |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## Invitations

| Endpoint | Purpose |
| --- | --- |
| [`POST /v1/invitations/{token}/accept`](#post-v1invitationstokenaccept) | Accept |

### `POST /v1/invitations/{token}/accept`

Accept

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `token` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## Kubernetes

| Endpoint | Purpose |
| --- | --- |
| [`GET /v1/kubernetes`](#get-v1kubernetes) | Kubernetes |
| [`POST /v1/kubernetes/check`](#post-v1kubernetescheck) | Kubernetes Check |

### `GET /v1/kubernetes`

Whether this machine's Linux sandboxes run on Kubernetes, and what the cluster lacks for them.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/kubernetes/check`

The requirements check with a probe pod under the RuntimeClass on every sandbox node (admins).

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## Platforms

| Endpoint | Purpose |
| --- | --- |
| [`GET /v1/platforms`](#get-v1platforms) | List Platforms |

### `GET /v1/platforms`

The built-in Linux runtime and the sandbox OSes available through registered node hosts.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## Pool

| Endpoint | Purpose |
| --- | --- |
| [`GET /v1/pool`](#get-v1pool) | List Pool |
| [`PUT /v1/pool`](#put-v1pool) | Set Pool |

### `GET /v1/pool`

List Pool

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /v1/pool`

Set Pool

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `kind` | `string` | yes |  |
| `server_id` | `string or null` | no |  |
| `size` | `integer` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## Profile-apps

| Endpoint | Purpose |
| --- | --- |
| [`GET /v1/profile-apps`](#get-v1profile-apps) | Profile Apps |

### `GET /v1/profile-apps`

Profile Apps

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `platform` | query | `string` | no |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## Usage

| Endpoint | Purpose |
| --- | --- |
| [`GET /v1/usage`](#get-v1usage) | Usage |
| [`GET /v1/usage/export`](#get-v1usageexport) | Export |

### `GET /v1/usage`

Usage

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `from` | query | `string` | yes |  |
| `to` | query | `string` | yes |  |
| `group_by` | query | `string` | no |  |
| `metric` | query | `string or null` | no |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/usage/export`

Export

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `from` | query | `string` | yes |  |
| `to` | query | `string` | yes |  |
| `group_by` | query | `string` | no |  |
| `metric` | query | `string or null` | no |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## Webhooks

| Endpoint | Purpose |
| --- | --- |
| [`GET /v1/webhooks`](#get-v1webhooks) | List Webhooks |
| [`POST /v1/webhooks`](#post-v1webhooks) | Create Webhook |
| [`PUT /v1/webhooks/{webhook_id}`](#put-v1webhookswebhook_id) | Update Webhook |
| [`DELETE /v1/webhooks/{webhook_id}`](#delete-v1webhookswebhook_id) | Delete Webhook |
| [`GET /v1/webhooks/{webhook_id}/deliveries`](#get-v1webhookswebhook_iddeliveries) | Deliveries |
| [`POST /v1/webhooks/{webhook_id}/test`](#post-v1webhookswebhook_idtest) | Test Webhook |

### `GET /v1/webhooks`

List Webhooks

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/webhooks`

Create Webhook

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `enabled` | `boolean` | no |  |
| `label_selector` | `object or null` | no |  |
| `types` | `string[]` | no |  |
| `url` | `string` | yes |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


### `PUT /v1/webhooks/{webhook_id}`

Update Webhook

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `webhook_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `enabled` | `boolean` | no |  |
| `label_selector` | `object or null` | no |  |
| `types` | `string[]` | no |  |
| `url` | `string` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `DELETE /v1/webhooks/{webhook_id}`

Delete Webhook

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `webhook_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `GET /v1/webhooks/{webhook_id}/deliveries`

Deliveries

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `webhook_id` | path | `string` | yes |  |
| `limit` | query | `integer` | no |  |
| `cursor` | query | `string or null` | no | next_cursor of the page before |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/webhooks/{webhook_id}/test`

Sends a webhook.test event to this webhook only; its delivery shows in /deliveries.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `webhook_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `202` | Successful Response |
| `422` | Validation Error |


## Workspaces

| Endpoint | Purpose |
| --- | --- |
| [`GET /v1/workspaces`](#get-v1workspaces) | List Workspaces |
| [`POST /v1/workspaces`](#post-v1workspaces) | Create Workspace |
| [`PATCH /v1/workspaces/{workspace_id}`](#patch-v1workspacesworkspace_id) | Rename Workspace |
| [`DELETE /v1/workspaces/{workspace_id}`](#delete-v1workspacesworkspace_id) | Delete Workspace |
| [`GET /v1/workspaces/{workspace_id}/invitations`](#get-v1workspacesworkspace_idinvitations) | List Invitations |
| [`POST /v1/workspaces/{workspace_id}/invitations`](#post-v1workspacesworkspace_idinvitations) | Invite |
| [`DELETE /v1/workspaces/{workspace_id}/invitations/{invitation_id}`](#delete-v1workspacesworkspace_idinvitationsinvitation_id) | Revoke Invitation |
| [`GET /v1/workspaces/{workspace_id}/label-quotas`](#get-v1workspacesworkspace_idlabel-quotas) | List Label Quotas |
| [`PUT /v1/workspaces/{workspace_id}/label-quotas`](#put-v1workspacesworkspace_idlabel-quotas) | Set Label Quota |
| [`DELETE /v1/workspaces/{workspace_id}/label-quotas`](#delete-v1workspacesworkspace_idlabel-quotas) | Remove Label Quota |
| [`PUT /v1/workspaces/{workspace_id}/lifecycle`](#put-v1workspacesworkspace_idlifecycle) | Set Lifecycle Defaults |
| [`GET /v1/workspaces/{workspace_id}/members`](#get-v1workspacesworkspace_idmembers) | List Members |
| [`PATCH /v1/workspaces/{workspace_id}/members/{user_id}`](#patch-v1workspacesworkspace_idmembersuser_id) | Change Role |
| [`DELETE /v1/workspaces/{workspace_id}/members/{user_id}`](#delete-v1workspacesworkspace_idmembersuser_id) | Remove Member |
| [`GET /v1/workspaces/{workspace_id}/quota`](#get-v1workspacesworkspace_idquota) | Get Quota |
| [`PUT /v1/workspaces/{workspace_id}/quota`](#put-v1workspacesworkspace_idquota) | Set Quota |

### `GET /v1/workspaces`

List Workspaces

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/workspaces`

Create Workspace

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


### `PATCH /v1/workspaces/{workspace_id}`

Rename Workspace

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `DELETE /v1/workspaces/{workspace_id}`

Delete Workspace

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `GET /v1/workspaces/{workspace_id}/invitations`

List Invitations

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /v1/workspaces/{workspace_id}/invitations`

Invite

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `email` | `string` | yes |  |
| `role` | `string` | no |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


### `DELETE /v1/workspaces/{workspace_id}/invitations/{invitation_id}`

Revoke Invitation

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `invitation_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `GET /v1/workspaces/{workspace_id}/label-quotas`

List Label Quotas

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /v1/workspaces/{workspace_id}/label-quotas`

Set Label Quota

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `key` | `string` | yes |  |
| `max_cpus` | `number or null` | no |  |
| `max_memory_mb` | `integer or null` | no |  |
| `max_running_sandboxes` | `integer or null` | no |  |
| `max_storage_gb` | `integer or null` | no |  |
| `value` | `string` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `DELETE /v1/workspaces/{workspace_id}/label-quotas`

Remove Label Quota

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `key` | query | `string` | yes |  |
| `value` | query | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `PUT /v1/workspaces/{workspace_id}/lifecycle`

Set Lifecycle Defaults

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `default_idle_timeout_minutes` | `integer or null` | no |  |
| `default_max_lifetime_minutes` | `integer or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /v1/workspaces/{workspace_id}/members`

List Members

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PATCH /v1/workspaces/{workspace_id}/members/{user_id}`

Change Role

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `user_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `role` | `string` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `DELETE /v1/workspaces/{workspace_id}/members/{user_id}`

Remove Member

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `user_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `GET /v1/workspaces/{workspace_id}/quota`

Get Quota

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /v1/workspaces/{workspace_id}/quota`

Set Quota

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `max_cpus` | `number or null` | no |  |
| `max_memory_mb` | `integer or null` | no |  |
| `max_running_sandboxes` | `integer or null` | no |  |
| `max_storage_gb` | `integer or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## Schemas

Request and response bodies, as the API defines them.


### `ActivityResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `action` | `string` | yes |  |
| `created_at` | `string` | yes |  |
| `id` | `string` | yes |  |
| `metadata` | `object` | yes |  |
| `resource_id` | `string or null` | yes |  |
| `resource_type` | `string` | yes |  |
| `sandbox_id` | `string or null` | yes |  |


### `ApiKeyRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `expires_at` | `string or null` | no |  |
| `label_selector` | `object or null` | no |  |
| `name` | `string` | yes |  |
| `read_only` | `boolean` | no |  |
| `sandbox_id` | `string or null` | no |  |


### `ApiKeyResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` | yes |  |
| `expires_at` | `string or null` | yes |  |
| `id` | `string` | yes |  |
| `key_prefix` | `string` | yes |  |
| `label_selector` | `object or null` | no |  |
| `last_used_at` | `string or null` | yes |  |
| `name` | `string` | yes |  |
| `read_only` | `boolean` | yes |  |
| `revoked_at` | `string or null` | yes |  |
| `sandbox_id` | `string or null` | yes |  |


### `AppPermissionRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `effect` | `string` | yes |  |


### `AppResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `binary` | `string` | yes |  |
| `effect` | `string` | yes |  |
| `name` | `string` | yes |  |


### `AttachedSecretResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |


### `AuditLogResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `action` | `string` | yes |  |
| `actor_email` | `string or null` | yes |  |
| `actor_id` | `string or null` | yes |  |
| `created_at` | `string` | yes |  |
| `id` | `string` | yes |  |
| `metadata` | `object` | yes |  |
| `resource_id` | `string or null` | yes |  |
| `resource_type` | `string` | yes |  |
| `sandbox_id` | `string or null` | yes |  |


### `AuditPage`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `data` | `AuditLogResponse[]` | yes |  |
| `next_cursor` | `string or null` | yes |  |


### `AuthRequst`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `email` | `string` | yes |  |
| `name` | `string or null` | no |  |
| `password` | `string` | yes |  |


### `BackupResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` | yes |  |
| `key` | `string` | yes |  |
| `size` | `integer` | yes |  |


### `BaseInstallRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `edition` | `string or null` | no |  |
| `iso` | `string or null` | no |  |


### `BaseStatus`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `message` | `string or null` | no |  |
| `progress` | `number or null` | no |  |
| `ready` | `boolean` | no |  |
| `state` | `string` | yes |  |


### `CreateSandboxRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `admin` | `boolean` | no |  |
| `cpus` | `number` | no |  |
| `disk_gb` | `integer or null` | no |  |
| `idle_timeout_minutes` | `integer or null` | no |  |
| `kind` | `string` | no |  |
| `labels` | `object` | no |  |
| `max_lifetime_minutes` | `integer or null` | no |  |
| `memory_mb` | `integer` | no |  |
| `name` | `string or null` | no |  |
| `profile_ids` | `string[]` | no |  |
| `queue` | `boolean` | no |  |
| `secret_ids` | `string[]` | no |  |
| `server_id` | `string or null` | no |  |


### `CreatedApiKeyResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` | yes |  |
| `expires_at` | `string or null` | yes |  |
| `id` | `string` | yes |  |
| `key` | `string` | yes |  |
| `key_prefix` | `string` | yes |  |
| `label_selector` | `object or null` | no |  |
| `last_used_at` | `string or null` | yes |  |
| `name` | `string` | yes |  |
| `read_only` | `boolean` | yes |  |
| `revoked_at` | `string or null` | yes |  |
| `sandbox_id` | `string or null` | yes |  |


### `CreatedInvitationResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` | yes |  |
| `email` | `string` | yes |  |
| `expires_at` | `string` | yes |  |
| `id` | `string` | yes |  |
| `role` | `string` | yes |  |
| `token` | `string` | yes |  |


### `CreatedViewerLinkResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` | yes |  |
| `end_user` | `string or null` | yes |  |
| `expires_at` | `string` | yes |  |
| `id` | `string` | yes |  |
| `mode` | `string` | yes |  |
| `revoked_at` | `string or null` | yes |  |
| `sandbox_id` | `string` | yes |  |
| `token` | `string` | yes |  |
| `url` | `string` | yes |  |


### `CreatedWebhookResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` | yes |  |
| `enabled` | `boolean` | yes |  |
| `id` | `string` | yes |  |
| `label_selector` | `object or null` | yes |  |
| `secret` | `string` | yes |  |
| `types` | `string[]` | yes |  |
| `url` | `string` | yes |  |


### `DeleteSandboxResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `deleted` | `boolean` | no |  |
| `id` | `string` | yes |  |


### `DeliveryResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `attempts` | `integer` | yes |  |
| `created_at` | `string` | yes |  |
| `delivered_at` | `string or null` | yes |  |
| `event_id` | `string` | yes |  |
| `event_type` | `string` | yes |  |
| `id` | `string` | yes |  |
| `last_error` | `string or null` | yes |  |
| `next_attempt_at` | `string` | yes |  |
| `response_status` | `integer or null` | yes |  |
| `status` | `string` | yes |  |


### `DomainRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `hostname` | `string` | yes |  |


### `DomainResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `addresses` | `string[]` | yes |  |
| `created_at` | `string` | yes |  |
| `hostname` | `string` | yes |  |
| `id` | `string` | yes |  |
| `points_here` | `boolean or null` | yes |  |
| `public_ip` | `string or null` | yes |  |
| `url` | `string` | yes |  |


### `EmbedStateResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `mode` | `string` | yes |  |
| `sandbox_status` | `string` | yes |  |


### `EventPage`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `after` | `integer` | yes |  |
| `events` | `EventResponse[]` | yes |  |


### `EventResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` | yes |  |
| `data` | `object` | yes |  |
| `id` | `string` | yes |  |
| `sandbox_id` | `string or null` | yes |  |
| `seq` | `integer` | yes |  |
| `type` | `string` | yes |  |


### `ExecRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `command` | `string` | yes |  |
| `timeout` | `integer` | no |  |


### `ExecResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `exit_code` | `integer` | yes |  |
| `sandbox_id` | `string` | yes |  |
| `stderr` | `string` | yes |  |
| `stdout` | `string` | yes |  |
| `timed_out` | `boolean` | no |  |


### `HTTPValidationError`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `detail` | `ValidationError[]` | no |  |


### `HostResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `architecture` | `string` | yes |  |
| `containers_running` | `integer` | yes |  |
| `cpus` | `integer` | yes |  |
| `docker_version` | `string` | yes |  |
| `images` | `integer` | yes |  |
| `memory_total` | `integer` | yes |  |
| `name` | `string` | yes |  |
| `os` | `string` | yes |  |


### `InvitationRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `email` | `string` | yes |  |
| `role` | `string` | no |  |


### `InvitationResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` | yes |  |
| `email` | `string` | yes |  |
| `expires_at` | `string` | yes |  |
| `id` | `string` | yes |  |
| `role` | `string` | yes |  |


### `JobResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `attempts` | `integer` | yes |  |
| `deadline` | `string or null` | yes |  |
| `kind` | `string` | yes |  |
| `last_error` | `string or null` | yes |  |
| `max_attempts` | `integer` | yes |  |
| `state` | `string` | yes |  |
| `waiting` | `boolean` | no |  |


### `JoinRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `arch` | `string` | yes |  |
| `csr` | `string` | yes |  |
| `hostname` | `string` | no |  |
| `os` | `string` | yes |  |
| `ssh_host_key` | `string` | yes |  |
| `ssh_user` | `string` | yes |  |
| `token` | `string` | yes |  |


### `JoinResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `ca` | `string` | yes |  |
| `certificate` | `string` | yes |  |
| `endpoints` | `string[]` | yes |  |
| `node_id` | `string` | yes |  |
| `server_id` | `string` | yes |  |


### `KubernetesCheck`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `detail` | `string` | yes |  |
| `name` | `string` | yes |  |
| `ok` | `boolean` | yes |  |


### `KubernetesStatus`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `checks` | `KubernetesCheck[]` | no |  |
| `enabled` | `boolean` | yes |  |
| `namespace` | `string` | no |  |
| `runtime_class` | `string` | no |  |


### `LabelQuotaRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `key` | `string` | yes |  |
| `max_cpus` | `number or null` | no |  |
| `max_memory_mb` | `integer or null` | no |  |
| `max_running_sandboxes` | `integer or null` | no |  |
| `max_storage_gb` | `integer or null` | no |  |
| `value` | `string` | yes |  |


### `LabelQuotaResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `cpus` | `number or null` | yes |  |
| `key` | `string` | yes |  |
| `max_cpus` | `number or null` | no |  |
| `max_memory_mb` | `integer or null` | no |  |
| `max_running_sandboxes` | `integer or null` | no |  |
| `max_storage_gb` | `integer or null` | no |  |
| `memory_mb` | `integer or null` | yes |  |
| `running_sandboxes` | `integer or null` | yes |  |
| `storage_gb` | `number or null` | yes |  |
| `value` | `string` | yes |  |


### `LifecycleDefaultsRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `default_idle_timeout_minutes` | `integer or null` | no |  |
| `default_max_lifetime_minutes` | `integer or null` | no |  |


### `LifecycleRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `idle_timeout_minutes` | `integer or null` | no |  |
| `max_lifetime_minutes` | `integer or null` | no |  |


### `MemberResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `email` | `string` | yes |  |
| `joined_at` | `string or null` | yes |  |
| `name` | `string or null` | yes |  |
| `role` | `string` | yes |  |
| `status` | `string` | yes |  |
| `user_id` | `string` | yes |  |


### `MonitoringResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `collected_at` | `string` | yes |  |
| `cpu_percent` | `number` | yes |  |
| `host` | `HostResponse` | yes |  |
| `memory_usage` | `integer` | yes |  |
| `sandboxes` | `object` | yes |  |
| `usage` | `SandboxUsage[]` | yes |  |


### `MoveSandboxRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `node_name` | `string or null` | no |  |
| `server_id` | `string or null` | no |  |


### `NetworkPolicyRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `allow_dns` | `boolean` | yes |  |
| `default_action` | `string` | yes |  |


### `NetworkResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `allow_dns` | `boolean` | yes |  |
| `default_action` | `string` | yes |  |
| `rules` | `NetworkRuleResponse[]` | yes |  |


### `NetworkRuleRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `effect` | `string` | yes |  |
| `rule_type` | `string` | yes |  |
| `value` | `string` | yes |  |


### `NetworkRuleResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `effect` | `string` | yes |  |
| `id` | `string` | yes |  |
| `rule_type` | `string` | yes |  |
| `value` | `string` | yes |  |


### `NodeCheck`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `detail` | `string` | no |  |
| `name` | `string` | yes |  |
| `ok` | `boolean` | yes |  |


### `NodeDriver`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `available` | `boolean` | yes |  |
| `detail` | `string` | no |  |
| `name` | `string` | yes |  |


### `NodeInfo`

The server's zoo-node and what it last reported.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `arch` | `string` | yes |  |
| `cert_expires_at` | `string` | yes |  |
| `checks` | `NodeCheck[]` | yes |  |
| `connected` | `boolean` | yes |  |
| `cpus` | `integer or null` | yes |  |
| `disk_free` | `integer or null` | yes |  |
| `disk_total` | `integer or null` | yes |  |
| `drivers` | `NodeDriver[]` | yes |  |
| `hostname` | `string` | yes |  |
| `id` | `string` | yes |  |
| `load` | `number or null` | yes |  |
| `memory_available` | `integer or null` | yes |  |
| `memory_total` | `integer or null` | yes |  |
| `os` | `string` | yes |  |
| `sandboxes` | `integer` | yes |  |
| `seen_at` | `string or null` | yes |  |
| `version` | `string` | yes |  |


### `Page_DeliveryResponse_`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `data` | `DeliveryResponse[]` | yes |  |
| `next_cursor` | `string or null` | yes |  |


### `Page_SandboxResponse_`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `data` | `SandboxResponse[]` | yes |  |
| `next_cursor` | `string or null` | yes |  |


### `Page_ToolExecutionResponse_`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `data` | `ToolExecutionResponse[]` | yes |  |
| `next_cursor` | `string or null` | yes |  |


### `PermissionRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `action` | `string` | yes |  |
| `effect` | `string` | yes |  |
| `permission` | `string` | yes |  |


### `PermissionResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `action` | `string` | yes |  |
| `effect` | `string` | yes |  |
| `label` | `string` | yes |  |
| `permission` | `string` | yes |  |


### `PlatformResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `available` | `boolean` | yes |  |
| `host` | `string` | yes |  |
| `id` | `string` | yes |  |
| `kinds` | `string[]` | yes |  |
| `name` | `string` | yes |  |
| `requirements` | `string` | yes |  |
| `runs` | `string[]` | yes |  |
| `servers` | `integer` | yes |  |


### `PoolEntry`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `booting` | `integer` | yes |  |
| `claimed` | `integer` | yes |  |
| `error` | `string or null` | yes |  |
| `idle` | `integer` | yes |  |
| `kind` | `string` | yes |  |
| `server_id` | `string or null` | yes |  |
| `server_name` | `string` | yes |  |
| `size` | `integer` | yes |  |


### `PoolRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `kind` | `string` | yes |  |
| `server_id` | `string or null` | no |  |
| `size` | `integer` | yes |  |


### `ProfileRename`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |


### `ProfileRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `app` | `string` | yes |  |
| `name` | `string` | yes |  |
| `profile_id` | `string or null` | no |  |


### `ProfileResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `app` | `string` | yes |  |
| `created_at` | `string` | yes |  |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |
| `platform` | `string` | yes |  |
| `size_bytes` | `integer` | yes |  |
| `updated_at` | `string` | yes |  |
| `version` | `integer` | yes |  |
| `versions` | `integer` | yes |  |


### `ProfileVersionResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` | yes |  |
| `sandbox_id` | `string or null` | yes |  |
| `size_bytes` | `integer` | yes |  |
| `version` | `integer` | yes |  |


### `QuotaRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `max_cpus` | `number or null` | no |  |
| `max_memory_mb` | `integer or null` | no |  |
| `max_running_sandboxes` | `integer or null` | no |  |
| `max_storage_gb` | `integer or null` | no |  |


### `QuotaResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `cpus` | `number` | yes |  |
| `max_cpus` | `number or null` | no |  |
| `max_memory_mb` | `integer or null` | no |  |
| `max_running_sandboxes` | `integer or null` | no |  |
| `max_storage_gb` | `integer or null` | no |  |
| `memory_mb` | `integer` | yes |  |
| `running_sandboxes` | `integer` | yes |  |
| `storage_gb` | `number` | yes |  |


### `ReminderResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `due_at` | `string` | yes |  |
| `id` | `string` | yes |  |
| `message` | `string` | yes |  |
| `name` | `string` | yes |  |
| `status` | `string` | yes |  |


### `RoleRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `role` | `string` | yes |  |


### `SandboxRef`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `last_used_at` | `string or null` | yes |  |
| `name` | `string` | yes |  |
| `status` | `string` | yes |  |


### `SandboxResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `base_version` | `string or null` | no |  |
| `boot_seconds` | `number or null` | no |  |
| `cpus` | `number` | no |  |
| `created_at` | `string` | yes |  |
| `disk_gb` | `integer or null` | no |  |
| `error_message` | `string or null` | yes |  |
| `id` | `string` | yes |  |
| `idle_timeout_minutes` | `integer or null` | no |  |
| `image` | `string or null` | no |  |
| `image_outdated` | `boolean` | no |  |
| `job` | `JobResponse or null` | no |  |
| `kind` | `string` | yes |  |
| `labels` | `object` | no |  |
| `last_activity_at` | `string or null` | no |  |
| `max_lifetime_minutes` | `integer or null` | no |  |
| `memory_mb` | `integer` | no |  |
| `name` | `string` | yes |  |
| `recovered_at` | `string or null` | no |  |
| `resumable` | `boolean` | no |  |
| `server_id` | `string or null` | yes |  |
| `started_at` | `string or null` | yes |  |
| `status` | `string` | yes |  |
| `unreachable` | `boolean` | no |  |


### `SandboxUsage`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `cpu_percent` | `number` | yes |  |
| `memory_limit` | `integer` | yes |  |
| `memory_percent` | `number` | yes |  |
| `memory_usage` | `integer` | yes |  |
| `name` | `string` | yes |  |
| `network_rx` | `integer` | yes |  |
| `network_tx` | `integer` | yes |  |
| `pids` | `integer` | yes |  |
| `sandbox_id` | `string` | yes |  |
| `status` | `string` | yes |  |


### `SecretRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |
| `value` | `string` | yes |  |


### `SecretResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` | yes |  |
| `enabled` | `boolean` | yes |  |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |


### `ServerResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `bind_address` | `string` | yes |  |
| `capabilities` | `string[]` | yes |  |
| `created_at` | `string` | yes |  |
| `docker_url` | `string` | yes |  |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |
| `node` | `NodeInfo or null` | no |  |
| `platform` | `string` | yes |  |


### `ServerStatus`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `containers_running` | `integer or null` | no |  |
| `cpus` | `integer or null` | no |  |
| `docker_version` | `string or null` | no |  |
| `error` | `string or null` | no |  |
| `memory_total` | `integer or null` | no |  |
| `microvm` | `boolean or null` | no |  |
| `name` | `string or null` | no |  |
| `online` | `boolean` | yes |  |
| `os` | `string or null` | no |  |
| `sandboxes` | `integer` | no |  |


### `SnapshotRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | no |  |


### `SnapshotResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` | yes |  |
| `error` | `string or null` | yes |  |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |
| `size_bytes` | `integer` | yes |  |
| `state` | `string` | yes |  |


### `TokenRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |


### `ToolExecutionResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `completed_at` | `string or null` | yes |  |
| `created_at` | `string` | yes |  |
| `error_message` | `string or null` | yes |  |
| `id` | `string` | yes |  |
| `input` | `string` | yes |  |
| `status` | `string` | yes |  |
| `tool_name` | `string` | yes |  |


### `UpdateSandboxRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `labels` | `object or null` | no |  |
| `name` | `string or null` | no |  |


### `UsageReport`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `group_by` | `string` | yes |  |
| `rows` | `UsageRow[]` | yes |  |
| `since` | `string` | yes |  |
| `until` | `string` | yes |  |


### `UsageRow`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `group` | `string or null` | yes |  |
| `metric` | `string` | yes |  |
| `quantity` | `number` | yes |  |


### `UserResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `avatar_url` | `string or null` | yes |  |
| `email` | `string` | yes |  |
| `id` | `string` | yes |  |
| `name` | `string or null` | yes |  |
| `onboarded_at` | `string or null` | no |  |


### `ValidationError`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `ctx` | `object` | no |  |
| `input` | `any` | no |  |
| `loc` | `string or integer[]` | yes |  |
| `msg` | `string` | yes |  |
| `type` | `string` | yes |  |


### `VaultSecretRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `description` | `string or null` | no |  |
| `expires_at` | `string or null` | no |  |
| `name` | `string` | yes |  |
| `rotate_every_days` | `integer or null` | no |  |
| `value` | `string` | yes |  |


### `VaultSecretResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` | yes |  |
| `description` | `string or null` | yes |  |
| `expires_at` | `string or null` | yes |  |
| `id` | `string` | yes |  |
| `last_used_at` | `string or null` | yes |  |
| `name` | `string` | yes |  |
| `rotate_every_days` | `integer or null` | yes |  |
| `rotated_at` | `string or null` | yes |  |
| `rotation_due_at` | `string or null` | yes |  |
| `sandboxes` | `SandboxRef[]` | yes |  |
| `status` | `string` | yes |  |
| `updated_at` | `string` | yes |  |


### `VaultSecretUpdate`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `description` | `string or null` | no |  |
| `expires_at` | `string or null` | no |  |
| `rotate_every_days` | `integer or null` | no |  |
| `value` | `string or null` | no |  |


### `ViewerLinkRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `end_user` | `string or null` | no |  |
| `expires_in_minutes` | `integer` | no |  |
| `mode` | `string` | no |  |


### `ViewerLinkResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` | yes |  |
| `end_user` | `string or null` | yes |  |
| `expires_at` | `string` | yes |  |
| `id` | `string` | yes |  |
| `mode` | `string` | yes |  |
| `revoked_at` | `string or null` | yes |  |
| `sandbox_id` | `string` | yes |  |


### `VncTicketResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `expires_in` | `integer` | no |  |
| `ticket` | `string` | yes |  |


### `WebhookRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `enabled` | `boolean` | no |  |
| `label_selector` | `object or null` | no |  |
| `types` | `string[]` | no |  |
| `url` | `string` | yes |  |


### `WebhookResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` | yes |  |
| `enabled` | `boolean` | yes |  |
| `id` | `string` | yes |  |
| `label_selector` | `object or null` | yes |  |
| `types` | `string[]` | yes |  |
| `url` | `string` | yes |  |


### `WorkspaceRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |


### `WorkspaceResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `created_at` | `string` | yes |  |
| `default_idle_timeout_minutes` | `integer or null` | yes |  |
| `default_max_lifetime_minutes` | `integer or null` | yes |  |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |
| `personal` | `boolean` | yes |  |
| `role` | `string` | yes |  |
| `slug` | `string` | yes |  |


### `server__auth_api__TokenResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `access_token` | `string` | yes |  |
| `token_type` | `string` | no |  |
| `user_id` | `string` | yes |  |


### `server__nodes_api__TokenResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `expires_at` | `string` | yes |  |
| `install_command` | `string` | yes |  |
| `join_command` | `string` | yes |  |
| `token` | `string` | yes |  |

