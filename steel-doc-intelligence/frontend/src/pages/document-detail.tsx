import * as React from "react"
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom"
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  BoxesIcon,
  BrainCircuitIcon,
  DownloadIcon,
  HistoryIcon,
  LayoutListIcon,
  RefreshCwIcon,
  ScanTextIcon,
  Loader2Icon,
  ShieldAlertIcon,
  ShieldCheckIcon,
  ShieldIcon,
  Trash2Icon,
  TriangleAlertIcon,
} from "lucide-react"
import { api, ApiError } from "@/api"
import type { Chunk, Sensitivity, SteelEntity } from "@/api/types"
import { useAuth } from "@/lib/auth"
import { cn, formatBytes, formatDate, formatMs, formatNumber } from "@/lib/utils"
import { Page } from "@/components/layout/page"
import { Breadcrumbs, DataList, PageHeader, TitleBlock } from "@/components/domain/layout"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/input"
import { Input } from "@/components/ui/input"
import { Label, Progress, ScrollArea, Separator, Skeleton } from "@/components/ui/misc"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
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
  ConfidenceMeter,
  ContentKindBadge,
  DocumentStatusBadge,
  DrawingNumber,
  EntityChip,
  ExactnessNote,
  JobStatusBadge,
  PrecisionBadge,
  RevisionChip,
  SensitivityBadge,
} from "@/components/domain/badges"
import { SheetViewer, type Highlight } from "@/components/domain/sheet-viewer"
import { EmptyState, ErrorState, TextSkeleton } from "@/components/domain/states"
import { entityType } from "@/pages/search"

/** Per-content-type OCR floors, matching ChunkValidator. */
const OCR_FLOOR = { drawing: 0.15, prose: 0.35 }

