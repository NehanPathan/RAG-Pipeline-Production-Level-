import * as React from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  CpuIcon,
  LayersIcon,
  RotateCcwIcon,
  SaveIcon,
  SearchIcon,
  SlidersHorizontalIcon,
  TriangleAlertIcon,
} from "lucide-react"
import { api } from "@/api"
import type { SettingItem } from "@/api/types"
import { cn } from "@/lib/utils"
import { Page } from "@/components/layout/page"
import { PageHeader, Section } from "@/components/domain/layout"
import { Button } from "@/components/ui/button"
import { Input, Textarea } from "@/components/ui/input"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Label, Separator, Slider } from "@/components/ui/misc"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Hint } from "@/components/ui/tooltip"
import { TextSkeleton } from "@/components/domain/states"

interface SettingMeta {
  label: string
  note: string
  group: "models" | "retrieval" | "chunking"
  kind: "model" | "number"
  min?: number
  max?: number
  step?: number
  warnAbove?: number
  warnNote?: string
}

const META: Record<string, SettingMeta> = {
  small_llm: {
    label: "Small model",
    note: "Query rewriting, expansion, routing and judge scoring. Called many times per question, so its cost dominates at volume.",
    group: "models",
    kind: "model",
  },
  large_llm: {
    label: "Large model",
    note: "Answer generation. Called once per question, and the only model whose output a user reads.",
    group: "models",
    kind: "model",
  },
  embedding_model: {
    label: "Retrieval embeddings",
    note: "Changing this invalidates every stored vector — the corpus must be reindexed before search works again.",
    group: "models",
    kind: "model",
  },
  reranker: {
    label: "Reranker",
    note: "Cross-encoder over the fused candidates. The most expensive retrieval stage and the one that most changes ordering.",
    group: "models",
    kind: "model",
  },
  vector_top_k: {
    label: "Vector top-k",
    note: "Candidates pulled from Qdrant before fusion.",
    group: "retrieval",
    kind: "number",
    min: 5,
    max: 100,
    step: 5,
  },
  bm25_top_k: {
    label: "BM25 top-k",
    note: "Candidates pulled from Elasticsearch before fusion.",
    group: "retrieval",
    kind: "number",
    min: 5,
    max: 100,
    step: 5,
  },
  rerank_top_n: {
    label: "Rerank top-n",
    note: "How many fused candidates the cross-encoder scores, and how many reach the generator.",
    group: "retrieval",
    kind: "number",
    min: 3,
    max: 40,
    step: 1,
    warnAbove: 20,
    warnNote:
      "Above about 20 the reranker starts to dominate answer latency, and context relevancy usually falls because more marginal passages reach the prompt.",
  },
  chunk_parent_size: {
    label: "Parent chunk size",
    note: "Tokens in the parent chunk retrieved for context around a matched child.",
    group: "chunking",
    kind: "number",
    min: 256,
    max: 4096,
    step: 128,
  },
  chunk_child_size: {
    label: "Child chunk size",
    note: "Tokens in the embedded unit. Smaller is more precise to match and less useful to read.",
    group: "chunking",
    kind: "number",
    min: 64,
    max: 1024,
    step: 32,
  },
  chunk_overlap: {
    label: "Chunk overlap",
    note: "Tokens repeated between adjacent chunks so a fact spanning a boundary is not lost.",
    group: "chunking",
    kind: "number",
    min: 0,
    max: 256,
    step: 8,
  },
}

const GROUPS = [
  {
    key: "models" as const,
    icon: CpuIcon,
    title: "Models",
    description:
      "Providers are checked against the policy allow-list in the gateway before any request is made.",
  },
  {
    key: "retrieval" as const,
    icon: SearchIcon,
    title: "Retrieval",
    description:
      "How many candidates each arm contributes and how many survive reranking. These three trade recall against latency and cost.",
  },
  {
    key: "chunking" as const,
    icon: LayersIcon,
    title: "Chunking",
    description:
      "Applied at ingestion. Changing these does not alter already-indexed documents — reprocess them to re-derive the corpus.",
  },
]

export default function SettingsPage() {
  const { data: settings, isPending } = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.getSettings(),
  })

  return (
    <Page width="reading">
      <PageHeader
        eyebrow="Operations"
        title="Settings"
        description="Effective values: stored overrides layered over the built-in defaults. Every change is durable, attributable and written to the audit log with its previous value."
      />

      {isPending ? (
        <div className="mt-5">
          <TextSkeleton lines={10} />
        </div>
      ) : (
        <div className="mt-5 space-y-6">
          {GROUPS.map((group) => {
            const Icon = group.icon
            const items = (settings ?? []).filter((s) => META[s.key]?.group === group.key)
            if (items.length === 0) return null
            return (
              <Section
                key={group.key}
                title={
                  <span className="flex items-center gap-2">
                    <Icon className="size-4 text-muted-foreground" />
                    {group.title}
                  </span>
                }
                description={group.description}
              >
                <Card className="divide-y divide-border">
                  {items.map((item) => (
                    <SettingRow key={item.key} item={item} />
                  ))}
                </Card>
              </Section>
            )
          })}

          <Card className="flex items-start gap-2.5 bg-muted/30 p-3.5">
            <SlidersHorizontalIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
            <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
              <b className="text-foreground">Kill switches live elsewhere.</b> Runtime flags share
              this table but have their own audited route, so they cannot be changed from here —
              routing them through this page would bypass the{" "}
              <code className="font-mono">kill_switch_toggled</code> audit action. They are on the
              Governance page.
            </p>
          </Card>
        </div>
      )}
    </Page>
  )
}

