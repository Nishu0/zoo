import { useEffect } from "react"
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { z } from "zod"

import { configuredApiUrl } from "@/lib/runtime_config"

const CONFIGURED_API_URL = configuredApiUrl()

function resolveApiUrl() {
  if (typeof window === "undefined") return CONFIGURED_API_URL
  const local = (host: string) =>
    host === "localhost" || host === "127.0.0.1" || host.endsWith(".localhost")
  const configured = new URL(CONFIGURED_API_URL, window.location.origin)
  if (local(configured.hostname) && !local(window.location.hostname)) {
    return `${window.location.origin}/api`
  }
  return configured.toString().replace(/\/$/, "")
}

const API_ROOT = resolveApiUrl()
const API_URL = `${API_ROOT}/v1`
const TOKEN_KEY = "zoo.token"
// the workspace this browser acts in; none means the user's personal workspace
const WORKSPACE_KEY = "zoo.workspace"

export const loginSchema = z.object({
  email: z.email("Enter a valid email address."),
  password: z
    .string()
    .min(8, "Password must be at least 8 characters.")
    .max(72),
})

export const registerSchema = loginSchema
  .extend({
    name: z.string().trim().min(1, "Enter your name.").max(100),
    confirmPassword: z.string(),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: "Passwords do not match.",
    path: ["confirmPassword"],
  })

export const tokenSchema = z.object({
  access_token: z.string(),
  token_type: z.string(),
  user_id: z.string(),
})

export const userSchema = z.object({
  id: z.string(),
  email: z.string(),
  name: z.string().nullable(),
  avatar_url: z.string().nullable(),
  onboarded_at: z.string().nullable().optional(),
})

export const sandboxStatusSchema = z.enum([
  "pending",
  "provisioning",
  "running",
  "stopped",
  "failed",
  "deleting",
  "deleted",
])

// the boot, stop, delete, move or snapshot work in progress
export const sandboxJobSchema = z.object({
  kind: z.enum(["boot", "stop", "delete", "move", "snapshot", "restore"]),
  state: z.enum(["queued", "running"]),
  attempts: z.number(),
  max_attempts: z.number(),
  last_error: z.string().nullable(),
  deadline: z.string().nullable(),
  // waiting for room (a full Mac) rather than retrying; last_error says what for
  waiting: z.boolean().default(false),
})

export const sandboxSchema = z.object({
  id: z.string(),
  name: z.string(),
  kind: z.enum(["desktop", "browser", "code", "macos", "windows"]),
  server_id: z.string().nullable(),
  status: sandboxStatusSchema,
  error_message: z.string().nullable(),
  started_at: z.string().nullable(),
  created_at: z.string(),
  // the host didn't answer the last health check
  unreachable: z.boolean().default(false),
  job: sandboxJobSchema.nullable().default(null),
  // the image a Linux sandbox boots from; it stays on it until restarted on the new one
  image: z.string().nullable().default(null),
  image_outdated: z.boolean().default(false),
  // macOS: the base VM it was cloned from, its last boot time, and when Zoo last restarted it because it hung
  base_version: z.string().nullable().default(null),
  boot_seconds: z.number().nullable().default(null),
  recovered_at: z.string().nullable().default(null),
  cpus: z.number().default(2),
  memory_mb: z.number().default(2048),
  disk_gb: z.number().nullable().default(null),
  idle_timeout_minutes: z.number().nullable().default(null),
  max_lifetime_minutes: z.number().nullable().default(null),
  last_activity_at: z.string().nullable().default(null),
  // e.g. end_user: alice, for builders' own customers
  labels: z.record(z.string(), z.string()).default({}),
})

export const createSandboxSchema = z.object({
  name: z.string().trim().max(100).optional(),
  kind: z.enum(["desktop", "browser", "code", "macos", "windows"]).optional(),
  server_id: z.string().nullable().optional(),
  profile_ids: z.array(z.string()).optional(),
  secret_ids: z.array(z.string()).optional(),
  // macOS: keep the guest user an administrator with passwordless sudo
  admin: z.boolean().optional(),
  // Linux only; left out, the defaults (2 CPUs, 2048 MB, the host's disk)
  cpus: z.number().positive("CPUs must be more than 0.").optional(),
  memory_mb: z.number().int().min(256, "Give it at least 256 MB.").optional(),
  disk_gb: z.number().int().min(1).optional(),
  // minutes; left out, the workspace's default
  idle_timeout_minutes: z.number().int().positive().optional(),
  max_lifetime_minutes: z.number().int().positive().optional(),
  labels: z.record(z.string(), z.string()).optional(),
})

// the server's zoo-node and what it last reported
export const nodeSchema = z.object({
  id: z.string(),
  connected: z.boolean(),
  version: z.string(),
  os: z.string(),
  arch: z.string(),
  hostname: z.string(),
  drivers: z.array(
    z.object({ name: z.string(), available: z.boolean(), detail: z.string() })
  ),
  cpus: z.number().nullable(),
  memory_total: z.number().nullable(),
  memory_available: z.number().nullable(),
  disk_total: z.number().nullable(),
  disk_free: z.number().nullable(),
  load: z.number().nullable(),
  sandboxes: z.number(),
  checks: z.array(
    z.object({ name: z.string(), ok: z.boolean(), detail: z.string() })
  ),
  cert_expires_at: z.string(),
  seen_at: z.string().nullable(),
})

export type NodeInfo = z.infer<typeof nodeSchema>

export const serverSchema = z.object({
  id: z.string(),
  name: z.string(),
  docker_url: z.string(),
  bind_address: z.string(),
  platform: z.enum(["linux", "macos", "windows"]),
  capabilities: z.array(z.enum(["linux", "macos", "windows"])),
  created_at: z.string(),
  node: nodeSchema.nullable().default(null),
})

export const nodeTokenSchema = z.object({
  token: z.string(),
  join_command: z.string(),
  install_command: z.string(),
  expires_at: z.string(),
})

export type NodeToken = z.infer<typeof nodeTokenSchema>

export const kubernetesSchema = z.object({
  enabled: z.boolean(),
  namespace: z.string(),
  runtime_class: z.string(),
  checks: z.array(
    z.object({ name: z.string(), ok: z.boolean(), detail: z.string() })
  ),
})

export type Kubernetes = z.infer<typeof kubernetesSchema>

export const snapshotSchema = z.object({
  id: z.string(),
  name: z.string(),
  size_bytes: z.number(),
  state: z.enum(["creating", "ready", "failed"]),
  error: z.string().nullable(),
  created_at: z.string(),
})

export type Snapshot = z.infer<typeof snapshotSchema>

export const poolEntrySchema = z.object({
  kind: z.enum(["desktop", "browser", "code"]),
  server_id: z.string().nullable(),
  server_name: z.string(),
  size: z.number(),
  idle: z.number(),
  booting: z.number(),
  claimed: z.number(),
  error: z.string().nullable(),
})

export type PoolEntry = z.infer<typeof poolEntrySchema>

export const platformSchema = z.object({
  id: z.enum(["linux", "macos", "windows"]),
  name: z.string(),
  host: z.string(),
  kinds: z.array(z.string()),
  runs: z.array(z.enum(["linux", "macos", "windows"])),
  requirements: z.string(),
  servers: z.number(),
  available: z.boolean(),
})

export const installCommandSchema = z.object({
  platform: z.string(),
  command: z.string(),
  public_key: z.string(),
  requirements: z.string(),
})

export const serverInputSchema = z.object({
  name: z.string().trim().min(1, "Name is required").max(100),
  docker_url: z
    .string()
    .trim()
    .regex(
      /^((ssh|tcp):\/\/.+|local:\/\/)$/,
      "Use ssh://user@host, tcp://host:2376 or local://"
    ),
  bind_address: z.string().trim().min(1, "Address is required"),
  platform: z.enum(["linux", "macos", "windows"]),
  host_key: z.string().optional(),
})

