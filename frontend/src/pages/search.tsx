import * as React from "react"
import { Link, useSearchParams } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import {
  FilterXIcon,
  MessagesSquareIcon,
  SearchIcon,
  SearchXIcon,
  SlidersHorizontalIcon,
} from "lucide-react"
import { api } from "@/api"
import type { FacetValue, SearchHit, SearchRequest } from "@/api/types"
import { cn, formatNumber } from "@/lib/utils"
import { Page } from "@/components/layout/page"
import { PageHeader } from "@/components/domain/layout"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
  Checkbox,
  Label,
  ScrollArea,
  SegmentedTrack,
  Skeleton,
  Switch,
  ToggleGroup,
  ToggleGroupItem,
} from "@/components/ui/misc"
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { Hint } from "@/components/ui/tooltip"
import {
  ContentKindBadge,
  DrawingNumber,
  EntityChip,
  RevisionChip,
} from "@/components/domain/badges"
import { EmptyState, ErrorState } from "@/components/domain/states"

/** Facet fields worth a sidebar section, in the order an engineer scans them. */
const FACET_SECTIONS: { field: string; label: string; hint: string; limit: number }[] = [
  { field: "project_number", label: "Project", hint: "Only projects you are a member of appear here.", limit: 8 },
  { field: "entity_canonicals", label: "Steel entity", hint: "Section designations, grades and bolt classes, canonicalised — ISMB300, ISMB 300 and I.S.M.B.-300 are one value.", limit: 12 },
  { field: "drawing_number", label: "Drawing", hint: "Drawing identity, independent of revision.", limit: 10 },
  { field: "content_kind", label: "Provenance", hint: "Whether a dimension here is a CAD value, a plotted string or an OCR reading.", limit: 6 },
  { field: "domain", label: "Domain", hint: "What kind of document this is.", limit: 8 },
  { field: "file_type", label: "File type", hint: "", limit: 6 },
  { field: "revision_label", label: "Revision", hint: "", limit: 8 },
  { field: "sensitivity", label: "Classification", hint: "", limit: 4 },
]

