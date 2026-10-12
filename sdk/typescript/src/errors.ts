/** An error from the Zoo API (with its stable `code`, `hint` and `docs_url`), or the SDK giving up on a request. */
export class ZooError extends Error {
  readonly status: number | null
  readonly code: string
  readonly hint: string | null
  readonly docsUrl: string | null
  readonly requestId: string | null

  constructor(
    message: string,
    options: {
      status?: number | null
      code?: string
      hint?: string | null
      docsUrl?: string | null
      requestId?: string | null
      cause?: unknown
    } = {}
  ) {
    super(options.status ? `${options.status}: ${message}` : message, {
      cause: options.cause,
    })
    this.name = new.target.name
    this.status = options.status ?? null
    this.code = options.code ?? "error"
    this.hint = options.hint ?? null
    this.docsUrl = options.docsUrl ?? null
    this.requestId = options.requestId ?? null
  }
}

/** 401: no or invalid API key. */
export class AuthenticationError extends ZooError {}
/** 403: the key's role or scope, or a sandbox policy, doesn't allow it. */
export class PermissionDeniedError extends ZooError {}
/** 404: no such sandbox (or other object) in this workspace. */
export class NotFoundError extends ZooError {}
/** 409: the state doesn't allow it, a quota is used up, or an idempotency key clashed. */
export class ConflictError extends ZooError {}
/** 422: a field didn't match the schema. */
export class ValidationError extends ZooError {}
/** 429: too many requests; retried automatically before this is thrown. */
export class RateLimitError extends ZooError {}
/** 5xx: Zoo, or a sandbox it talks to, failed. */
export class ServerError extends ZooError {}
/** The server couldn't be reached. */
export class APIConnectionError extends ZooError {}
/** The server didn't answer within the timeout. */
export class APITimeoutError extends ZooError {}

const BY_STATUS: Record<number, typeof ZooError> = {
  401: AuthenticationError,
  403: PermissionDeniedError,
  404: NotFoundError,
  409: ConflictError,
  422: ValidationError,
  429: RateLimitError,
}

type ErrorBody = {
  error?: {
    code?: string
    message?: string
    hint?: string | null
    docs_url?: string
    request_id?: string
  }
  detail?: unknown
}

export function errorFrom(status: number, body: unknown, text = ""): ZooError {
  const parsed = (body && typeof body === "object" ? body : {}) as ErrorBody
  const error = parsed.error ?? {
    message: String(parsed.detail ?? text ?? `request failed (${status})`),
  }
  const Kind = BY_STATUS[status] ?? (status >= 500 ? ServerError : ZooError)
  return new Kind(error.message || `request failed (${status})`, {
    status,
    code: error.code,
    hint: error.hint,
    docsUrl: error.docs_url,
    requestId: error.request_id,
  })
}
