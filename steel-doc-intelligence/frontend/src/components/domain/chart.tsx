/**
 * Chart chrome.
 *
 * Recharts defaults are a different design system — grey grids at full
 * opacity, white tooltips, 12px sans labels. These props pull every chart onto
 * the app's tokens so they read as part of the page rather than as embedded
 * widgets, and so they follow the theme without a second palette.
 */
import * as React from "react"
import { cn } from "@/lib/utils"
import { Skeleton } from "@/components/ui/misc"
import { Card } from "@/components/ui/card"

export const chartGrid = {
  strokeDasharray: "2 4",
  stroke: "var(--border)",
  vertical: false,
} as const

export const chartAxis = {
  stroke: "var(--muted-foreground)",
  fontSize: 10,
  tickLine: false,
  axisLine: false,
  tickMargin: 6,
  style: { fontFamily: "var(--font-mono)", fontVariantNumeric: "tabular-nums" },
} as const

export const chartTooltip = {
  cursor: { fill: "color-mix(in oklab, var(--muted-foreground) 10%, transparent)" },
  contentStyle: {
    background: "var(--popover)",
    border: "1px solid var(--border)",
    borderRadius: "6px",
    fontSize: "12px",
    padding: "6px 10px",
    boxShadow: "0 8px 24px rgb(0 0 0 / 0.18)",
    color: "var(--popover-foreground)",
  },
  labelStyle: {
    color: "var(--muted-foreground)",
    fontSize: "10px",
    textTransform: "uppercase" as const,
    letterSpacing: "0.06em",
    marginBottom: "2px",
  },
  itemStyle: { padding: "1px 0", fontVariantNumeric: "tabular-nums" as const },
} as const

export function ChartFrame({
  title,
  description,
  action,
  children,
  height = 200,
  loading,
  legend,
  className,
}: {
  title: React.ReactNode
  description?: React.ReactNode
  action?: React.ReactNode
  children: React.ReactNode
  height?: number
  loading?: boolean
  legend?: { label: string; color: string }[]
  className?: string
}) {
  return (
    <Card className={cn("overflow-hidden", className)}>
      <div className="flex flex-wrap items-start justify-between gap-2 px-4 pb-2 pt-3.5">
        <div className="min-w-0 space-y-0.5">
          <h3 className="text-sm font-semibold tracking-tight">{title}</h3>
          {description && (
            <p className="text-pretty text-xs leading-relaxed text-muted-foreground">
              {description}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-3">
          {legend && (
            <div className="flex items-center gap-3">
              {legend.map((item) => (
                <span
                  key={item.label}
                  className="flex items-center gap-1.5 text-[0.6875rem] text-muted-foreground"
                >
                  <span
                    className="size-2 rounded-[2px]"
                    style={{ background: item.color }}
                    aria-hidden
                  />
                  {item.label}
                </span>
              ))}
            </div>
          )}
          {action}
        </div>
      </div>
      <div className="px-2 pb-2" style={{ height }}>
        {loading ? (
          <div className="flex h-full items-end gap-1.5 px-3 pb-3">
            {Array.from({ length: 14 }).map((_, i) => (
              <Skeleton
                key={i}
                className="flex-1"
                style={{ height: `${28 + ((i * 37) % 62)}%` }}
              />
            ))}
          </div>
        ) : (
          children
        )}
      </div>
    </Card>
  )
}

/** A horizontal bar for a metric measured against a floor or a target. */
export function MetricBar({
  label,
  value,
  floor,
  format = (v: number) => v.toFixed(3),
  className,
}: {
  label: string
  value: number | null | undefined
  floor?: number | null
  format?: (value: number) => string
  className?: string
}) {
  const has = value != null
  const pass = !has || floor == null || value >= floor
  return (
    <div className={cn("space-y-1.5", className)}>
      <div className="flex items-baseline justify-between gap-3">
        <span className="truncate text-[0.8125rem] capitalize">{label.replace(/_/g, " ")}</span>
        <span
          className={cn(
            "font-mono text-[0.8125rem] tabular-nums",
            !pass && "font-semibold text-error",
          )}
        >
          {has ? format(value) : "—"}
          {floor != null && (
            <span className="ml-1.5 text-[0.6875rem] text-muted-foreground">
              floor {floor.toFixed(2)}
            </span>
          )}
        </span>
      </div>
      <div className="relative h-1.5 overflow-hidden rounded-full bg-muted">
        {has && (
          <div
            className={cn("absolute inset-y-0 left-0 rounded-full", pass ? "bg-ok" : "bg-error")}
            style={{ width: `${Math.min(100, Math.max(2, value * 100))}%` }}
          />
        )}
        {floor != null && (
          <div
            className="absolute inset-y-[-2px] w-px bg-foreground/60"
            style={{ left: `${floor * 100}%` }}
            aria-hidden
          />
        )}
      </div>
    </div>
  )
}
