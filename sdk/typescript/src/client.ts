import type { components } from "./schema.js"
import {
  APIConnectionError,
  APITimeoutError,
  NotFoundError,
  ZooError,
  errorFrom,
} from "./errors.js"

export type SandboxData = components["schemas"]["SandboxResponse"]
export type SandboxKind = NonNullable<
  components["schemas"]["CreateSandboxRequest"]["kind"]
>
export type ExecResult = {
  exit_code: number
  stdout: string
  stderr: string
  timed_out: boolean
}
/** A piece of a streamed command: output as it arrives, then `exit`. */
export type ExecChunk =
  | { stream: "stdout" | "stderr"; data: string }
  | { stream: "exit"; exitCode: number; timedOut: boolean }

export type ZooOptions = {
  /** Defaults to ZOO_API_KEY. */
  apiKey?: string
  /** Defaults to ZOO_URL, then http://localhost:8000. */
  baseUrl?: string
  /** Milliseconds before a call gives up, unless it passes its own (30000). */
  timeout?: number
  /** Retries when safe (3): connection failures and 429 always; 5xx and timeouts for reads and creates. */
  maxRetries?: number
  workspace?: string
  fetch?: typeof fetch
}

export type RequestOptions = {
  json?: unknown
  query?: Record<string, string | number | boolean | undefined | null | string[]>
  body?: BodyInit
  headers?: Record<string, string>
  /** Milliseconds; null for none (streams). */
  timeout?: number | null
  signal?: AbortSignal
}

export type CreateSandboxOptions = {
  kind?: SandboxKind
  name?: string
  labels?: Record<string, string>
  serverId?: string
  profileIds?: string[]
  /** Wait until it runs (true). */
  wait?: boolean
  /** Milliseconds to wait (180000). */
  waitTimeout?: number
}

const VERSION = "0.1.0"
const PREFIX = "/v1"
const IDEMPOTENT = new Set(["GET", "HEAD", "PUT", "DELETE", "OPTIONS"])
// POSTs that create something get an Idempotency-Key, so retrying one never creates two
const CREATES =
  /^\/(sandboxes|sandboxes\/[^/]+\/snapshots|sandboxes\/[^/]+\/viewer-links|webhooks|api-keys|agent\/exports|benchmarks)$/
const SETTLING = new Set(["pending", "provisioning"])

// Node, Bun and Deno have process.env; browsers don't
const env = (name: string): string | undefined =>
  (globalThis as { process?: { env: Record<string, string | undefined> } })
    .process?.env[name]

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

function backoff(attempt: number, response?: Response): number {
  const after = response?.headers.get("Retry-After")
  if (after && /^\d+$/.test(after)) return Math.min(Number(after), 60) * 1000
  return Math.min(500 * 2 ** attempt, 8000) * (0.5 + Math.random() / 2)
}

function uuid(): string {
  return globalThis.crypto.randomUUID().replaceAll("-", "")
}

/** (event, data) of each server-sent event in a response body. */
async function* events(
  body: ReadableStream<Uint8Array>
): AsyncGenerator<[string, unknown]> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let end
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, end)
        buffer = buffer.slice(end + 2)
        let event = "message"
        const data: string[] = []
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim()
          else if (line.startsWith("data:")) data.push(line.slice(5).trimStart())
        }
        if (data.length) yield [event, JSON.parse(data.join("\n"))]
      }
    }
  } finally {
    reader.releaseLock()
    await body.cancel().catch(() => undefined)
  }
}

/**
 * The Zoo API.
 *
 * ```ts
 * const zoo = new Zoo()
 * await using box = await zoo.sandbox({ kind: "desktop" }) // deleted when the scope ends
 * for await (const chunk of box.execStream("make test")) process.stdout.write("data" in chunk ? chunk.data : "")
 * ```
 */
export class Zoo {
  readonly baseUrl: string
  readonly timeout: number
  readonly maxRetries: number
  private readonly headers: Record<string, string>
  private readonly fetcher: typeof fetch

