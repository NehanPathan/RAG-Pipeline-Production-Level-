/**
 * The sample-corpus implementation of ApiSurface.
 *
 * It behaves like the real service rather than like a fixture: it applies the
 * caller's clearance and project reach the way `_load_readable_document` and
 * `_access_filter` do, returns 404 (not 403) for unreadable documents, streams
 * chat token by token, and lets ingestion actually progress through its stages
 * over time. States the UI has to handle — a failed OCR job, a document above
 * your clearance, a breached quality floor — are present in the data because
 * they are the states worth designing for.
 */
import { ApiError } from "@/api/client"
import type { ApiSurface } from "@/api/surface"
import type * as T from "@/api/types"
import {
  AUDIT,
  CONVERSATIONS,
  DOCUMENTS,
  DOCUMENT_BY_ID,
  DRAWINGS,
  DRAWING_BY_ID,
  ENTITY_CANONICALS,
  EVAL_RUNS,
  FEEDBACK,
  JOBS,
  METRICS,
  PROJECTS,
  PROJECT_BY_ID,
  PROJECT_MEMBERS,
  RISKS,
  USERS,
  agoIso,
  answerFor,
  chunksFor,
  citationFor,
  intelligenceFor,
  messagesFor,
  uid,
} from "@/api/mock/corpus"

const SENSITIVITY_LEVEL: Record<T.Sensitivity, number> = {
  public: 0,
  internal: 1,
  confidential: 2,
  restricted: 3,
}

/** Who the mock believes is calling. Set by the auth provider on sign-in. */
let currentPrincipal: T.Principal = USERS[0]
export function setMockPrincipal(principal: T.Principal) {
  currentPrincipal = principal
}
export function getMockPrincipal() {
  return currentPrincipal
}

function readable(doc: T.DocumentSummary): boolean {
  const cleared =
    SENSITIVITY_LEVEL[doc.sensitivity] <= SENSITIVITY_LEVEL[currentPrincipal.clearance]
  if (!cleared) return false
  // Access scope: own document, or a project you are a member of. A document
  // with no project is personal and visible only to its owner.
  if (!doc.project_id) return doc.uploaded_by === currentPrincipal.user_id
  const member = PROJECT_MEMBERS.some(
    (m) => m.project_id === doc.project_id && m.user_id === currentPrincipal.user_id,
  )
  return member || doc.uploaded_by === currentPrincipal.user_id
}

function visibleDocuments(): T.DocumentSummary[] {
  return DOCUMENTS.filter(readable)
}

function visibleProjects(): T.Project[] {
  const ids = new Set(
    PROJECT_MEMBERS.filter((m) => m.user_id === currentPrincipal.user_id).map((m) => m.project_id),
  )
  return PROJECTS.filter((p) => ids.has(p.id))
}

/** Latency, so loading states are exercised rather than theoretical. */
function delay<V>(value: V, ms = 180 + Math.random() * 260): Promise<V> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms))
}

function notFound(what = "Document not found"): never {
  throw new ApiError(404, what)
}

/* --------------------------------------------- mutable in-session state */

const flags: T.RuntimeFlag[] = [
  { name: "answering_enabled", enabled: true, source: "default", description: "Master kill switch. When off, /chat refuses every question. The most consequential control in the system." },
  { name: "ingestion_enabled", enabled: true, source: "default", description: "Accept new uploads. Turning this off returns 503 from POST /documents and leaves the queue to drain." },
  { name: "reranking_enabled", enabled: true, source: "override", description: "Cross-encoder rerank of fused candidates. Disable to shed latency during a provider incident." },
  { name: "semantic_cache_enabled", enabled: true, source: "default", description: "Read-before-write answer cache. Disable when a corpus change must be visible immediately." },
  { name: "llm_fallback_enabled", enabled: true, source: "default", description: "Fail over to the next approved provider. A stream never fails over once a token has reached the client." },
  { name: "online_scoring_enabled", enabled: true, source: "override", description: "Sampled LLM-judge scoring of live answers. Costs one judge call per sampled trace." },
  { name: "pii_redaction_enabled", enabled: false, source: "default", description: "Redact detected PII from context before it reaches the model. Off for this corpus: drawings carry names in title blocks that answers legitimately need." },
]

const settings: T.SettingItem[] = [
  { key: "small_llm", value: { provider: "anthropic", model: "claude-haiku-4-5-20251001" }, is_override: false },
  { key: "large_llm", value: { provider: "anthropic", model: "claude-sonnet-4-6" }, is_override: false },
  { key: "embedding_model", value: { provider: "openai", model: "text-embedding-3-large" }, is_override: false },
  { key: "reranker", value: { provider: "bge", model: "BAAI/bge-reranker-large" }, is_override: false },
  { key: "vector_top_k", value: 20, is_override: false },
  { key: "bm25_top_k", value: 20, is_override: false },
  { key: "rerank_top_n", value: 12, is_override: true },
  { key: "chunk_parent_size", value: 1024, is_override: false },
  { key: "chunk_child_size", value: 256, is_override: false },
  { key: "chunk_overlap", value: 32, is_override: false },
]

const sessionJobs = [...JOBS]
const sessionDocuments = DOCUMENTS

