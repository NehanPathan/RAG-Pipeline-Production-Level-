/**
 * HTTP transport.
 *
 * One place decides whether a call goes to the real FastAPI service or to the
 * in-memory sample corpus, so no page or hook ever knows the difference. Flip
 * with `VITE_USE_MOCKS=false` once the backend is reachable.
 *
 * Two auth modes, matching `resolve_principal()` on the server:
 *   - verified   → `Authorization: Bearer <firebase id token>`
 *   - developer  → `X-User-Id: <uuid>`, which proves nothing and is refused
 *                  by any deployment with FIREBASE_ENABLED=true.
 */

export const API_BASE = import.meta.env.VITE_API_BASE ?? "/api/v1"
export const USE_MOCKS = import.meta.env.VITE_USE_MOCKS !== "false"

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly traceId?: string,
    readonly body?: unknown,
  ) {
    super(detail)
    this.name = "ApiError"
  }

  /** 404 on a document is how the API says "not yours" (see
   *  `_load_readable_document`), so the UI must not promise it is missing. */
  get isAccessOrMissing() {
    return this.status === 404
  }
  get isForbidden() {
    return this.status === 403
  }
  get isUnauthenticated() {
    return this.status === 401
  }
  get isRateLimited() {
    return this.status === 429
  }
  get isUnavailable() {
    return this.status === 503
  }
}

type Credentials =
  | { mode: "bearer"; token: string; userId: string }
  | { mode: "dev"; userId: string }
  | { mode: "anonymous" }

let credentials: Credentials = { mode: "anonymous" }

export function setCredentials(next: Credentials) {
  credentials = next
}

export function authHeaders(): Record<string, string> {
  if (credentials.mode === "bearer") {
    return { Authorization: `Bearer ${credentials.token}` }
  }
  if (credentials.mode === "dev") {
    return { "X-User-Id": credentials.userId }
  }
  return {}
}

async function parseError(response: Response): Promise<ApiError> {
  const traceId = response.headers.get("X-Trace-Id") ?? undefined
  let detail = response.statusText || `Request failed (${response.status})`
  let body: unknown
  try {
    body = await response.json()
    const payload = body as { detail?: unknown; message?: unknown }
    if (typeof payload?.detail === "string") detail = payload.detail
    else if (Array.isArray(payload?.detail)) {
      // FastAPI validation errors arrive as a list of {loc, msg}.
      detail = payload.detail
        .map((item: { loc?: unknown[]; msg?: string }) =>
          `${item.loc?.slice(1).join(".") ?? "field"}: ${item.msg ?? "invalid"}`,
        )
        .join("; ")
    } else if (typeof payload?.message === "string") detail = payload.message
  } catch {
    /* A non-JSON error body (a proxy 502, say) keeps the status text. */
  }
  return new ApiError(response.status, detail, traceId, body)
}

export interface RequestOptions extends Omit<RequestInit, "body"> {
  query?: Record<string, unknown>
  body?: unknown
  /** Skip JSON encoding — used by the multipart upload path. */
  raw?: boolean
}

export function buildUrl(path: string, query?: Record<string, unknown>): string {
  const url = `${API_BASE}${path}`
  if (!query) return url
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue
    if (Array.isArray(value)) value.forEach((v) => params.append(key, String(v)))
    else params.append(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${url}?${qs}` : url
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { query, body, raw, headers, ...rest } = options
  const response = await fetch(buildUrl(path, query), {
    ...rest,
    headers: {
      ...(raw ? {} : body !== undefined ? { "Content-Type": "application/json" } : {}),
      Accept: "application/json",
      ...authHeaders(),
      ...headers,
    },
    body: raw ? (body as BodyInit) : body !== undefined ? JSON.stringify(body) : undefined,
  })

  if (!response.ok) throw await parseError(response)
  if (response.status === 204) return undefined as T

  const contentType = response.headers.get("Content-Type") ?? ""
  if (!contentType.includes("application/json")) {
    return (await response.text()) as unknown as T
  }
  return (await response.json()) as T
}

/**
 * Server-sent events from POST /chat.
 *
 * Written against `fetch` rather than `EventSource` because the endpoint is a
 * POST and needs auth headers, neither of which EventSource supports. The
 * terminator is the literal `data: [DONE]` the route emits after the final
 * frame.
 */
export async function* streamSSE<T>(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<T, void, void> {
  const response = await fetch(buildUrl(path), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...authHeaders(),
    },
    body: JSON.stringify(body),
    signal,
  })

  if (!response.ok) throw await parseError(response)
  if (!response.body) throw new ApiError(500, "The server returned no response body.")

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // Frames are separated by a blank line; a partial frame stays buffered.
      const frames = buffer.split("\n\n")
      buffer = frames.pop() ?? ""

      for (const frame of frames) {
        const line = frame.split("\n").find((l) => l.startsWith("data:"))
        if (!line) continue
        const payload = line.slice(5).trim()
        if (payload === "[DONE]") return
        if (!payload) continue
        try {
          yield JSON.parse(payload) as T
        } catch {
          /* A frame we cannot parse is dropped rather than killing the
             stream — the user keeps the tokens that already arrived. */
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

/** The trace id for the most recent chat response, for feedback and support. */
export function traceIdFrom(response: Response): string | undefined {
  return response.headers.get("X-Trace-Id") ?? undefined
}