  constructor(options: ZooOptions = {}) {
    const apiKey = options.apiKey ?? env("ZOO_API_KEY")
    if (!apiKey) {
      throw new ZooError(
        "pass apiKey or set ZOO_API_KEY (create a key in Settings → API keys)"
      )
    }
    this.baseUrl = (options.baseUrl ?? env("ZOO_URL") ?? "http://localhost:8000")
      .replace(/\/+$/, "")
      .replace(/\/v1$/, "")
    this.timeout = options.timeout ?? 30000
    this.maxRetries = options.maxRetries ?? 3
    this.fetcher = options.fetch ?? globalThis.fetch.bind(globalThis)
    this.headers = {
      Authorization: `Bearer ${apiKey}`,
      "User-Agent": `zoo-sdk-typescript/${VERSION}`,
    }
    const workspace = options.workspace ?? env("ZOO_WORKSPACE")
    if (workspace) this.headers["X-Zoo-Workspace"] = workspace
  }

  /** Sends a request to /v1`path`, retrying when safe; throws a ZooError subclass for an error response. */
  async request(
    method: string,
    path: string,
    options: RequestOptions = {}
  ): Promise<Response> {
    const url = new URL(`${this.baseUrl}${PREFIX}${path}`)
    for (const [key, value] of Object.entries(options.query ?? {})) {
      if (value === undefined || value === null) continue
      for (const item of Array.isArray(value) ? value : [value])
        url.searchParams.append(key, String(item))
    }
    const headers: Record<string, string> = { ...this.headers, ...options.headers }
    if (method === "POST" && CREATES.test(path) && !headers["Idempotency-Key"])
      headers["Idempotency-Key"] = uuid()
    let body = options.body
    if (options.json !== undefined) {
      headers["Content-Type"] = "application/json"
      body = JSON.stringify(options.json)
    }
    const safe = IDEMPOTENT.has(method) || "Idempotency-Key" in headers
    const timeout = options.timeout === undefined ? this.timeout : options.timeout

    for (let attempt = 0; ; attempt++) {
      const signals = [options.signal, timeout ? AbortSignal.timeout(timeout) : undefined]
      const signal = AbortSignal.any(signals.filter((s): s is AbortSignal => !!s))
      let response: Response | undefined
      let retry = false
      try {
        response = await this.fetcher(url, { method, headers, body, signal })
      } catch (error) {
        if (options.signal?.aborted) throw error
        const timedOut = error instanceof DOMException && error.name === "TimeoutError"
        // a timed-out request may have run; only repeat it when that's harmless
        retry = !timedOut || safe
        if (attempt >= this.maxRetries || !retry) {
          throw timedOut
            ? new APITimeoutError(`${method} ${path} timed out`, { cause: error })
            : new APIConnectionError(`couldn't reach ${this.baseUrl}`, { cause: error })
        }
      }
      if (response) {
        if (response.ok) return response
        retry =
          response.status === 429 ||
          (safe && [500, 502, 503, 504].includes(response.status))
        if (attempt >= this.maxRetries || !retry) {
          const text = await response.text()
          let parsed: unknown = null
          try {
            parsed = JSON.parse(text)
          } catch {
            // not JSON: a proxy in front of Zoo
          }
          throw errorFrom(response.status, parsed, text.slice(0, 500))
        }
        await response.body?.cancel()
      }
      await sleep(backoff(attempt, response))
    }
  }

  async json<T>(method: string, path: string, options?: RequestOptions): Promise<T> {
    return (await (await this.request(method, path, options)).json()) as T
  }

  async *stream(
    method: string,
    path: string,
    options: RequestOptions = {}
  ): AsyncGenerator<[string, unknown]> {
    const response = await this.request(method, path, { ...options, timeout: null })
    if (!response.body) return
    yield* events(response.body)
  }

  /** Every sandbox in the workspace, newest first, following pages. */
  async *iterSandboxes(labels: Record<string, string> = {}): AsyncGenerator<Sandbox> {
    const label = Object.entries(labels).map(([k, v]) => `${k}=${v}`)
    let cursor: string | null = null
    do {
      const page: { data: SandboxData[]; next_cursor: string | null } = await this.json(
        "GET",
        "/sandboxes",
        { query: { label, limit: 100, cursor } }
      )
      for (const data of page.data) yield new Sandbox(this, data)
      cursor = page.next_cursor
    } while (cursor)
  }