/** Running ingestions advance while the page is open, so the queue is live. */
const STAGE_ORDER: NonNullable<T.Job["stage"]>[] = [
  "queued", "load", "classify", "ocr", "layout", "extract", "chunk", "embed", "index", "done",
]

setInterval(() => {
  for (const job of sessionJobs) {
    if (job.status !== "running") continue
    job.progress = Math.min(100, (job.progress ?? 0) + 3 + Math.random() * 6)
    const stageIndex = Math.min(
      STAGE_ORDER.length - 2,
      Math.floor((job.progress / 100) * (STAGE_ORDER.length - 1)),
    )
    job.stage = STAGE_ORDER[Math.max(1, stageIndex)]
    if (job.progress >= 100) {
      job.status = "succeeded"
      job.stage = "done"
      job.finished_at = new Date().toISOString()
      const doc = DOCUMENT_BY_ID.get(job.document_id ?? "")
      if (doc) {
        doc.status = "indexed"
        doc.indexed_at = job.finished_at
        doc.chunk_count = doc.chunk_count || 24
        doc.page_count = doc.page_count ?? 3
      }
      // Promote a queued job so the pipeline keeps moving.
      const next = sessionJobs.find((j) => j.status === "queued")
      if (next) {
        next.status = "running"
        next.started_at = new Date().toISOString()
        next.attempts = 1
        next.progress = 2
        next.stage = "load"
      }
    }
  }
}, 1500)

/* ------------------------------------------------------------ the surface */

