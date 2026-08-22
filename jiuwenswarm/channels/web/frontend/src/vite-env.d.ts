/// <reference types="vite/client" />
/// <reference types="vite-plugin-svgr/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  readonly VITE_WS_BASE?: string;
  readonly VITE_WEB_TRANSPORT?: string;
  readonly VITE_TRANSPORT?: string;
  readonly VITE_GATEWAY_HTTP_BASE?: string;
  readonly VITE_WEB_HTTP_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

type DesktopSaveResult = {
  ok: boolean;
  cancelled?: boolean;
};

interface Window {
  pywebview?: {
    api?: {
      download_file?: (url: string, filename: string) => Promise<DesktopSaveResult> | DesktopSaveResult;
      install_update?: (path: string) => Promise<boolean> | boolean;
      save_data_url?: (dataUrl: string, filename: string) => Promise<DesktopSaveResult> | DesktopSaveResult;
      select_project_directory?: () => Promise<string | null> | string | null;
    };
  };
}
