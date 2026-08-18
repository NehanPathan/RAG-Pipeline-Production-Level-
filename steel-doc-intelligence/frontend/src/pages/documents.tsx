import * as React from "react"
import { Link, useSearchParams } from "react-router-dom"
import { useQueries, useQueryClient } from "@tanstack/react-query"
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  FileStackIcon,
  FilterXIcon,
  RefreshCwIcon,
  SearchIcon,
  UploadCloudIcon,
} from "lucide-react"
import { api } from "@/api"
import type { ContentKind, DocumentStatus, Sensitivity } from "@/api/types"
import { useAuth } from "@/lib/auth"
import { cn, formatBytes, formatNumber, timeAgo } from "@/lib/utils"
import { Page } from "@/components/layout/page"
import { PageHeader } from "@/components/domain/layout"
import { Button } from "@/components/ui/button"
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
import { Label, SegmentedTrack, Switch, ToggleGroup, ToggleGroupItem } from "@/components/ui/misc"
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
  SensitivityBar,
} from "@/components/domain/badges"
import { EmptyState, ErrorState, TableSkeleton } from "@/components/domain/states"

const PAGE_SIZE = 25

const STATUSES: DocumentStatus[] = ["indexed", "processing", "pending", "failed"]
const SENSITIVITIES: Sensitivity[] = ["public", "internal", "confidential", "restricted"]
const KINDS: ContentKind[] = [
  "cad_native",
  "vector_drawing",
  "scanned_drawing",
  "scanned_prose",
  "mixed",
  "prose",
]

