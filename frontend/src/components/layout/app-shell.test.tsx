/**
 * The collapsed rail.
 *
 * Reported as "icons all mixed up", and it was four separate faults stacked in
 * one branch of one component: the icon column was padded but not centred, so
 * every icon sat left of the wordmark above it; a group separator rendered
 * before the *first* group, putting a stray rule under the logo; the Reference
 * item had no separator at all, so it merged into the group above; and the
 * collapse button at the foot was left-aligned for the same reason as the
 * icons. All four came from serving two different layouts through one tree of
 * conditional classes.
 *
 * jsdom computes no layout, so these cannot assert pixel positions. What they
 * can pin is the structure the layout depends on — a centred flex column,
 * separators only between groups — and the thing that stays broken however the
 * pixels land: an icon-only link with no accessible name.
 */
import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { TooltipProvider } from "@/components/ui/tooltip"
import { ThemeProvider } from "@/lib/theme"
import { authAs } from "@/test/render"
import { AppShell } from "@/components/layout/app-shell"

vi.mock("@/lib/auth", () => ({ useAuth: () => authAs("admin") }))

const SIDEBAR_KEY = "girder.sidebar.collapsed"

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <TooltipProvider>
          <MemoryRouter initialEntries={["/"]}>
            <AppShell />
          </MemoryRouter>
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  )
}

/** The desktop rail, as opposed to the mobile drawer that mirrors it. */
function rail(): HTMLElement {
  const nav = document.querySelector("aside nav")
  if (!nav) throw new Error("the rail rendered no navigation")
  return nav as HTMLElement
}

beforeEach(() => localStorage.clear())

describe("collapsed", () => {
  beforeEach(() => localStorage.setItem(SIDEBAR_KEY, "true"))

  it("names every link even though only an icon is drawn", () => {
    // An icon is not an accessible name. Before this the whole rail reached a
    // screen reader as a column of unnamed links, and the tooltip does not
    // stand in — it is hover-and-focus content, not the link's own label.
    show()
    for (const label of ["Overview", "Ask", "Search", "Projects", "Drawings", "Documents"]) {
      expect(within(rail()).getByRole("link", { name: label })).toBeInTheDocument()
    }
  })

  it("centres the icon column instead of padding it", () => {
    // The rail is 56px and each link is 36px. Padded-and-left-aligned put every
    // icon four pixels off the centre line the wordmark above sits on, which is
    // what read as "mixed up".
    show()
    expect(rail().className).toContain("items-center")
  })

  it("puts a rule between groups and not before the first", () => {
    // A separator emitted at the top of every group meant one immediately
    // under the logo, and each group topped by a rule rather than divided from
    // the one before it.
    show()
    const children = Array.from(rail().children)
    expect(children[0]?.getAttribute("data-orientation")).not.toBe("horizontal")
  })

  it("separates Reference from the group above it", () => {
    // It had `pt-1` and no rule, so Legend merged into Operations — the one
    // boundary in the rail that looked different from every other.
    show()
    const children = Array.from(rail().children)
    const legend = within(rail()).getByRole("link", { name: /legend/i })
    const legendIndex = children.findIndex((child) => child.contains(legend))
    expect(children[legendIndex - 1]?.getAttribute("data-orientation")).toBe("horizontal")
  })

  it("applies its classes instead of stringifying them", () => {
    // The bug behind the whole report, and the reason it hid so well.
    //
    // Collapsed, the link is wrapped in `Hint`, whose Radix trigger uses
    // `asChild`. Slot merges `className` by string concatenation, so a
    // className *function* is coerced with `String()` rather than called —
    // the links carried the literal source of the callback as their class
    // list. `size-9` never applied, so the icons were bare 16px glyphs with
    // no hit target, no hover and no selected state.
    //
    // Expanded there is no Hint, no Slot and no coercion, which is why the
    // same component looked perfect on one side of the toggle and broken on
    // the other.
    show()
    const link = within(rail()).getByRole("link", { name: "Ask" })
    expect(link.className).not.toMatch(/^\s*\(/)
    expect(link.className).toContain("size-9")
  })

  it("gives each icon a hit target rather than only an icon", () => {
    // 16px is below any reasonable target size; the 36px box is what makes
    // the rail clickable and what gives the active state something to fill.
    show()
    expect(within(rail()).getByRole("link", { name: "Ask" }).className).toContain("size-9")
  })

  it("hides the group headings that only make sense with labels", () => {
    show()
    expect(within(rail()).queryByText("Workspace")).not.toBeInTheDocument()
    expect(within(rail()).queryByText("Register")).not.toBeInTheDocument()
  })

  it("still offers the control that expands it again", () => {
    show()
    expect(screen.getByRole("button", { name: /expand sidebar/i })).toBeInTheDocument()
  })
})

describe("expanded", () => {
  beforeEach(() => localStorage.setItem(SIDEBAR_KEY, "false"))

  it("labels every link visibly", () => {
    show()
    expect(within(rail()).getByRole("link", { name: "Overview" })).toBeInTheDocument()
    expect(within(rail()).getByText("Ask")).toBeInTheDocument()
  })

  it("shows the group headings", () => {
    show()
    expect(within(rail()).getByText("Workspace")).toBeInTheDocument()
    expect(within(rail()).getByText("Reference")).toBeInTheDocument()
  })

  it("draws no icon-rail separators", () => {
    // Expanded, the headings do the dividing; rules as well would be noise.
    show()
    const rules = rail().querySelectorAll('[data-orientation="horizontal"]')
    expect(rules.length).toBe(0)
  })
})

describe("the same links either way", () => {
  it("collapsing hides labels without hiding destinations", () => {
    // The rail is a navigation, not a feature gate: collapsing is a display
    // preference and must not quietly remove somewhere the user can go.
    localStorage.setItem(SIDEBAR_KEY, "false")
    const expanded = show()
    const before = within(rail())
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"))
    expanded.unmount()

    localStorage.setItem(SIDEBAR_KEY, "true")
    show()
    const after = within(rail())
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"))

    expect(after).toEqual(before)
  })
})
