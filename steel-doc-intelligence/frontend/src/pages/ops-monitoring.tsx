import { useQueries } from "@tanstack/react-query"
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from "recharts"
import {
  ActivityIcon,
  BanknoteIcon,
  CircleSlashIcon,
  DatabaseIcon,
  GaugeCircleIcon,
  RefreshCwIcon,
  ServerIcon,
  ShieldXIcon,
  ZapIcon,
} from "lucide-react"
import { api } from "@/api"
import { cn, formatMs, formatNumber, formatPercent } from "@/lib/utils"
import { Page } from "@/components/layout/page"
import { PageHeader, Section, StatTile } from "@/components/domain/layout"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table"
import { ChartFrame, chartAxis, chartGrid, chartTooltip } from "@/components/domain/chart"
import { TileRowSkeleton } from "@/components/domain/states"

const SERVICE_NOTE: Record<string, string> = {
  postgres: "Metadata, chunks, jobs, audit log and the system of record for every denormalized field.",
  redis: "Embedding cache, rate limits, runtime flags and the arq job queue.",
  qdrant: "Dense vectors with payload indexes for the clearance pre-filter.",
  elasticsearch: "BM25 keyword search and the facet aggregations behind the filter sidebar.",
  minio: "Blob storage for stored originals. Bytes are proxied through the API, never presigned.",
  arq_worker: "The out-of-process ingestion worker.",
}

