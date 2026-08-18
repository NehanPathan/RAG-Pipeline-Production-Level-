import { USE_MOCKS } from "@/api/client"
import { mockApi } from "@/api/mock/handlers"
import { realApi } from "@/api/real"
import type { ApiSurface } from "@/api/surface"

/**
 * The single entry point every hook and page uses.
 *
 * `VITE_USE_MOCKS=false` points the whole app at the FastAPI service without
 * touching a line of feature code.
 */
export const api: ApiSurface = USE_MOCKS ? mockApi : realApi

export { USE_MOCKS }
export { ApiError } from "@/api/client"
export type { ApiSurface } from "@/api/surface"
