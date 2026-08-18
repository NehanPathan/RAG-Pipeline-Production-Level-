/**
 * The last line of defence between a bug and a blank page.
 *
 * React unmounts the whole tree when a render throws and nothing catches it,
 * which on a dark theme is indistinguishable from a broken deployment: a
 * black rectangle, no sidebar, and reloading changes nothing because the same
 * data throws again. That is not hypothetical -- `GET /feedback/summary` never
 * returned the `by_tag` the quality page's type claimed was required, so
 * `Object.keys(undefined)` threw and the app went black over an empty table.
 *
 * Deliberately a class component: `componentDidCatch` and
 * `getDerivedStateFromError` are the only API React offers for this.
 */
import * as React from "react"
import { AlertTriangleIcon, RotateCcwIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"

interface Props {
  children: React.ReactNode
  /** Changing this resets the boundary — the route path, so navigating away
   *  from a broken page clears the error instead of stranding the user. */
  resetKey?: string
}

interface State {
  error: Error | null
}

export class RouteErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // Kept: the stack is the only thing that identifies which component threw,
    // and it is gone from the UI by design — a component stack means nothing
    // to the person looking at the screen.
    console.error("Route render failed", error, info.componentStack)
  }

  componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null })
    }
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <div className="mx-auto w-full max-w-2xl p-6">
        <Card className="p-6">
          <p className="flex items-center gap-2 text-sm font-semibold tracking-tight">
            <AlertTriangleIcon className="size-4 text-error" />
            This page failed to render
          </p>
          <p className="mt-2 text-pretty text-[0.8125rem] leading-relaxed text-muted-foreground">
            The error is in the page itself, not in your permissions or your
            data. Everything else still works — use the sidebar to carry on, and
            send this message to whoever maintains the app.
          </p>
          {/* The message, verbatim. A paraphrase would lose the one detail
              that makes the report actionable. */}
          <pre className="mt-3 overflow-x-auto rounded-sm border bg-muted/40 p-3 font-mono text-[0.6875rem] leading-relaxed">
            {error.message || String(error)}
          </pre>
          <div className="mt-4 flex gap-2">
            <Button size="sm" variant="outline" onClick={() => this.setState({ error: null })}>
              <RotateCcwIcon />
              Try again
            </Button>
            <Button size="sm" variant="ghost" onClick={() => window.location.reload()}>
              Reload the app
            </Button>
          </div>
        </Card>
      </div>
    )
  }
}
