/**
 * The dictation control.
 *
 * Rendered only where the browser supports recognition — see `useDictation`
 * for why hiding beats showing a control that errors on click.
 *
 * While listening it is unmistakably on: filled, in the error colour rather
 * than the brand one, and pulsing. That is not decoration. An open microphone
 * the user has forgotten about is the failure mode worth designing against,
 * and the same colour the app uses for "something is wrong" is the right one
 * for "you are being recorded".
 */
import { MicIcon, SquareIcon } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Hint } from "@/components/ui/tooltip"
import { NOTICE, type Dictation } from "@/lib/speech"

export function MicButton({
  dictation,
  className,
}: {
  dictation: Dictation
  className?: string
}) {
  if (!dictation.supported) return null

  const { listening, toggle } = dictation
  return (
    <Hint
      label={
        listening ? (
          "Stop dictating"
        ) : (
          <span className="block space-y-1">
            <span className="block font-medium">Dictate your question</span>
            <span className="block text-muted-foreground">{NOTICE}</span>
          </span>
        )
      }
    >
      <Button
        type="button"
        size="icon-sm"
        variant={listening ? "default" : "ghost"}
        onClick={toggle}
        aria-pressed={listening}
        className={cn(
          listening && "animate-pulse bg-error text-white hover:bg-error/90",
          className,
        )}
      >
        {listening ? <SquareIcon /> : <MicIcon />}
        {/* The label changes with state so a screen reader announces the
            microphone opening and closing, which is the one thing a sighted
            user gets from the colour and a blind user would not. */}
        <span className="sr-only">{listening ? "Stop dictating" : "Dictate your question"}</span>
      </Button>
    </Hint>
  )
}

/**
 * Words heard but not yet finalised.
 *
 * Shown separately from the input rather than written into it: interim results
 * are revised as the recogniser hears more, so putting them in the field means
 * text that rewrites itself under the cursor — and a stray Enter would submit a
 * half-heard sentence. They land in the field only once final.
 */
export function InterimTranscript({ text }: { text: string }) {
  if (!text.trim()) return null
  return (
    <p className="px-1 pt-1 text-[0.75rem] italic text-muted-foreground" aria-live="polite">
      {text}…
    </p>
  )
}
