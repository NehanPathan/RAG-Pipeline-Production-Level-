/**
 * Opening the app already signed in.
 *
 * The build under test runs against the mock API (`VITE_USE_MOCKS` defaults on),
 * where a session is just a value in `localStorage` — so these tests seed one
 * rather than driving the sign-in form. That is deliberate on two counts: it
 * keeps Firebase out of a suite that is about layout, and it means the fixture
 * cannot drift into being a login test that happens to also check the sidebar.
 */
import type { Page } from "@playwright/test"

const SESSION_KEY = "girder.session"
const SIDEBAR_KEY = "girder.sidebar.collapsed"
const THEME_KEY = "girder.theme"

/** The first mock user — an admin, so every nav group is visible. */
const ADMIN = {
  user_id: "00000000-0000-0000-0000-000000000001",
  email: "r.venkataraman@girder.works",
  display_name: "R. Venkataraman",
  role: "admin",
  clearance: "restricted",
  auth_provider: "firebase",
  authenticated: true,
}

export interface OpenOptions {
  collapsed?: boolean
  theme?: "dark" | "light"
  path?: string
}

export async function open(page: Page, options: OpenOptions = {}) {
  const { collapsed = false, theme = "dark", path = "/" } = options

  await page.addInitScript(
    ([session, sidebar, mode, keys]) => {
      localStorage.setItem(keys.session, JSON.stringify(session))
      localStorage.setItem(keys.sidebar, sidebar)
      localStorage.setItem(keys.theme, mode)
    },
    [
      { principal: ADMIN, token: `mock.${ADMIN.user_id}` },
      String(collapsed),
      theme,
      { session: SESSION_KEY, sidebar: SIDEBAR_KEY, theme: THEME_KEY },
    ] as const,
  )

  await page.goto(path)
  // The rail is the last thing to settle, and everything here measures it.
  await page.waitForSelector("aside:has(nav) nav a")
}

/** Geometry of the desktop rail, measured in the browser. */
export async function railGeometry(page: Page) {
  return page.evaluate(() => {
    const aside = document.querySelector("aside:has(nav)")!
    const box = aside.getBoundingClientRect()
    const links = [...aside.querySelectorAll("nav a")].map((a) => {
      const b = a.getBoundingClientRect()
      const style = getComputedStyle(a)
      return {
        name: (a.textContent ?? "").trim(),
        centreX: Math.round((b.x + b.width / 2) * 10) / 10,
        top: Math.round(b.y * 10) / 10,
        width: Math.round(b.width * 10) / 10,
        height: Math.round(b.height * 10) / 10,
        background: style.backgroundColor,
        // The symptom of the Slot bug: the class list was the source text of
        // a callback rather than a list of classes.
        classIsStringifiedFunction: a.className.trim().startsWith("("),
      }
    })
    const toggle = aside.querySelector("button")?.getBoundingClientRect()
    return {
      width: Math.round(box.width * 10) / 10,
      centreX: Math.round((box.x + box.width / 2) * 10) / 10,
      toggleCentreX: toggle ? Math.round((toggle.x + toggle.width / 2) * 10) / 10 : null,
      links,
    }
  })
}
