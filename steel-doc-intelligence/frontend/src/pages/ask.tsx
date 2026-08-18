import * as React from "react"
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  ArrowUpIcon,
  BookOpenIcon,
  CircleStopIcon,
  ClockIcon,
  CopyIcon,
  DatabaseZapIcon,
  FileTextIcon,
  LoaderIcon,
  MessagesSquareIcon,
  PanelRightCloseIcon,
  PanelRightOpenIcon,
  PlusIcon,
  RouteIcon,
  SlidersHorizontalIcon,
  SparklesIcon,
  ThumbsDownIcon,
  ThumbsUpIcon,
  Trash2Icon,
} from "lucide-react"
import { api } from "@/api"
import type {
  Citation,
  ChatStreamEvent,
  ConversationMessage,
  ConversationSummary,
} from "@/api/types"
import { cn, formatMs, timeAgo } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import { Kbd, ScrollArea, Separator, Skeleton, Switch, Label } from "@/components/ui/misc"
import { Hint } from "@/components/ui/tooltip"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { AnswerBody, CitationCard, RefusalNotice } from "@/components/domain/answer"
import { EmptyState } from "@/components/domain/states"
import { SheetViewer } from "@/components/domain/sheet-viewer"
import { FeedbackDialog } from "@/components/domain/feedback-dialog"
import { InterimTranscript, MicButton } from "@/components/domain/mic-button"
import { useDictation } from "@/lib/speech"

interface Turn {
  id: string
  role: "user" | "assistant"
  content: string
  citations: Citation[]
  streaming?: boolean
  status?: string
  traceId?: string
  messageId?: string
  latencyMs?: number
  model?: string
  route?: string
  cached?: boolean
  grounded?: boolean
  refused?: boolean
  refusalReason?: string | null
  createdAt: string
}

const STARTERS = [
  {
    icon: FileTextIcon,
    text: "What is the current base plate thickness on CG4-S-104?",
    note: "Resolves the revision family first, then answers from the current issue.",
  },
  {
    icon: DatabaseZapIcon,
    text: "Which drawings reference ISMB 400, and are any of them superseded?",
    note: "Entity search across the corpus, with revision status on every hit.",
  },
  {
    icon: BookOpenIcon,
    text: "What weld procedure applies to plate thicker than 25 mm?",
    note: "Pulls from the general notes and the governing WPS.",
  },
  {
    icon: SparklesIcon,
    text: "Summarise what changed between Rev A and Rev E of CPS-S-101.",
    note: "Reads the revision notes in order rather than diffing geometry.",
  },
]