/** Reads the `zoo-join:` line the node installer prints into the fields of a new server. */
export function parseJoinLine(line: string): ServerInput {
  const encoded = line.trim().replace(/^zoo-join:/, "")
  let data: unknown
  try {
    data = JSON.parse(atob(encoded))
  } catch {
    throw new Error("That isn't a join line; copy the whole zoo-join:… line")
  }
  const parsed = serverInputSchema.safeParse(data)
  if (!parsed.success) {
    throw new Error(parsed.error.issues[0]?.message ?? "Invalid join line")
  }
  return parsed.data
}

export const serverStatusSchema = z.object({
  online: z.boolean(),
  error: z.string().nullable(),
  name: z.string().nullable(),
  os: z.string().nullable(),
  cpus: z.number().nullable(),
  memory_total: z.number().nullable(),
  docker_version: z.string().nullable(),
  microvm: z.boolean().nullable(),
  containers_running: z.number().nullable(),
  sandboxes: z.number(),
})

export const baseStatusSchema = z.object({
  state: z.enum(["missing", "installing", "failed", "stopped", "running"]),
  progress: z.number().nullable(),
  message: z.string().nullable(),
  ready: z.boolean(),
})

export type BaseStatus = z.infer<typeof baseStatusSchema>

export const profileSchema = z.object({
  id: z.string(),
  name: z.string(),
  app: z.string(),
  // the OS it was captured on; it only loads into sandboxes of the same one
  platform: z.string().default("linux"),
  // of the latest version
  size_bytes: z.number(),
  version: z.number().default(1),
  versions: z.number().default(1),
  created_at: z.string(),
  updated_at: z.string().optional(),
})

export type Profile = z.infer<typeof profileSchema>

export const profileVersionSchema = z.object({
  version: z.number(),
  size_bytes: z.number(),
  sandbox_id: z.string().nullable(),
  created_at: z.string(),
})

export type ProfileVersion = z.infer<typeof profileVersionSchema>

export const secretStatusSchema = z.enum([
  "ok",
  "rotation_soon",
  "expiring_soon",
  "rotation_due",
  "expired",
])

export type SecretStatus = z.infer<typeof secretStatusSchema>

export const vaultSecretSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
  last_used_at: z.string().nullable(),
  expires_at: z.string().nullable().default(null),
  rotate_every_days: z.number().nullable().default(null),
  rotated_at: z.string().nullable().default(null),
  rotation_due_at: z.string().nullable().default(null),
  status: secretStatusSchema.default("ok"),
  sandboxes: z.array(
    z.object({
      id: z.string(),
      name: z.string(),
      status: z.string().optional(),
      // when this sandbox last received the secret: at boot or by a live update
      last_used_at: z.string().nullable().optional(),
    })
  ),
})

export type VaultSecret = z.infer<typeof vaultSecretSchema>

export const vaultReminderSchema = z.object({
  id: z.string(),
  name: z.string(),
  status: secretStatusSchema,
  due_at: z.string(),
  message: z.string(),
})

export type VaultReminder = z.infer<typeof vaultReminderSchema>

export const vaultSecretInputSchema = z.object({
  name: z
    .string()
    .trim()
    .regex(
      /^[A-Za-z_][A-Za-z0-9_]{0,63}$/,
      "Use an env var name like API_TOKEN."
    ),
  value: z.string().min(1, "Enter a value.").max(8192),
  description: z.string().trim().max(200).optional(),
  // ISO date-time; leave out for no expiry
  expires_at: z.string().optional(),
  rotate_every_days: z.number().int().min(1).max(3650).optional(),
})

export type VaultSecretInput = z.infer<typeof vaultSecretInputSchema>

export const attachedSecretSchema = z.object({
  id: z.string(),
  name: z.string(),
})

export const vaultActivitySchema = z.object({
  id: z.string(),
  action: z.string(),
  resource_type: z.string(),
  resource_id: z.string().nullable(),
  sandbox_id: z.string().nullable(),
  metadata: z.record(z.string(), z.unknown()),
  created_at: z.string(),
})

export type VaultActivity = z.infer<typeof vaultActivitySchema>

export const domainSchema = z.object({
  id: z.string(),
  hostname: z.string(),
  url: z.string(),
  addresses: z.array(z.string()),
  public_ip: z.string().nullable(),
  points_here: z.boolean().nullable(),
  created_at: z.string(),
})

export const domainInputSchema = z
  .string()
  .trim()
  .toLowerCase()
  .regex(
    /^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/,
    "Enter a hostname like zoo.example.com"
  )

export type ServerInput = z.infer<typeof serverInputSchema>

export const execResultSchema = z.object({
  sandbox_id: z.string(),
  exit_code: z.number(),
  stdout: z.string(),
  stderr: z.string(),
  timed_out: z.boolean(),
})

const deletedSchema = z.object({ deleted: z.boolean(), id: z.string() })

export const effectSchema = z.enum(["allow", "deny"])

export const permissionSchema = z.object({
  permission: z.string(),
  action: z.string(),
  label: z.string(),
  effect: effectSchema,
})

export const networkRuleSchema = z.object({
  id: z.string(),
  rule_type: z.enum(["domain", "ip", "cidr"]),
  value: z.string(),
  effect: effectSchema,
})

export const networkSchema = z.object({
  default_action: effectSchema,
  allow_dns: z.boolean(),
  rules: z.array(networkRuleSchema),
})

export const networkRuleInputSchema = z.object({
  rule_type: networkRuleSchema.shape.rule_type,
  value: z
    .string()
    .trim()
    .min(1, "Enter a domain, IP or CIDR.")
    .max(253)
    .regex(
      /^[a-zA-Z0-9.*:/-]+$/,
      "Only letters, digits, dots, colons, / and *."
    ),
  effect: effectSchema,
})

export const appSchema = z.object({
  name: z.string(),
  binary: z.string(),
  effect: effectSchema,
})

export const monitoringSchema = z.object({
  host: z.object({
    name: z.string(),
    os: z.string(),
    architecture: z.string(),
    docker_version: z.string(),
    cpus: z.number(),
    memory_total: z.number(),
    containers_running: z.number(),
    images: z.number(),
  }),
  sandboxes: z.partialRecord(z.string(), z.number()),
  cpu_percent: z.number(),
  memory_usage: z.number(),
  usage: z.array(
    z.object({
      sandbox_id: z.string(),
      name: z.string(),
      status: z.string(),
      cpu_percent: z.number(),
      memory_usage: z.number(),
      memory_limit: z.number(),
      memory_percent: z.number(),
      network_rx: z.number(),
      network_tx: z.number(),
      pids: z.number(),
    })
  ),
  collected_at: z.string(),
})

export const secretSchema = z.object({
  id: z.string(),
  name: z.string(),
  enabled: z.boolean(),
  created_at: z.string(),
})

export const secretInputSchema = z.object({
  name: z
    .string()
    .trim()
    .regex(
      /^[A-Za-z_][A-Za-z0-9_]{0,63}$/,
      "Use an env var name like API_TOKEN."
    ),
  value: z.string().min(1, "Enter a value.").max(8192),
})

export const executionSchema = z.object({
  id: z.string(),
  tool_name: z.string(),
  status: z.string(),
  input: z.string(),
  error_message: z.string().nullable(),
  created_at: z.string(),
  completed_at: z.string().nullable(),
})

