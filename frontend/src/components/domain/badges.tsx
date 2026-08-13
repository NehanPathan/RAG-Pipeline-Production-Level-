/**
 * The product's vocabulary, as components.
 *
 * Each of these encodes one distinction the backend actually makes, and each
 * owns its colour exclusively — classification never borrows the status ramp,
 * provenance never borrows classification. A reader learns the mapping once.
 */
import * as React from "react"
import {
  AlertTriangleIcon,
  BoxIcon,
  CheckCircle2Icon,
  CircleDashedIcon,
  EyeOffIcon,
  FileTextIcon,
  LayersIcon,
  LoaderIcon,
  LockIcon,
  ScanLineIcon,
  ShieldIcon,
  SlashIcon,
  UsersIcon,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Hint } from "@/components/ui/tooltip"
import type {
  ContentKind,
  DocumentStatus,
  JobStatus,
  ProjectStatus,
  Role,
  Sensitivity,
} from "@/api/types"

/* ------------------------------------------------------- Classification */

const SENSITIVITY_META: Record<
  Sensitivity,
  { label: string; tone: string; bar: string; icon: React.ElementType; note: string }
> = {
  public: {
    label: "Public",
    tone: "text-sens-public border-sens-public/35 bg-sens-public/10",
    bar: "bg-sens-public",
    icon: UsersIcon,
    note: "Readable by anyone with an account, including site and client logins.",
  },
  internal: {
    label: "Internal",
    tone: "text-sens-internal border-sens-internal/35 bg-sens-internal/10",
    bar: "bg-sens-internal",
    icon: ShieldIcon,
    note: "Readable by analysts and above. The default for an unlabelled upload.",
  },
  confidential: {
    label: "Confidential",
    tone: "text-sens-confidential border-sens-confidential/40 bg-sens-confidential/10",
    bar: "bg-sens-confidential",
    icon: LockIcon,
    note: "Stewards and admins only. Client-commercial drawings sit here.",
  },
  restricted: {
    label: "Restricted",
    tone: "text-sens-restricted border-sens-restricted/40 bg-sens-restricted/12",
    bar: "bg-sens-restricted",
    icon: EyeOffIcon,
    note: "Admin clearance only. Never enters retrieval for a lower clearance.",
  },
}

export function SensitivityBadge({
  value,
  className,
  withIcon = true,
  size = "default",
}: {
  value: Sensitivity
  className?: string
  withIcon?: boolean
  size?: "default" | "sm"
}) {
  const meta = SENSITIVITY_META[value]
  const Icon = meta.icon
  return (
    <Hint label={<span><b>{meta.label}</b> — {meta.note}</span>}>
      <span
        className={cn(
          "inline-flex w-fit items-center gap-1 rounded-sm border px-1.5 py-0.5 font-medium leading-4",
          size === "sm" ? "text-[0.625rem]" : "text-[0.6875rem]",
          meta.tone,
          className,
        )}
      >
        {withIcon && <Icon className="size-3 shrink-0" />}
        {meta.label}
      </span>
    </Hint>
  )
}

/** The four-step ramp as a stepped bar — a glanceable level, not a word. */
export function SensitivityBar({ value, className }: { value: Sensitivity; className?: string }) {
  const order: Sensitivity[] = ["public", "internal", "confidential", "restricted"]
  const level = order.indexOf(value)
  return (
    <Hint label={`Classification: ${SENSITIVITY_META[value].label}`}>
      <span className={cn("inline-flex h-3 items-end gap-[2px]", className)} aria-hidden>
        {order.map((step, i) => (
          <span
            key={step}
            className={cn(
              "w-[3px] rounded-[1px]",
              i <= level ? SENSITIVITY_META[value].bar : "bg-border",
            )}
            style={{ height: `${5 + i * 2.5}px` }}
          />
        ))}
      </span>
    </Hint>
  )
}

export { SENSITIVITY_META }

/* ------------------------------------------------------ Document status */

const DOC_STATUS_META: Record<
  DocumentStatus,
  { label: string; variant: "ok" | "warn" | "error" | "idle" | "technical"; icon: React.ElementType }
> = {
  indexed: { label: "Indexed", variant: "ok", icon: CheckCircle2Icon },
  processing: { label: "Processing", variant: "technical", icon: LoaderIcon },
  pending: { label: "Queued", variant: "idle", icon: CircleDashedIcon },
  failed: { label: "Failed", variant: "error", icon: AlertTriangleIcon },
  deleted: { label: "Deleted", variant: "idle", icon: SlashIcon },
}

