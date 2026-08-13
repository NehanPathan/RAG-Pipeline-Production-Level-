/**
 * Wire types, mirroring the FastAPI response models in `src/api/routes/`.
 *
 * Anything marked PLANNED has no route yet: the domain entities and
 * repositories exist (`src/domain/entities/project.py`,
 * `src/domain/repositories/project_repository.py`) and the design is fixed in
 * `docs/architecture/14_steel_domain_design.md`, but the HTTP surface has not
 * been written. Those shapes are the frontend's proposed contract — they are
 * isolated here so that wiring them up later is an adapter change, not a
 * rewrite of the pages that consume them.
 */

/* ------------------------------------------------------------ Identity */

export type Role = "viewer" | "analyst" | "steward" | "admin"
export type Sensitivity = "public" | "internal" | "confidential" | "restricted"
export type ProjectRole = "owner" | "contributor" | "reader"

/** GET /governance/policy → `principal` */
export interface Principal {
  user_id: string
  role: Role
  clearance: Sensitivity
  email: string
  auth_provider: string
  authenticated: boolean
  /** Display-only; not part of the API payload. */
  display_name?: string
}

/* ------------------------------------------------------------ Documents */

export type DocumentStatus =
  | "pending"
  | "processing"
  | "indexed"
  | "failed"
  | "deleted"

/** "Is this an exact CAD value, a plotted string, or OCR's reading of one?" */
export type ContentKind =
  | "prose"
  | "vector_drawing"
  | "scanned_drawing"
  | "scanned_prose"
  | "mixed"
  | "cad_native"

/** GET /documents/{id} */
export interface DocumentSummary {
  id: string
  file_name: string
  file_type: string
  status: DocumentStatus
  page_count: number | null
  word_count: number | null
  domain: string | null
  tags: string[]
  indexed_at: string | null
  created_at: string
  sensitivity: Sensitivity
  retention_until: string | null
  /* PLANNED — columns exist on `documents`, not yet in DocumentResponse. */
  file_size_bytes?: number
  project_id?: string | null
  project_number?: string | null
  drawing_id?: string | null
  drawing_number?: string | null
  revision_label?: string | null
  revision_index?: number | null
  revision_date?: string | null
  revision_note?: string | null
  is_latest?: boolean
  superseded_by_document_id?: string | null
  content_kind?: ContentKind | null
  uploaded_by?: string
  chunk_count?: number
}

export interface DocumentListResponse {
  items: DocumentSummary[]
  total: number
  page: number
  size: number
}

export interface UploadResponse {
  document_id: string
  file_name: string
  status: string
  message: string
}

/** GET /documents/{id}/chunks */
export interface Chunk {
  id: string
  parent_chunk_id: string | null
  chunk_type: "parent" | "child" | "table" | "figure" | string
  content: string
  position: number
  page_number: number | null
  section_title: string | null
  heading_level: number | null
  semantic_cluster: number | null
  ocr_confidence: number | null
  language: string | null
  token_count: number
  embedding_model: string
  /* PLANNED — `ChunkMetadata.regions` / `content_kind` exist in the domain
     model and are stored, but ChunkResponse does not project them yet. */
  content_kind?: ContentKind | null
  region_precision?: "block" | "section" | "page" | null
  regions?: Region[]
  entities?: SteelEntity[]
}

export interface ChunkListResponse {
  items: Chunk[]
  total: number
}

/** Normalized render space (0..1, Y-down) unless `space` says otherwise. */
export interface Region {
  page_number: number
  x0: number
  y0: number
  x1: number
  y1: number
  space?: "page" | "image" | "model"
}

/** PLANNED — written to `document_metadata.custom_metadata` today. */
export interface SteelEntity {
  canonical: string
  surface: string
  type:
    | "section"
    | "grade"
    | "bolt"
    | "weld"
    | "dimension"
    | "load"
    | "mark"
    | "drawing_ref"
    | "project_ref"
  confidence: number
  source: "gazetteer" | "pattern" | "context" | "llm"
  occurrences?: number
  unit?: string
  value?: number
}