export const apiKeySchema = z.object({
  id: z.string(),
  name: z.string(),
  key_prefix: z.string(),
  // the one sandbox the key reaches, or every sandbox in the workspace
  sandbox_id: z.string().nullable(),
  // or only the sandboxes with these labels
  label_selector: z.record(z.string(), z.string()).nullable().default(null),
  read_only: z.boolean(),
  expires_at: z.string().nullable(),
  last_used_at: z.string().nullable(),
  revoked_at: z.string().nullable(),
  created_at: z.string(),
})

export const createdApiKeySchema = apiKeySchema.extend({ key: z.string() })

export type CreateApiKeyInput = {
  name: string
  sandbox_id?: string | null
  label_selector?: Record<string, string> | null
  read_only?: boolean
  // ISO time; the key stops working then
  expires_at?: string | null
}

export const roleSchema = z.enum(["owner", "admin", "member", "viewer"])
export const ROLES = roleSchema.options

/** Whether `role` can do what `minimum` can (roles go viewer < member < admin < owner). */
export function roleAtLeast(role: Role | undefined, minimum: Role) {
  return role !== undefined && ROLES.indexOf(role) <= ROLES.indexOf(minimum)
}

export const workspaceSchema = z.object({
  id: z.string(),
  name: z.string(),
  slug: z.string(),
  role: roleSchema,
  personal: z.boolean(),
  created_at: z.string(),
  // what a sandbox without its own settings gets; null: never
  default_idle_timeout_minutes: z.number().nullable(),
  default_max_lifetime_minutes: z.number().nullable(),
})

export const quotaSchema = z.object({
  // null: no limit
  max_running_sandboxes: z.number().nullable(),
  max_cpus: z.number().nullable(),
  max_memory_mb: z.number().nullable(),
  max_storage_gb: z.number().nullable(),
  running_sandboxes: z.number(),
  cpus: z.number(),
  memory_mb: z.number(),
  storage_gb: z.number(),
})

export const memberSchema = z.object({
  user_id: z.string(),
  email: z.string(),
  name: z.string().nullable(),
  role: roleSchema,
  status: z.string(),
  joined_at: z.string().nullable(),
})

export const invitationSchema = z.object({
  id: z.string(),
  email: z.string(),
  role: roleSchema,
  expires_at: z.string(),
  created_at: z.string(),
})

export const createdInvitationSchema = invitationSchema.extend({
  token: z.string(),
})

export const auditEntrySchema = z.object({
  id: z.string(),
  actor_id: z.string().nullable(),
  actor_email: z.string().nullable(),
  action: z.string(),
  resource_type: z.string(),
  resource_id: z.string().nullable(),
  sandbox_id: z.string().nullable(),
  metadata: z.record(z.string(), z.unknown()),
  created_at: z.string(),
})

/** A page of a list endpoint; pass `next_cursor` back as `cursor` for the next one. */
function pageSchema<T extends z.ZodType>(item: T) {
  return z.object({ data: z.array(item), next_cursor: z.string().nullable() })
}

export const auditPageSchema = pageSchema(auditEntrySchema)

export type AuditFilters = {
  actor?: string
  action?: string
  resource_type?: string
  sandbox_id?: string
  since?: string
  until?: string
}

function auditQuery(filters: AuditFilters, cursor?: string | null) {
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(filters)) if (v) params.set(k, v)
  if (cursor) params.set("cursor", cursor)
  const query = params.toString()
  return query ? `?${query}` : ""
}

export type LoginInput = z.infer<typeof loginSchema>
export type RegisterInput = z.infer<typeof registerSchema>
export type TokenResponse = z.infer<typeof tokenSchema>
export type User = z.infer<typeof userSchema>
export type Sandbox = z.infer<typeof sandboxSchema>
export type SandboxStatus = z.infer<typeof sandboxStatusSchema>
export type CreateSandboxInput = z.infer<typeof createSandboxSchema>
export type ExecResult = z.infer<typeof execResultSchema>
export type Effect = z.infer<typeof effectSchema>
export type Permission = z.infer<typeof permissionSchema>
export type Network = z.infer<typeof networkSchema>
export type NetworkRuleInput = z.infer<typeof networkRuleInputSchema>
export type App = z.infer<typeof appSchema>
export type Monitoring = z.infer<typeof monitoringSchema>
export type SecretInput = z.infer<typeof secretInputSchema>
export type ApiKey = z.infer<typeof apiKeySchema>
export type Role = z.infer<typeof roleSchema>
export type Workspace = z.infer<typeof workspaceSchema>
export type Quota = z.infer<typeof quotaSchema>
export type Member = z.infer<typeof memberSchema>
export type Invitation = z.infer<typeof invitationSchema>
export type AuditEntry = z.infer<typeof auditEntrySchema>
export type ViewerLinkMode = z.infer<typeof viewerLinkModeSchema>
export type ViewerLink = z.infer<typeof viewerLinkSchema>
export type Webhook = z.infer<typeof webhookSchema>
export type WebhookDelivery = z.infer<typeof webhookDeliverySchema>

export const WEBHOOK_EVENT_TYPES = ["sandbox.state"] as const

export function getToken(): string | null {
  if (typeof window === "undefined") return null
  try {
    return window.localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

function setToken(token: string) {
  window.localStorage.setItem(TOKEN_KEY, token)
}

function clearToken() {
  window.localStorage.removeItem(TOKEN_KEY)
  setWorkspaceId(null)
}

export function getWorkspaceId(): string | null {
  if (typeof window === "undefined") return null
  try {
    return window.localStorage.getItem(WORKSPACE_KEY)
  } catch {
    return null
  }
}

function setWorkspaceId(id: string | null) {
  try {
    if (id) window.localStorage.setItem(WORKSPACE_KEY, id)
    else window.localStorage.removeItem(WORKSPACE_KEY)
  } catch {
    // private mode: the personal workspace for this page load
  }
}

/** The token and the selected workspace, for every authenticated request. */
function authHeaders(init?: HeadersInit): Headers {
  const headers = new Headers(init)
  const token = getToken()
  if (token) headers.set("Authorization", `Bearer ${token}`)
  const workspace = getWorkspaceId()
  if (workspace) headers.set("X-Zoo-Workspace", workspace)
  return headers
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code = "unknown",
    public hint: string | null = null,
    public requestId: string | null = null
  ) {
    super(message)
    this.name = "ApiError"
  }
}

const errorSchema = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
    hint: z.string().nullable().optional(),
    docs_url: z.string().optional(),
    request_id: z.string().optional(),
  }),
})

async function request<T extends z.ZodType>(
  path: string,
  schema: T,
  init: RequestInit = {}
): Promise<z.infer<T>> {
  const headers = authHeaders(init.headers)
  headers.set("Content-Type", "application/json")

  let res: Response
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers })
  } catch {
    throw new ApiError(0, "Can't reach the server. Is the API running?")
  }

  const body: unknown = await res.json().catch(() => null)

  if (!res.ok) throw responseError(res.status, body)

  return schema.parse(body)
}

function responseError(status: number, body: unknown) {
  const parsed = errorSchema.safeParse(body)
  if (!parsed.success) return new ApiError(status, `Request failed (${status})`)
  const { code, message, hint, request_id } = parsed.data.error
  return new ApiError(status, message, code, hint ?? null, request_id ?? null)
}

/** Every item of a paged list endpoint, following next_cursor. */
async function requestAll<T extends z.ZodType>(
  path: string,
  item: T
): Promise<z.infer<T>[]> {
  const items: z.infer<T>[] = []
  let cursor: string | null = null
  do {
    const sep = path.includes("?") ? "&" : "?"
    const query = `${sep}limit=200${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`
    const page: { data: z.infer<T>[]; next_cursor: string | null } =
      await request(`${path}${query}`, pageSchema(item))
    items.push(...page.data)
    cursor = page.next_cursor
  } while (cursor)
  return items
}

