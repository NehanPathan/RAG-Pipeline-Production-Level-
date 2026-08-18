/**
 * The document/drawing viewer.
 *
 * Highlights are drawn from `ChunkMetadata.regions`, which are normalized to
 * the page render (0..1, Y-down) precisely so one renderer serves PDFs, scans
 * and CAD alike. Precision is honoured rather than flattened: a `block`
 * region gets a tight box, a `section` region a soft wash, and a `page`
 * region a full-width band — because the semantic chunker has no
 * sentence→bbox map and drawing a confident rectangle around the wrong
 * sentence is worse than admitting the imprecision.
 */
import * as React from "react"
import {
  DownloadIcon,
  ExpandIcon,
  HighlighterIcon,
  MaximizeIcon,
  MinusIcon,
  PlusIcon,
  RotateCcwIcon,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Hint } from "@/components/ui/tooltip"
import { Switch, Label } from "@/components/ui/misc"
import { SheetCanvas } from "@/components/domain/sheet-canvas"
import { ContentKindBadge } from "@/components/domain/badges"
import type { ContentKind, DocumentSummary, Region } from "@/api/types"

export interface Highlight {
  id: string
  regions: Region[]
  precision?: "block" | "section" | "page" | null
  label?: string
  /** Citation index, drawn in the corner tag like a callout bubble. */
  index?: number
}

