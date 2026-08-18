/**
 * The sample corpus.
 *
 * Modelled on what a structural steel detailing office actually holds: a
 * handful of live jobs, each a set of drawings that exist in several
 * revisions, plus the specifications, schedules, weld procedures and site
 * correspondence that surround them. Section designations, grades and bolt
 * classes are real (IS 800 / IS 2062 / EN 10025 vocabulary), so the entity
 * chips and facet lists read the way they will against a real index.
 *
 * Everything is derived from fixed seeds — reloading the page never reshuffles
 * a table, which matters when comparing two states of a screen.
 */
import type {
  AuditEntry,
  Chunk,
  ContentKind,
  ConversationMessage,
  ConversationSummary,
  DocumentIntelligence,
  DocumentStatus,
  DocumentSummary,
  Drawing,
  EvaluationRun,
  FeedbackEntry,
  Job,
  PageClassification,
  Principal,
  Project,
  ProjectMember,
  Region,
  Risk,
  Sensitivity,
  SteelEntity,
  SystemMetrics,
} from "@/api/types"

/* -------------------------------------------------------------- helpers */

/** Deterministic PRNG so every render of the sample data is identical. */
function rng(seed: string) {
  let h = 1779033703 ^ seed.length
  for (let i = 0; i < seed.length; i++) {
    h = Math.imul(h ^ seed.charCodeAt(i), 3432918353)
    h = (h << 13) | (h >>> 19)
  }
  return () => {
    h = Math.imul(h ^ (h >>> 16), 2246822507)
    h = Math.imul(h ^ (h >>> 13), 3266489909)
    h ^= h >>> 16
    return (h >>> 0) / 4294967296
  }
}

function pick<T>(items: readonly T[], r: () => number): T {
  return items[Math.floor(r() * items.length)]
}

/** A UUID-shaped id derived from a name, so links survive a reload. */
export function uid(namespace: string, key: string): string {
  const r = rng(`${namespace}:${key}`)
  const hex = (n: number) =>
    Array.from({ length: n }, () => Math.floor(r() * 16).toString(16)).join("")
  return `${hex(8)}-${hex(4)}-4${hex(3)}-a${hex(3)}-${hex(12)}`
}

const NOW = Date.now()
const HOUR = 3600_000
const DAY = 24 * HOUR

export function agoIso(ms: number): string {
  return new Date(NOW - ms).toISOString()
}
function aheadIso(ms: number): string {
  return new Date(NOW + ms).toISOString()
}

/* ---------------------------------------------------------------- people */

export const USERS: (Principal & { display_name: string })[] = [
  {
    user_id: "00000000-0000-0000-0000-000000000001",
    email: "r.venkataraman@girder.works",
    display_name: "R. Venkataraman",
    role: "admin",
    clearance: "restricted",
    auth_provider: "firebase",
    authenticated: true,
  },
  {
    user_id: uid("user", "steward"),
    email: "p.almeida@girder.works",
    display_name: "Priya Almeida",
    role: "steward",
    clearance: "confidential",
    auth_provider: "firebase",
    authenticated: true,
  },
  {
    user_id: uid("user", "analyst"),
    email: "j.thorne@girder.works",
    display_name: "Jonas Thorne",
    role: "analyst",
    clearance: "internal",
    auth_provider: "firebase",
    authenticated: true,
  },
  {
    user_id: uid("user", "viewer"),
    email: "site.mundra@girder.works",
    display_name: "Mundra Site Office",
    role: "viewer",
    clearance: "public",
    auth_provider: "firebase",
    authenticated: true,
  },
  {
    user_id: uid("user", "detailer"),
    email: "a.kowalski@girder.works",
    display_name: "Anna Kowalski",
    role: "analyst",
    clearance: "internal",
    auth_provider: "firebase",
    authenticated: true,
  },
]

export const USER_BY_ID = new Map(USERS.map((u) => [u.user_id, u]))

/* -------------------------------------------------------- steel vocabulary */

export const SECTIONS = [
  "ISMB 300", "ISMB 400", "ISMB 450", "ISMB 600", "ISMC 200", "ISMC 300",
  "ISWB 600", "ISHB 350", "ISA 75x75x8", "ISA 100x100x10",
  "IPE 400", "HEB 300", "HEA 260", "UB 457x191x67", "UC 254x254x89",
  "SHS 200x200x10", "RHS 250x150x8", "CHS 219.1x8", "PFC 300x90",
] as const

export const GRADES = [
  "IS 2062 E250 BR", "IS 2062 E350 C", "IS 2062 E410", "Fe 415", "Fe 500D",
  "S355 J2", "S275 JR", "A36", "A572 Gr.50",
] as const

export const BOLTS = ["M16 4.6", "M20 8.8", "M24 8.8 HSFG", "M30 10.9", "M20 HSFG"] as const
export const WELDS = ["6 mm fillet", "8 mm fillet", "10 mm fillet", "CJP butt", "PJP butt", "E7018"] as const
export const MARKS = ["BM-14", "CL-07", "BR-22", "BP-31", "GP-05", "TR-11", "PL-18", "ST-42"] as const

export const ENTITY_CANONICALS = [
  ...SECTIONS, ...GRADES, ...BOLTS,
] as readonly string[]

/* -------------------------------------------------------------- projects */

interface ProjectSeed {
  number: string
  name: string
  client: string
  status: Project["status"]
  location: string
  tonnage: number | null
  startedDays: number
  targetDays: number | null
  sensitivity: Sensitivity
}

const PROJECT_SEEDS: ProjectSeed[] = [
  {
    number: "2024-0117",
    name: "Rourkela Pellet Plant — Conveyor Gantry CG-4",
    client: "Odisha Minerals & Metals Ltd",
    status: "active",
    location: "Rourkela, Odisha",
    tonnage: 1840,
    startedDays: 214,
    targetDays: 96,
    sensitivity: "confidential",
  },
  {
    number: "2024-0093",
    name: "Chakan Press Shop — Structural Steel Package",
    client: "Marwood Automotive India Pvt Ltd",
    status: "active",
    location: "Chakan MIDC, Pune",
    tonnage: 3260,
    startedDays: 268,
    targetDays: 41,
    sensitivity: "internal",
  },
  {
    number: "2025-0022",
    name: "Hosur Data Centre — Rooftop Plant Platform",
    client: "Meridian Digital Infrastructure",
    status: "active",
    location: "Hosur, Tamil Nadu",
    tonnage: 415,
    startedDays: 63,
    targetDays: 158,
    sensitivity: "restricted",
  },
  {
    number: "2023-0451",
    name: "Mundra Bulk Terminal — Ship Loader Frame",
    client: "Sanghavi Ports & SEZ",
    status: "on_hold",
    location: "Mundra, Gujarat",
    tonnage: 2270,
    startedDays: 512,
    targetDays: null,
    sensitivity: "internal",
  },
  {
    number: "2023-0288",
    name: "Vizag Refinery — Pipe Rack Modules PR-11…PR-18",
    client: "Coastal Petrochem Corporation",
    status: "closed",
    location: "Visakhapatnam, AP",
    tonnage: 5910,
    startedDays: 740,
    targetDays: null,
    sensitivity: "confidential",
  },
]

export const PROJECTS: Project[] = PROJECT_SEEDS.map((seed) => {
  const r = rng(`project:${seed.number}`)
  const id = uid("project", seed.number)
  return {
    id,
    project_number: seed.number,
    name: seed.name,
    client_name: seed.client,
    status: seed.status,
    start_date: agoIso(seed.startedDays * DAY),
    target_completion_date: seed.targetDays ? aheadIso(seed.targetDays * DAY) : null,
    default_sensitivity: seed.sensitivity,
    created_at: agoIso(seed.startedDays * DAY),
    updated_at: agoIso(Math.floor(r() * 6) * DAY + HOUR),
    archived_at: null,
    location: seed.location,
    tonnage_t: seed.tonnage,
    last_activity_at: agoIso(Math.floor(r() * 72) * HOUR),
  }
})

