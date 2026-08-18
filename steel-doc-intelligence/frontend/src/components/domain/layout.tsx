/**
 * Page furniture: headers, metric tiles, and the title-block metadata grid
 * that stands in for a drawing's own title block.
 */
import * as React from "react"
import { Link } from "react-router-dom"
import { ChevronRightIcon, TrendingDownIcon, TrendingUpIcon } from "lucide-react"
import { cn, formatNumber } from "@/lib/utils"
import { Hint } from "@/components/ui/tooltip"

/* ---------------------------------------------------------- Page header */

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  meta,
  className,
  children,
}: {
  eyebrow?: React.ReactNode
  title: React.ReactNode
  description?: React.ReactNode
  actions?: React.ReactNode
  meta?: React.ReactNode
  className?: string
  children?: React.ReactNode
}) {
  return (
    <header className={cn("space-y-3 pb-1", className)}>
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 space-y-1.5">
          {eyebrow && <div className="eyebrow">{eyebrow}</div>}
          <h1 className="text-balance text-xl font-semibold leading-tight tracking-tight">
            {title}
          </h1>
          {description && (
            <p className="max-w-2xl text-pretty text-sm leading-relaxed text-muted-foreground">
              {description}
            </p>
          )}
          {meta && <div className="flex flex-wrap items-center gap-2 pt-0.5">{meta}</div>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children}
    </header>
  )
}

export function Breadcrumbs({
  items,
}: {
  items: { label: string; to?: string }[]
}) {
  return (
    <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1 text-xs">
      {items.map((item, i) => (
        <React.Fragment key={`${item.label}-${i}`}>
          {i > 0 && <ChevronRightIcon className="size-3 shrink-0 text-muted-foreground/50" />}
          {item.to ? (
            <Link
              to={item.to}
              className="truncate text-muted-foreground transition-colors hover:text-foreground"
            >
              {item.label}
            </Link>
          ) : (
            <span className="truncate font-medium text-foreground">{item.label}</span>
          )}
        </React.Fragment>
      ))}
    </nav>
  )
}

/* ------------------------------------------------------------- Sections */

