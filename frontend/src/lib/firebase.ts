/**
 * Firebase sign-in over its REST API, without the web SDK.
 *
 * The SDK would add ~200 KB to wrap three HTTP calls we make once each, and
 * it manages a token lifecycle we do not want it to own -- the server is the
 * authority on identity, and the client's only job is to obtain an ID token
 * and hand it over. Doing it directly also keeps every reachable host named
 * in the Content-Security-Policy.
 *
 * The API key is not a secret. It identifies the project and is designed to
 * ship in client bundles; security comes from server-side token verification
 * and the email-domain allow-list.
 */

const IDENTITY = "https://identitytoolkit.googleapis.com/v1"
const SECURETOKEN = "https://securetoken.googleapis.com/v1"

export const FIREBASE_API_KEY: string = import.meta.env.VITE_FIREBASE_API_KEY ?? ""
export const FIREBASE_CONFIGURED = FIREBASE_API_KEY.length > 0

export interface FirebaseSession {
  idToken: string
  refreshToken: string
  /** Epoch milliseconds at which `idToken` stops being accepted. */
  expiresAt: number
  email: string
  localId: string
}

/** Errors Firebase returns that a person can actually act on. */
const READABLE: Record<string, string> = {
  EMAIL_NOT_FOUND: "No account exists for that email address.",
  INVALID_PASSWORD: "That email and password combination was not recognised.",
  INVALID_LOGIN_CREDENTIALS: "That email and password combination was not recognised.",
  USER_DISABLED: "That account has been disabled.",
  INVALID_EMAIL: "That does not look like an email address.",
  TOO_MANY_ATTEMPTS_TRY_LATER:
    "Too many failed attempts. Firebase has temporarily blocked sign-in from here.",
}

async function post<T>(url: string, body: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
  } catch {
    // A refused connection here is almost always the Content-Security-Policy
    // rather than the network, and the browser reports both identically.
    throw new Error(
      "Could not reach Firebase. If this is a fresh deployment, check that " +
        "identitytoolkit.googleapis.com is allowed by connect-src in the CSP.",
    )
  }

  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const code = String(payload?.error?.message ?? "").split(" ")[0]
    throw new Error(READABLE[code] ?? `Sign-in failed (${code || response.status}).`)
  }
  return payload as T
}

function sessionFrom(payload: {
  idToken: string
  refreshToken: string
  expiresIn: string
  email?: string
  localId?: string
}): FirebaseSession {
  return {
    idToken: payload.idToken,
    refreshToken: payload.refreshToken,
    // 60 seconds early, so a request started just before expiry does not
    // arrive just after it.
    expiresAt: Date.now() + (Number(payload.expiresIn) - 60) * 1000,
    email: payload.email ?? "",
    localId: payload.localId ?? "",
  }
}

export async function signInWithPassword(
  email: string,
  password: string,
): Promise<FirebaseSession> {
  if (!FIREBASE_CONFIGURED) {
    throw new Error(
      "Firebase is not configured for this build. Set VITE_FIREBASE_API_KEY " +
        "(see docker-compose.yml) and rebuild, or run with VITE_USE_MOCKS=true.",
    )
  }
  const payload = await post<Parameters<typeof sessionFrom>[0]>(
    `${IDENTITY}/accounts:signInWithPassword?key=${FIREBASE_API_KEY}`,
    { email: email.trim(), password, returnSecureToken: true },
  )
  return sessionFrom(payload)
}

export async function refreshSession(refreshToken: string): Promise<FirebaseSession> {
  const payload = await post<{
    id_token: string
    refresh_token: string
    expires_in: string
    user_id: string
  }>(`${SECURETOKEN}/token?key=${FIREBASE_API_KEY}`, {
    grant_type: "refresh_token",
    refresh_token: refreshToken,
  })
  return sessionFrom({
    idToken: payload.id_token,
    refreshToken: payload.refresh_token,
    expiresIn: payload.expires_in,
    localId: payload.user_id,
  })
}

export function isExpired(session: FirebaseSession): boolean {
  return Date.now() >= session.expiresAt
}
