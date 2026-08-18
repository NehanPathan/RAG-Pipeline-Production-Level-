import * as React from "react"
import { useMutation } from "@tanstack/react-query"
import { toast } from "sonner"
import { StarIcon } from "lucide-react"
import { api } from "@/api"
import { FEEDBACK_TAGS, type FeedbackTag } from "@/api/types"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/input"
import { Label } from "@/components/ui/misc"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

const TAG_LABEL: Record<FeedbackTag, string> = {
  hallucinated: "Made something up",
  wrong_source: "Cited the wrong source",
  incomplete: "Missed part of the answer",
  outdated: "Used a superseded revision",
  irrelevant: "Not relevant",
  too_slow: "Too slow",
  helpful: "Helpful",
  other: "Something else",
}

/**
 * Feedback capture.
 *
 * The tag vocabulary is closed on the server (`ALLOWED_TAGS`) and anything
 * outside it is silently dropped, so the UI offers exactly those values rather
 * than free text that would vanish. Negative ratings feed the golden dataset a
 * steward can promote, which is why the comment box asks for what was wrong
 * rather than for a general opinion.
 */
export function FeedbackDialog({
  open,
  onOpenChange,
  messageId,
  traceId,
  question,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  messageId?: string
  traceId?: string
  question?: string
}) {
  const [rating, setRating] = React.useState(2)
  const [tags, setTags] = React.useState<FeedbackTag[]>([])
  const [comment, setComment] = React.useState("")

  React.useEffect(() => {
    if (open) {
      setRating(2)
      setTags([])
      setComment("")
    }
  }, [open])

  const submit = useMutation({
    mutationFn: () =>
      api.submitFeedback({
        rating,
        message_id: messageId ?? null,
        trace_id: traceId ?? null,
        comment: comment.trim() || null,
        tags,
      }),
    onSuccess: (result) => {
      toast.success("Feedback recorded", {
        description:
          rating <= 2
            ? "Negative feedback can be promoted into the regression dataset by a steward."
            : undefined,
      })
      if (result.ignored_tags.length) {
        toast.warning(`Ignored unknown tags: ${result.ignored_tags.join(", ")}`)
      }
      onOpenChange(false)
    },
    onError: (error) =>
      toast.error("Could not record feedback", {
        description: error instanceof Error ? error.message : undefined,
      }),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>What went wrong?</DialogTitle>
          <DialogDescription>
            {question ? (
              <>
                About: <span className="text-foreground">“{question}”</span>
              </>
            ) : (
              "Rate this answer so the quality gate can learn from it."
            )}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label>Rating</Label>
            <div className="flex items-center gap-1">
              {[1, 2, 3, 4, 5].map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setRating(value)}
                  aria-label={`${value} out of 5`}
                  className={cn(
                    "rounded-sm p-1 transition-colors",
                    value <= rating ? "text-primary" : "text-muted-foreground/40 hover:text-muted-foreground",
                  )}
                >
                  <StarIcon className={cn("size-5", value <= rating && "fill-current")} />
                </button>
              ))}
              <span className="ml-2 text-xs text-muted-foreground">
                {rating <= 2 ? "Counts as negative" : "Counts as positive"}
              </span>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label>What was the problem?</Label>
            <div className="flex flex-wrap gap-1.5">
              {FEEDBACK_TAGS.map((tag) => {
                const selected = tags.includes(tag)
                return (
                  <button
                    key={tag}
                    type="button"
                    onClick={() =>
                      setTags((current) =>
                        selected ? current.filter((t) => t !== tag) : [...current, tag],
                      )
                    }
                    className={cn(
                      "rounded-sm border px-2 py-1 text-[0.75rem] transition-colors",
                      selected
                        ? "border-primary bg-primary-soft text-primary"
                        : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground",
                    )}
                  >
                    {TAG_LABEL[tag]}
                  </button>
                )
              })}
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="feedback-comment">Detail (optional)</Label>
            <Textarea
              id="feedback-comment"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              maxLength={4000}
              rows={3}
              placeholder="Which sheet should it have used? What did it get wrong?"
            />
          </div>

          {traceId && (
            <p className="font-mono text-[0.625rem] text-muted-foreground">
              trace {traceId}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => submit.mutate()} disabled={submit.isPending}>
            Submit feedback
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
