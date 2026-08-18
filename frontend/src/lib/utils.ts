import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Bytes → a size an engineer would write, not a marketing one. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return "—"
  if (bytes < 1024) return `${bytes} B`
  const units = ["KB", "MB", "GB", "TB"]
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit++
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`
}

export function formatNumber(n: number | null | undefined, digits = 0): string {
  if (n == null || Number.isNaN(n)) return "—"
  return n.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function formatMs(ms: number | null | undefined): string {
  if (ms == null) return "—"
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`
}

export function formatPercent(value: number | null | undefined, digits = 0): string {
  if (value == null || Number.isNaN(value)) return "—"
  return `${(value * 100).toFixed(digits)}%`
}

/** Compact relative time. Absolute dates stay available in a tooltip. */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—"
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return "—"
  const seconds = Math.round((Date.now() - then) / 1000)
  if (seconds < 45) return "just now"
  const table: [number, string][] = [
    [60, "s"],
    [3600, "m"],
    [86400, "h"],
    [604800, "d"],
    [2629800, "w"],
    [31557600, "mo"],
  ]
  if (seconds < 60) return `${seconds}s ago`
  for (let i = 1; i < table.length; i++) {
    if (seconds < table[i][0]) {
      return `${Math.floor(seconds / table[i - 1][0])}${table[i - 1][1]} ago`
    }
  }
  return `${Math.floor(seconds / 31557600)}y ago`
}

export function formatDate(iso: string | null | undefined, withTime = false): string {
  if (!iso) return "—"
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return "—"
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  })
}

/** First letters of a name, for avatars. Two at most. */
/**
 * Two letters for an avatar fallback.
 *
 * Accepts null because the names it is given are nullable at the source:
 * `users.display_name` is a nullable column, and both the member and user
 * payloads carry it through as `null`. Typed as non-null here once, this threw
 * `Cannot read properties of null (reading 'split')` and — with no error
 * boundary at the time — blanked the whole app on the access page.
 *
 * An avatar with no name should be a blank circle. It should never be the
 * reason a page does not render.
 */
export function initials(name: string | null | undefined): string {
  return (name ?? "")
    .split(/[\s@._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("")
}

/** Stable pseudo-random in [0,1) from a string — keeps sample data fixed
 *  across reloads so a screenshot is reproducible. */
export function hashUnit(seed: string): number {
  let h = 2166136261
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return ((h >>> 0) % 100000) / 100000
}

export function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max - 1).trimEnd()}…`
}

/** Short form of a UUID for display; the full value stays in the title. */
export function shortId(id: string, len = 8): string {
  return id.replace(/-/g, "").slice(0, len)
}

export function pluralize(n: number, one: string, many = `${one}s`): string {
  return n === 1 ? one : many
}
