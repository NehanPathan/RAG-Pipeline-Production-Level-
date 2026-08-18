import * as React from "react"
import { Link, useParams } from "react-router-dom"
import { useQueries } from "@tanstack/react-query"
import {
  ArrowRightIcon,
  CheckCircle2Icon,
  GitCompareIcon,
  MessagesSquareIcon,
  TriangleAlertIcon,
} from "lucide-react"
import { api } from "@/api"
import type { Revision } from "@/api/types"
import { cn, formatDate, timeAgo } from "@/lib/utils"
import { Page } from "@/components/layout/page"
import { Breadcrumbs, PageHeader, TitleBlock } from "@/components/domain/layout"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Hint } from "@/components/ui/tooltip"
import {
  ContentKindBadge,
  DocumentStatusBadge,
  RevisionChip,
} from "@/components/domain/badges"
import { SheetViewer } from "@/components/domain/sheet-viewer"
import { EmptyState, ErrorState, TextSkeleton } from "@/components/domain/states"

export default function DrawingDetailPage() {
  const { drawingId = "" } = useParams()
  const [compareWith, setCompareWith] = React.useState<string | null>(null)

  const [drawingQ, revisionsQ] = useQueries({
    queries: [
      { queryKey: ["drawing", drawingId], queryFn: () => api.getDrawing(drawingId) },
      { queryKey: ["revisions", drawingId], queryFn: () => api.listRevisions(drawingId) },
    ],
  })

  const drawing = drawingQ.data
  const revisions = revisionsQ.data ?? []
  const current = revisions.find((r) => r.is_latest) ?? revisions[0]

  const [selectedId, setSelectedId] = React.useState<string | null>(null)
  const selected = revisions.find((r) => r.document_id === selectedId) ?? current

  const documentIds = [selected?.document_id, compareWith].filter(Boolean) as string[]
  const documentQueries = useQueries({
    queries: documentIds.map((id) => ({
      queryKey: ["document", id],
      queryFn: () => api.getDocument(id),
    })),
  })
  const [leftDoc, rightDoc] = documentQueries.map((q) => q.data)

  if (drawingQ.isError) {
    return (
      <Page width="reading">
        <ErrorState error={drawingQ.error} onRetry={drawingQ.refetch} resource="this drawing" />
      </Page>
    )
  }

  return (
    <Page>
      <Breadcrumbs
        items={[
          { label: "Drawings", to: "/drawings" },
          ...(drawing?.project_number
            ? [{ label: drawing.project_number, to: `/projects/${drawing.project_id}` }]
            : []),
          { label: drawing?.drawing_number ?? "…" },
        ]}
      />

      <PageHeader
        className="mt-3"
        eyebrow={
          <span className="font-mono normal-case tracking-normal">
            {drawing?.drawing_number}
            {drawing?.sheet_number ? `/${drawing.sheet_number}` : ""}
          </span>
        }
        title={drawing?.title ?? "Loading…"}
        meta={
          drawing && (
            <>
              <RevisionChip
                label={drawing.current_revision_label}
                date={drawing.current_revision_date}
              />
              <ContentKindBadge kind={drawing.content_kind} />
              <Badge variant="outline">{drawing.discipline}</Badge>
              <span className="text-xs text-muted-foreground">
                {revisions.length} {revisions.length === 1 ? "revision" : "revisions"} on file
              </span>
            </>
          )
        }
        actions={
          <>
            <Select
              value={compareWith ?? "none"}
              onValueChange={(v) => setCompareWith(v === "none" ? null : v)}
            >
              <SelectTrigger size="sm" className="w-auto min-w-44">
                <GitCompareIcon className="size-3.5" />
                <SelectValue placeholder="Compare with…" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">No comparison</SelectItem>
                {revisions
                  .filter((r) => r.document_id !== selected?.document_id)
                  .map((revision) => (
                    <SelectItem key={revision.document_id} value={revision.document_id}>
                      Rev {revision.revision_label} · {formatDate(revision.revision_date)}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
            <Button asChild size="sm">
              <Link
                to={`/ask?q=${encodeURIComponent(`On ${drawing?.drawing_number ?? ""}, `)}`}
              >
                <MessagesSquareIcon />
                Ask about this sheet
              </Link>
            </Button>
          </>
        }
      />

      {drawing && current && (
        <TitleBlock
          className="mt-4"
          columns={6}
          fields={[
            { label: "Drawing no.", value: drawing.drawing_number, mono: true },
            { label: "Sheet", value: drawing.sheet_number ?? "—", mono: true },
            { label: "Current rev", value: current.revision_label, mono: true },
            { label: "Issued", value: formatDate(current.revision_date), mono: true },
            { label: "Discipline", value: drawing.discipline },
            { label: "Project", value: drawing.project_number, mono: true },
            { label: "Title", value: drawing.title, span: 4 },
            { label: "Revisions", value: String(revisions.length), mono: true },
            { label: "Status", value: <DocumentStatusBadge status={current.status} /> },
          ]}
        />
      )}

      <div className="mt-6 grid gap-5 lg:grid-cols-[1fr_22rem]">
        {/* ------------------------------------------------------ viewer */}
        <div className="min-w-0 space-y-3">
          {compareWith && leftDoc && rightDoc ? (
            <div className="grid gap-3 xl:grid-cols-2">
              <div className="space-y-1.5">
                <p className="flex items-center gap-2 text-xs">
                  <RevisionChip label={leftDoc.revision_label} isLatest={leftDoc.is_latest} />
                  <span className="text-muted-foreground">{formatDate(leftDoc.revision_date)}</span>
                </p>
                <SheetViewer document={leftDoc} className="h-[30rem]" />
              </div>
              <div className="space-y-1.5">
                <p className="flex items-center gap-2 text-xs">
                  <RevisionChip label={rightDoc.revision_label} isLatest={rightDoc.is_latest} />
                  <span className="text-muted-foreground">{formatDate(rightDoc.revision_date)}</span>
                </p>
                <SheetViewer document={rightDoc} className="h-[30rem]" />
              </div>
            </div>
          ) : leftDoc ? (
            <SheetViewer
              document={leftDoc}
              className="h-[36rem]"
              downloadUrl={api.originalUrl(leftDoc.id)}
              toolbarExtra={
                <Button asChild variant="ghost" size="xs">
                  <Link to={`/documents/${leftDoc.id}`}>
                    Document detail
                    <ArrowRightIcon />
                  </Link>
                </Button>
              }
            />
          ) : (
            <Card className="flex h-96 items-center justify-center">
              <TextSkeleton lines={3} />
            </Card>
          )}

          {compareWith && (
            <Card className="bg-muted/30 p-3">
              <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
                <b className="text-foreground">Side-by-side, not a geometric diff.</b> The system
                does not compute a difference between two sheets — it shows both issues and their
                revision notes. What changed is recorded in the note, which is the drafter's own
                statement of intent and more reliable than an inferred diff.
              </p>
            </Card>
          )}
        </div>

        {/* --------------------------------------------- revision history */}
        <aside className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold tracking-tight">Revision history</h2>
            <Hint label="Exactly one revision per drawing is current, enforced by a partial unique index in Postgres.">
              <Badge variant="outline" className="font-mono">
                {revisions.length}
              </Badge>
            </Hint>
          </div>

          {revisionsQ.isPending ? (
            <TextSkeleton lines={6} />
          ) : revisions.length === 0 ? (
            <EmptyState compact title="No revisions on file" />
          ) : (
            <ol className="relative space-y-2 pl-5">
              {/* the timeline spine */}
              <span
                className="absolute bottom-3 left-[7px] top-3 w-px bg-border"
                aria-hidden
              />
              {revisions.map((revision) => (
                <RevisionRow
                  key={revision.document_id}
                  revision={revision}
                  selected={selected?.document_id === revision.document_id}
                  comparing={compareWith === revision.document_id}
                  onSelect={() => setSelectedId(revision.document_id)}
                />
              ))}
            </ol>
          )}

          <Card className="space-y-2 bg-muted/30 p-3">
            <p className="flex items-center gap-1.5 text-xs font-medium">
              <TriangleAlertIcon className="size-3.5 text-warn" />
              Why superseded sheets stay
            </p>
            <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
              Old issues are kept and remain searchable behind the{" "}
              <b className="text-foreground">include superseded</b> toggle, because as-built
              queries and claims genuinely need them. They are excluded by default, struck through
              wherever they appear, and stamped in the viewer — answering from an old sheet is
              worse than not answering.
            </p>
          </Card>
        </aside>
      </div>
    </Page>
  )
}

function RevisionRow({
  revision,
  selected,
  comparing,
  onSelect,
}: {
  revision: Revision
  selected: boolean
  comparing: boolean
  onSelect: () => void
}) {
  return (
    <li className="relative">
      <span
        className={cn(
          "absolute -left-5 top-3 size-[9px] rounded-full border-2 border-background",
          revision.is_latest ? "bg-primary" : "bg-border",
        )}
        aria-hidden
      />
      <button
        onClick={onSelect}
        className={cn(
          "w-full rounded-md border bg-card p-2.5 text-left transition-colors",
          selected
            ? "border-primary/55 bg-primary-soft/30"
            : comparing
              ? "border-blueprint/45"
              : "border-border hover:border-primary/35",
        )}
      >
        <div className="flex items-center gap-2">
          <RevisionChip
            label={revision.revision_label}
            isLatest={revision.is_latest}
            date={revision.revision_date}
          />
          {revision.is_latest ? (
            <Badge variant="ok">
              <CheckCircle2Icon className="size-3" />
              Current
            </Badge>
          ) : (
            <span className="text-[0.625rem] uppercase tracking-wide text-muted-foreground">
              superseded
            </span>
          )}
          <span className="ml-auto font-mono text-[0.625rem] tabular-nums text-muted-foreground">
            {timeAgo(revision.revision_date)}
          </span>
        </div>
        {revision.revision_note && (
          <p className="mt-1.5 text-pretty text-[0.75rem] leading-relaxed text-muted-foreground">
            {revision.revision_note}
          </p>
        )}
        <div className="mt-1.5 flex items-center gap-2 text-[0.625rem] text-muted-foreground">
          <DocumentStatusBadge status={revision.status} />
          <span className="truncate font-mono">{revision.file_name}</span>
        </div>
      </button>
    </li>
  )
}
