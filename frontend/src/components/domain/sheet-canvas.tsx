/**
 * Synthetic sheet renderer.
 *
 * The backend renders every page to PNG during ingestion (see 14.6, "render
 * always"), and in production this component would draw that raster. Until the
 * asset route exists it draws the sheet itself — a real frame, grid bubbles,
 * I-section columns, braced bays, dimension strings and a populated title
 * block — from the document's own metadata.
 *
 * That is worth more than a grey rectangle: highlight geometry, zoom, page
 * navigation and the region overlay are all exercised against something with
 * the proportions and density of an actual drawing, so the layout is honest
 * about how it behaves on one.
 */
import * as React from "react"
import { cn } from "@/lib/utils"
import type { ContentKind, DocumentSummary } from "@/api/types"

const W = 1000
const H = 707

function seeded(seed: string) {
  let h = 2166136261
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return () => {
    h = Math.imul(h ^ (h >>> 15), 2246822507)
    h ^= h >>> 13
    return ((h >>> 0) % 100000) / 100000
  }
}

/* ------------------------------------------------------------- fragments */

/** An I-section in elevation: two flanges and a web. */
function Column({ x, y, height, width = 14 }: { x: number; y: number; height: number; width?: number }) {
  return (
    <g stroke="currentColor" strokeWidth={1.1} fill="none">
      <line x1={x - width / 2} y1={y} x2={x + width / 2} y2={y} />
      <line x1={x - width / 2} y1={y + height} x2={x + width / 2} y2={y + height} />
      <line x1={x - width / 2} y1={y} x2={x - width / 2} y2={y + height} />
      <line x1={x + width / 2} y1={y} x2={x + width / 2} y2={y + height} />
      <line x1={x} y1={y} x2={x} y2={y + height} strokeWidth={0.7} />
    </g>
  )
}

function Beam({ x1, x2, y, depth = 10 }: { x1: number; x2: number; y: number; depth?: number }) {
  return (
    <g stroke="currentColor" strokeWidth={1.1} fill="none">
      <rect x={x1} y={y - depth / 2} width={x2 - x1} height={depth} />
      <line x1={x1} y1={y} x2={x2} y2={y} strokeWidth={0.6} strokeDasharray="6 3" opacity={0.6} />
    </g>
  )
}

function GridBubble({ x, y, label }: { x: number; y: number; label: string }) {
  return (
    <g>
      <line x1={x} y1={y + 9} x2={x} y2={y + 28} stroke="currentColor" strokeWidth={0.6} strokeDasharray="4 3" />
      <circle cx={x} cy={y} r={9} fill="none" stroke="currentColor" strokeWidth={1} />
      <text
        x={x}
        y={y + 3.4}
        textAnchor="middle"
        fontSize={9}
        fontFamily="ui-monospace, monospace"
        fill="currentColor"
      >
        {label}
      </text>
    </g>
  )
}

/** A dimension string with witness lines and filled arrowheads. */
function Dimension({
  x1,
  x2,
  y,
  text,
  extend = 10,
}: {
  x1: number
  x2: number
  y: number
  text: string
  extend?: number
}) {
  return (
    <g stroke="currentColor" strokeWidth={0.7} fill="currentColor">
      <line x1={x1} y1={y - extend} x2={x1} y2={y + 4} />
      <line x1={x2} y1={y - extend} x2={x2} y2={y + 4} />
      <line x1={x1} y1={y} x2={x2} y2={y} />
      <path d={`M${x1} ${y} l6 -2.4 v4.8 z`} stroke="none" />
      <path d={`M${x2} ${y} l-6 -2.4 v4.8 z`} stroke="none" />
      <rect
        x={(x1 + x2) / 2 - text.length * 2.6 - 3}
        y={y - 11}
        width={text.length * 5.2 + 6}
        height={11}
        fill="var(--sheet)"
        stroke="none"
      />
      <text
        x={(x1 + x2) / 2}
        y={y - 2.5}
        textAnchor="middle"
        fontSize={8.5}
        fontFamily="ui-monospace, monospace"
        stroke="none"
      >
        {text}
      </text>
    </g>
  )
}