export function SheetViewer({
  document,
  highlights = [],
  activeHighlightId,
  onHighlightClick,
  page,
  onPageChange,
  downloadUrl,
  className,
  toolbarExtra,
}: {
  document: DocumentSummary
  highlights?: Highlight[]
  activeHighlightId?: string | null
  onHighlightClick?: (id: string) => void
  page?: number
  onPageChange?: (page: number) => void
  downloadUrl?: string
  className?: string
  toolbarExtra?: React.ReactNode
}) {
  const totalPages = Math.max(1, document.page_count ?? 1)
  const [internalPage, setInternalPage] = React.useState(1)
  const currentPage = page ?? internalPage
  const setPage = onPageChange ?? setInternalPage

  const [zoom, setZoom] = React.useState(1)
  const [showHighlights, setShowHighlights] = React.useState(true)
  const [fullscreen, setFullscreen] = React.useState(false)
  const scrollRef = React.useRef<HTMLDivElement>(null)

  const pageHighlights = React.useMemo(
    () =>
      highlights
        .map((h) => ({
          ...h,
          regions: h.regions.filter((r) => r.page_number === currentPage),
        }))
        .filter((h) => h.regions.length > 0),
    [highlights, currentPage],
  )

  // Jump to the page holding the active citation, so clicking a citation in
  // the answer lands on the right sheet rather than the right document.
  React.useEffect(() => {
    if (!activeHighlightId) return
    const target = highlights.find((h) => h.id === activeHighlightId)
    const first = target?.regions[0]
    if (first && first.page_number !== currentPage) setPage(first.page_number)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeHighlightId])

  const kind: ContentKind = document.content_kind ?? (document.drawing_number ? "vector_drawing" : "prose")

  const viewer = (
    <div
      className={cn(
        "flex min-h-0 flex-col overflow-hidden rounded-lg border border-border bg-card",
        fullscreen && "fixed inset-3 z-50 shadow-2xl shadow-black/40",
        className,
      )}
    >
      {/* toolbar */}
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-2.5 py-1.5">
        <div className="flex items-center gap-0.5">
          <Hint label="Zoom out">
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => setZoom((z) => Math.max(0.5, Number((z - 0.25).toFixed(2))))}
              disabled={zoom <= 0.5}
            >
              <MinusIcon />
            </Button>
          </Hint>
          <span className="w-11 text-center font-mono text-[0.6875rem] tabular-nums text-muted-foreground">
            {Math.round(zoom * 100)}%
          </span>
          <Hint label="Zoom in">
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => setZoom((z) => Math.min(4, Number((z + 0.25).toFixed(2))))}
              disabled={zoom >= 4}
            >
              <PlusIcon />
            </Button>
          </Hint>
          <Hint label="Fit to width">
            <Button variant="ghost" size="icon-xs" onClick={() => setZoom(1)}>
              <ExpandIcon />
            </Button>
          </Hint>
        </div>

        <span className="h-4 w-px bg-border" />

        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="xs"
            onClick={() => setPage(Math.max(1, currentPage - 1))}
            disabled={currentPage <= 1}
          >
            Prev
          </Button>
          <span className="font-mono text-[0.6875rem] tabular-nums text-muted-foreground">
            {currentPage} / {totalPages}
          </span>
          <Button
            variant="ghost"
            size="xs"
            onClick={() => setPage(Math.min(totalPages, currentPage + 1))}
            disabled={currentPage >= totalPages}
          >
            Next
          </Button>
        </div>

        <span className="h-4 w-px bg-border" />
        <ContentKindBadge kind={kind} compact />

        <div className="ml-auto flex items-center gap-2">
          {toolbarExtra}
          {highlights.length > 0 && (
            <Label className="cursor-pointer gap-1.5 text-[0.6875rem] text-muted-foreground">
              <HighlighterIcon className="size-3.5" />
              <Switch checked={showHighlights} onCheckedChange={setShowHighlights} />
              <span className="tabular-nums">{highlights.length}</span>
            </Label>
          )}
          {downloadUrl && (
            <Hint label="Download the stored original. Streamed through the API so every download is an authorised one.">
              <Button asChild variant="ghost" size="icon-xs">
                <a href={downloadUrl} download>
                  <DownloadIcon />
                </a>
              </Button>
            </Hint>
          )}
          <Hint label={fullscreen ? "Exit full screen" : "Full screen"}>
            <Button variant="ghost" size="icon-xs" onClick={() => setFullscreen((f) => !f)}>
              {fullscreen ? <RotateCcwIcon /> : <MaximizeIcon />}
            </Button>
          </Hint>
        </div>
      </div>

      {/* canvas */}
      <div
        ref={scrollRef}
        className="bg-grid-fine relative min-h-0 flex-1 overflow-auto p-4"
        style={{ backgroundColor: "color-mix(in oklab, var(--muted) 55%, transparent)" }}
      >
        <div
          className="relative mx-auto shadow-lg shadow-black/15 ring-1 ring-black/10 transition-[width] duration-150"
          style={{ width: `${Math.min(100 * zoom, 400)}%`, maxWidth: zoom <= 1 ? "1100px" : "none" }}
        >
          <SheetCanvas document={document} page={currentPage} kind={kind} />

          {/* region overlay, in the same normalized space as the canvas */}
          {showHighlights && (
            <div className="pointer-events-none absolute inset-0">
              {pageHighlights.map((highlight) =>
                highlight.regions.map((region, i) => {
                  const active = highlight.id === activeHighlightId
                  const precision = highlight.precision ?? "block"
                  const left = `${region.x0 * 100}%`
                  const top = `${region.y0 * 100}%`
                  const width = `${(region.x1 - region.x0) * 100}%`
                  const height = `${(region.y1 - region.y0) * 100}%`
                  return (
                    <button
                      key={`${highlight.id}-${i}`}
                      type="button"
                      onClick={() => onHighlightClick?.(highlight.id)}
                      aria-label={highlight.label ?? `Highlighted region ${highlight.index ?? ""}`}
                      className={cn(
                        "group pointer-events-auto absolute cursor-pointer transition-all duration-150",
                        precision === "block" && "rounded-[2px] border-2",
                        precision === "section" && "rounded-sm border border-dashed",
                        precision === "page" && "rounded-sm border border-dotted",
                        active
                          ? "z-10 border-primary bg-primary/25 shadow-[0_0_0_3px_color-mix(in_oklab,var(--primary)_25%,transparent)]"
                          : "border-highlight/70 bg-highlight/20 hover:bg-highlight/35",
                      )}
                      style={{
                        left,
                        top,
                        width,
                        height,
                        // A section-precision box is deliberately softer: the
                        // coordinates came from the parent, not this text.
                        ...(precision !== "block" && !active ? { opacity: 0.72 } : {}),
                      }}
                    >
                      {highlight.index != null && (
                        <span
                          className={cn(
                            "absolute -left-px -top-px flex size-4 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border font-mono text-[0.5625rem] font-semibold leading-none",
                            active
                              ? "border-primary bg-primary text-primary-foreground"
                              : "border-highlight bg-card text-foreground",
                          )}
                        >
                          {highlight.index}
                        </span>
                      )}
                    </button>
                  )
                }),
              )}
            </div>
          )}
        </div>

        {/* precision legend, only when a soft highlight is actually on screen */}
        {showHighlights && pageHighlights.some((h) => h.precision && h.precision !== "block") && (
          <div className="pointer-events-none sticky bottom-0 left-0 mt-3 flex w-fit items-center gap-3 rounded-md border border-border bg-card/95 px-2.5 py-1.5 text-[0.625rem] text-muted-foreground backdrop-blur">
            <span className="flex items-center gap-1.5">
              <span className="size-2.5 rounded-[2px] border-2 border-highlight/70 bg-highlight/20" />
              block — exact
            </span>
            <span className="flex items-center gap-1.5">
              <span className="size-2.5 rounded-sm border border-dashed border-highlight/70 bg-highlight/20" />
              section — approximate
            </span>
          </div>
        )}
      </div>
    </div>
  )

  return (
    <>
      {fullscreen && (
        <div
          className="fixed inset-0 z-40 bg-background/80 backdrop-blur-sm"
          onClick={() => setFullscreen(false)}
        />
      )}
      {viewer}
    </>
  )
}
