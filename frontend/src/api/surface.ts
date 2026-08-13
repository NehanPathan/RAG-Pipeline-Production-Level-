import type * as T from "@/api/types"

/**
 * Everything the UI is allowed to ask for.
 *
 * Both the live HTTP client and the sample-corpus implementation satisfy this
 * interface, so swapping between them is a one-line change and no page can
 * accidentally depend on a mock-only affordance.
 */
export interface ApiSurface {
  /* Identity */
  whoAmI(): Promise<{ policy: T.GovernancePolicy; principal: T.Principal }>
  listUsers(): Promise<T.Principal[]>

  /* Documents */
  listDocuments(params: {
    page?: number
    size?: number
    search?: string
    /* Client-side facets over the returned page until the API grows filters. */
    status?: T.DocumentStatus[]
    sensitivity?: T.Sensitivity[]
    project_id?: string | null
    content_kind?: T.ContentKind[]
    file_type?: string[]
    latest_only?: boolean
  }): Promise<T.DocumentListResponse>
  getDocument(id: string): Promise<T.DocumentSummary>
  getChunks(id: string): Promise<T.ChunkListResponse>
  getIntelligence(id: string): Promise<T.DocumentIntelligence>
  getDocumentJobs(id: string): Promise<T.Job[]>
  uploadDocument(input: {
    file: File
    domain?: string
    tags?: string
    sensitivity?: T.Sensitivity | ""
    project_id?: string
    drawing_number?: string
    revision_label?: string
    onProgress?: (fraction: number) => void
  }): Promise<T.UploadResponse>
  reclassifyDocument(id: string, sensitivity: T.Sensitivity, reason: string): Promise<T.DocumentSummary>
  reprocessDocument(id: string): Promise<{ document_id: string; job_id: string; status: string; message: string }>
  deleteDocument(id: string): Promise<{ document_id: string; deleted_chunks: number; message: string }>
  originalUrl(id: string): string

  /* Projects, drawings, revisions */
  listProjects(): Promise<T.Project[]>
  getProject(id: string): Promise<T.Project>
  listProjectMembers(id: string): Promise<T.ProjectMember[]>
  listDrawings(params?: { project_id?: string; search?: string }): Promise<T.Drawing[]>
  getDrawing(id: string): Promise<T.Drawing>
  listRevisions(drawingId: string): Promise<T.Revision[]>

  /* Search */
  getFacets(params?: { fields?: string[]; include_superseded?: boolean }): Promise<T.FacetsResponse>
  search(body: T.SearchRequest): Promise<T.SearchResponse>

  /* Chat */
  streamChat(
    body: T.ChatRequest,
    signal?: AbortSignal,
  ): AsyncGenerator<T.ChatStreamEvent, void, void>
  listConversations(limit?: number): Promise<T.ConversationSummary[]>
  getConversation(id: string): Promise<T.ConversationMessage[]>

  /* Retrieval inspection */
  inspectRetrieval(query: string): Promise<T.RetrievalInspection>

  /* Feedback */
  submitFeedback(body: T.FeedbackRequest): Promise<T.FeedbackResponse>
  feedbackTags(): Promise<string[]>
  feedbackSummary(windowHours?: number): Promise<T.FeedbackSummary>
  promoteFeedback(dataset?: string): Promise<{ promoted: number; dataset: string; [k: string]: unknown }>

  /* Evaluation */
  listEvaluationRuns(): Promise<T.EvaluationRun[]>
  getEvaluationRun(id: string): Promise<T.EvaluationRun>
  startEvaluationRun(body: { name: string; dataset_name: string; questions?: string[] }): Promise<{ run_id: string; status: string; total_items: number; message: string }>
  listDatasets(): Promise<string[]>
  evaluationGate(dataset?: string): Promise<T.EvaluationGate>
  onlineMetrics(windowHours?: number): Promise<T.OnlineMetrics>

  /* Governance */
  governanceStatus(): Promise<T.GovernanceStatus>
  listFlags(): Promise<T.RuntimeFlag[]>
  setFlag(name: string, enabled: boolean, reason: string): Promise<T.RuntimeFlag[]>
  listRisks(fn?: string): Promise<{ version: string; count: number; risks: T.Risk[] }>
  listAudit(params: { limit?: number; action?: string; resource_id?: string; hours?: number }): Promise<T.AuditEntry[]>
  runRetention(dryRun: boolean): Promise<T.RetentionResult>
  listPlugins(): Promise<T.PluginRegistry>
  describeGateway(): Promise<T.GatewayDescription>

  /* Admin & health */
  getSettings(): Promise<T.SettingItem[]>
  updateSetting(key: string, value: unknown, reason: string): Promise<T.SettingItem>
  health(): Promise<T.HealthCheck>
  systemMetrics(): Promise<T.SystemMetrics>

  /* Job queue (PLANNED as a global view; per-document jobs exist today). */
  listJobs(params?: { status?: T.JobStatus[]; limit?: number }): Promise<T.Job[]>
}
