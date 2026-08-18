import * as React from "react"
import { Link, useSearchParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  ArrowRightIcon,
  CheckCircle2Icon,
  FileIcon,
  FolderKanbanIcon,
  TriangleAlertIcon,
  UploadCloudIcon,
  XIcon,
} from "lucide-react"
import { api, ApiError } from "@/api"
import type { Sensitivity } from "@/api/types"
import { useAuth } from "@/lib/auth"
import { cn, formatBytes } from "@/lib/utils"
import { Page } from "@/components/layout/page"
import { PageHeader, Section } from "@/components/domain/layout"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Label, Progress, Separator } from "@/components/ui/misc"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Hint } from "@/components/ui/tooltip"
import { SensitivityBadge } from "@/components/domain/badges"

const ACCEPTED = [
  "pdf", "docx", "doc", "txt", "md", "png", "jpg", "jpeg", "tiff", "dxf", "dwg", "xlsx", "csv",
]

interface Staged {
  id: string
  file: File
  drawingNumber: string
  revisionLabel: string
  progress: number
  status: "staged" | "uploading" | "done" | "error"
  error?: string
  documentId?: string
}

/** Filenames like `CG4-S-104-01_RevD.pdf` carry their own metadata. */
function guessMetadata(name: string): { drawingNumber: string; revisionLabel: string } {
  const base = name.replace(/\.[^.]+$/, "")
  const rev = base.match(/[_\-\s]rev[\s_\-.]?([A-Z]{1,2}\d{0,3}|\d{1,3})/i)
  const drawing = base.match(/^([A-Z]{1,4}\d?[-_][A-Z]{1,2}[-_]?\d{2,4})/i)
  return {
    drawingNumber: drawing ? drawing[1].replace(/_/g, "-").toUpperCase() : "",
    revisionLabel: rev ? rev[1].toUpperCase() : "",
  }
}

