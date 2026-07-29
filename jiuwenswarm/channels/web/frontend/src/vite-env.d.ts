/// <reference types="vite/client" />
/// <reference types="vite-plugin-svgr/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  readonly VITE_WS_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

interface Window {
  pywebview?: {
    api?: {
      download_file?: (url: string, filename: string) => Promise<boolean> | boolean;
      install_update?: (path: string) => Promise<boolean> | boolean;
      save_data_url?: (dataUrl: string, filename: string) => Promise<{ ok: boolean; cancelled?: boolean }> | { ok: boolean; cancelled?: boolean };
      select_project_directory?: () => Promise<string | null> | string | null;
    };
  };
}
