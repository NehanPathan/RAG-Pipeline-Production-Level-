import * as React from "react"
import { api, USE_MOCKS } from "@/api"
import { setCredentials } from "@/api/client"
import { setMockPrincipal } from "@/api/mock/handlers"
import { USERS } from "@/api/mock/corpus"
import type { Principal, Role, Sensitivity } from "@/api/types"
import {
  type FirebaseSession,
  isExpired,
  refreshSession,
  signInWithPassword,
} from "@/lib/firebase"

/**
 * Session state.
 *
 * The backend resolves identity two ways (`resolve_principal`): a verified
 * Firebase ID token, or — with Firebase disabled — an `X-User-Id` header that
 * proves nothing. This mirrors both, and says which one is in force, because
 * "signed in" and "authenticated" are not the same claim and an engineer
 * looking at a restricted drawing deserves to know which one they have.
 */

const SESSION_KEY = "girder.session"

export interface Session {
  principal: Principal
  /** Present only in verified mode. */
  token?: string
  /** Firebase tokens, so a reload can refresh rather than force a re-login. */
  firebase?: FirebaseSession
}

interface AuthContextValue {
  session: Session | null
  principal: Principal | null
  loading: boolean
  signIn: (email: string, password: string) => Promise<void>
  signInAs: (userId: string) => Promise<void>
  signOut: () => void
  /** Platform role gate — mirrors `require_role`, an allow-list not a rank. */
  hasRole: (...roles: Role[]) => boolean
  /** Data clearance gate — `public < internal < confidential < restricted`. */
  canRead: (sensitivity: Sensitivity) => boolean
}

const CLEARANCE_LEVEL: Record<Sensitivity, number> = {
  public: 0,
  internal: 1,
  confidential: 2,
  restricted: 3,
}

const AuthContext = React.createContext<AuthContextValue | null>(null)

function applyCredentials(session: Session | null) {
  if (!session) {
    setCredentials({ mode: "anonymous" })
    return
  }
  if (session.token) {
    setCredentials({ mode: "bearer", token: session.token, userId: session.principal.user_id })
  } else {
    setCredentials({ mode: "dev", userId: session.principal.user_id })
  }
  if (USE_MOCKS) setMockPrincipal(session.principal)
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = React.useState<Session | null>(() => {
    try {
      const raw = localStorage.getItem(SESSION_KEY)
      if (!raw) return null
      const parsed = JSON.parse(raw) as Session
      applyCredentials(parsed)
      return parsed
    } catch {
      return null
    }
  })
  const [loading, setLoading] = React.useState(false)

  const persist = React.useCallback((next: Session | null) => {
    setSession(next)
    applyCredentials(next)
    if (next) localStorage.setItem(SESSION_KEY, JSON.stringify(next))
    else localStorage.removeItem(SESSION_KEY)
  }, [])

  const signIn = React.useCallback(
    async (email: string, password: string) => {
      setLoading(true)
      try {
        if (USE_MOCKS) {
          await new Promise((r) => setTimeout(r, 550))
          const user = USERS.find((u) => u.email.toLowerCase() === email.trim().toLowerCase())
          if (!user || password.length < 4) {
            throw new Error("That email and password combination was not recognised.")
          }
          persist({ principal: user, token: `mock.${user.user_id}` })
          return
        }
        // Real deployment: exchange credentials with Firebase, then let the
        // API resolve the local user and role from the verified token.
        //
        // The principal is never taken from Firebase. Firebase proves *who*
        // the caller is; role and clearance are read from our own database
        // by `resolve_principal`, so a token cannot assert privilege. That
        // is why this second call exists rather than decoding the JWT here.
        const firebase = await signInWithPassword(email, password)
        setCredentials({ mode: "bearer", token: firebase.idToken, userId: firebase.localId })
        const { principal } = await api.whoAmI()
        persist({ principal, token: firebase.idToken, firebase })
      } finally {
        setLoading(false)
      }
    },
    [persist],
  )

  /** Development identity switch — the `X-User-Id` path. */
  const signInAs = React.useCallback(
    async (userId: string) => {
      setLoading(true)
      try {
        if (USE_MOCKS) {
          const user = USERS.find((u) => u.user_id === userId) ?? USERS[0]
          await new Promise((r) => setTimeout(r, 260))
          persist({ principal: { ...user, auth_provider: "none", authenticated: false } })
          return
        }
        setCredentials({ mode: "dev", userId })
        const { principal } = await api.whoAmI()
        persist({ principal })
      } finally {
        setLoading(false)
      }
    },
    [persist],
  )

  const signOut = React.useCallback(() => persist(null), [persist])

  // Firebase ID tokens last an hour. A restored session whose token has
  // expired would otherwise keep sending it: the API rejects it with 401 and
  // the UI shows "signed in" over a wall of failed requests. Refreshing on
  // mount turns that into either a working session or a clean sign-out.
  React.useEffect(() => {
    if (USE_MOCKS || !session?.firebase || !isExpired(session.firebase)) return
    let cancelled = false
    void (async () => {
      try {
        const firebase = await refreshSession(session.firebase!.refreshToken)
        if (cancelled) return
        persist({ ...session, token: firebase.idToken, firebase })
      } catch {
        // The refresh token is revoked or expired: the session is genuinely
        // over, and saying so is better than failing every request quietly.
        if (!cancelled) persist(null)
      }
    })()
    return () => {
      cancelled = true
    }
    // Runs on mount and whenever a new session is stored.
  }, [session, persist])

  const value = React.useMemo<AuthContextValue>(
    () => ({
      session,
      principal: session?.principal ?? null,
      loading,
      signIn,
      signInAs,
      signOut,
      hasRole: (...roles) => Boolean(session && roles.includes(session.principal.role)),
      canRead: (sensitivity) =>
        Boolean(
          session &&
            CLEARANCE_LEVEL[sensitivity] <= CLEARANCE_LEVEL[session.principal.clearance],
        ),
    }),
    [session, loading, signIn, signInAs, signOut],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = React.useContext(AuthContext)
  if (!context) throw new Error("useAuth must be used within an AuthProvider")
  return context
}

export { CLEARANCE_LEVEL }
