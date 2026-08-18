/**
 * The four states the quality screen has to keep apart.
 *
 * This page was reported as "blank". It was not broken: there were no
 * evaluation runs and it was showing its empty state correctly. What it could
 * not do was tell an un-measured system apart from a failed request, because
 * it had no error branch at all — a 403, a 500 and "nothing measured yet" all
 * rendered the same. These tests pin the distinction.
 *
 * The load-bearing one is `does not render a score for an unmeasured gate`: a
 * fabricated quality number is worse than a blank screen, because a blank
 * screen is obviously missing and a zero looks like a measurement.
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen, waitFor } from "@testing-library/react"
import { ApiError } from "@/api/client"
import { authAs, renderPage } from "@/test/render"

const api = {
  listEvaluationRuns: vi.fn(),
  evaluationGate: vi.fn(),
  onlineMetrics: vi.fn(),
  feedbackSummary: vi.fn(),
  listDatasets: vi.fn(),
  promoteFeedback: vi.fn(),
  startEvaluationRun: vi.fn(),
}

// Partial mock: only `api` is replaced. `@/api` also re-exports `ApiError`,
// which `ErrorState` uses to tell a 403 from a 500 -- replacing the whole
// module leaves that undefined and the error branch throws while rendering
// the very state under test.
vi.mock("@/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api")>()),
  api: new Proxy({}, { get: (_t, key: string) => api[key as never] }),
}))
vi.mock("@/lib/auth", () => ({ useAuth: () => authAs("admin") }))

// Recharts measures its container, which jsdom reports as zero and then warns
// about on every render. The chart is not what any of these assert on.
vi.mock("recharts", async () => {
  const actual = await vi.importActual<typeof import("recharts")>("recharts")
  return { ...actual, ResponsiveContainer: ({ children }: never) => children }
})

const NO_RUN_GATE = {
  dataset: "golden_set_v1",
  status: "no_completed_run",
  passing: null,
  policy_version: "1.0.0",
}

const ONLINE = {
  window_hours: 24,
  sample_count: 0,
  averages: {},
  floors: {},
  breaches: [],
}

const FEEDBACK = {
  window_hours: 168,
  total: 0,
  positive: 0,
  negative: 0,
  average_rating: null,
  by_tag: {},
}

async function load() {
  const { default: QualityPage } = await import("@/pages/ops-quality")
  renderPage(<QualityPage />)
}

beforeEach(() => {
  api.listEvaluationRuns.mockResolvedValue([])
  api.evaluationGate.mockResolvedValue(NO_RUN_GATE)
  api.onlineMetrics.mockResolvedValue(ONLINE)
  api.feedbackSummary.mockResolvedValue(FEEDBACK)
  api.listDatasets.mockResolvedValue(["golden_set_v1"])
})

describe("loading", () => {
  it("shows a skeleton rather than an empty state while the runs are in flight", async () => {
    // Never resolves: the component stays in its pending branch, which is the
    // state under test.
    api.listEvaluationRuns.mockReturnValue(new Promise(() => {}))
    await load()

    expect(screen.queryByText(/no evaluation runs yet/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/something went wrong/i)).not.toBeInTheDocument()
  })
})

describe("nothing measured yet", () => {
  it("says there are no evaluation runs", async () => {
    await load()
    expect(await screen.findByText(/no evaluation runs yet/i)).toBeInTheDocument()
  })

  it("explains that the gate has nothing to judge", async () => {
    await load()
    expect(await screen.findByText(/no completed run for this dataset/i)).toBeInTheDocument()
  })

  it("does not render a score for an unmeasured gate", async () => {
    await load()
    await screen.findByText(/no completed run for this dataset/i)

    // The three ways an unmeasured gate could be misreported: as failing, as
    // passing, or as a zero. None of them is true.
    expect(screen.queryByText(/passing every gated metric/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/below the floor/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/0\.00/)).not.toBeInTheDocument()
  })

  it("distinguishes empty from error", async () => {
    await load()
    await screen.findByText(/no evaluation runs yet/i)
    expect(screen.queryByRole("button", { name: /try again/i })).not.toBeInTheDocument()
  })
})

describe("errors", () => {
  it("renders a permission-denied state for 403, not an empty one", async () => {
    api.listEvaluationRuns.mockRejectedValue(
      new ApiError(403, "Role 'viewer' may not perform this action."),
    )
    await load()

    expect(await screen.findByText(/your role does not permit this/i)).toBeInTheDocument()
    expect(screen.queryByText(/no evaluation runs yet/i)).not.toBeInTheDocument()
  })

  it("renders a no-access state for 404", async () => {
    api.listEvaluationRuns.mockRejectedValue(new ApiError(404, "Not found"))
    await load()

    expect(await screen.findByText(/no access to evaluation runs/i)).toBeInTheDocument()
  })

  it("renders a generic error for 500", async () => {
    api.listEvaluationRuns.mockRejectedValue(new ApiError(500, "Upstream exploded"))
    await load()

    expect(await screen.findByText(/something went wrong/i)).toBeInTheDocument()
    expect(screen.queryByText(/no evaluation runs yet/i)).not.toBeInTheDocument()
  })

  it("offers a retry that refetches", async () => {
    api.listEvaluationRuns.mockRejectedValue(new ApiError(500, "Upstream exploded"))
    await load()

    const retry = await screen.findByRole("button", { name: /try again/i })
    const before = api.listEvaluationRuns.mock.calls.length
    retry.click()

    await waitFor(() =>
      expect(api.listEvaluationRuns.mock.calls.length).toBeGreaterThan(before),
    )
  })

  it("keeps a failing gate from blanking the rest of the page", async () => {
    // One query failing must not take the others with it: a broken gate and a
    // readable run list is a real state, and hiding the runs would lose the
    // only data the user has.
    api.evaluationGate.mockRejectedValue(new ApiError(500, "gate down"))
    await load()

    expect(await screen.findByText(/no evaluation runs yet/i)).toBeInTheDocument()
  })
})
