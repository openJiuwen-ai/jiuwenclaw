import type { HistoryItem } from "../types.js";
import type { AccentColorName, ThemeName } from "../../ui/theme.js";
import type { PendingQuestionItem, UserAnswer } from "../event-handlers.js";
import type { FileAttachment } from "../protocol.js";
import type { ConfigItemSchema } from "./builtins/config.js";
import type { ClientMode } from "../modes.js";
import type { SessionUsageSummary } from "../../app-state.js";

export type ConnectionStatus = "idle" | "connecting" | "connected" | "reconnecting" | "auth_failed";

export type StatusViewTab = "status" | "usage" | "config";

export enum CommandKind {
  BUILT_IN = "built-in",
}

export interface CommandSuggestion {
  value: string;
  description?: string;
  usage?: string;
  example?: string;
}

export interface CommandContext {
  /** 版本信息 */
  version: string;
  /**
   * options.logAsUser=false 可用于发送内部控制消息（例如 /init 生成的 orchestration prompt），
   * 避免在 CLI/TUI 历史中渲染为普通用户输入。
   */
  sendEventOnly: (method: string, params: Record<string, unknown>) => string;
  request: <T = Record<string, unknown>>(
    method: string,
    params: Record<string, unknown>,
    timeoutMs?: number,
  ) => Promise<T>;
  askQuestions: (questions: PendingQuestionItem[], source?: string) => Promise<UserAnswer[]>;
  sendMessage: (
    content: string,
    attachments?: FileAttachment[],
    mode?: ClientMode,
    options?: { logAsUser?: boolean },
  ) => string | null;
  sessionId: string;
  entries: HistoryItem[];
  themeName: ThemeName;
  accentColor: AccentColorName;
  updateSession: (id: string) => void;
  addItem: (item: HistoryItem) => void;
  clearEntries: () => void;
  restoreHistory: (sessionId: string) => Promise<void>;
  exitApp: () => void;
  isProcessing: boolean;
  /** Check if interrupt was requested locally (immediate detection for long-running commands) */
  isInterruptRequested: () => boolean;
  /** Clear local interrupt flag (for long-running commands to reset after handling interrupt) */
  clearInterruptRequested: () => void;
  connectionStatus: ConnectionStatus;
  mode: ClientMode;
  setMode: (mode: ClientMode) => void;
  setModel: (name: string) => void;
  setThemeName: (theme: ThemeName) => void;
  setAccentColor: (color: AccentColorName) => void;
  transcriptMode: "compact" | "detailed";
  setTranscriptMode: (mode: "compact" | "detailed") => void;
  transcriptFoldMode: "none" | "tools" | "thinking" | "all";
  setTranscriptFoldMode: (mode: "none" | "tools" | "thinking" | "all") => void;
  collapsedToolGroupCount: number;
  collapseToolGroups: (scope: "last" | "all") => void;
  expandToolGroups: (scope: "last" | "all") => void;
  sessionTitle: string;
  setSessionTitle: (title: string) => void;
  // Trusted directories management (project-scoped)
  getTrustedDirs: () => string[];
  validateDirPath: (path: string) => "valid" | "not_found" | "invalid" | "no_access";
  addTrustedDir: (path: string) => "added" | "exists" | "not_found" | "invalid" | "no_access";
  setTrustedDir: (path: string) => "set" | "not_found" | "invalid" | "no_access";
  removeTrustedDir: (path: string) => boolean;
  clearTrustedDirs: () => void;
  setCurrentProjectDir: (dir: string) => void;
  getCurrentProjectDir: () => string;
  // Workspace directory (current working directory)
  getWorkspaceDir: () => string | undefined;
  setInput?: (text: string) => void;
  getUsageSummary: () => SessionUsageSummary;
  enterConfigEditor?: (
    focusKey?: string,
    configPayload?: Record<string, unknown> & { schema?: ConfigItemSchema[] },
  ) => void;
  enterStatusView?: (tab?: StatusViewTab) => void;
  openInEditor?: (filePath: string) => void;
  /** Enter FileViewer mode to view large content (e.g., formatted logs) */
  enterFileViewer?: (content: string, title: string, source: string) => void;
  restartStatusLine?: () => void;
}

export interface SlashCommand {
  name: string;
  altNames?: string[];
  description: string;
  usage?: string;
  example?: string;
  /**
   * Inline hint shown after the cursor when the user has typed this command
   * (or sub-command) with no further arguments.  For commands that accept
   * key=value fields, this should list the available keys with brief labels,
   * e.g.  "name=任务名 cron_expr=\"时间\" description=\"让Agent做什么\""
   */
  argGuide?: string;
  hidden?: boolean;
  isSafeConcurrent?: boolean;
  kind: CommandKind;
  action: (ctx: CommandContext, args: string) => void | Promise<void>;
  completion?: (ctx: CommandContext, partial: string) => string[] | Promise<string[]>;
  completionSuffix?: string;
  takesArgs?: boolean;
  subCommands?: SlashCommand[];
}

export type SlashCommandListProvider = () => readonly SlashCommand[];
