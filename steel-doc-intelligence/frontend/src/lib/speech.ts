/**
 * Dictating a query instead of typing it.
 *
 * Uses the browser's own `SpeechRecognition`: no backend, no API key, no
 * per-minute bill and no audio upload through our stack. A Whisper endpoint
 * would mean all four, plus a round trip before the first word appears.
 *
 * **Where the audio goes.** Chrome and Edge stream it to the vendor's speech
 * service; only newer Safari recognises on device. In a workspace with
 * clearance levels that is worth stating, so the microphone opens only on an
 * explicit click and `NOTICE` sits beside the control.
 *
 * Firefox ships no implementation, so the hook reports `supported: false` and
 * the UI hides the control -- a microphone that errors on click is worse than
 * none.
 */
import * as React from "react"

export const NOTICE =
  "Your browser transcribes speech using its vendor's speech service, so audio leaves this machine."

/* -------------------------------------------------------------- typings */

/**
 * The API predates its own standardisation and is absent from TypeScript's DOM
 * library, so the surface actually used is declared here rather than reached
 * for through `any`.
 */
interface SpeechRecognitionAlternative {
  transcript: string
  confidence: number
}
interface SpeechRecognitionResult {
  readonly length: number
  isFinal: boolean
  [index: number]: SpeechRecognitionAlternative
}
interface SpeechRecognitionResultList {
  readonly length: number
  [index: number]: SpeechRecognitionResult
}
interface SpeechRecognitionEventLike extends Event {
  resultIndex: number
  results: SpeechRecognitionResultList
}
interface SpeechRecognitionErrorEventLike extends Event {
  error: string
}
interface SpeechRecognitionLike extends EventTarget {
  lang: string
  continuous: boolean
  interimResults: boolean
  maxAlternatives: number
  start(): void
  stop(): void
  abort(): void
  onresult: ((event: SpeechRecognitionEventLike) => void) | null
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null
  onend: (() => void) | null
  onstart: (() => void) | null
}
type SpeechRecognitionCtor = new () => SpeechRecognitionLike

function recogniser(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor
    webkitSpeechRecognition?: SpeechRecognitionCtor
  }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

/* ---------------------------------------------------------- normalising */

/**
 * Spoken piece marks, written the way the corpus spells them.
 *
 * Read aloud, `CE-4` is "C E dash four", and the recogniser returns "ce dash
 * 4" — which matches nothing, because every schedule, every title block and
 * the `_PIECE_MARK_RE` the router uses all spell it `CE-4`. One substitution
 * turns a query that retrieves nothing into one that retrieves the row.
 *
 * Deliberately narrow: it fires only on an explicit spoken "dash" or "hyphen"
 * between letters and digits, which is unambiguous. The tempting wider rule —
 * join any letters followed by a number — would rewrite "sheet 4" to "SHEET-4",
 * "grade 50" to "GRADE-50" and "M 20" to "M-20", all wrong, and it would do so
 * silently inside the user's own words. Leaving a transcript alone is always
 * recoverable; corrupting it is not, and the same preference for "unchanged"
 * over "confidently wrong" runs through the rest of this system.
 */
export function normaliseTranscript(text: string): string {
  return text.replace(
    /\b([a-z]{1,4})\s*(?:dash|hyphen)\s*(\d{1,4})\b/gi,
    (_match, letters: string, digits: string) => `${letters.toUpperCase()}-${digits}`,
  )
}

/* ---------------------------------------------------------------- hook */

export interface Dictation {
  supported: boolean
  listening: boolean
  /** Words recognised but not yet final — shown as a preview, never submitted. */
  interim: string
  error: string | null
  start(): void
  stop(): void
  toggle(): void
}

/**
 * @param onResult receives each finalised phrase, already normalised. The
 *   caller decides where it goes — appending to an existing draft rather than
 *   replacing it, so dictation adds to what someone typed instead of wiping it.
 */
export function useDictation(onResult: (text: string) => void): Dictation {
  const [listening, setListening] = React.useState(false)
  const [interim, setInterim] = React.useState("")
  const [error, setError] = React.useState<string | null>(null)
  const session = React.useRef<SpeechRecognitionLike | null>(null)

  // Held in a ref so a new closure over the parent's input state does not mean
  // tearing down and restarting recognition on every keystroke.
  const handler = React.useRef(onResult)
  React.useEffect(() => {
    handler.current = onResult
  }, [onResult])

  const supported = React.useMemo(() => recogniser() !== null, [])

  const stop = React.useCallback(() => {
    session.current?.stop()
    session.current = null
    setListening(false)
    setInterim("")
  }, [])

  const start = React.useCallback(() => {
    const Ctor = recogniser()
    if (!Ctor || session.current) return

    const instance = new Ctor()
    instance.lang = navigator.language || "en-US"
    // Keeps listening through the pauses in a spoken sentence. Without it the
    // first breath ends the session, which makes dictating anything longer
    // than a few words a fight.
    instance.continuous = true
    instance.interimResults = true
    instance.maxAlternatives = 1

    instance.onresult = (event) => {
      let pending = ""
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i]
        const phrase = result[0]?.transcript ?? ""
        if (result.isFinal) {
          const cleaned = normaliseTranscript(phrase).trim()
          if (cleaned) handler.current(cleaned)
        } else {
          pending += phrase
        }
      }
      setInterim(normaliseTranscript(pending))
    }

    instance.onerror = (event) => {
      // Reported by name rather than swallowed: "no-speech" after a silent
      // pause is ordinary and self-correcting, while a denied microphone needs
      // the user to change a browser setting and will never fix itself.
      if (event.error === "no-speech" || event.error === "aborted") return
      setError(
        event.error === "not-allowed" || event.error === "service-not-allowed"
          ? "Microphone access is blocked. Allow it for this site in your browser settings."
          : `Dictation stopped: ${event.error.replace(/-/g, " ")}.`,
      )
      stop()
    }

    instance.onend = () => {
      session.current = null
      setListening(false)
      setInterim("")
    }

    setError(null)
    session.current = instance
    try {
      instance.start()
      setListening(true)
    } catch {
      // `start()` throws when called while already running. Nothing to report:
      // the session the user asked for is the one already in progress.
      session.current = null
    }
  }, [stop])

  // A recogniser left running after the composer unmounts holds the microphone
  // open, and the browser keeps showing a recording indicator for a page that
  // is gone.
  React.useEffect(() => () => session.current?.abort(), [])

  const toggle = React.useCallback(() => {
    if (listening) stop()
    else start()
  }, [listening, start, stop])

  return { supported, listening, interim, error, start, stop, toggle }
}
