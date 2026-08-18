/**
 * Rendering a page the way the app does, minus the parts that would make a
 * unit test lie.
 *
 * The provider stack mirrors `main.tsx`. Every layer is load-bearing: the
 * `Toaster` throws without `ThemeProvider`, Radix throws on any tooltip
 * outside `TooltipProvider`, and without a mounted `Toaster` an error toast
 * renders nowhere so "the failure is shown to the user" cannot be asserted.
 *
 * Two departures from production: retries are off, because an error-state
 * assertion would otherwise wait through two backoffs; and auth is mocked at
 * the module boundary rather than by mounting `AuthProvider`, which reads
 * localStorage and talks to Firebase. All these tests need is the caller's
 * role.
 */
import * as React from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, type RenderResult } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { vi } from "vitest"
import type { Role } from "@/api/types"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { ThemeProvider } from "@/lib/theme"

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  })
}

export function renderPage(ui: React.ReactElement, route = "/"): RenderResult {
  const client = makeQueryClient()
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <TooltipProvider>
          <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
          <Toaster />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  )
}

/**
 * The roles a test's caller holds.
 *
 * Mirrors `require_role` on the API: an allow-list membership test, not a rank
 * comparison, so a test cannot accidentally assert that an analyst "outranks"
 * a viewer into a steward-only control.
 */
export function authAs(role: Role) {
  return {
    hasRole: (...roles: Role[]) => roles.includes(role),
    principal: {
      user_id: "11111111-1111-1111-1111-111111111111",
      role,
      clearance: "restricted" as const,
      email: `${role}@example.com`,
      auth_provider: "firebase",
      authenticated: true,
    },
    session: null,
    loading: false,
    signIn: vi.fn(),
    signInAs: vi.fn(),
    signOut: vi.fn(),
    canRead: () => true,
  }
}