  async sandboxes(labels: Record<string, string> = {}): Promise<Sandbox[]> {
    const all: Sandbox[] = []
    for await (const box of this.iterSandboxes(labels)) all.push(box)
    return all
  }

  async get(id: string): Promise<Sandbox> {
    return new Sandbox(this, await this.json("GET", `/sandboxes/${id}`))
  }

  /** `sandbox(id)` is an existing sandbox; `sandbox({ kind })` creates one, deleted by `await using`. */
  async sandbox(idOrOptions: string | CreateSandboxOptions = {}): Promise<Sandbox> {
    return typeof idOrOptions === "string"
      ? this.get(idOrOptions)
      : this.create(idOrOptions)
  }

  /** Creates a sandbox and, by default, waits until it runs; if it fails to start it is deleted and the error thrown. */
  async create(options: CreateSandboxOptions = {}): Promise<Sandbox> {
    const data = await this.json<SandboxData>("POST", "/sandboxes", {
      json: {
        kind: options.kind ?? "desktop",
        name: options.name ?? null,
        labels: options.labels ?? {},
        server_id: options.serverId ?? null,
        profile_ids: options.profileIds ?? [],
      },
    })
    const box = new Sandbox(this, data, true)
    if (options.wait === false) return box
    try {
      return await box.wait(options.waitTimeout ?? 180000)
    } catch (error) {
      await box.delete({ wait: false }).catch(() => undefined)
      throw error
    }
  }

  /** Runs `fn` with a new sandbox and deletes it afterwards, also when `fn` throws. */
  async withSandbox<T>(
    options: CreateSandboxOptions,
    fn: (box: Sandbox) => Promise<T>
  ): Promise<T> {
    const box = await this.create(options)
    try {
      return await fn(box)
    } finally {
      await box.delete({ wait: false }).catch(() => undefined)
    }
  }

  /** Follows the workspace's events (sandbox.state, run.*, takeover.*) from after `after` or from now. */
  async *events(
    options: { after?: number; sandboxId?: string; type?: string; signal?: AbortSignal } = {}
  ): AsyncGenerator<Record<string, unknown>> {
    for await (const [, data] of this.stream("GET", "/events/stream", {
      query: { after: options.after, sandbox_id: options.sandboxId, type: options.type },
      signal: options.signal,
    }))
      yield data as Record<string, unknown>
  }

  async tools(): Promise<unknown[]> {
    return this.json("GET", "/tools")
  }
}

/** One sandbox. With `await using`, a sandbox this client created is deleted when the scope ends. */
export class Sandbox implements AsyncDisposable {
  constructor(
    readonly client: Zoo,
    public data: SandboxData,
    readonly owned = false
  ) {}

  get id() {
    return this.data.id
  }
  get name() {
    return this.data.name
  }
  get kind() {
    return this.data.kind
  }
  get status() {
    return this.data.status
  }
  get labels(): Record<string, string> {
    return (this.data as { labels?: Record<string, string> }).labels ?? {}
  }

  async [Symbol.asyncDispose]() {
    if (this.owned) await this.delete({ wait: false })
  }

  private path(rest = "") {
    return `/sandboxes/${this.id}${rest}`
  }

  async refresh(): Promise<this> {
    this.data = await this.client.json("GET", this.path())
    return this
  }

  /** Waits until the sandbox runs; throws if it fails or takes longer than `timeout` ms. */
  async wait(timeout = 120000): Promise<this> {
    const deadline = Date.now() + timeout
    while (SETTLING.has((await this.refresh()).status)) {
      if (Date.now() > deadline)
        throw new ZooError(`sandbox ${this.id} not ready after ${timeout}ms`)
      await sleep(1000)
    }
    if (this.status !== "running")
      throw new ZooError(`sandbox ${this.id} is ${this.status}: ${this.data.error_message}`)
    return this
  }

  /** Calls a tool by name (`click`, `type_text`, `screenshot`, ...; `zoo.tools()` lists them). */
  async tool<T = unknown>(name: string, args: Record<string, unknown> = {}, timeout?: number): Promise<T> {
    return this.client.json("POST", this.path(`/tools/${name}`), { json: args, timeout })
  }