function Leader({ x, y, tx, ty, text }: { x: number; y: number; tx: number; ty: number; text: string }) {
  return (
    <g stroke="currentColor" strokeWidth={0.7} fill="currentColor">
      <circle cx={x} cy={y} r={1.6} stroke="none" />
      <path d={`M${x} ${y} L${tx - 14} ${ty + 2} L${tx} ${ty + 2}`} fill="none" />
      <text x={tx + 3} y={ty + 5} fontSize={8.5} fontFamily="ui-monospace, monospace" stroke="none">
        {text}
      </text>
    </g>
  )
}

/* --------------------------------------------------------------- layouts */

function GeneralArrangement({ rand }: { rand: () => number }) {
  const bays = 5
  const left = 90
  const right = 700
  const step = (right - left) / bays
  const baseY = 470
  const roofY = 190
  const letters = "ABCDEFGH".split("")

  return (
    <g className="text-sheet-ink">
      {/* ground line with hatching */}
      <line x1={60} y1={baseY} x2={740} y2={baseY} stroke="currentColor" strokeWidth={1.4} />
      <rect x={60} y={baseY} width={680} height={14} fill="url(#hatch)" stroke="none" opacity={0.7} />

      {/* columns and grid bubbles */}
      {Array.from({ length: bays + 1 }, (_, i) => {
        const x = left + i * step
        return (
          <g key={i}>
            <Column x={x} y={roofY} height={baseY - roofY} />
            <GridBubble x={x} y={roofY - 40} label={letters[i]} />
            {/* base plate */}
            <rect x={x - 16} y={baseY - 5} width={32} height={5} fill="none" stroke="currentColor" strokeWidth={1.1} />
          </g>
        )
      })}

      {/* roof beams */}
      {Array.from({ length: bays }, (_, i) => (
        <Beam key={i} x1={left + i * step + 7} x2={left + (i + 1) * step - 7} y={roofY + 6} />
      ))}

      {/* intermediate floor beam over part of the frame */}
      {Array.from({ length: bays }, (_, i) =>
        i % 2 === 0 ? (
          <Beam key={`f${i}`} x1={left + i * step + 7} x2={left + (i + 1) * step - 7} y={340} depth={8} />
        ) : null,
      )}

      {/* bracing in alternating bays */}
      {Array.from({ length: bays }, (_, i) => {
        if (i % 2 !== 1) return null
        const x1 = left + i * step + 7
        const x2 = left + (i + 1) * step - 7
        return (
          <g key={`b${i}`} stroke="currentColor" strokeWidth={0.9} opacity={0.85}>
            <line x1={x1} y1={baseY} x2={x2} y2={roofY + 12} />
            <line x1={x2} y1={baseY} x2={x1} y2={roofY + 12} />
          </g>
        )
      })}

      {/* dimensions */}
      <Dimension x1={left} x2={left + step} y={baseY + 46} text="6 000" />
      <Dimension x1={left + step} x2={right} y={baseY + 46} text={`${4 * 6} 000`} />
      <Dimension x1={left} x2={right} y={baseY + 76} text="30 000 OVERALL" extend={40} />

      {/* vertical dimension */}
      <g stroke="currentColor" strokeWidth={0.7} fill="currentColor">
        <line x1={745} y1={roofY} x2={770} y2={roofY} />
        <line x1={745} y1={baseY} x2={770} y2={baseY} />
        <line x1={762} y1={roofY} x2={762} y2={baseY} />
        <path d={`M762 ${roofY} l-2.4 6 h4.8 z`} stroke="none" />
        <path d={`M762 ${baseY} l-2.4 -6 h4.8 z`} stroke="none" />
        <text
          x={766}
          y={(roofY + baseY) / 2}
          fontSize={8.5}
          fontFamily="ui-monospace, monospace"
          stroke="none"
          transform={`rotate(-90 766 ${(roofY + baseY) / 2})`}
          textAnchor="middle"
        >
          9 200 TO U/S
        </text>
      </g>

      {/* member callouts */}
      <Leader x={left + step * 1.5} y={roofY + 6} tx={430} ty={132} text="ISMB 400 — BM-14" />
      <Leader x={left + step * 3} y={330} tx={470} ty={300} text="ISHB 350 — CL-07" />
      <Leader x={left + step * 1.5} y={400} tx={200} ty={560} text="ISA 100×100×10 — BR-22" />
      <Leader
        x={left}
        y={baseY - 2}
        tx={110}
        ty={600}
        text={`BP-31 PL 500×500×${20 + Math.round(rand() * 3) * 4}`}
      />

      <text x={400} y={640} textAnchor="middle" fontSize={11} fontFamily="ui-monospace, monospace" fill="currentColor">
        SECTION A–A · SCALE 1:100
      </text>
    </g>
  )
}