export default function IngestPage() {
  const [searchParams] = useSearchParams()
  const { principal } = useAuth()
  const queryClient = useQueryClient()

  const [staged, setStaged] = React.useState<Staged[]>([])
  const [projectId, setProjectId] = React.useState(searchParams.get("project") ?? "none")
  const [domain, setDomain] = React.useState("drawing")
  const [tags, setTags] = React.useState("")
  const [sensitivity, setSensitivity] = React.useState<Sensitivity | "">("")
  const [dragging, setDragging] = React.useState(false)

  const { data: projects = [] } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.listProjects(),
  })
  const { data: flags = [] } = useQuery({
    queryKey: ["flags"],
    queryFn: () => api.listFlags(),
  })

  const ingestionEnabled = flags.find((f) => f.name === "ingestion_enabled")?.enabled ?? true
  const project = projects.find((p) => p.id === projectId)

  // A document cannot be classified above its uploader's own clearance —
  // otherwise upload would be a way to create data you cannot then review.
  const LEVEL: Record<Sensitivity, number> = {
    public: 0, internal: 1, confidential: 2, restricted: 3,
  }
  const allowedSensitivities = (
    ["public", "internal", "confidential", "restricted"] as Sensitivity[]
  ).filter((value) => principal && LEVEL[value] <= LEVEL[principal.clearance])

  const effectiveSensitivity =
    sensitivity || project?.default_sensitivity || "internal"

  function addFiles(files: FileList | File[]) {
    const next: Staged[] = Array.from(files).map((file) => {
      const guessed = guessMetadata(file.name)
      return {
        id: `${file.name}-${file.size}-${Math.random().toString(36).slice(2, 8)}`,
        file,
        drawingNumber: guessed.drawingNumber,
        revisionLabel: guessed.revisionLabel,
        progress: 0,
        status: "staged",
      }
    })
    setStaged((current) => [...current, ...next])
  }

  const upload = useMutation({
    mutationFn: async () => {
      for (const item of staged.filter((s) => s.status === "staged")) {
        setStaged((current) =>
          current.map((s) => (s.id === item.id ? { ...s, status: "uploading" } : s)),
        )
        try {
          const result = await api.uploadDocument({
            file: item.file,
            domain,
            tags,
            sensitivity: sensitivity || "",
            project_id: projectId === "none" ? undefined : projectId,
            drawing_number: item.drawingNumber || undefined,
            revision_label: item.revisionLabel || undefined,
            onProgress: (fraction) =>
              setStaged((current) =>
                current.map((s) =>
                  s.id === item.id ? { ...s, progress: Math.round(fraction * 100) } : s,
                ),
              ),
          })
          setStaged((current) =>
            current.map((s) =>
              s.id === item.id
                ? { ...s, status: "done", progress: 100, documentId: result.document_id }
                : s,
            ),
          )
        } catch (error) {
          setStaged((current) =>
            current.map((s) =>
              s.id === item.id
                ? {
                    ...s,
                    status: "error",
                    error: error instanceof ApiError ? error.detail : String(error),
                  }
                : s,
            ),
          )
        }
      }
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["documents"] })
      void queryClient.invalidateQueries({ queryKey: ["jobs"] })
      const failed = staged.filter((s) => s.status === "error").length
      if (failed === 0) toast.success("Accepted for ingestion", {
        description: "Watch the queue for stage-by-stage progress.",
      })
    },
  })

  const pending = staged.filter((s) => s.status === "staged")
  const completed = staged.filter((s) => s.status === "done")
  const failed = staged.filter((s) => s.status === "error")

  return (
    <Page width="reading">
      <PageHeader
        eyebrow="Ingestion"
        title="Upload documents"
        description="Drawings, specifications, schedules, weld procedures and scanned bundles. Each page is classified on its own, so one PDF can be a specification, a schedule, a plotted sheet and a scan without any of them being mishandled."
        actions={
          <Button asChild variant="outline" size="sm">
            <Link to="/ingest/queue">
              Queue
              <ArrowRightIcon />
            </Link>
          </Button>
        }
      />

      {!ingestionEnabled && (
        <Card className="mt-4 flex items-start gap-2.5 border-error/35 bg-error-soft/40 p-3">
          <TriangleAlertIcon className="mt-0.5 size-4 shrink-0 text-error" />
          <div className="space-y-0.5 text-error">
            <p className="text-sm font-medium">Ingestion is paused</p>
            <p className="text-xs leading-relaxed opacity-90">
              An administrator has turned off the <code className="font-mono">ingestion_enabled</code>{" "}
              flag. Uploads will be refused with a 503 until it is turned back on. The queue
              continues to drain.
            </p>
          </div>
        </Card>
      )}

      {/* ------------------------------------------------------- dropzone */}
      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          addFiles(e.dataTransfer.files)
        }}
        className={cn(
          "relative mt-5 overflow-hidden rounded-lg border-2 border-dashed transition-colors",
          dragging ? "border-primary bg-primary-soft/40" : "border-border",
        )}
      >
        <div className="bg-grid grid-fade pointer-events-none absolute inset-0 opacity-60" aria-hidden />
        <label className="relative flex cursor-pointer flex-col items-center gap-3 px-6 py-10 text-center">
          <span className="flex size-11 items-center justify-center rounded-md border border-border bg-card">
            <UploadCloudIcon className={cn("size-5", dragging ? "text-primary" : "text-muted-foreground")} />
          </span>
          <span className="space-y-1">
            <span className="block text-sm font-semibold tracking-tight">
              Drop files here, or click to choose
            </span>
            <span className="block text-xs text-muted-foreground">
              {ACCEPTED.map((e) => e.toUpperCase()).join(" · ")} — up to 200 MB each
            </span>
          </span>
          <input
            type="file"
            multiple
            accept={ACCEPTED.map((e) => `.${e}`).join(",")}
            className="sr-only"
            onChange={(e) => e.target.files && addFiles(e.target.files)}
          />
        </label>
      </div>

      <p className="mt-2 px-1 text-[0.6875rem] leading-relaxed text-muted-foreground">
        <b className="text-foreground">DWG needs a converter.</b> DXF is read natively with ezdxf.
        DWG requires the operator-installed ODA File Converter — its licence forbids redistribution,
        so it cannot be baked into the image. Without it, a DWG upload fails with an actionable
        error rather than crashing the worker.
      </p>

      {/* --------------------------------------------------------- metadata */}
      <Section
        className="mt-6"
        title="Classification and filing"
        description="Applied to every file in this batch. Classification happens at the point of entry, not after indexing — the window between the two is exactly when an unclassified document is retrievable."
      >
        <Card className="grid gap-4 p-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="project">Project</Label>
            <Select value={projectId} onValueChange={setProjectId}>
              <SelectTrigger id="project">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">No project — personal</SelectItem>
                {projects.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.project_number} — {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
              {projectId === "none"
                ? "A document with no project is visible only to you."
                : "Every member of this project will be able to read it, subject to their clearance."}
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="sensitivity">Classification</Label>
            <Select
              value={sensitivity || "default"}
              onValueChange={(v) => setSensitivity(v === "default" ? "" : (v as Sensitivity))}
            >
              <SelectTrigger id="sensitivity">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="default">
                  {project?.default_sensitivity
                    ? `Project default — ${project.default_sensitivity}`
                    : "Policy default — internal"}
                </SelectItem>
                {allowedSensitivities.map((value) => (
                  <SelectItem key={value} value={value} className="capitalize">
                    {value}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="flex items-center gap-1.5 text-[0.6875rem] text-muted-foreground">
              Will be filed as <SensitivityBadge value={effectiveSensitivity} size="sm" />
              {principal && allowedSensitivities.length < 4 && (
                <Hint label={`You hold ${principal.clearance} clearance. You cannot classify a document above it, or you would create data you are unable to review.`}>
                  <span className="underline decoration-dotted">why the short list?</span>
                </Hint>
              )}
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="domain">Domain</Label>
            <Select value={domain} onValueChange={setDomain}>
              <SelectTrigger id="domain">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[
                  "drawing", "design-basis", "specification", "calculations", "fabrication",
                  "erection", "qa-qc", "correspondence", "as-built", "archive", "cad", "general",
                ].map((value) => (
                  <SelectItem key={value} value={value}>
                    {value}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="tags">Tags</Label>
            <Input
              id="tags"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="IS 800, bay-2, urgent"
            />
            <p className="text-[0.6875rem] text-muted-foreground">
              Comma-separated. Recognised steel entities are appended automatically during
              extraction.
            </p>
          </div>
        </Card>
      </Section>

      {/* ---------------------------------------------------------- staged */}
      {staged.length > 0 && (
        <Section
          className="mt-6"
          title={`${staged.length} ${staged.length === 1 ? "file" : "files"} staged`}
          description="Drawing number and revision are guessed from the filename where possible — correct them before uploading, because they decide which drawing this file becomes a revision of."
          actions={
            <Button variant="ghost" size="sm" onClick={() => setStaged([])}>
              Clear all
            </Button>
          }
        >
          <div className="space-y-2">
            {staged.map((item) => (
              <Card key={item.id} className="p-3">
                <div className="flex items-start gap-3">
                  <span
                    className={cn(
                      "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-sm border",
                      item.status === "done"
                        ? "border-ok/40 bg-ok-soft text-ok"
                        : item.status === "error"
                          ? "border-error/40 bg-error-soft text-error"
                          : "border-border bg-muted text-muted-foreground",
                    )}
                  >
                    {item.status === "done" ? (
                      <CheckCircle2Icon className="size-4" />
                    ) : item.status === "error" ? (
                      <TriangleAlertIcon className="size-4" />
                    ) : (
                      <FileIcon className="size-4" />
                    )}
                  </span>

                  <div className="min-w-0 flex-1 space-y-2">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="truncate text-[0.8125rem] font-medium">{item.file.name}</p>
                        <p className="font-mono text-[0.6875rem] text-muted-foreground">
                          {formatBytes(item.file.size)} ·{" "}
                          {item.file.name.split(".").pop()?.toUpperCase()}
                        </p>
                      </div>
                      {item.status === "staged" && (
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          onClick={() =>
                            setStaged((current) => current.filter((s) => s.id !== item.id))
                          }
                          aria-label="Remove"
                        >
                          <XIcon />
                        </Button>
                      )}
                      {item.status === "done" && item.documentId && (
                        <Button asChild variant="ghost" size="xs">
                          <Link to={`/documents/${item.documentId}`}>
                            Open <ArrowRightIcon />
                          </Link>
                        </Button>
                      )}
                    </div>

                    {item.status === "staged" && (
                      <div className="grid gap-2 sm:grid-cols-2">
                        <div className="space-y-1">
                          <Label
                            htmlFor={`dn-${item.id}`}
                            className="text-[0.625rem] uppercase tracking-[0.06em] text-muted-foreground"
                          >
                            Drawing number
                          </Label>
                          <Input
                            id={`dn-${item.id}`}
                            value={item.drawingNumber}
                            onChange={(e) =>
                              setStaged((current) =>
                                current.map((s) =>
                                  s.id === item.id ? { ...s, drawingNumber: e.target.value } : s,
                                ),
                              )
                            }
                            placeholder="Leave blank for non-drawings"
                            className="h-8 font-mono text-[0.8125rem]"
                          />
                        </div>
                        <div className="space-y-1">
                          <Label
                            htmlFor={`rev-${item.id}`}
                            className="text-[0.625rem] uppercase tracking-[0.06em] text-muted-foreground"
                          >
                            Revision
                          </Label>
                          <Input
                            id={`rev-${item.id}`}
                            value={item.revisionLabel}
                            onChange={(e) =>
                              setStaged((current) =>
                                current.map((s) =>
                                  s.id === item.id ? { ...s, revisionLabel: e.target.value } : s,
                                ),
                              )
                            }
                            placeholder="A, 0, P1, C1…"
                            className="h-8 font-mono text-[0.8125rem]"
                          />
                        </div>
                      </div>
                    )}

                    {item.status === "uploading" && <Progress value={item.progress} />}

                    {item.status === "error" && (
                      <p className="rounded-sm border border-error/25 bg-error-soft/40 px-2 py-1.5 text-[0.6875rem] leading-relaxed text-error">
                        {item.error}
                      </p>
                    )}

                    {item.status === "done" && (
                      <p className="text-[0.6875rem] text-ok">
                        Accepted — queued for ingestion.
                      </p>
                    )}
                  </div>
                </div>
              </Card>
            ))}
          </div>

          <Separator className="my-4" />

          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">
              {completed.length > 0 && (
                <Badge variant="ok" className="mr-2">
                  {completed.length} accepted
                </Badge>
              )}
              {failed.length > 0 && (
                <Badge variant="error" className="mr-2">
                  {failed.length} failed
                </Badge>
              )}
              {pending.length > 0 && `${pending.length} ready to upload`}
            </p>
            <div className="flex gap-2">
              {completed.length > 0 && (
                <Button asChild variant="outline" size="sm">
                  <Link to="/ingest/queue">Watch the queue</Link>
                </Button>
              )}
              <Button
                onClick={() => upload.mutate()}
                disabled={pending.length === 0 || upload.isPending || !ingestionEnabled}
              >
                <UploadCloudIcon />
                Upload {pending.length > 0 && `${pending.length} `}
                {pending.length === 1 ? "file" : "files"}
              </Button>
            </div>
          </div>
        </Section>
      )}

      {staged.length === 0 && (
        <Section className="mt-6" title="What happens next">
          <Card className="divide-y divide-border">
            {[
              ["Load", "Docling, Unstructured or ezdxf, chosen by file type. CAD constructs map onto the same intermediate representation as everything else — no parallel pipeline."],
              ["Classify", "Each page is judged separately: native text layer, sentence structure, title-block markers, dimension callouts, table grids and image coverage. Deterministic — no model call."],
              ["OCR", "Routed only to pages that need it. A scan among four clean pages does not force a re-OCR of the other four."],
              ["Extract", "Section designations, grades, bolts, welds, dimensions, loads and piece marks. Regex and gazetteer first, LLM only for what regex is bad at."],
              ["Chunk", "Drawings and schedules skip semantic chunking — they have no sentences — and emit one chunk per semantic unit, plus a sheet summary per page."],
              ["Embed & index", "bge-m3 for chunking, text-embedding-3-large for retrieval. Written to Postgres, Qdrant and Elasticsearch with the classification denormalized onto every chunk."],
            ].map(([stage, detail], i) => (
              <div key={stage} className="flex gap-3 px-3.5 py-2.5">
                <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-muted font-mono text-[0.625rem] tabular-nums">
                  {i + 1}
                </span>
                <div className="min-w-0 space-y-0.5">
                  <p className="text-[0.8125rem] font-medium">{stage}</p>
                  <p className="text-pretty text-[0.6875rem] leading-relaxed text-muted-foreground">
                    {detail}
                  </p>
                </div>
              </div>
            ))}
          </Card>
        </Section>
      )}

      {projects.length === 0 && (
        <Card className="mt-6 flex items-center gap-3 bg-muted/40 p-3">
          <FolderKanbanIcon className="size-4 shrink-0 text-muted-foreground" />
          <p className="text-xs text-muted-foreground">
            You are not a member of any project, so uploads will be filed as personal documents,
            visible only to you.
          </p>
        </Card>
      )}
    </Page>
  )
}