export function DocumentStatusBadge({
  status,
  className,
}: {
  status: DocumentStatus
  className?: string
}) {
  const meta = DOC_STATUS_META[status] ?? DOC_STATUS_META.pending
  const Icon = meta.icon
  return (
    <Badge variant={meta.variant} className={className}>
      <Icon className={cn("size-3", status === "processing" && "animate-spin")} />
      {meta.label}
    </Badge>
  )
}

const JOB_STATUS_META: Record<
  JobStatus,
  { label: string; variant: "ok" | "warn" | "error" | "idle" | "technical" }
> = {
  succeeded: { label: "Succeeded", variant: "ok" },
  running: { label: "Running", variant: "technical" },
  queued: { label: "Queued", variant: "idle" },
  failed: { label: "Failed", variant: "error" },
  skipped: { label: "Skipped", variant: "warn" },
}

export function JobStatusBadge({ status, className }: { status: JobStatus; className?: string }) {
  const meta = JOB_STATUS_META[status]
  return (
    <Badge variant={meta.variant} className={className}>
      {status === "running" && (
        <span className="relative flex size-1.5">
          <span className="absolute inline-flex size-full animate-pulse-ring rounded-full bg-current opacity-70" />
          <span className="relative inline-flex size-1.5 rounded-full bg-current" />
        </span>
      )}
      {meta.label}
    </Badge>
  )
}

/* ------------------------------------------------------------ Provenance */

const CONTENT_KIND_META: Record<
  ContentKind,
  { label: string; short: string; exact: boolean; icon: React.ElementType; note: string }
> = {
  cad_native: {
    label: "CAD native",
    short: "CAD",
    exact: true,
    icon: BoxIcon,
    note: "Read from a DXF/DWG. Dimensions are the CAD values — exact, with layers and blocks available.",
  },
  vector_drawing: {
    label: "Plotted sheet",
    short: "PLOT",
    exact: false,
    icon: LayersIcon,
    note: "Plotted from CAD to PDF. Text is real text with real coordinates, but a dimension is the string the drafter's system rendered.",
  },
  scanned_drawing: {
    label: "Scanned drawing",
    short: "SCAN",
    exact: false,
    icon: ScanLineIcon,
    note: "A picture of a drawing. Every character came from OCR — treat dimensions as a reading, not a value.",
  },
  scanned_prose: {
    label: "Scanned document",
    short: "SCAN",
    exact: false,
    icon: ScanLineIcon,
    note: "Pixels, but laid out as prose. Should OCR cleanly; a low score here is a real problem.",
  },
  mixed: {
    label: "Mixed page",
    short: "MIX",
    exact: false,
    icon: LayersIcon,
    note: "Drawing and prose both substantially present on the same page. Chunks are classified individually.",
  },
  prose: {
    label: "Prose",
    short: "TEXT",
    exact: false,
    icon: FileTextIcon,
    note: "A specification, calculation or schedule. Sentences and tables, not a sheet.",
  },
}

export function ContentKindBadge({
  kind,
  className,
  compact = false,
}: {
  kind: ContentKind | null | undefined
  className?: string
  compact?: boolean
}) {
  if (!kind) return null
  const meta = CONTENT_KIND_META[kind]
  if (!meta) return null
  const Icon = meta.icon
  return (
    <Hint
      label={
        <span>
          <b>{meta.label}</b> — {meta.note}
        </span>
      }
    >
      <span
        className={cn(
          "inline-flex w-fit items-center gap-1 rounded-sm border px-1.5 py-0.5 font-mono text-[0.625rem] font-medium uppercase leading-4 tracking-wide",
          meta.exact
            ? "border-ok/35 bg-ok-soft text-ok"
            : "border-border bg-muted text-muted-foreground",
          className,
        )}
      >
        <Icon className="size-3 shrink-0" />
        {compact ? meta.short : meta.label}
      </span>
    </Hint>
  )
}

/** Says out loud whether a number from this source can be trusted as exact. */
export function ExactnessNote({ kind }: { kind: ContentKind | null | undefined }) {
  if (!kind) return null
  const meta = CONTENT_KIND_META[kind]
  if (meta.exact) {
    return (
      <span className="inline-flex items-center gap-1 text-[0.6875rem] text-ok">
        <CheckCircle2Icon className="size-3" />
        Dimensions are exact CAD values
      </span>
    )
  }
  const ocr = kind === "scanned_drawing" || kind === "scanned_prose"
  return (
    <span className="inline-flex items-center gap-1 text-[0.6875rem] text-warn">
      <AlertTriangleIcon className="size-3" />
      {ocr ? "Dimensions are OCR readings — verify against the source sheet" : "Dimensions are rendered strings, not CAD values"}
    </span>
  )
}