export const api = {
  auth: {
    login: (input: LoginInput) =>
      request("/auth/login", tokenSchema, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    register: ({ confirmPassword: _, ...input }: RegisterInput) =>
      request("/auth/signup", tokenSchema, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    me: () => request("/auth/me", userSchema),
    onboarded: () => request("/auth/me/onboarded", z.null(), { method: "PUT" }),
  },
  sandboxes: {
    list: () => requestAll("/sandboxes", sandboxSchema),
    get: (id: string) => request(`/sandboxes/${id}`, sandboxSchema),
    create: (input: CreateSandboxInput) =>
      request("/sandboxes", sandboxSchema, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    remove: (id: string) =>
      request(`/sandboxes/${id}`, deletedSchema, { method: "DELETE" }),
    start: (id: string) =>
      request(`/sandboxes/${id}/start`, sandboxSchema, { method: "POST" }),
    stop: (id: string) =>
      request(`/sandboxes/${id}/stop`, sandboxSchema, { method: "POST" }),
    upgrade: (id: string) =>
      request(`/sandboxes/${id}/upgrade`, sandboxSchema, { method: "POST" }),
    secrets: (id: string) =>
      request(`/sandboxes/${id}/secrets`, z.array(secretSchema)),
    setSecret: (id: string, input: SecretInput) =>
      request(`/sandboxes/${id}/secrets`, z.array(secretSchema), {
        method: "PUT",
        body: JSON.stringify(input),
      }),
    removeSecret: (id: string, secretId: string) =>
      request(`/sandboxes/${id}/secrets/${secretId}`, z.array(secretSchema), {
        method: "DELETE",
      }),
    executions: (id: string) =>
      request(
        `/sandboxes/${id}/executions?limit=100`,
        pageSchema(executionSchema)
      ).then((page) => page.data),
    exec: (id: string, command: string) =>
      request(`/sandboxes/${id}/exec`, execResultSchema, {
        method: "POST",
        body: JSON.stringify({ command }),
      }),
    permissions: (id: string) =>
      request(`/sandboxes/${id}/permissions`, z.array(permissionSchema)),
    setPermission: (
      id: string,
      input: Pick<Permission, "permission" | "action" | "effect">
    ) =>
      request(`/sandboxes/${id}/permissions`, z.array(permissionSchema), {
        method: "PUT",
        body: JSON.stringify(input),
      }),
    network: (id: string) => request(`/sandboxes/${id}/network`, networkSchema),
    setNetwork: (id: string, input: Omit<Network, "rules">) =>
      request(`/sandboxes/${id}/network`, networkSchema, {
        method: "PUT",
        body: JSON.stringify(input),
      }),
    addRule: (id: string, input: NetworkRuleInput) =>
      request(`/sandboxes/${id}/network/rules`, networkSchema, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    removeRule: (id: string, ruleId: string) =>
      request(`/sandboxes/${id}/network/rules/${ruleId}`, networkSchema, {
        method: "DELETE",
      }),
    apps: (id: string) => request(`/sandboxes/${id}/apps`, z.array(appSchema)),
    setApp: (id: string, binary: string, effect: Effect) =>
      request(
        `/sandboxes/${id}/apps/${encodeURIComponent(binary)}`,
        z.array(appSchema),
        { method: "PUT", body: JSON.stringify({ effect }) }
      ),
  },
  monitoring: () => request("/monitoring", monitoringSchema),
  platforms: {
    list: () => request("/platforms", z.array(platformSchema)),
  },
  servers: {
    list: () => request("/servers", z.array(serverSchema)),
    installCommand: (platform: string) =>
      request(
        `/servers/install-command?platform=${platform}`,
        installCommandSchema
      ),
    status: (id: string) =>
      request(`/servers/${id}/status`, serverStatusSchema),
    create: (input: ServerInput) =>
      request("/servers", serverSchema, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    remove: (id: string) =>
      request(`/servers/${id}`, z.null(), { method: "DELETE" }),
    migrate: (id: string) =>
      request(`/servers/${id}/migrate`, z.null(), { method: "POST" }),
    base: (id: string) => request(`/servers/${id}/base`, baseStatusSchema),
    baseAction: ({
      id,
      action,
      iso,
      edition,
    }: {
      id: string
      action: "install" | "start" | "stop"
      iso?: string
      edition?: string
    }) =>
      request(`/servers/${id}/base/${action}`, baseStatusSchema, {
        method: "POST",
        ...(iso && { body: JSON.stringify({ iso, edition: edition || null }) }),
      }),
    baseSetup: (id: string) =>
      request(`/servers/${id}/base/setup`, z.null(), { method: "POST" }),
  },
  kubernetes: {
    status: () => request("/kubernetes", kubernetesSchema),
    // starts a probe pod on every sandbox node, so it takes a while (admins)
    check: () =>
      request("/kubernetes/check", kubernetesSchema, { method: "POST" }),
  },
  nodes: {
    createToken: ({ name, platform }: { name: string; platform: string }) =>
      request(`/nodes/tokens?platform=${platform}`, nodeTokenSchema, {
        method: "POST",
        body: JSON.stringify({ name }),
      }),
  },
  viewerLinks: {
    list: (sandboxId: string) =>
      request(
        `/sandboxes/${sandboxId}/viewer-links`,
        z.array(viewerLinkSchema)
      ),
    create: ({
      sandboxId,
      ...input
    }: CreateViewerLinkInput & { sandboxId: string }) =>
      request(`/sandboxes/${sandboxId}/viewer-links`, createdViewerLinkSchema, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    revoke: ({ sandboxId, id }: { sandboxId: string; id: string }) =>
      request(`/sandboxes/${sandboxId}/viewer-links/${id}`, z.null(), {
        method: "DELETE",
      }),
  },
  webhooks: {
    list: () => request("/webhooks", z.array(webhookSchema)),
    create: (input: WebhookInput) =>
      request("/webhooks", createdWebhookSchema, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    update: ({ id, ...input }: WebhookInput & { id: string }) =>
      request(`/webhooks/${id}`, webhookSchema, {
        method: "PUT",
        body: JSON.stringify(input),
      }),
    remove: (id: string) =>
      request(`/webhooks/${id}`, z.null(), { method: "DELETE" }),
    deliveries: (id: string) =>
      request(
        `/webhooks/${id}/deliveries?limit=100`,
        pageSchema(webhookDeliverySchema)
      ).then((page) => page.data),
    test: (id: string) =>
      request(`/webhooks/${id}/test`, z.object({ event_id: z.string() }), {
        method: "POST",
      }),
  },
  snapshots: {
    list: (sandboxId: string) =>
      request(`/sandboxes/${sandboxId}/snapshots`, z.array(snapshotSchema)),
    create: ({ sandboxId, name }: { sandboxId: string; name: string }) =>
      request(`/sandboxes/${sandboxId}/snapshots`, snapshotSchema, {
        method: "POST",
        body: JSON.stringify({ name }),
      }),
    restore: ({ sandboxId, id }: { sandboxId: string; id: string }) =>
      request(
        `/sandboxes/${sandboxId}/snapshots/${id}/restore`,
        sandboxSchema,
        {
          method: "POST",
        }
      ),
    remove: ({ sandboxId, id }: { sandboxId: string; id: string }) =>
      request(`/sandboxes/${sandboxId}/snapshots/${id}`, z.null(), {
        method: "DELETE",
      }),
  },
  pool: {
    list: () => request("/pool", z.array(poolEntrySchema)),
    set: (input: { kind: string; server_id: string | null; size: number }) =>
      request("/pool", poolEntrySchema, {
        method: "PUT",
        body: JSON.stringify(input),
      }),
  },
  domains: {
    list: () => request("/admin/domains", z.array(domainSchema)),
    create: (hostname: string) =>
      request("/admin/domains", domainSchema, {
        method: "POST",
        body: JSON.stringify({ hostname }),
      }),
    remove: (id: string) =>
      request(`/admin/domains/${id}`, z.null(), { method: "DELETE" }),
    verify: (id: string) =>
      request(`/admin/domains/${id}/verify`, domainSchema, { method: "POST" }),
  },
  profiles: {
    list: () => request("/profiles", z.array(profileSchema)),
    apps: (platform: string) =>
      request(
        `/profile-apps?platform=${encodeURIComponent(platform)}`,
        z.record(z.string(), z.string())
      ),
    capture: (
      id: string,
      input: { name: string; app: string; profile_id?: string }
    ) =>
      request(`/sandboxes/${id}/profiles`, profileSchema, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    apply: (id: string, profileId: string, version?: number) =>
      request(
        `/sandboxes/${id}/profiles/${profileId}${version ? `?version=${version}` : ""}`,
        profileSchema,
        { method: "POST" }
      ),
    versions: (id: string) =>
      request(`/profiles/${id}/versions`, z.array(profileVersionSchema)),
    removeVersion: ({ id, version }: { id: string; version: number }) =>
      request(`/profiles/${id}/versions/${version}`, profileSchema, {
        method: "DELETE",
      }),
    rename: ({ id, name }: { id: string; name: string }) =>
      request(`/profiles/${id}`, profileSchema, {
        method: "PATCH",
        body: JSON.stringify({ name }),
      }),
    remove: (id: string) =>
      request(`/profiles/${id}`, z.null(), { method: "DELETE" }),
  },
  vault: {
    secrets: () => request("/vault/secrets", z.array(vaultSecretSchema)),
    create: (input: VaultSecretInput) =>
      request("/vault/secrets", z.array(vaultSecretSchema), {
        method: "POST",
        body: JSON.stringify(input),
      }),
    update: ({
      id,
      ...input
    }: {
      id: string
      value?: string
      description?: string
      // null clears them
      expires_at?: string | null
      rotate_every_days?: number | null
    }) =>
      request(`/vault/secrets/${id}`, z.array(vaultSecretSchema), {
        method: "PATCH",
        body: JSON.stringify(input),
      }),
    remove: (id: string) =>
      request(`/vault/secrets/${id}`, z.array(vaultSecretSchema), {
        method: "DELETE",
      }),
    attached: (sandboxId: string) =>
      request(
        `/sandboxes/${sandboxId}/vault-secrets`,
        z.array(attachedSecretSchema)
      ),
    attach: (sandboxId: string, secretId: string) =>
      request(
        `/sandboxes/${sandboxId}/vault-secrets/${secretId}`,
        z.array(attachedSecretSchema),
        { method: "PUT" }
      ),
    detach: (sandboxId: string, secretId: string) =>
      request(
        `/sandboxes/${sandboxId}/vault-secrets/${secretId}`,
        z.array(attachedSecretSchema),
        { method: "DELETE" }
      ),
    activity: () => request("/vault/activity", z.array(vaultActivitySchema)),
    reminders: () => request("/vault/reminders", z.array(vaultReminderSchema)),
  },
  move: (id: string, serverId: string | null) =>
    request(`/sandboxes/${id}/move`, sandboxSchema, {
      method: "POST",
      body: JSON.stringify({ server_id: serverId }),
    }),
  apiKeys: {
    list: () => request("/api-keys", z.array(apiKeySchema)),
    create: (input: CreateApiKeyInput) =>
      request("/api-keys", createdApiKeySchema, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    revoke: (id: string) =>
      request(`/api-keys/${id}`, z.array(apiKeySchema), { method: "DELETE" }),
  },
  workspaces: {
    list: () => request("/workspaces", z.array(workspaceSchema)),
    create: (name: string) =>
      request("/workspaces", workspaceSchema, {
        method: "POST",
        body: JSON.stringify({ name }),
      }),
    rename: ({ id, name }: { id: string; name: string }) =>
      request(`/workspaces/${id}`, workspaceSchema, {
        method: "PATCH",
        body: JSON.stringify({ name }),
      }),
    remove: (id: string) =>
      request(`/workspaces/${id}`, z.null(), { method: "DELETE" }),
    quota: (id: string) => request(`/workspaces/${id}/quota`, quotaSchema),
    setLifecycle: ({
      id,
      ...input
    }: {
      id: string
      default_idle_timeout_minutes: number | null
      default_max_lifetime_minutes: number | null
    }) =>
      request(`/workspaces/${id}/lifecycle`, workspaceSchema, {
        method: "PUT",
        body: JSON.stringify(input),
      }),
    members: (id: string) =>
      request(`/workspaces/${id}/members`, z.array(memberSchema)),
    setRole: ({
      id,
      userId,
      role,
    }: {
      id: string
      userId: string
      role: Role
    }) =>
      request(`/workspaces/${id}/members/${userId}`, z.array(memberSchema), {
        method: "PATCH",
        body: JSON.stringify({ role }),
      }),
    removeMember: ({ id, userId }: { id: string; userId: string }) =>
      request(`/workspaces/${id}/members/${userId}`, z.null(), {
        method: "DELETE",
      }),
    invitations: (id: string) =>
      request(`/workspaces/${id}/invitations`, z.array(invitationSchema)),
    invite: ({ id, email, role }: { id: string; email: string; role: Role }) =>
      request(`/workspaces/${id}/invitations`, createdInvitationSchema, {
        method: "POST",
        body: JSON.stringify({ email, role }),
      }),
    revokeInvitation: ({
      id,
      invitationId,
    }: {
      id: string
      invitationId: string
    }) =>
      request(`/workspaces/${id}/invitations/${invitationId}`, z.null(), {
        method: "DELETE",
      }),
    accept: (token: string) =>
      request(
        `/invitations/${encodeURIComponent(token)}/accept`,
        workspaceSchema,
        {
          method: "POST",
        }
      ),
  },
  audit: {
    page: (filters: AuditFilters, cursor?: string | null) =>
      request(`/audit-logs${auditQuery(filters, cursor)}`, auditPageSchema),
  },
}

export function sandboxBackupUrl(id: string) {
  return `${API_URL}/sandboxes/${id}/backup`
}

export const MCP_URL = `${API_URL}/mcp/`
/** The REST API's base URL, /v1 included, for snippets people paste elsewhere. */
export const API_BASE = API_URL

export function apiUrl(path: string) {
  return `${API_URL}${path}`
}

const vncTicketSchema = z.object({ ticket: z.string(), expires_in: z.number() })

// the viewer URL carries a single-use, 30 second ticket instead of the session token
async function ticketedSocketUrl(path: string) {
  const { ticket } = await request(`${path}/vnc-ticket`, vncTicketSchema, {
    method: "POST",
  })
  const url = new URL(`${API_URL}${path}/ws`, window.location.origin)
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:"
  url.searchParams.set("ticket", ticket)
  return url.toString()
}

export function sandboxSocketUrl(id: string) {
  return ticketedSocketUrl(`/sandboxes/${id}`)
}

export function baseSocketUrl(serverId: string) {
  return ticketedSocketUrl(`/servers/${serverId}/base`)
}

// a shell in the sandbox; needs its guest, which sandboxes started before it don't have
export async function terminalSocketUrl(
  id: string,
  cols: number,
  rows: number
) {
  const { ticket } = await request(
    `/sandboxes/${id}/terminal-ticket`,
    vncTicketSchema,
    { method: "POST" }
  )
  const url = new URL(
    `${API_URL}/sandboxes/${id}/terminal`,
    window.location.origin
  )
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:"
  url.searchParams.set("ticket", ticket)
  url.searchParams.set("cols", String(cols))
  url.searchParams.set("rows", String(rows))
  return url.toString()
}

export const queryKeys = {
  me: ["auth", "me"] as const,
  sandboxes: ["sandboxes"] as const,
  sandbox: (id: string) => ["sandboxes", id] as const,
  permissions: (id: string) => ["sandboxes", id, "permissions"] as const,
  network: (id: string) => ["sandboxes", id, "network"] as const,
  apps: (id: string) => ["sandboxes", id, "apps"] as const,
  secrets: (id: string) => ["sandboxes", id, "secrets"] as const,
  executions: (id: string) => ["sandboxes", id, "executions"] as const,
  monitoring: ["monitoring"] as const,
  apiKeys: ["api-keys"] as const,
  workspaces: ["workspaces"] as const,
  members: (id: string) => ["workspaces", id, "members"] as const,
  invitations: (id: string) => ["workspaces", id, "invitations"] as const,
  quota: (id: string) => ["workspaces", id, "quota"] as const,
  audit: (filters: AuditFilters) => ["audit", filters] as const,
  servers: ["servers"] as const,
  pool: ["pool"] as const,
  kubernetes: ["kubernetes"] as const,
  // under "servers", so adding or removing a server refreshes what can run
  platforms: ["servers", "platforms"] as const,
  installCommand: (platform: string) =>
    ["servers", "install-command", platform] as const,
  server: (id: string) => ["servers", id] as const,
  snapshots: (sandboxId: string) =>
    ["sandboxes", sandboxId, "snapshots"] as const,
  viewerLinks: (sandboxId: string) =>
    ["sandboxes", sandboxId, "viewer-links"] as const,
  webhooks: ["webhooks"] as const,
  webhookDeliveries: (id: string) => ["webhooks", id, "deliveries"] as const,
  base: (id: string) => ["servers", id, "base"] as const,
  profiles: ["profiles"] as const,
  vault: ["vault"] as const,
  vaultSecrets: ["vault", "secrets"] as const,
  vaultActivity: ["vault", "activity"] as const,
  vaultReminders: ["vault", "reminders"] as const,
  attachedSecrets: (id: string) => ["sandboxes", id, "vault-secrets"] as const,
  domains: ["domains"] as const,
}

export const isSettling = (sandbox: Sandbox) =>
  sandbox.job !== null ||
  sandbox.status === "pending" ||
  sandbox.status === "provisioning" ||
  sandbox.status === "deleting"

const JOB_STATUS = {
  boot: "provisioning",
  stop: "stopping",
  delete: "deleting",
  move: "moving",
  snapshot: "snapshotting",
  restore: "restoring",
} as const

/** The status to show: work in progress first, then an unreachable host. */
export function displayStatus(sandbox: Sandbox): string {
  if (sandbox.job) return JOB_STATUS[sandbox.job.kind]
  if (sandbox.unreachable && sandbox.status === "running") return "unreachable"
  return sandbox.status
}

export function useMe() {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: async (): Promise<User | null> => {
      if (!getToken()) return null
      try {
        return await api.auth.me()
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          clearToken()
          return null
        }
        throw error
      }
    },
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
}

function useAuthMutation<TInput>(
  fn: (input: TInput) => Promise<TokenResponse>
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: async (data) => {
      setToken(data.access_token)
      await queryClient.invalidateQueries({ queryKey: queryKeys.me })
    },
  })
}

export function useLogin() {
  return useAuthMutation(api.auth.login)
}

export function useRegister() {
  return useAuthMutation(api.auth.register)
}

export function useLogout() {
  const queryClient = useQueryClient()
  return () => {
    clearToken()
    queryClient.setQueryData(queryKeys.me, null)
    queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== "auth" })
  }
}

const eventSchema = z.object({
  seq: z.number(),
  type: z.string(),
  sandbox_id: z.string().nullable(),
  data: z.record(z.string(), z.unknown()),
})

export type ZooEvent = z.infer<typeof eventSchema>

/** "live" while the workspace's event stream is connected, "offline" while the server can't be reached. */
export type Connection = "connecting" | "live" | "offline"

/**
 * Follows the workspace's events (GET /events/stream) for as long as the app is open, reconnecting with backoff and
 * Last-Event-ID, and refreshes the queries each event touches; this replaces polling for sandbox state.
 */
export function useEventStream(
  enabled: boolean,
  onConnection?: (state: Connection) => void
) {
  const queryClient = useQueryClient()
  const workspace = getWorkspaceId()
  useEffect(() => {
    if (!enabled) return
    const abort = new AbortController()
    const stopped = () => abort.signal.aborted
    let lastId: string | null = null
    let delay = 1000

    function touched(event: ZooEvent) {
      const id = event.sandbox_id
      if (event.type === "sandbox.state") {
        void queryClient.invalidateQueries({
          queryKey: queryKeys.sandboxes,
          exact: true,
        })
      }
      if (!id) return
      if (event.type === "sandbox.state") {
        void queryClient.invalidateQueries({
          queryKey: queryKeys.sandbox(id),
          exact: true,
        })
      }
    }

    async function follow() {
      while (!stopped()) {
        try {
          const headers = authHeaders()
          if (lastId) headers.set("Last-Event-ID", lastId)
          const res = await fetch(`${API_URL}/events/stream`, {
            headers,
            signal: abort.signal,
          })
          if (!res.ok || !res.body) throw new Error(`events: ${res.status}`)
          onConnection?.("live")
          delay = 1000
          // anything may have changed while disconnected
          void queryClient.invalidateQueries({ queryKey: queryKeys.sandboxes })
          const reader = res.body
            .pipeThrough(new TextDecoderStream())
            .getReader()
          let buffer = ""
          for (;;) {
            const { value, done } = await reader.read()
            if (done) break
            buffer += value
            let end
            while ((end = buffer.indexOf("\n\n")) >= 0) {
              const frame = buffer.slice(0, end)
              buffer = buffer.slice(end + 2)
              let data = ""
              for (const line of frame.split("\n")) {
                if (line.startsWith("id: ")) lastId = line.slice(4)
                else if (line.startsWith("data: ")) data += line.slice(6)
              }
              if (!data) continue
              const parsed = eventSchema.safeParse(JSON.parse(data))
              if (parsed.success) touched(parsed.data)
            }
          }
        } catch {
          if (stopped()) return
        }
        onConnection?.("offline")
        await new Promise((resolve) => setTimeout(resolve, delay))
        delay = Math.min(delay * 2, 30000)
      }
    }

    onConnection?.("connecting")
    void follow()
    return () => abort.abort()
  }, [enabled, queryClient, workspace, onConnection])
}

export function useSandboxes() {
  return useQuery({
    queryKey: queryKeys.sandboxes,
    queryFn: api.sandboxes.list,
  })
}

export function useSandbox(id: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.sandbox(id),
    queryFn: () => api.sandboxes.get(id),
    enabled,
    retry: false,
  })
}

export function useCreateSandbox() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.sandboxes.create,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.sandboxes })
    },
  })
}

