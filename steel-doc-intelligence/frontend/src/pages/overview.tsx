import { Link } from "react-router-dom"
import { useQueries } from "@tanstack/react-query"
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from "recharts"
import {
  ActivityIcon,
  AlertTriangleIcon,
  ArrowRightIcon,
  ClipboardListIcon,
  CpuIcon,
  DatabaseIcon,
  FileStackIcon,
  FolderKanbanIcon,
  GaugeCircleIcon,
  MessagesSquareIcon,
  SearchIcon,
  UploadCloudIcon,
} from "lucide-react"
import { api } from "@/api"
import { useAuth } from "@/lib/auth"
import { cn, formatMs, formatNumber, formatPercent, timeAgo } from "@/lib/utils"
import { Page } from "@/components/layout/page"
import { PageHeader, Section, StatTile } from "@/components/domain/layout"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/misc"
import { Hint } from "@/components/ui/tooltip"
import {
  ContentKindBadge,
  DocumentStatusBadge,
  DrawingNumber,
  ProjectStatusBadge,
  RevisionChip,
  SensitivityBar,
} from "@/components/domain/badges"
import { EmptyState, TileRowSkeleton } from "@/components/domain/states"
import { ChartFrame, chartAxis, chartGrid, chartTooltip } from "@/components/domain/chart"

