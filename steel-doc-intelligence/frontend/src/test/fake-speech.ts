/**
 * A `SpeechRecognition` a test can drive.
 *
 * jsdom implements none of the Web Speech API, and the real one needs a
 * microphone, a network round trip and a human speaking — so every dictation
 * test installs this instead. It makes the states that actually matter
 * expressible directly: the user paused mid-sentence, the browser denied the
 * microphone, the tab closed while listening.
 *
 * Shared between the hook's own tests and the page-wiring tests rather than
 * copied into each. Two fakes drifting apart would mean the unit tests and the
 * integration tests agreeing with each other about an API neither matches.
 */

export class FakeRecognition {
  /** The instance most recently constructed — what a test drives. */
  static last: FakeRecognition | null = null

  lang = ""
  continuous = false
  interimResults = false
  maxAlternatives = 1
  started = false
  aborted = false

  onresult: ((event: unknown) => void) | null = null
  onerror: ((event: unknown) => void) | null = null
  onend: (() => void) | null = null
  onstart: (() => void) | null = null

  constructor() {
    FakeRecognition.last = this
  }

  addEventListener() {}
  removeEventListener() {}
  dispatchEvent() {
    return true
  }

  start() {
    this.started = true
  }
  stop() {
    this.started = false
  }
  abort() {
    this.aborted = true
    this.started = false
  }

  /**
   * One phrase from the recogniser.
   *
   * `isFinal: false` is the interim case — words it is still revising, which
   * must never reach the input.
   */
  say(transcript: string, isFinal: boolean) {
    this.onresult?.({
      resultIndex: 0,
      results: {
        length: 1,
        0: { length: 1, isFinal, 0: { transcript, confidence: 0.9 } },
      },
    })
  }

  fail(error: string) {
    this.onerror?.({ error })
  }
}

declare global {
  interface Window {
    SpeechRecognition?: unknown
    webkitSpeechRecognition?: unknown
  }
}

/** Make the browser look like Chrome. Call in `beforeEach`. */
export function installFakeSpeech() {
  FakeRecognition.last = null
  window.SpeechRecognition = FakeRecognition
}

/** Make it look like Firefox, which ships no implementation at all. */
export function removeFakeSpeech() {
  delete window.SpeechRecognition
  delete window.webkitSpeechRecognition
}