function SettingRow({ item }: { item: SettingItem }) {
  const meta = META[item.key]
  const queryClient = useQueryClient()
  const [draft, setDraft] = React.useState<unknown>(item.value)
  const [confirming, setConfirming] = React.useState(false)
  const [reason, setReason] = React.useState("")

  React.useEffect(() => setDraft(item.value), [item.value])

  const save = useMutation({
    mutationFn: () => api.updateSetting(item.key, draft, reason),
    onSuccess: () => {
      toast.success(`${meta?.label ?? item.key} updated`, {
        description: "Persisted and audited with the value before and after.",
      })
      void queryClient.invalidateQueries({ queryKey: ["settings"] })
      setConfirming(false)
      setReason("")
    },
    onError: (error) =>
      toast.error("Could not save", {
        description: error instanceof Error ? error.message : undefined,
      }),
  })

  const dirty = JSON.stringify(draft) !== JSON.stringify(item.value)

  if (!meta) return null

  const numeric = meta.kind === "number"
  const value = numeric ? Number(draft) : draft
  const warn = numeric && meta.warnAbove != null && Number(value) > meta.warnAbove

  return (
    <>
      <div className="flex flex-wrap items-start gap-4 p-3.5">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <Label className="text-[0.8125rem]">{meta.label}</Label>
            <code className="text-[0.625rem] text-muted-foreground">{item.key}</code>
            {item.is_override && (
              <Hint label="This value differs from the built-in default — someone changed it deliberately.">
                <Badge variant="technical">override</Badge>
              </Hint>
            )}
          </div>
          <p className="max-w-2xl text-pretty text-[0.6875rem] leading-relaxed text-muted-foreground">
            {meta.note}
          </p>
          {warn && (
            <p className="flex items-start gap-1.5 text-[0.6875rem] leading-relaxed text-warn">
              <TriangleAlertIcon className="mt-px size-3 shrink-0" />
              {meta.warnNote}
            </p>
          )}
        </div>

        <div className="flex w-full shrink-0 items-center gap-2 sm:w-64">
          {numeric ? (
            <>
              <Slider
                value={[Number(value)]}
                min={meta.min}
                max={meta.max}
                step={meta.step}
                onValueChange={([v]) => setDraft(v)}
                className="flex-1"
              />
              <Input
                type="number"
                value={Number(value)}
                min={meta.min}
                max={meta.max}
                step={meta.step}
                onChange={(e) => setDraft(Number(e.target.value))}
                className="h-8 w-20 font-mono text-[0.8125rem]"
              />
            </>
          ) : (
            <div className="flex-1 space-y-1.5">
              <Input
                value={(draft as { provider?: string })?.provider ?? ""}
                onChange={(e) =>
                  setDraft({ ...(draft as object), provider: e.target.value })
                }
                placeholder="provider"
                className="h-8 font-mono text-[0.8125rem]"
              />
              <Input
                value={(draft as { model?: string })?.model ?? ""}
                onChange={(e) => setDraft({ ...(draft as object), model: e.target.value })}
                placeholder="model"
                className="h-8 font-mono text-[0.8125rem]"
              />
            </div>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-1">
          <Button
            variant="ghost"
            size="icon-sm"
            disabled={!dirty}
            onClick={() => setDraft(item.value)}
            aria-label="Revert"
          >
            <RotateCcwIcon />
          </Button>
          <Button size="sm" disabled={!dirty} onClick={() => setConfirming(true)}>
            <SaveIcon />
            Save
          </Button>
        </div>
      </div>

      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Change {meta.label}?</DialogTitle>
            <DialogDescription>
              {item.key === "embedding_model"
                ? "Changing the retrieval embedding model invalidates every stored vector. Search will return nothing useful until the corpus is reprocessed."
                : "The change takes effect on the next request. Nothing already indexed is altered."}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-md border border-border p-2.5">
                <p className="eyebrow mb-1">From</p>
                <code className="text-[0.75rem]">{JSON.stringify(item.value)}</code>
              </div>
              <div className="rounded-md border border-primary/40 bg-primary-soft/40 p-2.5">
                <p className="eyebrow mb-1">To</p>
                <code className="text-[0.75rem]">{JSON.stringify(draft)}</code>
              </div>
            </div>

            <Separator />

            <div className="space-y-1.5">
              <Label htmlFor={`reason-${item.key}`}>Reason</Label>
              <Textarea
                id={`reason-${item.key}`}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={2}
                placeholder="Raised after the recall regression on scanned drawings."
              />
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => save.mutate()}
              disabled={save.isPending}
              className={cn(item.key === "embedding_model" && "bg-destructive text-destructive-foreground hover:bg-destructive/90")}
            >
              Save change
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