export default function DocumentDetailPage() {
  const { documentId = "" } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const { hasRole } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [activeChunkId, setActiveChunkId] = React.useState<string | null>(
    searchParams.get("chunk"),
  )
  const [page, setPage] = React.useState(Number(searchParams.get("page") ?? 1))
  const [reclassifyOpen, setReclassifyOpen] = React.useState(false)

  const [docQ, chunksQ, intelQ, jobsQ] = useQueries({
    queries: [
      {
        queryKey: ["document", documentId],
        queryFn: () => api.getDocument(documentId),
        // A document mid-ingestion changes under the page; poll until it lands.
        refetchInterval: (query: { state: { data?: { status?: string } } }) =>
          query.state.data?.status === "processing" || query.state.data?.status === "pending"
            ? 3000
            : false,
      },
      { queryKey: ["chunks", documentId], queryFn: () => api.getChunks(documentId) },
      { queryKey: ["intelligence", documentId], queryFn: () => api.getIntelligence(documentId), retry: false },
      { queryKey: ["document-jobs", documentId], queryFn: () => api.getDocumentJobs(documentId) },
    ],
  })

  const doc = docQ.data
  const chunks = chunksQ.data?.items ?? []
  const intel = intelQ.data
  const jobs = jobsQ.data ?? []

  const reprocess = useMutation({
    mutationFn: () => api.reprocessDocument(documentId),
    onSuccess: (result) => {
      if (result.status === "already_running") {
        toast.info("Already in flight", { description: result.message })
      } else {
        toast.success("Reprocessing queued", {
          description:
            "Re-running is idempotent: the worker deletes what the previous attempt wrote before writing again.",
        })
      }
      void queryClient.invalidateQueries({ queryKey: ["document", documentId] })
      void queryClient.invalidateQueries({ queryKey: ["document-jobs", documentId] })
      void queryClient.invalidateQueries({ queryKey: ["jobs"] })
    },
    onError: (error) => toast.error("Could not queue reprocessing", { description: message(error) }),
  })

  const [releaseOpen, setReleaseOpen] = React.useState(false)
  const [releaseReason, setReleaseReason] = React.useState("")

  const release = useMutation({
    mutationFn: () => api.releaseDocument(documentId, releaseReason.trim()),
    onSuccess: (result) => {
      setReleaseOpen(false)
      setReleaseReason("")
      toast.success("Released for re-ingestion", { description: result.message })
      void queryClient.invalidateQueries({ queryKey: ["document", documentId] })
      void queryClient.invalidateQueries({ queryKey: ["document-jobs", documentId] })
      void queryClient.invalidateQueries({ queryKey: ["jobs"] })
      void queryClient.invalidateQueries({ queryKey: ["documents"] })
    },
    onError: (error) => toast.error("Could not release", { description: message(error) }),
  })

  const remove = useMutation({
    mutationFn: () => api.deleteDocument(documentId),
    onSuccess: (result) => {
      toast.success("Document deleted", {
        description: `${result.deleted_chunks} chunks removed from Postgres, Qdrant, Elasticsearch and the answer cache.`,
      })
      void queryClient.invalidateQueries({ queryKey: ["documents"] })
      navigate("/documents")
    },
    onError: (error) => toast.error("Could not delete", { description: message(error) }),
  })

  // Chunk selection drives the viewer: clicking a chunk highlights its regions
  // and moves to its page.
  const activeChunk = chunks.find((c) => c.id === activeChunkId) ?? null
  React.useEffect(() => {
    if (!activeChunk?.regions?.length) return
    const target = activeChunk.regions[0].page_number
    if (target && target !== page) setPage(target)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeChunkId])

  React.useEffect(() => {
    const next = new URLSearchParams(searchParams)
    if (activeChunkId) next.set("chunk", activeChunkId)
    else next.delete("chunk")
    next.set("page", String(page))
    setSearchParams(next, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeChunkId, page])

  const highlights: Highlight[] = React.useMemo(
    () =>
      chunks
        .filter((c) => c.regions?.length)
        .map((c, i) => ({
          id: c.id,
          regions: c.regions ?? [],
          precision: c.region_precision ?? "block",
          label: c.section_title ?? `Chunk ${i + 1}`,
        })),
    [chunks],
  )

  const entities = React.useMemo(() => aggregateEntities(chunks), [chunks])

  if (docQ.isError) {
    return (
      <Page width="reading">
        <ErrorState error={docQ.error} onRetry={docQ.refetch} resource="this document" />
      </Page>
    )
  }

  const isDrawingContent =
    doc?.content_kind === "scanned_drawing" ||
    doc?.content_kind === "cad_native" ||
    doc?.content_kind === "vector_drawing" ||
    doc?.content_kind === "mixed"
  const ocrFloor = isDrawingContent ? OCR_FLOOR.drawing : OCR_FLOOR.prose

  return (
    <Page>
      <Breadcrumbs
        items={[
          { label: "Documents", to: "/documents" },
          ...(doc?.project_number
            ? [{ label: doc.project_number, to: `/projects/${doc.project_id}` }]
            : []),
          { label: doc?.file_name ?? "…" },
        ]}
      />

      <PageHeader
        className="mt-3"
        eyebrow={doc?.domain}
        title={doc?.file_name ?? "Loading…"}
        meta={
          doc && (
            <>
              <DocumentStatusBadge status={doc.status} />
              <SensitivityBadge value={doc.sensitivity} />
              <ContentKindBadge kind={doc.content_kind} />
              {doc.drawing_number && (
                <Link to={`/drawings/${doc.drawing_id}`} className="hover:text-primary">
                  <DrawingNumber value={doc.drawing_number} />
                </Link>
              )}
              {doc.revision_label && (
                <RevisionChip
                  label={doc.revision_label}
                  isLatest={doc.is_latest !== false}
                  date={doc.revision_date}
                />
              )}
            </>
          )
        }
        actions={
          <>
            <Button asChild variant="outline" size="sm">
              <a href={api.originalUrl(documentId)} download>
                <DownloadIcon />
                Original
              </a>
            </Button>
            {hasRole("steward", "admin") && (
              <>
                <Button variant="outline" size="sm" onClick={() => setReclassifyOpen(true)}>
                  <ShieldIcon />
                  Reclassify
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => reprocess.mutate()}
                  disabled={reprocess.isPending}
                >
                  <RefreshCwIcon className={cn(reprocess.isPending && "animate-spin")} />
                  Reprocess
                </Button>
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button variant="ghost" size="icon-sm" aria-label="Delete document">
                      <Trash2Icon className="text-destructive" />
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>Delete this document?</AlertDialogTitle>
                      <AlertDialogDescription>
                        This removes it from Postgres, the Qdrant collection, the Elasticsearch
                        index and the semantic answer cache — {doc?.chunk_count ?? 0} chunks in
                        total. It is irreversible and written to the audit log against your name.
                        {doc?.is_latest !== false && doc?.drawing_number && (
                          <>
                            {" "}
                            This is the <b>current issue</b> of {doc.drawing_number}; deleting it
                            leaves the drawing without a current revision.
                          </>
                        )}
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Cancel</AlertDialogCancel>
                      <AlertDialogAction variant="destructive" onClick={() => remove.mutate()}>
                        Delete permanently
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </>
            )}
          </>
        }
      />

      {/* --------------------------------------------------- status banners */}
      {doc?.status === "processing" && (
        <Card className="mt-4 border-blueprint/35 bg-blueprint-soft/40 p-3">
          <div className="flex items-center gap-2 text-sm text-blueprint">
            <RefreshCwIcon className="size-4 animate-spin" />
            <span className="font-medium">Ingestion in progress</span>
            <span className="text-xs opacity-80">
              stage {jobs.find((j) => j.status === "running")?.stage ?? "—"}
            </span>
          </div>
          <Progress
            value={jobs.find((j) => j.status === "running")?.progress ?? 5}
            className="mt-2.5"
          />
          <p className="mt-2 text-[0.6875rem] leading-relaxed text-blueprint/80">
            Chunks, entities and layout appear as each stage completes. Pages that need OCR are
            routed individually — a scan among four clean pages does not force a full re-OCR.
          </p>
        </Card>
      )}

      {doc?.status === "quarantined" && (
        <Card className="mt-4 border-warn/35 bg-warn-soft/40 p-3">
          <p className="flex items-center gap-2 text-sm font-medium text-warn">
            <ShieldAlertIcon className="size-4" />
            Quarantined before indexing
          </p>
          <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
            {doc.error_message ??
              "The ingestion screen held this document back. It was parsed but never chunked, embedded or made retrievable."}
          </p>
          {hasRole("steward", "admin") && (
            <>
              <Button
                size="sm"
                className="mt-2.5"
                onClick={() => setReleaseOpen(true)}
                disabled={release.isPending}
              >
                {release.isPending ? (
                  <Loader2Icon className="animate-spin" />
                ) : (
                  <ShieldCheckIcon />
                )}
                Release / re-ingest
              </Button>
              <AlertDialog open={releaseOpen} onOpenChange={setReleaseOpen}>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Release this document?</AlertDialogTitle>
                    <AlertDialogDescription>
                      It re-enters <strong>normal ingestion</strong> and is screened again. This
                      is not an exemption: if it still fails the content policy it will be
                      quarantined a second time. Your name and reason are written to the audit
                      log.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <div className="px-6">
                    <Label htmlFor="release-reason" className="text-xs">
                      Reason (optional)
                    </Label>
                    <Input
                      id="release-reason"
                      className="mt-1"
                      placeholder="Reviewed — it is a legitimate transmittal"
                      value={releaseReason}
                      onChange={(event: React.ChangeEvent<HTMLInputElement>) => setReleaseReason(event.target.value)}
                      disabled={release.isPending}
                    />
                  </div>
                  <AlertDialogFooter>
                    <AlertDialogCancel disabled={release.isPending}>Cancel</AlertDialogCancel>
                    <Button
                      onClick={() => release.mutate()}
                      disabled={release.isPending}
                    >
                      {release.isPending && <Loader2Icon className="animate-spin" />}
                      Release
                    </Button>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </>
          )}
        </Card>
      )}

      {doc?.status === "failed" && (
        <Card className="mt-4 border-error/35 bg-error-soft/40 p-3">
          <p className="flex items-center gap-2 text-sm font-medium text-error">
            <TriangleAlertIcon className="size-4" />
            Ingestion failed
          </p>
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-sm border border-error/25 bg-card/60 p-2.5 font-mono text-[0.6875rem] leading-relaxed text-error">
            {jobs.find((j) => j.status === "failed")?.error_message ??
              "No error message was recorded."}
          </pre>
          {hasRole("steward", "admin") && (
            <Button size="sm" className="mt-2.5" onClick={() => reprocess.mutate()}>
              <RefreshCwIcon />
              Retry ingestion
            </Button>
          )}
        </Card>
      )}

      {doc?.is_latest === false && (
        <Card className="mt-4 flex flex-wrap items-center gap-3 border-warn/35 bg-warn-soft/40 p-3">
          <TriangleAlertIcon className="size-4 shrink-0 text-warn" />
          <p className="min-w-0 flex-1 text-xs leading-relaxed text-warn">
            <b>This revision has been superseded.</b> Dimensions on this sheet may no longer be
            correct. It is excluded from search and from answers unless superseded revisions are
            explicitly included.
          </p>
          {doc.superseded_by_document_id && (
            <Button asChild size="sm" variant="outline">
              <Link to={`/documents/${doc.superseded_by_document_id}`}>Open current issue</Link>
            </Button>
          )}
        </Card>
      )}

      {/* -------------------------------------------------------- title block */}
      {doc && (
        <TitleBlock
          className="mt-4"
          columns={6}
          fields={[
            { label: "File type", value: doc.file_type.toUpperCase(), mono: true },
            { label: "Size", value: formatBytes(doc.file_size_bytes), mono: true },
            { label: "Pages", value: doc.page_count ?? "—", mono: true },
            { label: "Words", value: formatNumber(doc.word_count), mono: true },
            { label: "Chunks", value: formatNumber(chunks.length), mono: true },
            { label: "Uploaded", value: formatDate(doc.created_at), mono: true },
            { label: "Indexed", value: doc.indexed_at ? formatDate(doc.indexed_at, true) : "—", mono: true, span: 2 },
            { label: "Retention until", value: formatDate(doc.retention_until), mono: true },
            { label: "Project", value: doc.project_number ?? "Personal", mono: true },
            { label: "Drawing", value: doc.drawing_number ?? "—", mono: true },
            { label: "Revision", value: doc.revision_label ?? "—", mono: true },
          ]}
        />
      )}

      {/* ---------------------------------------------------------- content */}
      <div className="mt-6 grid gap-5 xl:grid-cols-[minmax(0,1fr)_26rem]">
        <div className="min-w-0">
          {doc ? (
            <SheetViewer
              document={doc}
              className="h-[38rem]"
              page={page}
              onPageChange={setPage}
              highlights={highlights}
              activeHighlightId={activeChunkId}
              onHighlightClick={(id) => setActiveChunkId(id)}
              downloadUrl={api.originalUrl(documentId)}
            />
          ) : (
            <Skeleton className="h-[38rem] w-full rounded-lg" />
          )}

          {doc && (
            <p className="mt-2 px-1">
              <ExactnessNote kind={doc.content_kind} />
            </p>
          )}
        </div>

        <div className="min-w-0">
          <Tabs defaultValue="chunks">
            <TabsList>
              <TabsTrigger value="chunks">
                <LayoutListIcon />
                Chunks
                <Badge variant="muted" className="ml-1 font-mono">
                  {chunks.length}
                </Badge>
              </TabsTrigger>
              <TabsTrigger value="entities">
                <BoxesIcon />
                Entities
                <Badge variant="muted" className="ml-1 font-mono">
                  {entities.length}
                </Badge>
              </TabsTrigger>
              <TabsTrigger value="intelligence">
                <BrainCircuitIcon />
                Intelligence
              </TabsTrigger>
              <TabsTrigger value="jobs">
                <HistoryIcon />
                Jobs
                <Badge variant="muted" className="ml-1 font-mono">
                  {jobs.length}
                </Badge>
              </TabsTrigger>
            </TabsList>

            {/* -------------------------------------------------- chunks */}
            <TabsContent value="chunks">
              {chunksQ.isPending ? (
                <TextSkeleton lines={8} />
              ) : chunks.length === 0 ? (
                <EmptyState
                  compact
                  icon={LayoutListIcon}
                  title="No chunks yet"
                  description="Chunks appear once ingestion reaches the chunking stage."
                />
              ) : (
                <ScrollArea className="max-h-[36rem]">
                  <div className="space-y-2 pr-2">
                    {chunks.map((chunk) => (
                      <ChunkCard
                        key={chunk.id}
                        chunk={chunk}
                        active={chunk.id === activeChunkId}
                        ocrFloor={ocrFloor}
                        onClick={() =>
                          setActiveChunkId((current) => (current === chunk.id ? null : chunk.id))
                        }
                      />
                    ))}
                  </div>
                </ScrollArea>
              )}
            </TabsContent>

            {/* ------------------------------------------------ entities */}
            <TabsContent value="entities">
              {entities.length === 0 ? (
                <EmptyState
                  compact
                  icon={BoxesIcon}
                  title="No steel entities recognised"
                  description="The extractor looks for section designations, grades, bolt and weld specifications, dimensions, load capacities and piece marks. A specification with none is unusual but possible."
                />
              ) : (
                <div className="space-y-4">
                  <Card className="space-y-2 bg-muted/30 p-3">
                    <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
                      Extraction runs regex and a gazetteer first, then an LLM only for what regex
                      is bad at. Regex always wins a tie, and an LLM designation that is neither in
                      the gazetteer nor present in the document text is dropped.
                    </p>
                  </Card>
                  {groupEntities(entities).map(([type, items]) => (
                    <div key={type} className="space-y-1.5">
                      <p className="eyebrow">{type.replace("_", " ")}</p>
                      <div className="flex flex-wrap gap-1.5">
                        {items.map((entity) => (
                          <EntityChip
                            key={entity.canonical}
                            canonical={entity.canonical}
                            type={entity.type}
                            confidence={entity.confidence}
                            source={entity.source}
                            onClick={() => {
                              const hit = chunks.find((c) =>
                                c.entities?.some((e) => e.canonical === entity.canonical),
                              )
                              if (hit) setActiveChunkId(hit.id)
                            }}
                          />
                        ))}
                      </div>
                    </div>
                  ))}
                  <Separator />
                  <Button asChild variant="outline" size="sm" className="w-full">
                    <Link
                      to={`/search?q=${encodeURIComponent(entities[0]?.canonical ?? "")}`}
                    >
                      Find these across the corpus
                    </Link>
                  </Button>
                </div>
              )}
            </TabsContent>

            {/* -------------------------------------------- intelligence */}
            <TabsContent value="intelligence">
              {intelQ.isError ? (
                <ErrorState
                  compact
                  error={intelQ.error}
                  resource="document intelligence"
                  onRetry={() => intelQ.refetch()}
                />
              ) : intelQ.isPending ? (
                <TextSkeleton lines={6} />
              ) : intel ? (
                <div className="space-y-4">
                  <Card className="p-3">
                    <p className="eyebrow mb-2">Pipeline</p>
                    <DataList
                      items={[
                        { label: "Chunking model", value: intel.embedding_model_chunking, mono: true },
                        { label: "Retrieval model", value: intel.embedding_model_retrieval, mono: true },
                        { label: "OCR engine", value: intel.ocr_ran ? intel.ocr_engine : "not run", mono: true },
                        {
                          label: "OCR time",
                          value: intel.ocr_ran ? formatMs(intel.ocr_processing_time_ms) : "—",
                          mono: true,
                        },
                        {
                          label: "Mean OCR confidence",
                          value: (
                            <ConfidenceMeter value={intel.ocr_confidence_avg} floor={ocrFloor} />
                          ),
                        },
                      ]}
                    />
                  </Card>

                  {intel.pages && intel.pages.length > 0 && (
                    <Card>
                      <div className="flex items-center gap-2 px-3 pt-3">
                        <ScanTextIcon className="size-3.5 text-muted-foreground" />
                        <p className="eyebrow">Per-page classification</p>
                      </div>
                      <p className="px-3 pb-2 pt-1.5 text-[0.6875rem] leading-relaxed text-muted-foreground">
                        Classified page by page, never as a whole. One PDF is routinely a
                        specification, a schedule, a plotted sheet and a scan — a document-wide
                        average over those is meaningless, and a summed text-layer count hides the
                        single scanned page completely.
                      </p>
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead numeric>Pg</TableHead>
                            <TableHead>Kind</TableHead>
                            <TableHead numeric>Native</TableHead>
                            <TableHead numeric>Loader</TableHead>
                            <TableHead>OCR</TableHead>
                            <TableHead numeric>Conf.</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {intel.pages.map((p) => (
                            <TableRow
                              key={p.page_number}
                              className="cursor-pointer"
                              onClick={() => setPage(p.page_number)}
                              data-state={page === p.page_number ? "selected" : undefined}
                            >
                              <TableCell numeric>{p.page_number}</TableCell>
                              <TableCell>
                                <ContentKindBadge kind={p.kind} compact />
                              </TableCell>
                              <TableCell numeric>
                                <Hint
                                  label={
                                    p.native_text_words === 0
                                      ? "No native text layer — this page is a scan. That is the only reliable scan-vs-plot discriminator, because Docling silently OCRs scanned pages and returns the result as ordinary extracted text."
                                      : "Words in the file's own text layer, read directly with pypdf."
                                  }
                                >
                                  <span className={cn(p.native_text_words === 0 && "text-warn")}>
                                    {p.native_text_words ?? "—"}
                                  </span>
                                </Hint>
                              </TableCell>
                              <TableCell numeric>{p.loader_words}</TableCell>
                              <TableCell>
                                {p.ocr_ran ? (
                                  <Badge variant="technical">ran</Badge>
                                ) : (
                                  <span className="text-muted-foreground">—</span>
                                )}
                              </TableCell>
                              <TableCell numeric>
                                {p.ocr_confidence != null ? p.ocr_confidence.toFixed(2) : "—"}
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </Card>
                  )}

                  <Card className="p-3">
                    <p className="eyebrow mb-2">Layout</p>
                    <div className="grid grid-cols-3 gap-2">
                      {[
                        ["Tables", intel.layout.tables_count],
                        ["Figures", intel.layout.figures_count],
                        ["Lists", intel.layout.lists_count],
                        ["Forms", intel.layout.forms_count],
                        ["Footnotes", intel.layout.footnotes_count],
                        ["Headings", intel.layout.headings.length],
                      ].map(([label, value]) => (
                        <div key={label as string} className="rounded-sm border border-border p-2">
                          <p className="text-[0.625rem] uppercase tracking-[0.06em] text-muted-foreground">
                            {label}
                          </p>
                          <p className="font-mono text-base tabular-nums">{value as number}</p>
                        </div>
                      ))}
                    </div>
                    {intel.layout.outline.length > 0 && (
                      <>
                        <p className="eyebrow mb-1.5 mt-3">Outline</p>
                        <ol className="space-y-0.5 text-[0.75rem]">
                          {intel.layout.outline.map((entry, i) => (
                            <li
                              key={i}
                              className="flex items-baseline justify-between gap-2 text-muted-foreground"
                              style={{ paddingLeft: `${(entry.level - 1) * 10}px` }}
                            >
                              <span className="truncate">{entry.text}</span>
                              <span className="shrink-0 font-mono text-[0.625rem]">
                                p.{entry.page}
                              </span>
                            </li>
                          ))}
                        </ol>
                      </>
                    )}
                  </Card>

                  {intel.semantic_graph.length > 0 && (
                    <Card className="p-3">
                      <p className="eyebrow mb-1.5">Semantic neighbours</p>
                      <p className="mb-2 text-[0.6875rem] leading-relaxed text-muted-foreground">
                        Chunk pairs whose embeddings sit close together. Useful for spotting a
                        detail repeated across sheets, or a schedule split over two pages.
                      </p>
                      <SimilarityGraph
                        edges={intel.semantic_graph.slice(0, 14)}
                        onSelect={setActiveChunkId}
                        activeId={activeChunkId}
                      />
                    </Card>
                  )}
                </div>
              ) : null}
            </TabsContent>

            {/* ---------------------------------------------------- jobs */}
            <TabsContent value="jobs">
              {jobs.length === 0 ? (
                <EmptyState
                  compact
                  icon={HistoryIcon}
                  title="No ingestion attempts recorded"
                  description="Documents ingested before the job queue existed have no history. Reprocessing creates one."
                />
              ) : (
                <div className="space-y-2">
                  {jobs.map((job) => (
                    <Card key={job.id} className="space-y-2 p-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <JobStatusBadge status={job.status} />
                        <span className="font-mono text-[0.6875rem] uppercase tracking-wide text-muted-foreground">
                          {job.job_type.replace(/_/g, " ")}
                        </span>
                        <span className="ml-auto font-mono text-[0.625rem] tabular-nums text-muted-foreground">
                          attempt {job.attempts}/{job.max_attempts}
                        </span>
                      </div>
                      <DataList
                        items={[
                          { label: "Queued", value: formatDate(job.created_at, true), mono: true },
                          {
                            label: "Finished",
                            value: job.finished_at ? formatDate(job.finished_at, true) : "—",
                            mono: true,
                          },
                          { label: "Stage", value: job.stage ?? "—", mono: true },
                        ]}
                      />
                      {job.error_message && (
                        <pre className="overflow-x-auto whitespace-pre-wrap rounded-sm border border-error/25 bg-error-soft/40 p-2 font-mono text-[0.625rem] leading-relaxed text-error">
                          {job.error_message}
                        </pre>
                      )}
                    </Card>
                  ))}
                </div>
              )}
            </TabsContent>
          </Tabs>
        </div>
      </div>

      {doc && (
        <ReclassifyDialog
          open={reclassifyOpen}
          onOpenChange={setReclassifyOpen}
          documentId={documentId}
          current={doc.sensitivity}
          fileName={doc.file_name}
          chunkCount={chunks.length}
        />
      )}
    </Page>
  )
}

/* ------------------------------------------------------------------ parts */

function ChunkCard({
  chunk,
  active,
  ocrFloor,
  onClick,
}: {
  chunk: Chunk
  active: boolean
  ocrFloor: number
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full rounded-md border bg-card p-2.5 text-left transition-colors",
        active ? "border-primary/60 bg-primary-soft/30" : "border-border hover:border-primary/35",
      )}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono text-[0.625rem] tabular-nums text-muted-foreground">
          #{chunk.position}
        </span>
        <Badge variant={chunk.chunk_type === "parent" ? "technical" : "muted"} className="font-mono">
          {chunk.chunk_type}
        </Badge>
        {chunk.section_title && (
          <span className="truncate text-[0.75rem] font-medium">{chunk.section_title}</span>
        )}
        <span className="ml-auto flex items-center gap-1.5">
          {chunk.page_number != null && (
            <span className="font-mono text-[0.625rem] text-muted-foreground">
              p.{chunk.page_number}
            </span>
          )}
          <PrecisionBadge precision={chunk.region_precision} />
        </span>
      </div>

      <p className="mt-1.5 line-clamp-3 whitespace-pre-line text-[0.75rem] leading-relaxed text-muted-foreground">
        {chunk.content}
      </p>

      <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-border pt-2">
        <span className="font-mono text-[0.625rem] tabular-nums text-muted-foreground">
          {chunk.token_count} tok
        </span>
        {chunk.ocr_confidence != null && (
          <ConfidenceMeter value={chunk.ocr_confidence} floor={ocrFloor} />
        )}
        {chunk.entities && chunk.entities.length > 0 && (
          <span className="ml-auto flex flex-wrap gap-1">
            {chunk.entities.slice(0, 3).map((entity) => (
              <EntityChip
                key={entity.canonical}
                canonical={entity.canonical}
                type={entity.type}
                confidence={entity.confidence}
                source={entity.source}
              />
            ))}
            {chunk.entities.length > 3 && (
              <span className="self-center text-[0.625rem] text-muted-foreground">
                +{chunk.entities.length - 3}
              </span>
            )}
          </span>
        )}
      </div>
    </button>
  )
}

/** Similarity edges as a small force-free chord list — legible at this size. */
function SimilarityGraph({
  edges,
  onSelect,
  activeId,
}: {
  edges: { chunk_id_a: string; chunk_id_b: string; similarity: number }[]
  onSelect: (id: string) => void
  activeId: string | null
}) {
  return (
    <ul className="space-y-1">
      {edges.map((edge, i) => (
        <li key={i} className="flex items-center gap-2">
          <button
            onClick={() => onSelect(edge.chunk_id_a)}
            className={cn(
              "shrink-0 rounded-sm border border-border px-1 font-mono text-[0.625rem] transition-colors hover:border-primary hover:text-primary",
              activeId === edge.chunk_id_a && "border-primary bg-primary-soft text-primary",
            )}
          >
            {edge.chunk_id_a.slice(0, 6)}
          </button>
          <span className="relative h-1 min-w-0 flex-1 overflow-hidden rounded-full bg-muted">
            <span
              className="absolute inset-y-0 left-0 rounded-full bg-chart-2"
              style={{ width: `${((edge.similarity - 0.5) / 0.5) * 100}%` }}
            />
          </span>
          <span className="shrink-0 font-mono text-[0.625rem] tabular-nums text-muted-foreground">
            {edge.similarity.toFixed(3)}
          </span>
          <button
            onClick={() => onSelect(edge.chunk_id_b)}
            className={cn(
              "shrink-0 rounded-sm border border-border px-1 font-mono text-[0.625rem] transition-colors hover:border-primary hover:text-primary",
              activeId === edge.chunk_id_b && "border-primary bg-primary-soft text-primary",
            )}
          >
            {edge.chunk_id_b.slice(0, 6)}
          </button>
        </li>
      ))}
    </ul>
  )
}

function ReclassifyDialog({
  open,
  onOpenChange,
  documentId,
  current,
  fileName,
  chunkCount,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  documentId: string
  current: Sensitivity
  fileName: string
  chunkCount: number
}) {
  const queryClient = useQueryClient()
  const [next, setNext] = React.useState<Sensitivity>(current)
  const [reason, setReason] = React.useState("")

  React.useEffect(() => {
    if (open) {
      setNext(current)
      setReason("")
    }
  }, [open, current])

  const mutate = useMutation({
    mutationFn: () => api.reclassifyDocument(documentId, next, reason),
    onSuccess: () => {
      toast.success("Classification changed", {
        description: `Propagated to ${chunkCount} chunks across Postgres, Qdrant and Elasticsearch, and the answer cache was invalidated.`,
      })
      void queryClient.invalidateQueries({ queryKey: ["document", documentId] })
      void queryClient.invalidateQueries({ queryKey: ["documents"] })
      onOpenChange(false)
    },
    onError: (error) => toast.error("Could not reclassify", { description: message(error) }),
  })

  const LEVEL: Record<Sensitivity, number> = {
    public: 0,
    internal: 1,
    confidential: 2,
    restricted: 3,
  }
  const loosening = LEVEL[next] < LEVEL[current]

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Reclassify document</DialogTitle>
          <DialogDescription className="truncate">{fileName}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <div className="space-y-1">
              <Label>Currently</Label>
              <SensitivityBadge value={current} />
            </div>
            <span className="mt-5 text-muted-foreground">→</span>
            <div className="flex-1 space-y-1">
              <Label htmlFor="new-classification">Change to</Label>
              <Select value={next} onValueChange={(v) => setNext(v as Sensitivity)}>
                <SelectTrigger id="new-classification">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(["public", "internal", "confidential", "restricted"] as Sensitivity[]).map(
                    (value) => (
                      <SelectItem key={value} value={value} className="capitalize">
                        {value}
                      </SelectItem>
                    ),
                  )}
                </SelectContent>
              </Select>
            </div>
          </div>

          {loosening && (
            <p className="flex items-start gap-2 rounded-md border border-warn/35 bg-warn-soft/40 p-2.5 text-[0.6875rem] leading-relaxed text-warn">
              <TriangleAlertIcon className="mt-px size-3.5 shrink-0" />
              <span>
                You are widening access. Reclassifying downward is the one action in this system
                that turns restricted data into broadly-readable data, so it is recorded with both
                the old and new values against your name.
              </span>
            </p>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="reclassify-reason">Reason</Label>
            <Textarea
              id="reclassify-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={2}
              placeholder="Client NDA expired; released for construction; misfiled on upload…"
            />
            <p className="text-[0.6875rem] text-muted-foreground">
              Written to the audit log with control id C-MAP-02.
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => mutate.mutate()}
            disabled={next === current || mutate.isPending}
            variant={loosening ? "destructive" : "default"}
          >
            Apply and re-propagate
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/* ----------------------------------------------------------------- utils */

function aggregateEntities(chunks: Chunk[]): SteelEntity[] {
  const map = new Map<string, SteelEntity>()
  for (const chunk of chunks) {
    for (const entity of chunk.entities ?? []) {
      const existing = map.get(entity.canonical)
      if (existing) {
        existing.occurrences = (existing.occurrences ?? 1) + (entity.occurrences ?? 1)
        existing.confidence = Math.max(existing.confidence, entity.confidence)
      } else {
        map.set(entity.canonical, { ...entity })
      }
    }
  }
  return [...map.values()].sort(
    (a, b) => b.confidence - a.confidence || a.canonical.localeCompare(b.canonical),
  )
}

/** Grouped in the order an engineer reads a schedule; unknown types last. */
const ENTITY_ORDER = ["section", "grade", "bolt", "weld", "mark", "dimension", "load"]

function groupEntities(entities: SteelEntity[]): [string, SteelEntity[]][] {
  const groups = new Map<string, SteelEntity[]>()
  for (const entity of entities) {
    const type = entity.type || entityType(entity.canonical)
    const bucket = groups.get(type) ?? []
    bucket.push(entity)
    groups.set(type, bucket)
  }
  const rank = (type: string) => {
    const index = ENTITY_ORDER.indexOf(type)
    return index === -1 ? ENTITY_ORDER.length : index
  }
  return [...groups.entries()].sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
}

function message(error: unknown): string | undefined {
  if (error instanceof ApiError) return error.detail
  return error instanceof Error ? error.message : undefined
}