export const PROJECT_BY_ID = new Map(PROJECTS.map((p) => [p.id, p]))
export const PROJECT_BY_NUMBER = new Map(PROJECTS.map((p) => [p.project_number, p]))

export const PROJECT_MEMBERS: ProjectMember[] = PROJECTS.flatMap((project, index) => {
  const roster = [
    { user: USERS[0], role: "owner" as const },
    { user: USERS[1], role: "contributor" as const },
    { user: USERS[2], role: "contributor" as const },
    { user: USERS[4], role: "contributor" as const },
    { user: USERS[3], role: "reader" as const },
  ]
  // The closed job and the restricted one have tighter rosters, which is what
  // makes the members panel worth showing at all.
  const size = project.status === "closed" ? 2 : project.default_sensitivity === "restricted" ? 3 : 5
  return roster.slice(0, size).map(({ user, role }) => ({
    project_id: project.id,
    user_id: user.user_id,
    project_role: role,
    added_at: agoIso((200 - index * 20) * DAY),
    email: user.email,
    display_name: user.display_name,
    platform_role: user.role,
  }))
})

/* -------------------------------------------------------------- drawings */

interface DrawingSeed {
  project: string
  number: string
  sheet: string | null
  discipline: string
  title: string
  revisions: string[]
  kind: ContentKind
}

const DRAWING_SEEDS: DrawingSeed[] = [
  // Rourkela — conveyor gantry
  { project: "2024-0117", number: "CG4-S-101", sheet: "01", discipline: "Structural", title: "General Arrangement — Gantry CG-4 Plan & Elevations", revisions: ["A", "B", "C"], kind: "vector_drawing" },
  { project: "2024-0117", number: "CG4-S-104", sheet: "01", discipline: "Structural", title: "Column Base Plate Details — Grid A/1 to A/9", revisions: ["A", "B", "C", "D"], kind: "cad_native" },
  { project: "2024-0117", number: "CG4-S-112", sheet: "01", discipline: "Structural", title: "Beam Schedule — Gantry Trusses T1–T6", revisions: ["A", "B"], kind: "prose" },
  { project: "2024-0117", number: "CG4-S-140", sheet: "02", discipline: "Structural", title: "Truss Elevation & Member Marks — Span 4", revisions: ["A", "B", "C"], kind: "cad_native" },
  { project: "2024-0117", number: "CG4-S-205", sheet: "01", discipline: "Structural", title: "Vertical Bracing Layout — Bents 3–7", revisions: ["A"], kind: "vector_drawing" },
  { project: "2024-0117", number: "CG4-M-310", sheet: "01", discipline: "Mechanical", title: "Conveyor Idler Support Brackets", revisions: ["A", "B"], kind: "scanned_drawing" },

  // Chakan press shop
  { project: "2024-0093", number: "CPS-S-101", sheet: "01", discipline: "Structural", title: "Press Shop GA — Column Grid & Roof Plan", revisions: ["A", "B", "C", "D", "E"], kind: "vector_drawing" },
  { project: "2024-0093", number: "CPS-S-118", sheet: "01", discipline: "Structural", title: "Crane Girder Details — 40T EOT, Bay 2", revisions: ["A", "B", "C"], kind: "cad_native" },
  { project: "2024-0093", number: "CPS-S-131", sheet: "01", discipline: "Structural", title: "Rafter to Column Moment Connection", revisions: ["A", "B"], kind: "cad_native" },
  { project: "2024-0093", number: "CPS-S-142", sheet: "01", discipline: "Structural", title: "Purlin & Side Rail Layout", revisions: ["A", "B", "C"], kind: "vector_drawing" },
  { project: "2024-0093", number: "CPS-S-160", sheet: "01", discipline: "Structural", title: "Press Pit Retaining Frame — Sections", revisions: ["A"], kind: "scanned_drawing" },
  { project: "2024-0093", number: "CPS-C-020", sheet: "01", discipline: "Civil", title: "Foundation Bolt Setting Plan", revisions: ["A", "B"], kind: "vector_drawing" },

  // Hosur data centre
  { project: "2025-0022", number: "HDC-S-201", sheet: "01", discipline: "Structural", title: "Rooftop Plant Platform — Framing Plan", revisions: ["P1", "C1"], kind: "cad_native" },
  { project: "2025-0022", number: "HDC-S-214", sheet: "01", discipline: "Structural", title: "Chiller Support Frame — Anti-Vibration Detail", revisions: ["P1", "P2", "C1"], kind: "cad_native" },
  { project: "2025-0022", number: "HDC-S-230", sheet: "01", discipline: "Structural", title: "Access Walkway & Handrail Standard Detail", revisions: ["C1"], kind: "vector_drawing" },

  // Mundra ship loader
  { project: "2023-0451", number: "MSL-S-401", sheet: "01", discipline: "Structural", title: "Ship Loader Portal Frame — GA", revisions: ["0", "1", "2"], kind: "vector_drawing" },
  { project: "2023-0451", number: "MSL-S-418", sheet: "01", discipline: "Structural", title: "Boom Lattice Node Connections", revisions: ["0", "1"], kind: "scanned_drawing" },
  { project: "2023-0451", number: "MSL-S-455", sheet: "01", discipline: "Structural", title: "Rail Clamp & Storm Anchor Details", revisions: ["0"], kind: "scanned_drawing" },

  // Vizag pipe racks
  { project: "2023-0288", number: "VZR-S-711", sheet: "01", discipline: "Structural", title: "Pipe Rack PR-14 — Transverse Frame", revisions: ["0", "1", "2", "3"], kind: "vector_drawing" },
  { project: "2023-0288", number: "VZR-S-725", sheet: "01", discipline: "Structural", title: "Longitudinal Bracing PR-11 to PR-18", revisions: ["0", "1"], kind: "vector_drawing" },
  { project: "2023-0288", number: "VZR-S-760", sheet: "01", discipline: "Structural", title: "Anchor Bolt Schedule & Grout Detail", revisions: ["0", "1", "2"], kind: "prose" },
]

/** Non-drawing documents: the paperwork that surrounds a steel package. */
interface DocSeed {
  project: string
  name: string
  type: string
  domain: string
  kind: ContentKind
  sensitivity?: Sensitivity
  status?: DocumentStatus
  tags?: string[]
}

