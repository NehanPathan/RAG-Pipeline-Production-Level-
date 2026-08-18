import { defineConfig, devices } from "@playwright/test"

/**
 * Browser-level checks, kept apart from the Vitest suite.
 *
 * They exist because a real bug got through everything else. The collapsed
 * sidebar's links were wrapped in a Radix tooltip trigger, whose `asChild`
 * Slot merges `className` by string concatenation — so it stringified the
 * className *function* instead of calling it. Every class was inert. jsdom
 * computes no layout and could not see it; reading the code did not show it
 * either, because the same component was correct on the expanded side. Only a
 * browser reporting `height: 16px` where 36 was intended found it.
 *
 * Two projects, deliberately separate:
 *
 * **layout** measures geometry — centres, spacing, box sizes, computed
 * backgrounds. Deterministic, identical on every OS, and it is the pair that
 * actually caught the bug. This is the one worth running in CI.
 *
 * **visual** compares screenshots. It catches what numbers cannot — overlap,
 * colour, a control that renders but looks wrong — at the cost of baselines
 * that are per-platform: font rasterisation differs between Windows, macOS and
 * Linux, so a Windows baseline fails on a Linux runner for reasons that have
 * nothing to do with the code. Baselines here were generated on Windows, so CI
 * runs `layout` only. See the README note before adding Linux ones.
 *
 * The app under test is the **built** bundle served by `vite preview`, not the
 * dev server: it is what actually ships, and Vite's default mock API means no
 * backend, database or Firebase is involved.
 */
export default defineConfig({
  testDir: "./e2e",
  // Vitest owns `src/**/*.test.tsx`; these two must never collide.
  testMatch: "**/*.spec.ts",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",

  use: {
    baseURL: "http://127.0.0.1:4173",
    // A screenshot of a half-finished transition is a flake, not a finding.
    trace: "retain-on-failure",
  },

  expect: {
    toHaveScreenshot: {
      animations: "disabled",
      // Subpixel text rendering varies slightly even between runs on one
      // machine. Small enough to catch a moved control, loose enough not to
      // fail on a re-rasterised glyph.
      maxDiffPixelRatio: 0.01,
    },
  },

  projects: [
    { name: "layout", testMatch: "**/*.layout.spec.ts", use: { ...devices["Desktop Chrome"] } },
    { name: "visual", testMatch: "**/*.visual.spec.ts", use: { ...devices["Desktop Chrome"] } },
  ],

  webServer: {
    // `--host` is explicit: without it Vite's preview server binds in a way
    // Playwright's 127.0.0.1 health check does not always reach, and the run
    // dies on a two-minute timeout that looks like a broken app.
    command: "npx vite preview --port 4173 --strictPort --host 127.0.0.1",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
