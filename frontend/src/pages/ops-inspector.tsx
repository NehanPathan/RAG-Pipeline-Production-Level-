import * as React from "react"
import { useMutation } from "@tanstack/react-query"
import {
  Bar,
  BarChart,
  Cell,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from "recharts"
import {
  ArrowRightIcon,
  EyeOffIcon,
  LayersIcon,
  PlayIcon,
  ShuffleIcon,
  SlidersHorizontalIcon,
  SparklesIcon,
  TargetIcon,
} from "lucide-react"
import { api } from "@/api"
import type { RetrievalInspection } from "@/api/types"
import { cn, formatMs, truncate } from "@/lib/utils"
import { Page } from "@/components/layout/page"
import { PageHeader, Section } from "@/components/domain/layout"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Hint } from "@/components/ui/tooltip"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { chartAxis, chartTooltip } from "@/components/domain/chart"
import { EmptyState, ErrorState, TextSkeleton } from "@/components/domain/states"

/**
 * Retrieval inspector.
 *
 * Modules A–D only — no answer is generated. The point is to see where a bad
 * answer came from: whether the query rewrite went wrong, whether the vector
 * side or the keyword side found the passage, what fusion did to the ordering,
 * and what the reranker promoted. It also reports how many candidates the
 * clearance filter removed, so "nothing matched" is distinguishable from
 * "matches existed but you are not cleared for them".
 */
const STAGE_COLORS = [
  "var(--chart-2)",
  "var(--chart-1)",
  "var(--chart-3)",
  "var(--chart-5)",
  "var(--chart-4)",
]

export default function InspectorPage() {
  const [query, setQuery] = React.useState("")

  const inspect = useMutation({
    mutationFn: (q: string) => api.inspectRetrieval(q),
  })

  const result = inspect.data

  return (
    <Page>
      <PageHeader
        eyebrow="Operations"
        title="Retrieval inspector"
        description="Runs the retrieval half of the pipeline and stops. No model is asked to answer, so what you see is exactly what the generator would have been handed."
      />

      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (query.trim()) inspect.mutate(query.trim())
        }}
        className="mt-4 flex gap-2"
      >
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Type the question that produced a bad answer…"
          className="h-10"
        />
        <Button type="submit" size="lg" className="h-10" disabled={!query.trim() || inspect.isPending}>
          <PlayIcon />
          Inspect
        </Button>
      </form>

      <div className="mt-5">
        {inspect.isPending ? (
          <div className="space-y-4">
            <TextSkeleton lines={2} />
            <TextSkeleton lines={8} />
          </div>
        ) : inspect.isError ? (
          <ErrorState error={inspect.error} resource="retrieval inspection" />
        ) : !result ? (
          <EmptyState
            icon={SlidersHorizontalIcon}
            title="Nothing inspected yet"
            description="Paste in a question that returned a wrong or empty answer. The inspector shows the rewritten query, both retrieval arms separately, what reciprocal rank fusion did to the ordering, and what the cross-encoder promoted."
          />
        ) : (
          <Inspection result={result} />
        )}
      </div>
    </Page>
  )
}