export default function OverviewPage() {
  const { principal } = useAuth()

  const [metricsQ, healthQ, gateQ, onlineQ, projectsQ, drawingsQ, jobsQ, docsQ, convosQ] =
    useQueries({
      queries: [
        { queryKey: ["metrics"], queryFn: () => api.systemMetrics(), refetchInterval: 15_000 },
        { queryKey: ["health"], queryFn: () => api.health(), refetchInterval: 30_000 },
        { queryKey: ["gate"], queryFn: () => api.evaluationGate() },
        { queryKey: ["online", 24], queryFn: () => api.onlineMetrics(24) },
        { queryKey: ["projects"], queryFn: () => api.listProjects() },
        { queryKey: ["drawings"], queryFn: () => api.listDrawings() },
        { queryKey: ["jobs"], queryFn: () => api.listJobs({ limit: 50 }), refetchInterval: 4_000 },
        {
          queryKey: ["documents", "recent"],
          queryFn: () => api.listDocuments({ page: 1, size: 8 }),
        },
        { queryKey: ["conversations"], queryFn: () => api.listConversations(5) },
      ],
    })

  const metrics = metricsQ.data
  const health = healthQ.data
  const gate = gateQ.data
  const online = onlineQ.data
  const projects = projectsQ.data ?? []
  const drawings = drawingsQ.data ?? []
  const jobs = jobsQ.data ?? []
  const recentDocs = docsQ.data?.items ?? []

  const failedJobs = jobs.filter((j) => j.status === "failed")
  const runningJobs = jobs.filter((j) => j.status === "running")
  const degraded = health
    ? Object.entries(health.checks).filter(([, value]) => value !== "ok")
    : []
  const breaches = online?.breaches ?? []

  const attention =
    failedJobs.length + degraded.length + breaches.length + (gate?.passing === false ? 1 : 0)

  const recentRevisions = [...drawings]
    .filter((d) => d.current_revision_date)
    .sort((a, b) => (b.current_revision_date ?? "").localeCompare(a.current_revision_date ?? ""))
    .slice(0, 6)

  const hour = new Date().getHours()
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening"
  const firstName = (principal?.display_name ?? principal?.email ?? "").split(/[\s.@]/)[0]

  return (
    <Page>
      <PageHeader
        eyebrow={new Date().toLocaleDateString(undefined, {
          weekday: "long",
          day: "numeric",
          month: "long",
        })}
        title={`${greeting}, ${firstName.charAt(0).toUpperCase()}${firstName.slice(1)}`}
        description="Where the corpus stands, what is moving through ingestion, and anything that needs a decision today."
        actions={
          <>
            <Button asChild variant="outline" size="sm">
              <Link to="/search">
                <SearchIcon />
                Search
              </Link>
            </Button>
            <Button asChild size="sm">
              <Link to="/ask">
                <MessagesSquareIcon />
                Ask the corpus
              </Link>
            </Button>
          </>
        }
      />

      <div className="mt-5 space-y-6">
        {/* --------------------------------------------------- attention */}
        {attention > 0 && (
          <Card className="border-warn/35 bg-warn-soft/30">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-warn">
                <AlertTriangleIcon className="size-4" />
                {attention} {attention === 1 ? "item needs" : "items need"} attention
              </CardTitle>
            </CardHeader>
            <CardContent className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
              {failedJobs.slice(0, 3).map((job) => (
                <AttentionRow
                  key={job.id}
                  to="/ingest/queue"
                  tone="error"
                  title={`Ingestion failed — ${job.document_name}`}
                  detail={job.error_message ?? "The job exhausted its retries."}
                />
              ))}
              {degraded.map(([service, message]) => (
                <AttentionRow
                  key={service}
                  to="/ops/monitoring"
                  tone="error"
                  title={`${service} is unhealthy`}
                  detail={message}
                />
              ))}
              {breaches.map((metric) => (
                <AttentionRow
                  key={metric}
                  to="/ops/quality"
                  tone="warn"
                  title={`${metric.replace(/_/g, " ")} is below its policy floor`}
                  detail={`Rolling ${online?.window_hours}h average ${online?.averages[metric]?.toFixed(3)} against a floor of ${online?.floors[metric]?.toFixed(2)}.`}
                />
              ))}
              {gate?.passing === false && (
                <AttentionRow
                  to="/ops/quality"
                  tone="warn"
                  title="The offline quality gate is failing"
                  detail={`Latest completed run on ${gate.dataset} does not clear every gated metric.`}
                />
              )}
            </CardContent>
          </Card>
        )}

        {/* ------------------------------------------------------- tiles */}
        {metricsQ.isPending ? (
          <TileRowSkeleton count={5} />
        ) : metrics ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            <StatTile
              label="Documents indexed"
              value={formatNumber(metrics.documents_indexed)}
              unit={`/ ${formatNumber(metrics.documents_total)}`}
              icon={FileStackIcon}
              to="/documents"
              footer={
                <Progress
                  value={(metrics.documents_indexed / Math.max(1, metrics.documents_total)) * 100}
                  className="mt-1"
                  tone="ok"
                />
              }
              hint="Everything readable at your clearance, in the projects you belong to."
            />
            <StatTile
              label="Drawings tracked"
              value={formatNumber(drawings.length)}
              icon={ClipboardListIcon}
              to="/drawings"
              footer={`${formatNumber(drawings.reduce((s, d) => s + (d.revision_count ?? 0), 0))} revisions across ${projects.length} projects`}
              hint="A drawing is one identity; its revisions are separate documents of it."
            />
            <StatTile
              label="In the queue"
              value={formatNumber(metrics.queue_depth + metrics.jobs_running)}
              icon={ActivityIcon}
              tone={metrics.jobs_failed_24h > 0 ? "warn" : "default"}
              to="/ingest/queue"
              footer={`${runningJobs.length} running · ${metrics.queue_depth} waiting · ${metrics.jobs_failed_24h} failed`}
            />
            <StatTile
              label="Questions, 24 h"
              value={formatNumber(metrics.queries_24h)}
              icon={MessagesSquareIcon}
              to="/ask"
              footer={`${formatPercent(metrics.cache_hit_rate)} served from cache · ${formatPercent(metrics.refusal_rate, 1)} refused`}
              hint="A refusal is a deliberate outcome — an ungrounded answer about a dimension is worse than none."
            />
            <StatTile
              label="p95 answer latency"
              value={formatMs(metrics.p95_latency_ms)}
              icon={GaugeCircleIcon}
              tone={metrics.p95_latency_ms > 6000 ? "warn" : "ok"}
              to="/ops/monitoring"
              footer={`$${metrics.cost_24h_usd.toFixed(2)} inference spend in 24 h`}
            />
          </div>
        ) : null}

        {/* ------------------------------------------------------ charts */}
        <div className="grid gap-4 lg:grid-cols-[1.25fr_1fr]">
          <ChartFrame
            title="Ingestion, last 14 days"
            description="Documents that reached indexed, against jobs that exhausted their retries."
            action={
              <Button asChild variant="ghost" size="xs">
                <Link to="/ingest/queue">
                  Queue <ArrowRightIcon />
                </Link>
              </Button>
            }
            loading={metricsQ.isPending}
            height={196}
          >
            {metrics && (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={metrics.ingest_series} margin={{ top: 4, right: 4, left: -22, bottom: 0 }}>
                  <CartesianGrid {...chartGrid} />
                  <XAxis
                    dataKey="t"
                    {...chartAxis}
                    tickFormatter={(v) =>
                      new Date(v).toLocaleDateString(undefined, { day: "numeric", month: "short" })
                    }
                  />
                  <YAxis {...chartAxis} allowDecimals={false} />
                  <RTooltip
                    {...chartTooltip}
                    labelFormatter={(v) => new Date(v as string).toLocaleDateString()}
                  />
                  <Bar dataKey="ingested" name="Indexed" stackId="a" fill="var(--chart-1)" radius={[0, 0, 0, 0]} maxBarSize={22} />
                  <Bar dataKey="failed" name="Failed" stackId="a" fill="var(--status-error)" radius={[2, 2, 0, 0]} maxBarSize={22} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </ChartFrame>

          <ChartFrame
            title="Query volume and p95"
            description="Hourly, last 24 hours."
            loading={metricsQ.isPending}
            height={196}
          >
            {metrics && (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={metrics.query_series} margin={{ top: 4, right: 4, left: -22, bottom: 0 }}>
                  <defs>
                    <linearGradient id="queries" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="var(--chart-2)" stopOpacity={0.35} />
                      <stop offset="100%" stopColor="var(--chart-2)" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid {...chartGrid} />
                  <XAxis
                    dataKey="t"
                    {...chartAxis}
                    tickFormatter={(v) =>
                      new Date(v).toLocaleTimeString(undefined, { hour: "2-digit" })
                    }
                    interval={5}
                  />
                  <YAxis {...chartAxis} allowDecimals={false} />
                  <RTooltip
                    {...chartTooltip}
                    labelFormatter={(v) => new Date(v as string).toLocaleString()}
                  />
                  <Area
                    type="monotone"
                    dataKey="queries"
                    name="Questions"
                    stroke="var(--chart-2)"
                    strokeWidth={1.6}
                    fill="url(#queries)"
                  />
                  <Line
                    type="monotone"
                    dataKey="p95_ms"
                    name="p95 (ms)"
                    stroke="var(--chart-1)"
                    strokeWidth={1.4}
                    dot={false}
                    yAxisId={0}
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </ChartFrame>
        </div>

        {/* --------------------------------------------- live + revisions */}
        <div className="grid gap-4 lg:grid-cols-[1fr_1.1fr]">
          <Section
            title="Moving through ingestion"
            description="Stages run in order: load → classify → OCR → layout → extract → chunk → embed → index."
            actions={
              <Button asChild variant="ghost" size="xs">
                <Link to="/ingest/queue">
                  All jobs <ArrowRightIcon />
                </Link>
              </Button>
            }
          >
            {runningJobs.length === 0 && failedJobs.length === 0 ? (
              <EmptyState
                compact
                icon={UploadCloudIcon}
                title="The queue is clear"
                description="Nothing is being ingested right now."
                action={
                  <Button asChild size="sm" variant="outline">
                    <Link to="/ingest">Upload drawings</Link>
                  </Button>
                }
              />
            ) : (
              <div className="space-y-2">
                {[...runningJobs, ...failedJobs].slice(0, 4).map((job) => (
                  <Card key={job.id} className="p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 space-y-1">
                        <Link
                          to={`/documents/${job.document_id}`}
                          className="block truncate text-[0.8125rem] font-medium hover:text-primary"
                        >
                          {job.document_name}
                        </Link>
                        <p className="font-mono text-[0.6875rem] uppercase tracking-wide text-muted-foreground">
                          {job.job_type.replace(/_/g, " ")} · stage {job.stage ?? "—"} · attempt{" "}
                          {job.attempts}/{job.max_attempts}
                        </p>
                      </div>
                      <Badge variant={job.status === "failed" ? "error" : "technical"}>
                        {job.status}
                      </Badge>
                    </div>
                    {job.status === "running" ? (
                      <Progress value={job.progress ?? 0} className="mt-2.5" />
                    ) : (
                      <p className="mt-2 line-clamp-2 rounded-sm border border-error/25 bg-error-soft/40 px-2 py-1.5 font-mono text-[0.6875rem] leading-relaxed text-error">
                        {job.error_message}
                      </p>
                    )}
                  </Card>
                ))}
              </div>
            )}
          </Section>

          <Section
            title="Latest revisions"
            description="The current issue of each drawing touched most recently."
            actions={
              <Button asChild variant="ghost" size="xs">
                <Link to="/drawings">
                  Register <ArrowRightIcon />
                </Link>
              </Button>
            }
          >
            <Card className="divide-y divide-border">
              {recentRevisions.map((drawing) => (
                <Link
                  key={drawing.id}
                  to={`/drawings/${drawing.id}`}
                  className="flex items-center gap-3 px-3 py-2.5 transition-colors hover:bg-muted/50"
                >
                  <div className="min-w-0 flex-1 space-y-0.5">
                    <div className="flex items-center gap-2">
                      <DrawingNumber value={drawing.drawing_number} sheet={drawing.sheet_number} />
                      <RevisionChip
                        label={drawing.current_revision_label}
                        date={drawing.current_revision_date}
                      />
                    </div>
                    <p className="truncate text-xs text-muted-foreground">{drawing.title}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <ContentKindBadge kind={drawing.content_kind} compact />
                    <span className="w-14 text-right font-mono text-[0.6875rem] tabular-nums text-muted-foreground">
                      {timeAgo(drawing.current_revision_date)}
                    </span>
                  </div>
                </Link>
              ))}
            </Card>
          </Section>
        </div>

        {/* ---------------------------------------------------- projects */}
        <Section
          title="Your projects"
          description="Project membership grants reach; clearance grants depth. Both must pass."
          actions={
            <Button asChild variant="ghost" size="xs">
              <Link to="/projects">
                All projects <ArrowRightIcon />
              </Link>
            </Button>
          }
        >
          {projects.length === 0 ? (
            <EmptyState
              icon={FolderKanbanIcon}
              title="You are not a member of any project"
              description="Documents with no project are personal and visible only to their owner. Ask a steward to add you to a job."
            />
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {projects.slice(0, 6).map((project) => (
                <Link
                  key={project.id}
                  to={`/projects/${project.id}`}
                  className="group flex flex-col gap-2.5 rounded-lg border border-border bg-card p-3.5 transition-colors hover:border-primary/40"
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="font-mono text-[0.75rem] font-medium text-muted-foreground">
                      {project.project_number}
                    </span>
                    <ProjectStatusBadge status={project.status} />
                  </div>
                  <div className="space-y-0.5">
                    <p className="text-pretty text-[0.8125rem] font-medium leading-snug transition-colors group-hover:text-primary">
                      {project.name}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">{project.client_name}</p>
                  </div>
                  <div className="mt-auto flex items-center justify-between gap-2 border-t border-border pt-2.5 text-[0.6875rem] text-muted-foreground">
                    <span className="flex items-center gap-2.5 font-mono tabular-nums">
                      <Hint label="Drawings">
                        <span className="flex items-center gap-1">
                          <ClipboardListIcon className="size-3" />
                          {project.drawing_count}
                        </span>
                      </Hint>
                      <Hint label="Documents">
                        <span className="flex items-center gap-1">
                          <FileStackIcon className="size-3" />
                          {project.document_count}
                        </span>
                      </Hint>
                      {project.tonnage_t != null && (
                        <Hint label="Fabricated tonnage">
                          <span className="flex items-center gap-1">
                            <CpuIcon className="size-3" />
                            {formatNumber(project.tonnage_t)} t
                          </span>
                        </Hint>
                      )}
                    </span>
                    {project.default_sensitivity && (
                      <SensitivityBar value={project.default_sensitivity} />
                    )}
                  </div>
                </Link>
              ))}
            </div>
          )}
        </Section>

        {/* ---------------------------------------- recent docs + asks */}
        <div className="grid gap-4 lg:grid-cols-2">
          <Section title="Recently added" description="Newest documents you can read.">
            <Card className="divide-y divide-border">
              {recentDocs.slice(0, 6).map((doc) => (
                <Link
                  key={doc.id}
                  to={`/documents/${doc.id}`}
                  className="flex items-center gap-3 px-3 py-2.5 transition-colors hover:bg-muted/50"
                >
                  <SensitivityBar value={doc.sensitivity} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[0.8125rem] font-medium">{doc.file_name}</p>
                    <p className="truncate text-[0.6875rem] text-muted-foreground">
                      {doc.project_number ?? "Personal"} · {timeAgo(doc.created_at)}
                    </p>
                  </div>
                  <DocumentStatusBadge status={doc.status} />
                </Link>
              ))}
            </Card>
          </Section>

          <Section title="Your recent questions" description="Every answer keeps its citations and trace id.">
            {(convosQ.data ?? []).length === 0 ? (
              <EmptyState
                compact
                icon={MessagesSquareIcon}
                title="No questions yet"
                description="Ask something about a drawing, a grade or a connection detail."
                action={
                  <Button asChild size="sm">
                    <Link to="/ask">Open Ask</Link>
                  </Button>
                }
              />
            ) : (
              <Card className="divide-y divide-border">
                {(convosQ.data ?? []).map((conversation) => (
                  <Link
                    key={conversation.id}
                    to={`/ask/${conversation.id}`}
                    className="flex items-center gap-3 px-3 py-2.5 transition-colors hover:bg-muted/50"
                  >
                    <MessagesSquareIcon className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 flex-1 truncate text-[0.8125rem]">
                      {conversation.title}
                    </span>
                    <span className="shrink-0 font-mono text-[0.6875rem] tabular-nums text-muted-foreground">
                      {timeAgo(conversation.updated_at)}
                    </span>
                  </Link>
                ))}
              </Card>
            )}
          </Section>
        </div>

        {/* ----------------------------------------------- infrastructure */}
        {health && (
          <Section title="Infrastructure" description="Every store the pipeline depends on.">
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
              {Object.entries(health.checks).map(([service, status]) => {
                const ok = status === "ok"
                return (
                  <Hint key={service} label={ok ? "Responding normally" : status}>
                    <div
                      className={cn(
                        "flex items-center gap-2 rounded-md border px-2.5 py-2",
                        ok ? "border-border bg-card" : "border-error/35 bg-error-soft/40",
                      )}
                    >
                      <span
                        className={cn(
                          "size-1.5 shrink-0 rounded-full",
                          ok ? "bg-ok" : "animate-pulse bg-error",
                        )}
                      />
                      <DatabaseIcon className="size-3.5 shrink-0 text-muted-foreground" />
                      <span className="truncate text-[0.75rem] font-medium capitalize">
                        {service}
                      </span>
                    </div>
                  </Hint>
                )
              })}
            </div>
          </Section>
        )}
      </div>
    </Page>
  )
}

function AttentionRow({
  to,
  tone,
  title,
  detail,
}: {
  to: string
  tone: "warn" | "error"
  title: string
  detail: string
}) {
  return (
    <Link
      to={to}
      className={cn(
        "group flex items-start gap-2 rounded-md border bg-card px-2.5 py-2 transition-colors",
        tone === "error"
          ? "border-error/30 hover:border-error/55"
          : "border-warn/30 hover:border-warn/55",
      )}
    >
      <span
        className={cn(
          "mt-1 size-1.5 shrink-0 rounded-full",
          tone === "error" ? "bg-error" : "bg-warn",
        )}
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[0.8125rem] font-medium capitalize">{title}</span>
        <span className="line-clamp-2 text-[0.6875rem] leading-relaxed text-muted-foreground">
          {detail}
        </span>
      </span>
      <ArrowRightIcon className="mt-0.5 size-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
    </Link>
  )
}
