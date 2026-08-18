import * as React from "react"
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from "recharts"
import {
  BadgeCheckIcon,
  CheckCircle2Icon,
  MessageSquareWarningIcon,
  PlayIcon,
  ShieldXIcon,
  StarIcon,
  TriangleAlertIcon,
  UploadIcon,
} from "lucide-react"
import { api } from "@/api"
import type { EvaluationRun } from "@/api/types"
import { useAuth } from "@/lib/auth"
import { cn, formatDate, timeAgo } from "@/lib/utils"
import { Page } from "@/components/layout/page"
import { PageHeader, Section, StatTile } from "@/components/domain/layout"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Label, Progress, Separator } from "@/components/ui/misc"
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Hint } from "@/components/ui/tooltip"
import { ChartFrame, MetricBar, chartAxis, chartGrid, chartTooltip } from "@/components/domain/chart"
import { EmptyState, ErrorState, TextSkeleton, TileRowSkeleton } from "@/components/domain/states"

export default function QualityPage() {
  const { hasRole } = useAuth()
  const queryClient = useQueryClient()
  const [dataset, setDataset] = React.useState("golden_set_v1")
  const [runDialogOpen, setRunDialogOpen] = React.useState(false)

  const [runsQ, gateQ, onlineQ, feedbackQ, datasetsQ] = useQueries({
    queries: [
      { queryKey: ["eval-runs"], queryFn: () => api.listEvaluationRuns(), refetchInterval: 5_000 },
      { queryKey: ["gate", dataset], queryFn: () => api.evaluationGate(dataset) },
      { queryKey: ["online", 24], queryFn: () => api.onlineMetrics(24) },
      { queryKey: ["feedback-summary", 168], queryFn: () => api.feedbackSummary(168) },
      { queryKey: ["datasets"], queryFn: () => api.listDatasets() },
    ],
  })

  const runs = runsQ.data ?? []
  const gate = gateQ.data
  const online = onlineQ.data
  const feedback = feedbackQ.data

  const promote = useMutation({
    mutationFn: () => api.promoteFeedback(),
    onSuccess: (result) => {
      toast.success(`${result.promoted} negatives promoted`, {
        description: `Added to ${result.dataset}. This changes what "passing" means for the CI gate — the next run measures against them.`,
      })
      void queryClient.invalidateQueries({ queryKey: ["datasets"] })
    },
    onError: (error) =>
      toast.error("Could not promote", {
        description: error instanceof Error ? error.message : undefined,
      }),
  })

  // Trend across completed runs of the selected dataset, oldest first.
  const trend = React.useMemo(
    () =>
      runs
        .filter((r) => r.dataset_name === dataset && r.status === "completed")
        .slice()
        .reverse()
        .map((run) => ({
          t: run.completed_at ?? run.started_at,
          name: run.name,
          ...Object.fromEntries(run.metrics.map((m) => [m.name, Number(m.value.toFixed(4))])),
        })),
    [runs, dataset],
  )

  const running = runs.filter((r) => r.status === "running")

  return (
    <Page>
      <PageHeader
        eyebrow="Operations"
        title="Answer quality"
        description="Two independent measures. Offline runs score a fixed golden set and gate releases; online judging samples live traffic and answers the question an offline run cannot — is quality acceptable right now."
        actions={
          hasRole("analyst", "steward", "admin") && (
            <Button size="sm" onClick={() => setRunDialogOpen(true)}>
              <PlayIcon />
              Start a run
            </Button>
          )
        }
      />

      {/* ---------------------------------------------------------- gate */}
      <div className="mt-5 grid gap-4 lg:grid-cols-[1fr_1fr]">
        <Card
          className={cn(
            "p-4",
            gate?.passing === true && "border-ok/40",
            gate?.passing === false && "border-error/40",
          )}
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <BadgeCheckIcon
                className={cn(
                  "size-4",
                  gate?.passing === true ? "text-ok" : gate?.passing === false ? "text-error" : "text-muted-foreground",
                )}
              />
              <h2 className="text-sm font-semibold tracking-tight">Release gate</h2>
            </div>
            <Select value={dataset} onValueChange={setDataset}>
              <SelectTrigger size="sm" className="w-auto min-w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(datasetsQ.data ?? [dataset]).map((name) => (
                  <SelectItem key={name} value={name}>
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {gateQ.isPending ? (
            <div className="mt-3">
              <TextSkeleton lines={4} />
            </div>
          ) : gateQ.isError ? (
            // Distinct from the empty state below. Without this branch a 403
            // and "no run has completed yet" render identically, and the
            // difference is the whole question: one is a permission problem
            // to escalate, the other is a run nobody has started.
            <ErrorState
              compact
              className="mt-3"
              resource="the quality gate"
              error={gateQ.error}
              onRetry={() => void gateQ.refetch()}
            />
          ) : gate?.status === "no_completed_run" ? (
            <EmptyState
              compact
              className="mt-3"
              title="No completed run for this dataset"
              description="Start a run to establish a baseline. Until one completes, the gate has nothing to judge."
            />
          ) : (
            <>
              <p className="mt-2 flex items-center gap-2 text-sm">
                {gate?.passing ? (
                  <>
                    <CheckCircle2Icon className="size-4 text-ok" />
                    <span className="font-medium text-ok">Passing every gated metric</span>
                  </>
                ) : (
                  <>
                    <TriangleAlertIcon className="size-4 text-error" />
                    <span className="font-medium text-error">Below at least one policy floor</span>
                  </>
                )}
                <span className="ml-auto font-mono text-[0.6875rem] text-muted-foreground">
                  policy {gate?.policy_version}
                </span>
              </p>

              <div className="mt-3.5 space-y-3">
                {gate?.metrics?.map((metric) => (
                  <MetricBar
                    key={metric.metric}
                    label={metric.metric}
                    value={metric.value}
                    floor={metric.gated ? metric.floor : null}
                  />
                ))}
              </div>

              <p className="mt-3 border-t border-border pt-3 text-[0.6875rem] leading-relaxed text-muted-foreground">
                The same judgement CI makes, exposed over HTTP so the state of the gate is visible
                without reading a build log — and so both answers come from one implementation
                rather than two that can disagree.
              </p>
            </>
          )}
        </Card>

        <Card className="p-4">
          <div className="flex items-center gap-2">
            <ShieldXIcon
              className={cn(
                "size-4",
                (online?.breaches.length ?? 0) > 0 ? "text-error" : "text-ok",
              )}
            />
            <h2 className="text-sm font-semibold tracking-tight">Live traffic, last 24 h</h2>
            {online && (
              <Badge variant="muted" className="ml-auto font-mono">
                {online.sample_count} sampled
              </Badge>
            )}
          </div>

          {onlineQ.isPending ? (
            <div className="mt-3">
              <TextSkeleton lines={4} />
            </div>
          ) : onlineQ.isError ? (
            <ErrorState
              compact
              className="mt-3"
              resource="online metrics"
              error={onlineQ.error}
              onRetry={() => void onlineQ.refetch()}
            />
          ) : online ? (
            <>
              <div className="mt-3.5 space-y-3">
                {Object.entries(online.averages).map(([metric, value]) => (
                  <MetricBar
                    key={metric}
                    label={metric}
                    value={value}
                    floor={online.floors[metric]}
                  />
                ))}
              </div>
              {online.breaches.length > 0 && (
                <p className="mt-3 flex items-start gap-2 rounded-md border border-error/30 bg-error-soft/40 p-2.5 text-[0.6875rem] leading-relaxed text-error">
                  <TriangleAlertIcon className="mt-px size-3.5 shrink-0" />
                  <span>
                    <b>{online.breaches.join(", ").replace(/_/g, " ")}</b> below the policy floor on
                    live traffic. Context relevancy dropping while faithfulness holds usually means
                    retrieval is pulling in too much — check the reranker and{" "}
                    <code className="font-mono">rerank_top_n</code>.
                  </span>
                </p>
              )}
              <p className="mt-3 border-t border-border pt-3 text-[0.6875rem] leading-relaxed text-muted-foreground">
                Scoring runs in the background so it never adds latency to a user's request. An
                unparseable judge response returns nothing and is excluded — scoring it 0.5 would
                make it indistinguishable from a mediocre answer.
              </p>
            </>
          ) : null}
        </Card>
      </div>

      <Tabs defaultValue="runs" className="mt-6">
        <TabsList>
          <TabsTrigger value="runs">
            Evaluation runs
            <Badge variant="muted" className="ml-1 font-mono">
              {runs.length}
            </Badge>
          </TabsTrigger>
          <TabsTrigger value="trend">Trend</TabsTrigger>
          <TabsTrigger value="feedback">
            User feedback
            {feedback && (
              <Badge variant="muted" className="ml-1 font-mono">
                {feedback.total}
              </Badge>
            )}
          </TabsTrigger>
        </TabsList>

        {/* ----------------------------------------------------- runs */}
        <TabsContent value="runs">
          {running.length > 0 && (
            <div className="mb-3 space-y-2">
              {running.map((run) => (
                <Card key={run.run_id} className="border-blueprint/35 bg-blueprint-soft/25 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[0.8125rem] font-medium">{run.name}</span>
                    <span className="font-mono text-[0.6875rem] tabular-nums text-muted-foreground">
                      {run.completed_items}/{run.total_items}
                    </span>
                  </div>
                  <Progress
                    value={(run.completed_items / Math.max(1, run.total_items)) * 100}
                    className="mt-2"
                  />
                  <p className="mt-1.5 text-[0.6875rem] text-muted-foreground">
                    One generation plus three judge calls per sample. Started {timeAgo(run.started_at)}.
                  </p>
                </Card>
              ))}
            </div>
          )}

          {runsQ.isPending ? (
            <TextSkeleton lines={6} />
          ) : runsQ.isError ? (
            <ErrorState
              resource="evaluation runs"
              error={runsQ.error}
              onRetry={() => void runsQ.refetch()}
            />
          ) : runs.length === 0 ? (
            <EmptyState
              title="No evaluation runs yet"
              description="A run scores a dataset of questions against the live pipeline. Runs cost real money — one generation and three judge calls per sample — which is why they are started deliberately rather than on a timer."
            />
          ) : (
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Run</TableHead>
                    <TableHead>Dataset</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead numeric>Items</TableHead>
                    <TableHead>Faithfulness</TableHead>
                    <TableHead>Answer rel.</TableHead>
                    <TableHead>Context rel.</TableHead>
                    <TableHead>Started</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {runs.map((run) => (
                    <RunRow key={run.run_id} run={run} />
                  ))}
                </TableBody>
              </Table>
            </Card>
          )}
        </TabsContent>

        {/* ---------------------------------------------------- trend */}
        <TabsContent value="trend">
          {trend.length < 2 ? (
            <EmptyState
              title="Not enough completed runs to plot"
              description={`Two or more completed runs of ${dataset} are needed before a trend means anything.`}
            />
          ) : (
            <ChartFrame
              title={`${dataset} over time`}
              description="Each point is a completed run. The dashed line is the faithfulness floor in policy."
              height={280}
              legend={[
                { label: "Faithfulness", color: "var(--chart-1)" },
                { label: "Answer relevancy", color: "var(--chart-2)" },
                { label: "Context relevancy", color: "var(--chart-3)" },
              ]}
            >
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={trend} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                  <CartesianGrid {...chartGrid} />
                  <XAxis
                    dataKey="t"
                    {...chartAxis}
                    tickFormatter={(v) => new Date(v).toLocaleDateString(undefined, { day: "numeric", month: "short" })}
                  />
                  <YAxis {...chartAxis} domain={[0.5, 1]} />
                  <RTooltip {...chartTooltip} labelFormatter={(v) => new Date(v as string).toLocaleString()} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Line type="monotone" dataKey="faithfulness" stroke="var(--chart-1)" strokeWidth={1.8} dot={{ r: 2.5 }} />
                  <Line type="monotone" dataKey="answer_relevancy" stroke="var(--chart-2)" strokeWidth={1.8} dot={{ r: 2.5 }} />
                  <Line type="monotone" dataKey="context_relevancy" stroke="var(--chart-3)" strokeWidth={1.8} dot={{ r: 2.5 }} />
                  <Line
                    type="monotone"
                    dataKey={() => 0.75}
                    stroke="var(--status-error)"
                    strokeDasharray="4 4"
                    strokeWidth={1}
                    dot={false}
                    legendType="none"
                    name="floor"
                  />
                </LineChart>
              </ResponsiveContainer>
            </ChartFrame>
          )}
        </TabsContent>

        {/* ------------------------------------------------- feedback */}
        <TabsContent value="feedback">
          {feedbackQ.isPending ? (
            <TileRowSkeleton />
          ) : feedbackQ.isError ? (
            <ErrorState
              resource="feedback"
              error={feedbackQ.error}
              onRetry={() => void feedbackQ.refetch()}
            />
          ) : feedback ? (
            <div className="space-y-5">
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <StatTile label="Responses, 7 d" value={feedback.total} />
                <StatTile
                  label="Average rating"
                  value={feedback.average_rating?.toFixed(2) ?? "—"}
                  unit="/ 5"
                  icon={StarIcon}
                  tone={(feedback.average_rating ?? 0) < 3 ? "warn" : "ok"}
                />
                {/* The server counts negatives, not positives. Deriving it
                    from total - negative would be wrong: a 3-star rating is
                    neither. Shown as unknown rather than as a made-up zero. */}
                <StatTile label="Positive" value={feedback.positive ?? "—"} tone="ok" />
                <StatTile
                  label="Negative"
                  value={feedback.negative}
                  tone={feedback.negative ? "error" : "default"}
                  icon={MessageSquareWarningIcon}
                />
              </div>

              {Object.keys(feedback.by_tag ?? {}).length > 0 && (
                <Section title="What people report" dense>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(feedback.by_tag ?? {})
                      .sort((a, b) => b[1] - a[1])
                      .map(([tag, count]) => (
                        <span
                          key={tag}
                          className={cn(
                            "inline-flex items-center gap-1.5 rounded-sm border px-2 py-1 text-[0.75rem]",
                            tag === "helpful"
                              ? "border-ok/35 bg-ok-soft text-ok"
                              : "border-border bg-card",
                          )}
                        >
                          {tag.replace(/_/g, " ")}
                          <span className="font-mono tabular-nums text-muted-foreground">{count}</span>
                        </span>
                      ))}
                  </div>
                </Section>
              )}

              {feedback.recent && feedback.recent.length > 0 && (
                <Section
                  title="Recent comments"
                  description="The specific complaints behind the numbers."
                  actions={
                    hasRole("steward", "admin") && (
                      <Hint label="Folds every negative rating into a golden dataset the CI gate measures against. This changes the definition of passing, so it is restricted to stewards and admins.">
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => promote.mutate()}
                          disabled={promote.isPending}
                        >
                          <UploadIcon />
                          Promote negatives to dataset
                        </Button>
                      </Hint>
                    )
                  }
                >
                  <div className="space-y-2">
                    {feedback.recent.map((entry) => (
                      <Card key={entry.id} className="p-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="flex items-center gap-0.5">
                            {Array.from({ length: 5 }, (_, i) => (
                              <StarIcon
                                key={i}
                                className={cn(
                                  "size-3",
                                  i < entry.rating
                                    ? entry.rating <= 2
                                      ? "fill-error text-error"
                                      : "fill-primary text-primary"
                                    : "text-muted-foreground/30",
                                )}
                              />
                            ))}
                          </span>
                          {entry.tags.map((tag) => (
                            <Badge key={tag} variant={tag === "helpful" ? "ok" : "outline"}>
                              {tag.replace(/_/g, " ")}
                            </Badge>
                          ))}
                          <span className="ml-auto font-mono text-[0.625rem] tabular-nums text-muted-foreground">
                            {timeAgo(entry.created_at)}
                          </span>
                        </div>
                        <p className="mt-1.5 text-[0.75rem] text-muted-foreground">
                          Q: <span className="text-foreground">{entry.question}</span>
                        </p>
                        {entry.comment && (
                          <p className="mt-1 text-pretty text-[0.8125rem] leading-relaxed">
                            {entry.comment}
                          </p>
                        )}
                        <p className="mt-1.5 flex items-center gap-2 font-mono text-[0.625rem] text-muted-foreground">
                          <span>{entry.user_email}</span>
                          <span>·</span>
                          <span>trace {entry.trace_id.slice(0, 12)}</span>
                        </p>
                      </Card>
                    ))}
                  </div>
                </Section>
              )}
            </div>
          ) : null}
        </TabsContent>
      </Tabs>

      <StartRunDialog
        open={runDialogOpen}
        onOpenChange={setRunDialogOpen}
        datasets={datasetsQ.data ?? []}
      />
    </Page>
  )
}