function Inspection({ result }: { result: RetrievalInspection }) {
  const latency = result.latency_breakdown
  const stages = [
    { name: "Query", ms: latency.query_processing_ms },
    { name: "Vector", ms: latency.vector_search_ms },
    { name: "BM25", ms: latency.bm25_search_ms },
    { name: "Fusion", ms: latency.fusion_ms },
    { name: "Rerank", ms: latency.reranking_ms },
  ]

  const vectorIds = new Set(result.vector_results.map((h) => h.chunk_id))
  const bm25Ids = new Set(result.bm25_results.map((h) => h.chunk_id))
  const both = [...vectorIds].filter((id) => bm25Ids.has(id)).length

  const contentById = new Map(
    [...result.vector_results, ...result.bm25_results].map((h) => [h.chunk_id, h]),
  )

  return (
    <div className="space-y-5">
      {/* ---------------------------------------------------- query plan */}
      <Card className="p-4">
        <p className="eyebrow mb-3">Query processing</p>
        <dl className="grid gap-3 md:grid-cols-2">
          <div className="space-y-1">
            <dt className="text-[0.6875rem] uppercase tracking-[0.06em] text-muted-foreground">
              As asked
            </dt>
            <dd className="font-mono text-[0.8125rem]">{result.original_query}</dd>
          </div>
          <div className="space-y-1">
            <dt className="text-[0.6875rem] uppercase tracking-[0.06em] text-muted-foreground">
              Rewritten
            </dt>
            <dd className="font-mono text-[0.8125rem]">{result.processed_query.rewritten}</dd>
          </div>
          <div className="space-y-1 md:col-span-2">
            <dt className="text-[0.6875rem] uppercase tracking-[0.06em] text-muted-foreground">
              Expansions
            </dt>
            <dd className="flex flex-wrap gap-1.5">
              {result.processed_query.expanded.map((expansion) => (
                <Badge key={expansion} variant="outline" className="font-mono">
                  {expansion}
                </Badge>
              ))}
            </dd>
          </div>
        </dl>
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
          <Badge variant="technical">intent: {result.processed_query.intent}</Badge>
          {result.processed_query.domain && (
            <Badge variant="outline">domain: {result.processed_query.domain}</Badge>
          )}
          {result.processed_query.selected_sources.map((source) => (
            <Badge key={source} variant="muted" className="font-mono">
              {source}
            </Badge>
          ))}
          <span className="ml-auto font-mono text-[0.6875rem] tabular-nums text-muted-foreground">
            trace {result.governance.trace_id.slice(0, 16)}
          </span>
        </div>
      </Card>

      {/* ------------------------------------------------------- summary */}
      <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <Card className="p-4">
          <p className="eyebrow mb-3">Where the time went</p>
          <div className="h-36">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={stages} layout="vertical" margin={{ top: 0, right: 40, left: 8, bottom: 0 }}>
                <XAxis type="number" {...chartAxis} hide />
                <YAxis type="category" dataKey="name" {...chartAxis} width={54} />
                <RTooltip {...chartTooltip} formatter={(v) => [`${v} ms`, "duration"]} />
                <Bar dataKey="ms" radius={[0, 3, 3, 0]} maxBarSize={18}>
                  {stages.map((_, i) => (
                    <Cell key={i} fill={STAGE_COLORS[i]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-1 border-t border-border pt-2.5 text-[0.6875rem] text-muted-foreground">
            Total{" "}
            <span className="font-mono tabular-nums text-foreground">
              {formatMs(latency.total_ms)}
            </span>
            {latency.reranking_ms > latency.total_ms * 0.5 && (
              <> — the cross-encoder dominates. Lowering <code className="font-mono">rerank_top_n</code> is the usual lever.</>
            )}
          </p>
        </Card>

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
          <Card className="space-y-1 p-3.5">
            <p className="eyebrow">Overlap</p>
            <p className="font-mono text-2xl tabular-nums">{both}</p>
            <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
              Chunks found by <i>both</i> arms. High overlap means the query was unambiguous; near
              zero means one arm is carrying the result alone.
            </p>
          </Card>
          <Card
            className={cn(
              "space-y-1 p-3.5",
              result.governance.blocked_by_clearance > 0 && "border-warn/40 bg-warn-soft/25",
            )}
          >
            <p className="eyebrow flex items-center gap-1.5">
              <EyeOffIcon className="size-3" />
              Withheld
            </p>
            <p
              className={cn(
                "font-mono text-2xl tabular-nums",
                result.governance.blocked_by_clearance > 0 && "text-warn",
              )}
            >
              {result.governance.blocked_by_clearance}
            </p>
            <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
              Candidates the clearance filter removed. Surfaced so a classification filter is never
              mistaken for a retrieval bug.
            </p>
          </Card>
        </div>
      </div>

      {/* --------------------------------------------------------- stages */}
      <Tabs defaultValue="fused">
        <TabsList>
          <TabsTrigger value="vector">
            <SparklesIcon />
            Vector
            <Badge variant="muted" className="ml-1 font-mono">
              {result.vector_results.length}
            </Badge>
          </TabsTrigger>
          <TabsTrigger value="bm25">
            <LayersIcon />
            BM25
            <Badge variant="muted" className="ml-1 font-mono">
              {result.bm25_results.length}
            </Badge>
          </TabsTrigger>
          <TabsTrigger value="fused">
            <ShuffleIcon />
            Fused
            <Badge variant="muted" className="ml-1 font-mono">
              {result.fused_results.length}
            </Badge>
          </TabsTrigger>
          <TabsTrigger value="reranked">
            <TargetIcon />
            Reranked
            <Badge variant="muted" className="ml-1 font-mono">
              {result.reranked_results.length}
            </Badge>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="vector">
          <Section
            dense
            description="Dense retrieval from Qdrant. Finds passages that mean the same thing in different words — “baseplate thickness” matching “PL 500×500×32”."
          >
            <HitTable
              rows={result.vector_results.map((h) => ({
                rank: h.rank,
                chunk_id: h.chunk_id,
                score: h.vector_score ?? 0,
                label: "cosine",
                document: h.document_name,
                page: h.page_number,
                content: h.content,
              }))}
            />
          </Section>
        </TabsContent>

        <TabsContent value="bm25">
          <Section
            dense
            description="Keyword retrieval from Elasticsearch. Finds exact designations a vector model blurs — ISMB 300 and ISMB 400 are close in embedding space and completely different in a schedule."
          >
            <HitTable
              rows={result.bm25_results.map((h) => ({
                rank: h.rank,
                chunk_id: h.chunk_id,
                score: h.bm25_score ?? 0,
                label: "bm25",
                document: h.document_name,
                page: h.page_number,
                content: h.content,
              }))}
            />
          </Section>
        </TabsContent>

        <TabsContent value="fused">
          <Section
            dense
            description="Reciprocal rank fusion. Combines the two orderings by rank rather than by score, because a cosine similarity and a BM25 score are not on the same scale and normalising them invents a comparison that does not exist."
          >
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead numeric>#</TableHead>
                    <TableHead>Chunk</TableHead>
                    <TableHead>Found by</TableHead>
                    <TableHead numeric>Vector</TableHead>
                    <TableHead numeric>BM25</TableHead>
                    <TableHead numeric>RRF</TableHead>
                    <TableHead>Preview</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {result.fused_results.map((hit) => {
                    const source = contentById.get(hit.chunk_id)
                    const inBoth = hit.vector_score != null && hit.bm25_score != null
                    return (
                      <TableRow key={hit.chunk_id}>
                        <TableCell numeric>{hit.rank}</TableCell>
                        <TableCell className="font-mono text-[0.75rem]">
                          {hit.chunk_id.slice(0, 8)}
                        </TableCell>
                        <TableCell>
                          <div className="flex gap-1">
                            {hit.vector_score != null && (
                              <Badge variant="technical" className="font-mono">
                                vec
                              </Badge>
                            )}
                            {hit.bm25_score != null && (
                              <Badge variant="outline" className="font-mono">
                                bm25
                              </Badge>
                            )}
                            {inBoth && (
                              <Hint label="Found by both arms — the strongest signal RRF has.">
                                <Badge variant="ok">both</Badge>
                              </Hint>
                            )}
                          </div>
                        </TableCell>
                        <TableCell numeric>
                          {hit.vector_score?.toFixed(4) ?? "—"}
                        </TableCell>
                        <TableCell numeric>{hit.bm25_score?.toFixed(2) ?? "—"}</TableCell>
                        <TableCell numeric className="font-semibold">
                          {hit.rrf_score.toFixed(5)}
                        </TableCell>
                        <TableCell className="max-w-md text-[0.75rem] text-muted-foreground">
                          {truncate((source?.content ?? "").replace(/\n/g, " "), 90)}
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </Card>
          </Section>
        </TabsContent>

        <TabsContent value="reranked">
          <Section
            dense
            description="Cross-encoder rerank. Reads the query and each candidate together rather than comparing two independent embeddings, which is why it moves things — and why it is the most expensive stage."
          >
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead numeric>Final</TableHead>
                    <TableHead numeric>Was</TableHead>
                    <TableHead>Movement</TableHead>
                    <TableHead>Chunk</TableHead>
                    <TableHead numeric>Rerank score</TableHead>
                    <TableHead>Preview</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {result.reranked_results.map((hit) => {
                    const before = result.fused_results.find((f) => f.chunk_id === hit.chunk_id)?.rank
                    const delta = before != null ? before - hit.final_rank : 0
                    const source = contentById.get(hit.chunk_id)
                    return (
                      <TableRow key={hit.chunk_id}>
                        <TableCell numeric className="font-semibold">
                          {hit.final_rank}
                        </TableCell>
                        <TableCell numeric className="text-muted-foreground">
                          {before ?? "—"}
                        </TableCell>
                        <TableCell>
                          {delta === 0 ? (
                            <span className="text-[0.75rem] text-muted-foreground">—</span>
                          ) : (
                            <span
                              className={cn(
                                "inline-flex items-center gap-0.5 font-mono text-[0.6875rem] tabular-nums",
                                delta > 0 ? "text-ok" : "text-error",
                              )}
                            >
                              {delta > 0 ? "▲" : "▼"} {Math.abs(delta)}
                            </span>
                          )}
                        </TableCell>
                        <TableCell className="font-mono text-[0.75rem]">
                          {hit.chunk_id.slice(0, 8)}
                        </TableCell>
                        <TableCell numeric>{hit.rerank_score.toFixed(4)}</TableCell>
                        <TableCell className="max-w-md text-[0.75rem] text-muted-foreground">
                          {truncate((source?.content ?? "").replace(/\n/g, " "), 90)}
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </Card>
          </Section>
        </TabsContent>
      </Tabs>

      <Card className="flex flex-wrap items-center justify-between gap-3 bg-muted/30 p-3.5">
        <p className="max-w-2xl text-[0.6875rem] leading-relaxed text-muted-foreground">
          <b className="text-foreground">Reading this.</b> If the passage you expected is absent
          from <i>both</i> arms, the problem is upstream — the chunk was never indexed, or the
          clearance filter removed it. If it is present but ranks low after rerank, the problem is
          the reranker or the query rewrite, not retrieval.
        </p>
        <Button variant="outline" size="sm" asChild>
          <a href="/ops/settings">
            Retrieval settings
            <ArrowRightIcon />
          </a>
        </Button>
      </Card>
    </div>
  )
}

function HitTable({
  rows,
}: {
  rows: {
    rank: number
    chunk_id: string
    score: number
    label: string
    document: string | null
    page: number | null
    content: string
  }[]
}) {
  const max = Math.max(...rows.map((r) => r.score), 0.0001)
  return (
    <Card>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead numeric>#</TableHead>
            <TableHead>Document</TableHead>
            <TableHead numeric>Pg</TableHead>
            <TableHead className="w-40">Score</TableHead>
            <TableHead>Passage</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.chunk_id}>
              <TableCell numeric>{row.rank}</TableCell>
              <TableCell className="max-w-[16rem] truncate text-[0.8125rem]">
                {row.document ?? "—"}
              </TableCell>
              <TableCell numeric>{row.page ?? "—"}</TableCell>
              <TableCell>
                <span className="flex items-center gap-2">
                  <span className="relative h-1.5 w-16 overflow-hidden rounded-full bg-muted">
                    <span
                      className="absolute inset-y-0 left-0 rounded-full bg-chart-2"
                      style={{ width: `${(row.score / max) * 100}%` }}
                    />
                  </span>
                  <span className="font-mono text-[0.6875rem] tabular-nums">
                    {row.score.toFixed(row.label === "bm25" ? 2 : 4)}
                  </span>
                </span>
              </TableCell>
              <TableCell className="max-w-lg text-[0.75rem] text-muted-foreground">
                {truncate(row.content.replace(/\n/g, " "), 120)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  )
}
