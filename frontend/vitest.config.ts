/// <reference types="vitest" />
import path from "node:path"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vitest/config"

/**
 * Test config, kept separate from `vite.config.ts`.
 *
 * The production config carries the manual-chunk strategy, the dev proxy and
 * the HMR settings, none of which mean anything under Vitest — and a `test`
 * key bolted onto it would put test concerns in the file that builds the
 * shipped bundle. Only the `@` alias is shared, because the source imports
 * depend on it.
 *
 * Tailwind's Vite plugin is deliberately absent: these tests assert on roles
 * and text, never on computed styles, so compiling a stylesheet for every run
 * would cost seconds and prove nothing.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
    /**
     * Vitest's default is 5s, which these tests sat just under on an idle
     * machine and blew through whenever anything else was running — the whole
     * docker stack, another suite, a build. Every failure was a timeout, every
     * file passed when run alone, and nothing was actually broken.
     *
     * A page here mounts React Query, Radix, a router and a chart library,
     * then waits on several settling queries; 1-2s is normal and a cold first
     * render is slower still. 20s is not slack for a genuinely hanging test —
     * that still fails, just later — it is headroom so a passing test does not
     * report as a failure because the CPU was busy.
     */
    testTimeout: 20_000,
    hookTimeout: 20_000,
    /**
     * Capped rather than left to fill the machine.
     *
     * Vitest defaults to roughly one worker per core, and each one is a whole
     * jsdom environment mounting React Query, Radix, a router and a chart
     * library. On a developer box also running the compose stack — Postgres,
     * Elasticsearch, Qdrant, Redis, the API and the worker — twelve of those
     * starve each other badly enough that renders which normally take a second
     * blow through a twenty-second timeout. Every failure was a timeout and
     * every file passed alone; nothing was ever actually broken.
     *
     * Four is measured, not guessed: unbounded fails four tests, one worker
     * passes in 30s, four passes in 15s. Fewer workers with enough CPU each
     * beat more workers fighting over it, and the same cap keeps a two-core CI
     * runner from thrashing.
     */
    pool: "threads",
    poolOptions: { threads: { maxThreads: 4 } },
  },
})
