# 15. CAD understanding — what we do not yet read, and what to do about it

Written after fixing the block-traversal bug (commit `65b1b89`). That fix
recovered 78% of the *text* in a real drawing. This document is about
everything in a drawing that is **not** text, which is most of it.

Measured against the reference file `SSD09.0-02.dxf`, a real steel
beam-to-column connection detail:

| what the file contains | what we extract today |
|---|---|
| 133 TEXT entities (45 non-empty) | 35, with their layer ✅ |
| 171 LINE, 36 POLYLINE, 24 ARC, 2 CIRCLE | a count per layer, nothing more |
| 52 INSERT across 34 blocks | a "block schedule" of `*U10, *U11, *U12…` |
| 19 leader lines on `S-LEADER` | nothing |
| 55 entities on `S-BOLTS` | "55 entities" |
| 0 native DIMENSION entities | correctly nothing |

So: we read the drawing's *words* and ignore its *drawing*. A human looking
at this sheet learns what connects to what from the geometry; we learn only
what somebody happened to type.

---

## What the field does that we do not

Three approaches recur in the literature and in shipped products.

**Vector-native symbol spotting.** GAT-CADNet (CVPR 2022) and CADSpotting
(2024) treat a CAD drawing as a *graph of primitives* — segments, arcs,
circles with their real coordinates — and learn to group them into symbol
instances. Both papers make the same argument for working on vectors rather
than rendering to pixels: rasterising "introduces discrepancies between the
original vector graphics and the rasterized images, resulting in errors due
to the loss of precise geometric information". We are in the fortunate
position of *having* the vectors and discarding them anyway.

**Graph/knowledge representation, then retrieval over the graph.** ChatP&ID
converts a P&ID into a knowledge graph of symbols, connectivity and
attributes, and answers questions by traversing it rather than by embedding
similarity. Their stated limitation is instructive: everything downstream
depends on symbol detection being right, so connectivity errors propagate
into wrong answers. That is an argument for extracting conservatively and
citing, not for extracting more aggressively.

**Text-first, vision as fallback.** SteelFlo's steel takeoff pipeline runs
text extraction with coordinates first, applies steel-specific pattern
matching, and only escalates to a vision model when text extraction fails —
"CAD drawings using vector fonts or hand-lettered annotations". This is
exactly the shape we already have, and the shape we should keep. LlamaIndex's
survey adds the capability list buyers actually check: spatial reasoning
relating text to dimensions, symbol interpretation, title-block extraction
across supplier variation, table/schedule preservation, multi-page sets.

Commercial steel takeoff tools converge on one thing we cannot do at all:
they "extract section callouts, count occurrences, link each detection to its
source page, and produce a bill of materials". Note *count* and *link to
location*. Both need geometry.

---

## The gaps, in the order they hurt

### G1. Text is never associated with what it labels — **highest value**

Our drawing has an `S-LEADER` layer: 19 lines, 3 polylines, 1 arrowhead
block. Those leaders exist precisely to connect a callout to the part it
describes. We store `CadTextEntity.bbox` for every text and coordinates for
every line, and we never join them.

The consequence is that `BENT PLATE 5x5x 12 GA.` and `(2) L3 1/2 x 3 1/2 x
5/16` are free-floating strings. We can tell you the drawing *mentions* an
angle; we cannot tell you what it is an angle *of*, or where.

This is deterministic, cheap, and needs no model: a leader is a polyline
whose one end is near a text bbox and whose other end lands on or near
geometry. Nearest-endpoint matching with a tolerance derived from text height
solves most of it. The DXF `LEADER` entity type exists for exactly this and
carries its own association; this file draws leaders as plain lines, which is
common in R12 exports, so both paths are needed.

**Unlocks:** "what is the bent plate for?", "what does this callout point
at?", and a genuine part→annotation record instead of a bag of strings.

### G2. Anonymous blocks make the bill of materials meaningless

