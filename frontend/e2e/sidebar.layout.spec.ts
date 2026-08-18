/**
 * The sidebar, measured in a real browser.
 *
 * Every assertion here corresponds to something that was actually wrong and
 * that nothing else caught. The unit tests passed, TypeScript passed, the build
 * passed, and the rail still rendered as a column of bare 16px glyphs with no
 * hit target and no way to see which page you were on.
 *
 * The reason jsdom could not find it: jsdom implements no layout engine. It has
 * no notion of a box being 36 pixels tall or an element sitting off the centre
 * line, so the entire class of defect is invisible there by construction. These
 * are the assertions that need a browser, and nothing else lives here.
 */
import { expect, test } from "@playwright/test"
import { open, railGeometry } from "./app"

test.describe("collapsed", () => {
  test.beforeEach(async ({ page }) => open(page, { collapsed: true }))

  test("applies its classes rather than stringifying them", async ({ page }) => {
    // The root cause. `Hint` wraps each collapsed link in a Radix trigger using
    // `asChild`, and Slot merges `className` by string concatenation — so a
    // className *function* was coerced with String() instead of called. The
    // links carried the literal source of the callback as their class list.
    const { links } = await railGeometry(page)
    expect(links.length).toBeGreaterThan(0)
    for (const link of links) {
      expect(link.classIsStringifiedFunction, `${link.name} class list`).toBe(false)
    }
  })

  test("gives every icon a real hit target", async ({ page }) => {
    // 36px, not the 16px the icon alone occupied. This is the number that
    // exposed the bug, and it cannot be checked without a layout engine.
    const { links } = await railGeometry(page)
    for (const link of links) {
      expect(link.height, `${link.name} height`).toBeGreaterThanOrEqual(32)
      expect(link.width, `${link.name} width`).toBeGreaterThanOrEqual(32)
    }
  })

  test("lines every icon up on the rail's centre", async ({ page }) => {
    // Reported as "the icons look mixed up". Padding without centring left
    // each icon off the line the wordmark above it sits on.
    const { centreX, toggleCentreX, links } = await railGeometry(page)
    for (const link of links) {
      expect(Math.abs(link.centreX - centreX), `${link.name} off centre`).toBeLessThanOrEqual(1)
    }
    expect(Math.abs((toggleCentreX ?? 0) - centreX)).toBeLessThanOrEqual(1)
  })

  test("spaces the icons evenly", async ({ page }) => {
    // Uneven rhythm was the other half of "mixed up": items 2px apart inside a
    // group and ~32px apart between them. Gaps at group boundaries are larger
    // by design, so this checks the spacing *within* a run rather than all of
    // it being identical.
    const { links } = await railGeometry(page)
    const gaps = links.slice(1).map((link, i) => Math.round(link.top - links[i].top))
    const withinGroups = gaps.filter((gap) => gap < 50)
    expect(withinGroups.length).toBeGreaterThan(5)
    expect(new Set(withinGroups).size, `varied gaps: ${withinGroups}`).toBe(1)
  })

  test("shows which page you are on", async ({ page }) => {
    // With the classes inert there was no active background at all, so the rail
    // gave no indication of the current page.
    const { links } = await railGeometry(page)
    const filled = links.filter((l) => l.background !== "rgba(0, 0, 0, 0)")
    expect(filled.length, "no link carries an active background").toBe(1)
  })

  test("stays within the rail", async ({ page }) => {
    const { width, links } = await railGeometry(page)
    for (const link of links) {
      expect(link.width, `${link.name} overflows the rail`).toBeLessThanOrEqual(width)
    }
  })
})

test.describe("expanded", () => {
  test.beforeEach(async ({ page }) => open(page, { collapsed: false }))

  test("labels every link and still marks the active one", async ({ page }) => {
    const { links } = await railGeometry(page)
    expect(links.every((l) => l.name.length > 0)).toBe(true)
    expect(links.filter((l) => l.background !== "rgba(0, 0, 0, 0)").length).toBe(1)
  })

  test("applies its classes here too", async ({ page }) => {
    // Expanded has no Hint, so no Slot and no coercion — which is exactly why
    // the bug survived. Pinned on both sides so a future refactor that wraps
    // this one in a tooltip fails loudly.
    const { links } = await railGeometry(page)
    for (const link of links) {
      expect(link.classIsStringifiedFunction, `${link.name} class list`).toBe(false)
    }
  })
})

test.describe("toggling", () => {
  test("keeps every destination when the labels go away", async ({ page }) => {
    // The rail is navigation, not a feature gate: collapsing is a display
    // preference and must not quietly remove somewhere the user can go.
    await open(page, { collapsed: false })
    const expanded = await page.locator("aside:has(nav) nav a").evaluateAll((links) =>
      links.map((a) => a.getAttribute("href")),
    )

    await page.getByRole("button", { name: /collapse/i }).click()
    await expect(page.getByRole("button", { name: /expand sidebar/i })).toBeVisible()

    const collapsed = await page.locator("aside:has(nav) nav a").evaluateAll((links) =>
      links.map((a) => a.getAttribute("href")),
    )
    expect(collapsed).toEqual(expanded)
  })

  test("names every link for a screen reader once the text is hidden", async ({ page }) => {
    // An icon is not an accessible name. Collapsed, the whole rail reached
    // assistive technology as a column of unnamed links.
    await open(page, { collapsed: true })
    for (const label of ["Overview", "Ask", "Search", "Projects", "Drawings"]) {
      await expect(page.getByRole("link", { name: label }).first()).toBeAttached()
    }
  })
})
