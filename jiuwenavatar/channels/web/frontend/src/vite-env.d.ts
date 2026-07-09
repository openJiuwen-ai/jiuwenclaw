/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  readonly VITE_WS_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

interface Window {
  __jiuwenOpenReports?: (avatarId?: string, read?: string) => void;
  __jiuwenSwitchAvatar?: (avatarId?: string) => void;
  __jiuwenSwitchSession?: (sessionId?: string, avatarId?: string) => void;
}