export default function AskPage() {
  const { conversationId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [turns, setTurns] = React.useState<Turn[]>([])
  const [input, setInput] = React.useState("")
  const [busy, setBusy] = React.useState(false)
  const [activeCitation, setActiveCitation] = React.useState<Citation | null>(null)
  const [showSources, setShowSources] = React.useState(true)
  const [latestOnly, setLatestOnly] = React.useState(true)
  const [feedbackTarget, setFeedbackTarget] = React.useState<Turn | null>(null)

  const abortRef = React.useRef<AbortController | null>(null)
  const scrollRef = React.useRef<HTMLDivElement>(null)
  const inputRef = React.useRef<HTMLTextAreaElement>(null)

  // Appends rather than replaces: someone who typed half a question and then
  // reached for the microphone means to finish it, not to start again. The
  // spacing keeps two dictated phrases from running into one word.
  const dictation = useDictation(
    React.useCallback((phrase: string) => {
      setInput((current) => (current ? `${current.replace(/\s+$/, "")} ${phrase}` : phrase))
      inputRef.current?.focus()
    }, []),
  )
  React.useEffect(() => {
    if (dictation.error) toast.error("Dictation", { description: dictation.error })
  }, [dictation.error])

  const { data: conversations = [] } = useQuery({
    queryKey: ["conversations"],
    queryFn: () => api.listConversations(30),
  })

  const [deleting, setDeleting] = React.useState<ConversationSummary | null>(null)

  const remove = useMutation({
    mutationFn: (conversation: ConversationSummary) =>
      api.deleteConversation(conversation.id),
    onSuccess: (_result, conversation) => {
      void queryClient.invalidateQueries({ queryKey: ["conversations"] })
      queryClient.removeQueries({ queryKey: ["conversation", conversation.id] })
      setDeleting(null)
      toast.success("Removed from your history", {
        description: "The answers and their ratings are kept for quality tracking.",
      })
      // Leaving the user staring at a transcript that is no longer in the
      // sidebar would be a dead end, so navigate off it — but only if it is
      // the one they just removed.
      if (conversation.id === conversationId) navigate("/ask")
    },
    onError: (error) =>
      toast.error("Could not remove the conversation", {
        description: error instanceof Error ? error.message : undefined,
      }),
  })

  const { data: history, isPending: historyPending } = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () => api.getConversation(conversationId!),
    enabled: Boolean(conversationId),
  })

  // Loading a saved conversation replaces the transcript wholesale; merging
  // would interleave a stored exchange with a live one.
  React.useEffect(() => {
    if (!conversationId) {
      setTurns([])
      return
    }
    if (!history) return
    setTurns(history.map(toTurn))
  }, [conversationId, history])

  // A question handed over from the command palette runs immediately.
  const pendingQuery = searchParams.get("q")
  React.useEffect(() => {
    if (!pendingQuery) return
    setSearchParams({}, { replace: true })
    void send(pendingQuery)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingQuery])

  React.useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" })
  }, [turns])

  async function send(question: string) {
    const trimmed = question.trim()
    if (!trimmed || busy) return

    setInput("")
    setBusy(true)
    setActiveCitation(null)

    const userTurn: Turn = {
      id: `u-${Date.now()}`,
      role: "user",
      content: trimmed,
      citations: [],
      createdAt: new Date().toISOString(),
    }
    const assistantId = `a-${Date.now()}`
    setTurns((prev) => [
      ...prev,
      userTurn,
      {
        id: assistantId,
        role: "assistant",
        content: "",
        citations: [],
        streaming: true,
        status: "Starting",
        createdAt: new Date().toISOString(),
      },
    ])

    const controller = new AbortController()
    abortRef.current = controller

    const patch = (updater: (turn: Turn) => Turn) =>
      setTurns((prev) => prev.map((t) => (t.id === assistantId ? updater(t) : t)))

    try {
      const stream = api.streamChat(
        {
          query: trimmed,
          conversation_id: conversationId ?? null,
          filters: latestOnly ? {} : { include_superseded: true },
          stream: true,
        },
        controller.signal,
      )

      for await (const event of stream as AsyncGenerator<ChatStreamEvent>) {
        if (event.type === "status") {
          patch((t) => ({ ...t, status: event.message ?? event.stage }))
        } else if (event.type === "citations") {
          patch((t) => ({ ...t, citations: event.citations }))
        } else if (event.type === "token") {
          patch((t) => ({ ...t, content: t.content + event.content, status: undefined }))
        } else if (event.type === "error") {
          patch((t) => ({
            ...t,
            streaming: false,
            status: undefined,
            content: t.content || `The pipeline reported an error: ${event.message}`,
          }))
        } else if (event.type === "done") {
          patch((t) => ({
            ...t,
            streaming: false,
            status: undefined,
            content: event.answer ?? t.content,
            citations: event.citations ?? t.citations,
            traceId: event.trace_id,
            messageId: event.message_id,
            latencyMs: event.latency_ms,
            model: event.model_used,
            route: event.route,
            cached: event.cached,
            grounded: event.grounded,
            refused: event.refused,
            refusalReason: event.refusal_reason ?? null,
          }))
          if (event.conversation_id && !conversationId) {
            navigate(`/ask/${event.conversation_id}`, { replace: true })
            void queryClient.invalidateQueries({ queryKey: ["conversations"] })
          }
        }
      }
    } catch (error) {
      if (controller.signal.aborted) {
        patch((t) => ({ ...t, streaming: false, status: undefined }))
      } else {
        patch((t) => ({
          ...t,
          streaming: false,
          status: undefined,
          content:
            t.content ||
            `Could not reach the answering service. ${error instanceof Error ? error.message : ""}`,
        }))
      }
    } finally {
      abortRef.current = null
      setBusy(false)
      inputRef.current?.focus()
    }
  }

  function stop() {
    abortRef.current?.abort()
    setBusy(false)
  }

  const lastAssistant = [...turns].reverse().find((t) => t.role === "assistant" && !t.streaming)
  const sourceDocs = lastAssistant?.citations ?? []

  const { data: previewDoc } = useQuery({
    queryKey: ["document", activeCitation?.document_id],
    queryFn: () => api.getDocument(activeCitation!.document_id!),
    enabled: Boolean(activeCitation?.document_id),
  })

  return (
    <div className="flex h-[calc(100svh-3.5rem)] min-h-0">
      {/* -------------------------------------------- conversation history */}
      <aside className="hidden w-60 shrink-0 flex-col border-r border-border xl:flex">
        <div className="flex items-center justify-between px-3 py-2.5">
          <span className="eyebrow">Conversations</span>
          <Hint label="New conversation">
            <Button variant="ghost" size="icon-xs" onClick={() => navigate("/ask")}>
              <PlusIcon />
            </Button>
          </Hint>
        </div>
        <Separator />
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-0.5 p-1.5">
            {conversations.length === 0 ? (
              <p className="px-2 py-6 text-center text-[0.6875rem] leading-relaxed text-muted-foreground">
                No conversations yet. Anything you ask is kept here with its
                citations and trace id.
              </p>
            ) : (
              conversations.map((conversation) => (
                <ConversationRow
                  key={conversation.id}
                  conversation={conversation}
                  active={conversation.id === conversationId}
                  deleting={deleting?.id === conversation.id && remove.isPending}
                  onDelete={() => setDeleting(conversation)}
                />
              ))
            )}
          </div>
        </ScrollArea>
      </aside>

      <AlertDialog
        open={Boolean(deleting)}
        onOpenChange={(open) => !open && !remove.isPending && setDeleting(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove this conversation?</AlertDialogTitle>
            <AlertDialogDescription className="space-y-2">
              <span className="block">
                “{deleting?.title}” will disappear from your history.
              </span>
              {/* Said plainly rather than implied. Calling this "delete
                  permanently" would be a lie, and the reason it is not a hard
                  delete is one a user can reasonably want to know. */}
              <span className="block text-[0.6875rem] leading-relaxed">
                The messages themselves are retained. Any ratings you gave those
                answers feed the quality gate and the regression dataset, and
                erasing the conversation would quietly take them with it.
              </span>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={remove.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={remove.isPending}
              onClick={(event) => {
                // Radix closes the dialog on action click by default; the
                // mutation decides when to close so a failure keeps the
                // dialog open with its error rather than vanishing.
                event.preventDefault()
                if (deleting) remove.mutate(deleting)
              }}
            >
              {remove.isPending && <LoaderIcon className="animate-spin" />}
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* ------------------------------------------------------- transcript */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-2">
          <MessagesSquareIcon className="size-4 shrink-0 text-muted-foreground" />
          <span className="min-w-0 truncate text-[0.8125rem] font-medium">
            {conversations.find((c) => c.id === conversationId)?.title ?? "New question"}
          </span>
          <div className="ml-auto flex items-center gap-2">
            <Hint label="Superseded revisions are excluded by default. Answering from an old sheet is worse than not answering.">
              <Label className="cursor-pointer gap-1.5 text-[0.6875rem] text-muted-foreground">
                <Switch checked={latestOnly} onCheckedChange={setLatestOnly} />
                Current revisions only
              </Label>
            </Hint>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => setShowSources((s) => !s)}
              aria-label={showSources ? "Hide sources" : "Show sources"}
              className="hidden lg:inline-flex"
            >
              {showSources ? <PanelRightCloseIcon /> : <PanelRightOpenIcon />}
            </Button>
          </div>
        </div>

        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-3xl px-4 py-6">
            {historyPending && conversationId ? (
              <div className="space-y-6">
                <Skeleton className="ml-auto h-9 w-2/3 rounded-lg" />
                <div className="space-y-2">
                  <Skeleton className="h-3 w-full" />
                  <Skeleton className="h-3 w-full" />
                  <Skeleton className="h-3 w-4/5" />
                </div>
              </div>
            ) : turns.length === 0 ? (
              <Welcome onPick={(text) => send(text)} />
            ) : (
              <div className="space-y-6">
                {turns.map((turn) =>
                  turn.role === "user" ? (
                    <div key={turn.id} className="flex justify-end">
                      <div className="max-w-[85%] rounded-lg rounded-br-sm border border-border bg-muted/70 px-3.5 py-2.5 text-[0.9375rem] leading-relaxed">
                        {turn.content}
                      </div>
                    </div>
                  ) : (
                    <AssistantTurn
                      key={turn.id}
                      turn={turn}
                      activeCitationIndex={activeCitation?.index ?? null}
                      onCite={(index) => {
                        const citation = turn.citations.find((c) => c.index === index)
                        if (citation) {
                          setActiveCitation(citation)
                          setShowSources(true)
                        }
                      }}
                      onFeedback={() => setFeedbackTarget(turn)}
                    />
                  ),
                )}
              </div>
            )}
          </div>
        </div>

        {/* ---------------------------------------------------- composer */}
        <div className="shrink-0 border-t border-border bg-background/90 px-4 py-3 backdrop-blur">
          <div className="mx-auto w-full max-w-3xl">
            <div className="relative rounded-lg border border-border bg-card transition-colors focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/20">
              <Textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault()
                    void send(input)
                  }
                }}
                rows={1}
                placeholder="Ask about a dimension, a grade, a connection detail or a revision…"
                className="max-h-40 min-h-11 resize-none border-0 bg-transparent py-3 pl-3.5 pr-24 shadow-none focus-visible:ring-0"
              />
              <div className="absolute bottom-2 right-2 flex items-center gap-1.5">
                <MicButton dictation={dictation} />
                {busy ? (
                  <Button size="sm" variant="outline" onClick={stop}>
                    <CircleStopIcon />
                    Stop
                  </Button>
                ) : (
                  <Button size="icon-sm" onClick={() => send(input)} disabled={!input.trim()}>
                    <ArrowUpIcon />
                    <span className="sr-only">Send</span>
                  </Button>
                )}
              </div>
            </div>
            <InterimTranscript text={dictation.interim} />
            <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-[0.6875rem] text-muted-foreground">
              <span className="flex items-center gap-1">
                <Kbd>↵</Kbd> send
                <Kbd>⇧↵</Kbd> newline
              </span>
              <span>Answers are grounded in the corpus and cite the sheet and page they came from.</span>
            </p>
          </div>
        </div>
      </div>

      {/* ----------------------------------------------------- source rail */}
      {showSources && (
        <aside className="hidden w-[26rem] shrink-0 flex-col border-l border-border lg:flex xl:w-[30rem]">
          <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2.5">
            <span className="eyebrow">Sources</span>
            {sourceDocs.length > 0 && (
              <Badge variant="muted" className="font-mono">
                {sourceDocs.length}
              </Badge>
            )}
            <Button asChild variant="ghost" size="xs" className="ml-auto">
              <Link to="/ops/inspector">
                <SlidersHorizontalIcon />
                Inspect
              </Link>
            </Button>
          </div>

          <ScrollArea className="min-h-0 flex-1">
            <div className="space-y-3 p-3">
              {sourceDocs.length === 0 ? (
                <EmptyState
                  compact
                  icon={FileTextIcon}
                  title="No sources yet"
                  description="Ask a question and the passages the answer was built from will appear here, each linked to its page and region."
                />
              ) : (
                <>
                  {activeCitation && previewDoc && (
                    <div className="space-y-2">
                      <p className="eyebrow">Cited region</p>
                      <SheetViewer
                        document={previewDoc}
                        className="h-72"
                        page={activeCitation.page_number ?? 1}
                        highlights={
                          activeCitation.regions?.length
                            ? [
                                {
                                  id: activeCitation.chunk_id ?? "active",
                                  regions: activeCitation.regions,
                                  precision: "block",
                                  index: activeCitation.index,
                                },
                              ]
                            : []
                        }
                        activeHighlightId={activeCitation.chunk_id ?? "active"}
                      />
                    </div>
                  )}
                  <div className="space-y-2">
                    {sourceDocs.map((citation) => (
                      <CitationCard
                        key={`${citation.index}-${citation.chunk_id}`}
                        citation={citation}
                        active={activeCitation?.index === citation.index}
                        onClick={() =>
                          setActiveCitation((current) =>
                            current?.index === citation.index ? null : citation,
                          )
                        }
                      />
                    ))}
                  </div>
                </>
              )}
            </div>
          </ScrollArea>
        </aside>
      )}

      <FeedbackDialog
        open={Boolean(feedbackTarget)}
        onOpenChange={(open) => !open && setFeedbackTarget(null)}
        messageId={feedbackTarget?.messageId}
        traceId={feedbackTarget?.traceId}
        question={
          turns[turns.findIndex((t) => t.id === feedbackTarget?.id) - 1]?.content ?? ""
        }
      />
    </div>
  )
}

