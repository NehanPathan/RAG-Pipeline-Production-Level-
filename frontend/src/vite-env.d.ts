/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base path for API calls. Defaults to `/api/v1`, proxied in dev. */
  readonly VITE_API_BASE?: string
  /** `"false"` points the app at the real FastAPI service. */
  readonly VITE_USE_MOCKS?: string
  /** Dev-server proxy target for `/api`. */
  readonly VITE_API_TARGET?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
