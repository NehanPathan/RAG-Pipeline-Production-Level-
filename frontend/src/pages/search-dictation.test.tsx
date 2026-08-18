/**
 * Dictation as it is actually wired into the Search page.
 *
 * The hook and the button each have their own tests, and both passed while
 * nothing connected them. This is the gap those cannot cover: that speaking
 * puts words in *this* page's input, that it adds to what is already typed
 * rather than replacing it, and that a spoken piece mark arrives spelled the
 * way the corpus spells it.
 *
 * Nothing is mocked between the microphone and the field — the real
 * `useDictation`, the real `MicButton` and the real page. Only the recogniser
 * itself is a fake, because jsdom has no Web Speech API and the real one wants
 * a microphone and a person.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { renderPage } from "@/test/render"
import { FakeRecognition, installFakeSpeech, removeFakeSpeech } from "@/test/fake-speech"

const api = {
  getFacets: vi.fn(),
  search: vi.fn(),
}

vi.mock("@/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api")>()),
  api: new Proxy({}, { get: (_t, key: string) => api[key as never] }),
}))

async function load() {
  const { default: SearchPage } = await import("@/pages/search")
  renderPage(<SearchPage />, "/search")
  return screen.findByPlaceholderText(/ISMB 300/i)
}

/** Open the microphone through the real button, then speak. */
async function dictate(phrase: string, { final = true } = {}) {
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: /dictate/i }))
  act(() => FakeRecognition.last!.say(phrase, final))
}

beforeEach(() => {
  installFakeSpeech()
  api.getFacets.mockResolvedValue({ facets: {} })
  api.search.mockResolvedValue({ items: [], total: 0 })
})
afterEach(removeFakeSpeech)

describe("the microphone on the search page", () => {
  it("is offered inside the search field", async () => {
    await load()
    expect(await screen.findByRole("button", { name: /dictate/i })).toBeInTheDocument()
  })

  it("is absent where the browser cannot dictate", async () => {
    // Firefox. The search row still works; it simply has no microphone.
    removeFakeSpeech()
    await load()

    expect(screen.queryByRole("button", { name: /dictate/i })).not.toBeInTheDocument()
    expect(await screen.findByPlaceholderText(/ISMB 300/i)).toBeInTheDocument()
  })

  it("does not open the microphone just by visiting the page", async () => {
    await load()
    expect(FakeRecognition.last).toBeNull()
  })
})

describe("speaking a query", () => {
  it("puts the words in the search field", async () => {
    const input = await load()
    await dictate("base plate detail")

    await waitFor(() => expect(input).toHaveValue("base plate detail"))
  })

  it("spells a spoken piece mark the way the corpus does", async () => {
    // Said aloud this is "C E dash four", and "CE 4" matches nothing: every
    // schedule and title block spells it CE-4.
    const input = await load()
    await dictate("what section is ce dash 4")

    await waitFor(() => expect(input).toHaveValue("what section is CE-4"))
  })

  it("adds to what was already typed instead of replacing it", async () => {
    // Someone who typed half a query and then reached for the microphone means
    // to finish it, not to start again.
    const user = userEvent.setup()
    const input = await load()
    await user.type(input, "base plate")
    await dictate("on sheet s dash 201")

    await waitFor(() => expect(input).toHaveValue("base plate on sheet S-201"))
  })

  it("keeps two dictated phrases from running into one word", async () => {
    const input = await load()
    await dictate("base plate")
    act(() => FakeRecognition.last!.say("detail", true))

    await waitFor(() => expect(input).toHaveValue("base plate detail"))
  })

  it("never writes words it is still revising into the field", async () => {
    // The property the whole design turns on. Interim results are rewritten as
    // the recogniser hears more, so committing one means text that changes
    // under the cursor — and this field submits on Enter.
    const input = await load()
    await dictate("base pl", { final: false })

    expect(input).toHaveValue("")
    // It is shown, just not in the input.
    expect(await screen.findByText(/base pl/)).toBeInTheDocument()
  })

  it("searches for what was dictated once submitted", async () => {
    const user = userEvent.setup()
    const input = await load()
    await dictate("what section is ce dash 4")
    await waitFor(() => expect(input).toHaveValue("what section is CE-4"))

    await user.type(input, "{Enter}")

    await waitFor(() =>
      expect(api.search).toHaveBeenCalledWith(
        expect.objectContaining({ query: "what section is CE-4" }),
      ),
    )
  })
})