function BasePlateDetail({ rand }: { rand: () => number }) {
  const cx = 380
  const cy = 320
  const plate = 200
  const bolt = 14
  const offset = plate / 2 - 34

  return (
    <g className="text-sheet-ink">
      {/* plan */}
      <rect x={cx - plate / 2} y={cy - plate / 2} width={plate} height={plate} fill="none" stroke="currentColor" strokeWidth={1.4} />
      <rect x={cx - plate / 2 + 6} y={cy - plate / 2 + 6} width={plate - 12} height={plate - 12} fill="none" stroke="currentColor" strokeWidth={0.5} strokeDasharray="5 3" opacity={0.6} />

      {/* column section, I-shape in plan */}
      <g stroke="currentColor" strokeWidth={1.2} fill="none">
        <rect x={cx - 44} y={cy - 30} width={88} height={7} />
        <rect x={cx - 44} y={cy + 23} width={88} height={7} />
        <rect x={cx - 4} y={cy - 23} width={8} height={46} />
      </g>

      {/* holding-down bolts */}
      {[
        [-1, -1], [1, -1], [-1, 1], [1, 1],
      ].map(([sx, sy], i) => (
        <g key={i}>
          <circle cx={cx + sx * offset} cy={cy + sy * offset} r={bolt / 2} fill="none" stroke="currentColor" strokeWidth={1.1} />
          <line x1={cx + sx * offset - 10} y1={cy + sy * offset} x2={cx + sx * offset + 10} y2={cy + sy * offset} stroke="currentColor" strokeWidth={0.5} />
          <line x1={cx + sx * offset} y1={cy + sy * offset - 10} x2={cx + sx * offset} y2={cy + sy * offset + 10} stroke="currentColor" strokeWidth={0.5} />
        </g>
      ))}

      {/* centre lines */}
      <line x1={cx - plate / 2 - 22} y1={cy} x2={cx + plate / 2 + 22} y2={cy} stroke="currentColor" strokeWidth={0.5} strokeDasharray="14 3 3 3" opacity={0.7} />
      <line x1={cx} y1={cy - plate / 2 - 22} x2={cx} y2={cy + plate / 2 + 22} stroke="currentColor" strokeWidth={0.5} strokeDasharray="14 3 3 3" opacity={0.7} />

      {/* dimensions */}
      <Dimension x1={cx - offset} x2={cx + offset} y={cy + plate / 2 + 40} text="332" />
      <Dimension x1={cx - plate / 2} x2={cx + plate / 2} y={cy + plate / 2 + 70} text="500" extend={34} />

      {/* elevation to the right */}
      <g transform="translate(620, 250)">
        <rect x={0} y={90} width={150} height={16} fill="none" stroke="currentColor" strokeWidth={1.4} />
        <rect x={0} y={106} width={150} height={12} fill="url(#hatch)" stroke="currentColor" strokeWidth={0.7} opacity={0.8} />
        <g stroke="currentColor" strokeWidth={1.2} fill="none">
          <rect x={53} y={0} width={44} height={6} />
          <rect x={53} y={84} width={44} height={6} />
          <rect x={72} y={6} width={6} height={78} />
        </g>
        {/* weld symbol */}
        <g stroke="currentColor" strokeWidth={0.7} fill="none">
          <path d="M50 88 L36 68 L8 68" />
          <path d="M36 68 l0 -9 l11 9 z" fill="currentColor" stroke="none" />
          <text x={-2} y={64} fontSize={8} fontFamily="ui-monospace, monospace" fill="currentColor" stroke="none">
            10
          </text>
        </g>
        <text x={75} y={148} textAnchor="middle" fontSize={9} fontFamily="ui-monospace, monospace" fill="currentColor">
          50 GROUT
        </text>
        <text x={75} y={-14} textAnchor="middle" fontSize={10} fontFamily="ui-monospace, monospace" fill="currentColor">
          ELEVATION
        </text>
      </g>

      <Leader x={cx + offset} y={cy - offset} tx={520} ty={150} text={`4 nos M${rand() > 0.5 ? 24 : 20} 8.8`} />
      <Leader x={cx} y={cy - plate / 2} tx={150} ty={140} text="PL 500×500×32 · E250 BR" />

      <text x={cx} y={cy + plate / 2 + 108} textAnchor="middle" fontSize={11} fontFamily="ui-monospace, monospace" fill="currentColor">
        BASE PLATE TYPE BP-31 · SCALE 1:10
      </text>
    </g>
  )
}