export default function MonitoringPage() {
  const [healthQ, metricsQ, onlineQ, feedbackQ, gatewayQ, pluginsQ] = useQueries({
    queries: [
      { queryKey: ["health"], queryFn: () => api.health(), refetchInterval: 15_000 },
      { queryKey: ["metrics"], queryFn: () => api.systemMetrics(), refetchInterval: 15_000 },
      { queryKey: ["online", 24], queryFn: () => api.onlineMetrics(24) },
      { queryKey: ["feedback-summary", 168], queryFn: () => api.feedbackSummary(168) },
      { queryKey: ["gateway"], queryFn: () => api.describeGateway() },
      { queryKey: ["plugins"], queryFn: () => api.listPlugins() },
    ],
  })

  const health = healthQ.data
  const metrics = metricsQ.data
  const online = onlineQ.data
  const feedback = feedbackQ.data

  const degraded = health ? Object.values(health.checks).filter((v) => v !== "ok").length : 0
  const uptimeDays = health ? Math.floor(health.uptime_seconds / 86400) : 0

  const kindMix = metrics
    ? [
        { name: "Indexed", value: metrics.documents_indexed, fill: "var(--chart-1)" },
        {
          name: "In flight",
          value: metrics.jobs_running + metrics.queue_depth,
          fill: "var(--chart-2)",
        },
        { name: "Failed", value: metrics.jobs_failed_24h, fill: "var(--status-error)" },
      ].filter((d) => d.value > 0)
    : []

  return (
    <Page>
      <PageHeader
        eyebrow="Operations"
        title="Monitoring"
        description="Service health, throughput, latency and spend. Everything here is derived from the same Prometheus registry the alerting rules read, so a tile and a page cannot disagree."
        meta={
          health && (
            <>
              <Badge variant={health.status === "healthy" ? "ok" : "warn"}>
                {health.status}
              </Badge>
              <span className="font-mono text-xs text-muted-foreground">
                v{health.version} · up {uptimeDays}d
              </span>
            </>
          )
        }
      />

      {metricsQ.isPending ? (
        <div className="mt-5">
          <TileRowSkeleton count={6} />
        </div>
      ) : metrics ? (
        <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
          <StatTile
            label="Services"
            value={`${Object.keys(health?.checks ?? {}).length - degraded}/${Object.keys(health?.checks ?? {}).length}`}
            tone={degraded ? "error" : "ok"}
            icon={ServerIcon}
            footer={degraded ? `${degraded} unhealthy` : "All responding"}
          />
          <StatTile
            label="Questions, 24 h"
            value={formatNumber(metrics.queries_24h)}
            icon={ActivityIcon}
          />
          <StatTile
            label="p95 latency"
            value={formatMs(metrics.p95_latency_ms)}
            tone={metrics.p95_latency_ms > 6000 ? "warn" : "ok"}
            icon={GaugeCircleIcon}
          />
          <StatTile
            label="Cache hit rate"
            value={formatPercent(metrics.cache_hit_rate)}
            icon={ZapIcon}
            hint="Semantic answer cache. Every hit is a model call not billed — and the numeric-literal guard stops “within 30 days” matching a cached “within 14 days”."
          />
          <StatTile
            label="Refusal rate"
            value={formatPercent(metrics.refusal_rate, 1)}
            icon={CircleSlashIcon}
            hint="Questions the pipeline declined rather than answer without grounding. A non-zero rate is correct behaviour, not a fault."
          />
          <StatTile
            label="Spend, 24 h"
            value={`$${metrics.cost_24h_usd.toFixed(2)}`}
            icon={BanknoteIcon}
            hint="Inference cost accounted per provider in the LLM gateway."
          />
        </div>
      ) : null}

      {/* ------------------------------------------------------- services */}
      <Section
        className="mt-6"
        title="Dependencies"
        description="Each store the pipeline needs, checked live."
      >
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {Object.entries(health?.checks ?? {}).map(([service, status]) => {
            const ok = status === "ok"
            return (
              <Card
                key={service}
                className={cn("p-3", !ok && "border-error/35 bg-error-soft/25")}
              >
                <div className="flex items-center gap-2">
                  <span
                    className={cn(
                      "size-2 shrink-0 rounded-full",
                      ok ? "bg-ok" : "animate-pulse bg-error",
                    )}
                  />
                  <DatabaseIcon className="size-3.5 shrink-0 text-muted-foreground" />
                  <span className="text-[0.8125rem] font-medium capitalize">
                    {service.replace(/_/g, " ")}
                  </span>
                  <Badge variant={ok ? "ok" : "error"} className="ml-auto">
                    {ok ? "ok" : "error"}
                  </Badge>
                </div>
                <p className="mt-1.5 text-[0.6875rem] leading-relaxed text-muted-foreground">
                  {SERVICE_NOTE[service] ?? "—"}
                </p>
                {!ok && (
                  <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-sm border border-error/25 bg-card/60 p-2 font-mono text-[0.625rem] leading-relaxed text-error">
                    {status}
                  </pre>
                )}
              </Card>
            )
          })}
        </div>
      </Section>

      {/* --------------------------------------------------------- charts */}
      <div className="mt-6 grid gap-4 lg:grid-cols-3">
        <ChartFrame
          title="Query volume"
          description="Hourly over the last day."
          loading={metricsQ.isPending}
          className="lg:col-span-2"
          height={210}
        >
          {metrics && (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={metrics.query_series} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="mon-q" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--chart-2)" stopOpacity={0.4} />
                    <stop offset="100%" stopColor="var(--chart-2)" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...chartGrid} />
                <XAxis
                  dataKey="t"
                  {...chartAxis}
                  interval={3}
                  tickFormatter={(v) => new Date(v).toLocaleTimeString(undefined, { hour: "2-digit" })}
                />
                <YAxis {...chartAxis} />
                <RTooltip {...chartTooltip} labelFormatter={(v) => new Date(v as string).toLocaleString()} />
                <Area
                  type="monotone"
                  dataKey="queries"
                  name="Questions"
                  stroke="var(--chart-2)"
                  strokeWidth={1.6}
                  fill="url(#mon-q)"
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </ChartFrame>

        <ChartFrame title="Corpus state" description="Where documents are." loading={metricsQ.isPending} height={210}>
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={kindMix}
                dataKey="value"
                nameKey="name"
                innerRadius={44}
                outerRadius={70}
                paddingAngle={2}
                strokeWidth={0}
              >
                {kindMix.map((entry) => (
                  <Cell key={entry.name} fill={entry.fill} />
                ))}
              </Pie>
              <RTooltip {...chartTooltip} />
            </PieChart>
          </ResponsiveContainer>
        </ChartFrame>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <ChartFrame
          title="p95 latency"
          description="Hourly, against the 6 s comfort threshold."
          loading={metricsQ.isPending}
          height={200}
        >
          {metrics && (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={metrics.query_series} margin={{ top: 4, right: 4, left: -14, bottom: 0 }}>
                <CartesianGrid {...chartGrid} />
                <XAxis
                  dataKey="t"
                  {...chartAxis}
                  interval={5}
                  tickFormatter={(v) => new Date(v).toLocaleTimeString(undefined, { hour: "2-digit" })}
                />
                <YAxis {...chartAxis} tickFormatter={(v) => `${(v / 1000).toFixed(0)}s`} />
                <RTooltip
                  {...chartTooltip}
                  formatter={(value) => [`${((value as number) / 1000).toFixed(2)} s`, "p95"]}
                  labelFormatter={(v) => new Date(v as string).toLocaleString()}
                />
                <Bar dataKey="p95_ms" name="p95" radius={[2, 2, 0, 0]} maxBarSize={14}>
                  {metrics.query_series.map((point, i) => (
                    <Cell
                      key={i}
                      fill={point.p95_ms > 6000 ? "var(--status-warn)" : "var(--chart-1)"}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </ChartFrame>

        <Card className="p-4">
          <p className="eyebrow mb-3">Rolling answer quality, 24 h</p>
          {online ? (
            <div className="space-y-3">
              {Object.entries(online.averages).map(([metric, value]) => {
                const floor = online.floors[metric]
                const breached = online.breaches.includes(metric)
                return (
                  <div key={metric} className="space-y-1.5">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-[0.8125rem] capitalize">{metric.replace(/_/g, " ")}</span>
                      <span
                        className={cn(
                          "font-mono text-[0.8125rem] tabular-nums",
                          breached && "font-semibold text-error",
                        )}
                      >
                        {value.toFixed(3)}
                        {floor != null && (
                          <span className="ml-1.5 text-[0.6875rem] text-muted-foreground">
                            floor {floor.toFixed(2)}
                          </span>
                        )}
                      </span>
                    </div>
                    <div className="relative h-1.5 overflow-hidden rounded-full bg-muted">
                      <div
                        className={cn(
                          "absolute inset-y-0 left-0 rounded-full",
                          breached ? "bg-error" : "bg-ok",
                        )}
                        style={{ width: `${value * 100}%` }}
                      />
                      {floor != null && (
                        <div
                          className="absolute inset-y-[-2px] w-px bg-foreground/60"
                          style={{ left: `${floor * 100}%` }}
                        />
                      )}
                    </div>
                  </div>
                )
              })}
              <p className="border-t border-border pt-3 text-[0.6875rem] leading-relaxed text-muted-foreground">
                Sampled LLM-judge scores over {online.sample_count} live traces. This is the only
                measure of quality <i>right now</i> — an offline run against a fixed golden set
                cannot see today's traffic.
              </p>
            </div>
          ) : (
            <TileRowSkeleton count={1} />
          )}
        </Card>
      </div>

      {/* ------------------------------------------------------- provider */}
      <Section
        className="mt-6"
        title="Provider chain"
        description="The fallback order in force for each model role. A stream never fails over once a token has reached the client."
      >
        <div className="grid gap-3 md:grid-cols-2">
          {Object.entries(gatewayQ.data ?? {}).map(([role, config]) => (
            <Card key={role} className="p-3.5">
              <p className="eyebrow mb-2 capitalize">{role} model</p>
              <ol className="space-y-1.5">
                {(config.chain ?? []).map((link, i) => (
                  <li key={i} className="flex items-center gap-2.5">
                    <span
                      className={cn(
                        "flex size-5 shrink-0 items-center justify-center rounded-full font-mono text-[0.625rem]",
                        i === 0 ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground",
                      )}
                    >
                      {i + 1}
                    </span>
                    <span className="min-w-0 flex-1 truncate font-mono text-[0.75rem]">
                      {link.model}
                    </span>
                    <Badge variant={i === 0 ? "default" : "muted"}>{link.provider}</Badge>
                  </li>
                ))}
              </ol>
            </Card>
          ))}
        </div>
      </Section>

      {/* -------------------------------------------------------- plugins */}
      <Section
        className="mt-6"
        title="Loaded plugins"
        description="What the registries actually contain. A registry populated by import side effects can silently lose an entry when a module fails to import — this is how that becomes visible rather than presenting as “the router never picks that tool”."
      >
        <div className="grid gap-3 lg:grid-cols-3">
          {(
            [
              ["LLM providers", pluginsQ.data?.llm_providers ?? []],
              ["Tools", pluginsQ.data?.tools ?? []],
              ["PII detectors", pluginsQ.data?.pii_detectors ?? []],
            ] as const
          ).map(([label, entries]) => (
            <Card key={label}>
              <p className="eyebrow px-3.5 pb-1.5 pt-3">{label}</p>
              <Table>
                <TableBody>
                  {entries.map((entry) => (
                    <TableRow key={entry.name}>
                      <TableCell className="align-top">
                        <p className="font-mono text-[0.75rem] font-medium">{entry.name}</p>
                        {entry.description && (
                          <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
                            {entry.description}
                          </p>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>
          ))}
        </div>
      </Section>

      {/* ------------------------------------------------------- feedback */}
      {feedback && (
        <Section
          className="mt-6"
          title="User feedback, 7 days"
          description="Ratings from the people using the answers."
        >
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatTile label="Responses" value={feedback.total} icon={ActivityIcon} />
            <StatTile
              label="Average rating"
              value={feedback.average_rating?.toFixed(2) ?? "—"}
              unit="/ 5"
              tone={(feedback.average_rating ?? 0) < 3 ? "warn" : "ok"}
            />
            <StatTile label="Positive" value={feedback.positive} tone="ok" />
            <StatTile
              label="Negative"
              value={feedback.negative}
              tone={feedback.negative > 0 ? "error" : "default"}
              icon={ShieldXIcon}
              footer="Promotable into the regression dataset."
            />
          </div>
        </Section>
      )}

      <p className="mt-6 flex items-center gap-1.5 text-[0.6875rem] text-muted-foreground">
        <RefreshCwIcon className="size-3" />
        Health and metrics refresh every 15 seconds.
      </p>
    </Page>
  )
}
