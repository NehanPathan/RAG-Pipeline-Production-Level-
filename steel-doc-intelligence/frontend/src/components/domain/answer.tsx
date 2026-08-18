/**
 * Answer rendering and citations.
 *
 * A deliberately small Markdown subset — headings, bold/italic, code, lists,
 * tables, blockquotes — plus the one thing this product actually needs that
 * no generic renderer does: `[n]` citation markers turned into interactive
 * chips wired to the source list and the sheet viewer. Pulling in a full
 * Markdown pipeline to get that would mean a plugin anyway.
 */
import * as React from "react"
import { Link } from "react-router-dom"
import {
  ArrowUpRightIcon,
  FileTextIcon,
  QuoteIcon,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { Hint } from "@/components/ui/tooltip"
import {
  ContentKindBadge,
  DrawingNumber,
  RevisionChip,
} from "@/components/domain/badges"
import type { Citation } from "@/api/types"

/* --------------------------------------------------------- inline markup */

function renderInline(
  text: string,
  onCite?: (index: number) => void,
  activeCite?: number | null,
  citations?: Citation[],
): React.ReactNode[] {
  const nodes: React.ReactNode[] = []
  // Order matters: citations first so a `[1]` inside emphasis still resolves.
  const pattern = /(\[\d+\])|(\*\*[^*]+\*\*)|(`[^`]+`)|(\*[^*]+\*)/g
  let last = 0
  let match: RegExpExecArray | null
  let key = 0

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index))
    const token = match[0]

    if (token.startsWith("[")) {
      const index = Number(token.slice(1, -1))
      const citation = citations?.find((c) => c.index === index)
      nodes.push(
        <CitationMarker
          key={`c${key++}`}
          index={index}
          citation={citation}
          active={activeCite === index}
          onClick={onCite ? () => onCite(index) : undefined}
        />,
      )
    } else if (token.startsWith("**")) {
      nodes.push(
        <strong key={`b${key++}`} className="font-semibold text-foreground">
          {token.slice(2, -2)}
        </strong>,
      )
    } else if (token.startsWith("`")) {
      nodes.push(
        <code
          key={`k${key++}`}
          className="rounded-[3px] border border-border bg-muted px-1 py-px font-mono text-[0.8125em]"
        >
          {token.slice(1, -1)}
        </code>,
      )
    } else {
      nodes.push(
        <em key={`i${key++}`} className="italic">
          {token.slice(1, -1)}
        </em>,
      )
    }
    last = match.index + token.length
  }
  if (last < text.length) nodes.push(text.slice(last))
  return nodes
}

/** The `[n]` chip. Superscript, mono, and clearly a control. */
export function CitationMarker({
  index,
  citation,
  active,
  onClick,
}: {
  index: number
  citation?: Citation
  active?: boolean
  onClick?: () => void
}) {
  const chip = (
    <button
      type="button"
      onClick={onClick}
      disabled={!onClick}
      className={cn(
        "mx-px inline-flex h-4 min-w-4 translate-y-[-1px] items-center justify-center rounded-[3px] border px-1 align-middle font-mono text-[0.625rem] font-semibold leading-none transition-colors",
        active
          ? "border-primary bg-primary text-primary-foreground"
          : "border-primary/40 bg-primary-soft text-primary hover:border-primary hover:bg-primary/15",
        !onClick && "cursor-default",
      )}
    >
      {index}
    </button>
  )
  if (!citation) return chip
  return (
    <Hint
      side="top"
      label={
        <span className="block space-y-1">
          <span className="block font-medium">{citation.document_name}</span>
          <span className="block text-muted-foreground">
            {citation.drawing_number ? `${citation.drawing_number} ` : ""}
            {citation.revision_label ? `Rev ${citation.revision_label} · ` : ""}
            {citation.page_number ? `page ${citation.page_number}` : ""}
            {citation.section ? ` · ${citation.section}` : ""}
          </span>
        </span>
      }
    >
      {chip}
    </Hint>
  )
}

/* -------------------------------------------------------------- blocks */