function ScheduleSheet() {
  const rows = [
    ["BM-14", "ISMB 400", "E250 BR", "8 450", "12", "CAMBER 15"],
    ["BM-15", "ISMB 450", "E250 BR", "9 200", "8", "—"],
    ["CL-07", "ISHB 350", "E350 C", "11 800", "6", "SPLICE +7.200"],
    ["BR-22", "ISA 100×100×10", "E250 BR", "4 100", "24", "SINGLE ANGLE"],
    ["BP-31", "PL 500×500×32", "E250 BR", "—", "6", "4 nos M24"],
    ["TR-11", "ISWB 600", "E350 C", "14 600", "4", "BOTTOM CHORD"],
    ["GP-05", "PL 350×300×16", "E250 BR", "—", "18", "GUSSET"],
    ["ST-42", "PL 200×150×12", "E250 BR", "—", "48", "STIFFENER"],
  ]
  const headers = ["MARK", "SECTION", "GRADE", "LENGTH", "QTY", "REMARKS"]
  const widths = [80, 150, 100, 90, 60, 170]
  const x0 = 80
  const y0 = 150
  const rowH = 26

  return (
    <g className="text-sheet-ink" fontFamily="ui-monospace, monospace">
      <text x={x0} y={y0 - 22} fontSize={13} fill="currentColor" letterSpacing="1">
        BEAM &amp; MEMBER SCHEDULE
      </text>
      {/* header row */}
      <rect
        x={x0}
        y={y0}
        width={widths.reduce((a, b) => a + b, 0)}
        height={rowH}
        fill="currentColor"
        opacity={0.08}
        stroke="none"
      />
      {headers.map((header, i) => {
        const x = x0 + widths.slice(0, i).reduce((a, b) => a + b, 0)
        return (
          <text key={header} x={x + 8} y={y0 + 17} fontSize={9} fill="currentColor" letterSpacing="0.5">
            {header}
          </text>
        )
      })}
      {rows.map((row, r) =>
        row.map((cell, c) => {
          const x = x0 + widths.slice(0, c).reduce((a, b) => a + b, 0)
          return (
            <text
              key={`${r}-${c}`}
              x={x + 8}
              y={y0 + rowH * (r + 1) + 17}
              fontSize={9.5}
              fill="currentColor"
            >
              {cell}
            </text>
          )
        }),
      )}
      {/* grid lines */}
      {Array.from({ length: rows.length + 2 }, (_, i) => (
        <line
          key={`h${i}`}
          x1={x0}
          y1={y0 + i * rowH}
          x2={x0 + widths.reduce((a, b) => a + b, 0)}
          y2={y0 + i * rowH}
          stroke="currentColor"
          strokeWidth={i === 0 || i === 1 ? 1 : 0.4}
          opacity={i <= 1 ? 0.9 : 0.5}
        />
      ))}
      {widths.map((_, i) => {
        const x = x0 + widths.slice(0, i + 1).reduce((a, b) => a + b, 0)
        return (
          <line
            key={`v${i}`}
            x1={x}
            y1={y0}
            x2={x}
            y2={y0 + (rows.length + 1) * rowH}
            stroke="currentColor"
            strokeWidth={0.4}
            opacity={0.5}
          />
        )
      })}
      <line x1={x0} y1={y0} x2={x0} y2={y0 + (rows.length + 1) * rowH} stroke="currentColor" strokeWidth={1} />
    </g>
  )
}