/* ------------------------------------------------------------- fragments */

/**
 * One row in the history sidebar.
 *
 * The delete control is a sibling of the link, not nested inside it — a
 * button inside an anchor is invalid HTML and behaves inconsistently across
 * browsers on keyboard activation.
 *
 * It sits at a low opacity and comes up to full on hover or keyboard focus.
 * It used to be fully hidden until hover, which cost more than the tidiness
 * it bought: users reported the app had no way to delete a conversation, and
 * on a touch device — where no hover event ever fires — that was literally
 * true, because nothing could bring the button into reach.
 */
function ConversationRow({
  conversation,
  active,
  deleting,
  onDelete,
}: {
  conversation: ConversationSummary
  active: boolean
  deleting: boolean
  onDelete: () => void
}) {
  return (
    <div
      className={cn(
        "group relative rounded-md transition-colors",
        active ? "bg-primary-soft text-primary" : "hover:bg-muted",
        deleting && "opacity-50",
      )}
    >
      <Link to={`/ask/${conversation.id}`} className="block px-2 py-1.5 pr-8">
        <span className="line-clamp-2 text-[0.75rem] leading-snug">
          {conversation.title}
        </span>
        <span className="mt-0.5 block font-mono text-[0.625rem] tabular-nums text-muted-foreground">
          {conversation.message_count} msg · {timeAgo(conversation.updated_at)}
        </span>
      </Link>

      <Hint label="Remove from history">
        <Button
          variant="ghost"
          size="icon-xs"
          disabled={deleting}
          onClick={onDelete}
          aria-label={`Remove conversation: ${conversation.title}`}
          className={cn(
            // Faint rather than hidden. Revealing it only on `group-hover`
            // read as "there is no delete" to a user looking straight at the
            // list, and on a touch device it was worse than invisible: there
            // is no hover to trigger, so the control could not be reached at
            // all. A third of an opacity still keeps a column of bins from
            // competing with the titles, which is what hiding it was for.
            "absolute right-1 top-1 text-muted-foreground opacity-35 transition-opacity",
            "hover:bg-destructive/10 hover:text-destructive",
            "group-hover:opacity-100 focus-visible:opacity-100",
            deleting && "opacity-100",
          )}
        >
          {deleting ? <LoaderIcon className="animate-spin" /> : <Trash2Icon />}
        </Button>
      </Hint>
    </div>
  )
}