export function useDeleteSandbox() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.sandboxes.remove,
    onSuccess: async (_, id) => {
      queryClient.removeQueries({ queryKey: queryKeys.sandbox(id) })
      await queryClient.invalidateQueries({ queryKey: queryKeys.sandboxes })
    },
  })
}

function useSandboxMutation<TInput, TData>(
  queryKey: ReadonlyArray<string>,
  fn: (input: TInput) => Promise<TData>
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: (data) => queryClient.setQueryData(queryKey, data),
  })
}

export function usePermissions(id: string) {
  return useQuery({
    queryKey: queryKeys.permissions(id),
    queryFn: () => api.sandboxes.permissions(id),
  })
}

export function useSetPermission(id: string) {
  return useSandboxMutation(
    queryKeys.permissions(id),
    (input: Pick<Permission, "permission" | "action" | "effect">) =>
      api.sandboxes.setPermission(id, input)
  )
}

export function useNetwork(id: string) {
  return useQuery({
    queryKey: queryKeys.network(id),
    queryFn: () => api.sandboxes.network(id),
  })
}

export function useSetNetwork(id: string) {
  return useSandboxMutation(
    queryKeys.network(id),
    (input: Omit<Network, "rules">) => api.sandboxes.setNetwork(id, input)
  )
}