/** A specification or report page: headings and text, not a sheet. */
function ProsePage({ rand, scanned }: { rand: () => number; scanned: boolean }) {
  const lines: React.ReactNode[] = []
  let y = 120
  const sections = ["4.3 MATERIAL SPECIFICATION", "4.4 WELDING", "4.5 SURFACE PREPARATION"]
  sections.forEach((heading, s) => {
    lines.push(
      <text key={`h${s}`} x={110} y={y} fontSize={12} fontWeight="600" fill="currentColor" fontFamily="ui-sans-serif, system-ui">
        {heading}
      </text>,
    )
    y += 26
    const count = 6 + Math.floor(rand() * 5)
    for (let i = 0; i < count; i++) {
      const width = i === count - 1 ? 300 + rand() * 300 : 740 + rand() * 40
      lines.push(
        <rect
          key={`l${s}-${i}`}
          x={110}
          y={y}
          width={width}
          height={5.5}
          rx={1}
          fill="currentColor"
          opacity={scanned ? 0.28 + rand() * 0.18 : 0.42}
        />,
      )
      y += 15
    }
    y += 20
  })
  return <g className="text-sheet-ink">{lines}</g>
}

/* ---------------------------------------------------------- title block */

function TitleBlockGraphic({
  document,
  page,
  totalPages,
}: {
  document: DocumentSummary
  page: number
  totalPages: number
}) {
  const x = 640
  const y = 560
  const w = 340
  const h = 120
  const rows = [
    [
      { label: "DRAWING NO", value: document.drawing_number ?? document.file_name.slice(0, 18), w: 200 },
      { label: "REV", value: document.revision_label ?? "—", w: 60 },
      { label: "SHEET", value: `${page}/${totalPages}`, w: 80 },
    ],
    [
      { label: "PROJECT", value: document.project_number ?? "UNASSIGNED", w: 200 },
      { label: "SCALE", value: "1:100", w: 60 },
      { label: "SIZE", value: "A1", w: 80 },
    ],
    [
      { label: "TITLE", value: (document.drawing_number ? "STRUCTURAL STEELWORK" : "DOCUMENT"), w: 340 },
    ],
    [
      { label: "DRAWN", value: "A.K.", w: 85 },
      { label: "CHECKED", value: "P.A.", w: 85 },
      { label: "APPROVED", value: "R.V.", w: 85 },
      { label: "DATE", value: new Date(document.created_at).toLocaleDateString("en-GB"), w: 85 },
    ],
  ]

  let cursorY = y
  return (
    <g fontFamily="ui-monospace, monospace" className="text-sheet-ink">
      <rect x={x} y={y} width={w} height={h} fill="var(--sheet)" stroke="currentColor" strokeWidth={1.4} />
      {rows.map((row, r) => {
        const rowH = h / rows.length
        let cursorX = x
        const cells = row.map((cell, c) => {
          const cw = (cell.w / 340) * w
          const el = (
            <g key={`${r}-${c}`}>
              <rect x={cursorX} y={cursorY} width={cw} height={rowH} fill="none" stroke="currentColor" strokeWidth={0.6} />
              <text x={cursorX + 5} y={cursorY + 10} fontSize={5.5} fill="currentColor" opacity={0.65} letterSpacing="0.6">
                {cell.label}
              </text>
              <text x={cursorX + 5} y={cursorY + 23} fontSize={9} fill="currentColor">
                {String(cell.value).slice(0, Math.floor(cw / 5.4))}
              </text>
            </g>
          )
          cursorX += cw
          return el
        })
        cursorY += rowH
        return <g key={r}>{cells}</g>
      })}
    </g>
  )
}

/* -------------------------------------------------------------- the page */

