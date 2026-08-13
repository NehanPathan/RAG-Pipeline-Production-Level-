/**
 * The live implementation — a thin, literal mapping onto the FastAPI routes.
 *
 * Where the server has no route yet (projects, drawings, revisions, the global
 * job queue, aggregate metrics) the method throws a NotImplemented ApiError
 * rather than inventing data. The pages catch it and render a "not wired up
 * yet" state, which is honest and points at the endpoint that has to exist.
 */
import { ApiError, API_BASE, authHeaders, request, streamSSE } from "@/api/client"
import type { ApiSurface } from "@/api/surface"
import type * as T from "@/api/types"

function notImplemented(endpoint: string): never {
  throw new ApiError(
    501,
    `No backend route for ${endpoint} yet. The domain entities exist (see src/domain/entities/project.py); the HTTP surface has not been written.`,
  )
}

export const realApi: ApiSurface = {
  async whoAmI() {
    return request("/governance/policy")
  },

  async listUsers() {
    return notImplemented("GET /users")
  },

  async listDocuments({ page = 1, size = 20, search }) {
    // The API paginates and full-text searches; the remaining facets in the
    // params are applied client-side by the caller until it filters natively.
    return request<T.DocumentListResponse>("/documents", {
      query: { page, size, search },
    })
  },

  getDocument(id) {
    return request<T.DocumentSummary>(`/documents/${id}`)
  },

  getChunks(id) {
    return request<T.ChunkListResponse>(`/documents/${id}/chunks`)
  },

  getIntelligence(id) {
    return request<T.DocumentIntelligence>(`/documents/${id}/intelligence`)
  },

  getDocumentJobs(id) {
    return request<T.Job[]>(`/documents/${id}/jobs`)
  },

  uploadDocument({
    file,
    domain,
    tags,
    sensitivity,
    project_id,
    drawing_number,
    revision_label,
    onProgress,
  }) {
    const form = new FormData()
    form.append("file", file)
    if (domain) form.append("domain", domain)
    if (tags) form.append("tags", tags)
    if (sensitivity) form.append("sensitivity", sensitivity)
    // The ingest form tells the user these decide which drawing the file
    // becomes a revision of. They were collected and then dropped here, so
    // a corrected drawing number was silently replaced by whatever the
    // extractor guessed from the sheet.
    if (project_id) form.append("project_id", project_id)
    if (drawing_number) form.append("drawing_number", drawing_number)
    if (revision_label) form.append("revision_label", revision_label)

    // XHR rather than fetch: only XHR reports upload progress, and a drawing
    // set is large enough that a progress bar is the difference between
    // "working" and "hung".
    return new Promise<T.UploadResponse>((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open("POST", `${API_BASE}/documents`)
      for (const [key, value] of Object.entries(authHeaders())) {
        xhr.setRequestHeader(key, value)
      }
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) onProgress?.(event.loaded / event.total)
      }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText) as T.UploadResponse)
        } else {
          let detail = xhr.statusText
          try {
            detail = JSON.parse(xhr.responseText).detail ?? detail
          } catch {
            /* keep the status text */
          }
          reject(new ApiError(xhr.status, detail, xhr.getResponseHeader("X-Trace-Id") ?? undefined))
        }
      }
      xhr.onerror = () => reject(new ApiError(0, "Network error during upload."))
      xhr.send(form)
    })
  },

  reclassifyDocument(id, sensitivity, reason) {
    return request<T.DocumentSummary>(`/documents/${id}/classification`, {
      method: "PUT",
      body: { sensitivity, reason },
    })
  },

  reprocessDocument(id) {
    return request(`/documents/${id}/reprocess`, { method: "POST" })
  },

  deleteDocument(id) {
    return request(`/documents/${id}`, { method: "DELETE" })
  },

  originalUrl(id) {
    return `${API_BASE}/documents/${id}/original`
  },

  // Projects and drawings. The server scopes all of these to the caller's
  // membership, so there is no project_id to send for "mine" — asking for
  // one the caller is not in returns nothing rather than 403, which is why
  // these take no scope argument of their own.
  listProjects() {
    return request<T.Project[]>("/projects")
  },

  getProject(id) {
    return request<T.Project>(`/projects/${id}`)
  },

  listProjectMembers(id) {
    return request<T.ProjectMember[]>(`/projects/${id}/members`)
  },

  listDrawings(params = {}) {
    return request<T.Drawing[]>("/drawings", {
      query: { project_id: params.project_id, search: params.search },
    })
  },

  getDrawing(id) {
    return request<T.Drawing>(`/drawings/${id}`)
  },

  listRevisions(drawingId) {
    return request<T.Revision[]>(`/drawings/${drawingId}/revisions`)
  },

  getFacets({ fields, include_superseded } = {}) {
    return request<T.FacetsResponse>("/search/facets", {
      query: { fields, include_superseded },
    })
  },

  search(body) {
    return request<T.SearchResponse>("/search", { method: "POST", body })
  },

  streamChat(body, signal) {
    return streamSSE<T.ChatStreamEvent>("/chat", { ...body, stream: true }, signal)
  },

  listConversations(limit = 50) {
    return request<T.ConversationSummary[]>("/conversations", { query: { limit } })
  },

  getConversation(id) {
    return request<T.ConversationMessage[]>(`/conversations/${id}`)
  },

  inspectRetrieval(query) {
    return request<T.RetrievalInspection>("/retrieval/inspect", {
      method: "POST",
      body: { query },
    })
  },

  submitFeedback(body) {
    return request<T.FeedbackResponse>("/feedback", { method: "POST", body })
  },

  async feedbackTags() {
    const response = await request<{ tags: string[] }>("/feedback/tags")
    return response.tags
  },

  feedbackSummary(windowHours = 168) {
    return request<T.FeedbackSummary>("/feedback/summary", {
      query: { window_hours: windowHours },
    })
  },

  promoteFeedback(dataset = "regression_from_feedback") {
    return request("/feedback/promote", {
      method: "POST",
      query: { dataset_name: dataset },
    })
  },

  listEvaluationRuns() {
    return request<T.EvaluationRun[]>("/evaluation/runs")
  },

  getEvaluationRun(id) {
    return request<T.EvaluationRun>(`/evaluation/runs/${id}`)
  },

  startEvaluationRun(body) {
    return request("/evaluation/runs", { method: "POST", body })
  },

  async listDatasets() {
    const response = await request<{ datasets: string[] }>("/evaluation/datasets")
    return response.datasets
  },

  evaluationGate(dataset = "golden_set_v1") {
    return request<T.EvaluationGate>("/evaluation/gate", {
      query: { dataset_name: dataset },
    })
  },

  onlineMetrics(windowHours = 24) {
    return request<T.OnlineMetrics>("/evaluation/online", {
      query: { window_hours: windowHours },
    })
  },

  governanceStatus() {
    return request<T.GovernanceStatus>("/governance/status")
  },

  async listFlags() {
    const response = await request<{ flags: T.RuntimeFlag[] }>("/governance/flags")
    return response.flags
  },

  async setFlag(name, enabled, reason) {
    const response = await request<{ flags: T.RuntimeFlag[] }>(`/governance/flags/${name}`, {
      method: "PUT",
      body: { enabled, reason },
    })
    return response.flags
  },

  listRisks(fn) {
    return request("/governance/risks", { query: { function: fn } })
  },

  listAudit({ limit = 100, action, resource_id, hours = 168 }) {
    return request<T.AuditEntry[]>("/governance/audit", {
      query: { limit, action, resource_id, hours },
    })
  },

  runRetention(dryRun) {
    return request<T.RetentionResult>("/governance/retention/run", {
      method: "POST",
      query: { dry_run: dryRun },
    })
  },

  listPlugins() {
    return request<T.PluginRegistry>("/governance/plugins")
  },

  describeGateway() {
    return request<T.GatewayDescription>("/governance/gateway")
  },

  async getSettings() {
    const response = await request<{ settings: T.SettingItem[] }>("/admin/settings")
    return response.settings
  },

  async updateSetting(key, value, reason) {
    const response = await request<{ key: string; value: unknown }>(`/admin/settings/${key}`, {
      method: "PUT",
      body: { value, reason },
    })
    return { key: response.key, value: response.value, is_override: true }
  },

  health() {
    return request<T.HealthCheck>("/health")
  },

  async systemMetrics() {
    // /metrics returns Prometheus text, not JSON. Parsing it in the browser
    // would couple the dashboard to metric names; a small JSON tile endpoint
    // is the right shape and does not exist yet.
    return notImplemented("GET /metrics/summary (JSON tiles)")
  },

  listJobs(params = {}) {
    // `status` repeats rather than joining with commas: FastAPI reads a
    // repeated query parameter as a list, and a comma-joined string would
    // arrive as one unrecognised status and be ignored.
    return request<T.Job[]>("/jobs", {
      query: { status: params.status, limit: params.limit },
    })
  },
}
