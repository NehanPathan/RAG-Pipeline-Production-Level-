import "@testing-library/jest-dom/vitest"
import { cleanup } from "@testing-library/react"
import { afterEach, vi } from "vitest"

// jsdom implements neither, and Radix's popper and the chart container both
// construct one on mount. Without these the dialogs under test throw before
// rendering anything to assert on.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver

if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
}

// jsdom has no layout, so it implements no scrolling at all. The Ask
// composer scrolls its transcript to the bottom after every turn, which throws
// rather than no-opping without this — a page that works perfectly in a browser
// failing to render at all under test.
Element.prototype.scrollTo ??= () => {}

// Radix uses these for focus scoping and scroll locking in dialogs.
Element.prototype.scrollIntoView ??= () => {}
Element.prototype.hasPointerCapture ??= () => false
Element.prototype.releasePointerCapture ??= () => {}
Element.prototype.setPointerCapture ??= () => {}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})
