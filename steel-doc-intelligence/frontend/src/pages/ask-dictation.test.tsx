/**
 * Dictation as it is wired into the Ask composer.
 *
 * The Search page's equivalent of this test found a real omission — the
 * microphone was connected but the interim preview was not, so words appeared
 * only after each phrase finalised and speaking felt broken. Both pages carry
 * the same control, so both get the same check rather than one being trusted
 * because the other passed.
 *
 * The composer is the riskier of the two: it submits on a bare Enter, so a
 * half-recognised phrase reaching the textarea is a question sent to the
 * pipeline that nobody asked.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { authAs, renderPage } from "@/test/render"
import { FakeRecognition, installFakeSpeech, removeFakeSpeech } from "@/test/fake-speech"

const api = {
  listConversations: vi.fn(),
  getConversation: vi.fn(),
  streamChat: vi.fn(),
  deleteConversation: vi.fn(),
  submitFeedback: vi.fn(),
}

vi.mock("@/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api")>()),
  api: new Proxy({}, { get: (_t, key: string) => api[key as never] }),
}))
vi.mock("@/lib/auth", () => ({ useAuth: () => authAs("analyst") }))

async function load() {
  const { default: AskPage } = await import("@/pages/ask")
  renderPage(<AskPage />, "/ask")
  return screen.findByPlaceholderText(/Ask about a dimension/i)
}

async function dictate(phrase: string, { final = true } = {}) {
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: /dictate/i }))
  act(() => FakeRecognition.last!.say(phrase, final))
}

beforeEach(() => {
  installFakeSpeech()
  api.listConversations.mockResolvedValue([])
  api.getConversation.mockResolvedValue({ id: "c1", title: "", turns: [] })
  // Never yields: nothing here asserts on an answer, and a stream that
  // completes would race the assertions about the input.
  api.streamChat.mockImplementation(async function* () {})
})
afterEach(removeFakeSpeech)

describe("the microphone in the composer", () => {
  it("is offered beside the send button", async () => {
    await load()
    expect(await screen.findByRole("button", { name: /dictate/i })).toBeInTheDocument()
  })

  it("is absent where the browser cannot dictate", async () => {
    removeFakeSpeech()
    await load()

    expect(screen.queryByRole("button", { name: /dictate/i })).not.toBeInTheDocument()
    // The composer itself is untouched by the absence.
    expect(await screen.findByPlaceholderText(/Ask about a dimension/i)).toBeInTheDocument()
  })

  it("does not open the microphone just by opening the page", async () => {
    await load()
    expect(FakeRecognition.last).toBeNull()
  })
})

describe("speaking a question", () => {
  it("puts the words in the composer", async () => {
    const input = await load()
    await dictate("which sheet shows the base plate detail")

    await waitFor(() => expect(input).toHaveValue("which sheet shows the base plate detail"))
  })

  it("spells a spoken piece mark the way the corpus does", async () => {
    const input = await load()
    await dictate("what section is ce dash 4")

    await waitFor(() => expect(input).toHaveValue("what section is CE-4"))
  })

  it("adds to a half-typed question instead of replacing it", async () => {
    const user = userEvent.setup()
    const input = await load()
    await user.type(input, "what is the weight of")
    await dictate("bp dash 1")

    await waitFor(() => expect(input).toHaveValue("what is the weight of BP-1"))
  })

  it("never writes words it is still revising into the composer", async () => {
    // This is the one that matters here. The composer sends on Enter, so an
    // interim phrase in the textarea is one keystroke away from being asked.
    const input = await load()
    await dictate("what section is", { final: false })

    expect(input).toHaveValue("")
    expect(await screen.findByText(/what section is/)).toBeInTheDocument()
  })

  it("does not send anything by itself", async () => {
    // Dictation fills the box; the person decides when to ask.
    await load()
    await dictate("what section is ce dash 4")

    await waitFor(() => expect(FakeRecognition.last!.started).toBe(true))
    expect(api.streamChat).not.toHaveBeenCalled()
  })

  it("sends what was dictated when the user presses enter", async () => {
    const user = userEvent.setup()
    const input = await load()
    await dictate("what section is ce dash 4")
    await waitFor(() => expect(input).toHaveValue("what section is CE-4"))

    await user.type(input, "{Enter}")

    await waitFor(() =>
      expect(api.streamChat).toHaveBeenCalledWith(
        expect.objectContaining({ query: "what section is CE-4" }),
        expect.anything(),
      ),
    )
  })
})