const LOOSE_DOC_SEEDS: DocSeed[] = [
  { project: "2024-0117", name: "CG4 Structural Design Basis Report Rev C.pdf", type: "pdf", domain: "design-basis", kind: "prose", tags: ["design-basis", "IS 800"] },
  { project: "2024-0117", name: "Weld Procedure Specification WPS-014 (SMAW).pdf", type: "pdf", domain: "qa-qc", kind: "prose", tags: ["WPS", "E7018"] },
  { project: "2024-0117", name: "MTC — Plate 20mm Heat 4471822.pdf", type: "pdf", domain: "qa-qc", kind: "scanned_prose", tags: ["MTC", "IS 2062 E250 BR"] },
  { project: "2024-0117", name: "Site Query SQ-088 — Base plate grout gap.docx", type: "docx", domain: "correspondence", kind: "prose", tags: ["site-query"] },
  { project: "2024-0093", name: "Chakan Bill of Materials Rev D.xlsx", type: "xlsx", domain: "fabrication", kind: "prose", tags: ["BOM"] },
  { project: "2024-0093", name: "Crane Girder Fatigue Check — Bay 2.pdf", type: "pdf", domain: "calculations", kind: "prose", tags: ["fatigue", "EOT"] },
  { project: "2024-0093", name: "Erection Method Statement Rev B.pdf", type: "pdf", domain: "erection", kind: "prose", tags: ["method-statement"] },
  { project: "2024-0093", name: "Bolt Torque Record — Bay 2 (scanned).pdf", type: "pdf", domain: "qa-qc", kind: "scanned_prose", tags: ["torque", "M24 8.8 HSFG"] },
  { project: "2025-0022", name: "HDC Platform Load Schedule.xlsx", type: "xlsx", domain: "design-basis", kind: "prose", sensitivity: "restricted", tags: ["loads"] },
  { project: "2025-0022", name: "Client Specification — Meridian STR-001 Rev 4.pdf", type: "pdf", domain: "specification", kind: "prose", sensitivity: "restricted", tags: ["client-spec"] },
  { project: "2023-0451", name: "Ship Loader Wind Load Assessment.pdf", type: "pdf", domain: "calculations", kind: "prose", tags: ["wind", "IS 875"] },
  { project: "2023-0451", name: "Legacy Fabrication Drawings Bundle (scan).pdf", type: "pdf", domain: "archive", kind: "scanned_drawing", status: "failed", tags: ["legacy"] },
  { project: "2023-0288", name: "PR-14 Anchor Bolt Pull-Out Test Report.pdf", type: "pdf", domain: "qa-qc", kind: "scanned_prose", tags: ["testing"] },
  { project: "2023-0288", name: "As-Built Marked Up Set PR-11 to PR-18.pdf", type: "pdf", domain: "as-built", kind: "scanned_drawing", tags: ["as-built"] },
  { project: "2024-0093", name: "CPS-S-118 Crane Girder Model.dxf", type: "dxf", domain: "cad", kind: "cad_native", tags: ["CAD"] },
  { project: "2024-0117", name: "CG4-S-104 Base Plate Model.dxf", type: "dxf", domain: "cad", kind: "cad_native", tags: ["CAD"] },
]

/* ------------------------------------------------- documents & drawings */

export const DRAWINGS: Drawing[] = []
export const DOCUMENTS: DocumentSummary[] = []

function sensitivityFor(project: Project, r: () => number): Sensitivity {
  const base = project.default_sensitivity ?? "internal"
  if (base === "restricted") return r() < 0.7 ? "restricted" : "confidential"
  if (base === "confidential") return r() < 0.55 ? "confidential" : "internal"
  return r() < 0.15 ? "public" : "internal"
}

DRAWING_SEEDS.forEach((seed, seedIndex) => {
  const project = PROJECT_BY_NUMBER.get(seed.project)!
  const r = rng(`drawing:${seed.number}`)
  const drawingId = uid("drawing", seed.number)
  const latestLabel = seed.revisions[seed.revisions.length - 1]
  let currentDocumentId = ""
  let currentRevisionDate: string | null = null

  seed.revisions.forEach((label, index) => {
    const isLatest = index === seed.revisions.length - 1
    const documentId = uid("document", `${seed.number}:${label}`)
    // Older revisions are older files. Spread them across the job's life.
    const ageDays = Math.round(
      (seed.revisions.length - index) * (14 + r() * 20) + r() * 6,
    )
    const pageCount = 1 + Math.floor(r() * 3)
    const uploader = USERS[(seedIndex + index) % 4]

    // One in-flight revision and one failure, so the library shows the
    // states an operator actually has to act on rather than all-green.
    let status: DocumentStatus = "indexed"
    if (isLatest && seed.number === "HDC-S-214") status = "processing"
    if (isLatest && seed.number === "MSL-S-455") status = "failed"

    DOCUMENTS.push({
      id: documentId,
      file_name: `${seed.number}${seed.sheet ? `-${seed.sheet}` : ""}_Rev${label}.${seed.kind === "cad_native" ? "dxf" : "pdf"}`,
      file_type: seed.kind === "cad_native" ? "dxf" : "pdf",
      status,
      page_count: status === "indexed" ? pageCount : null,
      word_count: status === "indexed" ? 180 + Math.floor(r() * 900) : null,
      domain: "drawing",
      tags: [seed.discipline.toLowerCase(), ...Array.from({ length: 2 + Math.floor(r() * 3) }, () => pick(ENTITY_CANONICALS, r))],
      indexed_at: status === "indexed" ? agoIso(ageDays * DAY - 2 * HOUR) : null,
      created_at: agoIso(ageDays * DAY),
      sensitivity: sensitivityFor(project, r),
      retention_until: aheadIso((2555 - ageDays) * DAY),
      file_size_bytes: Math.round((0.8 + r() * 11) * 1024 * 1024),
      project_id: project.id,
      project_number: project.project_number,
      drawing_id: drawingId,
      drawing_number: seed.number,
      revision_label: label,
      revision_index: index,
      revision_date: agoIso(ageDays * DAY),
      revision_note: revisionNote(label, seed.number, index, r),
      is_latest: isLatest,
      superseded_by_document_id: isLatest
        ? null
        : uid("document", `${seed.number}:${seed.revisions[index + 1]}`),
      content_kind: seed.kind,
      uploaded_by: uploader.user_id,
      chunk_count: status === "indexed" ? 12 + Math.floor(r() * 60) : 0,
    })

    if (isLatest) {
      currentDocumentId = documentId
      currentRevisionDate = agoIso(ageDays * DAY)
    }
  })

  DRAWINGS.push({
    id: drawingId,
    drawing_number: seed.number,
    sheet_number: seed.sheet,
    project_id: project.id,
    project_number: project.project_number,
    discipline: seed.discipline,
    title: seed.title,
    created_at: agoIso(200 * DAY),
    updated_at: currentRevisionDate ?? agoIso(30 * DAY),
    revision_count: seed.revisions.length,
    current_revision_label: latestLabel,
    current_document_id: currentDocumentId,
    current_revision_date: currentRevisionDate,
    content_kind: seed.kind,
    status: DOCUMENTS.find((d) => d.id === currentDocumentId)?.status ?? "indexed",
  })
})

function revisionNote(label: string, drawing: string, index: number, r: () => number): string {
  if (index === 0) return "First issue for review."
  const notes = [
    "Base plate thickness increased to 32 mm following revised uplift.",
    "Bolt group changed from M20 8.8 to M24 8.8 HSFG at gridline C.",
    "Stiffener spacing revised to suit revised crane wheel loads.",
    "Grade updated to IS 2062 E350 C for members in the splash zone.",
    "Weld size increased to 10 mm fillet at truss node N4.",
    "Issued for construction. Site query SQ-088 incorporated.",
    "Purlin cleat holes revised to slotted; erection tolerance comment.",
    "Member mark BM-14 re-sectioned ISMB 400 → ISWB 600.",
    "Clash with cable tray resolved; brace moved one bay north.",
  ]
  const note = notes[Math.floor(r() * notes.length)]
  return `${label === "C1" ? "Issued for construction. " : ""}${note} (${drawing})`
}

LOOSE_DOC_SEEDS.forEach((seed, index) => {
  const project = PROJECT_BY_NUMBER.get(seed.project)!
  const r = rng(`doc:${seed.name}`)
  const ageDays = 3 + Math.floor(r() * 180)
  const status = seed.status ?? (index === 3 ? "processing" : "indexed")
  DOCUMENTS.push({
    id: uid("document", seed.name),
    file_name: seed.name,
    file_type: seed.type,
    status,
    page_count: status === "indexed" ? 1 + Math.floor(r() * 40) : null,
    word_count: status === "indexed" ? 400 + Math.floor(r() * 9000) : null,
    domain: seed.domain,
    tags: [...(seed.tags ?? []), ...Array.from({ length: 1 + Math.floor(r() * 2) }, () => pick(ENTITY_CANONICALS, r))],
    indexed_at: status === "indexed" ? agoIso(ageDays * DAY - HOUR) : null,
    created_at: agoIso(ageDays * DAY),
    sensitivity: seed.sensitivity ?? sensitivityFor(project, r),
    retention_until: aheadIso((2555 - ageDays) * DAY),
    file_size_bytes: Math.round((0.2 + r() * 24) * 1024 * 1024),
    project_id: project.id,
    project_number: project.project_number,
    drawing_id: null,
    drawing_number: null,
    revision_label: null,
    revision_index: null,
    revision_date: null,
    revision_note: null,
    is_latest: true,
    superseded_by_document_id: null,
    content_kind: seed.kind,
    uploaded_by: USERS[index % 4].user_id,
    chunk_count: status === "indexed" ? 20 + Math.floor(r() * 240) : 0,
  })
})

