/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base path for API calls. Defaults to `/api/v1`, proxied in dev. */
  readonly VITE_API_BASE?: string
  /** `"false"` points the app at the real FastAPI service. */
  readonly VITE_USE_MOCKS?: string
  /** Dev-server proxy target for `/api`. */
  readonly VITE_API_TARGET?: string
  /**
   * Firebase web API key. Absent means sign-in is unavailable and the login
   * form says so, rather than failing at the point of submission.
   *
   * Not a credential: it identifies the project and is designed to ship in
   * client bundles. Security comes from server-side token verification and
   * the email-domain allow-list.
   */
  readonly VITE_FIREBASE_API_KEY?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