export { CONTENT_KIND_META }

/* -------------------------------------------------------------- Revision */

/**
 * A revision label, framed like the triangular revision flag on a drawing.
 * Superseded revisions are struck through — on a drawing register the single
 * most expensive mistake is reading a number off an old sheet.
 */
export function RevisionChip({
  label,
  isLatest = true,
  date,
  className,
  size = "default",
}: {
  label: string | null | undefined
  isLatest?: boolean
  date?: string | null
  className?: string
  size?: "default" | "sm"
}) {
  if (!label) return null
  return (
    <Hint
      label={
        isLatest
          ? `Revision ${label} — current issue${date ? ` (${new Date(date).toLocaleDateString()})` : ""}`
          : `Revision ${label} — superseded${date ? ` (${new Date(date).toLocaleDateString()})` : ""}. Do not take dimensions from this sheet.`
      }
    >
      <span
        className={cn(
          "inline-flex items-center gap-1 rounded-sm border px-1.5 font-mono font-semibold uppercase leading-4",
          size === "sm" ? "text-[0.625rem] py-0" : "text-[0.6875rem] py-0.5",
          isLatest
            ? "border-primary/45 bg-primary-soft text-primary"
            : "border-border bg-muted text-muted-foreground line-through decoration-1",
          className,
        )}
      >
        <svg viewBox="0 0 10 9" className="size-2.5 shrink-0" aria-hidden>
          <path d="M5 0.5 L9.5 8.5 L0.5 8.5 Z" fill="none" stroke="currentColor" strokeWidth="1.2" />
        </svg>
        {label}
      </span>
    </Hint>
  )
}

/** Drawing numbers are identifiers: always mono, never wrapped. */
export function DrawingNumber({
  value,
  sheet,
  className,
}: {
  value: string | null | undefined
  sheet?: string | null
  className?: string
}) {
  if (!value) return <span className="text-muted-foreground">—</span>
  return (
    <span className={cn("font-mono text-[0.8125rem] font-medium tracking-tight", className)}>
      {value}
      {sheet && <span className="text-muted-foreground">/{sheet}</span>}
    </span>
  )
}

/* --------------------------------------------------------------- People */

const ROLE_META: Record<Role, { label: string; note: string; tone: string }> = {
  viewer: { label: "Viewer", note: "Read only, public clearance.", tone: "border-border bg-muted text-muted-foreground" },
  analyst: { label: "Analyst", note: "Upload, ask, run evaluations. Internal clearance.", tone: "border-blueprint/30 bg-blueprint-soft text-blueprint" },
  steward: { label: "Steward", note: "Reclassify, delete, promote feedback. Confidential clearance.", tone: "border-sens-confidential/35 bg-sens-confidential/10 text-sens-confidential" },
  admin: { label: "Admin", note: "Kill switches, settings, retention. Restricted clearance.", tone: "border-primary/45 bg-primary-soft text-primary" },
}

export function RoleBadge({ role, className }: { role: Role; className?: string }) {
  const meta = ROLE_META[role] ?? ROLE_META.viewer
  return (
    <Hint label={<span><b>{meta.label}</b> — {meta.note}</span>}>
      <span
        className={cn(
          "inline-flex w-fit items-center rounded-sm border px-1.5 py-0.5 text-[0.6875rem] font-medium capitalize leading-4",
          meta.tone,
          className,
        )}
      >
        {meta.label}
      </span>
    </Hint>
  )
}

export { ROLE_META }

/* -------------------------------------------------------------- Project */

const PROJECT_STATUS_META: Record<ProjectStatus, { label: string; variant: "ok" | "warn" | "idle" | "muted" }> = {
  active: { label: "Active", variant: "ok" },
  on_hold: { label: "On hold", variant: "warn" },
  closed: { label: "Closed", variant: "idle" },
  archived: { label: "Archived", variant: "muted" },
}

export function ProjectStatusBadge({ status }: { status: ProjectStatus }) {
  const meta = PROJECT_STATUS_META[status] ?? PROJECT_STATUS_META.active
  return <Badge variant={meta.variant}>{meta.label}</Badge>
}

/* ------------------------------------------------------- Steel entities */