Our block schedule lists `*U10 ×1, *U11 ×1, *U12 ×1`. Those are anonymous R12
blocks; the names carry nothing. Worse, the table looks authoritative.

Two deterministic fixes, in order of value:

1. **Name a block by what it contains.** `*U8` holds the text `ALL BOLTS 3/4"
   DIA. A325`; it should appear as "bolt note", not `*U8`. A block's own text
   is the best label available and costs nothing.
2. **Signature-match repeated blocks.** Blocks with identical primitive
   signatures are the same symbol placed many times. Grouping by signature
   turns 34 anonymous blocks into "N distinct symbols, one appearing 19
   times" — which is the beginning of a count.

**Unlocks:** a BOM that means something, and the counting that steel takeoff
is actually about.

### G3. Geometry-only layers can be described but not counted

`S-BOLTS` is 26 lines, 16 polylines, 13 block references and zero text. Since
the layer-inventory work we can say the layer exists and how much is on it.
We cannot say *how many bolts*, because a bolt is a symbol drawn from
primitives.

The honest intermediate step is G2's signature grouping: 13 INSERTs on
`S-BOLTS` referencing the same block is strong evidence of 13 bolts, and can
be stated as "13 instances of one repeated symbol on the bolts layer" —
which is true, useful, and not a claim about bolt count that the file does
not support. Full symbol recognition (G7) is the real answer and is a much
larger piece of work.

**What we must not do:** infer "13 bolts" and present it as the bolt count.
The drawing itself says `SEE BOLT SCHEDULE FOR MINIMUM BOLT COUNT` — the
count is deliberately *not* on this sheet.

### G4. Dimensions drawn as geometry are invisible

The file has **zero** native `DIMENSION` entities. Its dimensions are drawn:
`S-DIMS` holds 9 lines and 7 polylines, and the values live as text on
`S-TEXT` — `3"` five times, `1 1/2"`, `1/2" MAX. (TYP.)`.

