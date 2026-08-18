import * as React from "react"
import { Link } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  ActivityIcon,
  CheckCircle2Icon,
  RefreshCwIcon,
  TriangleAlertIcon,
  UploadCloudIcon,
} from "lucide-react"
import { api } from "@/api"
import type { IngestionStage, Job, JobStatus } from "@/api/types"
import { useAuth } from "@/lib/auth"
import { cn, formatMs, timeAgo } from "@/lib/utils"
import { Page } from "@/components/layout/page"
import { PageHeader, StatTile } from "@/components/domain/layout"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Progress, SegmentedTrack, ToggleGroup, ToggleGroupItem } from "@/components/ui/misc"
import { Hint } from "@/components/ui/tooltip"
import { JobStatusBadge } from "@/components/domain/badges"
import { EmptyState, ErrorState, TextSkeleton } from "@/components/domain/states"

/** Stage order, matching the ingestion pipeline. */
const STAGES: { key: IngestionStage; label: string; note: string }[] = [
  { key: "load", label: "Load", note: "Docling / Unstructured / ezdxf, chosen by file type" },
  { key: "classify", label: "Classify", note: "Per page: text layer, sentence structure, title-block markers" },
  { key: "ocr", label: "OCR", note: "Only the pages that need it" },
  { key: "layout", label: "Layout", note: "Headings, tables, figures, reading order" },
  { key: "extract", label: "Extract", note: "Sections, grades, bolts, welds, marks" },
  { key: "chunk", label: "Chunk", note: "Structure → semantic → parent/child → validate" },
  { key: "embed", label: "Embed", note: "bge-m3 for chunking, text-embedding-3-large for retrieval" },
  { key: "index", label: "Index", note: "Postgres, Qdrant and Elasticsearch" },
]

