/**
 * Screenshot baselines for the sidebar.
 *
 * The layout spec next door measures numbers, which is what actually caught
 * the bug and is what stays honest across machines. This catches the rest:
 * overlap, contrast, an icon that renders in the wrong colour, a control that
 * is the right size in the wrong place. Numbers cannot see any of that.
 *
 * **Scoped to the rail, not the page.** A full-page baseline fails whenever
 * anything on the page changes — a chart, a date, a document count — and a
 * suite that cries wolf gets updated without being read, which is worse than
 * not having it. The rail is small, stable and the thing under review.
 *
 * **Baselines are per-platform.** Font rasterisation differs between Windows,
 * macOS and Linux, so these were generated on Windows and will not match a
 * Linux CI runner. CI therefore runs the `layout` project only. To gate CI on
 * pixels too, generate Linux baselines from the Playwright container:
 *
 *   docker run --rm -v "$PWD:/w" -w /w mcr.microsoft.com/playwright:v1.62.1-noble \
 *     npx playwright test --project=visual --update-snapshots
 *
 * and commit the `-linux` snapshots alongside these.
 */
import { expect, test } from "@playwright/test"
import { open } from "./app"

// The *navigation* aside. `/search` renders a second one for its filter
// sidebar, so a bare "aside" matches two elements there and the screenshot
// call fails with an error that says nothing about the cause.
const rail = "aside:has(nav)"

test.describe("the rail", () => {
  for (const theme of ["dark", "light"] as const) {
    test(`collapsed, ${theme}`, async ({ page }) => {
      await open(page, { collapsed: true, theme })
      await expect(page.locator(rail)).toHaveScreenshot(`rail-collapsed-${theme}.png`)
    })

    test(`expanded, ${theme}`, async ({ page }) => {
      await open(page, { collapsed: false, theme })
      await expect(page.locator(rail)).toHaveScreenshot(`rail-expanded-${theme}.png`)
    })
  }

  test("the active item is marked on a page other than the first", async ({ page }) => {
    // Guards the state that had no visual at all while the classes were inert:
    // with every class dropped there was no active background anywhere, and
    // the rail looked identical whichever page you were on.
    await open(page, { collapsed: true, path: "/search" })
    await expect(page.locator(rail)).toHaveScreenshot("rail-collapsed-search.png")
  })
})