function RunRow({ run }: { run: EvaluationRun }) {
  const metric = (name: string) => run.metrics.find((m) => m.name === name)?.value
  const floors: Record<string, number> = {
    faithfulness: 0.75,
    answer_relevancy: 0.7,
    context_relevancy: 0.6,
  }

  return (
    <TableRow>
      <TableCell className="max-w-[14rem]">
        <p className="truncate text-[0.8125rem] font-medium">{run.name}</p>
        <p className="font-mono text-[0.625rem] text-muted-foreground">
          {run.run_id.slice(0, 8)}
        </p>
      </TableCell>
      <TableCell className="font-mono text-[0.75rem] text-muted-foreground">
        {run.dataset_name}
      </TableCell>
      <TableCell>
        <Badge
          variant={
            run.status === "completed" ? "ok" : run.status === "failed" ? "error" : "technical"
          }
        >
          {run.status}
        </Badge>
        {run.error_message && (
          <Hint label={run.error_message}>
            <TriangleAlertIcon className="ml-1.5 inline size-3 text-error" />
          </Hint>
        )}
      </TableCell>
      <TableCell numeric>
        {run.completed_items}/{run.total_items}
      </TableCell>
      {(["faithfulness", "answer_relevancy", "context_relevancy"] as const).map((name) => {
        const value = metric(name)
        const pass = value == null || value >= floors[name]
        return (
          <TableCell key={name} numeric>
            {value == null ? (
              <span className="text-muted-foreground">—</span>
            ) : (
              <span className={cn(!pass && "font-semibold text-error")}>{value.toFixed(3)}</span>
            )}
          </TableCell>
        )
      })}
      <TableCell className="whitespace-nowrap text-[0.8125rem] text-muted-foreground">
        <Hint label={formatDate(run.started_at, true)}>
          <span>{timeAgo(run.started_at)}</span>
        </Hint>
      </TableCell>
    </TableRow>
  )
}