export function useAddRule(id: string) {
  return useSandboxMutation(queryKeys.network(id), (input: NetworkRuleInput) =>
    api.sandboxes.addRule(id, input)
  )
}

export function useRemoveRule(id: string) {
  return useSandboxMutation(queryKeys.network(id), (ruleId: string) =>
    api.sandboxes.removeRule(id, ruleId)
  )
}

export function useApps(id: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.apps(id),
    queryFn: () => api.sandboxes.apps(id),
    enabled,
  })
}

export function useSetApp(id: string) {
  return useSandboxMutation(
    queryKeys.apps(id),
    ({ binary, effect }: { binary: string; effect: Effect }) =>
      api.sandboxes.setApp(id, binary, effect)
  )
}

export function useMonitoring() {
  return useQuery({
    queryKey: queryKeys.monitoring,
    queryFn: api.monitoring,
    refetchInterval: 5000,
  })
}

export function useSandboxAction(action: "start" | "stop" | "upgrade") {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.sandboxes[action],
    onSuccess: async (data) => {
      queryClient.setQueryData(queryKeys.sandbox(data.id), data)
      await queryClient.invalidateQueries({ queryKey: queryKeys.sandboxes })
    },
  })
}

export function useSecrets(id: string) {
  return useQuery({
    queryKey: queryKeys.secrets(id),
    queryFn: () => api.sandboxes.secrets(id),
  })
}

