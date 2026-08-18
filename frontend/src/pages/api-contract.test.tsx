/**
 * The pages, rendered against what the server actually sends.
 *
 * Every other test here mocks `api` with a fixture *I* wrote, which means
 * they all agree with each other and none of them agrees with the server. That
 * gap is not theoretical: the quality page declared `by_tag` required on
 * `GET /feedback/summary`, the endpoint has never sent it, and
 * `Object.keys(undefined)` threw during render — blanking the entire app,
 * because a throw with no error boundary unmounts everything. The unit tests
 * passed the whole time, on a fixture that supplied the field.
 *
 * So the payloads below are **verbatim** from the running API. They are not
 * tidied, not filled in, and not derived from the TypeScript types — the types
 * are the thing under test. When one of these drifts, this fails and the type
 * gets fixed, rather than a user finding it as a black screen.
 *
 * Captured 2026-08-17 against a deployment with no evaluation runs and no
 * feedback, which is the state a fresh install is in and the one every
 * hand-written fixture forgets.
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import { authAs, renderPage } from "@/test/render"

const api = {
  listEvaluationRuns: vi.fn(),
  evaluationGate: vi.fn(),
  onlineMetrics: vi.fn(),
  feedbackSummary: vi.fn(),
  listDatasets: vi.fn(),
  promoteFeedback: vi.fn(),
  startEvaluationRun: vi.fn(),
  listUsers: vi.fn(),
  listProjects: vi.fn(),
  whoAmI: vi.fn(),
  listProjectMembers: vi.fn(),
}

vi.mock("@/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api")>()),
  api: new Proxy({}, { get: (_t, key: string) => api[key as never] }),
}))
vi.mock("@/lib/auth", () => ({ useAuth: () => authAs("admin") }))
vi.mock("recharts", async () => {
  const actual = await vi.importActual<typeof import("recharts")>("recharts")
  return { ...actual, ResponsiveContainer: ({ children }: never) => children }
})

/* -------------------------------------------------- verbatim API responses */

/** GET /evaluation/runs */
const RUNS: unknown[] = []

/** GET /evaluation/gate */
const GATE = {
  dataset: "golden_set_v1",
  status: "no_completed_run",
  passing: null,
  policy_version: "1.0.0",
}

/** GET /evaluation/online?window_hours=24 */
const ONLINE = {
  window_hours: 24,
  sample_count: 9,
  averages: {},
  floors: { faithfulness: 0.75, answer_relevancy: 0.7, context_relevancy: 0.6 },
  breaches: [],
}

/**
 * GET /feedback/summary?window_hours=168
 *
 * Note what is *not* here: `positive`, `by_tag` and `recent`. The endpoint is
 * declared `-> dict` with no response model, so nothing on the server enforces
 * a shape and nothing on the client checks one. This literal is the contract.
 */
const FEEDBACK = {
  window_hours: 168,
  total: 0,
  average_rating: null,
  negative: 0,
  negative_rate: 0.0,
}

/** GET /evaluation/datasets — the client unwraps `{datasets: [...]}`. */
const DATASETS = ["golden_set_v1", "regression_from_feedback"]

/** GET /users — UserOut names four fields and no more. */
const USERS = [
  { user_id: "u1", email: "engineer@example.com", role: "admin", clearance: "restricted" },
]

/**
 * GET /projects and GET /projects/{id}/members.
 *
 * `display_name` is null here on purpose, and that is not an edge case:
 * `users.display_name` is a nullable column, `MemberResponse` declares it
 * `str | None`, and anyone provisioned by Firebase without a profile name has
 * it unset. The frontend type claimed `string`, so nothing flagged
 * `initials(member.display_name)` -- which threw "Cannot read properties of
 * null (reading 'split')" and took the whole access page down.
 */
const PROJECTS = [
  {
    id: "p1",
    project_number: "J-2214",
    name: "Warehouse extension",
    client: null,
    status: "active",
    default_sensitivity: "internal",
    my_role: "owner",
    drawing_count: 3,
    member_count: 2,
  },
]

