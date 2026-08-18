/**
 * The quarantine release control.
 *
 * Release means "an authorised human reviewed this and wants ingestion to
 * proceed". The UI's job is to make that a deliberate act and an honest one:
 * show *why* the document was held, confirm before acting, say plainly that
 * the file is screened again, and make it impossible to fire twice by
 * double-clicking.
 *
 * Authorisation is asserted here only as *visibility*. The decision itself is
 * the API's, enforced by `require_role` and covered by backend tests; hiding a
 * button is a courtesy to the user, never a control. Firebase internals are
 * deliberately untouched — the tests supply a role, nothing more.
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Route, Routes } from "react-router-dom"
import { ApiError } from "@/api/client"
import type { Role } from "@/api/types"
import { authAs, renderPage } from "@/test/render"

const api = {
  getDocument: vi.fn(),
  getChunks: vi.fn(),
  getIntelligence: vi.fn(),
  getDocumentJobs: vi.fn(),
  releaseDocument: vi.fn(),
  reprocessDocument: vi.fn(),
  deleteDocument: vi.fn(),
  reclassifyDocument: vi.fn(),
  originalUrl: vi.fn(() => "http://example.test/original"),
}

let role: Role = "admin"

vi.mock("@/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api")>()),
  api: new Proxy({}, { get: (_t, key: string) => api[key as never] }),
}))
vi.mock("@/lib/auth", () => ({ useAuth: () => authAs(role) }))

const QUARANTINE_REASON =
  "The document shows no engineering content: 0 domain term(s) and 0 steel " +
  "designation(s) across 134 words. It has been quarantined for review rather than indexed."

function documentFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: "doc-1",
    file_name: "cake-recipe.txt",
    file_type: "txt",
    status: "quarantined",
    error_message: QUARANTINE_REASON,
    page_count: 1,
    word_count: 134,
    domain: null,
    tags: [],
    indexed_at: null,
    created_at: new Date().toISOString(),
    sensitivity: "internal",
    retention_until: null,
    chunk_count: 0,
    ...overrides,
  }
}

async function load() {
  const { default: DocumentDetailPage } = await import("@/pages/document-detail")
  renderPage(
    <Routes>
      <Route path="/documents/:documentId" element={<DocumentDetailPage />} />
    </Routes>,
    "/documents/doc-1",
  )
  // The banner only exists once the document query has settled.
  await screen.findByText(/quarantined before indexing/i).catch(() => null)
}

beforeEach(() => {
  role = "admin"
  api.getDocument.mockResolvedValue(documentFixture())
  api.getChunks.mockResolvedValue({ items: [], total: 0 })
  api.getIntelligence.mockRejectedValue(new ApiError(404, "none"))
  api.getDocumentJobs.mockResolvedValue([])
  api.releaseDocument.mockResolvedValue({
    document_id: "doc-1",
    job_id: "job-1",
    status: "queued",
    message: "Released. The document re-enters normal ingestion and is screened again.",
  })
})

describe("the quarantine banner", () => {
  it("tells the reviewer why the document was held", async () => {
    await load()
    expect(await screen.findByText(/quarantined before indexing/i)).toBeInTheDocument()
    expect(screen.getByText(/no engineering content/i)).toBeInTheDocument()
  })

  it("does not appear for an indexed document", async () => {
    api.getDocument.mockResolvedValue(documentFixture({ status: "indexed", error_message: null }))
    await load()

    await waitFor(() => expect(api.getDocument).toHaveBeenCalled())
    expect(screen.queryByText(/quarantined before indexing/i)).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /release/i })).not.toBeInTheDocument()
  })
})

describe("authorisation", () => {
  it("offers the release action to an admin", async () => {
    await load()
    expect(await screen.findByRole("button", { name: /release \/ re-ingest/i })).toBeInTheDocument()
  })

  it("offers it to a steward", async () => {
    role = "steward"
    await load()
    expect(await screen.findByRole("button", { name: /release \/ re-ingest/i })).toBeInTheDocument()
  })

  it.each(["analyst", "viewer"] as Role[])("hides it from a %s", async (r) => {
    role = r
    await load()

    // The banner is still shown -- an analyst may see that a document was
    // held, they simply cannot act on it.
    expect(await screen.findByText(/quarantined before indexing/i)).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /release \/ re-ingest/i })).not.toBeInTheDocument()
  })
})

describe("the confirmation flow", () => {
  it("does not call the API until the dialog is confirmed", async () => {
    const user = userEvent.setup()
    await load()

    await user.click(await screen.findByRole("button", { name: /release \/ re-ingest/i }))
    expect(await screen.findByRole("alertdialog")).toBeInTheDocument()
    expect(api.releaseDocument).not.toHaveBeenCalled()
  })

  it("says plainly that release is not an exemption", async () => {
    const user = userEvent.setup()
    await load()

    await user.click(await screen.findByRole("button", { name: /release \/ re-ingest/i }))
    const dialog = await screen.findByRole("alertdialog")
    expect(within(dialog).getByText(/screened again/i)).toBeInTheDocument()
    expect(within(dialog).getByText(/quarantined a second time/i)).toBeInTheDocument()
  })

  it("cancelling leaves the document alone", async () => {
    const user = userEvent.setup()
    await load()

    await user.click(await screen.findByRole("button", { name: /release \/ re-ingest/i }))
    const dialog = await screen.findByRole("alertdialog")
    await user.click(within(dialog).getByRole("button", { name: /cancel/i }))

    expect(api.releaseDocument).not.toHaveBeenCalled()
  })

  it("sends the reason the reviewer typed", async () => {
    const user = userEvent.setup()
    await load()

    await user.click(await screen.findByRole("button", { name: /release \/ re-ingest/i }))
    const dialog = await screen.findByRole("alertdialog")
    await user.type(within(dialog).getByLabelText(/reason/i), "reviewed, it is a transmittal")
    await user.click(within(dialog).getByRole("button", { name: /^release$/i }))

    await waitFor(() =>
      expect(api.releaseDocument).toHaveBeenCalledWith("doc-1", "reviewed, it is a transmittal"),
    )
  })

  it("releases without a reason when none is given", async () => {
    const user = userEvent.setup()
    await load()

    await user.click(await screen.findByRole("button", { name: /release \/ re-ingest/i }))
    const dialog = await screen.findByRole("alertdialog")
    await user.click(within(dialog).getByRole("button", { name: /^release$/i }))

    await waitFor(() => expect(api.releaseDocument).toHaveBeenCalledWith("doc-1", ""))
  })
})

describe("in flight", () => {
  it("a second click cannot fire a second release", async () => {
    const user = userEvent.setup()
    // Never settles, so the mutation stays pending for the whole test.
    api.releaseDocument.mockReturnValue(new Promise(() => {}))
    await load()

    await user.click(await screen.findByRole("button", { name: /release \/ re-ingest/i }))
    const dialog = await screen.findByRole("alertdialog")
    const confirm = within(dialog).getByRole("button", { name: /^release$/i })

    await user.click(confirm)
    await waitFor(() => expect(confirm).toBeDisabled())
    await user.click(confirm)
    await user.click(confirm)

    // Two extra clicks on a disabled control must not queue two more jobs --
    // the API rejects a second one with a 409, but the UI should never send it.
    expect(api.releaseDocument).toHaveBeenCalledTimes(1)
  })
})

describe("outcomes", () => {
  it("refetches the document so the new status appears", async () => {
    const user = userEvent.setup()
    await load()

    const before = api.getDocument.mock.calls.length
    await user.click(await screen.findByRole("button", { name: /release \/ re-ingest/i }))
    const dialog = await screen.findByRole("alertdialog")
    await user.click(within(dialog).getByRole("button", { name: /^release$/i }))

    await waitFor(() =>
      expect(api.getDocument.mock.calls.length).toBeGreaterThan(before),
    )
  })

  it("surfaces a 409 rather than pretending it worked", async () => {
    const user = userEvent.setup()
    api.releaseDocument.mockRejectedValue(
      new ApiError(409, "Document is indexed, not quarantined."),
    )
    await load()

    await user.click(await screen.findByRole("button", { name: /release \/ re-ingest/i }))
    const dialog = await screen.findByRole("alertdialog")
    await user.click(within(dialog).getByRole("button", { name: /^release$/i }))

    expect(await screen.findByText(/could not release/i)).toBeInTheDocument()
  })

  it("surfaces a 403 from the API even though the button was shown", async () => {
    // Hiding the control is a courtesy; the API is the control. If the two
    // ever disagree, the user must see the refusal rather than silence.
    const user = userEvent.setup()
    api.releaseDocument.mockRejectedValue(new ApiError(403, "Role may not perform this action."))
    await load()

    await user.click(await screen.findByRole("button", { name: /release \/ re-ingest/i }))
    const dialog = await screen.findByRole("alertdialog")
    await user.click(within(dialog).getByRole("button", { name: /^release$/i }))

    expect(await screen.findByText(/could not release/i)).toBeInTheDocument()
  })
})
