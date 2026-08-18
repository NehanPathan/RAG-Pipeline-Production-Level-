/**
 * Dictation, driven by a fake recogniser.
 *
 * jsdom implements no Web Speech API, so the tests install one. That is not a
 * workaround — it is the only way to exercise the states that matter, because
 * the real API needs a microphone, a network round trip and a human speaking.
 * The fake lets a test say "the user paused mid-sentence", "the browser denied
 * the microphone" or "the tab was closed while listening" directly.
 *
 * The property worth protecting is that **interim text is never submitted**.
 * The recogniser revises what it thinks it heard as it hears more, so a half
 * recognised phrase committed to the input is a question the user did not ask
 * — and one stray Enter sends it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, renderHook } from "@testing-library/react"
import { NOTICE, normaliseTranscript, useDictation } from "@/lib/speech"
import { FakeRecognition, installFakeSpeech, removeFakeSpeech } from "@/test/fake-speech"

beforeEach(installFakeSpeech)
afterEach(removeFakeSpeech)

/* ------------------------------------------------------------- the marks */

describe("spoken piece marks", () => {
  it("writes a spoken dash the way the corpus spells it", () => {
    // "CE 4" retrieves nothing; every schedule and title block says "CE-4".
    expect(normaliseTranscript("what section is ce dash 4")).toBe("what section is CE-4")
  })

  it("handles hyphen as well as dash, and mixed case", () => {
    expect(normaliseTranscript("show BP hyphen 1")).toBe("show BP-1")
    expect(normaliseTranscript("sheet s dash 201")).toBe("sheet S-201")
  })

  it("converts every mark in a sentence", () => {
    expect(normaliseTranscript("compare ce dash 4 with bp dash 1")).toBe("compare CE-4 with BP-1")
  })

  it("leaves a correctly transcribed mark alone", () => {
    expect(normaliseTranscript("what section is CE-4")).toBe("what section is CE-4")
  })

  it.each([
    "sheet 4",
    "grade 50",
    "M 20 bolts",
    "revision 3",
    "page 12",
    "level 2 framing",
    "ISMB 300",
  ])("does not invent a mark in %s", (phrase) => {
    // The wider rule -- join any letters followed by digits -- would rewrite
    // all of these, silently, inside the user's own words. An unchanged
    // transcript is always recoverable; a corrupted one is not.
    expect(normaliseTranscript(phrase)).toBe(phrase)
  })
})

/* -------------------------------------------------------------- the hook */

describe("dictating", () => {
  it("reports support from the browser rather than assuming it", () => {
    const { result } = renderHook(() => useDictation(vi.fn()))
    expect(result.current.supported).toBe(true)
  })

  it("reports no support when the browser has no implementation", () => {
    // Firefox. The UI hides the control entirely on this.
    delete window.SpeechRecognition
    const { result } = renderHook(() => useDictation(vi.fn()))
    expect(result.current.supported).toBe(false)
  })

  it("does not open the microphone until asked", () => {
    renderHook(() => useDictation(vi.fn()))
    expect(FakeRecognition.last).toBeNull()
  })

  it("listens continuously so a pause does not end the session", () => {
    // Without this the first breath ends dictation, which makes saying
    // anything longer than a few words a fight.
    const { result } = renderHook(() => useDictation(vi.fn()))
    act(() => result.current.start())
    expect(FakeRecognition.last?.continuous).toBe(true)
    expect(FakeRecognition.last?.interimResults).toBe(true)
  })

  it("delivers a finished phrase to the caller", () => {
    const onResult = vi.fn()
    const { result } = renderHook(() => useDictation(onResult))
    act(() => result.current.start())
    act(() => FakeRecognition.last!.say("what section is ce dash 4", true))

    expect(onResult).toHaveBeenCalledWith("what section is CE-4")
  })

  it("never delivers a phrase it is still revising", () => {
    // The property this whole design turns on. An interim result committed to
    // the input is a question the user did not ask, and Enter would send it.
    const onResult = vi.fn()
    const { result } = renderHook(() => useDictation(onResult))
    act(() => result.current.start())
    act(() => FakeRecognition.last!.say("what section", false))

    expect(onResult).not.toHaveBeenCalled()
    expect(result.current.interim).toBe("what section")
  })

  it("shows the revision as a preview and clears it once final", () => {
    const onResult = vi.fn()
    const { result } = renderHook(() => useDictation(onResult))
    act(() => result.current.start())
    act(() => FakeRecognition.last!.say("what sect", false))
    expect(result.current.interim).toBe("what sect")

    act(() => FakeRecognition.last!.say("what section is CE-4", true))
    expect(result.current.interim).toBe("")
    expect(onResult).toHaveBeenCalledWith("what section is CE-4")
  })

  it("stops when asked", () => {
    const { result } = renderHook(() => useDictation(vi.fn()))
    act(() => result.current.start())
    const session = FakeRecognition.last!
    act(() => result.current.stop())

    expect(session.started).toBe(false)
    expect(result.current.listening).toBe(false)
  })

  it("toggles rather than stacking sessions", () => {
    const { result } = renderHook(() => useDictation(vi.fn()))
    act(() => result.current.toggle())
    const first = FakeRecognition.last
    act(() => result.current.toggle())
    expect(result.current.listening).toBe(false)
    expect(first!.started).toBe(false)
  })

  it("a second start while running does not open a second microphone", () => {
    const { result } = renderHook(() => useDictation(vi.fn()))
    act(() => result.current.start())
    const first = FakeRecognition.last
    act(() => result.current.start())
    expect(FakeRecognition.last).toBe(first)
  })

  it("releases the microphone when the page goes away", () => {
    // Otherwise the browser keeps its recording indicator lit for a component
    // that no longer exists.
    const { result, unmount } = renderHook(() => useDictation(vi.fn()))
    act(() => result.current.start())
    const session = FakeRecognition.last!
    unmount()
    expect(session.aborted).toBe(true)
  })
})

describe("when it goes wrong", () => {
  it("explains a blocked microphone in terms of the fix", () => {
    // This one never resolves itself -- the user has to change a browser
    // setting -- so the message has to say which setting.
    const { result } = renderHook(() => useDictation(vi.fn()))
    act(() => result.current.start())
    act(() => FakeRecognition.last!.fail("not-allowed"))

    expect(result.current.error).toMatch(/browser settings/i)
    expect(result.current.listening).toBe(false)
  })

  it("stays quiet about a silent pause", () => {
    // "no-speech" fires whenever someone stops to think. Surfacing it would
    // mean a toast every few seconds during ordinary use.
    const { result } = renderHook(() => useDictation(vi.fn()))
    act(() => result.current.start())
    act(() => FakeRecognition.last!.fail("no-speech"))

    expect(result.current.error).toBeNull()
  })

  it("names any other failure rather than failing silently", () => {
    const { result } = renderHook(() => useDictation(vi.fn()))
    act(() => result.current.start())
    act(() => FakeRecognition.last!.fail("network"))

    expect(result.current.error).toMatch(/network/i)
  })
})

describe("the privacy notice", () => {
  it("says plainly that audio leaves the machine", () => {
    // The workspace has clearance levels. Someone dictating a question about a
    // restricted drawing should know the audio reaches a third party, and
    // should learn it before pressing the button rather than after.
    expect(NOTICE.toLowerCase()).toContain("leaves this machine")
  })
})