const MEMBERS = [
  {
    project_id: "p1",
    user_id: "u1",
    project_role: "owner",
    added_at: null,
    email: "engineer@example.com",
    display_name: null,
    platform_role: "admin",
  },
  {
    project_id: "p1",
    user_id: "u2",
    project_role: "reader",
    added_at: "2026-08-01T09:00:00Z",
    email: "checker@example.com",
    display_name: "Priya Raman",
    platform_role: "analyst",
  },
]

/** GET /governance/policy */
const POLICY = {
  policy: {
    version: "1.0.0",
    role_clearance: {
      viewer: "public",
      analyst: "internal",
      steward: "confidential",
      admin: "restricted",
    },
  },
  principal: { user_id: "u1", role: "admin" },
}

beforeEach(() => {
  api.listEvaluationRuns.mockResolvedValue(RUNS)
  api.evaluationGate.mockResolvedValue(GATE)
  api.onlineMetrics.mockResolvedValue(ONLINE)
  api.feedbackSummary.mockResolvedValue(FEEDBACK)
  api.listDatasets.mockResolvedValue(DATASETS)
  api.listUsers.mockResolvedValue(USERS)
  api.listProjects.mockResolvedValue(PROJECTS)
  api.whoAmI.mockResolvedValue(POLICY)
  api.listProjectMembers.mockResolvedValue(MEMBERS)
})

describe("the quality page against real payloads", () => {
  it("renders a deployment that has never been evaluated", async () => {
    const { default: QualityPage } = await import("@/pages/ops-quality")
    renderPage(<QualityPage />)
    expect(await screen.findByText(/no evaluation runs yet/i)).toBeInTheDocument()
  })

  it("survives a feedback summary with no by_tag", async () => {
    // The exact regression. Before the fix this threw
    // "Cannot convert undefined or null to object" and unmounted the app.
    const { default: QualityPage } = await import("@/pages/ops-quality")
    const user = (await import("@testing-library/user-event")).default.setup()
    renderPage(<QualityPage />)

    await user.click(await screen.findByRole("tab", { name: /feedback/i }))
    expect(await screen.findByText(/average rating/i)).toBeInTheDocument()
  })

  it("shows an absent positive count as unknown, not as zero", async () => {
    // The server counts negatives only. A zero here would read as "nobody
    // liked it" on a deployment where nobody has rated anything at all.
    const { default: QualityPage } = await import("@/pages/ops-quality")
    const user = (await import("@testing-library/user-event")).default.setup()
    renderPage(<QualityPage />)

    await user.click(await screen.findByRole("tab", { name: /feedback/i }))
    // Walk up to the tile itself: the label sits in its own element, so
    // `closest("div")` finds only the label's wrapper.
    const label = await screen.findByText(/^positive$/i)
    const tile = label.closest("a, div.rounded-md, div[class*='border']") ?? label.parentElement!
    expect(tile.textContent).toContain("—")
    expect(tile.textContent).not.toMatch(/0/)
  })
})

describe("the access page against real payloads", () => {
  it("renders the roster from a four-field UserOut", async () => {
    const { default: AccessPage } = await import("@/pages/ops-access")
    renderPage(<AccessPage />)
    expect(await screen.findByRole("cell", { name: /engineer@example\.com/i })).toBeInTheDocument()
  })

  it("renders a member whose display_name is null", async () => {
    // The exact regression. Before the fix this threw
    // "Cannot read properties of null (reading 'split')" and blanked the page.
    const { default: AccessPage } = await import("@/pages/ops-access")
    renderPage(<AccessPage />)
    expect(await screen.findByText(/priya raman/i)).toBeInTheDocument()
  })

  it("falls back to the email when there is no display name", async () => {
    // A blank row would be worse than useless on a screen whose whole job is
    // "who can reach what" -- an unnamed member still has to be identifiable.
    const { default: AccessPage } = await import("@/pages/ops-access")
    renderPage(<AccessPage />)
    const names = await screen.findAllByText(/engineer@example\.com/i)
    expect(names.length).toBeGreaterThan(0)
  })

  it("renders the project roster alongside the people table", async () => {
    const { default: AccessPage } = await import("@/pages/ops-access")
    renderPage(<AccessPage />)
    expect(await screen.findByText(/J-2214/)).toBeInTheDocument()
  })
})