/** One personal document with no project — the `project_id IS NULL` case. */
DOCUMENTS.push({
  id: uid("document", "personal-notes"),
  file_name: "Connection design scratch — moment splice.docx",
  file_type: "docx",
  status: "indexed",
  page_count: 4,
  word_count: 1120,
  domain: "notes",
  tags: ["personal", "ISMB 600", "M24 8.8 HSFG"],
  indexed_at: agoIso(2 * DAY),
  created_at: agoIso(2 * DAY),
  sensitivity: "internal",
  retention_until: aheadIso(2553 * DAY),
  file_size_bytes: 486_000,
  project_id: null,
  project_number: null,
  drawing_id: null,
  drawing_number: null,
  revision_label: null,
  revision_index: null,
  revision_date: null,
  revision_note: null,
  is_latest: true,
  content_kind: "prose",
  uploaded_by: USERS[0].user_id,
  chunk_count: 9,
})

export const DOCUMENT_BY_ID = new Map(DOCUMENTS.map((d) => [d.id, d]))
export const DRAWING_BY_ID = new Map(DRAWINGS.map((d) => [d.id, d]))

// Counters the register views read, computed once from the corpus itself so
// they can never disagree with the rows they sit above.
for (const project of PROJECTS) {
  const docs = DOCUMENTS.filter((d) => d.project_id === project.id)
  project.document_count = docs.length
  project.drawing_count = DRAWINGS.filter((d) => d.project_id === project.id).length
  project.member_count = PROJECT_MEMBERS.filter((m) => m.project_id === project.id).length
  project.open_jobs = docs.filter((d) => d.status === "processing" || d.status === "pending").length
}

/* ---------------------------------------------------------------- chunks */

const CHUNK_TEXT: Record<string, string[]> = {
  title_block: [
    "DRAWING NO: {drawing}   REV: {rev}   SCALE 1:50   SHEET 1 OF 3\nPROJECT: {project}\nCLIENT: {client}\nDRAWN: A.K.   CHECKED: P.A.   APPROVED: R.V.\nDATE: {date}   STATUS: ISSUED FOR CONSTRUCTION",
  ],
  schedule: [
    "BEAM SCHEDULE (rows 1–6 of 18)\nMARK | SECTION | GRADE | LENGTH (mm) | QTY | REMARKS\nBM-14 | ISMB 400 | IS 2062 E250 BR | 8 450 | 12 | Camber 15 mm\nBM-15 | ISMB 450 | IS 2062 E250 BR | 9 200 | 8 | —\nCL-07 | ISHB 350 | IS 2062 E350 C | 11 800 | 6 | Splice at +7.200\nBR-22 | ISA 100x100x10 | IS 2062 E250 BR | 4 100 | 24 | Single angle brace\nBP-31 | PL 500x500x32 | IS 2062 E250 BR | — | 6 | 4 nos M24 holes\nTR-11 | ISWB 600 | IS 2062 E350 C | 14 600 | 4 | Truss bottom chord",
  ],
  notes: [
    "GENERAL NOTES\n1. All structural steelwork to conform to IS 800:2007 and IS 2062:2011.\n2. All welding to be carried out to IS 9595 by qualified welders; electrodes E7018 (low hydrogen), pre-heat 100 °C for plate thickness above 25 mm.\n3. All bolts M20 grade 8.8 unless noted otherwise. HSFG bolts to IS 3757, tightened by turn-of-nut method.\n4. Base plates to be set on 50 mm non-shrink cementitious grout, minimum compressive strength 60 MPa at 28 days.\n5. Surface preparation SA 2.5; primer 75 µm zinc phosphate epoxy, finish coat 50 µm polyurethane.",
  ],
  detail: [
    "COLUMN BASE PLATE DETAIL — TYPE BP-31\nBase plate 500 × 500 × 32 thk, grade IS 2062 E250 BR. Column ISHB 350 welded all round with 10 mm fillet weld. Four holding-down bolts M24 grade 8.8, 600 mm embedment, 90 mm projection above grout. Anchor bolt sleeves 75 mm dia × 200 mm deep. Grout gap 50 mm minimum, 75 mm maximum. Shear key ISMC 200 × 150 long welded to underside where noted.",
    "MOMENT CONNECTION — RAFTER TO COLUMN\nExtended end plate 25 mm thk grade IS 2062 E350 C, 10 nos M24 8.8 HSFG bolts in two vertical rows at 90 mm gauge, 70/140/140/140/70 pitch. Column web stiffeners 16 mm thk both sides, fitted and welded with 8 mm fillet. Design moment 342 kNm, shear 186 kN.",
    "CRANE GIRDER DETAIL — 40 T EOT, BAY 2\nGirder ISWB 600 with 250 × 20 top flange plate, grade IS 2062 E350 C. Rail CR-100 fixed with proprietary clips at 750 mm centres. Web stiffeners at 1 500 mm centres, 12 mm thk. Fatigue category 71 to IS 800 Annex T; 2 × 10⁶ cycles design life. Vertical deflection limit L/1000 = 12 mm at rail level.",
  ],
  spec: [
    "4.3 MATERIAL SPECIFICATION\nAll hot-rolled sections shall conform to IS 2062:2011 Grade E250 (Fe 410 W) Quality BR, except members identified on the drawings as E350 C which shall conform to IS 2062:2011 Grade E350 Quality C with a guaranteed Charpy V-notch impact value of 27 J at 0 °C. Mill test certificates traceable to heat number shall be furnished for every consignment. Substitution of grades is not permitted without written approval from the Engineer.",
    "6.1 DESIGN LOADS\nDead load of steelwork computed from section weights plus 5% for connections. Imposed load on access walkways 5.0 kN/m². Wind load per IS 875 (Part 3):2015, basic wind speed Vb = 50 m/s, terrain category 2, risk coefficient k1 = 1.00, topography k3 = 1.00. Seismic per IS 1893 (Part 4), zone III, Z = 0.16, importance factor 1.5, response reduction factor 4.0 for an ordinary moment-resisting frame.",
  ],
  bom: [
    "BILL OF MATERIALS — PART LIST (rows 12–19 of 143)\nITEM | MARK | DESCRIPTION | GRADE | QTY | UNIT WT (kg) | TOTAL (kg)\n12 | CL-07 | ISHB 350 × 11 800 | E350 C | 6 | 838.9 | 5 033\n13 | BP-31 | PL 500×500×32 | E250 BR | 6 | 62.8 | 377\n14 | GP-05 | Gusset PL 350×300×16 | E250 BR | 18 | 13.2 | 238\n15 | BM-14 | ISMB 400 × 8 450 | E250 BR | 12 | 511.6 | 6 139\n16 | BR-22 | ISA 100×100×10 × 4 100 | E250 BR | 24 | 60.0 | 1 440\n17 | ST-42 | Stiffener PL 200×150×12 | E250 BR | 48 | 2.8 | 134",
  ],
}

function makeRegions(r: () => number, page: number, count: number): Region[] {
  return Array.from({ length: count }, () => {
    const x0 = 0.06 + r() * 0.5
    const y0 = 0.08 + r() * 0.72
    return {
      page_number: page,
      x0,
      y0,
      x1: Math.min(0.96, x0 + 0.12 + r() * 0.34),
      y1: Math.min(0.96, y0 + 0.03 + r() * 0.1),
      space: "page" as const,
    }
  })
}

