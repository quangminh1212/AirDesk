/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the AirDesk API (empty = same origin / dev proxy). */
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