/** GET /documents/{id}/intelligence */
export interface DocumentIntelligence {
  document_id: string
  ocr_engine: string
  ocr_ran: boolean
  ocr_confidence_avg: number | null
  ocr_processing_time_ms: number
  ocr_language: string | null
  embedding_model_chunking: string
  embedding_model_retrieval: string
  layout: LayoutSummary
  semantic_graph: SimilarityEdge[]
  created_at: string | null
  /* PLANNED — per-page classification from DrawingContentDetector. */
  pages?: PageClassification[]
}

export interface LayoutSummary {
  headings: { text: string; level: number; page?: number }[]
  outline: { text: string; level: number; page?: number; children?: unknown[] }[]
  tables_count: number
  figures_count: number
  lists_count: number
  forms_count: number
  footnotes_count: number
}

export interface SimilarityEdge {
  chunk_id_a: string
  chunk_id_b: string
  similarity: number
}

export interface PageClassification {
  page_number: number
  kind: ContentKind
  confidence: number
  native_text_words: number | null
  loader_words: number
  ocr_ran: boolean
  ocr_confidence: number | null
}

/** GET /documents/{id}/jobs */
export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "skipped"
export type JobType = "ingest_document" | "reindex_document"

export interface Job {
  id: string
  job_type: JobType
  status: JobStatus
  attempts: number
  max_attempts: number
  error_message: string | null
  created_at: string
  finished_at: string | null
  /* PLANNED — a queue view needs the document behind each job. */
  document_id?: string
  document_name?: string
  started_at?: string | null
  stage?: IngestionStage | null
  progress?: number
}

export type IngestionStage =
  | "queued"
  | "load"
  | "classify"
  | "ocr"
  | "layout"
  | "extract"
  | "chunk"
  | "embed"
  | "index"
  | "done"

/* ------------------------------------------------- Projects & drawings */
/* PLANNED — entities and repositories exist; routes do not.             */

export type ProjectStatus = "active" | "on_hold" | "closed" | "archived"

export interface Project {
  id: string
  project_number: string
  name: string
  client_name: string | null
  status: ProjectStatus
  start_date: string | null
  target_completion_date: string | null
  default_sensitivity: Sensitivity | null
  created_at: string
  updated_at: string
  archived_at: string | null
  /* Denormalized counters for the register view. */
  drawing_count?: number
  document_count?: number
  member_count?: number
  open_jobs?: number
  last_activity_at?: string | null
  tonnage_t?: number | null
  location?: string | null
}

export interface ProjectMember {
  project_id: string
  user_id: string
  project_role: ProjectRole
  added_at: string
  email: string
  display_name: string
  platform_role: Role
}

export interface Drawing {
  id: string
  drawing_number: string
  sheet_number: string | null
  project_id: string | null
  project_number?: string | null
  discipline: string | null
  title: string | null
  created_at: string
  updated_at: string
  /* Revision family summary. */
  revision_count?: number
  current_revision_label?: string | null
  current_document_id?: string | null
  current_revision_date?: string | null
  content_kind?: ContentKind | null
  status?: DocumentStatus
}

export interface Revision {
  document_id: string
  drawing_id: string
  revision_label: string
  revision_index: number
  revision_date: string | null
  revision_note: string | null
  is_latest: boolean
  superseded_at: string | null
  superseded_by_document_id: string | null
  file_name: string
  status: DocumentStatus
  page_count: number | null
  uploaded_by: string
  created_at: string
}

/* --------------------------------------------------------------- Search */

export interface FacetValue {
  value: string
  count: number
}

/** GET /search/facets */
export interface FacetsResponse {
  facets: Record<string, FacetValue[]>
}

export const FACETABLE_FIELDS = [
  "project_number",
  "drawing_number",
  "revision_label",
  "domain",
  "file_type",
  "tags",
  "entity_canonicals",
  "sensitivity",
  "content_kind",
] as const

export type FacetField = (typeof FACETABLE_FIELDS)[number]