export function useSetSecret(id: string) {
  return useSandboxMutation(queryKeys.secrets(id), (input: SecretInput) =>
    api.sandboxes.setSecret(id, input)
  )
}

export function useRemoveSecret(id: string) {
  return useSandboxMutation(queryKeys.secrets(id), (secretId: string) =>
    api.sandboxes.removeSecret(id, secretId)
  )
}

export function useExecutions(id: string) {
  return useQuery({
    queryKey: queryKeys.executions(id),
    queryFn: () => api.sandboxes.executions(id),
  })
}

export function useViewerLinks(sandboxId: string) {
  return useQuery({
    queryKey: queryKeys.viewerLinks(sandboxId),
    queryFn: () => api.viewerLinks.list(sandboxId),
  })
}

export function useCreateViewerLink(sandboxId: string) {
  return useInvalidatingMutation(
    queryKeys.viewerLinks(sandboxId),
    (input: CreateViewerLinkInput) =>
      api.viewerLinks.create({ sandboxId, ...input })
  )
}

export function useRevokeViewerLink(sandboxId: string) {
  return useInvalidatingMutation(
    queryKeys.viewerLinks(sandboxId),
    (id: string) => api.viewerLinks.revoke({ sandboxId, id })
  )
}

export function useWebhooks(enabled = true) {
  return useQuery({
    queryKey: queryKeys.webhooks,
    queryFn: api.webhooks.list,
    enabled,
  })
}

export function useCreateWebhook() {
  return useInvalidatingMutation(queryKeys.webhooks, api.webhooks.create)
}

export function useUpdateWebhook() {
  return useInvalidatingMutation(queryKeys.webhooks, api.webhooks.update)
}

export function useRemoveWebhook() {
  return useInvalidatingMutation(queryKeys.webhooks, api.webhooks.remove)
}

export function useTestWebhook() {
  return useInvalidatingMutation(queryKeys.webhooks, api.webhooks.test)
}

export function useWebhookDeliveries(id: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.webhookDeliveries(id),
    queryFn: () => api.webhooks.deliveries(id),
    enabled,
    refetchInterval: enabled ? 5000 : false,
  })
}

export function useApiKeys() {
  return useQuery({ queryKey: queryKeys.apiKeys, queryFn: api.apiKeys.list })
}

/** Marks the first-run steps done (or skipped). */
export function useFinishOnboarding() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.auth.onboarded,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.me })
    },
  })
}

export function useCreateApiKey() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.apiKeys.create,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.apiKeys })
    },
  })
}

export function useRevokeApiKey() {
  return useSandboxMutation(queryKeys.apiKeys, api.apiKeys.revoke)
}

export async function downloadBackup(id: string, name: string) {
  const res = await fetch(sandboxBackupUrl(id), { headers: authHeaders() })
  if (!res.ok) throw new ApiError(res.status, "Backup failed")
  const url = URL.createObjectURL(await res.blob())
  const a = document.createElement("a")
  a.href = url
  a.download = `${name}.tar`
  a.click()
  URL.revokeObjectURL(url)
}

function useInvalidatingMutation<TInput, TData>(
  queryKey: ReadonlyArray<string>,
  fn: (input: TInput) => Promise<TData>
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => queryClient.invalidateQueries({ queryKey }),
  })
}

export function useServers(refetchInterval: number | false = false) {
  return useQuery({
    queryKey: queryKeys.servers,
    queryFn: api.servers.list,
    refetchInterval,
  })
}

export function usePool() {
  return useQuery({
    queryKey: queryKeys.pool,
    queryFn: api.pool.list,
    refetchInterval: (query) =>
      query.state.data?.some((e) => e.booting > 0 || e.idle < e.size)
        ? 3000
        : 15000,
  })
}

export function useSetPool() {
  return useInvalidatingMutation(queryKeys.pool, api.pool.set)
}

export function usePlatforms() {
  return useQuery({
    queryKey: queryKeys.platforms,
    queryFn: api.platforms.list,
  })
}

export function useInstallCommand(platform: string) {
  return useQuery({
    queryKey: queryKeys.installCommand(platform),
    queryFn: () => api.servers.installCommand(platform),
    staleTime: Infinity,
  })
}

export function useServerStatus(id: string) {
  return useQuery({
    queryKey: queryKeys.server(id),
    queryFn: () => api.servers.status(id),
    refetchInterval: 15000,
  })
}

export function useBaseStatus(id: string) {
  return useQuery({
    queryKey: queryKeys.base(id),
    queryFn: () => api.servers.base(id),
    refetchInterval: (query) =>
      query.state.data?.state === "installing" ? 3000 : 15000,
  })
}

export function useBaseAction(id: string) {
  return useInvalidatingMutation(queryKeys.base(id), api.servers.baseAction)
}

export function useBaseSetup() {
  return useMutation({ mutationFn: api.servers.baseSetup })
}

export function useCreateServer() {
  return useInvalidatingMutation(queryKeys.servers, api.servers.create)
}

export function useRemoveServer() {
  return useInvalidatingMutation(queryKeys.servers, api.servers.remove)
}

export function useMigrateServer() {
  return useInvalidatingMutation(queryKeys.servers, api.servers.migrate)
}

export function useKubernetes() {
  return useQuery({
    queryKey: queryKeys.kubernetes,
    queryFn: api.kubernetes.status,
    refetchInterval: 30000,
  })
}

export function useKubernetesCheck() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.kubernetes.check,
    onSuccess: (status) =>
      queryClient.setQueryData(queryKeys.kubernetes, status),
  })
}

export function useCreateNodeToken() {
  return useMutation({ mutationFn: api.nodes.createToken })
}

export function useSnapshots(sandboxId: string) {
  return useQuery({
    queryKey: queryKeys.snapshots(sandboxId),
    queryFn: () => api.snapshots.list(sandboxId),
    refetchInterval: (query) =>
      query.state.data?.some((s) => s.state === "creating") ? 2000 : false,
  })
}

