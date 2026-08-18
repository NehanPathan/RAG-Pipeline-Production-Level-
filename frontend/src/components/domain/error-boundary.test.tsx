/**
 * What a page crash should look like.
 *
 * Without a boundary React unmounts the whole tree, which on a dark theme is a
 * black rectangle with no sidebar and no message — indistinguishable from a
 * broken deployment, and unchanged by reloading. These pin the three things
 * that make the difference: the shell survives, the actual error is shown, and
 * navigating away clears it.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { RouteErrorBoundary } from "@/components/domain/error-boundary"

function Boom(): React.ReactElement {
  throw new Error("Cannot convert undefined or null to object")
}

beforeEach(() => {
  // React logs the caught error itself; the boundary logs the component stack.
  // Neither is a test failure, and both bury the real output.
  vi.spyOn(console, "error").mockImplementation(() => {})
})
afterEach(() => vi.restoreAllMocks())

describe("a page that throws", () => {
  it("does not take the rest of the app with it", () => {
    render(
      <div>
        <nav>Sidebar</nav>
        <RouteErrorBoundary resetKey="/ops/quality">
          <Boom />
        </RouteErrorBoundary>
      </div>,
    )
    expect(screen.getByText("Sidebar")).toBeInTheDocument()
    expect(screen.getByText(/this page failed to render/i)).toBeInTheDocument()
  })

  it("shows the actual error rather than a generic apology", () => {
    render(
      <RouteErrorBoundary>
        <Boom />
      </RouteErrorBoundary>,
    )
    expect(
      screen.getByText(/cannot convert undefined or null to object/i),
    ).toBeInTheDocument()
  })

  it("says the problem is the page, not the user's permissions", () => {
    // The failure mode this replaces was routinely misread as "my account
    // cannot see this page".
    render(
      <RouteErrorBoundary>
        <Boom />
      </RouteErrorBoundary>,
    )
    expect(screen.getByText(/not in your permissions/i)).toBeInTheDocument()
  })

  it("clears when the route changes", () => {
    const { rerender } = render(
      <RouteErrorBoundary resetKey="/ops/quality">
        <Boom />
      </RouteErrorBoundary>,
    )
    expect(screen.getByText(/this page failed to render/i)).toBeInTheDocument()

    rerender(
      <RouteErrorBoundary resetKey="/ops/access">
        <p>The next page</p>
      </RouteErrorBoundary>,
    )
    expect(screen.getByText("The next page")).toBeInTheDocument()
    expect(screen.queryByText(/this page failed to render/i)).not.toBeInTheDocument()
  })

  it("renders its children untouched when nothing throws", () => {
    render(
      <RouteErrorBoundary>
        <p>Ordinary page</p>
      </RouteErrorBoundary>,
    )
    expect(screen.getByText("Ordinary page")).toBeInTheDocument()
  })
})