function extractEntities(text: string, r: () => number): SteelEntity[] {
  const found: SteelEntity[] = []
  const add = (canonical: string, type: SteelEntity["type"], surface: string) => {
    if (found.some((e) => e.canonical === canonical)) return
    const inGazetteer = (SECTIONS as readonly string[]).includes(canonical) ||
      (GRADES as readonly string[]).includes(canonical)
    found.push({
      canonical,
      surface,
      type,
      confidence: inGazetteer ? 0.95 : r() < 0.6 ? 0.85 : 0.6,
      source: inGazetteer ? "gazetteer" : r() < 0.7 ? "pattern" : "context",
      occurrences: 1 + Math.floor(r() * 4),
    })
  }
  for (const section of SECTIONS) if (text.includes(section)) add(section, "section", section)
  for (const grade of GRADES) if (text.includes(grade)) add(grade, "grade", grade)
  for (const bolt of BOLTS) if (text.includes(bolt)) add(bolt, "bolt", bolt)
  for (const weld of WELDS) if (text.includes(weld)) add(weld, "weld", weld)
  for (const mark of MARKS) if (text.includes(mark)) add(mark, "mark", mark)
  return found
}

const chunkCache = new Map<string, Chunk[]>()

export function chunksFor(documentId: string): Chunk[] {
  const cached = chunkCache.get(documentId)
  if (cached) return cached

  const doc = DOCUMENT_BY_ID.get(documentId)
  if (!doc || doc.status !== "indexed") return []

  const r = rng(`chunks:${documentId}`)
  const project = doc.project_id ? PROJECT_BY_ID.get(doc.project_id) : undefined
  const isDrawing = Boolean(doc.drawing_number)
  const pool: (keyof typeof CHUNK_TEXT)[] = isDrawing
    ? ["title_block", "detail", "notes", "schedule"]
    : ["spec", "notes", "bom", "schedule"]

  const total = Math.min(doc.chunk_count ?? 14, 26)
  const chunks: Chunk[] = []
  let parentId: string | null = null

  for (let i = 0; i < total; i++) {
    const kindKey = i === 0 && isDrawing ? "title_block" : pick(pool, r)
    const variants = CHUNK_TEXT[kindKey]
    let content = variants[Math.floor(r() * variants.length)]
      .replace("{drawing}", doc.drawing_number ?? "—")
      .replace("{rev}", doc.revision_label ?? "—")
      .replace("{project}", project?.name ?? "Unassigned")
      .replace("{client}", project?.client_name ?? "—")
      .replace("{date}", new Date(doc.created_at).toLocaleDateString("en-GB"))

    // Vary the tail so no two chunks are byte-identical in the viewer.
    if (r() < 0.5) {
      content += `\n\nRefer ${pick(MARKS, r)} on ${doc.drawing_number ?? "the general arrangement"} for typical arrangement.`
    }

    const page = Math.min(doc.page_count ?? 1, 1 + Math.floor(r() * (doc.page_count ?? 1)))
    const isParent = i % 4 === 0
    const id = uid("chunk", `${documentId}:${i}`)
    if (isParent) parentId = id

    const scanned = doc.content_kind === "scanned_drawing" || doc.content_kind === "scanned_prose"
    const precision: Chunk["region_precision"] =
      kindKey === "title_block" || kindKey === "detail" ? "block" : r() < 0.5 ? "section" : "page"

    chunks.push({
      id,
      parent_chunk_id: isParent ? null : parentId,
      chunk_type: isParent ? "parent" : kindKey === "schedule" || kindKey === "bom" ? "table" : "child",
      content,
      position: i,
      page_number: page,
      section_title:
        ({
          title_block: "Title block",
          schedule: "Beam schedule",
          notes: "General notes",
          detail: "Connection details",
          spec: "Technical specification",
          bom: "Bill of materials",
        } as Record<string, string>)[kindKey] ?? null,
      heading_level: isParent ? 1 : 2,
      semantic_cluster: Math.floor(r() * 6),
      ocr_confidence: scanned ? 0.18 + r() * 0.55 : null,
      language: "en",
      token_count: Math.round(content.length / 3.6),
      embedding_model: "text-embedding-3-large",
      content_kind: doc.content_kind ?? "prose",
      region_precision: precision,
      regions: makeRegions(r, page, precision === "block" ? 1 + Math.floor(r() * 2) : 1),
      entities: extractEntities(content, r),
    })
  }

  chunkCache.set(documentId, chunks)
  return chunks
}

/* -------------------------------------------------------- intelligence */

export function intelligenceFor(documentId: string): DocumentIntelligence | null {
  const doc = DOCUMENT_BY_ID.get(documentId)
  if (!doc || doc.status !== "indexed") return null
  const r = rng(`intel:${documentId}`)
  const scanned = doc.content_kind === "scanned_drawing" || doc.content_kind === "scanned_prose"
  const chunks = chunksFor(documentId)

  const pages: PageClassification[] = Array.from({ length: doc.page_count ?? 1 }, (_, i) => {
    const kinds: ContentKind[] = doc.drawing_number
      ? ["vector_drawing", "cad_native", "mixed", "scanned_drawing"]
      : ["prose", "prose", "scanned_prose", "mixed"]
    const kind = i === 0 ? (doc.content_kind ?? kinds[0]) : pick(kinds, r)
    const isScan = kind === "scanned_drawing" || kind === "scanned_prose"
    return {
      page_number: i + 1,
      kind,
      confidence: Number((isScan ? 0.9 + r() * 0.1 : 0.35 + r() * 0.65).toFixed(2)),
      native_text_words: isScan ? 0 : Math.round(20 + r() * 400),
      loader_words: Math.round(25 + r() * 420),
      ocr_ran: isScan || r() < 0.2,
      ocr_confidence: isScan ? Number((0.18 + r() * 0.5).toFixed(2)) : null,
    }
  })

  const edges = chunks.slice(0, 12).flatMap((chunk, i) =>
    chunks
      .slice(i + 1, i + 3)
      .filter(() => r() < 0.55)
      .map((other) => ({
        chunk_id_a: chunk.id,
        chunk_id_b: other.id,
        similarity: Number((0.62 + r() * 0.34).toFixed(3)),
      })),
  )

  return {
    document_id: documentId,
    ocr_engine: scanned ? "tesseract-5.3" : "none",
    ocr_ran: scanned,
    ocr_confidence_avg: scanned ? Number((0.22 + r() * 0.4).toFixed(3)) : null,
    ocr_processing_time_ms: scanned ? Math.round(2400 + r() * 26000) : 0,
    ocr_language: scanned ? "eng" : null,
    embedding_model_chunking: "BAAI/bge-m3",
    embedding_model_retrieval: "text-embedding-3-large",
    layout: {
      headings: chunks
        .filter((c) => c.heading_level === 1)
        .slice(0, 8)
        .map((c) => ({ text: c.section_title ?? "Section", level: 1, page: c.page_number ?? 1 })),
      outline: chunks.slice(0, 6).map((c) => ({
        text: c.section_title ?? "Section",
        level: c.heading_level ?? 2,
        page: c.page_number ?? 1,
      })),
      tables_count: chunks.filter((c) => c.chunk_type === "table").length,
      figures_count: doc.drawing_number ? 1 + Math.floor(r() * 5) : Math.floor(r() * 3),
      lists_count: Math.floor(r() * 9),
      forms_count: doc.drawing_number ? 1 : 0,
      footnotes_count: Math.floor(r() * 4),
    },
    semantic_graph: edges,
    created_at: doc.indexed_at,
    pages,
  }
}

/* ------------------------------------------------------------------ jobs */