export default function SearchPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [query, setQuery] = React.useState(searchParams.get("q") ?? "")
  const [submitted, setSubmitted] = React.useState(searchParams.get("q") ?? "")
  const [selected, setSelected] = React.useState<Record<string, string[]>>({})
  const [includeSuperseded, setIncludeSuperseded] = React.useState(false)
  const [density, setDensity] = React.useState<"comfortable" | "compact">("comfortable")

  const facetsQ = useQuery({
    queryKey: ["facets", includeSuperseded],
    queryFn: () => api.getFacets({ include_superseded: includeSuperseded }),
  })

  const request: SearchRequest = React.useMemo(
    () => ({
      query: submitted,
      project_ids: [],
      drawing_numbers: selected.drawing_number ?? [],
      entity_canonicals: selected.entity_canonicals ?? [],
      tags: selected.tags ?? [],
      file_type: selected.file_type?.[0] ?? null,
      domain: selected.domain?.[0] ?? null,
      content_kinds: selected.content_kind ?? [],
      include_superseded: includeSuperseded,
      top_k: 60,
    }),
    [submitted, selected, includeSuperseded],
  )

  const resultsQ = useQuery({
    queryKey: ["search", request],
    queryFn: () => api.search(request),
  })

  // Project and revision facets are applied client-side: the API filters
  // projects by id, and the sidebar shows project *numbers*.
  const hits = React.useMemo(() => {
    let items = resultsQ.data?.items ?? []
    const projects = selected.project_number
    if (projects?.length) items = items.filter((h) => h.project_number && projects.includes(h.project_number))
    const revisions = selected.revision_label
    if (revisions?.length) items = items.filter((h) => h.revision_label && revisions.includes(h.revision_label))
    return items
  }, [resultsQ.data, selected])

  const activeCount = Object.values(selected).reduce((sum, values) => sum + values.length, 0)

  function toggle(field: string, value: string) {
    setSelected((current) => {
      const values = current[field] ?? []
      const next = values.includes(value)
        ? values.filter((v) => v !== value)
        : [...values, value]
      const updated = { ...current, [field]: next }
      if (next.length === 0) delete updated[field]
      return updated
    })
  }

  function submit(event?: React.FormEvent) {
    event?.preventDefault()
    setSubmitted(query)
    setSearchParams(query ? { q: query } : {}, { replace: true })
  }

  const filters = (
    <FacetPanel
      facets={facetsQ.data?.facets ?? {}}
      loading={facetsQ.isPending}
      selected={selected}
      onToggle={toggle}
      onClear={() => setSelected({})}
      activeCount={activeCount}
    />
  )

  return (
    <Page>
      <PageHeader
        eyebrow="Register search"
        title="Search every sheet"
        description="Keyword and semantic search across chunks, narrowed by project, steel entity, drawing and provenance. An empty query with filters set is a legitimate browse — that is how a drawing register is read."
        actions={
          <Button asChild variant="outline" size="sm">
            <Link to={`/ask${submitted ? `?q=${encodeURIComponent(submitted)}` : ""}`}>
              <MessagesSquareIcon />
              Ask this instead
            </Link>
          </Button>
        }
      />

      <form onSubmit={submit} className="mt-4 flex gap-2">
        <div className="relative min-w-0 flex-1">
          <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="ISMB 300, base plate, M24 HSFG, grout gap…"
            className="h-10 pl-9"
          />
        </div>
        <Button type="submit" size="lg" className="h-10">
          Search
        </Button>
        <Sheet>
          <SheetTrigger asChild>
            <Button variant="outline" size="lg" className="h-10 lg:hidden">
              <SlidersHorizontalIcon />
              Filters
              {activeCount > 0 && <Badge className="ml-1">{activeCount}</Badge>}
            </Button>
          </SheetTrigger>
          <SheetContent side="left" className="w-[300px] p-0">
            <SheetHeader>
              <SheetTitle>Filters</SheetTitle>
            </SheetHeader>
            <ScrollArea className="min-h-0 flex-1">
              <div className="p-3">{filters}</div>
            </ScrollArea>
          </SheetContent>
        </Sheet>
      </form>

      <div className="mt-5 grid gap-5 lg:grid-cols-[15rem_1fr] xl:grid-cols-[16.5rem_1fr]">
        <aside className="hidden lg:block">
          <div className="sticky top-[4.5rem] space-y-4">
            <div className="space-y-2 rounded-md border border-border bg-card p-3">
              <Hint label="Superseded revisions are excluded by default: answering from an old sheet is worse than not answering.">
                <Label className="cursor-pointer justify-between">
                  <span className="text-[0.8125rem]">Include superseded</span>
                  <Switch checked={includeSuperseded} onCheckedChange={setIncludeSuperseded} />
                </Label>
              </Hint>
              <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
                {includeSuperseded
                  ? "Old revisions are in scope. Check the revision chip on every hit."
                  : "Only current issues are searched."}
              </p>
            </div>
            <ScrollArea className="max-h-[calc(100svh-16rem)]">{filters}</ScrollArea>
          </div>
        </aside>

        <div className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-muted-foreground">
              {resultsQ.isPending ? (
                "Searching…"
              ) : (
                <>
                  <span className="font-mono tabular-nums text-foreground">
                    {formatNumber(hits.length)}
                  </span>{" "}
                  {hits.length === 1 ? "passage" : "passages"}
                  {resultsQ.data && resultsQ.data.total > hits.length && (
                    <> of {formatNumber(resultsQ.data.total)} matched</>
                  )}
                  {submitted && (
                    <>
                      {" "}
                      for <span className="text-foreground">“{submitted}”</span>
                    </>
                  )}
                </>
              )}
            </p>
            <SegmentedTrack>
              <ToggleGroup
                type="single"
                value={density}
                onValueChange={(v) => v && setDensity(v as "comfortable" | "compact")}
              >
                <ToggleGroupItem value="comfortable">Comfortable</ToggleGroupItem>
                <ToggleGroupItem value="compact">Compact</ToggleGroupItem>
              </ToggleGroup>
            </SegmentedTrack>
          </div>

          {activeCount > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              {Object.entries(selected).flatMap(([field, values]) =>
                values.map((value) => (
                  <button
                    key={`${field}:${value}`}
                    onClick={() => toggle(field, value)}
                    className="inline-flex items-center gap-1 rounded-sm border border-primary/40 bg-primary-soft px-1.5 py-0.5 font-mono text-[0.6875rem] text-primary transition-colors hover:border-primary"
                  >
                    {value}
                    <span aria-hidden>×</span>
                  </button>
                )),
              )}
              <Button variant="ghost" size="xs" onClick={() => setSelected({})}>
                <FilterXIcon />
                Clear all
              </Button>
            </div>
          )}

          {resultsQ.isError ? (
            <ErrorState error={resultsQ.error} onRetry={() => resultsQ.refetch()} resource="search" />
          ) : resultsQ.isPending ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Card key={i} className="space-y-2 p-3.5">
                  <div className="flex gap-2">
                    <Skeleton className="h-3 w-24" />
                    <Skeleton className="h-3 w-16" />
                  </div>
                  <Skeleton className="h-3 w-full" />
                  <Skeleton className="h-3 w-4/5" />
                </Card>
              ))}
            </div>
          ) : hits.length === 0 ? (
            <EmptyState
              icon={SearchXIcon}
              title={submitted ? `Nothing matched “${submitted}”` : "No passages in scope"}
              description={
                activeCount > 0 ? (
                  <>
                    The filters may be too narrow. Try clearing one, or enable{" "}
                    <b>Include superseded</b> if the sheet you want has since been revised.
                  </>
                ) : (
                  <>
                    Try a section designation (<code className="font-mono">ISMB 300</code>), a
                    piece mark (<code className="font-mono">BM-14</code>) or a phrase from the
                    notes. Facet counts on the left show the corpus's actual vocabulary — nothing
                    listed there returns zero.
                  </>
                )
              }
              action={
                activeCount > 0 ? (
                  <Button size="sm" variant="outline" onClick={() => setSelected({})}>
                    Clear filters
                  </Button>
                ) : (
                  <Button asChild size="sm">
                    <Link to={`/ask?q=${encodeURIComponent(submitted)}`}>Ask it as a question</Link>
                  </Button>
                )
              }
            />
          ) : (
            <div className="space-y-2">
              {hits.map((hit) => (
                <HitCard key={hit.chunk_id} hit={hit} query={submitted} compact={density === "compact"} />
              ))}
            </div>
          )}
        </div>
      </div>
    </Page>
  )
}