Today those strings are indistinguishable from any other text. Associated
with the `S-DIMS` geometry by proximity (G1's machinery), each becomes a
dimension with a location and an extent.

**Unlocks:** "what are the bolt spacings?" — currently unanswerable from a
sheet that states them five times.

### G5. Schedules and tables inside drawings are not detected

The drawing says `SEE BOLT SCHEDULE`. Schedules on drawings are drawn as a
grid of lines with text in the cells. We detect none of it — `_grid_to_table`
only handles `ACAD_TABLE` entities, which this file has none of.

Commercial takeoff tools treat the schedule as the *primary* source, because
it is where quantities and sizes are tabulated. Detecting a grid of
axis-aligned lines and binding text to cells is classical, deterministic
work.

**Unlocks:** the single richest structure on most steel sheets.

### G6. Citations are page-level, so nothing is verifiable on the sheet

We carry `bbox` on text and `BoundingBox.space` was designed for this, but a
CAD citation resolves to "page 1" — a whole drawing. Every commercial tool
links a detection to a location so an engineer can check it.

The pieces exist (`Region`, `region_precision` in the P1 design); drawings
simply never emit them.

**Unlocks:** click-to-highlight, and the ability to disprove a wrong answer.

### G7. No symbol recognition, and no vision fallback implemented

`VisionPageClassifier` is a defined port with no implementation — deliberate,
since the deterministic path handles classification. But for symbol counting
on geometry-only layers there is no deterministic answer, and this is where
the literature is unanimous that a model is required.

Two options, very different in cost:

- **Vision-LLM on a rendered crop** — render the sheet (we already have
  matplotlib and the CAD renderer was specced), crop by layer bbox, ask once
  per *region*, not per entity. Cheap enough, and matches the SteelFlo
  fallback pattern.
- **Trained vector symbol spotter** (GAT-CADNet / CADSpotting family) —
  accurate and fast at inference, but needs labelled drawings we do not have.
  Not a starting point.

### G8. Retrieval has no notion of drawing structure

The layer inventory is one chunk competing on embedding similarity with
everything else. ChatP&ID's argument is that diagram questions are
*structural* — "what connects to what", "what is on layer X" — and are
better served by traversing a representation than by similarity search.

We already denormalise `content_kind`, `drawing_number` and
`entity_canonicals` as filters. Layer is the obvious next facet, and a far
smaller step than a graph store.

---

## Plan

Sequenced so each phase is independently useful and the cheap deterministic
wins come first. Nothing here calls an LLM per entity, and nothing weakens
the grounding guard.

### Phase 1 — Spatial association (G1, G4) · **implemented**

The foundation everything else builds on. Delivered in
`src/ingestion/cad/spatial.py`; on the reference drawing all seven callouts
resolve and six drawn dimensions are recovered. Four things the plan did not
anticipate, all found by running it against the real file:

* **Arrowheads must be excluded from the chain.** `S-ARROW_HEAD` looks like
  leader geometry and is not. Chaining its 133 lines in fused the 9 leaders
  into 34 fragments, and since an arrowhead attaches at the tip, the tip
  stopped being a free end — every callout then appeared to point at its own
  label. Resolved count went 0 → 7 on excluding them.
* **Scaffolding layers cannot be targets.** Dimension lines are drawn in the
  clear space around a detail, so a leader tip often lands beside one.
  `PURLIN, SEE PLAN` resolved to a dimension line 4.75 away instead of the
  purlin 0.19 away.
* **Callouts are drafted as stacked single-line TEXT entities.** `ALL BOLTS
  3/4" DIA. A325,` / `SEE BOLT SCHEDULE FOR` / `MINIMUM BOLT COUNT` is three
  entities. Ungrouped, two thirds of the bolt spec is stranded as unresolved
  and the resolved third ends in a comma.
* **Block-local coordinates had to be transformed.** Text and geometry inside
  a block are stored in the block's own frame; without composing the INSERT
  transform down the traversal, distances between a leader in one block and
  geometry in another are meaningless. The reference drawing inserts at the
  origin unscaled, which is exactly why this would not have been noticed.

* `CadTextEntity` already carries `bbox`; add `CadGeometry` records for LINE,
  POLYLINE, ARC, CIRCLE with their real coordinates, capped and layer-tagged.
* `LeaderResolver`: for each text, find leaders (explicit `LEADER` entities
  first, then polylines on leader-ish layers) with an endpoint within
  `k × text_height`; follow the other end to the nearest geometry; record
  `annotates(text → target_layer, target_point)`.
* `DimensionResolver`: text matching a measurement pattern, near `S-DIM*`
  geometry, becomes a `DimensionRecord` with `source="drawn"` — explicitly
  distinct from a native DIMENSION's exact value.
* Chunks gain an "annotations" section: `BENT PLATE 5x5x 12 GA. → S-SECT_STEEL
  at (x, y)`.

**Verified:** all seven callouts resolve (confidence 0.69–1.00), the five `3"`
spacings and the `1 1/2"` edge distance bind to `S-DIMS` geometry, and the
disclaimer, scale note and detail title stay unresolved. Inspect any drawing
with `python scripts/inspect_cad_associations.py <file.dxf>`.

**Risk, as anticipated:** leader conventions vary and tolerance tuning per
drawing set is likely. Mitigated as planned — unresolved callouts are recorded
in those words rather than guessed at, and tolerances are expressed in
multiples of the sheet's own median text height rather than in drawing units,
so a sheet plotted at a different scale needs no retuning. Deriving them from
drawing extents would have failed on this file, which has geometry at x=1529
against a detail 100 wide.

**Known limitation:** a leader whose tip lands on a construction reference
resolves to that reference. `ALL BOLTS 3/4" DIA. A325 …` resolves to
`S-CENTERLINE` — the bolt gauge line — because no `S-BOLTS` geometry lies
within reach of the tip. The layer is reported, so the answer is checkable,
but it is one step removed from the physical part.

### Phase 2 — Block semantics (G2, G3) · **implemented**

Delivered in `src/ingestion/cad/blocks.py`. On the reference drawing 36 block
definitions collapse to 23 distinct shapes, every text-carrying block is named
by its own text, and `S-BOLTS` reports as *13 placements of 4 shapes* instead
of thirteen meaningless rows.

* Each block is named by the text it contains, falling back to its own name.
* Definitions are grouped by a **signature of primitive makeup, overall size
  and text content**.
* The block schedule is replaced by a symbol schedule: label, composition,
  size, placements, layers, and piece marks where the file has them.

Three corrections the plan's design would have got wrong:

* **Relative rounded geometry does not work as a signature.** It was tried
  first and fragmented badly — `*U25`, `*U27` and `*U29` are identical in
  shape yet landed in three buckets, because one vertex sat on a rounding
  boundary. Primitive makeup plus overall size is coarser, stable under
  floating-point noise, and states a claim a reader can check against the
  sheet.
* **Size has to be in the signature.** `*U4` and `*U35` are both four lines
  and two solids; one is an arrowhead 0.19 units wide, the other a leader 45
  units long. Primitive counts alone call them one symbol.
* **Text has to be in the signature.** Nine unrelated callouts are each one
  TEXT at the same size, so without it they collapse into "one symbol used
  nine times" — a plain falsehood about the drawing.

A fourth problem was a counting bug rather than a design gap: placements were
counted by walking the block tree, so 19 arrowheads each wrapping one nested
block reported 38. `PartInstance.depth` separates what is placed on the sheet
from what is placed inside something else.

**Verified:** `S-BOLTS` reports (13, 4); `S-ARROW_HEAD` reports (19, 1) rather
than 38; the five `3"` dimension blocks group as one symbol placed five times;
and no chunk anywhere states a bolt count.

**What this deliberately does not do:** name a geometry-only shape. Thirteen
placements on a layer called `S-BOLTS` is not thirteen bolts, and this sheet
settles the point itself — `SEE BOLT SCHEDULE FOR MINIMUM BOLT COUNT`. Asked
"how many bolts are shown on this drawing?", the live system reports the
placements and says the count cannot be determined, which is what the drawing
says.

**Known limitation:** the signature is not rotation- or scale-invariant. A
symbol placed rotated is usually a separate anonymous block in an R12 export
and will be reported as a separate shape — under-grouping, which overstates
variety rather than inventing sameness. That is the safer direction, and the
composition and size columns make the duplication visible to a reader.

**Verify:** `*U8` reads "bolt note"; the 19 `AXARROW` instances collapse to
one symbol with a count; `S-BOLTS` reports its repeated symbol honestly.

### Phase 3 — Regions on CAD chunks (G6) · **implemented**

`Region` was described in the P1 design but never built, so this phase was the
whole chain rather than the wiring the gap note implies: the value object, the
field on `ChunkMetadata`, accumulation through all three chunkers, both search
payloads, a Postgres column (`0008_chunk_regions`), and `Citation.regions`
out to the API.

* `src/domain/value_objects/provenance.py` — `Region` with `merge`, three
  coordinate conventions and three precision levels.
* `_SheetFrame` in the DXF loader converts model space to normalised sheet
  space once, so no consumer reinvents the Y-flip.
* Each callout chunk carries its own callout's box; layer chunks carry the
  box enclosing that layer's text.

**Verified live:** the bent-plate citation returns
`(0.848, 0.187)-(0.942, 0.248)` in sheet space — the top right of the sheet,
where the note actually is — at `block` precision.

Three things worth recording:

* **The Y-flip is the failure that hides.** Model space is Y-up, a highlight
  is Y-down. Get it wrong and the rectangle is perfectly valid, on the
  opposite side of the sheet from its content, and nothing downstream can
  tell. It is pinned by tests asserting that content at the top of model
  space comes out near y=0.
* **Extents must be the union of declared and actual.** This drawing declares
  `$EXTMIN/$EXTMAX` of (0,0)-(116,116) and carries review boxes out to
  x=-114. Normalising against the declaration alone puts them at a negative
  coordinate; against content alone ignores the sheet the drafter set up.
* **Precision must not be overstated, or understated.** A section cut into
  token windows has rectangles that bound text the window no longer contains,
  so it is downgraded to `section`. But a section that survived the split
  intact — every CAD callout — is still block-precise, and reporting
  `section` there would leave a viewer drawing a soft box around text we can
  locate exactly.

**Known limitation:** a layer chunk's region is one rectangle enclosing that
layer's scattered text, because `TextBlock` carries a single box. For
`S-TEXT` that is most of the sheet. Coarse, but it is the enclosing box of
real content rather than a guess, and the callout chunks — the ones that
answer questions — are tight.

### Phase 4 — Drawn schedules (G5) · **implemented**

Delivered in `src/ingestion/cad/schedules.py`, after being held back once
because a naive detector fabricated tables out of member outlines and bolt
gauge lines.

**The fixture.** The natural one is AxiomCPL's `SSD11.0` "Notes & Schedules"
set — same publisher and template system as the committed `SSD09.0-02.dxf`.
Those files were located and downloaded during this work and are deliberately
**not** committed: axiomcpl.com requires written consent to redistribute, and
they ship as AutoCAD 2000 DWG, which needs the ODA File Converter that this
project leaves for the operator to install under their own EULA. So
`tests/fixtures/cad/SSD11-member-schedule.dxf` is generated by
`scripts/make_schedule_fixture.py`, reproducing the structure rather than
copying the file, using the drafting conventions verified in the real drawing
across Phases 1–3 — R12, text inside anonymous blocks, `S-` layers, plain
`LINE` grid. **It is deliberately hostile**: it contains the schedule *and*
every false-positive grid from the real sheet, so the positive fixture is
also a negative test.

**Why the naive test cannot be the primary decision.** On `SSD09.0-02.dxf`,
"3+ horizontals and 3+ verticals" fires on eight layers and none is a table.
`S-CENTERLINE` settles the design: five evenly spaced horizontals crossed by
four verticals *is* a grid geometrically. What it is not is a table, because
nothing sits in its cells.

**So detection requires two independent kinds of evidence**, combined as a
geometric mean so neither can carry the other:

* *Structural* — a near-closed boundary, separators that span ≥80% of the
  table (this is what rejects a member outline, whose internal lines stop at
  a flange), and grid regularity.
* *Content* — text inside the cells, in ≥2 rows and ≥2 columns, with ≥35%
  cell occupancy. **A hard gate, not a score contributor.**

**Results.**

| Drawing | Detected | Rejected |
|---|---|---|
| `SSD11-member-schedule.dxf` | 1 (`MEMBER SCHEDULE`, 5×6, confidence 0.94) | 2 decoys |
| `SSD09.0-02.dxf` (real) | **0** | 36 grids, each with a reason |

Every named false positive is refused: `S-CENTERLINE` and `S-BOLTS` for
having no cell content, `S-SECT_STEEL`, `S-ARROW_HEAD`, `S-LEADER` and
`CURRENT_STATUS` on separator span. `REVIEW_TEXT_BOX` never reaches candidacy
— a bare rectangle has no interior separators.

**Chunking.** One chunk per row, written with its column names attached, so
`MARK BP-1 - DESCRIPTION ASTM A36 25MM THK MS PLATE - QTY 1` is answerable
retrieved alone. The whole table is emitted as well, as prose and as a
`TableBlock`, and each row carries its own sheet-space region so a citation
highlights the row rather than the schedule.

**Known limitation:** axis-aligned tables only. A rotated grid is refused
rather than read, because reading one as axis-aligned would produce cells
slicing across several real ones. Tested, not assumed.

### Phase 5 — Layer as a retrieval facet (G8) · **implemented**

`layers` is a list rather than a scalar, which is the one design decision
here that matters. An annotation belongs to *two* layers — the one its text
sits on and the one holding the geometry its leader points at — and a
question about either should reach it. That is what makes the association
work from Phase 1 pay off in retrieval: `S-SECT_STEEL_THRU` carries no text
at all, so before this nothing about it was reachable by any means.

* `layers` threaded from `TextBlock`/`TableBlock` through `StructuralSection`
  and `SemanticSegment` onto `ChunkMetadata`.
* Indexed as a keyword in Elasticsearch, added to `INDEXED_PAYLOAD_FIELDS` in
  Qdrant, `BM25SearchFilter.layers`, `FACETABLE_FIELDS` and `POST /search`.
* Postgres column with a partial GIN index (`0009_chunk_layers`), so
  `reindex_chunks.py` preserves it.

**Verified live** against the reprocessed reference drawing:

* `GET /search/facets?fields=layers` returns the real vocabulary —
  `S-TEXT` 43, `S-SECT_STEEL` 15, `S-SECT_STEEL_THRU` 13, `S-BOLTS` 7.
* Filter-only search: `layers=[S-BOLTS]` → 7 hits, `layers=[S-SECT_STEEL_THRU]`
  → 10, a layer that does not exist → 0.
* *"What does the S-SECT_STEEL_THRU layer contain?"* answers with the bent
  plate and purlin callouts, grounded and cited — from a layer with zero text
  on it.

### Phase 6 — Vision fallback, region-scoped (G7) · ~5–7 days

Only after 1–5, and only where deterministic extraction genuinely cannot
answer.

* Render each sheet once (matplotlib via `ezdxf.addons.drawing`).
* Crop per layer or per detected region; one vision call per *region*.
* Results marked with a distinct provenance and a confidence, never merged
  silently with deterministic facts. A vision claim that contradicts extracted
  text loses.

**Explicitly out of scope for now:** training a vector symbol spotter. It is
the right long-term answer for counting and needs a labelled corpus that does
not exist yet. Phase 2's signature grouping plus Phase 6's region prompts is
the cheap approximation until user corrections have accumulated.

---

## What this does not fix

Worth stating so the plan is not read as a promise of drawing comprehension.

* A sheet that says "SEE BOLT SCHEDULE" still does not contain the bolt count.
  Better extraction cannot invent it; the correct answer remains "this sheet
  refers to a schedule it does not contain".
* Geometry tells us *what is drawn*, not *what is meant*. A line on
  `S-SECT_STEEL` is a line; calling it a flange is inference.
* Everything above raises what can be *retrieved*. The grounding and citation
  guards stay exactly as they are — the point is to give them more evidence,
  not to lower the bar for answering.

## Sources

* GAT-CADNet, CVPR 2022 — <https://openaccess.thecvf.com/content/CVPR2022/papers/Zheng_GAT-CADNet_Graph_Attention_Network_for_Panoptic_Symbol_Spotting_in_CAD_CVPR_2022_paper.pdf>
* CADSpotting, 2024 — <https://arxiv.org/html/2412.07377v1>
* ChatP&ID (GraphRAG for engineering diagrams) — <https://arxiv.org/pdf/2603.22528>
* SteelFlo, AI structural steel takeoff — <https://www.steelfloai.com/blog/ai-structural-steel-takeoff-software>
* LlamaIndex, AI for engineering drawings — <https://www.llamaindex.ai/insights/best-ai-for-engineering-drawings>
* ezdxf LEADER entity — <https://ezdxf.readthedocs.io/en/stable/dxfentities/leader.html>