export const JOBS: Job[] = (() => {
  const jobs: Job[] = []
  const stages: Job["stage"][] = ["load", "classify", "ocr", "layout", "extract", "chunk", "embed", "index"]

  DOCUMENTS.forEach((doc, index) => {
    const r = rng(`job:${doc.id}`)
    const base = {
      document_id: doc.id,
      document_name: doc.file_name,
      job_type: "ingest_document" as const,
      max_attempts: 3,
    }
    if (doc.status === "processing") {
      jobs.push({
        ...base,
        id: uid("job", `${doc.id}:run`),
        status: "running",
        attempts: 1,
        error_message: null,
        created_at: agoIso(Math.round(r() * 20 + 2) * 60_000),
        started_at: agoIso(Math.round(r() * 18 + 1) * 60_000),
        finished_at: null,
        stage: stages[Math.floor(r() * stages.length)],
        progress: Math.round(15 + r() * 70),
      })
      return
    }
    if (doc.status === "failed") {
      jobs.push({
        ...base,
        id: uid("job", `${doc.id}:fail`),
        status: "failed",
        attempts: 3,
        error_message:
          "OCR stage failed: tesseract exited 1 on page 4 — `Error in pixReadStream: Pdf reading is not supported`. Host is missing poppler-utils; the document has no extractable text layer to fall back on.",
        created_at: agoIso(6 * HOUR),
        started_at: agoIso(6 * HOUR - 30_000),
        finished_at: agoIso(5.4 * HOUR),
        stage: "ocr",
        progress: 38,
      })
      return
    }
    if (index % 5 === 0) {
      jobs.push({
        ...base,
        id: uid("job", `${doc.id}:ok`),
        status: "succeeded",
        attempts: 1,
        error_message: null,
        created_at: doc.created_at,
        started_at: doc.created_at,
        finished_at: doc.indexed_at,
        stage: "done",
        progress: 100,
      })
    }
  })

  // A couple queued behind the running ones, so the queue view has depth.
  const queuedDocs = DOCUMENTS.filter((d) => d.status === "indexed").slice(0, 3)
  queuedDocs.forEach((doc, i) => {
    jobs.push({
      id: uid("job", `${doc.id}:queued`),
      document_id: doc.id,
      document_name: doc.file_name,
      job_type: "reindex_document",
      status: "queued",
      attempts: 0,
      max_attempts: 3,
      error_message: null,
      created_at: agoIso((i + 1) * 3 * 60_000),
      started_at: null,
      finished_at: null,
      stage: "queued",
      progress: 0,
    })
  })

  jobs.push({
    id: uid("job", "skipped-1"),
    document_id: DOCUMENTS[2].id,
    document_name: DOCUMENTS[2].file_name,
    job_type: "reindex_document",
    status: "skipped",
    attempts: 0,
    max_attempts: 3,
    error_message: "An identical ingestion for this document was already in flight.",
    created_at: agoIso(2 * HOUR),
    started_at: null,
    finished_at: agoIso(2 * HOUR),
    stage: null,
    progress: 0,
  })

  return jobs.sort((a, b) => b.created_at.localeCompare(a.created_at))
})()

/* --------------------------------------------------------- conversations */

const SAMPLE_EXCHANGES: { q: string; a: string; cites: number }[] = [
  {
    q: "What is the current base plate thickness on CG4-S-104?",
    a: `The current issue of **CG4-S-104** (Rev D, dated {revdate}) specifies a base plate of **500 × 500 × 32 mm** in grade **IS 2062 E250 BR** for type BP-31 [1].

The thickness was increased from 25 mm to 32 mm at Rev C following a revised uplift check; Rev C's note records the change explicitly [2]. Associated details on the current sheet:

- Holding-down bolts — **4 nos M24 grade 8.8**, 600 mm embedment, 90 mm projection above grout [1]
- Column — **ISHB 350**, welded all round with a **10 mm fillet weld** [1]
- Grout — 50 mm minimum / 75 mm maximum non-shrink cementitious, 60 MPa at 28 days [3]

Note that site query **SQ-088** raised a grout gap observation against this detail and is still shown as open in the project correspondence [4].`,
    cites: 4,
  },
  {
    q: "Which drawings reference ISMB 400, and are any of them superseded?",
    a: `**ISMB 400** appears on four drawings across two projects. Three are current; one has been superseded.

| Drawing | Project | Rev | Status |
|---|---|---|---|
| CG4-S-112 | 2024-0117 | B | Current [1] |
| CG4-S-140 | 2024-0117 | C | Current [2] |
| CPS-S-101 | 2024-0093 | E | Current [3] |
| CG4-S-140 | 2024-0117 | B | **Superseded by Rev C** [4] |

On CG4-S-140 the member is mark **BM-14**, 8 450 mm long, 12 off, with 15 mm camber [2]. Be aware that Rev C of that sheet carries the note *"Member mark BM-14 re-sectioned ISMB 400 → ISWB 600"* — so on the current issue BM-14 is **ISWB 600**, and the ISMB 400 reference survives only in the schedule row for the adjacent member [2].`,
    cites: 4,
  },
  {
    q: "What weld procedure applies to plate thicker than 25 mm?",
    a: `For plate thickness above 25 mm the general notes require **low-hydrogen E7018 electrodes with a 100 °C pre-heat**, welded to **IS 9595** by qualified welders [1].

The governing procedure is **WPS-014 (SMAW)**, which is on file for project 2024-0117 [2]. Welding of structural steelwork generally is to IS 800:2007 and material to IS 2062:2011 [1].`,
    cites: 2,
  },
]

export const CONVERSATIONS: ConversationSummary[] = [
  {
    id: uid("conv", "1"),
    title: "What is the current base plate thickness on CG4-S-104?",
    message_count: 4,
    created_at: agoIso(2 * HOUR),
    updated_at: agoIso(1.6 * HOUR),
  },
  {
    id: uid("conv", "2"),
    title: "Which drawings reference ISMB 400, and are any superseded?",
    message_count: 2,
    created_at: agoIso(28 * HOUR),
    updated_at: agoIso(28 * HOUR),
  },
  {
    id: uid("conv", "3"),
    title: "Weld procedure for plate over 25 mm",
    message_count: 2,
    created_at: agoIso(3 * DAY),
    updated_at: agoIso(3 * DAY),
  },
  {
    id: uid("conv", "4"),
    title: "Bolt torque records for Bay 2 — where are they?",
    message_count: 6,
    created_at: agoIso(6 * DAY),
    updated_at: agoIso(5.8 * DAY),
  },
  {
    id: uid("conv", "5"),
    title: "Summarise the Rev A→E changes on CPS-S-101",
    message_count: 2,
    created_at: agoIso(11 * DAY),
    updated_at: agoIso(11 * DAY),
  },
]

export function messagesFor(conversationId: string): ConversationMessage[] {
  const index = CONVERSATIONS.findIndex((c) => c.id === conversationId)
  if (index < 0) return []
  const exchange = SAMPLE_EXCHANGES[index % SAMPLE_EXCHANGES.length]
  const r = rng(`messages:${conversationId}`)
  const baseTime = new Date(CONVERSATIONS[index].created_at).getTime()
  const cited = DOCUMENTS.filter((d) => d.status === "indexed").slice(0, exchange.cites)
  const revdate = new Date(
    DOCUMENTS.find((d) => d.drawing_number === "CG4-S-104" && d.is_latest)?.revision_date ??
      agoIso(20 * DAY),
  ).toLocaleDateString("en-GB")

  return [
    {
      id: uid("message", `${conversationId}:u`),
      role: "user",
      content: exchange.q,
      citations: [],
      model_used: "",
      latency_ms: 0,
      trace_id: "",
      created_at: new Date(baseTime).toISOString(),
    },
    {
      id: uid("message", `${conversationId}:a`),
      role: "assistant",
      content: exchange.a.replace("{revdate}", revdate),
      citations: cited.map((doc, i) => citationFor(doc, i + 1, r)),
      model_used: "claude-sonnet-4-6",
      latency_ms: Math.round(1800 + r() * 3600),
      trace_id: uid("trace", `${conversationId}`).replace(/-/g, "").slice(0, 32),
      created_at: new Date(baseTime + 4200).toISOString(),
    },
  ]
}

