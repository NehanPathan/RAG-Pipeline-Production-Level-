import { lazy } from "react"
import { Navigate, Route, Routes, useLocation } from "react-router-dom"
import { useAuth } from "@/lib/auth"
import type { Role } from "@/api/types"
import { AppShell } from "@/components/layout/app-shell"
import { EmptyState, NotFoundScreen } from "@/components/domain/states"
import { BanIcon } from "lucide-react"

// Eager: sign-in, and the two screens an engineer uses all day. Everything
// else is lazy — in particular Overview and the ops pages, which are the only
// consumers of the charting library. Someone who only ever asks questions
// never downloads it.
import LoginPage from "@/pages/login"
import AskPage from "@/pages/ask"
import SearchPage from "@/pages/search"

const OverviewPage = lazy(() => import("@/pages/overview"))
const ProjectsPage = lazy(() => import("@/pages/projects"))
const ProjectDetailPage = lazy(() => import("@/pages/project-detail"))
const DrawingsPage = lazy(() => import("@/pages/drawings"))
const DrawingDetailPage = lazy(() => import("@/pages/drawing-detail"))
const DocumentsPage = lazy(() => import("@/pages/documents"))
const DocumentDetailPage = lazy(() => import("@/pages/document-detail"))
const IngestPage = lazy(() => import("@/pages/ingest"))
const QueuePage = lazy(() => import("@/pages/queue"))
const MonitoringPage = lazy(() => import("@/pages/ops-monitoring"))
const QualityPage = lazy(() => import("@/pages/ops-quality"))
const InspectorPage = lazy(() => import("@/pages/ops-inspector"))
const GovernancePage = lazy(() => import("@/pages/ops-governance"))
const AccessPage = lazy(() => import("@/pages/ops-access"))
const SettingsPage = lazy(() => import("@/pages/ops-settings"))
const LegendPage = lazy(() => import("@/pages/legend"))

/** Route guard. Mirrors `require_role`: an allow-list, not a rank compare. */
function RequireRole({ roles, children }: { roles: Role[]; children: React.ReactNode }) {
  const { principal } = useAuth()
  if (!principal) return null
  if (!roles.includes(principal.role)) {
    return (
      <div className="mx-auto w-full max-w-2xl p-6">
        <EmptyState
          icon={BanIcon}
          title="Your role does not open this page"
          description={
            <>
              This area is limited to {roles.join(" and ")}. You are signed in as{" "}
              <b>{principal.role}</b>. Roles are read from the database and never from anything the
              browser sends, so this is not something the UI can grant.
            </>
          }
        />
      </div>
    )
  }
  return <>{children}</>
}

export default function App() {
  const { principal } = useAuth()
  const location = useLocation()

  if (!principal) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace state={{ from: location }} />} />
      </Routes>
    )
  }

  return (
    <Routes>
      <Route path="/login" element={<Navigate to="/" replace />} />
      <Route element={<AppShell />}>
        <Route index element={<OverviewPage />} />
        <Route path="ask" element={<AskPage />} />
        <Route path="ask/:conversationId" element={<AskPage />} />
        <Route path="search" element={<SearchPage />} />

        <Route path="projects" element={<ProjectsPage />} />
        <Route path="projects/:projectId" element={<ProjectDetailPage />} />
        <Route path="drawings" element={<DrawingsPage />} />
        <Route path="drawings/:drawingId" element={<DrawingDetailPage />} />
        <Route path="documents" element={<DocumentsPage />} />
        <Route path="documents/:documentId" element={<DocumentDetailPage />} />

        <Route
          path="ingest"
          element={
            <RequireRole roles={["analyst", "steward", "admin"]}>
              <IngestPage />
            </RequireRole>
          }
        />
        <Route
          path="ingest/queue"
          element={
            <RequireRole roles={["analyst", "steward", "admin"]}>
              <QueuePage />
            </RequireRole>
          }
        />

        <Route
          path="ops/monitoring"
          element={
            <RequireRole roles={["steward", "admin"]}>
              <MonitoringPage />
            </RequireRole>
          }
        />
        <Route
          path="ops/quality"
          element={
            <RequireRole roles={["analyst", "steward", "admin"]}>
              <QualityPage />
            </RequireRole>
          }
        />
        <Route
          path="ops/inspector"
          element={
            <RequireRole roles={["analyst", "steward", "admin"]}>
              <InspectorPage />
            </RequireRole>
          }
        />
        <Route
          path="ops/governance"
          element={
            <RequireRole roles={["steward", "admin"]}>
              <GovernancePage />
            </RequireRole>
          }
        />
        <Route
          path="ops/access"
          element={
            <RequireRole roles={["steward", "admin"]}>
              <AccessPage />
            </RequireRole>
          }
        />
        <Route
          path="ops/settings"
          element={
            <RequireRole roles={["admin"]}>
              <SettingsPage />
            </RequireRole>
          }
        />

        <Route path="legend" element={<LegendPage />} />
        <Route path="*" element={<NotFoundScreen />} />
      </Route>
    </Routes>
  )
}