function StartRunDialog({
  open,
  onOpenChange,
  datasets,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  datasets: string[]
}) {
  const queryClient = useQueryClient()
  const [name, setName] = React.useState("manual run")
  const [dataset, setDataset] = React.useState(datasets[0] ?? "golden_set_v1")
  const [inline, setInline] = React.useState("")

  const start = useMutation({
    mutationFn: () =>
      api.startEvaluationRun({
        name,
        dataset_name: dataset,
        questions: inline.trim()
          ? inline.split("\n").map((q) => q.trim()).filter(Boolean)
          : undefined,
      }),
    onSuccess: (result) => {
      toast.success("Evaluation started", { description: result.message })
      void queryClient.invalidateQueries({ queryKey: ["eval-runs"] })
      onOpenChange(false)
    },
    onError: (error) =>
      toast.error("Could not start the run", {
        description: error instanceof Error ? error.message : undefined,
      }),
  })

  const questionCount = inline.split("\n").filter((q) => q.trim()).length

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Start an evaluation run</DialogTitle>
          <DialogDescription>
            One generation plus three judge calls per sample. Results feed the CI quality gate.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3.5">
          <div className="space-y-1.5">
            <Label htmlFor="run-name">Run name</Label>
            <Input id="run-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="run-dataset">Dataset</Label>
            <Select value={dataset} onValueChange={setDataset}>
              <SelectTrigger id="run-dataset">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {datasets.map((d) => (
                  <SelectItem key={d} value={d}>
                    {d}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <Separator />

          <div className="space-y-1.5">
            <Label htmlFor="run-inline">Or run ad-hoc questions</Label>
            <textarea
              id="run-inline"
              value={inline}
              onChange={(e) => setInline(e.target.value)}
              rows={4}
              placeholder={"One question per line.\nWhat is the base plate thickness on CG4-S-104?\nWhich drawings use IS 2062 E350 C?"}
              className="w-full rounded-md border border-input bg-card px-3 py-2 font-mono text-[0.8125rem] outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/25"
            />
            <p className="text-[0.6875rem] text-muted-foreground">
              {questionCount > 0
                ? `${questionCount} inline ${questionCount === 1 ? "question" : "questions"} — the dataset file is ignored.`
                : "Leave blank to use the dataset file."}
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => start.mutate()} disabled={start.isPending}>
            <PlayIcon />
            Start run
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
