/**
 * Empty, loading and error states.
 *
 * Treated as first-class screens rather than afterthoughts: a drawing register
 * is empty on day one, ingestion fails on real corpora, and a 404 from this
 * API often means "not cleared for it" rather than "gone" — each of which
 * needs a different sentence and a different next action.
 */
import * as React from "react"
import { Link } from "react-router-dom"
import {
  AlertTriangleIcon,
  ArrowRightIcon,
  BanIcon,
  CloudOffIcon,
  FileQuestionIcon,
  RefreshCwIcon,
  ShieldAlertIcon,
  TimerIcon,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/misc"
import { ApiError } from "@/api"

/* ----------------------------------------------------------- Empty state */

export function EmptyState({
  icon: Icon = FileQuestionIcon,
  title,
  description,
  action,
  secondaryAction,
  className,
  compact = false,
}: {
  icon?: React.ElementType
  title: string
  description?: React.ReactNode
  action?: React.ReactNode
  secondaryAction?: React.ReactNode
  className?: string
  compact?: boolean
}) {
  return (
    <div
      className={cn(
        "relative flex flex-col items-center justify-center overflow-hidden rounded-lg border border-dashed border-border text-center",
        compact ? "gap-2 px-6 py-8" : "gap-3 px-6 py-14",
        className,
      )}
    >
      <div className="bg-grid grid-fade pointer-events-none absolute inset-0 opacity-60" aria-hidden />
      <div className="relative flex size-10 items-center justify-center rounded-md border border-border bg-card">
        <Icon className="size-4.5 text-muted-foreground" />
      </div>
      <div className="relative space-y-1">
        <p className="text-sm font-semibold tracking-tight">{title}</p>
        {description && (
          <p className="mx-auto max-w-md text-pretty text-xs leading-relaxed text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      {(action || secondaryAction) && (
        <div className="relative mt-1 flex flex-wrap items-center justify-center gap-2">
          {action}
          {secondaryAction}
        </div>
      )}
    </div>
  )
}

/* ----------------------------------------------------------- Error state */

/**
 * The 404 case deserves particular care. `_load_readable_document` returns 404
 * rather than 403 for a document above the caller's clearance, on purpose — a
 * 403 would confirm the document exists. So the UI must not claim it is
 * missing; it says both possibilities and lets the reader act on either.
 */
export function ErrorState({
  error,
  onRetry,
  resource = "this resource",
  className,
  compact = false,
}: {
  error: unknown
  onRetry?: () => void
  resource?: string
  className?: string
  compact?: boolean
}) {
  const api = error instanceof ApiError ? error : null
  const status = api?.status

  let icon: React.ElementType = AlertTriangleIcon
  let title = "Something went wrong"
  let description: React.ReactNode =
    error instanceof Error ? error.message : "An unexpected error occurred."
  let tone: "error" | "warn" | "idle" = "error"

  if (status === 404) {
    icon = ShieldAlertIcon
    tone = "warn"
    title = `No access to ${resource}`
    description = (
      <>
        It either does not exist, or your clearance does not cover it. The API deliberately does
        not distinguish the two — confirming that a restricted document exists is itself a
        disclosure. Ask a steward if you believe you should have access.
      </>
    )
  } else if (status === 403) {
    icon = BanIcon
    tone = "warn"
    title = "Your role does not permit this"
    description = api?.detail ?? "This action requires a steward or admin role."
  } else if (status === 401) {
    icon = ShieldAlertIcon
    title = "Session expired"
    description = "Sign in again to continue."
  } else if (status === 429) {
    icon = TimerIcon
    tone = "warn"
    title = "Rate limit reached"
    description = api?.detail ?? "Too many requests. Wait a moment and try again."
  } else if (status === 503) {
    icon = CloudOffIcon
    tone = "warn"
    title = "Temporarily unavailable"
    description = api?.detail ?? "A dependency is down or an administrator has paused this."
  } else if (status === 501) {
    icon = CloudOffIcon
    tone = "idle"
    title = "Not wired up yet"
    description = api?.detail ?? "This endpoint does not exist on the backend."
  } else if (status === 0) {
    icon = CloudOffIcon
    title = "Cannot reach the API"
    description = "The request never left the browser or the service is not running."
  }

  const Icon = icon
  const toneClass = {
    error: "border-error/30 bg-error-soft/40 text-error",
    warn: "border-warn/30 bg-warn-soft/40 text-warn",
    idle: "border-border bg-muted text-muted-foreground",
  }[tone]

  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-lg border text-center",
        compact ? "px-5 py-6" : "px-6 py-12",
        toneClass,
        className,
      )}
    >
      <Icon className="size-5" />
      <div className="space-y-1">
        <p className="text-sm font-semibold tracking-tight">{title}</p>
        <p className="mx-auto max-w-md text-pretty text-xs leading-relaxed opacity-90">
          {description}
        </p>
        {api?.traceId && (
          <p className="pt-1 font-mono text-[0.625rem] opacity-70">trace {api.traceId}</p>
        )}
      </div>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCwIcon />
          Try again
        </Button>
      )}
    </div>
  )
}

