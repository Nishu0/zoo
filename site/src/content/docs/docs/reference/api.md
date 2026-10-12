---
title: REST API
description: Every HTTP endpoint the Zoo API serves, generated from its OpenAPI schema.
---

:::note
Generated from the code by `site/scripts/generate_reference.py` — do not edit by hand.
Regenerate with `bun run docs:gen` from `site/`.
:::

The API serves the same schema at `/docs` on a running server. Every request is
authenticated with an API key unless it is under `/auth`:
- **HTTPBearer**: http — 

The Python SDK wraps these endpoints; see [SDK](/docs/reference/sdk/).

## Sandboxes

| Endpoint | Purpose |
| --- | --- |
| [`GET /sandboxes`](#get-sandboxes) | List Sandboxes |
| [`POST /sandboxes`](#post-sandboxes) | Create Sandbox |
| [`GET /sandboxes/{sandbox_id}`](#get-sandboxessandbox_id) | Get Sandbox |
| [`PATCH /sandboxes/{sandbox_id}`](#patch-sandboxessandbox_id) | Update Sandbox |
| [`DELETE /sandboxes/{sandbox_id}`](#delete-sandboxessandbox_id) | Delete Sandbox |
| [`GET /sandboxes/{sandbox_id}/agent`](#get-sandboxessandbox_idagent) | State |
| [`POST /sandboxes/{sandbox_id}/agent`](#post-sandboxessandbox_idagent) | Chat |
| [`DELETE /sandboxes/{sandbox_id}/agent`](#delete-sandboxessandbox_idagent) | Reset |
| [`GET /sandboxes/{sandbox_id}/agent/channels`](#get-sandboxessandbox_idagentchannels) | List Channels |
| [`POST /sandboxes/{sandbox_id}/agent/channels`](#post-sandboxessandbox_idagentchannels) | Add Channel |
| [`PATCH /sandboxes/{sandbox_id}/agent/channels/{channel_id}`](#patch-sandboxessandbox_idagentchannelschannel_id) | Update Channel |
| [`DELETE /sandboxes/{sandbox_id}/agent/channels/{channel_id}`](#delete-sandboxessandbox_idagentchannelschannel_id) | Remove Channel |
| [`GET /sandboxes/{sandbox_id}/agent/messages/{message_id}/screenshot`](#get-sandboxessandbox_idagentmessagesmessage_idscreenshot) | Screenshot |
| [`POST /sandboxes/{sandbox_id}/agent/release`](#post-sandboxessandbox_idagentrelease) | Release |
| [`POST /sandboxes/{sandbox_id}/agent/stop`](#post-sandboxessandbox_idagentstop) | Stop |
| [`GET /sandboxes/{sandbox_id}/agent/stream`](#get-sandboxessandbox_idagentstream) | Attach |
| [`POST /sandboxes/{sandbox_id}/agent/takeover`](#post-sandboxessandbox_idagenttakeover) | Takeover |
| [`GET /sandboxes/{sandbox_id}/apps`](#get-sandboxessandbox_idapps) | List Apps |
| [`PUT /sandboxes/{sandbox_id}/apps/{binary}`](#put-sandboxessandbox_idappsbinary) | Set App |
| [`GET /sandboxes/{sandbox_id}/backup`](#get-sandboxessandbox_idbackup) | Backup |
| [`POST /sandboxes/{sandbox_id}/exec`](#post-sandboxessandbox_idexec) | Execute |
| [`GET /sandboxes/{sandbox_id}/executions`](#get-sandboxessandbox_idexecutions) | List Executions |
| [`GET /sandboxes/{sandbox_id}/guest`](#get-sandboxessandbox_idguest) | Guest Status |
| [`PUT /sandboxes/{sandbox_id}/lifecycle`](#put-sandboxessandbox_idlifecycle) | Set Lifecycle |
| [`POST /sandboxes/{sandbox_id}/move`](#post-sandboxessandbox_idmove) | Move Sandbox |
| [`GET /sandboxes/{sandbox_id}/network`](#get-sandboxessandbox_idnetwork) | Get Network |
| [`PUT /sandboxes/{sandbox_id}/network`](#put-sandboxessandbox_idnetwork) | Set Network |
| [`POST /sandboxes/{sandbox_id}/network/rules`](#post-sandboxessandbox_idnetworkrules) | Add Rule |
| [`DELETE /sandboxes/{sandbox_id}/network/rules/{rule_id}`](#delete-sandboxessandbox_idnetworkrulesrule_id) | Delete Rule |
| [`GET /sandboxes/{sandbox_id}/permissions`](#get-sandboxessandbox_idpermissions) | List Permissions |
| [`PUT /sandboxes/{sandbox_id}/permissions`](#put-sandboxessandbox_idpermissions) | Set Permission |
| [`POST /sandboxes/{sandbox_id}/profiles`](#post-sandboxessandbox_idprofiles) | Capture Profile |
| [`POST /sandboxes/{sandbox_id}/profiles/{profile_id}`](#post-sandboxessandbox_idprofilesprofile_id) | Apply Profile |
| [`POST /sandboxes/{sandbox_id}/restore`](#post-sandboxessandbox_idrestore) | Restore |
| [`POST /sandboxes/{sandbox_id}/screenshot`](#post-sandboxessandbox_idscreenshot) | Screenshot |
| [`GET /sandboxes/{sandbox_id}/secrets`](#get-sandboxessandbox_idsecrets) | List Secrets |
| [`PUT /sandboxes/{sandbox_id}/secrets`](#put-sandboxessandbox_idsecrets) | Set Secret |
| [`DELETE /sandboxes/{sandbox_id}/secrets/{secret_id}`](#delete-sandboxessandbox_idsecretssecret_id) | Delete Secret |
| [`GET /sandboxes/{sandbox_id}/snapshots`](#get-sandboxessandbox_idsnapshots) | List Snapshots |
| [`POST /sandboxes/{sandbox_id}/snapshots`](#post-sandboxessandbox_idsnapshots) | Create Snapshot |
| [`DELETE /sandboxes/{sandbox_id}/snapshots/{snapshot_id}`](#delete-sandboxessandbox_idsnapshotssnapshot_id) | Delete Snapshot |
| [`POST /sandboxes/{sandbox_id}/snapshots/{snapshot_id}/restore`](#post-sandboxessandbox_idsnapshotssnapshot_idrestore) | Restore From Snapshot |
| [`POST /sandboxes/{sandbox_id}/start`](#post-sandboxessandbox_idstart) | Start Sandbox |
| [`POST /sandboxes/{sandbox_id}/stop`](#post-sandboxessandbox_idstop) | Stop Sandbox |
| [`POST /sandboxes/{sandbox_id}/terminal-ticket`](#post-sandboxessandbox_idterminal-ticket) | Terminal Ticket |
| [`POST /sandboxes/{sandbox_id}/tools/{name}`](#post-sandboxessandbox_idtoolsname) | Call Tool |
| [`POST /sandboxes/{sandbox_id}/upgrade`](#post-sandboxessandbox_idupgrade) | Upgrade Sandbox |
| [`GET /sandboxes/{sandbox_id}/vault-secrets`](#get-sandboxessandbox_idvault-secrets) | Attached Secrets |
| [`PUT /sandboxes/{sandbox_id}/vault-secrets/{secret_id}`](#put-sandboxessandbox_idvault-secretssecret_id) | Attach Secret |
| [`DELETE /sandboxes/{sandbox_id}/vault-secrets/{secret_id}`](#delete-sandboxessandbox_idvault-secretssecret_id) | Detach Secret |
| [`GET /sandboxes/{sandbox_id}/viewer-links`](#get-sandboxessandbox_idviewer-links) | List Links |
| [`POST /sandboxes/{sandbox_id}/viewer-links`](#post-sandboxessandbox_idviewer-links) | Create Link |
| [`DELETE /sandboxes/{sandbox_id}/viewer-links/{link_id}`](#delete-sandboxessandbox_idviewer-linkslink_id) | Revoke Link |
| [`POST /sandboxes/{sandbox_id}/vnc-ticket`](#post-sandboxessandbox_idvnc-ticket) | Vnc Ticket |

### `GET /sandboxes`

List Sandboxes

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `label` | query | `string[]` | no | key=value; repeat to match all of them |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /sandboxes`

Create Sandbox

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string or null` | no |  |
| `kind` | `string` | no |  |
| `server_id` | `string or null` | no |  |
| `profile_ids` | `string[]` | no |  |
| `secret_ids` | `string[]` | no |  |
| `admin` | `boolean` | no |  |
| `queue` | `boolean` | no |  |
| `cpus` | `number` | no |  |
| `memory_mb` | `integer` | no |  |
| `disk_gb` | `integer or null` | no |  |
| `idle_timeout_minutes` | `integer or null` | no |  |
| `max_lifetime_minutes` | `integer or null` | no |  |
| `labels` | `object` | no |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


### `GET /sandboxes/{sandbox_id}`

Get Sandbox

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PATCH /sandboxes/{sandbox_id}`

Update Sandbox

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string or null` | no |  |
| `labels` | `object or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `DELETE /sandboxes/{sandbox_id}`

Delete Sandbox

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /sandboxes/{sandbox_id}/agent`

State

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /sandboxes/{sandbox_id}/agent`

Chat

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `message` | `string` | yes |  |
| `model` | `string or null` | no |  |
| `provider` | `string or null` | no |  |
| `api_base` | `string or null` | no |  |
| `stream` | `boolean` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `DELETE /sandboxes/{sandbox_id}/agent`

Reset

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `GET /sandboxes/{sandbox_id}/agent/channels`

List Channels

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /sandboxes/{sandbox_id}/agent/channels`

Add Channel

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `platform` | `string` | yes |  |
| `external_id` | `string` | yes |  |
| `allowed_users` | `string[]` | no |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


### `PATCH /sandboxes/{sandbox_id}/agent/channels/{channel_id}`

Update Channel

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `channel_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `allowed_users` | `string[]` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `DELETE /sandboxes/{sandbox_id}/agent/channels/{channel_id}`

Remove Channel

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `channel_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `GET /sandboxes/{sandbox_id}/agent/messages/{message_id}/screenshot`

Screenshot

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `message_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /sandboxes/{sandbox_id}/agent/release`

Hands the screen back to the agent, which carries on from the screen as the person left it.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /sandboxes/{sandbox_id}/agent/stop`

Stop

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `GET /sandboxes/{sandbox_id}/agent/stream`

Attach

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /sandboxes/{sandbox_id}/agent/takeover`

Pauses the running agent before its next action, so a person can use the screen.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /sandboxes/{sandbox_id}/apps`

List Apps

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /sandboxes/{sandbox_id}/apps/{binary}`

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


### `GET /sandboxes/{sandbox_id}/backup`

Backup

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /sandboxes/{sandbox_id}/exec`

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


### `GET /sandboxes/{sandbox_id}/executions`

List Executions

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /sandboxes/{sandbox_id}/guest`

Guest Status

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /sandboxes/{sandbox_id}/lifecycle`

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


### `POST /sandboxes/{sandbox_id}/move`

Move Sandbox

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `server_id` | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /sandboxes/{sandbox_id}/network`

Get Network

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /sandboxes/{sandbox_id}/network`

Set Network

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `default_action` | `string` | yes |  |
| `allow_dns` | `boolean` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /sandboxes/{sandbox_id}/network/rules`

Add Rule

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `rule_type` | `string` | yes |  |
| `value` | `string` | yes |  |
| `effect` | `string` | yes |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


### `DELETE /sandboxes/{sandbox_id}/network/rules/{rule_id}`

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


### `GET /sandboxes/{sandbox_id}/permissions`

List Permissions

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /sandboxes/{sandbox_id}/permissions`

Set Permission

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `permission` | `string` | yes |  |
| `action` | `string` | yes |  |
| `effect` | `string` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /sandboxes/{sandbox_id}/profiles`

Capture Profile

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |
| `app` | `string` | yes |  |
| `profile_id` | `string or null` | no |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


### `POST /sandboxes/{sandbox_id}/profiles/{profile_id}`

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


### `POST /sandboxes/{sandbox_id}/restore`

Restore

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /sandboxes/{sandbox_id}/screenshot`

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


### `GET /sandboxes/{sandbox_id}/secrets`

List Secrets

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /sandboxes/{sandbox_id}/secrets`

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


### `DELETE /sandboxes/{sandbox_id}/secrets/{secret_id}`

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


### `GET /sandboxes/{sandbox_id}/snapshots`

List Snapshots

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /sandboxes/{sandbox_id}/snapshots`

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


### `DELETE /sandboxes/{sandbox_id}/snapshots/{snapshot_id}`

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


### `POST /sandboxes/{sandbox_id}/snapshots/{snapshot_id}/restore`

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


### `POST /sandboxes/{sandbox_id}/start`

Start Sandbox

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /sandboxes/{sandbox_id}/stop`

Stop Sandbox

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /sandboxes/{sandbox_id}/terminal-ticket`

Terminal Ticket

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /sandboxes/{sandbox_id}/tools/{name}`

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


### `POST /sandboxes/{sandbox_id}/upgrade`

Restarts the sandbox on the current default image. Its home volume is kept.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /sandboxes/{sandbox_id}/vault-secrets`

Attached Secrets

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /sandboxes/{sandbox_id}/vault-secrets/{secret_id}`

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


### `DELETE /sandboxes/{sandbox_id}/vault-secrets/{secret_id}`

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


### `GET /sandboxes/{sandbox_id}/viewer-links`

List Links

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /sandboxes/{sandbox_id}/viewer-links`

Create Link

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sandbox_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `mode` | `string` | no |  |
| `expires_in_minutes` | `integer` | no |  |
| `end_user` | `string or null` | no |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


### `DELETE /sandboxes/{sandbox_id}/viewer-links/{link_id}`

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


### `POST /sandboxes/{sandbox_id}/vnc-ticket`

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
| [`GET /servers`](#get-servers) | List Servers |
| [`POST /servers`](#post-servers) | Add Server |
| [`GET /servers/install-command`](#get-serversinstall-command) | Server Install Command |
| [`DELETE /servers/{server_id}`](#delete-serversserver_id) | Delete Server |
| [`GET /servers/{server_id}/base`](#get-serversserver_idbase) | Base Status |
| [`POST /servers/{server_id}/base/install`](#post-serversserver_idbaseinstall) | Base Install |
| [`POST /servers/{server_id}/base/setup`](#post-serversserver_idbasesetup) | Base Setup |
| [`POST /servers/{server_id}/base/start`](#post-serversserver_idbasestart) | Base Start |
| [`POST /servers/{server_id}/base/stop`](#post-serversserver_idbasestop) | Base Stop |
| [`POST /servers/{server_id}/base/vnc-ticket`](#post-serversserver_idbasevnc-ticket) | Base Vnc Ticket |
| [`POST /servers/{server_id}/migrate`](#post-serversserver_idmigrate) | Migrate Server |
| [`GET /servers/{server_id}/status`](#get-serversserver_idstatus) | Server Status |

### `GET /servers`

List Servers

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /servers`

Add Server

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |
| `docker_url` | `string` | yes |  |
| `bind_address` | `string` | yes |  |
| `platform` | `string` | no |  |
| `host_key` | `string or null` | no |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


### `GET /servers/install-command`

Server Install Command

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `platform` | query | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `DELETE /servers/{server_id}`

Delete Server

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `server_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `GET /servers/{server_id}/base`

Base Status

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `server_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /servers/{server_id}/base/install`

Base Install

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `server_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /servers/{server_id}/base/setup`

Base Setup

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `server_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `POST /servers/{server_id}/base/start`

Base Start

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `server_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /servers/{server_id}/base/stop`

Base Stop

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `server_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /servers/{server_id}/base/vnc-ticket`

Base Vnc Ticket

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `server_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /servers/{server_id}/migrate`

Migrate Server

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `server_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `GET /servers/{server_id}/status`

Server Status

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `server_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## Agent

| Endpoint | Purpose |
| --- | --- |
| [`GET /agent/exports`](#get-agentexports) | List Exports |
| [`POST /agent/exports`](#post-agentexports) | Create Export |
| [`GET /agent/exports/{export_id}`](#get-agentexportsexport_id) | Get Export |
| [`GET /agent/exports/{export_id}/download`](#get-agentexportsexport_iddownload) | Download |
| [`GET /agent/integrations`](#get-agentintegrations) | Integrations |
| [`GET /agent/providers`](#get-agentproviders) | Providers |
| [`GET /agent/settings`](#get-agentsettings) | Get Settings |
| [`PUT /agent/settings`](#put-agentsettings) | Put Settings |
| [`DELETE /agent/settings`](#delete-agentsettings) | Reset Settings |

### `GET /agent/exports`

List Exports

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /agent/exports`

Create Export

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `run_id` | `string or null` | no |  |
| `sandbox_id` | `string or null` | no |  |
| `labels` | `object or null` | no |  |
| `from` | `string or null` | no |  |
| `to` | `string or null` | no |  |

| Response | |
| --- | --- |
| `202` | Successful Response |
| `422` | Validation Error |


### `GET /agent/exports/{export_id}`

Get Export

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `export_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /agent/exports/{export_id}/download`

Download

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `export_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /agent/integrations`

Integrations

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /agent/providers`

What a run can be started with, and whether this workspace has a key for each.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /agent/settings`

Get Settings

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /agent/settings`

Put Settings

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `provider` | `string` | yes |  |
| `model` | `string` | yes |  |
| `api_key` | `string or null` | no |  |
| `api_key_secret_id` | `string or null` | no |  |
| `api_base` | `string or null` | no |  |
| `max_steps` | `integer or null` | no |  |
| `max_seconds` | `integer or null` | no |  |
| `max_tokens` | `integer or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `DELETE /agent/settings`

Reset Settings

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## App profiles

| Endpoint | Purpose |
| --- | --- |
| [`GET /profiles`](#get-profiles) | List Profiles |
| [`PATCH /profiles/{profile_id}`](#patch-profilesprofile_id) | Rename Profile |
| [`DELETE /profiles/{profile_id}`](#delete-profilesprofile_id) | Delete Profile |
| [`GET /profiles/{profile_id}/versions`](#get-profilesprofile_idversions) | List Profile Versions |
| [`DELETE /profiles/{profile_id}/versions/{version}`](#delete-profilesprofile_idversionsversion) | Delete Profile Version |

### `GET /profiles`

List Profiles

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PATCH /profiles/{profile_id}`

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


### `DELETE /profiles/{profile_id}`

Delete Profile

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `profile_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `GET /profiles/{profile_id}/versions`

List Profile Versions

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `profile_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `DELETE /profiles/{profile_id}/versions/{version}`

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
| [`GET /vault/activity`](#get-vaultactivity) | Activity |
| [`GET /vault/reminders`](#get-vaultreminders) | Reminders |
| [`GET /vault/secrets`](#get-vaultsecrets) | List Secrets |
| [`POST /vault/secrets`](#post-vaultsecrets) | Create Secret |
| [`PATCH /vault/secrets/{secret_id}`](#patch-vaultsecretssecret_id) | Update Secret |
| [`DELETE /vault/secrets/{secret_id}`](#delete-vaultsecretssecret_id) | Delete Secret |

### `GET /vault/activity`

Activity

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /vault/reminders`

Reminders

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /vault/secrets`

List Secrets

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /vault/secrets`

Create Secret

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |
| `value` | `string` | yes |  |
| `description` | `string or null` | no |  |
| `expires_at` | `string or null` | no |  |
| `rotate_every_days` | `integer or null` | no |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


### `PATCH /vault/secrets/{secret_id}`

Update Secret

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `secret_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `value` | `string or null` | no |  |
| `description` | `string or null` | no |  |
| `expires_at` | `string or null` | no |  |
| `rotate_every_days` | `integer or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `DELETE /vault/secrets/{secret_id}`

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
| [`GET /api-keys`](#get-api-keys) | List Keys |
| [`POST /api-keys`](#post-api-keys) | Create Key |
| [`DELETE /api-keys/{key_id}`](#delete-api-keyskey_id) | Revoke Key |

### `GET /api-keys`

List Keys

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /api-keys`

Create Key

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |
| `sandbox_id` | `string or null` | no |  |
| `label_selector` | `object or null` | no |  |
| `read_only` | `boolean` | no |  |
| `expires_at` | `string or null` | no |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


### `DELETE /api-keys/{key_id}`

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
| [`GET /domains/check`](#get-domainscheck) | Check Domain |

### `GET /domains/check`

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
| [`GET /admin/backups`](#get-adminbackups) | List Backups |
| [`POST /admin/backups`](#post-adminbackups) | Create Backup |
| [`GET /admin/domains`](#get-admindomains) | List Domains |
| [`POST /admin/domains`](#post-admindomains) | Add Domain |
| [`DELETE /admin/domains/{domain_id}`](#delete-admindomainsdomain_id) | Delete Domain |
| [`POST /admin/domains/{domain_id}/verify`](#post-admindomainsdomain_idverify) | Verify Domain |
| [`GET /admin/sandboxes`](#get-adminsandboxes) | List Sandboxes |
| [`GET /admin/users`](#get-adminusers) | List Users |

### `GET /admin/backups`

List Backups

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /admin/backups`

Takes a backup now (server/backups.py): the database dump is done when this returns; the sandbox
snapshots are queued.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


### `GET /admin/domains`

List Domains

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /admin/domains`

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


### `DELETE /admin/domains/{domain_id}`

Delete Domain

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `domain_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `POST /admin/domains/{domain_id}/verify`

Looks the domain up again, for after its DNS record was changed.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `domain_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /admin/sandboxes`

List Sandboxes

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /admin/users`

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
| [`POST /auth/login`](#post-authlogin) | Login |
| [`GET /auth/me`](#get-authme) | Me |
| [`POST /auth/signup`](#post-authsignup) | Register |

### `POST /auth/login`

Login

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `email` | `string` | yes |  |
| `password` | `string` | yes |  |
| `name` | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `GET /auth/me`

Me

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /auth/signup`

Register

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `email` | `string` | yes |  |
| `password` | `string` | yes |  |
| `name` | `string or null` | no |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


## Nodes

| Endpoint | Purpose |
| --- | --- |
| [`GET /nodes/download/{os_name}/{arch}`](#get-nodesdownloados_namearch) | Download |
| [`POST /nodes/join`](#post-nodesjoin) | Join |
| [`POST /nodes/tokens`](#post-nodestokens) | Create Token |

### `GET /nodes/download/{os_name}/{arch}`

This API's zoo-node build, for installers. The binary is the same for everyone, so it's public.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `os_name` | path | `string` | yes |  |
| `arch` | path | `string` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /nodes/join`

Called by `zoo-node join`, with no other credentials than the one-time token.

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `token` | `string` | yes |  |
| `csr` | `string` | yes |  |
| `hostname` | `string` | no |  |
| `os` | `string` | yes |  |
| `arch` | `string` | yes |  |
| `ssh_user` | `string or null` | no |  |
| `ssh_host_key` | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /nodes/tokens`

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
| [`GET /tools`](#get-tools) | List Tools |

### `GET /tools`

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
| [`GET /monitoring`](#get-monitoring) | Monitoring |

### `GET /monitoring`

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
| [`GET /audit-logs`](#get-audit-logs) | List Audit Logs |
| [`GET /audit-logs/export`](#get-audit-logsexport) | Export Audit Logs |

### `GET /audit-logs`

List Audit Logs

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `cursor` | query | `string or null` | no |  |
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


### `GET /audit-logs/export`

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


## Benchmarks

| Endpoint | Purpose |
| --- | --- |
| [`GET /benchmarks`](#get-benchmarks) | List Benchmarks |
| [`POST /benchmarks`](#post-benchmarks) | Create Benchmark |
| [`GET /benchmarks/{benchmark_id}`](#get-benchmarksbenchmark_id) | Get Benchmark |
| [`POST /benchmarks/{benchmark_id}/cancel`](#post-benchmarksbenchmark_idcancel) | Cancel Benchmark |
| [`GET /benchmarks/{benchmark_id}/export`](#get-benchmarksbenchmark_idexport) | Export Benchmark |

### `GET /benchmarks`

List Benchmarks

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /benchmarks`

Create Benchmark

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `suite` | `string or object` | yes |  |
| `models` | `ModelChoice[]` | yes |  |
| `parallel` | `integer` | no |  |

| Response | |
| --- | --- |
| `202` | Successful Response |
| `422` | Validation Error |


### `GET /benchmarks/{benchmark_id}`

Get Benchmark

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `benchmark_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /benchmarks/{benchmark_id}/cancel`

Cancel Benchmark

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `benchmark_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `GET /benchmarks/{benchmark_id}/export`

Export Benchmark

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `benchmark_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## Embed

| Endpoint | Purpose |
| --- | --- |
| [`GET /embed/{token}`](#get-embedtoken) | Embed |
| [`POST /embed/{token}/release`](#post-embedtokenrelease) | Embed Release |
| [`GET /embed/{token}/state`](#get-embedtokenstate) | Embed State |
| [`POST /embed/{token}/takeover`](#post-embedtokentakeover) | Embed Takeover |

### `GET /embed/{token}`

Embed

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `token` | path | `string` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /embed/{token}/release`

Embed Release

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `token` | path | `string` | yes |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `GET /embed/{token}/state`

Embed State

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `token` | path | `string` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /embed/{token}/takeover`

Embed Takeover

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `token` | path | `string` | yes |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


## Events

| Endpoint | Purpose |
| --- | --- |
| [`GET /events`](#get-events) | List Events |
| [`GET /events/stream`](#get-eventsstream) | Stream |

### `GET /events`

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


### `GET /events/stream`

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
| [`POST /invitations/{token}/accept`](#post-invitationstokenaccept) | Accept |

### `POST /invitations/{token}/accept`

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
| [`GET /kubernetes`](#get-kubernetes) | Kubernetes |
| [`POST /kubernetes/check`](#post-kubernetescheck) | Kubernetes Check |

### `GET /kubernetes`

Whether this machine's Linux sandboxes run on Kubernetes, and what the cluster lacks for them.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /kubernetes/check`

The requirements check with a probe pod under the RuntimeClass on every sandbox node (admins).

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## Other

| Endpoint | Purpose |
| --- | --- |
| [`GET /`](#get-) | Home |

### `GET /`

Home

| Response | |
| --- | --- |
| `200` | Successful Response |


## Platforms

| Endpoint | Purpose |
| --- | --- |
| [`GET /platforms`](#get-platforms) | List Platforms |

### `GET /platforms`

Every sandbox OS, and whether this install can run it. The control plane's own Docker runs Linux.

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
| [`GET /pool`](#get-pool) | List Pool |
| [`PUT /pool`](#put-pool) | Set Pool |

### `GET /pool`

List Pool

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /pool`

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
| [`GET /profile-apps`](#get-profile-apps) | Profile Apps |

### `GET /profile-apps`

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
| [`GET /usage`](#get-usage) | Usage |
| [`GET /usage/export`](#get-usageexport) | Export |

### `GET /usage`

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


### `GET /usage/export`

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
| [`GET /webhooks`](#get-webhooks) | List Webhooks |
| [`POST /webhooks`](#post-webhooks) | Create Webhook |
| [`PUT /webhooks/{webhook_id}`](#put-webhookswebhook_id) | Update Webhook |
| [`DELETE /webhooks/{webhook_id}`](#delete-webhookswebhook_id) | Delete Webhook |
| [`GET /webhooks/{webhook_id}/deliveries`](#get-webhookswebhook_iddeliveries) | Deliveries |
| [`POST /webhooks/{webhook_id}/test`](#post-webhookswebhook_idtest) | Test Webhook |

### `GET /webhooks`

List Webhooks

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /webhooks`

Create Webhook

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `url` | `string` | yes |  |
| `types` | `string[]` | no |  |
| `label_selector` | `object or null` | no |  |
| `enabled` | `boolean` | no |  |

| Response | |
| --- | --- |
| `201` | Successful Response |
| `422` | Validation Error |


### `PUT /webhooks/{webhook_id}`

Update Webhook

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `webhook_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `url` | `string` | yes |  |
| `types` | `string[]` | no |  |
| `label_selector` | `object or null` | no |  |
| `enabled` | `boolean` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `DELETE /webhooks/{webhook_id}`

Delete Webhook

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `webhook_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `GET /webhooks/{webhook_id}/deliveries`

Deliveries

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `webhook_id` | path | `string` | yes |  |
| `limit` | query | `integer` | no |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /webhooks/{webhook_id}/test`

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
| [`GET /workspaces`](#get-workspaces) | List Workspaces |
| [`POST /workspaces`](#post-workspaces) | Create Workspace |
| [`PATCH /workspaces/{workspace_id}`](#patch-workspacesworkspace_id) | Rename Workspace |
| [`DELETE /workspaces/{workspace_id}`](#delete-workspacesworkspace_id) | Delete Workspace |
| [`GET /workspaces/{workspace_id}/invitations`](#get-workspacesworkspace_idinvitations) | List Invitations |
| [`POST /workspaces/{workspace_id}/invitations`](#post-workspacesworkspace_idinvitations) | Invite |
| [`DELETE /workspaces/{workspace_id}/invitations/{invitation_id}`](#delete-workspacesworkspace_idinvitationsinvitation_id) | Revoke Invitation |
| [`GET /workspaces/{workspace_id}/label-quotas`](#get-workspacesworkspace_idlabel-quotas) | List Label Quotas |
| [`PUT /workspaces/{workspace_id}/label-quotas`](#put-workspacesworkspace_idlabel-quotas) | Set Label Quota |
| [`DELETE /workspaces/{workspace_id}/label-quotas`](#delete-workspacesworkspace_idlabel-quotas) | Remove Label Quota |
| [`PUT /workspaces/{workspace_id}/lifecycle`](#put-workspacesworkspace_idlifecycle) | Set Lifecycle Defaults |
| [`GET /workspaces/{workspace_id}/members`](#get-workspacesworkspace_idmembers) | List Members |
| [`PATCH /workspaces/{workspace_id}/members/{user_id}`](#patch-workspacesworkspace_idmembersuser_id) | Change Role |
| [`DELETE /workspaces/{workspace_id}/members/{user_id}`](#delete-workspacesworkspace_idmembersuser_id) | Remove Member |
| [`GET /workspaces/{workspace_id}/quota`](#get-workspacesworkspace_idquota) | Get Quota |
| [`PUT /workspaces/{workspace_id}/quota`](#put-workspacesworkspace_idquota) | Set Quota |
| [`PUT /workspaces/{workspace_id}/trajectories`](#put-workspacesworkspace_idtrajectories) | Set Trajectory Settings |

### `GET /workspaces`

List Workspaces

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /workspaces`

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


### `PATCH /workspaces/{workspace_id}`

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


### `DELETE /workspaces/{workspace_id}`

Delete Workspace

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `204` | Successful Response |
| `422` | Validation Error |


### `GET /workspaces/{workspace_id}/invitations`

List Invitations

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `POST /workspaces/{workspace_id}/invitations`

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


### `DELETE /workspaces/{workspace_id}/invitations/{invitation_id}`

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


### `GET /workspaces/{workspace_id}/label-quotas`

List Label Quotas

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /workspaces/{workspace_id}/label-quotas`

Set Label Quota

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `max_running_sandboxes` | `integer or null` | no |  |
| `max_cpus` | `number or null` | no |  |
| `max_memory_mb` | `integer or null` | no |  |
| `max_storage_gb` | `integer or null` | no |  |
| `key` | `string` | yes |  |
| `value` | `string` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `DELETE /workspaces/{workspace_id}/label-quotas`

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


### `PUT /workspaces/{workspace_id}/lifecycle`

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


### `GET /workspaces/{workspace_id}/members`

List Members

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PATCH /workspaces/{workspace_id}/members/{user_id}`

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


### `DELETE /workspaces/{workspace_id}/members/{user_id}`

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


### `GET /workspaces/{workspace_id}/quota`

Get Quota

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /workspaces/{workspace_id}/quota`

Set Quota

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `max_running_sandboxes` | `integer or null` | no |  |
| `max_cpus` | `number or null` | no |  |
| `max_memory_mb` | `integer or null` | no |  |
| `max_storage_gb` | `integer or null` | no |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


### `PUT /workspaces/{workspace_id}/trajectories`

Set Trajectory Settings

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | `string` | yes |  |
| `x-zoo-workspace` | header | `string or null` | no |  |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `capture_a11y` | `boolean` | yes |  |

| Response | |
| --- | --- |
| `200` | Successful Response |
| `422` | Validation Error |


## Schemas

Request and response bodies, as the API defines them.


### `ActivityResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `action` | `string` | yes |  |
| `resource_type` | `string` | yes |  |
| `resource_id` | `string or null` | yes |  |
| `sandbox_id` | `string or null` | yes |  |
| `metadata` | `object` | yes |  |
| `created_at` | `string` | yes |  |


### `AgentMessageResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `kind` | `string` | yes |  |
| `content` | `string` | yes |  |
| `source` | `string` | yes |  |
| `run_id` | `string or null` | yes |  |
| `has_screenshot` | `boolean` | yes |  |
| `created_at` | `string` | yes |  |


### `AgentRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `message` | `string` | yes |  |
| `model` | `string or null` | no |  |
| `provider` | `string or null` | no |  |
| `api_base` | `string or null` | no |  |
| `stream` | `boolean` | no |  |


### `AgentRunResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `state` | `string` | yes |  |
| `source` | `string` | yes |  |
| `attempts` | `integer` | yes |  |
| `steps` | `integer` | yes |  |
| `tokens` | `integer` | yes |  |
| `cost` | `number` | yes |  |
| `max_steps` | `integer` | yes |  |
| `max_seconds` | `integer` | yes |  |
| `max_tokens` | `integer` | yes |  |
| `elapsed_seconds` | `number` | yes |  |
| `error` | `string or null` | yes |  |
| `created_at` | `string` | yes |  |
| `started_at` | `string or null` | yes |  |
| `finished_at` | `string or null` | yes |  |
| `paused_at` | `string or null` | no |  |
| `paused_by` | `string or null` | no |  |
| `provider` | `string or null` | no |  |
| `model` | `string or null` | no |  |


### `AgentSettingsRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `provider` | `string` | yes |  |
| `model` | `string` | yes |  |
| `api_key` | `string or null` | no |  |
| `api_key_secret_id` | `string or null` | no |  |
| `api_base` | `string or null` | no |  |
| `max_steps` | `integer or null` | no |  |
| `max_seconds` | `integer or null` | no |  |
| `max_tokens` | `integer or null` | no |  |


### `AgentSettingsResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `provider` | `string or null` | yes |  |
| `model` | `string` | yes |  |
| `has_api_key` | `boolean` | yes |  |
| `api_key_secret` | `SecretRef or null` | yes |  |
| `api_base` | `string or null` | yes |  |
| `default_model` | `string` | yes |  |
| `limits` | `LimitsResponse` | yes |  |
| `caps` | `LimitsResponse` | yes |  |
| `providers` | `ProviderResponse[]` | yes |  |


### `AgentStateResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `running` | `boolean` | yes |  |
| `model` | `string` | yes |  |
| `run` | `AgentRunResponse or null` | yes |  |
| `messages` | `AgentMessageResponse[]` | yes |  |


### `ApiKeyRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |
| `sandbox_id` | `string or null` | no |  |
| `label_selector` | `object or null` | no |  |
| `read_only` | `boolean` | no |  |
| `expires_at` | `string or null` | no |  |


### `ApiKeyResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |
| `key_prefix` | `string` | yes |  |
| `sandbox_id` | `string or null` | yes |  |
| `label_selector` | `object or null` | no |  |
| `read_only` | `boolean` | yes |  |
| `expires_at` | `string or null` | yes |  |
| `last_used_at` | `string or null` | yes |  |
| `revoked_at` | `string or null` | yes |  |
| `created_at` | `string` | yes |  |


### `AppPermissionRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `effect` | `string` | yes |  |


### `AppResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |
| `binary` | `string` | yes |  |
| `effect` | `string` | yes |  |


### `AttachedSecretResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |


### `AuditLogResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `actor_id` | `string or null` | yes |  |
| `actor_email` | `string or null` | yes |  |
| `action` | `string` | yes |  |
| `resource_type` | `string` | yes |  |
| `resource_id` | `string or null` | yes |  |
| `sandbox_id` | `string or null` | yes |  |
| `metadata` | `object` | yes |  |
| `created_at` | `string` | yes |  |


### `AuditPage`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `entries` | `AuditLogResponse[]` | yes |  |
| `cursor` | `string or null` | yes |  |


### `AuthRequst`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `email` | `string` | yes |  |
| `password` | `string` | yes |  |
| `name` | `string or null` | no |  |


### `BackupResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `key` | `string` | yes |  |
| `size` | `integer` | yes |  |
| `created_at` | `string` | yes |  |


### `BaseInstallRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `iso` | `string or null` | no |  |
| `edition` | `string or null` | no |  |


### `BaseStatus`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `state` | `string` | yes |  |
| `progress` | `number or null` | no |  |
| `message` | `string or null` | no |  |
| `ready` | `boolean` | no |  |


### `BenchmarkDetailResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |
| `state` | `string` | yes |  |
| `parallel` | `integer` | yes |  |
| `tasks` | `integer` | yes |  |
| `models` | `string[]` | yes |  |
| `error` | `string or null` | yes |  |
| `created_at` | `string` | yes |  |
| `started_at` | `string or null` | yes |  |
| `finished_at` | `string or null` | yes |  |
| `summary` | `ModelSummary[]` | yes |  |
| `results` | `ResultResponse[]` | yes |  |


### `BenchmarkRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `suite` | `string or object` | yes |  |
| `models` | `ModelChoice[]` | yes |  |
| `parallel` | `integer` | no |  |


### `BenchmarkResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |
| `state` | `string` | yes |  |
| `parallel` | `integer` | yes |  |
| `tasks` | `integer` | yes |  |
| `models` | `string[]` | yes |  |
| `error` | `string or null` | yes |  |
| `created_at` | `string` | yes |  |
| `started_at` | `string or null` | yes |  |
| `finished_at` | `string or null` | yes |  |


### `ChannelRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `platform` | `string` | yes |  |
| `external_id` | `string` | yes |  |
| `allowed_users` | `string[]` | no |  |


### `ChannelResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `platform` | `string` | yes |  |
| `external_id` | `string` | yes |  |
| `allowed_users` | `string[]` | yes |  |
| `created_at` | `string` | yes |  |


### `ChannelUpdate`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `allowed_users` | `string[]` | yes |  |


### `CreateSandboxRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string or null` | no |  |
| `kind` | `string` | no |  |
| `server_id` | `string or null` | no |  |
| `profile_ids` | `string[]` | no |  |
| `secret_ids` | `string[]` | no |  |
| `admin` | `boolean` | no |  |
| `queue` | `boolean` | no |  |
| `cpus` | `number` | no |  |
| `memory_mb` | `integer` | no |  |
| `disk_gb` | `integer or null` | no |  |
| `idle_timeout_minutes` | `integer or null` | no |  |
| `max_lifetime_minutes` | `integer or null` | no |  |
| `labels` | `object` | no |  |


### `CreatedApiKeyResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |
| `key_prefix` | `string` | yes |  |
| `sandbox_id` | `string or null` | yes |  |
| `label_selector` | `object or null` | no |  |
| `read_only` | `boolean` | yes |  |
| `expires_at` | `string or null` | yes |  |
| `last_used_at` | `string or null` | yes |  |
| `revoked_at` | `string or null` | yes |  |
| `created_at` | `string` | yes |  |
| `key` | `string` | yes |  |


### `CreatedInvitationResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `email` | `string` | yes |  |
| `role` | `string` | yes |  |
| `expires_at` | `string` | yes |  |
| `created_at` | `string` | yes |  |
| `token` | `string` | yes |  |


### `CreatedViewerLinkResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `sandbox_id` | `string` | yes |  |
| `mode` | `string` | yes |  |
| `end_user` | `string or null` | yes |  |
| `expires_at` | `string` | yes |  |
| `revoked_at` | `string or null` | yes |  |
| `created_at` | `string` | yes |  |
| `token` | `string` | yes |  |
| `url` | `string` | yes |  |


### `CreatedWebhookResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `url` | `string` | yes |  |
| `types` | `string[]` | yes |  |
| `label_selector` | `object or null` | yes |  |
| `enabled` | `boolean` | yes |  |
| `created_at` | `string` | yes |  |
| `secret` | `string` | yes |  |


### `DeleteSandboxResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `deleted` | `boolean` | no |  |
| `id` | `string` | yes |  |


### `DeliveryResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `event_id` | `string` | yes |  |
| `event_type` | `string` | yes |  |
| `status` | `string` | yes |  |
| `attempts` | `integer` | yes |  |
| `next_attempt_at` | `string` | yes |  |
| `last_error` | `string or null` | yes |  |
| `response_status` | `integer or null` | yes |  |
| `created_at` | `string` | yes |  |
| `delivered_at` | `string or null` | yes |  |


### `DomainRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `hostname` | `string` | yes |  |


### `DomainResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `hostname` | `string` | yes |  |
| `url` | `string` | yes |  |
| `addresses` | `string[]` | yes |  |
| `public_ip` | `string or null` | yes |  |
| `points_here` | `boolean or null` | yes |  |
| `created_at` | `string` | yes |  |


### `EmbedStateResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `mode` | `string` | yes |  |
| `sandbox_status` | `string` | yes |  |
| `agent` | `string` | yes |  |
| `paused_by` | `string or null` | yes |  |


### `EventPage`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `events` | `EventResponse[]` | yes |  |
| `after` | `integer` | yes |  |


### `EventResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `seq` | `integer` | yes |  |
| `type` | `string` | yes |  |
| `sandbox_id` | `string or null` | yes |  |
| `run_id` | `string or null` | yes |  |
| `data` | `object` | yes |  |
| `created_at` | `string` | yes |  |


### `ExecRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `command` | `string` | yes |  |
| `timeout` | `integer` | no |  |


### `ExecResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `sandbox_id` | `string` | yes |  |
| `exit_code` | `integer` | yes |  |
| `stdout` | `string` | yes |  |
| `stderr` | `string` | yes |  |
| `timed_out` | `boolean` | no |  |


### `ExportRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `run_id` | `string or null` | no |  |
| `sandbox_id` | `string or null` | no |  |
| `labels` | `object or null` | no |  |
| `from` | `string or null` | no |  |
| `to` | `string or null` | no |  |


### `ExportResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `state` | `string` | yes |  |
| `runs` | `integer` | yes |  |
| `steps` | `integer` | yes |  |
| `size_bytes` | `integer` | yes |  |
| `error` | `string or null` | yes |  |
| `created_at` | `string` | yes |  |
| `finished_at` | `string or null` | yes |  |


### `HTTPValidationError`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `detail` | `ValidationError[]` | no |  |


### `HostResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |
| `os` | `string` | yes |  |
| `architecture` | `string` | yes |  |
| `docker_version` | `string` | yes |  |
| `cpus` | `integer` | yes |  |
| `memory_total` | `integer` | yes |  |
| `containers_running` | `integer` | yes |  |
| `images` | `integer` | yes |  |


### `InstallCommand`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `platform` | `string` | yes |  |
| `command` | `string` | yes |  |
| `public_key` | `string` | yes |  |
| `requirements` | `string` | yes |  |


### `IntegrationResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `platform` | `string` | yes |  |
| `configured` | `boolean` | yes |  |
| `webhook_path` | `string or null` | yes |  |


### `InvitationRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `email` | `string` | yes |  |
| `role` | `string` | no |  |


### `InvitationResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `email` | `string` | yes |  |
| `role` | `string` | yes |  |
| `expires_at` | `string` | yes |  |
| `created_at` | `string` | yes |  |


### `JobResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `kind` | `string` | yes |  |
| `state` | `string` | yes |  |
| `attempts` | `integer` | yes |  |
| `max_attempts` | `integer` | yes |  |
| `last_error` | `string or null` | yes |  |
| `deadline` | `string or null` | yes |  |
| `waiting` | `boolean` | no |  |


### `JoinRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `token` | `string` | yes |  |
| `csr` | `string` | yes |  |
| `hostname` | `string` | no |  |
| `os` | `string` | yes |  |
| `arch` | `string` | yes |  |
| `ssh_user` | `string or null` | no |  |
| `ssh_host_key` | `string or null` | no |  |


### `JoinResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `node_id` | `string` | yes |  |
| `server_id` | `string` | yes |  |
| `certificate` | `string` | yes |  |
| `ca` | `string` | yes |  |
| `endpoints` | `string[]` | yes |  |


### `KubernetesCheck`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |
| `ok` | `boolean` | yes |  |
| `detail` | `string` | yes |  |


### `KubernetesStatus`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `enabled` | `boolean` | yes |  |
| `namespace` | `string` | no |  |
| `runtime_class` | `string` | no |  |
| `checks` | `KubernetesCheck[]` | no |  |


### `LabelQuotaRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `max_running_sandboxes` | `integer or null` | no |  |
| `max_cpus` | `number or null` | no |  |
| `max_memory_mb` | `integer or null` | no |  |
| `max_storage_gb` | `integer or null` | no |  |
| `key` | `string` | yes |  |
| `value` | `string` | yes |  |


### `LabelQuotaResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `max_running_sandboxes` | `integer or null` | no |  |
| `max_cpus` | `number or null` | no |  |
| `max_memory_mb` | `integer or null` | no |  |
| `max_storage_gb` | `integer or null` | no |  |
| `key` | `string` | yes |  |
| `value` | `string` | yes |  |
| `running_sandboxes` | `integer or null` | yes |  |
| `cpus` | `number or null` | yes |  |
| `memory_mb` | `integer or null` | yes |  |
| `storage_gb` | `number or null` | yes |  |


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


### `LimitsResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `max_steps` | `integer` | yes |  |
| `max_seconds` | `integer` | yes |  |
| `max_tokens` | `integer` | yes |  |


### `MemberResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `user_id` | `string` | yes |  |
| `email` | `string` | yes |  |
| `name` | `string or null` | yes |  |
| `role` | `string` | yes |  |
| `status` | `string` | yes |  |
| `joined_at` | `string or null` | yes |  |


### `ModelChoice`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `provider` | `string` | yes |  |
| `model` | `string or null` | no |  |


### `ModelSummary`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `model` | `string` | yes |  |
| `tasks` | `integer` | yes |  |
| `passed` | `integer` | yes |  |
| `pass_rate` | `number` | yes |  |
| `mean_steps` | `number` | yes |  |
| `mean_cost` | `number` | yes |  |
| `mean_seconds` | `number` | yes |  |


### `MonitoringResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `host` | `HostResponse` | yes |  |
| `sandboxes` | `object` | yes |  |
| `cpu_percent` | `number` | yes |  |
| `memory_usage` | `integer` | yes |  |
| `usage` | `SandboxUsage[]` | yes |  |
| `collected_at` | `string` | yes |  |


### `MoveSandboxRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `server_id` | `string or null` | no |  |


### `NetworkPolicyRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `default_action` | `string` | yes |  |
| `allow_dns` | `boolean` | yes |  |


### `NetworkResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `default_action` | `string` | yes |  |
| `allow_dns` | `boolean` | yes |  |
| `rules` | `NetworkRuleResponse[]` | yes |  |


### `NetworkRuleRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `rule_type` | `string` | yes |  |
| `value` | `string` | yes |  |
| `effect` | `string` | yes |  |


### `NetworkRuleResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `rule_type` | `string` | yes |  |
| `value` | `string` | yes |  |
| `effect` | `string` | yes |  |


### `NodeCheck`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |
| `ok` | `boolean` | yes |  |
| `detail` | `string` | no |  |


### `NodeDriver`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |
| `available` | `boolean` | yes |  |
| `detail` | `string` | no |  |


### `NodeInfo`

The server's zoo-node and what it last reported.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `connected` | `boolean` | yes |  |
| `version` | `string` | yes |  |
| `os` | `string` | yes |  |
| `arch` | `string` | yes |  |
| `hostname` | `string` | yes |  |
| `drivers` | `NodeDriver[]` | yes |  |
| `cpus` | `integer or null` | yes |  |
| `memory_total` | `integer or null` | yes |  |
| `memory_available` | `integer or null` | yes |  |
| `disk_total` | `integer or null` | yes |  |
| `disk_free` | `integer or null` | yes |  |
| `load` | `number or null` | yes |  |
| `sandboxes` | `integer` | yes |  |
| `checks` | `NodeCheck[]` | yes |  |
| `cert_expires_at` | `string` | yes |  |
| `seen_at` | `string or null` | yes |  |


### `PermissionRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `permission` | `string` | yes |  |
| `action` | `string` | yes |  |
| `effect` | `string` | yes |  |


### `PermissionResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `permission` | `string` | yes |  |
| `action` | `string` | yes |  |
| `label` | `string` | yes |  |
| `effect` | `string` | yes |  |


### `PlatformResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |
| `host` | `string` | yes |  |
| `kinds` | `string[]` | yes |  |
| `runs` | `string[]` | yes |  |
| `requirements` | `string` | yes |  |
| `servers` | `integer` | yes |  |
| `available` | `boolean` | yes |  |


### `PoolEntry`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `kind` | `string` | yes |  |
| `server_id` | `string or null` | yes |  |
| `server_name` | `string` | yes |  |
| `size` | `integer` | yes |  |
| `idle` | `integer` | yes |  |
| `booting` | `integer` | yes |  |
| `claimed` | `integer` | yes |  |
| `error` | `string or null` | yes |  |


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
| `name` | `string` | yes |  |
| `app` | `string` | yes |  |
| `profile_id` | `string or null` | no |  |


### `ProfileResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |
| `app` | `string` | yes |  |
| `platform` | `string` | yes |  |
| `size_bytes` | `integer` | yes |  |
| `version` | `integer` | yes |  |
| `versions` | `integer` | yes |  |
| `created_at` | `string` | yes |  |
| `updated_at` | `string` | yes |  |


### `ProfileVersionResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `version` | `integer` | yes |  |
| `size_bytes` | `integer` | yes |  |
| `sandbox_id` | `string or null` | yes |  |
| `created_at` | `string` | yes |  |


### `ProviderResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `label` | `string` | yes |  |
| `default_model` | `string` | yes |  |
| `default_base` | `string or null` | yes |  |
| `needs_key` | `boolean` | yes |  |
| `server_key` | `boolean` | yes |  |


### `ProviderStatusResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `label` | `string` | yes |  |
| `default_model` | `string` | yes |  |
| `default_base` | `string or null` | yes |  |
| `needs_key` | `boolean` | yes |  |
| `server_key` | `boolean` | yes |  |
| `has_key` | `boolean` | yes |  |
| `key_secret` | `string or null` | yes |  |


### `QuotaRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `max_running_sandboxes` | `integer or null` | no |  |
| `max_cpus` | `number or null` | no |  |
| `max_memory_mb` | `integer or null` | no |  |
| `max_storage_gb` | `integer or null` | no |  |


### `QuotaResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `max_running_sandboxes` | `integer or null` | no |  |
| `max_cpus` | `number or null` | no |  |
| `max_memory_mb` | `integer or null` | no |  |
| `max_storage_gb` | `integer or null` | no |  |
| `running_sandboxes` | `integer` | yes |  |
| `cpus` | `number` | yes |  |
| `memory_mb` | `integer` | yes |  |
| `storage_gb` | `number` | yes |  |


### `ReminderResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |
| `status` | `string` | yes |  |
| `due_at` | `string` | yes |  |
| `message` | `string` | yes |  |


### `ResultResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `task_id` | `string` | yes |  |
| `model` | `string` | yes |  |
| `passed` | `boolean or null` | yes |  |
| `score` | `number or null` | yes |  |
| `steps` | `integer` | yes |  |
| `tokens` | `integer` | yes |  |
| `cost` | `number` | yes |  |
| `seconds` | `number` | yes |  |
| `run_id` | `string or null` | yes |  |
| `error` | `string or null` | yes |  |


### `RoleRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `role` | `string` | yes |  |


### `SandboxRef`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |
| `status` | `string` | yes |  |
| `last_used_at` | `string or null` | yes |  |


### `SandboxResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |
| `kind` | `string` | yes |  |
| `server_id` | `string or null` | yes |  |
| `status` | `string` | yes |  |
| `error_message` | `string or null` | yes |  |
| `started_at` | `string or null` | yes |  |
| `created_at` | `string` | yes |  |
| `unreachable` | `boolean` | no |  |
| `job` | `JobResponse or null` | no |  |
| `image` | `string or null` | no |  |
| `image_outdated` | `boolean` | no |  |
| `base_version` | `string or null` | no |  |
| `boot_seconds` | `number or null` | no |  |
| `recovered_at` | `string or null` | no |  |
| `cpus` | `number` | no |  |
| `memory_mb` | `integer` | no |  |
| `disk_gb` | `integer or null` | no |  |
| `idle_timeout_minutes` | `integer or null` | no |  |
| `max_lifetime_minutes` | `integer or null` | no |  |
| `last_activity_at` | `string or null` | no |  |
| `labels` | `object` | no |  |


### `SandboxUsage`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `sandbox_id` | `string` | yes |  |
| `name` | `string` | yes |  |
| `status` | `string` | yes |  |
| `cpu_percent` | `number` | yes |  |
| `memory_usage` | `integer` | yes |  |
| `memory_limit` | `integer` | yes |  |
| `memory_percent` | `number` | yes |  |
| `network_rx` | `integer` | yes |  |
| `network_tx` | `integer` | yes |  |
| `pids` | `integer` | yes |  |


### `SecretRef`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |


### `SecretRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |
| `value` | `string` | yes |  |


### `SecretResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |
| `enabled` | `boolean` | yes |  |
| `created_at` | `string` | yes |  |


### `ServerRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |
| `docker_url` | `string` | yes |  |
| `bind_address` | `string` | yes |  |
| `platform` | `string` | no |  |
| `host_key` | `string or null` | no |  |


### `ServerResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |
| `docker_url` | `string` | yes |  |
| `bind_address` | `string` | yes |  |
| `platform` | `string` | yes |  |
| `capabilities` | `string[]` | yes |  |
| `created_at` | `string` | yes |  |
| `node` | `NodeInfo or null` | no |  |


### `ServerStatus`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `online` | `boolean` | yes |  |
| `error` | `string or null` | no |  |
| `name` | `string or null` | no |  |
| `os` | `string or null` | no |  |
| `cpus` | `integer or null` | no |  |
| `memory_total` | `integer or null` | no |  |
| `docker_version` | `string or null` | no |  |
| `microvm` | `boolean or null` | no |  |
| `containers_running` | `integer or null` | no |  |
| `sandboxes` | `integer` | no |  |


### `SnapshotRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | no |  |


### `SnapshotResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |
| `size_bytes` | `integer` | yes |  |
| `state` | `string` | yes |  |
| `error` | `string or null` | yes |  |
| `created_at` | `string` | yes |  |


### `TokenRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |


### `ToolExecutionResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `tool_name` | `string` | yes |  |
| `status` | `string` | yes |  |
| `input` | `string` | yes |  |
| `error_message` | `string or null` | yes |  |
| `created_at` | `string` | yes |  |
| `completed_at` | `string or null` | yes |  |


### `TrajectorySettingsRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `capture_a11y` | `boolean` | yes |  |


### `UpdateSandboxRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string or null` | no |  |
| `labels` | `object or null` | no |  |


### `UsageReport`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `since` | `string` | yes |  |
| `until` | `string` | yes |  |
| `group_by` | `string` | yes |  |
| `rows` | `UsageRow[]` | yes |  |


### `UsageRow`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `group` | `string or null` | yes |  |
| `metric` | `string` | yes |  |
| `quantity` | `number` | yes |  |


### `UserResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `email` | `string` | yes |  |
| `name` | `string or null` | yes |  |
| `avatar_url` | `string or null` | yes |  |


### `ValidationError`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `loc` | `string or integer[]` | yes |  |
| `msg` | `string` | yes |  |
| `type` | `string` | yes |  |
| `input` | `any` | no |  |
| `ctx` | `object` | no |  |


### `VaultSecretRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |
| `value` | `string` | yes |  |
| `description` | `string or null` | no |  |
| `expires_at` | `string or null` | no |  |
| `rotate_every_days` | `integer or null` | no |  |


### `VaultSecretResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |
| `description` | `string or null` | yes |  |
| `created_at` | `string` | yes |  |
| `updated_at` | `string` | yes |  |
| `last_used_at` | `string or null` | yes |  |
| `expires_at` | `string or null` | yes |  |
| `rotate_every_days` | `integer or null` | yes |  |
| `rotated_at` | `string or null` | yes |  |
| `rotation_due_at` | `string or null` | yes |  |
| `status` | `string` | yes |  |
| `used_by_agent` | `boolean` | yes |  |
| `sandboxes` | `SandboxRef[]` | yes |  |


### `VaultSecretUpdate`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `value` | `string or null` | no |  |
| `description` | `string or null` | no |  |
| `expires_at` | `string or null` | no |  |
| `rotate_every_days` | `integer or null` | no |  |


### `ViewerLinkRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `mode` | `string` | no |  |
| `expires_in_minutes` | `integer` | no |  |
| `end_user` | `string or null` | no |  |


### `ViewerLinkResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `sandbox_id` | `string` | yes |  |
| `mode` | `string` | yes |  |
| `end_user` | `string or null` | yes |  |
| `expires_at` | `string` | yes |  |
| `revoked_at` | `string or null` | yes |  |
| `created_at` | `string` | yes |  |


### `VncTicketResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `ticket` | `string` | yes |  |
| `expires_in` | `integer` | no |  |


### `WebhookRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `url` | `string` | yes |  |
| `types` | `string[]` | no |  |
| `label_selector` | `object or null` | no |  |
| `enabled` | `boolean` | no |  |


### `WebhookResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `url` | `string` | yes |  |
| `types` | `string[]` | yes |  |
| `label_selector` | `object or null` | yes |  |
| `enabled` | `boolean` | yes |  |
| `created_at` | `string` | yes |  |


### `WorkspaceRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | yes |  |


### `WorkspaceResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `string` | yes |  |
| `name` | `string` | yes |  |
| `slug` | `string` | yes |  |
| `role` | `string` | yes |  |
| `personal` | `boolean` | yes |  |
| `created_at` | `string` | yes |  |
| `default_idle_timeout_minutes` | `integer or null` | yes |  |
| `default_max_lifetime_minutes` | `integer or null` | yes |  |
| `capture_a11y` | `boolean` | no |  |


### `server__auth_api__TokenResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `access_token` | `string` | yes |  |
| `token_type` | `string` | no |  |
| `user_id` | `string` | yes |  |


### `server__nodes_api__TokenResponse`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `token` | `string` | yes |  |
| `join_command` | `string` | yes |  |
| `install_command` | `string` | yes |  |
| `expires_at` | `string` | yes |  |

