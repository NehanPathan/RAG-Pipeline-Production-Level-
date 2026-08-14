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

### Phase 1 — Spatial association (G1, G4) · ~3–4 days

The foundation everything else builds on.

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

**Verify:** on the reference drawing, each of the seven callouts resolves to a
target, and the five `3"` dimensions bind to `S-DIMS` geometry.

**Risk:** leader conventions vary; tolerance tuning per drawing set is
likely. Mitigate by recording *unresolved* callouts explicitly rather than
guessing — an unattached note is a fact about the drawing.

### Phase 2 — Block semantics (G2, G3) · ~2–3 days

* Name each block by the text it contains, falling back to its own name.
* Compute a primitive signature per block definition (sorted type + relative
  geometry, rounded) and group identical ones.
* Replace the block schedule with: distinct symbols, instance counts, layers
  they appear on, and the label where one exists.

**Verify:** `*U8` reads "bolt note"; the 19 `AXARROW` instances collapse to
one symbol with a count; `S-BOLTS` reports its repeated symbol honestly.

### Phase 3 — Regions on CAD chunks (G6) · ~2 days

* Emit `Region` per chunk in normalised sheet space, as designed in P1.
* Extend the CAD path so citations resolve to a rectangle, not a page.

**Verify:** a bolt-note citation returns coordinates inside the sheet extents.

### Phase 4 — Drawn schedules (G5) · ~4–5 days

* Detect axis-aligned line grids: cluster collinear lines, find intersections,
  derive cells.
* Bind text to cells by containment; emit a real `TableBlock` with the
  existing row-window chunking.

**Verify:** on a sheet with a drawn schedule, cells reconstruct in reading
order. *We do not yet have such a sheet* — needs a fixture before starting.

### Phase 5 — Layer as a retrieval facet (G8) · ~1 day

* Denormalise `layer` onto chunk payloads; index as a keyword in both
  backends; add to `FACETABLE_FIELDS` and `BM25SearchFilter`.

**Verify:** "what is on S-BOLTS" filters rather than competing on similarity.

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