export function SheetCanvas({
  document,
  page,
  kind,
  className,
}: {
  document: DocumentSummary
  page: number
  kind: ContentKind
  className?: string
}) {
  const rand = React.useMemo(() => seeded(`${document.id}:${page}`), [document.id, page])
  const totalPages = document.page_count ?? 1
  const isDrawing = kind === "cad_native" || kind === "vector_drawing" || kind === "scanned_drawing" || kind === "mixed"
  const scanned = kind === "scanned_drawing" || kind === "scanned_prose"

  // Which layout this page gets. Deterministic, and page 1 of a drawing is
  // always the general arrangement — that is the convention.
  const layout = !isDrawing
    ? "prose"
    : page === 1
      ? "ga"
      : page === 2
        ? "detail"
        : rand() > 0.5
          ? "schedule"
          : "detail"

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className={cn("block h-auto w-full", className)}
      role="img"
      aria-label={`${document.file_name}, page ${page}`}
    >
      <defs>
        <pattern id="hatch" width="7" height="7" patternTransform="rotate(45)" patternUnits="userSpaceOnUse">
          <line x1="0" y1="0" x2="0" y2="7" stroke="currentColor" strokeWidth="1" opacity="0.55" />
        </pattern>
        <filter id="scan-noise">
          <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="3" result="noise" />
          <feColorMatrix in="noise" type="saturate" values="0" />
          <feComponentTransfer result="grain">
            <feFuncA type="linear" slope="0.16" />
          </feComponentTransfer>
          <feComposite operator="over" in="SourceGraphic" />
        </filter>
      </defs>

      {/* paper */}
      <rect width={W} height={H} fill="var(--sheet)" />

      <g filter={scanned ? "url(#scan-noise)" : undefined} opacity={scanned ? 0.88 : 1}>
        {/* sheet frame */}
        <rect x={10} y={10} width={W - 20} height={H - 20} fill="none" stroke="var(--sheet-ink)" strokeWidth={1.6} />
        <rect x={24} y={24} width={W - 48} height={H - 48} fill="none" stroke="var(--sheet-ink)" strokeWidth={0.7} opacity={0.7} />

        {/* zone letters along the frame, as a real sheet carries */}
        {["1", "2", "3", "4", "5", "6"].map((z, i) => (
          <text
            key={z}
            x={24 + ((W - 48) / 6) * (i + 0.5)}
            y={20}
            textAnchor="middle"
            fontSize={7}
            fontFamily="ui-monospace, monospace"
            fill="var(--sheet-ink)"
            opacity={0.6}
          >
            {z}
          </text>
        ))}

        {layout === "ga" && <GeneralArrangement rand={rand} />}
        {layout === "detail" && <BasePlateDetail rand={rand} />}
        {layout === "schedule" && <ScheduleSheet />}
        {layout === "prose" && <ProsePage rand={rand} scanned={scanned} />}

        {isDrawing && <TitleBlockGraphic document={document} page={page} totalPages={totalPages} />}

        {/* revision flag, top right */}
        {document.revision_label && isDrawing && (
          <g className="text-sheet-ink">
            <path d={`M${W - 74} 40 L${W - 54} 74 L${W - 94} 74 Z`} fill="none" stroke="currentColor" strokeWidth={1.2} />
            <text
              x={W - 74}
              y={70}
              textAnchor="middle"
              fontSize={12}
              fontFamily="ui-monospace, monospace"
              fill="currentColor"
            >
              {document.revision_label}
            </text>
          </g>
        )}

        {/* superseded stamp */}
        {document.is_latest === false && (
          <g transform={`rotate(-22 ${W / 2} ${H / 2})`} opacity={0.22}>
            <rect
              x={W / 2 - 220}
              y={H / 2 - 46}
              width={440}
              height={92}
              fill="none"
              stroke="var(--sens-restricted)"
              strokeWidth={5}
              rx={6}
            />
            <text
              x={W / 2}
              y={H / 2 + 20}
              textAnchor="middle"
              fontSize={56}
              fontWeight="700"
              fontFamily="ui-sans-serif, system-ui"
              fill="var(--sens-restricted)"
              letterSpacing="6"
            >
              SUPERSEDED
            </text>
          </g>
        )}
      </g>
    </svg>
  )
}

export { W as SHEET_WIDTH, H as SHEET_HEIGHT }
