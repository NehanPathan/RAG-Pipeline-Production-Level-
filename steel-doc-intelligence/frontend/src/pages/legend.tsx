import { Page } from "@/components/layout/page"
import { PageHeader, Section } from "@/components/domain/layout"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import {
  ConfidenceMeter,
  ContentKindBadge,
  DocumentStatusBadge,
  DrawingNumber,
  EntityChip,
  JobStatusBadge,
  PrecisionBadge,
  RevisionChip,
  RoleBadge,
  SensitivityBadge,
  SensitivityBar,
} from "@/components/domain/badges"

/**
 * The key to the drawing.
 *
 * Every symbol on these screens means exactly one thing, and this page is
 * where that is written down. Worth its own route rather than a pile of
 * tooltips: a new engineer reads it once, and a reviewer can point at it.
 */
export default function LegendPage() {
  return (
    <Page width="reading">
      <PageHeader
        eyebrow="Reference"
        title="Legend and conventions"
        description="What every badge, colour and score on these screens means — and, where it matters, what it deliberately does not mean."
      />

      <div className="mt-6 space-y-8">
        <Section
          title="Classification"
          description="Four levels, least to most restrictive. This ramp is used for nothing else, so a colour from it always means classification."
        >
          <Card className="divide-y divide-border">
            {(
              [
                ["public", "Readable by anyone with an account, including site and client logins."],
                ["internal", "Analysts and above. The default for an upload with no classification given."],
                ["confidential", "Stewards and admins. Client-commercial drawings sit here."],
                ["restricted", "Admin clearance only. Never enters retrieval for a lower clearance — not redacted afterwards, never fetched."],
              ] as const
            ).map(([level, note]) => (
              <div key={level} className="flex items-start gap-3 p-3">
                <span className="flex w-32 shrink-0 items-center gap-2">
                  <SensitivityBar value={level} />
                  <SensitivityBadge value={level} />
                </span>
                <p className="text-[0.8125rem] leading-relaxed text-muted-foreground">{note}</p>
              </div>
            ))}
          </Card>
          <p className="text-[0.75rem] leading-relaxed text-muted-foreground">
            A document above your clearance returns <b>404, not 403</b>. The API will not confirm
            that a document you cannot read exists — that confirmation is itself a disclosure. So
            "not found" on this system genuinely means "not found <i>or</i> not yours".
          </p>
        </Section>

        <Section
          title="Provenance"
          description="Where a number on the screen actually came from. This is the distinction that decides whether a dimension can be trusted as exact."
        >
          <Card className="divide-y divide-border">
            {(
              [
                ["cad_native", "Read from a DXF or DWG. The dimension is the CAD value. Layers, blocks and piece marks are available, and the measurement is exact."],
                ["vector_drawing", "Plotted from CAD to PDF. The text is real text with real coordinates, but a dimension is the string the drafter's system rendered — not a queried CAD value."],
                ["scanned_drawing", "A photograph or scan of a drawing. Every character came from OCR. Treat every dimension as a reading, and verify against the source sheet."],
                ["scanned_prose", "Pixels, but laid out as prose. Should OCR cleanly, so a low confidence here is a real problem rather than the nature of the input."],
                ["mixed", "Drawing and prose both substantially present on one page — a detail above, erection notes below. Chunks are classified individually from their own text."],
                ["prose", "A specification, calculation, schedule or letter. Sentences and tables, not a sheet."],
              ] as const
            ).map(([kind, note]) => (
              <div key={kind} className="flex items-start gap-3 p-3">
                <span className="w-36 shrink-0">
                  <ContentKindBadge kind={kind} />
                </span>
                <p className="text-[0.8125rem] leading-relaxed text-muted-foreground">{note}</p>
              </div>
            ))}
          </Card>
          <p className="text-[0.75rem] leading-relaxed text-muted-foreground">
            Only <b>CAD native</b> carries exact dimensions, and the interface says so explicitly
            wherever a measurement is shown. Classification is decided per page, never per
            document: one PDF is routinely a specification, a schedule, a plotted sheet and a scan,
            and an average over those four is meaningless.
          </p>
        </Section>

        <Section
          title="Revisions"
          description="A drawing is an identity that outlives its revisions."
        >
          <Card className="space-y-3 p-4">
            <div className="flex flex-wrap items-center gap-3">
              <DrawingNumber value="CG4-S-104" sheet="01" />
              <RevisionChip label="D" isLatest />
              <RevisionChip label="C" isLatest={false} />
              <RevisionChip label="B" isLatest={false} />
            </div>
            <p className="text-[0.8125rem] leading-relaxed text-muted-foreground">
              <b className="text-foreground">CG4-S-104</b> is one drawing. Rev B, C and D are three
              documents of it. Exactly one is current — enforced by a partial unique index, not by
              convention — and superseded revisions are struck through everywhere they appear.
            </p>
            <p className="text-[0.8125rem] leading-relaxed text-muted-foreground">
              Search and answers exclude superseded revisions by default. Answering from an old
              sheet is worse than not answering. Old issues remain searchable behind an explicit
              toggle, because as-built queries and claims genuinely need them.
            </p>
            <p className="text-[0.8125rem] leading-relaxed text-muted-foreground">
              Revision labels are not consistent between practices — alphabetic (A, B, C), numeric
              (0, 1, 2) and staged (P1 preliminary, C1 construction) all occur, sometimes in one
              project. Ordering uses an integer derived once at registration, so a construction
              issue supersedes a preliminary one regardless of its number.
            </p>
          </Card>
        </Section>

        <Section title="Status" description="Where a document or job has got to.">
          <div className="grid gap-4 sm:grid-cols-2">
            <Card className="space-y-2.5 p-3.5">
              <p className="eyebrow">Document</p>
              {(["indexed", "processing", "pending", "failed"] as const).map((status) => (
                <div key={status} className="flex items-center gap-3">
                  <span className="w-28 shrink-0">
                    <DocumentStatusBadge status={status} />
                  </span>
                  <span className="text-[0.75rem] text-muted-foreground">
                    {
                      {
                        indexed: "Searchable and answerable.",
                        processing: "Moving through the pipeline.",
                        pending: "Accepted, waiting for a worker.",
                        failed: "Ingestion exhausted its retries.",
                      }[status]
                    }
                  </span>
                </div>
              ))}
            </Card>
            <Card className="space-y-2.5 p-3.5">
              <p className="eyebrow">Job</p>
              {(["running", "queued", "succeeded", "failed", "skipped"] as const).map((status) => (
                <div key={status} className="flex items-center gap-3">
                  <span className="w-28 shrink-0">
                    <JobStatusBadge status={status} />
                  </span>
                  <span className="text-[0.75rem] text-muted-foreground">
                    {
                      {
                        running: "In flight on a worker.",
                        queued: "Waiting for a slot.",
                        succeeded: "Completed and indexed.",
                        failed: "Needs a decision.",
                        skipped: "An identical job was already running — not an error.",
                      }[status]
                    }
                  </span>
                </div>
              ))}
            </Card>
          </div>
        </Section>

        <Section
          title="Steel entities"
          description="Recognised designations, coloured by what kind of thing they are. Canonicalised, so ISMB300, ISMB 300 and I.S.M.B.-300 are one value and retrieve the same chunks."
        >
          <Card className="space-y-3 p-4">
            <div className="flex flex-wrap gap-1.5">
              <EntityChip canonical="ISMB 400" type="section" confidence={0.95} source="gazetteer" />
              <EntityChip canonical="IS 2062 E350 C" type="grade" confidence={0.95} source="gazetteer" />
              <EntityChip canonical="M24 8.8 HSFG" type="bolt" confidence={0.85} source="pattern" />
              <EntityChip canonical="10 mm fillet" type="weld" confidence={0.85} source="pattern" />
              <EntityChip canonical="BM-14" type="mark" confidence={0.6} source="context" />
              <EntityChip canonical="342 kNm" type="load" confidence={0.6} source="context" />
            </div>
            <p className="text-[0.8125rem] leading-relaxed text-muted-foreground">
              Confidence is <span className="font-mono">0.95</span> for a gazetteer-validated
              designation, <span className="font-mono">0.85</span> for a pattern match, and{" "}
              <span className="font-mono">0.6</span> for one that needed surrounding context to be
              certain. A trailing <span className="font-mono">~</span> marks anything below 0.80.
            </p>
            <p className="text-[0.8125rem] leading-relaxed text-muted-foreground">
              An LLM extractor fills the gaps regex is bad at — member descriptions in prose,
              connection descriptions, design assumptions — but its confidence is capped at 0.75 so
              regex always wins a tie, and a designation it produces that is neither in the
              gazetteer nor present in the document text is dropped and logged.
            </p>
          </Card>
        </Section>

        <Section
          title="Highlights and confidence"
          description="How the viewer marks the region a passage came from, and how honest it is about it."
        >
          <Card className="space-y-3 p-4">
            <div className="flex flex-wrap items-center gap-4">
              <span className="flex items-center gap-2">
                <span className="size-4 rounded-[2px] border-2 border-highlight/70 bg-highlight/20" />
                <PrecisionBadge precision="block" />
              </span>
              <span className="flex items-center gap-2">
                <span className="size-4 rounded-sm border border-dashed border-highlight/70 bg-highlight/20" />
                <PrecisionBadge precision="section" />
              </span>
              <span className="flex items-center gap-2">
                <span className="size-4 rounded-sm border border-dotted border-highlight/70 bg-highlight/20" />
                <PrecisionBadge precision="page" />
              </span>
            </div>
            <p className="text-[0.8125rem] leading-relaxed text-muted-foreground">
              A tight solid box means the chunk's own text blocks carry coordinates. A dashed box
              means the coordinates were inherited from the parent section, so the region is
              approximate — the semantic chunker has no sentence-to-box map, and drawing a
              confident rectangle around the wrong sentence would be worse than admitting the
              imprecision. A dotted box means only the page is known.
            </p>
            <div className="flex flex-wrap items-center gap-6 border-t border-border pt-3">
              <span className="flex items-center gap-2 text-[0.75rem]">
                <ConfidenceMeter value={0.62} floor={0.35} label="OCR" />
                <span className="text-muted-foreground">above the prose floor</span>
              </span>
              <span className="flex items-center gap-2 text-[0.75rem]">
                <ConfidenceMeter value={0.22} floor={0.35} label="OCR" />
                <span className="text-muted-foreground">below it — rejected</span>
              </span>
            </div>
            <p className="text-[0.8125rem] leading-relaxed text-muted-foreground">
              The tick on the bar is the floor that applies to <i>this</i> content. Drawings are
              held to 0.15 and prose to 0.35, decided per chunk rather than per document — a single
              PDF routinely contains both, and one floor for the whole file either waves prose
              through or throws every drawing chunk away.
            </p>
          </Card>
        </Section>

        <Section title="Roles" description="What a person may do, before anything about what they may read.">
          <Card className="divide-y divide-border">
            {(
              [
                ["viewer", "Read and ask. Public clearance."],
                ["analyst", "Upload, ask, run evaluations. Internal clearance."],
                ["steward", "Reclassify, delete, reprocess, promote feedback, read the audit trail. Confidential clearance."],
                ["admin", "Kill switches, settings, retention purges. Restricted clearance."],
              ] as const
            ).map(([role, note]) => (
              <div key={role} className="flex items-center gap-3 p-3">
                <span className="w-24 shrink-0">
                  <RoleBadge role={role} />
                </span>
                <span className="text-[0.8125rem] text-muted-foreground">{note}</span>
              </div>
            ))}
          </Card>
          <p className="text-[0.75rem] leading-relaxed text-muted-foreground">
            Role checks are allow-lists, not rank comparisons — a steward is not implicitly an
            admin. Roles are read from the database on every request and never from anything the
            browser sends.
          </p>
        </Section>

        <Section title="Answer metadata" description="The chips under an answer.">
          <Card className="space-y-2.5 p-4">
            {(
              [
                [<Badge key="1" variant="outline" className="font-mono">rag</Badge>, "The answer was built from retrieved documents. Other routes exist and are named."],
                [<Badge key="2" variant="technical">cached</Badge>, "Served from the semantic answer cache — no model call was made. A numeric-literal guard stops “within 30 days” matching a cached “within 14 days”."],
                [<Badge key="3" variant="warn">ungrounded</Badge>, "No document backed this. Treat it as model knowledge, not as a fact from your corpus."],
              ] as const
            ).map(([chip, note], i) => (
              <div key={i} className="flex items-start gap-3">
                <span className="w-24 shrink-0">{chip}</span>
                <span className="text-[0.8125rem] leading-relaxed text-muted-foreground">{note}</span>
              </div>
            ))}
            <p className="border-t border-border pt-3 text-[0.8125rem] leading-relaxed text-muted-foreground">
              A <b>refusal</b> is a result, not a failure. When nothing clears the grounding
              threshold the pipeline declines rather than guesses — for a question about a
              structural dimension, that is the correct behaviour.
            </p>
          </Card>
        </Section>
      </div>
    </Page>
  )
}