export default function QueuePage() {
  const { hasRole } = useAuth()
  const queryClient = useQueryClient()
  const [filter, setFilter] = React.useState<JobStatus[]>([])

  const jobsQ = useQuery({
    queryKey: ["jobs", filter],
    queryFn: () => api.listJobs({ status: filter.length ? filter : undefined, limit: 200 }),
    refetchInterval: 2500,
  })

  const jobs = jobsQ.data ?? []
  const running = jobs.filter((j) => j.status === "running")
  const queued = jobs.filter((j) => j.status === "queued")
  const failed = jobs.filter((j) => j.status === "failed")
  const succeeded = jobs.filter((j) => j.status === "succeeded")

  const retry = useMutation({
    mutationFn: (documentId: string) => api.reprocessDocument(documentId),
    onSuccess: (result) => {
      toast[result.status === "already_running" ? "info" : "success"](
        result.status === "already_running" ? "Already in flight" : "Requeued",
        { description: result.message },
      )
      void queryClient.invalidateQueries({ queryKey: ["jobs"] })
      void queryClient.invalidateQueries({ queryKey: ["documents"] })
    },
  })

  return (
    <Page>
      <PageHeader
        eyebrow="Ingestion"
        title="Job queue"
        description="Ingestion runs out of process on an arq worker, not in the request. A drawing set is minutes of CPU-bound work — CAD parsing, OCR, layout inference, embedding — and running it inline would let one upload starve every other request."
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => queryClient.invalidateQueries({ queryKey: ["jobs"] })}
            >
              <RefreshCwIcon className={cn(jobsQ.isFetching && "animate-spin")} />
              Refresh
            </Button>
            <Button asChild size="sm">
              <Link to="/ingest">
                <UploadCloudIcon />
                Upload
              </Link>
            </Button>
          </>
        }
      />

      <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label="Running"
          value={running.length}
          icon={ActivityIcon}
          tone={running.length ? "technical" : "default"}
          footer="Concurrency is bounded by the worker pool."
        />
        <StatTile label="Waiting" value={queued.length} footer="Picked up in order as slots free." />
        <StatTile
          label="Failed"
          value={failed.length}
          tone={failed.length ? "error" : "default"}
          icon={failed.length ? TriangleAlertIcon : undefined}
          footer="Exhausted their retries and need a decision."
        />
        <StatTile
          label="Succeeded"
          value={succeeded.length}
          tone="ok"
          icon={CheckCircle2Icon}
          footer="Indexed and searchable."
        />
      </div>

      <div className="mt-5 flex flex-wrap items-center justify-between gap-2">
        <SegmentedTrack>
          <ToggleGroup
            type="multiple"
            value={filter}
            onValueChange={(v) => setFilter(v as JobStatus[])}
          >
            {(["running", "queued", "failed", "succeeded", "skipped"] as JobStatus[]).map((s) => (
              <ToggleGroupItem key={s} value={s} className="capitalize">
                {s}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </SegmentedTrack>
        <p className="font-mono text-[0.6875rem] tabular-nums text-muted-foreground">
          auto-refreshing every 2.5 s
        </p>
      </div>

      <div className="mt-4 space-y-2">
        {jobsQ.isPending ? (
          <TextSkeleton lines={6} />
        ) : jobsQ.isError ? (
          <ErrorState error={jobsQ.error} onRetry={jobsQ.refetch} resource="the job queue" />
        ) : jobs.length === 0 ? (
          <EmptyState
            icon={ActivityIcon}
            title={filter.length ? "No jobs in that state" : "The queue is empty"}
            description={
              filter.length
                ? "Clear the filter to see everything that has run."
                : "Nothing is waiting, running or failed. Upload a document and its ingestion will appear here stage by stage."
            }
            action={
              filter.length ? (
                <Button size="sm" variant="outline" onClick={() => setFilter([])}>
                  Clear filter
                </Button>
              ) : (
                <Button asChild size="sm">
                  <Link to="/ingest">Upload documents</Link>
                </Button>
              )
            }
          />
        ) : (
          jobs.map((job) => (
            <JobCard
              key={job.id}
              job={job}
              canRetry={hasRole("steward", "admin")}
              onRetry={() => job.document_id && retry.mutate(job.document_id)}
            />
          ))
        )}
      </div>
    </Page>
  )
}

function JobCard({
  job,
  canRetry,
  onRetry,
}: {
  job: Job
  canRetry: boolean
  onRetry: () => void
}) {
  const stageIndex = STAGES.findIndex((s) => s.key === job.stage)
  const duration =
    job.started_at && job.finished_at
      ? new Date(job.finished_at).getTime() - new Date(job.started_at).getTime()
      : job.started_at && job.status === "running"
        ? Date.now() - new Date(job.started_at).getTime()
        : null

  return (
    <Card
      className={cn(
        "overflow-hidden",
        job.status === "failed" && "border-error/35",
        job.status === "running" && "border-blueprint/35",
      )}
    >
      <div className="flex flex-wrap items-center gap-2.5 px-3.5 py-2.5">
        <JobStatusBadge status={job.status} />
        <Link
          to={`/documents/${job.document_id}`}
          className="min-w-0 flex-1 truncate text-[0.8125rem] font-medium hover:text-primary"
        >
          {job.document_name}
        </Link>
        <Badge variant="outline" className="font-mono uppercase">
          {job.job_type.replace("_document", "")}
        </Badge>
        <Hint label="Retries are bounded; a job that exhausts them is left failed rather than looping.">
          <span className="font-mono text-[0.6875rem] tabular-nums text-muted-foreground">
            {job.attempts}/{job.max_attempts}
          </span>
        </Hint>
        {duration != null && (
          <span className="w-16 text-right font-mono text-[0.6875rem] tabular-nums text-muted-foreground">
            {formatMs(duration)}
          </span>
        )}
        <span className="w-16 text-right font-mono text-[0.6875rem] tabular-nums text-muted-foreground">
          {timeAgo(job.created_at)}
        </span>
        {job.status === "failed" && canRetry && (
          <Button variant="outline" size="xs" onClick={onRetry}>
            <RefreshCwIcon />
            Retry
          </Button>
        )}
      </div>

      {(job.status === "running" || job.status === "failed") && (
        <div className="border-t border-border bg-muted/25 px-3.5 py-2.5">
          <StageTrack currentIndex={stageIndex} failed={job.status === "failed"} />
          {job.status === "running" && <Progress value={job.progress ?? 0} className="mt-2.5" />}
          {job.error_message && (
            <pre className="mt-2.5 overflow-x-auto whitespace-pre-wrap rounded-sm border border-error/25 bg-error-soft/40 p-2 font-mono text-[0.6875rem] leading-relaxed text-error">
              {job.error_message}
            </pre>
          )}
        </div>
      )}

      {job.status === "skipped" && job.error_message && (
        <div className="border-t border-border bg-warn-soft/30 px-3.5 py-2 text-[0.6875rem] leading-relaxed text-warn">
          {job.error_message} — not an error, and nothing to alert on.
        </div>
      )}
    </Card>
  )
}

/** The pipeline as a track; the failed stage is marked, not just the job. */
function StageTrack({ currentIndex, failed }: { currentIndex: number; failed: boolean }) {
  return (
    <ol className="flex flex-wrap items-center gap-x-1 gap-y-1.5">
      {STAGES.map((stage, i) => {
        const done = currentIndex > i
        const active = currentIndex === i
        return (
          <React.Fragment key={stage.key}>
            {i > 0 && (
              <span
                className={cn("h-px w-3 shrink-0", done ? "bg-ok" : "bg-border")}
                aria-hidden
              />
            )}
            <Hint label={stage.note}>
              <li
                className={cn(
                  "flex shrink-0 items-center gap-1 rounded-sm border px-1.5 py-0.5 font-mono text-[0.625rem] uppercase tracking-wide",
                  done && "border-ok/35 bg-ok-soft text-ok",
                  active && !failed && "border-blueprint/45 bg-blueprint-soft text-blueprint",
                  active && failed && "border-error/45 bg-error-soft text-error",
                  !done && !active && "border-border text-muted-foreground",
                )}
              >
                {done && <CheckCircle2Icon className="size-2.5" />}
                {active && failed && <TriangleAlertIcon className="size-2.5" />}
                {stage.label}
              </li>
            </Hint>
          </React.Fragment>
        )
      })}
    </ol>
  )
}