export default function DocumentsPage() {
  const { hasRole } = useAuth()
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()

  const [search, setSearch] = React.useState(searchParams.get("q") ?? "")
  const [debounced, setDebounced] = React.useState(search)
  const [page, setPage] = React.useState(1)
  const [status, setStatus] = React.useState<DocumentStatus[]>([])
  const [sensitivity, setSensitivity] = React.useState<Sensitivity[]>([])
  const [kind, setKind] = React.useState<ContentKind[]>([])
  const [projectId, setProjectId] = React.useState("all")
  const [latestOnly, setLatestOnly] = React.useState(false)

  React.useEffect(() => {
    const timer = setTimeout(() => {
      setDebounced(search)
      setPage(1)
      setSearchParams(search ? { q: search } : {}, { replace: true })
    }, 250)
    return () => clearTimeout(timer)
  }, [search, setSearchParams])

  const filters = {
    page,
    size: PAGE_SIZE,
    search: debounced || undefined,
    status: status.length ? status : undefined,
    sensitivity: sensitivity.length ? sensitivity : undefined,
    content_kind: kind.length ? kind : undefined,
    project_id: projectId === "all" ? undefined : projectId,
    latest_only: latestOnly || undefined,
  }

  const [docsQ, projectsQ] = useQueries({
    queries: [
      { queryKey: ["documents", filters], queryFn: () => api.listDocuments(filters) },
      { queryKey: ["projects"], queryFn: () => api.listProjects() },
    ],
  })

  const documents = docsQ.data?.items ?? []
  const total = docsQ.data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const activeFilters =
    status.length + sensitivity.length + kind.length + (projectId !== "all" ? 1 : 0) + (latestOnly ? 1 : 0)

  function clearFilters() {
    setStatus([])
    setSensitivity([])
    setKind([])
    setProjectId("all")
    setLatestOnly(false)
    setPage(1)
  }

  return (
    <Page>
      <PageHeader
        eyebrow="Register"
        title="Documents"
        description="Every ingested file you can read. The list is filtered by clearance in the query itself, so the count is the true count of what is yours to see — never a full count with rows removed afterwards."
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => queryClient.invalidateQueries({ queryKey: ["documents"] })}
            >
              <RefreshCwIcon className={cn(docsQ.isFetching && "animate-spin")} />
              Refresh
            </Button>
            {hasRole("analyst", "steward", "admin") && (
              <Button asChild size="sm">
                <Link to="/ingest">
                  <UploadCloudIcon />
                  Upload
                </Link>
              </Button>
            )}
          </>
        }
      />

      {/* ------------------------------------------------------- filters */}
      <div className="mt-4 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-0 flex-1 sm:max-w-sm">
            <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="File name, drawing number, tag…"
              className="pl-9"
            />
          </div>

          <Select
            value={projectId}
            onValueChange={(value) => {
              setProjectId(value)
              setPage(1)
            }}
          >
            <SelectTrigger size="sm" className="w-auto min-w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All projects</SelectItem>
              {(projectsQ.data ?? []).map((project) => (
                <SelectItem key={project.id} value={project.id}>
                  {project.project_number}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Hint label="Hide superseded revisions of a drawing.">
            <Label className="cursor-pointer gap-1.5 rounded-md border border-border bg-card px-2.5 py-1.5 text-[0.75rem]">
              <Switch
                checked={latestOnly}
                onCheckedChange={(v) => {
                  setLatestOnly(v)
                  setPage(1)
                }}
              />
              Current revisions only
            </Label>
          </Hint>

          {activeFilters > 0 && (
            <Button variant="ghost" size="sm" onClick={clearFilters}>
              <FilterXIcon />
              Clear ({activeFilters})
            </Button>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <FilterRow label="Status">
            <SegmentedTrack>
              <ToggleGroup
                type="multiple"
                value={status}
                onValueChange={(v) => {
                  setStatus(v as DocumentStatus[])
                  setPage(1)
                }}
              >
                {STATUSES.map((value) => (
                  <ToggleGroupItem key={value} value={value} className="capitalize">
                    {value}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
            </SegmentedTrack>
          </FilterRow>

          <FilterRow label="Classification">
            <SegmentedTrack>
              <ToggleGroup
                type="multiple"
                value={sensitivity}
                onValueChange={(v) => {
                  setSensitivity(v as Sensitivity[])
                  setPage(1)
                }}
              >
                {SENSITIVITIES.map((value) => (
                  <ToggleGroupItem key={value} value={value} className="capitalize">
                    {value}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
            </SegmentedTrack>
          </FilterRow>

          <FilterRow label="Provenance">
            <SegmentedTrack>
              <ToggleGroup
                type="multiple"
                value={kind}
                onValueChange={(v) => {
                  setKind(v as ContentKind[])
                  setPage(1)
                }}
              >
                {KINDS.map((value) => (
                  <ToggleGroupItem key={value} value={value} className="font-mono">
                    {value.replace("_", " ")}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
            </SegmentedTrack>
          </FilterRow>
        </div>
      </div>

      {/* --------------------------------------------------------- table */}
      <div className="mt-4">
        {docsQ.isPending ? (
          <TableSkeleton rows={10} cols={8} />
        ) : docsQ.isError ? (
          <ErrorState error={docsQ.error} onRetry={docsQ.refetch} resource="documents" />
        ) : documents.length === 0 ? (
          <EmptyState
            icon={FileStackIcon}
            title={
              debounced || activeFilters
                ? "Nothing matches these filters"
                : "No documents yet"
            }
            description={
              debounced || activeFilters ? (
                "Try widening the classification or status filters, or clear the search."
              ) : (
                <>
                  Upload a drawing set, a specification or a scanned bundle. Ingestion classifies
                  each page, routes only the pages that need it to OCR, and extracts section
                  designations, grades and bolt classes as it goes.
                </>
              )
            }
            action={
              debounced || activeFilters ? (
                <Button size="sm" variant="outline" onClick={clearFilters}>
                  Clear filters
                </Button>
              ) : hasRole("analyst", "steward", "admin") ? (
                <Button asChild size="sm">
                  <Link to="/ingest">Upload documents</Link>
                </Button>
              ) : undefined
            }
          />
        ) : (
          <>
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-6" />
                    <TableHead>File</TableHead>
                    <TableHead>Drawing / rev</TableHead>
                    <TableHead>Project</TableHead>
                    <TableHead>Provenance</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead numeric>Pages</TableHead>
                    <TableHead numeric>Chunks</TableHead>
                    <TableHead numeric>Size</TableHead>
                    <TableHead>Added</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {documents.map((doc) => (
                    <TableRow key={doc.id} className={cn(doc.is_latest === false && "opacity-70")}>
                      <TableCell className="pr-0">
                        <SensitivityBar value={doc.sensitivity} />
                      </TableCell>
                      <TableCell className="max-w-sm">
                        <Link
                          to={`/documents/${doc.id}`}
                          className="block truncate text-[0.8125rem] font-medium hover:text-primary"
                          title={doc.file_name}
                        >
                          {doc.file_name}
                        </Link>
                        <span className="flex items-center gap-1.5 text-[0.6875rem] text-muted-foreground">
                          {doc.domain}
                          {doc.tags.slice(0, 2).map((tag) => (
                            <Badge key={tag} variant="muted" className="font-mono">
                              {tag}
                            </Badge>
                          ))}
                          {doc.tags.length > 2 && <span>+{doc.tags.length - 2}</span>}
                        </span>
                      </TableCell>
                      <TableCell>
                        {doc.drawing_number ? (
                          <span className="flex items-center gap-1.5">
                            <Link
                              to={`/drawings/${doc.drawing_id}`}
                              className="hover:text-primary"
                            >
                              <DrawingNumber value={doc.drawing_number} />
                            </Link>
                            <RevisionChip
                              label={doc.revision_label}
                              isLatest={doc.is_latest !== false}
                              date={doc.revision_date}
                              size="sm"
                            />
                          </span>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell className="font-mono text-[0.75rem] text-muted-foreground">
                        {doc.project_number ?? (
                          <Hint label="No project — a personal document, visible only to its owner.">
                            <span className="italic">personal</span>
                          </Hint>
                        )}
                      </TableCell>
                      <TableCell>
                        <ContentKindBadge kind={doc.content_kind} compact />
                      </TableCell>
                      <TableCell>
                        <DocumentStatusBadge status={doc.status} />
                      </TableCell>
                      <TableCell numeric>{doc.page_count ?? "—"}</TableCell>
                      <TableCell numeric>{doc.chunk_count ?? "—"}</TableCell>
                      <TableCell numeric>{formatBytes(doc.file_size_bytes)}</TableCell>
                      <TableCell className="whitespace-nowrap text-[0.8125rem] text-muted-foreground">
                        {timeAgo(doc.created_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>

            <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs text-muted-foreground">
                Showing{" "}
                <span className="font-mono tabular-nums text-foreground">
                  {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)}
                </span>{" "}
                of <span className="font-mono tabular-nums text-foreground">{formatNumber(total)}</span>
              </p>
              <div className="flex items-center gap-1.5">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page <= 1}
                >
                  <ChevronLeftIcon />
                  Previous
                </Button>
                <span className="px-2 font-mono text-xs tabular-nums text-muted-foreground">
                  {page} / {pages}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage((p) => Math.min(pages, p + 1))}
                  disabled={page >= pages}
                >
                  Next
                  <ChevronRightIcon />
                </Button>
              </div>
            </div>
          </>
        )}
      </div>
    </Page>
  )
}

function FilterRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <span className="eyebrow shrink-0">{label}</span>
      {children}
    </div>
  )
}