export function citationFor(doc: DocumentSummary, index: number, r: () => number): {
  index: number
  document_id: string
  document_name: string
  chunk_id: string
  page_number: number
  section: string
  drawing_number: string | null
  revision_label: string | null
  content_kind: ContentKind | null
  snippet: string
  score: number
  regions: Region[]
} {
  const chunks = chunksFor(doc.id)
  const chunk = chunks[Math.floor(r() * Math.max(1, chunks.length))] ?? chunks[0]
  return {
    index,
    document_id: doc.id,
    document_name: doc.file_name,
    chunk_id: chunk?.id ?? uid("chunk", `${doc.id}:0`),
    page_number: chunk?.page_number ?? 1,
    section: chunk?.section_title ?? "Document",
    drawing_number: doc.drawing_number ?? null,
    revision_label: doc.revision_label ?? null,
    content_kind: doc.content_kind ?? null,
    snippet: (chunk?.content ?? "").slice(0, 260),
    score: Number((0.58 + r() * 0.4).toFixed(3)),
    regions: chunk?.regions ?? [],
  }
}

/** The canned answer the mock chat streams back, chosen by keyword. */
export function answerFor(query: string): { answer: string; cites: number } {
  const q = query.toLowerCase()
  if (q.includes("base plate") || q.includes("baseplate") || q.includes("s-104")) {
    return { answer: SAMPLE_EXCHANGES[0].a, cites: 4 }
  }
  if (q.includes("ismb") || q.includes("supersed") || q.includes("revision")) {
    return { answer: SAMPLE_EXCHANGES[1].a, cites: 4 }
  }
  if (q.includes("weld") || q.includes("wps") || q.includes("electrode")) {
    return { answer: SAMPLE_EXCHANGES[2].a, cites: 2 }
  }
  return {
    answer: `Based on the indexed corpus, here is what the drawings and specifications say about **${query.trim().replace(/\?+$/, "")}**.

The governing specification is **IS 800:2007** with material to **IS 2062:2011**; members shown as E350 C require a guaranteed Charpy V-notch value of 27 J at 0 °C [1]. Connection bolts default to **M20 grade 8.8** unless noted otherwise, and HSFG assemblies are tightened by the turn-of-nut method to IS 3757 [2].

Where a drawing and a specification disagree, the later-issued drawing revision governs — check the revision note before relying on a dimension read from a superseded sheet [3].`,
    cites: 3,
  }
}

/* --------------------------------------------------------------- audit */

const AUDIT_ACTIONS = [
  "document_uploaded",
  "document_reclassified",
  "document_deleted",
  "access_denied",
  "setting_changed",
  "kill_switch_toggled",
  "answer_refused",
  "eval_run_started",
  "feedback_submitted",
  "retention_purged",
  "policy_violation",
] as const

export const AUDIT: AuditEntry[] = Array.from({ length: 140 }, (_, i) => {
  const r = rng(`audit:${i}`)
  const action = i < 3 ? AUDIT_ACTIONS[3] : pick(AUDIT_ACTIONS, r)
  const actor = pick(USERS, r)
  const doc = pick(DOCUMENTS, r)
  const denied = action === "access_denied" || action === "policy_violation"
  const reasons: Record<string, string> = {
    access_denied: `document is '${doc.sensitivity}', principal holds '${actor.clearance}'`,
    document_reclassified: "periodic classification review — client NDA scope",
    document_deleted: "duplicate upload of the same revision",
    setting_changed: "raised rerank_top_n after recall regression",
    kill_switch_toggled: "provider incident — paused answering",
    answer_refused: "no chunk cleared the grounding threshold",
    policy_violation: "faithfulness below the 0.75 policy floor",
    retention_purged: "retention deadline reached",
    eval_run_started: "pre-release regression run",
    feedback_submitted: "",
    document_uploaded: "",
  }
  return {
    id: uid("audit", String(i)),
    trace_id: uid("trace", String(i)).replace(/-/g, "").slice(0, 32),
    actor_id: actor.user_id,
    actor_role: actor.role,
    actor_email: actor.email,
    action,
    resource_type: action.startsWith("document") || denied ? "document" : action === "setting_changed" ? "system_setting" : "system",
    resource_id: action.startsWith("document") || denied ? doc.id : action === "setting_changed" ? "rerank_top_n" : null,
    resource_label: action.startsWith("document") || denied ? doc.file_name : null,
    outcome: denied ? "denied" : "completed",
    reason: reasons[action] || null,
    control_id: denied ? "C-MAP-01" : action === "document_reclassified" ? "C-MAP-02" : null,
    created_at: agoIso(Math.round(r() * 168) * HOUR + Math.round(r() * 3600) * 1000),
  }
}).sort((a, b) => b.created_at.localeCompare(a.created_at))

/* ---------------------------------------------------------------- risks */

export const RISKS: Risk[] = [
  {
    id: "R-G01", title: "Ungoverned model or provider substitution", function: "govern",
    severity: "high", likelihood: "medium", metric: "rag_policy_info", threshold: 1,
    threshold_direction: "gte", owner: "platform-team", residual_risk: "low",
    controls: [{ id: "C-GOV-01", description: "Approved-provider allow-list enforced in LLMGateway", implemented_in: "src/llm/gateway.py" }],
  },
  {
    id: "R-G03", title: "Unauthenticated access to client drawings", function: "govern",
    severity: "critical", likelihood: "low", metric: "rag_unauthenticated_requests_total", threshold: 0,
    threshold_direction: "lte", owner: "platform-team", residual_risk: "medium",
    controls: [
      { id: "C-GOV-02", description: "Firebase ID token verification on every request", implemented_in: "src/auth/firebase.py" },
      { id: "C-GOV-03", description: "Development X-User-Id header refused when FIREBASE_ENABLED", implemented_in: "src/governance/rbac.py" },
    ],
  },
  {
    id: "R-M01", title: "Retrieval returns content above the caller's clearance", function: "map",
    severity: "critical", likelihood: "low", metric: "rag_sensitivity_guard_drops_total", threshold: 0,
    threshold_direction: "lte", owner: "platform-team", residual_risk: "low",
    controls: [
      { id: "C-MAP-01", description: "Clearance pushed into Qdrant and Elasticsearch as a pre-filter", implemented_in: "src/domain/value_objects/metadata_filter.py" },
      { id: "C-MAP-03", description: "Post-retrieval sensitivity_guard re-check; any drop is a defect", implemented_in: "src/retrieval/guards.py" },
    ],
  },
  {
    id: "R-M02", title: "Answer quotes a superseded drawing revision", function: "map",
    severity: "high", likelihood: "medium", metric: "rag_superseded_citations_total", threshold: 0.02,
    threshold_direction: "lte", owner: "domain-team", residual_risk: "medium",
    controls: [
      { id: "C-MAP-04", description: "Search defaults to is_latest = true", implemented_in: "src/infrastructure/search/elasticsearch/repository.py" },
      { id: "C-MAP-05", description: "Revision registration invalidates the cache for the whole family", implemented_in: "src/retrieval/semantic_cache.py" },
    ],
  },
  {
    id: "R-S01", title: "Answer unfaithful to retrieved context", function: "measure",
    severity: "high", likelihood: "medium", metric: "rag_faithfulness_rolling", threshold: 0.75,
    threshold_direction: "gte", owner: "quality-team", residual_risk: "medium",
    controls: [
      { id: "C-MEA-01", description: "Sampled online LLM-judge scoring on live traffic", implemented_in: "src/evaluation/online/evaluator.py" },
      { id: "C-MEA-02", description: "CI quality gate against the golden dataset", implemented_in: "src/evaluation/offline/runner.py" },
    ],
  },
  {
    id: "R-S02", title: "OCR misreads a dimension on a scanned sheet", function: "measure",
    severity: "high", likelihood: "high", metric: "rag_ocr_confidence_avg", threshold: 0.15,
    threshold_direction: "gte", owner: "domain-team", residual_risk: "high",
    controls: [
      { id: "C-MEA-03", description: "Per-chunk OCR floor, separate for drawings and prose", implemented_in: "src/ingestion/chunkers/validator.py" },
      { id: "C-MEA-04", description: "content_kind.has_exact_dimensions is true only for cad_native", implemented_in: "src/ingestion/parsing/drawing_detector.py" },
    ],
  },
  {
    id: "R-N01", title: "Runaway inference spend", function: "manage",
    severity: "medium", likelihood: "medium", metric: "rag_llm_cost_usd_total", threshold: 250,
    threshold_direction: "lte", owner: "platform-team", residual_risk: "low",
    controls: [
      { id: "C-MAN-01", description: "Per-role rate limits and a semantic answer cache", implemented_in: "src/api/dependencies_rate_limit.py" },
      { id: "C-MAN-02", description: "answering_enabled kill switch", implemented_in: "src/governance/runtime_flags.py" },
    ],
  },
  {
    id: "R-N02", title: "Data retained past its contractual deadline", function: "manage",
    severity: "medium", likelihood: "low", metric: "rag_documents_past_retention", threshold: 0,
    threshold_direction: "lte", owner: "governance-team", residual_risk: "low",
    controls: [{ id: "C-MAN-03", description: "Retention purge sweeps every store, dry-run by default", implemented_in: "src/governance/retention.py" }],
  },
]

