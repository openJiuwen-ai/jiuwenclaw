/// <reference types="vite/client" />
/// <reference types="vite-plugin-svgr/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  readonly VITE_WS_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

type DesktopSaveResult = {
  ok: boolean;
  cancelled?: boolean;
};

type DesktopBlobSaveStartResult = DesktopSaveResult & {
  transfer_id?: string;
};

interface Window {
  pywebview?: {
    api?: {
      download_file?: (url: string, filename: string) => Promise<DesktopSaveResult> | DesktopSaveResult;
      begin_blob_save?: (filename: string, mimeType: string, totalSize: number) => Promise<DesktopBlobSaveStartResult> | DesktopBlobSaveStartResult;
      append_blob_save?: (transferId: string, encodedChunk: string) => Promise<boolean> | boolean;
      finish_blob_save?: (transferId: string) => Promise<DesktopSaveResult> | DesktopSaveResult;
      abort_blob_save?: (transferId: string) => Promise<boolean> | boolean;
      install_update?: (path: string) => Promise<boolean> | boolean;
      save_data_url?: (dataUrl: string, filename: string) => Promise<DesktopSaveResult> | DesktopSaveResult;
      select_project_directory?: () => Promise<string | null> | string | null;
    };
  };
}