function AssistantTurn({
  turn,
  activeCitationIndex,
  onCite,
  onFeedback,
}: {
  turn: Turn
  activeCitationIndex: number | null
  onCite: (index: number) => void
  onFeedback: () => void
}) {
  return (
    <div className="space-y-2.5">
      {turn.status && (
        <div className="flex items-center gap-2 text-[0.75rem] text-muted-foreground">
          <span className="relative flex size-1.5">
            <span className="absolute inline-flex size-full animate-pulse-ring rounded-full bg-primary opacity-70" />
            <span className="relative inline-flex size-1.5 rounded-full bg-primary" />
          </span>
          {turn.status}
        </div>
      )}

      {turn.refused ? (
        <RefusalNotice reason={turn.refusalReason} />
      ) : (
        turn.content && (
          <AnswerBody
            content={turn.content}
            citations={turn.citations}
            onCite={onCite}
            activeCite={activeCitationIndex}
            streaming={turn.streaming}
          />
        )
      )}

      {!turn.streaming && turn.content && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 pt-1">
          {turn.latencyMs != null && (
            <Hint label="End-to-end, including retrieval, rerank and generation.">
              <span className="flex items-center gap-1 font-mono text-[0.6875rem] tabular-nums text-muted-foreground">
                <ClockIcon className="size-3" />
                {formatMs(turn.latencyMs)}
              </span>
            </Hint>
          )}
          {turn.model && (
            <span className="font-mono text-[0.6875rem] text-muted-foreground">{turn.model}</span>
          )}
          {turn.route && (
            <Hint label="Which path answered: `rag` means it was built from retrieved documents.">
              <Badge variant="outline" className="font-mono">
                <RouteIcon className="size-3" />
                {turn.route}
              </Badge>
            </Hint>
          )}
          {turn.cached && (
            <Hint label="Served from the semantic answer cache — no model call was made.">
              <Badge variant="technical">cached</Badge>
            </Hint>
          )}
          {turn.grounded === false && (
            <Hint label="No document backed this answer. Treat it as model knowledge, not as a fact from your corpus.">
              <Badge variant="warn">ungrounded</Badge>
            </Hint>
          )}

          <div className="ml-auto flex items-center gap-0.5">
            <Hint label="Copy answer">
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => {
                  void navigator.clipboard.writeText(turn.content)
                  toast.success("Answer copied")
                }}
              >
                <CopyIcon />
              </Button>
            </Hint>
            <Hint label="This answer was useful">
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => {
                  void api.submitFeedback({
                    rating: 5,
                    message_id: turn.messageId,
                    trace_id: turn.traceId,
                    tags: ["helpful"],
                  })
                  toast.success("Thanks — logged as helpful")
                }}
              >
                <ThumbsUpIcon />
              </Button>
            </Hint>
            <Hint label="Report a problem with this answer">
              <Button variant="ghost" size="icon-xs" onClick={onFeedback}>
                <ThumbsDownIcon />
              </Button>
            </Hint>
          </div>
        </div>
      )}

      {turn.traceId && (
        <p className="font-mono text-[0.625rem] text-muted-foreground/70">
          trace {turn.traceId}
        </p>
      )}
    </div>
  )
}