/* ----------------------------------------------------------- evaluation */

export const EVAL_RUNS: EvaluationRun[] = [
  {
    run_id: uid("run", "1"), name: "pre-release regression", dataset_name: "golden_set_v1",
    status: "completed", total_items: 48, completed_items: 48,
    started_at: agoIso(5 * HOUR), completed_at: agoIso(4.6 * HOUR), error_message: null,
    metrics: [
      { name: "faithfulness", value: 0.871, sample_size: 48 },
      { name: "answer_relevancy", value: 0.844, sample_size: 48 },
      { name: "context_relevancy", value: 0.732, sample_size: 48 },
      { name: "citation_validity", value: 0.958, sample_size: 48 },
    ],
  },
  {
    run_id: uid("run", "2"), name: "drawing-heavy subset", dataset_name: "steel_drawings_v2",
    status: "running", total_items: 32, completed_items: 19,
    started_at: agoIso(11 * 60_000), completed_at: null, error_message: null,
    metrics: [],
  },
  {
    run_id: uid("run", "3"), name: "post-reranker change", dataset_name: "golden_set_v1",
    status: "completed", total_items: 48, completed_items: 48,
    started_at: agoIso(2 * DAY), completed_at: agoIso(2 * DAY - 22 * 60_000), error_message: null,
    metrics: [
      { name: "faithfulness", value: 0.812, sample_size: 48 },
      { name: "answer_relevancy", value: 0.836, sample_size: 48 },
      { name: "context_relevancy", value: 0.694, sample_size: 48 },
      { name: "citation_validity", value: 0.937, sample_size: 48 },
    ],
  },
  {
    run_id: uid("run", "4"), name: "regression from feedback", dataset_name: "regression_from_feedback",
    status: "failed", total_items: 14, completed_items: 6,
    started_at: agoIso(4 * DAY), completed_at: agoIso(4 * DAY - 3 * 60_000),
    error_message: "Judge provider returned 429 for 8 consecutive samples; run abandoned rather than scored on a partial set.",
    metrics: [],
  },
  {
    run_id: uid("run", "5"), name: "nightly", dataset_name: "golden_set_v1",
    status: "completed", total_items: 48, completed_items: 48,
    started_at: agoIso(7 * DAY), completed_at: agoIso(7 * DAY - 25 * 60_000), error_message: null,
    metrics: [
      { name: "faithfulness", value: 0.783, sample_size: 48 },
      { name: "answer_relevancy", value: 0.801, sample_size: 48 },
      { name: "context_relevancy", value: 0.658, sample_size: 48 },
      { name: "citation_validity", value: 0.912, sample_size: 48 },
    ],
  },
]

/* ------------------------------------------------------------- feedback */

export const FEEDBACK: FeedbackEntry[] = [
  {
    id: uid("fb", "1"), rating: 2, comment: "Quoted Rev B of CG4-S-140 — that sheet was superseded three weeks ago. The answer should have used Rev C.",
    tags: ["outdated", "wrong_source"], trace_id: uid("trace", "f1").replace(/-/g, "").slice(0, 32),
    message_id: uid("message", "f1"), question: "What section is member BM-14?",
    created_at: agoIso(5 * HOUR), user_email: USERS[2].email,
  },
  {
    id: uid("fb", "2"), rating: 5, comment: "Correct thickness and it flagged the open site query. Saved a phone call.",
    tags: ["helpful"], trace_id: uid("trace", "f2").replace(/-/g, "").slice(0, 32),
    message_id: uid("message", "f2"), question: "Current base plate thickness on CG4-S-104?",
    created_at: agoIso(9 * HOUR), user_email: USERS[4].email,
  },
  {
    id: uid("fb", "3"), rating: 1, comment: "Gave a dimension read off a scan as if it were exact. It's a scanned_drawing — the answer should say so.",
    tags: ["hallucinated", "wrong_source"], trace_id: uid("trace", "f3").replace(/-/g, "").slice(0, 32),
    message_id: uid("message", "f3"), question: "What is the boom node gusset thickness on MSL-S-418?",
    created_at: agoIso(31 * HOUR), user_email: USERS[1].email,
  },
  {
    id: uid("fb", "4"), rating: 3, comment: "Right answer but it took nearly nine seconds.",
    tags: ["too_slow"], trace_id: uid("trace", "f4").replace(/-/g, "").slice(0, 32),
    message_id: uid("message", "f4"), question: "List every drawing using IS 2062 E350 C.",
    created_at: agoIso(2 * DAY), user_email: USERS[2].email,
  },
  {
    id: uid("fb", "5"), rating: 2, comment: "Only found two of the four bolt torque records. The scanned one wasn't retrieved.",
    tags: ["incomplete"], trace_id: uid("trace", "f5").replace(/-/g, "").slice(0, 32),
    message_id: uid("message", "f5"), question: "Where are the Bay 2 bolt torque records?",
    created_at: agoIso(3 * DAY), user_email: USERS[4].email,
  },
  {
    id: uid("fb", "6"), rating: 4, comment: null, tags: ["helpful"],
    trace_id: uid("trace", "f6").replace(/-/g, "").slice(0, 32),
    message_id: uid("message", "f6"), question: "Summarise WPS-014.",
    created_at: agoIso(4 * DAY), user_email: USERS[0].email,
  },
]

/* --------------------------------------------------------------- metrics */

export const METRICS: SystemMetrics = (() => {
  const r = rng("metrics")
  const indexed = DOCUMENTS.filter((d) => d.status === "indexed").length
  return {
    queue_depth: JOBS.filter((j) => j.status === "queued").length,
    jobs_running: JOBS.filter((j) => j.status === "running").length,
    jobs_failed_24h: JOBS.filter((j) => j.status === "failed").length,
    documents_total: DOCUMENTS.length,
    documents_indexed: indexed,
    chunks_total: DOCUMENTS.reduce((sum, d) => sum + (d.chunk_count ?? 0), 0),
    queries_24h: 418,
    p95_latency_ms: 4820,
    cache_hit_rate: 0.312,
    refusal_rate: 0.041,
    access_denied_24h: AUDIT.filter(
      (a) => a.action === "access_denied" && Date.now() - new Date(a.created_at).getTime() < DAY,
    ).length,
    cost_24h_usd: 18.44,
    ingest_series: Array.from({ length: 14 }, (_, i) => ({
      t: agoIso((13 - i) * DAY),
      ingested: Math.round(2 + r() * 14),
      failed: r() < 0.28 ? Math.round(r() * 2) + 1 : 0,
    })),
    query_series: Array.from({ length: 24 }, (_, i) => ({
      t: agoIso((23 - i) * HOUR),
      queries: Math.round(4 + r() * 38),
      p95_ms: Math.round(2600 + r() * 4200),
    })),
  }
})()