/** POST /search */
export interface SearchRequest {
  query?: string
  project_ids?: string[]
  drawing_numbers?: string[]
  entity_canonicals?: string[]
  tags?: string[]
  file_type?: string | null
  domain?: string | null
  content_kinds?: string[]
  include_superseded?: boolean
  top_k?: number
}

export interface SearchHit {
  chunk_id: string
  document_id: string
  document_name: string | null
  page_number: number | null
  section: string | null
  drawing_number: string | null
  revision_label: string | null
  project_number: string | null
  entity_canonicals: string[]
  content_kind: ContentKind | null
  content: string
  score: number
}

export interface SearchResponse {
  items: SearchHit[]
  total: number
}

/* ----------------------------------------------------------------- Chat */

export interface Citation {
  index: number
  source_name?: string
  document_name: string
  chunk_id?: string
  document_id?: string | null
  page_number?: number | null
  section?: string | null
  /* PLANNED — needed to jump the viewer to the cited region. */
  drawing_number?: string | null
  revision_label?: string | null
  content_kind?: ContentKind | null
  snippet?: string
  regions?: Region[]
  score?: number
}

export interface ChatRequest {
  query: string
  conversation_id?: string | null
  filters?: Record<string, unknown>
  stream?: boolean
  debug?: boolean
}

export interface ChatResponse {
  answer: string
  citations: Citation[]
  conversation_id: string
  message_id: string
  latency_ms: number
  trace_id: string
  refused: boolean
  route: string
  route_source: string
  grounded: boolean
  cached: boolean
  refusal_reason: string | null
}

/** SSE frames emitted by POST /chat with `stream: true`. */
export type ChatStreamEvent =
  | { type: "token"; content: string }
  | { type: "status"; stage: string; message?: string }
  | { type: "citations"; citations: Citation[] }
  | { type: "error"; message: string }
  | ({ type: "done" } & Partial<ChatResponse> & { model_used?: string })

export interface ConversationSummary {
  id: string
  title: string
  message_count: number
  created_at: string
  updated_at: string
}

export interface ConversationMessage {
  id: string
  role: "user" | "assistant" | "system"
  content: string
  citations: Citation[]
  model_used: string
  latency_ms: number
  trace_id: string
  created_at: string
}

/* ------------------------------------------------ Retrieval inspection */

export interface RetrievalInspection {
  original_query: string
  processed_query: {
    rewritten: string
    expanded: string[]
    intent: string
    domain: string | null
    selected_sources: string[]
  }
  vector_results: InspectHit[]
  bm25_results: InspectHit[]
  fused_results: FusedHit[]
  reranked_results: { chunk_id: string; rerank_score: number; final_rank: number }[]
  latency_breakdown: {
    query_processing_ms: number
    vector_search_ms: number
    bm25_search_ms: number
    fusion_ms: number
    reranking_ms: number
    total_ms: number
  }
  governance: { blocked_by_clearance: number; trace_id: string }
}

export interface InspectHit {
  chunk_id: string
  content: string
  document_name: string | null
  page_number: number | null
  vector_score?: number
  bm25_score?: number
  rank: number
}

export interface FusedHit {
  chunk_id: string
  vector_score: number | null
  bm25_score: number | null
  rrf_score: number
  rank: number
}

/* ------------------------------------------------------------- Feedback */

export const FEEDBACK_TAGS = [
  "hallucinated",
  "wrong_source",
  "incomplete",
  "outdated",
  "irrelevant",
  "too_slow",
  "helpful",
  "other",
] as const

export type FeedbackTag = (typeof FEEDBACK_TAGS)[number]

export interface FeedbackRequest {
  rating: number
  message_id?: string | null
  trace_id?: string | null
  comment?: string | null
  tags?: string[]
}

export interface FeedbackResponse {
  feedback_id: string
  accepted_tags: string[]
  ignored_tags: string[]
}

export interface FeedbackSummary {
  window_hours: number
  total: number
  positive: number
  negative: number
  average_rating: number | null
  by_tag: Record<string, number>
  /* PLANNED — recent comments make the summary actionable. */
  recent?: FeedbackEntry[]
}