export function Section({
  title,
  description,
  actions,
  className,
  children,
  dense = false,
}: {
  title?: React.ReactNode
  description?: React.ReactNode
  actions?: React.ReactNode
  className?: string
  children: React.ReactNode
  dense?: boolean
}) {
  return (
    <section className={cn(dense ? "space-y-2" : "space-y-3", className)}>
      {(title || actions) && (
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div className="space-y-0.5">
            {title && <h2 className="text-sm font-semibold tracking-tight">{title}</h2>}
            {description && (
              <p className="text-xs leading-relaxed text-muted-foreground">{description}</p>
            )}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  )
}

/* ----------------------------------------------------------- Stat tiles */

export function StatTile({
  label,
  value,
  unit,
  hint,
  delta,
  deltaLabel,
  tone = "default",
  invertDelta = false,
  icon: Icon,
  footer,
  to,
  className,
}: {
  label: string
  value: React.ReactNode
  unit?: string
  hint?: React.ReactNode
  delta?: number | null
  deltaLabel?: string
  tone?: "default" | "ok" | "warn" | "error" | "technical"
  /** For metrics where down is good — latency, failures, cost. */
  invertDelta?: boolean
  icon?: React.ElementType
  footer?: React.ReactNode
  to?: string
  className?: string
}) {
  const toneRing = {
    default: "",
    ok: "border-ok/30",
    warn: "border-warn/35",
    error: "border-error/35",
    technical: "border-blueprint/30",
  }[tone]
  const valueTone = {
    default: "",
    ok: "text-ok",
    warn: "text-warn",
    error: "text-error",
    technical: "text-blueprint",
  }[tone]

  const good = delta == null ? null : invertDelta ? delta <= 0 : delta >= 0
  const DeltaIcon = (delta ?? 0) >= 0 ? TrendingUpIcon : TrendingDownIcon

  const body = (
    <>
      <div className="flex items-start justify-between gap-2">
        <span className="eyebrow">{label}</span>
        {Icon && <Icon className="size-3.5 shrink-0 text-muted-foreground/70" />}
      </div>
      <div className="flex items-baseline gap-1.5">
        <span
          data-slot="metric"
          className={cn("text-2xl font-semibold leading-none tracking-tight", valueTone)}
        >
          {value}
        </span>
        {unit && <span className="text-xs font-medium text-muted-foreground">{unit}</span>}
        {delta != null && (
          <span
            className={cn(
              "ml-auto inline-flex items-center gap-0.5 font-mono text-[0.6875rem] tabular-nums",
              good ? "text-ok" : "text-error",
            )}
          >
            <DeltaIcon className="size-3" />
            {delta > 0 ? "+" : ""}
            {formatNumber(delta, Math.abs(delta) < 10 ? 1 : 0)}
            {deltaLabel && <span className="text-muted-foreground"> {deltaLabel}</span>}
          </span>
        )}
      </div>
      {footer && <div className="text-[0.6875rem] leading-relaxed text-muted-foreground">{footer}</div>}
    </>
  )

  const shell = cn(
    "flex flex-col gap-2 rounded-lg border bg-card p-3.5 transition-colors",
    toneRing,
    to && "hover:border-primary/40 hover:bg-muted/40",
    className,
  )

  const tile = to ? (
    <Link to={to} className={shell}>
      {body}
    </Link>
  ) : (
    <div className={shell}>{body}</div>
  )

  return hint ? <Hint label={hint}>{tile}</Hint> : tile
}

/* ------------------------------------------------- Title-block metadata */

/**
 * A drawing's title block is a fixed grid of labelled cells in the corner of
 * the sheet. Document metadata is rendered the same way here: it is the
 * convention the audience already reads, and it packs more facts into less
 * space than a stack of label/value rows.
 */
export function TitleBlock({
  fields,
  columns = 3,
  className,
}: {
  fields: { label: string; value: React.ReactNode; span?: number; mono?: boolean }[]
  columns?: number
  className?: string
}) {
  return (
    <dl
      className={cn(
        "corner-ticks grid overflow-hidden rounded-md border border-border bg-card",
        className,
      )}
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
    >
      {fields.map((field, i) => (
        <div
          key={`${field.label}-${i}`}
          className="min-w-0 border-b border-r border-border px-3 py-2 last:border-r-0"
          style={field.span ? { gridColumn: `span ${field.span} / span ${field.span}` } : undefined}
        >
          <dt className="eyebrow mb-1 truncate">{field.label}</dt>
          <dd
            className={cn(
              "truncate text-[0.8125rem] leading-tight",
              field.mono && "font-mono tabular-nums",
            )}
          >
            {field.value ?? <span className="text-muted-foreground">—</span>}
          </dd>
        </div>
      ))}
    </dl>
  )
}

/** Label/value pairs for a sidebar, where a grid would be too wide. */
export function DataList({
  items,
  className,
}: {
  items: { label: React.ReactNode; value: React.ReactNode; mono?: boolean }[]
  className?: string
}) {
  return (
    <dl className={cn("divide-y divide-border text-[0.8125rem]", className)}>
      {items.map((item, i) => (
        <div key={i} className="flex items-start justify-between gap-4 py-1.5">
          <dt className="shrink-0 text-muted-foreground">{item.label}</dt>
          <dd className={cn("min-w-0 text-right", item.mono && "font-mono tabular-nums")}>
            {item.value ?? "—"}
          </dd>
        </div>
      ))}
    </dl>
  )
}

/** Small count next to a tab or heading. */
export function CountPill({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-sm bg-muted px-1 py-px font-mono text-[0.625rem] tabular-nums text-muted-foreground">
      {children}
    </span>
  )
}