const ENTITY_TONE: Record<string, string> = {
  section: "border-primary/35 bg-primary-soft text-primary",
  grade: "border-blueprint/30 bg-blueprint-soft text-blueprint",
  bolt: "border-chart-3/35 bg-chart-3/10 text-chart-3",
  weld: "border-chart-4/35 bg-chart-4/10 text-chart-4",
  mark: "border-chart-5/35 bg-chart-5/10 text-chart-5",
  dimension: "border-border bg-muted text-muted-foreground",
  load: "border-chart-6/35 bg-chart-6/10 text-chart-6",
  drawing_ref: "border-border bg-muted text-muted-foreground",
  project_ref: "border-border bg-muted text-muted-foreground",
}

const ENTITY_TYPE_LABEL: Record<string, string> = {
  section: "Section designation",
  grade: "Steel grade",
  bolt: "Bolt assembly",
  weld: "Weld",
  mark: "Piece mark",
  dimension: "Dimension",
  load: "Load capacity",
  drawing_ref: "Drawing reference",
  project_ref: "Project reference",
}

export function EntityChip({
  canonical,
  type = "section",
  confidence,
  source,
  onClick,
  active,
  className,
}: {
  canonical: string
  type?: string
  confidence?: number
  source?: string
  onClick?: () => void
  active?: boolean
  className?: string
}) {
  const Wrapper = onClick ? "button" : "span"
  return (
    <Hint
      label={
        <span className="space-y-0.5">
          <span className="block font-medium">{ENTITY_TYPE_LABEL[type] ?? "Entity"}</span>
          {confidence != null && (
            <span className="block">
              Confidence {confidence.toFixed(2)}
              {source && ` · ${source === "gazetteer" ? "gazetteer-validated" : source}`}
            </span>
          )}
          {source === "llm" && (
            <span className="block text-warn">LLM-extracted — capped at 0.75, regex wins ties.</span>
          )}
        </span>
      }
    >
      <Wrapper
        {...(onClick ? { onClick, type: "button" as const } : {})}
        className={cn(
          "inline-flex w-fit items-center gap-1 rounded-sm border px-1.5 py-0.5 font-mono text-[0.6875rem] font-medium leading-4 transition-colors",
          ENTITY_TONE[type] ?? ENTITY_TONE.section,
          onClick && "cursor-pointer hover:brightness-110",
          active && "ring-1 ring-current",
          className,
        )}
      >
        {canonical}
        {confidence != null && confidence < 0.8 && (
          <span className="opacity-60" aria-label="low confidence">
            ~
          </span>
        )}
      </Wrapper>
    </Hint>
  )
}

/* ------------------------------------------------------ Confidence meter */

/** OCR confidence, against the floor that actually applies to this content. */
export function ConfidenceMeter({
  value,
  floor,
  label = "OCR confidence",
  className,
}: {
  value: number | null | undefined
  floor?: number
  label?: string
  className?: string
}) {
  if (value == null) {
    return <span className={cn("text-xs text-muted-foreground", className)}>No OCR — native text</span>
  }
  const pass = floor == null || value >= floor
  return (
    <Hint
      label={
        floor != null
          ? `${label} ${value.toFixed(2)} against a floor of ${floor.toFixed(2)} for this content type. ${
              pass ? "Above the floor." : "Below the floor — chunks from this page are rejected."
            }`
          : `${label} ${value.toFixed(2)}`
      }
    >
      <span className={cn("inline-flex items-center gap-1.5", className)}>
        <span className="relative h-1.5 w-14 overflow-hidden rounded-full bg-muted">
          <span
            className={cn("absolute inset-y-0 left-0 rounded-full", pass ? "bg-ok" : "bg-error")}
            style={{ width: `${Math.max(3, value * 100)}%` }}
          />
          {floor != null && (
            <span
              className="absolute inset-y-0 w-px bg-foreground/50"
              style={{ left: `${floor * 100}%` }}
            />
          )}
        </span>
        <span
          className={cn(
            "font-mono text-[0.6875rem] tabular-nums",
            pass ? "text-muted-foreground" : "text-error",
          )}
        >
          {value.toFixed(2)}
        </span>
      </span>
    </Hint>
  )
}

/** Region precision, stated honestly — a soft highlight is not a tight box. */
export function PrecisionBadge({ precision }: { precision?: string | null }) {
  if (!precision) return null
  const note: Record<string, string> = {
    block: "Tight box — the chunk's own text blocks carry coordinates.",
    section: "Soft highlight — coordinates inherited from the parent section, so the box is approximate.",
    page: "Whole page — no coordinates survived chunking for this text.",
  }
  return (
    <Hint label={note[precision] ?? precision}>
      <Badge variant="outline" className="font-mono uppercase">
        {precision}
      </Badge>
    </Hint>
  )
}