/** Inline banner variant, for a panel inside an otherwise working page. */
export function InlineError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : "Request failed."
  return (
    <div className="flex items-start gap-2 rounded-md border border-error/30 bg-error-soft/50 px-3 py-2 text-xs text-error">
      <AlertTriangleIcon className="mt-px size-3.5 shrink-0" />
      <span className="flex-1 leading-relaxed">{message}</span>
      {onRetry && (
        <button onClick={onRetry} className="shrink-0 font-medium underline underline-offset-2">
          Retry
        </button>
      )}
    </div>
  )
}

/* --------------------------------------------------------- Loading state */

export function TableSkeleton({ rows = 8, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div className="w-full">
      <div className="flex gap-3 border-b border-border px-3 py-2">
        {Array.from({ length: cols }).map((_, i) => (
          <Skeleton key={i} className="h-2.5 flex-1" />
        ))}
      </div>
      {Array.from({ length: rows }).map((_, row) => (
        <div key={row} className="flex items-center gap-3 border-b border-border px-3 py-3">
          {Array.from({ length: cols }).map((_, col) => (
            <Skeleton
              key={col}
              className="h-3 flex-1"
              style={{ opacity: 1 - row * 0.07, maxWidth: col === 0 ? "none" : "60%" }}
            />
          ))}
        </div>
      ))}
    </div>
  )
}

export function CardGridSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="space-y-3 rounded-lg border border-border p-4">
          <div className="flex items-center justify-between">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-3.5 w-14 rounded-sm" />
          </div>
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-2.5 w-1/2" />
          <div className="flex gap-2 pt-1">
            <Skeleton className="h-3 w-12" />
            <Skeleton className="h-3 w-12" />
            <Skeleton className="h-3 w-12" />
          </div>
        </div>
      ))}
    </div>
  )
}

export function TileRowSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="space-y-2.5 rounded-lg border border-border p-4">
          <Skeleton className="h-2.5 w-20" />
          <Skeleton className="h-6 w-16" />
          <Skeleton className="h-2 w-24" />
        </div>
      ))}
    </div>
  )
}

export function TextSkeleton({ lines = 4 }: { lines?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} className="h-3" style={{ width: i === lines - 1 ? "62%" : "100%" }} />
      ))}
    </div>
  )
}

/* ------------------------------------------------------ Not-found screen */

export function NotFoundScreen() {
  return (
    <div className="relative flex min-h-[70vh] flex-col items-center justify-center gap-4 overflow-hidden text-center">
      <div className="bg-grid grid-fade pointer-events-none absolute inset-0" aria-hidden />
      <p className="relative font-mono text-6xl font-semibold tracking-tighter text-muted-foreground/50">
        404
      </p>
      <div className="relative space-y-1">
        <p className="text-base font-semibold tracking-tight">This sheet is not in the register</p>
        <p className="max-w-sm text-sm text-muted-foreground">
          The page you asked for does not exist. It may have been renamed, or the link may be
          from an older revision of the app.
        </p>
      </div>
      <Button asChild variant="outline" size="sm" className="relative">
        <Link to="/">
          Back to overview
          <ArrowRightIcon />
        </Link>
      </Button>
    </div>
  )
}