  /** Runs a shell command (at most `timeout` seconds) and returns its exit code and output. */
  async exec(command: string, timeout = 30): Promise<ExecResult> {
    return this.tool("execute_command", { command, timeout }, (timeout + 30) * 1000)
  }

  /** Runs a shell command and yields its output as it arrives, ending with an `exit` chunk. */
  async *execStream(command: string, timeout = 30): AsyncGenerator<ExecChunk> {
    for await (const [event, data] of this.client.stream("POST", this.path("/exec/stream"), {
      json: { command, timeout },
    })) {
      const payload = data as { data?: string; exit_code?: number; message?: string }
      if (event === "exit") {
        const exitCode = payload.exit_code ?? -1
        yield { stream: "exit", exitCode, timedOut: exitCode === 124 }
      } else if (event === "error") {
        throw new ZooError(payload.message ?? "exec failed")
      } else if (event === "stdout" || event === "stderr") {
        yield { stream: event, data: payload.data ?? "" }
      }
    }
  }

  async screenshot(): Promise<Uint8Array> {
    const data = await this.tool<string>("screenshot")
    return Uint8Array.from(atob(data), (c) => c.charCodeAt(0))
  }

  async readFile(path: string): Promise<Uint8Array> {
    const response = await this.client.request("GET", this.path("/files/content"), {
      query: { path },
      timeout: 300000,
    })
    return new Uint8Array(await response.arrayBuffer())
  }

  async writeFile(path: string, data: Uint8Array | string): Promise<unknown> {
    return this.client.json("PUT", this.path("/files/content"), {
      query: { path },
      body: typeof data === "string" ? data : new Blob([new Uint8Array(data)]),
      timeout: 300000,
    })
  }

  /** Runs the server-side computer-use agent and yields its events until it finishes. */
  async *ask(
    message: string,
    options: { model?: string; provider?: string; signal?: AbortSignal } = {}
  ): AsyncGenerator<Record<string, unknown>> {
    for await (const [, data] of this.client.stream("POST", this.path("/agent"), {
      json: { message, model: options.model, provider: options.provider, stream: true },
      signal: options.signal,
    }))
      yield data as Record<string, unknown>
  }

  async stopAgent() {
    await this.client.request("POST", this.path("/agent/stop"))
  }

  async takeover() {
    return this.client.json("POST", this.path("/agent/takeover"))
  }

  async release() {
    return this.client.json("POST", this.path("/agent/release"))
  }

  /** A link to the live screen for someone without a Zoo account; `expiresIn` is in minutes. */
  async viewerLink(
    options: { mode?: "view" | "control"; expiresIn?: number; endUser?: string } = {}
  ): Promise<{ id: string; url: string; expires_at: string }> {
    return this.client.json("POST", this.path("/viewer-links"), {
      json: {
        mode: options.mode ?? "view",
        expires_in_minutes: options.expiresIn ?? 60,
        end_user: options.endUser ?? null,
      },
    })
  }

  async setLabels(labels: Record<string, string>): Promise<this> {
    this.data = await this.client.json("PATCH", this.path(), { json: { labels } })
    return this
  }

  async start(wait = true): Promise<this> {
    this.data = await this.client.json("POST", this.path("/start"))
    return wait ? this.wait() : this
  }

  async stop(): Promise<this> {
    this.data = await this.client.json("POST", this.path("/stop"))
    return this
  }

  /** Deletes the sandbox; by default waits until it is gone. */
  async delete(options: { wait?: boolean; timeout?: number } = {}): Promise<void> {
    try {
      await this.client.request("DELETE", this.path())
    } catch (error) {
      if (error instanceof NotFoundError) return
      throw error
    }
    if (options.wait === false) return
    const deadline = Date.now() + (options.timeout ?? 120000)
    for (;;) {
      try {
        await this.refresh()
      } catch (error) {
        if (error instanceof NotFoundError) return
        throw error
      }
      if (Date.now() > deadline)
        throw new ZooError(`sandbox ${this.id} wasn't deleted in time`)
      await sleep(1000)
    }
  }
}