/** Snapshot changes also change the sandbox (its job), so both refresh. */
function useSnapshotMutation<T>(fn: (input: T) => Promise<unknown>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sandboxes"] }),
  })
}

export function useCreateSnapshot() {
  return useSnapshotMutation(api.snapshots.create)
}

export function useRestoreSnapshot() {
  return useSnapshotMutation(api.snapshots.restore)
}

export function useRemoveSnapshot() {
  return useSnapshotMutation(api.snapshots.remove)
}

export function useProfiles() {
  return useQuery({ queryKey: queryKeys.profiles, queryFn: api.profiles.list })
}

export function useProfileApps(platform: string) {
  return useQuery({
    queryKey: ["profile-apps", platform],
    queryFn: () => api.profiles.apps(platform),
  })
}

export function useCaptureProfile(id: string) {
  return useInvalidatingMutation(
    queryKeys.profiles,
    (input: { name: string; app: string; profile_id?: string }) =>
      api.profiles.capture(id, input)
  )
}

export function useApplyProfile(id: string) {
  return useMutation({
    mutationFn: ({
      profileId,
      version,
    }: {
      profileId: string
      version?: number
    }) => api.profiles.apply(id, profileId, version),
  })
}

export function useProfileVersions(id: string | null) {
  return useQuery({
    queryKey: ["profiles", id, "versions"],
    queryFn: () => api.profiles.versions(id ?? ""),
    enabled: id !== null,
  })
}

export function useRemoveProfileVersion() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.profiles.removeVersion,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.profiles })
      queryClient.invalidateQueries({ queryKey: queryKeys.vaultActivity })
    },
  })
}

export function useRemoveProfile() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.profiles.remove,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.profiles })
      queryClient.invalidateQueries({ queryKey: queryKeys.vaultActivity })
    },
  })
}

export function useRenameProfile() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.profiles.rename,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.profiles })
      queryClient.invalidateQueries({ queryKey: queryKeys.vaultActivity })
    },
  })
}

export function useVaultSecrets() {
  return useQuery({
    queryKey: queryKeys.vaultSecrets,
    queryFn: api.vault.secrets,
  })
}

export function useVaultActivity() {
  return useQuery({
    queryKey: queryKeys.vaultActivity,
    queryFn: api.vault.activity,
  })
}

function useVaultMutation<TInput>(
  fn: (input: TInput) => Promise<Array<VaultSecret>>
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.vaultSecrets, data)
      queryClient.invalidateQueries({ queryKey: queryKeys.vaultActivity })
      queryClient.invalidateQueries({ queryKey: queryKeys.vaultReminders })
    },
  })
}

export function useVaultReminders() {
  return useQuery({
    queryKey: queryKeys.vaultReminders,
    queryFn: api.vault.reminders,
  })
}

export function useCreateVaultSecret() {
  return useVaultMutation(api.vault.create)
}

export function useUpdateVaultSecret() {
  return useVaultMutation(api.vault.update)
}

export function useRemoveVaultSecret() {
  return useVaultMutation(api.vault.remove)
}

export function useAttachedSecrets(sandboxId: string) {
  return useQuery({
    queryKey: queryKeys.attachedSecrets(sandboxId),
    queryFn: () => api.vault.attached(sandboxId),
  })
}

export function useToggleAttachedSecret(sandboxId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, attach }: { id: string; attach: boolean }) =>
      attach
        ? api.vault.attach(sandboxId, id)
        : api.vault.detach(sandboxId, id),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.attachedSecrets(sandboxId), data)
      queryClient.invalidateQueries({ queryKey: queryKeys.vault })
    },
  })
}

export function useMoveSandbox(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (serverId: string | null) => api.move(id, serverId),
    onSuccess: async (data) => {
      queryClient.setQueryData(queryKeys.sandbox(data.id), data)
      await queryClient.invalidateQueries({ queryKey: queryKeys.sandboxes })
    },
  })
}

export function useDomains() {
  return useQuery({
    queryKey: queryKeys.domains,
    queryFn: api.domains.list,
    retry: false,
  })
}

export function useAddDomain() {
  return useInvalidatingMutation(queryKeys.domains, api.domains.create)
}

export function useRemoveDomain() {
  return useInvalidatingMutation(queryKeys.domains, api.domains.remove)
}

export function useVerifyDomain() {
  return useInvalidatingMutation(queryKeys.domains, api.domains.verify)
}

export function useWorkspaces() {
  return useQuery({
    queryKey: queryKeys.workspaces,
    queryFn: api.workspaces.list,
  })
}

/** The workspace this browser acts in: the selected one, else the personal one. */
export function useCurrentWorkspace() {
  const workspaces = useWorkspaces()
  const selected = getWorkspaceId()
  const list = workspaces.data ?? []
  return list.find((w) => w.id === selected) ?? list.find((w) => w.personal)
}

/** Switches workspace: everything cached belongs to the old one, so it all goes. */
export function useSwitchWorkspace() {
  const queryClient = useQueryClient()
  return (workspace: Workspace) => {
    setWorkspaceId(workspace.personal ? null : workspace.id)
    queryClient.removeQueries({
      predicate: (q) =>
        q.queryKey[0] !== "auth" && q.queryKey[0] !== "workspaces",
    })
    void queryClient.invalidateQueries()
  }
}

export function useCreateWorkspace() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.workspaces.create,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.workspaces }),
  })
}

export function useRenameWorkspace() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.workspaces.rename,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.workspaces }),
  })
}

export function useMembers(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.members(id ?? ""),
    queryFn: () => api.workspaces.members(id ?? ""),
    enabled: !!id,
  })
}

export function useSetRole() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.workspaces.setRole,
    onSuccess: (_, { id }) =>
      queryClient.invalidateQueries({ queryKey: queryKeys.members(id) }),
  })
}

export function useRemoveMember() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.workspaces.removeMember,
    onSuccess: (_, { id }) =>
      queryClient.invalidateQueries({ queryKey: queryKeys.members(id) }),
  })
}

export function useInvitations(id: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.invitations(id ?? ""),
    queryFn: () => api.workspaces.invitations(id ?? ""),
    enabled: !!id && enabled,
  })
}

export function useInvite() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.workspaces.invite,
    onSuccess: (_, { id }) =>
      queryClient.invalidateQueries({ queryKey: queryKeys.invitations(id) }),
  })
}

export function useRevokeInvitation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.workspaces.revokeInvitation,
    onSuccess: (_, { id }) =>
      queryClient.invalidateQueries({ queryKey: queryKeys.invitations(id) }),
  })
}

export function useAcceptInvitation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.workspaces.accept,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.workspaces }),
  })
}

export function useAuditLog(filters: AuditFilters) {
  return useInfiniteQuery({
    queryKey: queryKeys.audit(filters),
    queryFn: ({ pageParam }) => api.audit.page(filters, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  })
}

/** Downloads the audit log, filtered like the page, as CSV. */
export async function downloadAuditLog(filters: AuditFilters) {
  const res = await fetch(
    `${API_URL}/audit-logs/export${auditQuery(filters)}`,
    {
      headers: authHeaders(),
    }
  )
  if (!res.ok)
    throw responseError(res.status, await res.json().catch(() => null))
  const url = URL.createObjectURL(await res.blob())
  const a = document.createElement("a")
  a.href = url
  a.download = "audit-log.csv"
  a.click()
  URL.revokeObjectURL(url)
}

export function useQuota(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.quota(id ?? ""),
    queryFn: () => api.workspaces.quota(id ?? ""),
    enabled: !!id,
  })
}

export function useSetLifecycleDefaults() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.workspaces.setLifecycle,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.workspaces }),
  })
}
