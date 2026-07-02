/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  readonly VITE_PRODUCT_NAME?: string;
  readonly VITE_MANAGER_ALLOW_LOCAL_PROVISION?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