function Welcome({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="space-y-6 py-8">
      <div className="space-y-2">
        <h1 className="text-balance text-xl font-semibold tracking-tight">
          Ask the corpus
        </h1>
        <p className="max-w-lg text-pretty text-sm leading-relaxed text-muted-foreground">
          Questions are answered from the drawings, specifications and correspondence you have
          access to. Every claim carries a citation to a page and a region of a specific revision
          — and superseded sheets are excluded unless you ask for them.
        </p>
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        {STARTERS.map((starter) => {
          const Icon = starter.icon
          return (
            <button
              key={starter.text}
              onClick={() => onPick(starter.text)}
              className="group flex flex-col gap-1.5 rounded-lg border border-border bg-card p-3 text-left transition-colors hover:border-primary/45 hover:bg-muted/40"
            >
              <span className="flex items-center gap-2">
                <Icon className="size-3.5 shrink-0 text-muted-foreground transition-colors group-hover:text-primary" />
                <span className="text-[0.8125rem] font-medium leading-snug">{starter.text}</span>
              </span>
              <span className="pl-5.5 text-[0.6875rem] leading-relaxed text-muted-foreground">
                {starter.note}
              </span>
            </button>
          )
        })}
      </div>

      <Card className="bg-muted/40 p-3">
        <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
          <b className="text-foreground">A note on dimensions.</b> A value read from a DXF is the
          CAD value. On a plotted sheet it is the string the drafter's system rendered, and on a
          scan it is OCR's reading of that string. Every citation says which, and the answer will
          not present the three as equals.
        </p>
      </Card>
    </div>
  )
}

function toTurn(message: ConversationMessage): Turn {
  return {
    id: message.id,
    role: message.role === "assistant" ? "assistant" : "user",
    content: message.content,
    citations: message.citations ?? [],
    traceId: message.trace_id,
    messageId: message.id,
    latencyMs: message.latency_ms,
    model: message.model_used,
    createdAt: message.created_at,
  }
}