/* ---------------------------------------------------------------- facets */

function FacetPanel({
  facets,
  loading,
  selected,
  onToggle,
  onClear,
  activeCount,
}: {
  facets: Record<string, FacetValue[]>
  loading: boolean
  selected: Record<string, string[]>
  onToggle: (field: string, value: string) => void
  onClear: () => void
  activeCount: number
}) {
  const [expanded, setExpanded] = React.useState<Record<string, boolean>>({})

  if (loading) {
    return (
      <div className="space-y-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="space-y-2">
            <Skeleton className="h-3 w-20" />
            {Array.from({ length: 4 }).map((_, j) => (
              <Skeleton key={j} className="h-3 w-full" />
            ))}
          </div>
        ))}
      </div>
    )
  }

  const sections = FACET_SECTIONS.filter((section) => (facets[section.field] ?? []).length > 0)

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between px-1 pb-1">
        <span className="eyebrow">Filters</span>
        {activeCount > 0 && (
          <button onClick={onClear} className="text-[0.6875rem] text-primary hover:underline">
            Clear
          </button>
        )}
      </div>
      <Accordion
        type="multiple"
        defaultValue={["project_number", "entity_canonicals", "content_kind"]}
        className="rounded-md border border-border bg-card px-3"
      >
        {sections.map((section) => {
          const values = facets[section.field] ?? []
          const isExpanded = expanded[section.field]
          const shown = isExpanded ? values : values.slice(0, section.limit)
          const chosen = selected[section.field] ?? []
          return (
            <AccordionItem key={section.field} value={section.field} className="last:border-0">
              <AccordionTrigger className="text-[0.8125rem]">
                <span className="flex items-center gap-2">
                  {section.label}
                  {chosen.length > 0 && (
                    <Badge variant="default" className="font-mono">
                      {chosen.length}
                    </Badge>
                  )}
                </span>
              </AccordionTrigger>
              <AccordionContent>
                {section.hint && (
                  <p className="mb-2 text-[0.6875rem] leading-relaxed text-muted-foreground">
                    {section.hint}
                  </p>
                )}
                <div className="space-y-1">
                  {shown.map((facet) => {
                    const id = `${section.field}-${facet.value}`
                    const isChosen = chosen.includes(facet.value)
                    return (
                      <label
                        key={facet.value}
                        htmlFor={id}
                        className={cn(
                          "flex cursor-pointer items-center gap-2 rounded-sm px-1 py-1 transition-colors hover:bg-muted",
                          isChosen && "text-primary",
                        )}
                      >
                        <Checkbox
                          id={id}
                          checked={isChosen}
                          onCheckedChange={() => onToggle(section.field, facet.value)}
                        />
                        <span className="min-w-0 flex-1 truncate font-mono text-[0.75rem]">
                          {facet.value}
                        </span>
                        <span className="shrink-0 font-mono text-[0.625rem] tabular-nums text-muted-foreground">
                          {facet.count}
                        </span>
                      </label>
                    )
                  })}
                  {values.length > section.limit && (
                    <button
                      onClick={() =>
                        setExpanded((current) => ({
                          ...current,
                          [section.field]: !current[section.field],
                        }))
                      }
                      className="px-1 pt-1 text-[0.6875rem] text-primary hover:underline"
                    >
                      {isExpanded ? "Show fewer" : `Show all ${values.length}`}
                    </button>
                  )}
                </div>
              </AccordionContent>
            </AccordionItem>
          )
        })}
      </Accordion>
    </div>
  )
}

