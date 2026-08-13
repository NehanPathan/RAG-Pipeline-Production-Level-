import * as React from "react"
import { Link } from "react-router-dom"
import { useQueries } from "@tanstack/react-query"
import { ClipboardListIcon, LayersIcon, SearchIcon } from "lucide-react"
import { api } from "@/api"
import { cn, timeAgo } from "@/lib/utils"
import { Page } from "@/components/layout/page"
import { PageHeader } from "@/components/domain/layout"
import { Input } from "@/components/ui/input"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Hint } from "@/components/ui/tooltip"
import {
  ContentKindBadge,
  DocumentStatusBadge,
  DrawingNumber,
  RevisionChip,
} from "@/components/domain/badges"
import { EmptyState, ErrorState, TableSkeleton } from "@/components/domain/states"

/**
 * The drawing register.
 *
 * Keyed on drawing identity rather than on files: `S-104` is one row whose
 * revisions are its history. That is the distinction that makes "the current
 * baseplate thickness on S-104" a question with an answer.
 */
export default function DrawingsPage() {
  const [search, setSearch] = React.useState("")
  const [project, setProject] = React.useState("all")
  const [discipline, setDiscipline] = React.useState("all")

  const [drawingsQ, projectsQ] = useQueries({
    queries: [
      { queryKey: ["drawings"], queryFn: () => api.listDrawings() },
      { queryKey: ["projects"], queryFn: () => api.listProjects() },
    ],
  })

  const drawings = drawingsQ.data ?? []
  const projects = projectsQ.data ?? []
  const disciplines = React.useMemo(
    () => [...new Set(drawings.map((d) => d.discipline).filter(Boolean))] as string[],
    [drawings],
  )

  const filtered = React.useMemo(() => {
    let items = drawings
    if (project !== "all") items = items.filter((d) => d.project_id === project)
    if (discipline !== "all") items = items.filter((d) => d.discipline === discipline)
    if (search.trim()) {
      const q = search.toLowerCase()
      items = items.filter(
        (d) =>
          d.drawing_number.toLowerCase().includes(q) ||
          (d.title ?? "").toLowerCase().includes(q),
      )
    }
    return [...items].sort((a, b) => a.drawing_number.localeCompare(b.drawing_number))
  }, [drawings, project, discipline, search])

  const totalRevisions = filtered.reduce((sum, d) => sum + (d.revision_count ?? 0), 0)

  return (
    <Page>
      <PageHeader
        eyebrow="Register"
        title="Drawings"
        description="A drawing is an identity that outlives its revisions. Rev A, B and C are three documents of one drawing — search defaults to the current issue of each."
        meta={
          <>
            <Badge variant="outline" className="font-mono">
              {filtered.length} drawings
            </Badge>
            <Badge variant="outline" className="font-mono">
              {totalRevisions} revisions
            </Badge>
          </>
        }
      />

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-1 sm:max-w-xs">
          <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Drawing number or title…"
            className="pl-9"
          />
        </div>
        <Select value={project} onValueChange={setProject}>
          <SelectTrigger size="sm" className="w-auto min-w-40">
            <SelectValue placeholder="All projects" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All projects</SelectItem>
            {projects.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                {p.project_number}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={discipline} onValueChange={setDiscipline}>
          <SelectTrigger size="sm" className="w-auto min-w-36">
            <SelectValue placeholder="All disciplines" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All disciplines</SelectItem>
            {disciplines.map((d) => (
              <SelectItem key={d} value={d}>
                {d}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="mt-4">
        {drawingsQ.isPending ? (
          <TableSkeleton rows={10} cols={7} />
        ) : drawingsQ.isError ? (
          <ErrorState error={drawingsQ.error} onRetry={drawingsQ.refetch} resource="the drawing register" />
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={ClipboardListIcon}
            title={search ? `No drawing matches “${search}”` : "The register is empty"}
            description={
              search
                ? "Drawing numbers are matched as substrings — try just the sheet number."
                : "A drawing is registered the first time a file is uploaded with a drawing number and a revision label."
            }
          />
        ) : (
          <Card>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Drawing</TableHead>
                  <TableHead>Title</TableHead>
                  <TableHead>Project</TableHead>
                  <TableHead>Discipline</TableHead>
                  <TableHead>Current</TableHead>
                  <TableHead>Revision history</TableHead>
                  <TableHead>Provenance</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Issued</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((drawing) => (
                  <TableRow key={drawing.id}>
                    <TableCell>
                      <Link to={`/drawings/${drawing.id}`} className="hover:text-primary">
                        <DrawingNumber value={drawing.drawing_number} sheet={drawing.sheet_number} />
                      </Link>
                    </TableCell>
                    <TableCell className="max-w-sm">
                      <Link
                        to={`/drawings/${drawing.id}`}
                        className="block truncate text-[0.8125rem] hover:text-primary"
                      >
                        {drawing.title}
                      </Link>
                    </TableCell>
                    <TableCell className="font-mono text-[0.75rem] text-muted-foreground">
                      {drawing.project_number}
                    </TableCell>
                    <TableCell className="text-[0.8125rem] text-muted-foreground">
                      {drawing.discipline}
                    </TableCell>
                    <TableCell>
                      <RevisionChip
                        label={drawing.current_revision_label}
                        date={drawing.current_revision_date}
                      />
                    </TableCell>
                    <TableCell>
                      <RevisionLadder count={drawing.revision_count ?? 1} />
                    </TableCell>
                    <TableCell>
                      <ContentKindBadge kind={drawing.content_kind} compact />
                    </TableCell>
                    <TableCell>
                      {drawing.status && <DocumentStatusBadge status={drawing.status} />}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-[0.8125rem] text-muted-foreground">
                      {timeAgo(drawing.current_revision_date)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
        )}
      </div>
    </Page>
  )
}

/**
 * The revision count as a ladder of ticks, newest on the right. Reading
 * "four revisions, the last one current" off a shape is faster than off a
 * number, and it makes a heavily-revised sheet visible while scanning.
 */
function RevisionLadder({ count }: { count: number }) {
  const shown = Math.min(count, 8)
  return (
    <Hint
      label={`${count} ${count === 1 ? "revision" : "revisions"} on file. The rightmost tick is the current issue.`}
    >
      <span className="inline-flex items-end gap-[3px]">
        {Array.from({ length: shown }, (_, i) => (
          <span
            key={i}
            className={cn(
              "w-[3px] rounded-[1px]",
              i === shown - 1 ? "bg-primary" : "bg-border",
            )}
            style={{ height: `${6 + i * 1.4}px` }}
          />
        ))}
        {count > 8 && (
          <span className="ml-1 font-mono text-[0.625rem] text-muted-foreground">+{count - 8}</span>
        )}
        <span className="ml-1.5 flex items-center gap-1 font-mono text-[0.625rem] tabular-nums text-muted-foreground">
          <LayersIcon className="size-3" />
          {count}
        </span>
      </span>
    </Hint>
  )
}