export function AnswerBody({
  content,
  citations,
  onCite,
  activeCite,
  streaming = false,
  className,
}: {
  content: string
  citations?: Citation[]
  onCite?: (index: number) => void
  activeCite?: number | null
  streaming?: boolean
  className?: string
}) {
  const blocks = React.useMemo(() => content.split(/\n{2,}/), [content])

  return (
    <div className={cn("space-y-3 text-[0.9375rem] leading-relaxed", className)}>
      {blocks.map((block, i) => {
        const isLast = i === blocks.length - 1
        const caret = streaming && isLast

        // Table
        if (/^\s*\|.*\|\s*$/m.test(block) && block.includes("---")) {
          const rows = block.trim().split("\n").map((r) => r.trim())
          const header = rows[0].split("|").slice(1, -1).map((c) => c.trim())
          const body = rows.slice(2).map((r) => r.split("|").slice(1, -1).map((c) => c.trim()))
          return (
            <div key={i} className="overflow-x-auto rounded-md border border-border">
              <table className="w-full border-collapse text-[0.8125rem]">
                <thead>
                  <tr className="border-b border-border bg-muted/50">
                    {header.map((cell, c) => (
                      <th
                        key={c}
                        className="px-2.5 py-1.5 text-left text-[0.6875rem] font-semibold uppercase tracking-[0.06em] text-muted-foreground"
                      >
                        {cell}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {body.map((row, r) => (
                    <tr key={r} className="border-b border-border last:border-0">
                      {row.map((cell, c) => (
                        <td key={c} className="px-2.5 py-1.5 align-top">
                          {renderInline(cell, onCite, activeCite, citations)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }

        // Fenced code
        if (block.startsWith("```")) {
          const body = block.replace(/^```\w*\n?/, "").replace(/```$/, "")
          return (
            <pre
              key={i}
              className="overflow-x-auto rounded-md border border-border bg-muted/60 p-3 font-mono text-xs leading-relaxed"
            >
              {body}
            </pre>
          )
        }

        // Heading
        const heading = block.match(/^(#{1,4})\s+(.*)$/)
        if (heading) {
          const level = heading[1].length
          const size = ["text-base", "text-[0.9375rem]", "text-sm", "text-sm"][level - 1]
          return (
            <p key={i} className={cn("pt-1 font-semibold tracking-tight", size)}>
              {renderInline(heading[2], onCite, activeCite, citations)}
            </p>
          )
        }

        // Blockquote
        if (block.startsWith("> ")) {
          return (
            <blockquote
              key={i}
              className="border-l-2 border-primary/50 bg-muted/40 py-1.5 pl-3 text-sm italic text-muted-foreground"
            >
              {renderInline(block.replace(/^> ?/gm, ""), onCite, activeCite, citations)}
            </blockquote>
          )
        }

        // Lists
        if (/^\s*[-*]\s/.test(block)) {
          const items = block.split("\n").filter((l) => /^\s*[-*]\s/.test(l))
          return (
            <ul key={i} className="space-y-1 pl-1">
              {items.map((item, li) => (
                <li key={li} className="flex gap-2">
                  <span className="mt-[0.55em] size-1 shrink-0 rounded-full bg-primary/70" />
                  <span className="min-w-0 flex-1">
                    {renderInline(item.replace(/^\s*[-*]\s/, ""), onCite, activeCite, citations)}
                  </span>
                </li>
              ))}
            </ul>
          )
        }
        if (/^\s*\d+\.\s/.test(block)) {
          const items = block.split("\n").filter((l) => /^\s*\d+\.\s/.test(l))
          return (
            <ol key={i} className="space-y-1 pl-1">
              {items.map((item, li) => (
                <li key={li} className="flex gap-2">
                  <span className="mt-px w-4 shrink-0 text-right font-mono text-xs text-muted-foreground">
                    {li + 1}.
                  </span>
                  <span className="min-w-0 flex-1">
                    {renderInline(item.replace(/^\s*\d+\.\s/, ""), onCite, activeCite, citations)}
                  </span>
                </li>
              ))}
            </ol>
          )
        }

        return (
          <p key={i} className={cn("text-pretty", caret && "stream-caret")}>
            {renderInline(block, onCite, activeCite, citations)}
          </p>
        )
      })}
    </div>
  )
}

/* ---------------------------------------------------------- source card */

export function CitationCard({
  citation,
  active,
  onClick,
  compact = false,
}: {
  citation: Citation
  active?: boolean
  onClick?: () => void
  compact?: boolean
}) {
  return (
    <div
      className={cn(
        "group rounded-md border bg-card transition-colors",
        active ? "border-primary/60 bg-primary-soft/40" : "border-border hover:border-primary/35",
      )}
    >
      <button
        type="button"
        onClick={onClick}
        className="flex w-full items-start gap-2.5 p-2.5 text-left"
      >
        <span
          className={cn(
            "mt-px flex size-5 shrink-0 items-center justify-center rounded-[3px] border font-mono text-[0.6875rem] font-semibold",
            active
              ? "border-primary bg-primary text-primary-foreground"
              : "border-border bg-muted text-muted-foreground",
          )}
        >
          {citation.index}
        </span>
        <span className="min-w-0 flex-1 space-y-1">
          <span className="flex items-center gap-1.5">
            <FileTextIcon className="size-3 shrink-0 text-muted-foreground" />
            <span className="truncate text-[0.8125rem] font-medium">{citation.document_name}</span>
          </span>
          <span className="flex flex-wrap items-center gap-1.5 text-[0.6875rem] text-muted-foreground">
            {citation.drawing_number && <DrawingNumber value={citation.drawing_number} className="text-[0.6875rem]" />}
            {citation.revision_label && (
              <RevisionChip label={citation.revision_label} size="sm" />
            )}
            {citation.page_number != null && <span>p.{citation.page_number}</span>}
            {citation.section && <span className="truncate">· {citation.section}</span>}
            {citation.content_kind && <ContentKindBadge kind={citation.content_kind} compact />}
          </span>
          {!compact && citation.snippet && (
            <span className="line-clamp-2 whitespace-pre-line text-[0.6875rem] leading-relaxed text-muted-foreground">
              {citation.snippet}
            </span>
          )}
        </span>
      </button>
      {citation.document_id && (
        <div className="flex items-center justify-between border-t border-border px-2.5 py-1">
          {citation.score != null ? (
            <span className="font-mono text-[0.625rem] tabular-nums text-muted-foreground">
              score {citation.score.toFixed(3)}
            </span>
          ) : (
            <span />
          )}
          <Link
            to={`/documents/${citation.document_id}${citation.chunk_id ? `?chunk=${citation.chunk_id}` : ""}`}
            className="inline-flex items-center gap-0.5 text-[0.625rem] font-medium text-muted-foreground transition-colors hover:text-primary"
          >
            Open source
            <ArrowUpRightIcon className="size-3" />
          </Link>
        </div>
      )}
    </div>
  )
}

/** Shown when the pipeline refuses — a refusal is a result, not an error. */
/**
 * The keys are the values the API actually sends. `refusal_reason` carries the
 * **control id** that produced the refusal (`src/retrieval/pipeline.py` sets it
 * from `decision.control_id`), and this map previously keyed on invented names
 * — `no_grounding`, `clearance`, `policy` — that nothing ever emitted. So every
 * real refusal fell through to the default, which asserted the question had
 * been about "a structural dimension". Asked who was logged in, the user was
 * told an ungrounded answer about a dimension would be worse than none.
 *
 * These three are the whole set the API can send: `kill_switch` from the graph
 * and the two grounding controls from the policy. Clearance filtering is not
 * among them — it removes passages during retrieval, so it surfaces as
 * C-GOV-03 with nothing retrieved rather than as a reason of its own. Adding a
 * key for it would be inventing wire values again.
 *
 * C-GOV-03 is the common one and the only one where the reader can act, so it
 * says what would change the outcome instead of only justifying the refusal.
 */
export function RefusalNotice({ reason }: { reason?: string | null }) {
  const explanations: Record<string, string> = {
    kill_switch: "An administrator has paused answering. Your question was not sent to a model.",
    "C-GOV-03":
      "Nothing in the indexed documents matched, so answering would have meant guessing. " +
      "If the question was about a drawing, naming the sheet or the piece mark usually finds it; " +
      "if it was about this workspace rather than its documents, the corpus will not hold it.",
    "C-GOV-04":
      "Passages were retrieved, but the answer cited none of them — so it was not actually " +
      "grounded in them and was withheld rather than shown.",
  }
  return (
    <div className="flex items-start gap-2.5 rounded-md border border-warn/35 bg-warn-soft/50 p-3 text-warn">
      <QuoteIcon className="mt-0.5 size-4 shrink-0" />
      <div className="space-y-1 text-sm">
        <p className="font-medium">No answer was given</p>
        <p className="text-xs leading-relaxed opacity-90">
          {(reason && explanations[reason]) ??
            "The pipeline declined to answer rather than answer without support. This is a deliberate outcome, not a failure."}
        </p>
      </div>
    </div>
  )
}
