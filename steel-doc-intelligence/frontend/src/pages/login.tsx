import * as React from "react"
import { ArrowRightIcon, LoaderIcon, TriangleAlertIcon } from "lucide-react"
import { useAuth } from "@/lib/auth"
import { USE_MOCKS } from "@/api"
import { USERS } from "@/api/mock/corpus"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label, Separator } from "@/components/ui/misc"
import { GirderMark } from "@/components/layout/brand"
import { RoleBadge, SensitivityBadge } from "@/components/domain/badges"

/**
 * Sign-in.
 *
 * Says plainly which of the two identity modes is in force, because "signed
 * in" and "authenticated" are different claims here and the difference decides
 * whether a restricted drawing is reachable at all.
 */
export default function LoginPage() {
  const { signIn, signInAs, loading } = useAuth()
  const [email, setEmail] = React.useState(USERS[0].email)
  const [password, setPassword] = React.useState("••••••••")
  const [error, setError] = React.useState<string | null>(null)

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    try {
      await signIn(email, password)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.")
    }
  }

  return (
    <div className="relative flex min-h-svh items-center justify-center overflow-hidden px-4 py-10">
      {/* Drafting-paper ground: the product's own visual language, not a
          stock gradient. */}
      <div className="bg-grid grid-fade pointer-events-none absolute inset-0" aria-hidden />
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.55]"
        style={{
          background:
            "radial-gradient(60% 50% at 50% 0%, color-mix(in oklab, var(--primary) 12%, transparent), transparent 70%)",
        }}
        aria-hidden
      />

      <div className="relative grid w-full max-w-4xl gap-8 lg:grid-cols-[1.05fr_1fr] lg:gap-12">
        {/* left: the pitch */}
        <div className="hidden flex-col justify-center gap-6 lg:flex">
          <div className="flex items-center gap-2.5">
            <span className="flex size-9 items-center justify-center rounded-md bg-primary text-primary-foreground">
              <GirderMark className="size-5" />
            </span>
            <div className="leading-tight">
              <p className="text-lg font-semibold tracking-tight">Girder</p>
              <p className="text-[0.6875rem] uppercase tracking-[0.11em] text-muted-foreground">
                Steel Document Intelligence
              </p>
            </div>
          </div>

          <h1 className="text-balance text-3xl font-semibold leading-[1.15] tracking-tight">
            Every drawing, specification and revision — answerable in one place.
          </h1>
          <p className="max-w-md text-pretty text-sm leading-relaxed text-muted-foreground">
            Ingests PDFs, DWG/DXF, scans and schedules. Recognises section designations, grades,
            bolt classes and piece marks. Answers with citations that point at a page and a region
            of a specific revision — never at a superseded sheet.
          </p>

          <dl className="grid max-w-md grid-cols-2 gap-x-6 gap-y-4 border-t border-border pt-6">
            {[
              ["Hybrid retrieval", "Qdrant vectors fused with Elasticsearch BM25, then reranked"],
              ["Revision-aware", "Rev A/B/C is one drawing; answers default to the current issue"],
              ["Provenance honest", "A CAD dimension and an OCR reading are never shown as equals"],
              ["Governed", "Four-level clearance enforced before retrieval, not after"],
            ].map(([term, detail]) => (
              <div key={term}>
                <dt className="text-[0.8125rem] font-medium">{term}</dt>
                <dd className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{detail}</dd>
              </div>
            ))}
          </dl>
        </div>

        {/* right: the form */}
        <div className="corner-ticks rounded-lg border border-border bg-card p-6 shadow-xl shadow-black/5">
          <div className="mb-5 space-y-1 lg:hidden">
            <span className="flex size-8 items-center justify-center rounded-md bg-primary text-primary-foreground">
              <GirderMark className="size-4" />
            </span>
          </div>
          <div className="mb-5 space-y-1">
            <h2 className="text-base font-semibold tracking-tight">Sign in</h2>
            <p className="text-xs text-muted-foreground">
              Your role and clearance are read from the server, never from this form.
            </p>
          </div>

          <form onSubmit={onSubmit} className="space-y-3.5">
            <div className="space-y-1.5">
              <Label htmlFor="email">Work email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@girder.works"
                required
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>

            {error && (
              <p className="flex items-start gap-1.5 rounded-md border border-error/30 bg-error-soft/50 px-2.5 py-2 text-xs text-error">
                <TriangleAlertIcon className="mt-px size-3.5 shrink-0" />
                {error}
              </p>
            )}

            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? <LoaderIcon className="animate-spin" /> : <ArrowRightIcon />}
              Sign in
            </Button>
          </form>

          {USE_MOCKS && (
            <>
              <div className="my-5 flex items-center gap-3">
                <Separator className="flex-1" />
                <span className="eyebrow">or continue as</span>
                <Separator className="flex-1" />
              </div>

              <div className="space-y-1.5">
                {USERS.map((user) => (
                  <button
                    key={user.user_id}
                    onClick={() => signInAs(user.user_id)}
                    disabled={loading}
                    className="flex w-full items-center gap-3 rounded-md border border-border bg-card px-2.5 py-2 text-left transition-colors hover:border-primary/40 hover:bg-muted/50 disabled:opacity-60"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[0.8125rem] font-medium">
                        {user.display_name}
                      </span>
                      <span className="block truncate text-[0.6875rem] text-muted-foreground">
                        {user.email}
                      </span>
                    </span>
                    <span className="flex shrink-0 items-center gap-1">
                      <RoleBadge role={user.role} />
                      <SensitivityBadge value={user.clearance} withIcon={false} size="sm" />
                    </span>
                  </button>
                ))}
              </div>

              <p className="mt-4 flex items-start gap-1.5 rounded-md border border-warn/30 bg-warn-soft/40 px-2.5 py-2 text-[0.6875rem] leading-relaxed text-warn">
                <TriangleAlertIcon className="mt-px size-3 shrink-0" />
                <span>
                  These shortcuts use the <code className="font-mono">X-User-Id</code> development
                  header, which proves nothing. A deployment reachable by anyone untrusted must run
                  with <code className="font-mono">FIREBASE_ENABLED=true</code>, where this path is
                  refused outright.
                </span>
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