/* ------------------------------------------------------------------ hits */

function HitCard({ hit, query, compact }: { hit: SearchHit; query: string; compact: boolean }) {
  return (
    <Card className="group overflow-hidden transition-colors hover:border-primary/35">
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-muted/30 px-3 py-1.5">
        {hit.drawing_number ? (
          <DrawingNumber value={hit.drawing_number} />
        ) : (
          <span className="truncate text-[0.8125rem] font-medium">{hit.document_name}</span>
        )}
        {hit.revision_label && <RevisionChip label={hit.revision_label} />}
        {hit.project_number && (
          <Badge variant="outline" className="font-mono">
            {hit.project_number}
          </Badge>
        )}
        <ContentKindBadge kind={hit.content_kind} compact />
        <div className="ml-auto flex items-center gap-2">
          <Hint label="BM25 relevance for this passage.">
            <span className="font-mono text-[0.625rem] tabular-nums text-muted-foreground">
              {hit.score.toFixed(2)}
            </span>
          </Hint>
          <Link
            to={`/documents/${hit.document_id}?chunk=${hit.chunk_id}${hit.page_number ? `&page=${hit.page_number}` : ""}`}
            className="text-[0.6875rem] font-medium text-muted-foreground transition-colors hover:text-primary"
          >
            Open
          </Link>
        </div>
      </div>

      <div className={cn("space-y-2", compact ? "px-3 py-2" : "px-3 py-2.5")}>
        <p className="flex flex-wrap items-center gap-1.5 text-[0.6875rem] text-muted-foreground">
          {hit.section && <span className="font-medium text-foreground">{hit.section}</span>}
          {hit.page_number != null && <span>· page {hit.page_number}</span>}
          {hit.drawing_number && <span className="truncate">· {hit.document_name}</span>}
        </p>
        <p
          className={cn(
            "whitespace-pre-line text-[0.8125rem] leading-relaxed text-muted-foreground",
            compact ? "line-clamp-2" : "line-clamp-4",
          )}
        >
          {highlight(hit.content, query)}
        </p>
        {hit.entity_canonicals.length > 0 && !compact && (
          <div className="flex flex-wrap gap-1 pt-0.5">
            {hit.entity_canonicals.slice(0, 8).map((entity) => (
              <EntityChip key={entity} canonical={entity} type={entityType(entity)} />
            ))}
            {hit.entity_canonicals.length > 8 && (
              <span className="self-center text-[0.625rem] text-muted-foreground">
                +{hit.entity_canonicals.length - 8}
              </span>
            )}
          </div>
        )}
      </div>
    </Card>
  )
}

/** Marks query terms in a snippet without a Markdown pass. */
function highlight(text: string, query: string): React.ReactNode {
  const terms = query.trim().split(/\s+/).filter((t) => t.length > 1)
  if (terms.length === 0) return text
  // Capturing group means split() keeps the delimiters, so a part is a match
  // exactly when it equals one of the terms. Testing the /g regex here
  // instead would be wrong — lastIndex carries between calls and every other
  // match would come back false.
  const lowered = new Set(terms.map((t) => t.toLowerCase()))
  const pattern = new RegExp(`(${terms.map(escapeRegExp).join("|")})`, "gi")
  return text.split(pattern).map((part, i) =>
    lowered.has(part.toLowerCase()) ? (
      <mark key={i} className="rounded-[2px] bg-highlight/35 px-0.5 text-foreground">
        {part}
      </mark>
    ) : (
      <React.Fragment key={i}>{part}</React.Fragment>
    ),
  )
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

/** Best-effort type for colouring, from the shape of the designation. */
export function entityType(canonical: string): string {
  if (/^M\d+/.test(canonical)) return "bolt"
  if (/fillet|CJP|PJP|E\d{4}/i.test(canonical)) return "weld"
  if (/^(IS |Fe |S\d|A\d|Fy)/.test(canonical)) return "grade"
  if (/^(BM|CL|BR|BP|GP|TR|PL|ST)-\d+/.test(canonical)) return "mark"
  return "section"
}