export interface FeedbackEntry {
  id: string
  rating: number
  comment: string | null
  tags: string[]
  trace_id: string
  message_id: string | null
  question: string
  created_at: string
  user_email?: string
}

/* ----------------------------------------------------------- Evaluation */

export interface EvaluationRun {
  run_id: string
  name: string
  dataset_name: string
  status: "running" | "completed" | "failed" | string
  total_items: number
  completed_items: number
  started_at: string
  completed_at: string | null
  error_message: string | null
  metrics: { name: string; value: number; sample_size: number | null }[]
}

export interface EvaluationGate {
  dataset: string
  run_id?: string
  status: string
  passing: boolean | null
  policy_version: string
  metrics?: {
    metric: string
    value: number
    floor: number | null
    gated: boolean
    passing: boolean
  }[]
}

export interface OnlineMetrics {
  window_hours: number
  sample_count: number
  averages: Record<string, number>
  floors: Record<string, number | null>
  breaches: string[]
}

/* ----------------------------------------------------------- Governance */

export interface GovernancePolicy {
  version: string
  enforcement_mode: string
  default_sensitivity: Sensitivity
  default_clearance: Sensitivity
  retention_days: number
  min_faithfulness: number
  min_answer_relevancy: number
  min_context_relevancy: number
  [key: string]: unknown
}

export interface RuntimeFlag {
  name: string
  enabled: boolean
  source: string
  description: string
}

export interface GovernanceStatus {
  policy_version: string
  enforcement_mode: string
  risk_register_version: string
  risks_by_function: Record<string, number>
  controls: number
  flags: Record<string, boolean>
  quality_floors: Record<string, number>
}

export interface RiskControl {
  id: string
  description: string
  implemented_in: string
}

export interface Risk {
  id: string
  title: string
  function: "govern" | "map" | "measure" | "manage" | string
  severity: string
  likelihood: string
  metric: string
  threshold: number | string
  threshold_direction: string
  owner: string
  residual_risk: string
  controls: RiskControl[]
}

export interface AuditEntry {
  id: string
  trace_id: string | null
  actor_id: string | null
  actor_role: string | null
  action: string
  resource_type: string | null
  resource_id: string | null
  outcome: "allowed" | "denied" | "completed" | "failed" | string
  reason: string | null
  control_id: string | null
  created_at: string
  /* PLANNED — an audit reader wants a name, not a UUID. */
  actor_email?: string
  resource_label?: string | null
}

export interface RetentionResult {
  dry_run: boolean
  candidates: number
  purged: number
  chunks_deleted: number
  documents: { id: string; file_name: string; retention_until: string }[]
}

export interface PluginRegistry {
  llm_providers: RegistryEntry[]
  tools: RegistryEntry[]
  pii_detectors: RegistryEntry[]
}

export interface RegistryEntry {
  name: string
  description?: string
  [key: string]: unknown
}

export interface GatewayDescription {
  [role: string]: {
    chain: { provider: string; model: string }[]
    [key: string]: unknown
  }
}

/* ---------------------------------------------------------------- Admin */

export interface SettingItem {
  key: string
  value: unknown
  is_override: boolean
}

/* --------------------------------------------------------------- Health */

export interface HealthCheck {
  status: "healthy" | "degraded" | string
  version: string
  uptime_seconds: number
  checks: Record<string, string>
  timestamp: string
}

/** PLANNED — parsed from GET /metrics (Prometheus text) or a JSON tile API. */
export interface SystemMetrics {
  queue_depth: number
  jobs_running: number
  jobs_failed_24h: number
  documents_total: number
  documents_indexed: number
  chunks_total: number
  queries_24h: number
  p95_latency_ms: number
  cache_hit_rate: number
  refusal_rate: number
  access_denied_24h: number
  cost_24h_usd: number
  ingest_series: { t: string; ingested: number; failed: number }[]
  query_series: { t: string; queries: number; p95_ms: number }[]
}