export const mockApi: ApiSurface = {
  async whoAmI() {
    return delay({
      policy: {
        version: "2025.08-r3",
        enforcement_mode: "enforce",
        default_sensitivity: "internal" as T.Sensitivity,
        default_clearance: "public" as T.Sensitivity,
        retention_days: 2555,
        min_faithfulness: 0.75,
        min_answer_relevancy: 0.7,
        min_context_relevancy: 0.6,
        role_clearance: {
          viewer: "public",
          analyst: "internal",
          steward: "confidential",
          admin: "restricted",
        },
        approved_providers: ["anthropic", "openai", "azure-openai"],
      } as T.GovernancePolicy,
      principal: currentPrincipal,
    })
  },

  async listUsers() {
    return delay(USERS)
  },

  async listDocuments(params) {
    const {
      page = 1,
      size = 20,
      search,
      status,
      sensitivity,
      project_id,
      content_kind,
      file_type,
      latest_only,
    } = params

    let items = visibleDocuments()
    if (search) {
      const q = search.toLowerCase()
      items = items.filter(
        (d) =>
          d.file_name.toLowerCase().includes(q) ||
          (d.drawing_number ?? "").toLowerCase().includes(q) ||
          (d.project_number ?? "").toLowerCase().includes(q) ||
          d.tags.some((t) => t.toLowerCase().includes(q)),
      )
    }
    if (status?.length) items = items.filter((d) => status.includes(d.status))
    if (sensitivity?.length) items = items.filter((d) => sensitivity.includes(d.sensitivity))
    if (project_id) items = items.filter((d) => d.project_id === project_id)
    if (content_kind?.length) {
      items = items.filter((d) => d.content_kind && content_kind.includes(d.content_kind))
    }
    if (file_type?.length) items = items.filter((d) => file_type.includes(d.file_type))
    if (latest_only) items = items.filter((d) => d.is_latest !== false)

    items = [...items].sort((a, b) => b.created_at.localeCompare(a.created_at))
    const total = items.length
    const start = (page - 1) * size
    return delay({ items: items.slice(start, start + size), total, page, size })
  },

  async getDocument(id) {
    const doc = DOCUMENT_BY_ID.get(id)
    if (!doc || !readable(doc)) notFound()
    return delay(doc)
  },

  async getChunks(id) {
    const doc = DOCUMENT_BY_ID.get(id)
    if (!doc || !readable(doc)) notFound()
    const items = chunksFor(id)
    return delay({ items, total: items.length })
  },

  async getIntelligence(id) {
    const doc = DOCUMENT_BY_ID.get(id)
    if (!doc || !readable(doc)) notFound()
    const intel = intelligenceFor(id)
    if (!intel) {
      throw new ApiError(
        404,
        "No Document Intelligence data for this document (not yet indexed, or indexed before Phase 4A).",
      )
    }
    return delay(intel)
  },

  async getDocumentJobs(id) {
    const doc = DOCUMENT_BY_ID.get(id)
    if (!doc || !readable(doc)) notFound()
    return delay(sessionJobs.filter((j) => j.document_id === id))
  },

  async uploadDocument({ file, domain, tags, sensitivity, project_id, drawing_number, revision_label, onProgress }) {
    if (!flags.find((f) => f.name === "ingestion_enabled")?.enabled) {
      throw new ApiError(503, "Ingestion is temporarily disabled by an administrator.")
    }
    const ext = file.name.split(".").pop()?.toLowerCase() ?? ""
    const allowed = ["pdf", "docx", "doc", "txt", "md", "png", "jpg", "jpeg", "tiff", "dxf", "dwg", "xlsx", "csv"]
    if (!allowed.includes(ext)) {
      throw new ApiError(
        422,
        `File type '${ext}' is not supported. Allowed: ${allowed.join(", ")}`,
      )
    }
    if (file.size > 200 * 1024 * 1024) {
      throw new ApiError(422, "File size exceeds maximum of 200MB")
    }
    const chosen = (sensitivity || "internal") as T.Sensitivity
    if (SENSITIVITY_LEVEL[chosen] > SENSITIVITY_LEVEL[currentPrincipal.clearance]) {
      throw new ApiError(
        403,
        `Cannot classify a document as '${chosen}' with clearance '${currentPrincipal.clearance}'.`,
      )
    }

    // Simulate the transfer so the progress bar means something.
    for (let step = 1; step <= 10; step++) {
      await new Promise((r) => setTimeout(r, 70))
      onProgress?.(step / 10)
    }

    const documentId = uid("document", `${file.name}:${Date.now()}`)
    const project = project_id ? PROJECT_BY_ID.get(project_id) : undefined
    const doc: T.DocumentSummary = {
      id: documentId,
      file_name: file.name,
      file_type: ext,
      status: "pending",
      page_count: null,
      word_count: null,
      domain: domain || "general",
      tags: tags ? tags.split(",").map((t) => t.trim()).filter(Boolean) : [],
      indexed_at: null,
      created_at: new Date().toISOString(),
      sensitivity: chosen,
      retention_until: new Date(Date.now() + 2555 * 86400_000).toISOString(),
      file_size_bytes: file.size,
      project_id: project?.id ?? null,
      project_number: project?.project_number ?? null,
      drawing_id: drawing_number ? uid("drawing", drawing_number) : null,
      drawing_number: drawing_number || null,
      revision_label: revision_label || null,
      revision_index: revision_label ? 99 : null,
      revision_date: revision_label ? new Date().toISOString() : null,
      revision_note: null,
      is_latest: true,
      content_kind: ext === "dxf" || ext === "dwg" ? "cad_native" : null,
      uploaded_by: currentPrincipal.user_id,
      chunk_count: 0,
    }
    sessionDocuments.unshift(doc)
    DOCUMENT_BY_ID.set(documentId, doc)

    const job: T.Job = {
      id: uid("job", `${documentId}:ingest`),
      document_id: documentId,
      document_name: file.name,
      job_type: "ingest_document",
      status: "running",
      attempts: 1,
      max_attempts: 3,
      error_message: null,
      created_at: new Date().toISOString(),
      started_at: new Date().toISOString(),
      finished_at: null,
      stage: "load",
      progress: 4,
    }
    sessionJobs.unshift(job)
    doc.status = "processing"

    return {
      document_id: documentId,
      file_name: file.name,
      status: "processing",
      message: "Document accepted for ingestion. Poll /documents/{id} for status.",
    }
  },

  async reclassifyDocument(id, sensitivity, reason) {
    const doc = DOCUMENT_BY_ID.get(id)
    if (!doc || !readable(doc)) notFound()
    if (!["steward", "admin"].includes(currentPrincipal.role)) {
      throw new ApiError(403, "Reclassification requires the steward or admin role.")
    }
    if (SENSITIVITY_LEVEL[sensitivity] > SENSITIVITY_LEVEL[currentPrincipal.clearance]) {
      throw new ApiError(
        403,
        `Cannot set classification '${sensitivity}' with clearance '${currentPrincipal.clearance}'.`,
      )
    }
    const before = doc.sensitivity
    doc.sensitivity = sensitivity
    AUDIT.unshift({
      id: uid("audit", `reclass:${Date.now()}`),
      trace_id: uid("trace", String(Date.now())).replace(/-/g, "").slice(0, 32),
      actor_id: currentPrincipal.user_id,
      actor_role: currentPrincipal.role,
      actor_email: currentPrincipal.email,
      action: "document_reclassified",
      resource_type: "document",
      resource_id: id,
      resource_label: doc.file_name,
      outcome: "completed",
      reason: reason || `reclassified ${before} → ${sensitivity}`,
      control_id: "C-MAP-02",
      created_at: new Date().toISOString(),
    })
    return delay(doc, 400)
  },

  async releaseDocument(id, reason) {
    const doc = DOCUMENTS.find((d) => d.id === id)
    if (!doc) throw new ApiError(404, "Document not found")
    if (doc.status !== "quarantined") {
      throw new ApiError(409, `Document is ${doc.status}, not quarantined.`)
    }
    doc.status = "pending"
    return delay({
      document_id: id,
      job_id: crypto.randomUUID(),
      status: "queued",
      message: `Released${reason ? `: ${reason}` : ""}. It is screened again on re-ingestion.`,
    })
  },

  async reprocessDocument(id) {
    const doc = DOCUMENT_BY_ID.get(id)
    if (!doc || !readable(doc)) notFound()
    const active = sessionJobs.find(
      (j) => j.document_id === id && (j.status === "running" || j.status === "queued"),
    )
    if (active) {
      return {
        document_id: id,
        job_id: "",
        status: "already_running",
        message: "Ingestion for this document is already in flight.",
      }
    }
    const job: T.Job = {
      id: uid("job", `${id}:reindex:${Date.now()}`),
      document_id: id,
      document_name: doc.file_name,
      job_type: "reindex_document",
      status: "running",
      attempts: 1,
      max_attempts: 3,
      error_message: null,
      created_at: new Date().toISOString(),
      started_at: new Date().toISOString(),
      finished_at: null,
      stage: "load",
      progress: 3,
    }
    sessionJobs.unshift(job)
    doc.status = "processing"
    return { document_id: id, job_id: job.id, status: "queued", message: "Reprocessing queued." }
  },

  async deleteDocument(id) {
    const doc = DOCUMENT_BY_ID.get(id)
    if (!doc || !readable(doc)) notFound()
    if (!["steward", "admin"].includes(currentPrincipal.role)) {
      throw new ApiError(403, "Deletion requires the steward or admin role.")
    }
    const deletedChunks = doc.chunk_count ?? 0
    const index = sessionDocuments.findIndex((d) => d.id === id)
    if (index >= 0) sessionDocuments.splice(index, 1)
    DOCUMENT_BY_ID.delete(id)
    return delay({ document_id: id, deleted_chunks: deletedChunks, message: "Document deleted." }, 400)
  },

  originalUrl(id) {
    return `/api/v1/documents/${id}/original`
  },

  async listProjects() {
    return delay(visibleProjects())
  },

  async getProject(id) {
    const project = visibleProjects().find((p) => p.id === id)
    if (!project) notFound("Project not found")
    return delay(project)
  },

  async listProjectMembers(id) {
    return delay(PROJECT_MEMBERS.filter((m) => m.project_id === id))
  },

  async listDrawings({ project_id, search } = {}) {
    const reachable = new Set(visibleProjects().map((p) => p.id))
    let items = DRAWINGS.filter((d) => d.project_id && reachable.has(d.project_id))
    if (project_id) items = items.filter((d) => d.project_id === project_id)
    if (search) {
      const q = search.toLowerCase()
      items = items.filter(
        (d) =>
          d.drawing_number.toLowerCase().includes(q) ||
          (d.title ?? "").toLowerCase().includes(q) ||
          (d.discipline ?? "").toLowerCase().includes(q),
      )
    }
    return delay(items)
  },

  async getDrawing(id) {
    const drawing = DRAWING_BY_ID.get(id)
    if (!drawing) notFound("Drawing not found")
    return delay(drawing)
  },

  async listRevisions(drawingId) {
    const revisions = DOCUMENTS.filter((d) => d.drawing_id === drawingId && readable(d))
      .sort((a, b) => (b.revision_index ?? 0) - (a.revision_index ?? 0))
      .map<T.Revision>((d) => ({
        document_id: d.id,
        drawing_id: drawingId,
        revision_label: d.revision_label ?? "—",
        revision_index: d.revision_index ?? 0,
        revision_date: d.revision_date ?? null,
        revision_note: d.revision_note ?? null,
        is_latest: d.is_latest ?? false,
        superseded_at: d.is_latest ? null : (d.revision_date ?? null),
        superseded_by_document_id: d.superseded_by_document_id ?? null,
        file_name: d.file_name,
        status: d.status,
        page_count: d.page_count,
        uploaded_by: d.uploaded_by ?? "",
        created_at: d.created_at,
      }))
    return delay(revisions)
  },

  async getFacets({ include_superseded } = {}) {
    const docs = visibleDocuments().filter((d) => include_superseded || d.is_latest !== false)
    const count = (values: (string | null | undefined)[]) => {
      const map = new Map<string, number>()
      for (const value of values) {
        if (!value) continue
        map.set(value, (map.get(value) ?? 0) + 1)
      }
      return [...map.entries()]
        .map(([value, n]) => ({ value, count: n }))
        .sort((a, b) => b.count - a.count || a.value.localeCompare(b.value))
    }
    return delay({
      facets: {
        project_number: count(docs.map((d) => d.project_number)),
        drawing_number: count(docs.map((d) => d.drawing_number)),
        revision_label: count(docs.map((d) => d.revision_label)),
        domain: count(docs.map((d) => d.domain)),
        file_type: count(docs.map((d) => d.file_type)),
        tags: count(docs.flatMap((d) => d.tags)),
        entity_canonicals: count(
          docs.flatMap((d) => d.tags.filter((t) => ENTITY_CANONICALS.includes(t))),
        ),
        sensitivity: count(docs.map((d) => d.sensitivity)),
        content_kind: count(docs.map((d) => d.content_kind)),
      },
    })
  },

  async search(body) {
    const {
      query = "",
      project_ids = [],
      drawing_numbers = [],
      entity_canonicals = [],
      tags = [],
      file_type,
      domain,
      content_kinds = [],
      include_superseded = false,
      top_k = 20,
    } = body

    let docs = visibleDocuments()
    if (!include_superseded) docs = docs.filter((d) => d.is_latest !== false)
    if (project_ids.length) docs = docs.filter((d) => d.project_id && project_ids.includes(d.project_id))
    if (drawing_numbers.length) docs = docs.filter((d) => d.drawing_number && drawing_numbers.includes(d.drawing_number))
    if (tags.length) docs = docs.filter((d) => tags.some((t) => d.tags.includes(t)))
    if (entity_canonicals.length) docs = docs.filter((d) => entity_canonicals.some((e) => d.tags.includes(e)))
    if (file_type) docs = docs.filter((d) => d.file_type === file_type)
    if (domain) docs = docs.filter((d) => d.domain === domain)
    if (content_kinds.length) docs = docs.filter((d) => d.content_kind && content_kinds.includes(d.content_kind))

    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean)
    const hits: T.SearchHit[] = []

    for (const doc of docs) {
      for (const chunk of chunksFor(doc.id)) {
        const haystack = `${chunk.content} ${chunk.section_title ?? ""} ${doc.file_name}`.toLowerCase()
        // An empty query is a legitimate filter-only browse (the drawing
        // register's primary use), so it matches everything.
        const matched = terms.length === 0 || terms.every((t) => haystack.includes(t))
        if (!matched) continue
        const score = terms.length
          ? terms.reduce((sum, t) => sum + (haystack.split(t).length - 1), 0) * 1.7 +
            (chunk.section_title?.toLowerCase().includes(terms[0]) ? 3 : 0)
          : 1
        hits.push({
          chunk_id: chunk.id,
          document_id: doc.id,
          document_name: doc.file_name,
          page_number: chunk.page_number,
          section: chunk.section_title,
          drawing_number: doc.drawing_number ?? null,
          revision_label: doc.revision_label ?? null,
          project_number: doc.project_number ?? null,
          entity_canonicals: (chunk.entities ?? []).map((e) => e.canonical),
          content_kind: chunk.content_kind ?? null,
          content: chunk.content,
          score: Number(score.toFixed(3)),
        })
      }
    }

    hits.sort((a, b) => b.score - a.score)
    return delay({ items: hits.slice(0, top_k), total: hits.length }, 260)
  },

  async *streamChat(body, signal) {
    if (!flags.find((f) => f.name === "answering_enabled")?.enabled) {
      yield {
        type: "done",
        answer:
          "Answering is currently disabled by an administrator. Your question was not sent to a model.",
        citations: [],
        refused: true,
        refusal_reason: "kill_switch",
        latency_ms: 12,
        trace_id: uid("trace", String(Date.now())).replace(/-/g, "").slice(0, 32),
        conversation_id: body.conversation_id ?? uid("conv", String(Date.now())),
        message_id: uid("message", String(Date.now())),
        route: "refusal",
        grounded: false,
        cached: false,
      }
      return
    }

    const started = Date.now()
    const stages: [string, string][] = [
      ["query_analysis", "Rewriting and expanding the question"],
      ["retrieval", "Hybrid search — Qdrant vectors + Elasticsearch BM25"],
      ["fusion", "Reciprocal rank fusion"],
      ["rerank", "Cross-encoder rerank (BAAI/bge-reranker-large)"],
      ["generate", "Composing a grounded answer"],
    ]
    for (const [stage, message] of stages) {
      if (signal?.aborted) return
      await new Promise((r) => setTimeout(r, 240 + Math.random() * 320))
      yield { type: "status", stage, message }
    }

    const { answer, cites } = answerFor(body.query)
    const pool = visibleDocuments().filter((d) => d.status === "indexed")
    const rand = () => Math.random()
    const citations = pool.slice(0, cites).map((doc, i) => citationFor(doc, i + 1, rand))

    yield { type: "citations", citations }

    // Stream in word groups: character-by-character reads as a gimmick, a
    // whole paragraph at a time defeats the point of streaming.
    const tokens = answer.match(/\S+\s*/g) ?? []
    for (let i = 0; i < tokens.length; i += 2) {
      if (signal?.aborted) return
      await new Promise((r) => setTimeout(r, 16 + Math.random() * 26))
      yield { type: "token", content: tokens.slice(i, i + 2).join("") }
    }

    const conversationId = body.conversation_id ?? uid("conv", String(Date.now()))
    yield {
      type: "done",
      answer,
      citations,
      conversation_id: conversationId,
      message_id: uid("message", `${conversationId}:${Date.now()}`),
      latency_ms: Date.now() - started,
      trace_id: uid("trace", String(started)).replace(/-/g, "").slice(0, 32),
      refused: false,
      route: "rag",
      route_source: "query_agent",
      grounded: true,
      cached: false,
      model_used: "claude-sonnet-4-6",
    }
  },

  async listConversations(limit = 50) {
    return delay(CONVERSATIONS.slice(0, limit))
  },

  async getConversation(id) {
    const messages = messagesFor(id)
    if (!messages.length) notFound("Conversation not found")
    return delay(messages)
  },

  async deleteConversation(id) {
    const index = CONVERSATIONS.findIndex((c) => c.id === id)
    // 404 rather than 403 when it is not the caller's, matching the route.
    if (index < 0) notFound("Conversation not found")
    // Mirrors the server's soft delete: the entry leaves the history, and
    // `messagesFor` still resolves it, standing in for the message rows that
    // the feedback and evaluation samples continue to reference.
    CONVERSATIONS.splice(index, 1)
    return delay(
      {
        conversation_id: id,
        archived: true,
        message:
          "Removed from your history. The messages are retained so the ratings and evaluation samples attached to them stay intact.",
      },
      280,
    )
  },

  async inspectRetrieval(query) {
    const docs = visibleDocuments().filter((d) => d.status === "indexed")
    const allChunks = docs.flatMap((d) => chunksFor(d.id).slice(0, 3))
    const rand = () => Math.random()

    const vector = allChunks.slice(0, 12).map((chunk, i) => ({
      chunk_id: chunk.id,
      content: chunk.content,
      document_name: DOCUMENT_BY_ID.get(chunk.id.slice(0, 0) || "")?.file_name ??
        docs.find((d) => chunksFor(d.id).some((c) => c.id === chunk.id))?.file_name ?? null,
      page_number: chunk.page_number,
      vector_score: Number((0.92 - i * 0.035 - rand() * 0.02).toFixed(4)),
      rank: i + 1,
    }))
    const bm25 = [...allChunks]
      .sort(() => rand() - 0.5)
      .slice(0, 12)
      .map((chunk, i) => ({
        chunk_id: chunk.id,
        content: chunk.content,
        document_name:
          docs.find((d) => chunksFor(d.id).some((c) => c.id === chunk.id))?.file_name ?? null,
        page_number: chunk.page_number,
        bm25_score: Number((18.4 - i * 1.1 - rand()).toFixed(3)),
        rank: i + 1,
      }))

    const fusedIds = [...new Set([...vector.slice(0, 8), ...bm25.slice(0, 8)].map((h) => h.chunk_id))]
    const fused = fusedIds.map((id, i) => {
      const v = vector.find((h) => h.chunk_id === id)
      const b = bm25.find((h) => h.chunk_id === id)
      return {
        chunk_id: id,
        vector_score: v?.vector_score ?? null,
        bm25_score: b?.bm25_score ?? null,
        rrf_score: Number(
          (1 / (60 + (v?.rank ?? 200)) + 1 / (60 + (b?.rank ?? 200))).toFixed(6),
        ),
        rank: i + 1,
      }
    })
    fused.sort((a, b) => b.rrf_score - a.rrf_score).forEach((h, i) => (h.rank = i + 1))

    const reranked = fused.slice(0, 10).map((h, i) => ({
      chunk_id: h.chunk_id,
      rerank_score: Number((0.96 - i * 0.07 - rand() * 0.03).toFixed(4)),
      final_rank: i + 1,
    }))
    reranked.sort((a, b) => b.rerank_score - a.rerank_score).forEach((h, i) => (h.final_rank = i + 1))

    const qp = Math.round(120 + rand() * 260)
    const vs = Math.round(180 + rand() * 220)
    const bs = Math.round(60 + rand() * 120)
    const fs = Math.round(2 + rand() * 6)
    const rr = Math.round(320 + rand() * 480)

    return delay(
      {
        original_query: query,
        processed_query: {
          rewritten: query.replace(/^what(?:'s| is)\s+/i, "").trim(),
          expanded: [
            `${query} IS 800`,
            `${query} drawing revision`,
            `${query} connection detail`,
          ],
          intent: /compare|difference|versus/i.test(query)
            ? "comparison"
            : /list|which|all/i.test(query)
              ? "enumeration"
              : "factual",
          domain: "structural-steel",
          selected_sources: ["qdrant", "elasticsearch"],
        },
        vector_results: vector,
        bm25_results: bm25,
        fused_results: fused,
        reranked_results: reranked,
        latency_breakdown: {
          query_processing_ms: qp,
          vector_search_ms: vs,
          bm25_search_ms: bs,
          fusion_ms: fs,
          reranking_ms: rr,
          total_ms: qp + vs + bs + fs + rr,
        },
        governance: {
          blocked_by_clearance:
            currentPrincipal.clearance === "restricted"
              ? 0
              : DOCUMENTS.filter(
                  (d) =>
                    SENSITIVITY_LEVEL[d.sensitivity] >
                    SENSITIVITY_LEVEL[currentPrincipal.clearance],
                ).length,
          trace_id: uid("trace", query).replace(/-/g, "").slice(0, 32),
        },
      },
      600,
    )
  },

  async submitFeedback(body) {
    const allowed = [
      "hallucinated", "wrong_source", "incomplete", "outdated",
      "irrelevant", "too_slow", "helpful", "other",
    ]
    const accepted = (body.tags ?? []).filter((t) => allowed.includes(t)).sort()
    const ignored = (body.tags ?? []).filter((t) => !allowed.includes(t)).sort()
    FEEDBACK.unshift({
      id: uid("fb", String(Date.now())),
      rating: body.rating,
      comment: body.comment ?? null,
      tags: accepted,
      trace_id: body.trace_id ?? "",
      message_id: body.message_id ?? null,
      question: "(this session)",
      created_at: new Date().toISOString(),
      user_email: currentPrincipal.email,
    })
    return delay({ feedback_id: uid("fb", String(Date.now())), accepted_tags: accepted, ignored_tags: ignored }, 300)
  },

  async feedbackTags() {
    return delay([
      "hallucinated", "incomplete", "irrelevant", "helpful",
      "other", "outdated", "too_slow", "wrong_source",
    ])
  },

  async feedbackSummary(windowHours = 168) {
    const cutoff = Date.now() - windowHours * 3600_000
    const window = FEEDBACK.filter((f) => new Date(f.created_at).getTime() >= cutoff)
    const byTag: Record<string, number> = {}
    for (const entry of window) for (const tag of entry.tags) byTag[tag] = (byTag[tag] ?? 0) + 1
    // The server counts a rating of 2 or less as negative; anything above is
    // not automatically "positive", so these are counted separately rather
    // than derived from each other.
    const negative = window.filter((f) => f.rating <= 2).length
    const positive = window.filter((f) => f.rating >= 4).length
    return delay({
      window_hours: windowHours,
      total: window.length,
      positive,
      negative,
      negative_rate: window.length ? Number((negative / window.length).toFixed(3)) : 0,
      average_rating: window.length
        ? Number((window.reduce((s, f) => s + f.rating, 0) / window.length).toFixed(2))
        : null,
      by_tag: byTag,
      recent: window.slice(0, 12),
    })
  },

  async promoteFeedback(dataset = "regression_from_feedback") {
    const negatives = FEEDBACK.filter((f) => f.rating <= 2).length
    return delay({ promoted: negatives, dataset, skipped: 0 }, 700)
  },

  async listEvaluationRuns() {
    return delay(EVAL_RUNS)
  },

  async getEvaluationRun(id) {
    const run = EVAL_RUNS.find((r) => r.run_id === id)
    if (!run) notFound("Run not found")
    return delay(run)
  },

  async startEvaluationRun(body) {
    const total = body.questions?.length ?? 48
    const run: T.EvaluationRun = {
      run_id: uid("run", String(Date.now())),
      name: body.name,
      dataset_name: body.dataset_name,
      status: "running",
      total_items: total,
      completed_items: 0,
      started_at: new Date().toISOString(),
      completed_at: null,
      error_message: null,
      metrics: [],
    }
    EVAL_RUNS.unshift(run)
    // Advance it so the runs table is not permanently stuck at 0.
    const timer = setInterval(() => {
      run.completed_items = Math.min(run.total_items, run.completed_items + Math.ceil(total / 12))
      if (run.completed_items >= run.total_items) {
        run.status = "completed"
        run.completed_at = new Date().toISOString()
        run.metrics = [
          { name: "faithfulness", value: 0.83 + Math.random() * 0.09, sample_size: total },
          { name: "answer_relevancy", value: 0.8 + Math.random() * 0.1, sample_size: total },
          { name: "context_relevancy", value: 0.66 + Math.random() * 0.12, sample_size: total },
          { name: "citation_validity", value: 0.92 + Math.random() * 0.06, sample_size: total },
        ]
        clearInterval(timer)
      }
    }, 2200)
    return {
      run_id: run.run_id,
      status: "running",
      total_items: total,
      message: `Evaluation started with ${total} sample(s).`,
    }
  },

  async listDatasets() {
    return delay(["golden_set_v1", "steel_drawings_v2", "regression_from_feedback"])
  },

  async evaluationGate(dataset = "golden_set_v1") {
    const latest = EVAL_RUNS.find((r) => r.dataset_name === dataset && r.status === "completed")
    if (!latest) {
      return delay({ dataset, status: "no_completed_run", passing: null, policy_version: "2025.08-r3" })
    }
    const floors: Record<string, number | null> = {
      faithfulness: 0.75,
      answer_relevancy: 0.7,
      context_relevancy: 0.6,
      citation_validity: null,
    }
    const metrics = latest.metrics.map((m) => {
      const floor = floors[m.name] ?? null
      return {
        metric: m.name,
        value: Number(m.value.toFixed(4)),
        floor,
        gated: floor !== null,
        passing: floor === null || m.value >= floor,
      }
    })
    return delay({
      dataset,
      run_id: latest.run_id,
      status: latest.status,
      passing: metrics.every((m) => m.passing),
      policy_version: "2025.08-r3",
      metrics,
    })
  },

  async onlineMetrics(windowHours = 24) {
    const averages = {
      faithfulness: 0.796,
      answer_relevancy: 0.831,
      context_relevancy: 0.573,
    }
    const floors = { faithfulness: 0.75, answer_relevancy: 0.7, context_relevancy: 0.6 }
    return delay({
      window_hours: windowHours,
      sample_count: 138,
      averages,
      floors,
      breaches: Object.entries(averages)
        .filter(([name, value]) => value < (floors as Record<string, number>)[name])
        .map(([name]) => name),
    })
  },

  async governanceStatus() {
    return delay({
      policy_version: "2025.08-r3",
      enforcement_mode: "enforce",
      risk_register_version: "1.4.0",
      risks_by_function: {
        govern: RISKS.filter((r) => r.function === "govern").length,
        map: RISKS.filter((r) => r.function === "map").length,
        measure: RISKS.filter((r) => r.function === "measure").length,
        manage: RISKS.filter((r) => r.function === "manage").length,
      },
      controls: RISKS.reduce((sum, r) => sum + r.controls.length, 0),
      flags: Object.fromEntries(flags.map((f) => [f.name, f.enabled])),
      quality_floors: { faithfulness: 0.75, answer_relevancy: 0.7, context_relevancy: 0.6 },
    })
  },

  async listFlags() {
    return delay(flags)
  },

  async setFlag(name, enabled, reason) {
    if (currentPrincipal.role !== "admin") {
      throw new ApiError(403, "Flipping a kill switch requires the admin role.")
    }
    const flag = flags.find((f) => f.name === name)
    if (!flag) throw new ApiError(404, `Unknown flag '${name}'.`)
    flag.enabled = enabled
    flag.source = "override"
    AUDIT.unshift({
      id: uid("audit", `flag:${Date.now()}`),
      trace_id: uid("trace", String(Date.now())).replace(/-/g, "").slice(0, 32),
      actor_id: currentPrincipal.user_id,
      actor_role: currentPrincipal.role,
      actor_email: currentPrincipal.email,
      action: "kill_switch_toggled",
      resource_type: "feature_flag",
      resource_id: name,
      resource_label: name,
      outcome: "completed",
      reason,
      control_id: null,
      created_at: new Date().toISOString(),
    })
    return delay(flags, 300)
  },

  async listRisks(fn) {
    const risks = fn ? RISKS.filter((r) => r.function === fn) : RISKS
    return delay({ version: "1.4.0", count: risks.length, risks })
  },

  async listAudit({ limit = 100, action, resource_id, hours = 168 }) {
    const cutoff = Date.now() - hours * 3600_000
    let rows = AUDIT.filter((a) => new Date(a.created_at).getTime() >= cutoff)
    if (action) rows = rows.filter((a) => a.action === action)
    if (resource_id) rows = rows.filter((a) => a.resource_id === resource_id)
    return delay(rows.slice(0, limit))
  },

  async runRetention(dryRun) {
    const candidates = DOCUMENTS.filter(
      (d) => d.retention_until && new Date(d.retention_until).getTime() < Date.now() + 40 * 86400_000,
    ).slice(0, 3)
    return delay(
      {
        dry_run: dryRun,
        candidates: candidates.length,
        purged: dryRun ? 0 : candidates.length,
        chunks_deleted: dryRun ? 0 : candidates.reduce((s, d) => s + (d.chunk_count ?? 0), 0),
        documents: candidates.map((d) => ({
          id: d.id,
          file_name: d.file_name,
          retention_until: d.retention_until!,
        })),
      },
      900,
    )
  },

  async listPlugins() {
    return delay({
      llm_providers: [
        { name: "anthropic", description: "Claude models via langchain-anthropic", models: ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"] },
        { name: "openai", description: "GPT + embeddings via langchain-openai", models: ["text-embedding-3-large"] },
        { name: "azure-openai", description: "Azure-hosted deployment", models: [] },
      ],
      tools: [
        { name: "sql_query", description: "Structured lookups over the metadata store" },
        { name: "drawing_register", description: "Filter the drawing register by project, mark or section" },
        { name: "unit_convert", description: "SI / imperial conversion with dimensional checking" },
        { name: "section_properties", description: "Look up Ix, Zx, rx for a designation from the gazetteer" },
        { name: "web_search", description: "Disabled for this deployment" },
      ],
      pii_detectors: [
        { name: "email", description: "RFC-5322 addresses" },
        { name: "phone_in", description: "Indian mobile and landline formats" },
        { name: "aadhaar", description: "12-digit identity numbers with Verhoeff check" },
      ],
    })
  },

  async describeGateway() {
    return delay({
      small: {
        chain: [
          { provider: "anthropic", model: "claude-haiku-4-5-20251001" },
          { provider: "openai", model: "gpt-4.1-mini" },
        ],
      },
      large: {
        chain: [
          { provider: "anthropic", model: "claude-sonnet-4-6" },
          { provider: "azure-openai", model: "gpt-4.1" },
        ],
      },
    })
  },

  async getSettings() {
    return delay(settings)
  },

  async updateSetting(key, value, reason) {
    if (currentPrincipal.role !== "admin") {
      throw new ApiError(403, "Changing a setting requires the admin role.")
    }
    const item = settings.find((s) => s.key === key)
    if (!item) throw new ApiError(404, `Unknown setting '${key}'.`)
    const before = item.value
    item.value = value
    item.is_override = true
    AUDIT.unshift({
      id: uid("audit", `setting:${Date.now()}`),
      trace_id: uid("trace", String(Date.now())).replace(/-/g, "").slice(0, 32),
      actor_id: currentPrincipal.user_id,
      actor_role: currentPrincipal.role,
      actor_email: currentPrincipal.email,
      action: "setting_changed",
      resource_type: "system_setting",
      resource_id: key,
      resource_label: `${key}: ${JSON.stringify(before)} → ${JSON.stringify(value)}`,
      outcome: "completed",
      reason,
      control_id: null,
      created_at: new Date().toISOString(),
    })
    return delay(item, 350)
  },

  async health() {
    return delay({
      status: "degraded",
      version: "1.0.0",
      uptime_seconds: 412_388,
      checks: {
        postgres: "ok",
        redis: "ok",
        qdrant: "ok",
        elasticsearch:
          "error: ConnectionTimeout caused by TimeoutError() — cluster health yellow, 1 unassigned shard",
        minio: "ok",
        arq_worker: "ok",
      },
      timestamp: new Date().toISOString(),
    })
  },

  async systemMetrics() {
    return delay({
      ...METRICS,
      queue_depth: sessionJobs.filter((j) => j.status === "queued").length,
      jobs_running: sessionJobs.filter((j) => j.status === "running").length,
      jobs_failed_24h: sessionJobs.filter((j) => j.status === "failed").length,
      documents_total: visibleDocuments().length,
      documents_indexed: visibleDocuments().filter((d) => d.status === "indexed").length,
    })
  },

  async listJobs({ status, limit = 100 } = {}) {
    let rows = sessionJobs
    if (status?.length) rows = rows.filter((j) => status.includes(j.status))
    return delay(
      [...rows]
        .sort((a, b) => {
          // Work that needs attention first, then newest.
          const weight = (j: T.Job) =>
            j.status === "running" ? 0 : j.status === "queued" ? 1 : j.status === "failed" ? 2 : 3
          return weight(a) - weight(b) || b.created_at.localeCompare(a.created_at)
        })
        .slice(0, limit),
    )
  },
}

export { agoIso }
