/**
 * The dictation control as a user meets it.
 *
 * Two things are asserted that the hook's own tests cannot: the button is
 * *absent* where the browser cannot dictate, and while listening it is
 * announced to a screen reader rather than only coloured. An open microphone
 * nobody realises is open is the failure mode worth designing against, and
 * colour alone communicates it to only some of the people using this.
 */
import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { TooltipProvider } from "@/components/ui/tooltip"
import { InterimTranscript, MicButton } from "@/components/domain/mic-button"
import type { Dictation } from "@/lib/speech"

function dictation(overrides: Partial<Dictation> = {}): Dictation {
  return {
    supported: true,
    listening: false,
    interim: "",
    error: null,
    start: vi.fn(),
    stop: vi.fn(),
    toggle: vi.fn(),
    ...overrides,
  }
}

function show(value: Dictation) {
  return render(
    <TooltipProvider>
      <MicButton dictation={value} />
    </TooltipProvider>,
  )
}

describe("the microphone button", () => {
  it("is offered when the browser can dictate", () => {
    show(dictation())
    expect(screen.getByRole("button", { name: /dictate/i })).toBeInTheDocument()
  })

  it("is absent entirely when the browser cannot", () => {
    // Firefox. A control that errors on click is worse than no control -- it
    // reads as a broken product rather than an unsupported browser.
    const { container } = show(dictation({ supported: false }))
    expect(container).toBeEmptyDOMElement()
  })

  it("starts and stops from the same control", async () => {
    const user = userEvent.setup()
    const value = dictation()
    show(value)

    await user.click(screen.getByRole("button", { name: /dictate/i }))
    expect(value.toggle).toHaveBeenCalledTimes(1)
  })

  it("announces that it is listening, not only colours itself", async () => {
    show(dictation({ listening: true }))
    const button = await screen.findByRole("button", { name: /stop dictating/i })
    expect(button).toHaveAttribute("aria-pressed", "true")
  })

  it("is not pressed when idle", () => {
    show(dictation())
    expect(screen.getByRole("button", { name: /dictate/i })).toHaveAttribute(
      "aria-pressed",
      "false",
    )
  })
})

describe("the interim preview", () => {
  it("shows words that are still being revised", () => {
    render(<InterimTranscript text="what section is" />)
    expect(screen.getByText(/what section is/)).toBeInTheDocument()
  })

  it("takes up no room when there is nothing to preview", () => {
    const { container } = render(<InterimTranscript text="   " />)
    expect(container).toBeEmptyDOMElement()
  })

  it("is announced politely rather than interrupting", () => {
    // It updates on almost every syllable; `assertive` would make a screen
    // reader unusable while dictating.
    render(<InterimTranscript text="what section" />)
    expect(screen.getByText(/what section/)).toHaveAttribute("aria-live", "polite")
  })
})
