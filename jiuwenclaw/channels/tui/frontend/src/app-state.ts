import { addError, addInfo } from "./core/commands/helpers.js";
import type { CommandContext } from "./core/commands/types.js";
import {
  computeTimeoutAt,
  isIgnorableHistoryRestoreError,
  rebuildToolExecutionStateFromEntries,
  upsertToolGroupDisplay,
} from "./core/app-state-helpers.js";
import {
  applyToolResult,
  coalesceAssistantHistoryEntries,
  createToolCallDisplay,
  mergeHistoryMessagesForRestore,
  parseHistoryFrame,
} from "./core/history-parser.js";
import { generateSessionId } from "./core/session-state.js";
import { getToolGroupIds } from "./core/transcript-timeline.js";
import {
  handleIncomingFrame,
  type AppEventDelegate,
  type PendingQuestion,
  type PendingQuestionItem,
  type UserAnswer,
} from "./core/event-handlers.js";
import { isTeamMode, type ClientMode } from "./core/modes.js";
import { isEventFrame, type EventFrame, type FileAttachment } from "./core/protocol.js";
import {
  StreamingState,
  type ContextCompressionStats,
  type HistoryItem,
  type SubtaskState,
  type TeamMemberEvent,
  type TeamMessageEvent,
  type TeamTaskEvent,
  type TodoItem,
  type ToolCallDisplay,
  type ToolExecution,
} from "./core/types.js";
import { isTeamWorking } from "./ui/components/team-shared.js";
import {
  getCurrentAccentColor,
  getCurrentThemeName,
  setCurrentAccentColor,
  setCurrentThemeName,
  type AccentColorName,
  type ThemeName,
} from "./ui/theme.js";
import { type ConnectionStatus, WsClient } from "./core/ws-client.js";
import {
  getTrustedDirs,
  validateDirPath,
  addTrustedDir,
  setTrustedDir,
  removeTrustedDir,
  clearTrustedDirs,
} from "./core/tui-trusted-dirs-store.js";
import { loadTuiConfig, type StatusLineSetting } from "./core/tui-config-store.js";
import { execFile } from "node:child_process";
import { writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, sep } from "node:path";

export interface ModelUsageEntry {
  model: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface SessionUsageSummary {
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  byModel: ModelUsageEntry[];
}

export interface AppSnapshot {
  connectionStatus: ConnectionStatus;
  sessionId: string;
  mode: ClientMode;
  themeName: ThemeName;
  accentColor: AccentColorName;
  transcriptMode: "compact" | "detailed";
  transcriptFoldMode: "none" | "tools" | "thinking" | "all";
  collapsedToolGroupIds: Set<string>;
  entries: HistoryItem[];
  toolExecutions: ToolExecution[];
  streamingState: StreamingState;
  pendingQuestion: PendingQuestion | null;
  lastError: string | null;
  isProcessing: boolean;
  /**
   * 当前 UI 观测到是否存在运行中的工作。
   * 用于渲染与本地交互（如 Esc）；Ctrl+C 的中断请求仍以服务端为准，不依赖此值放行。
   */
  cancellableWork: boolean;
  isPaused: boolean;
  isInterrupted: boolean;
  activeSubtasks: SubtaskState[];
  todos: TodoItem[];
  teamMemberEvents: TeamMemberEvent[];
  teamTaskEvents: TeamTaskEvent[];
  teamMessageEvents: TeamMessageEvent[];
  evolutionStatus: "idle" | "running";
  contextCompression: ContextCompressionStats | null;
  modelInfo: { provider: string; model: string; version: string };
  sessionTitle: string;
  statusLineText: string | null;
}

export class CliPiAppState {
  private listeners = new Set<() => void>();
  private entries: HistoryItem[] = [];
  /** AppScreen 注入的 setInput 回调，用于自动恢复后填充输入框。 */
  private _setInputRef: ((text: string) => void) | null = null;
  /** AppScreen 注入的 getInputValue 回调，用于自动恢复判断输入框是否为空。 */
  private _getInputValueRef: (() => string) | null = null;
  private connectionStatus: ConnectionStatus = "idle";
  private sessionId: string;
  private sessionTitle: string = "";
  private mode: ClientMode = "code.normal";
  private themeName: ThemeName = getCurrentThemeName();
  private accentColor: AccentColorName = getCurrentAccentColor();
  private transcriptMode: "compact" | "detailed" = "compact";
  private transcriptFoldMode: "none" | "tools" | "thinking" | "all" = "none";
  private collapsedToolGroupIds = new Set<string>();
  private streamingState: StreamingState = StreamingState.Idle;
  private pendingQuestion: PendingQuestion | null = null;
  private localPendingQuestion:
    | {
        requestId: string;
        resolve: (answers: UserAnswer[]) => void;
        reject: (error: Error) => void;
      }
    | null = null;
  private lastError: string | null = null;
  private activeSubtasks = new Map<string, SubtaskState>();
  private todos: TodoItem[] = [];
  private teamMemberEvents: TeamMemberEvent[] = [];
  private teamTaskEvents: TeamTaskEvent[] = [];
  private teamMessageEvents: TeamMessageEvent[] = [];
  private evolutionStatus: "idle" | "running" = "idle";
  private contextCompression: ContextCompressionStats | null = null;
  private toolExecutions = new Map<string, ToolExecution>();
  private toolExecutionOrder: string[] = [];
  private orphanToolResults = new Map<
    string,
    { tool: ToolCallDisplay; requestId?: string; updatedAt: string }
  >();
  private historyEntries: HistoryItem[] = [];
  private historyFlushTimer: ReturnType<typeof setTimeout> | null = null;
  private toolTimeoutTimer: ReturnType<typeof setTimeout> | null = null;
  private historyRequestToken = 0;
  /** history.get 流返回的分页总数；由 `history.message` 事件帧的 `total_pages` 持续刷新。 */
  private historyTotalPages: number | null = null;
  /** 各页 done 事件的 resolver；restoreHistory 循环拉取时按 page_idx 等待。 */
  private historyPageDoneResolvers = new Map<number, () => void>();
  private unlistenStatus: (() => void) | null = null;
  private unlistenFrames: (() => void) | null = null;
  private statusLineText: string | null = null;
  private statusLineTimer: ReturnType<typeof setInterval> | null = null;
  private usageByModel = new Map<string, ModelUsageEntry>();
  private modelInfo: { provider: string; model: string; version: string } = {
    provider: "",
    model: "",
    version: "",
  };
  /** 当 closeUi 中 cancelBeforeExit 调 cancel({showNotice:false}) 时置 true，抑制 chat.interrupt_result 的 UI 通知。 */
  private suppressInterruptResult = false;
  /** 本地中断请求标志，cancel() 调用时立即置 true，用于 long-running 命令的中断检测。 */
  private interruptRequested = false;
  private readonly eventDelegate: AppEventDelegate = {
    getConnectionStatus: () => this.connectionStatus,
    getSessionId: () => this.sessionId,
    setSessionId: (sessionId) => {
      this.sessionId = sessionId;
    },
    setMode: (mode) => {
      this.mode = mode;
    },
    getMode: () => this.mode,
    getEntries: () => this.entries,
    setEntries: (entries) => {
      this.entries = entries;
    },
    setStreamingState: (state) => {
      this.streamingState = state;
    },
    setPendingQuestion: (question) => {
      this.pendingQuestion = question;
    },
    setLastError: (error) => {
      this.lastError = error;
    },
    getActiveSubtasks: () => this.activeSubtasks,
    setTodos: (todos) => {
      this.todos = todos;
    },
    appendTeamMemberEvent: (event) => {
      this.teamMemberEvents = [...this.teamMemberEvents.slice(-99), event];
    },
    appendTeamTaskEvent: (event) => {
      this.teamTaskEvents = [...this.teamTaskEvents.slice(-99), event];
    },
    appendTeamMessageEvent: (event) => {
      this.teamMessageEvents = [...this.teamMessageEvents.slice(-99), event];
    },
    setEvolutionStatus: (status) => {
      this.evolutionStatus = status;
    },
    setContextCompression: (stats) => {
      this.contextCompression = stats;
    },
    addToolCallPayload: (payload, sessionId, requestId, startedAt) => {
      this.addToolCallPayload(payload, sessionId, requestId, startedAt);
    },
    addToolResultPayload: (payload, sessionId, requestId, updatedAt) => {
      this.addToolResultPayload(payload, sessionId, requestId, updatedAt);
    },
    addSyntheticToolExecution: (tool, sessionId, requestId, at) => {
      this.addSyntheticToolExecution(tool, sessionId, requestId, at);
    },
    clearToolExecutionState: () => {
      this.clearToolExecutionState();
    },
    markRunningToolsInterrupted: () => {
      this.markRunningToolsInterrupted();
    },
    pushHistoryEntry: (entry) => {
      this.historyEntries.push(entry);
    },
    scheduleHistoryFlush: () => {
      this.scheduleHistoryFlush();
    },
    safeRestoreHistory: (sessionId) => {
      this.safeRestoreHistory(sessionId);
    },
    setSessionTitle: (title) => {
      this.setSessionTitle(title);
    },
    safeFetchSessionTitle: (sessionId) => {
      this.safeFetchSessionTitle(sessionId);
    },
    getSuppressInterruptResult: () => this.suppressInterruptResult,
    clearSuppressInterruptResult: () => {
      this.suppressInterruptResult = false;
    },
    clearInterruptRequested: () => this.clearInterruptRequested(),
    reportHistoryPageMeta: ({ totalPages }) => {
      if (typeof totalPages === "number" && Number.isFinite(totalPages) && totalPages > 0) {
        this.historyTotalPages = totalPages;
      }
    },
    notifyHistoryPageDone: (pageIdx) => {
      const resolver = this.historyPageDoneResolvers.get(pageIdx);
      if (resolver) {
        this.historyPageDoneResolvers.delete(pageIdx);
        resolver();
      }
    },
    tryAutoRestoreAfterCancel: () => this.tryAutoRestoreAfterCancel(),
    appendUsageSummary: (usage, model) => {
      const key = model || "unknown";
      const existing = this.usageByModel.get(key);
      const u = usage as Record<string, unknown>;
      const entry: ModelUsageEntry = {
        model: key,
        input_tokens: (existing?.input_tokens ?? 0) + (typeof u.input_tokens === "number" ? u.input_tokens : 0),
        output_tokens: (existing?.output_tokens ?? 0) + (typeof u.output_tokens === "number" ? u.output_tokens : 0),
        total_tokens: (existing?.total_tokens ?? 0) + (typeof u.total_tokens === "number" ? u.total_tokens : 0),
      };
      this.usageByModel.set(key, entry);
    },
  };

  constructor(
    private readonly wsClient: WsClient,
    cliSession?: string,
  ) {
    this.sessionId = cliSession || generateSessionId();
    const config = loadTuiConfig();
    if (config.theme) {
      setCurrentThemeName(config.theme);
      this.themeName = config.theme;
    }
    if (config.accentColor) {
      setCurrentAccentColor(config.accentColor);
      this.accentColor = config.accentColor;
    }
  }

  start(): void {
    this.unlistenStatus = this.wsClient.onStatusChange(async (status) => {
      this.connectionStatus = status;
      this.emitChange();
      if (status === "connected") {
        await this.fetchModelInfo();
      }
    });

    this.unlistenFrames = this.wsClient.onFrame((frame) => {
      this.handleFrame(frame);
    });

    this.wsClient.connect();
    this.startStatusLinePoll();
  }

  stop(): void {
    if (this.localPendingQuestion) {
      this.localPendingQuestion.reject(new Error("app stopped while awaiting input"));
      this.localPendingQuestion = null;
    }
    if (this.historyFlushTimer) {
      clearTimeout(this.historyFlushTimer);
      this.historyFlushTimer = null;
    }
    if (this.toolTimeoutTimer) {
      clearTimeout(this.toolTimeoutTimer);
      this.toolTimeoutTimer = null;
    }
    this.unlistenStatus?.();
    this.unlistenStatus = null;
    this.unlistenFrames?.();
    this.unlistenFrames = null;
    this.stopStatusLinePoll();
    this.wsClient.disconnect();
  }

  private async fetchModelInfo(): Promise<void> {
    try {
      const [configPayload, modelsPayload] = await Promise.allSettled([
        this.request("config.get", {}),
        this.request("models.list", {}),
      ]);
      const config =
        configPayload.status === "fulfilled" && configPayload.value && typeof configPayload.value === "object"
          ? (configPayload.value as Record<string, unknown>)
          : {};
      const modelsResult =
        modelsPayload.status === "fulfilled" && modelsPayload.value && typeof modelsPayload.value === "object"
          ? (modelsPayload.value as Record<string, unknown>)
          : {};
      const activeModelName = String(modelsResult.active_model ?? "").trim();
      const models = Array.isArray(modelsResult.models) ? (modelsResult.models as Record<string, unknown>[]) : [];
      const activeModel = activeModelName ? models.find((m) => m.model_name === activeModelName) : models[0];
      this.modelInfo = {
        provider: String(activeModel?.model_provider ?? config.model_provider ?? ""),
        model: activeModelName || String(config.model ?? ""),
        version: String(config.app_version ?? ""),
      };
      this.emitChange();
    } catch {
      // ignore error, use defaults
    }
  }

  onChange(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  getSnapshot(): AppSnapshot {
    const isProcessing =
      this.streamingState === StreamingState.Responding ||
      this.streamingState === StreamingState.WaitingForConfirmation;
    const hasRunningTools = this.toolExecutionOrder.some((id) => {
      const ex = this.toolExecutions.get(id);
      return ex?.tool.status === "running";
    });
    const hasActiveSubtasks = [...this.activeSubtasks.values()].some(
      (s) => s.status !== "completed" && s.status !== "error",
    );
    // 与「Ctrl+C 强制结束当前任务」对齐：有任一进行中工作则为 true。
    const cancellableWork =
      isProcessing ||
      this.streamingState === StreamingState.Paused ||
      hasRunningTools ||
      hasActiveSubtasks ||
      this.evolutionStatus === "running" ||
      (isTeamMode(this.mode) &&
        isTeamWorking(this.teamMemberEvents, this.teamMessageEvents));
    return {
      connectionStatus: this.connectionStatus,
      sessionId: this.sessionId,
      mode: this.mode,
      themeName: this.themeName,
      accentColor: this.accentColor,
      transcriptMode: this.transcriptMode,
      transcriptFoldMode: this.transcriptFoldMode,
      collapsedToolGroupIds: new Set(this.collapsedToolGroupIds),
      entries: [...this.entries],
      toolExecutions: this.toolExecutionOrder
        .map((toolCallId) => this.toolExecutions.get(toolCallId))
        .filter((item): item is ToolExecution => Boolean(item)),
      streamingState: this.streamingState,
      pendingQuestion: this.pendingQuestion
        ? {
            ...this.pendingQuestion,
            questions: this.pendingQuestion.questions.map((question) => ({
              ...question,
              options: [...question.options],
            })),
          }
        : null,
      lastError: this.lastError,
      isProcessing,
      cancellableWork,
      isPaused: this.streamingState === StreamingState.Paused,
      isInterrupted: this.streamingState === StreamingState.Interrupted,
      activeSubtasks: [...this.activeSubtasks.values()].sort((a, b) => a.index - b.index),
      todos: [...this.todos],
      teamMemberEvents: [...this.teamMemberEvents],
      teamTaskEvents: [...this.teamTaskEvents],
      teamMessageEvents: [...this.teamMessageEvents],
      evolutionStatus: this.evolutionStatus,
      contextCompression: this.contextCompression ? { ...this.contextCompression } : null,
      modelInfo: this.modelInfo,
      sessionTitle: this.sessionTitle,
      statusLineText: this.statusLineText,
    };
  }

  /** Check if interrupt was requested locally (for long-running command detection) */
  isInterruptRequested(): boolean {
    return this.interruptRequested;
  }

  /** Set local interrupt flag (for long-running local commands like log streaming) */
  requestLocalInterrupt(): void {
    this.interruptRequested = true;
  }

  /** Clear local interrupt flag (called after handling interrupt) */
  clearInterruptRequested(): void {
    this.interruptRequested = false;
  }

  /** Check if there's a server task running (for deciding whether to send chat.interrupt) */
  hasServerTask(): boolean {
    const snapshot = this.getSnapshot();
    // Include pendingQuestion: user may want to cancel while waiting for answer
    return snapshot.isProcessing || snapshot.cancellableWork || Boolean(snapshot.pendingQuestion);
  }

  getCommandContext(): CommandContext {
    const snapshot = this.getSnapshot();
    const toolGroupIds = getToolGroupIds(snapshot.entries, snapshot.toolExecutions);
    return {
      version: snapshot.modelInfo.version || "",
      sendEventOnly: this.sendEventOnly,
      request: this.request,
      askQuestions: this.askQuestions,
      sendMessage: this.sendMessage,
      sessionId: snapshot.sessionId,
      entries: snapshot.entries,
      themeName: snapshot.themeName,
      accentColor: snapshot.accentColor,
      updateSession: this.updateSession,
      addItem: this.addItem,
      clearEntries: this.clearEntries,
      restoreHistory: this.restoreHistory,
      exitApp: () => {
        // AppScreen injects the real exit handler when executing slash commands.
      },
      isProcessing: snapshot.isProcessing,
      /** Check if interrupt was requested locally (for long-running command detection) */
      isInterruptRequested: () => this.interruptRequested,
      /** Clear local interrupt flag (for long-running commands to reset after handling interrupt) */
      clearInterruptRequested: () => this.clearInterruptRequested(),
      connectionStatus: snapshot.connectionStatus,
      mode: snapshot.mode,
      setMode: this.setMode,
      setModel: this.setModel,
      setThemeName: this.setThemeName,
      setAccentColor: this.setAccentColor,
      transcriptMode: snapshot.transcriptMode,
      setTranscriptMode: this.setTranscriptMode,
      transcriptFoldMode: snapshot.transcriptFoldMode,
      setTranscriptFoldMode: this.setTranscriptFoldMode,
      collapsedToolGroupCount: toolGroupIds.filter((id) => snapshot.collapsedToolGroupIds.has(id))
        .length,
      collapseToolGroups: this.collapseToolGroups,
      expandToolGroups: this.expandToolGroups,
      sessionTitle: snapshot.sessionTitle,
      setSessionTitle: this.setSessionTitle,
      getTrustedDirs: getTrustedDirs,
      validateDirPath: validateDirPath,
      addTrustedDir: addTrustedDir,
      setTrustedDir: setTrustedDir,
      removeTrustedDir: removeTrustedDir,
      clearTrustedDirs: clearTrustedDirs,
      getWorkspaceDir: () => getTrustedDirs()[0] || process.cwd(),
      enterConfigEditor: undefined, // AppScreen injects the real handler when executing slash commands.
      setInput: this._setInputRef ?? undefined,
      enterStatusView: undefined,
      getUsageSummary: () => this.getUsageSummary(),
      restartStatusLine: () => this.restartStatusLinePoll(),
    };
  }

  getUsageSummary(): SessionUsageSummary {
    const entries = Array.from(this.usageByModel.values());
    return {
      total_input_tokens: entries.reduce((s, e) => s + e.input_tokens, 0),
      total_output_tokens: entries.reduce((s, e) => s + e.output_tokens, 0),
      total_tokens: entries.reduce((s, e) => s + e.total_tokens, 0),
      byModel: entries,
    };
  }

  /** AppScreen 在初始化时注入 setInput 回调，使 app-state 可以填充输入框。 */
  setInputRef(ref: (text: string) => void): void {
    this._setInputRef = ref;
  }

  /** AppScreen 在初始化时注入 getInputValue 回调，使 app-state 可以读取输入框内容。 */
  getInputValueRef(ref: () => string): void {
    this._getInputValueRef = ref;
  }

  readonly sendEventOnly = (method: string, params: Record<string, unknown>, isStream = false): string => {
    const id = `tui_${Date.now().toString(16)}_${Math.random().toString(36).slice(2, 6)}`;
    const trustedDirs = getTrustedDirs();
    const workspaceDir = trustedDirs[0] || process.cwd();
    this.wsClient.send({
      type: "req",
      id,
      method,
      ...(isStream ? { is_stream: true } : {}),
      params: {
        ...params,
        session_id: (params.session_id as string | undefined) ?? this.sessionId,
        ...(trustedDirs.length > 0 ? { trusted_dirs: trustedDirs } : {}),
        ...(workspaceDir ? { cwd: workspaceDir } : {}),
      },
    });
    return id;
  };

readonly request = async <T = Record<string, unknown>>(
    method: string,
    params: Record<string, unknown>,
    timeoutMs?: number,
  ): Promise<T> => {
    const id = `tui_${Date.now().toString(16)}_${Math.random().toString(36).slice(2, 6)}`;
    const trustedDirs = getTrustedDirs();
    const workspaceDir = trustedDirs[0] || process.cwd();
    const response = await this.wsClient.request(id, method, {
      ...params,
      session_id: params.session_id ?? this.sessionId,
      ...(trustedDirs.length > 0 ? { trusted_dirs: trustedDirs } : {}),
      ...(workspaceDir ? { cwd: workspaceDir } : {}),
    }, timeoutMs ?? 30000);
    return response.payload as T;
  };

  readonly updateSession = (newId: string): void => {
    this.sessionId = newId;
    this.usageByModel.clear();
    this.emitChange();
  };

  readonly setSessionTitle = (title: string): void => {
    this.sessionTitle = title;
    this.emitChange();
  };

  readonly safeFetchSessionTitle = (sessionId: string): void => {
    void (async () => {
      try {
        const meta = await this.request<{ session_id: string; title: string }>(
          "session.rename",
          { session_id: sessionId },
        );
        this.setSessionTitle(meta.title || "");
      } catch {
        // 标题获取失败不影响核心功能
      }
    })();
  };

  readonly addItem = (item: HistoryItem): void => {
    this.entries = [...this.entries, item];
    if (item.kind === "error") {
      this.lastError = item.content;
    } else {
      this.lastError = null;
    }
    this.emitChange();
  };

  readonly clearEntries = (): void => {
    if (this.localPendingQuestion) {
      this.localPendingQuestion.reject(new Error("input flow was interrupted"));
      this.localPendingQuestion = null;
    }
    this.entries = [];
    this.pendingQuestion = null;
    this.lastError = null;
    this.streamingState = StreamingState.Idle;
    this.collapsedToolGroupIds.clear();
    this.activeSubtasks.clear();
    this.todos = [];
    this.teamMemberEvents = [];
    this.teamTaskEvents = [];
    this.teamMessageEvents = [];
    this.evolutionStatus = "idle";
    this.contextCompression = null;
    this.clearToolExecutionState();
    this.historyEntries = [];
    this.historyTotalPages = null;
    this.historyPageDoneResolvers.clear();
    this.emitChange();
  };

  readonly setMode = (mode: ClientMode): void => {
    if (this.mode !== mode) {
      this.mode = mode;
      this.emitChange();
    }
  };

  readonly setModel = (name: string): void => {
    const trimmed = name.trim();
    if (trimmed && this.modelInfo.model !== trimmed) {
      this.modelInfo = { ...this.modelInfo, model: trimmed };
      this.emitChange();
    }
  };

  readonly setThemeName = (theme: ThemeName): void => {
    if (this.themeName !== theme) {
      this.themeName = theme;
      setCurrentThemeName(theme);
      this.emitChange();
    }
  };

  readonly setAccentColor = (color: AccentColorName): void => {
    if (this.accentColor !== color) {
      this.accentColor = color;
      setCurrentAccentColor(color);
      this.emitChange();
    }
  };

  readonly setTranscriptMode = (mode: "compact" | "detailed"): void => {
    if (this.transcriptMode !== mode) {
      this.transcriptMode = mode;
      this.emitChange();
    }
  };

  readonly setTranscriptFoldMode = (mode: "none" | "tools" | "thinking" | "all"): void => {
    if (this.transcriptFoldMode !== mode) {
      this.transcriptFoldMode = mode;
      this.emitChange();
    }
  };

  readonly collapseToolGroups = (scope: "last" | "all"): void => {
    const ids = getToolGroupIds(
      this.entries,
      this.toolExecutionOrder
        .map((toolCallId) => this.toolExecutions.get(toolCallId))
        .filter((item): item is ToolExecution => Boolean(item)),
    );
    if (scope === "all") {
      this.collapsedToolGroupIds = new Set(ids);
    } else {
      const last = ids[ids.length - 1];
      if (last) {
        this.collapsedToolGroupIds = new Set(this.collapsedToolGroupIds);
        this.collapsedToolGroupIds.add(last);
      }
    }
    this.emitChange();
  };

  readonly expandToolGroups = (scope: "last" | "all"): void => {
    if (scope === "all") {
      this.collapsedToolGroupIds.clear();
    } else {
      const ids = getToolGroupIds(
        this.entries,
        this.toolExecutionOrder
          .map((toolCallId) => this.toolExecutions.get(toolCallId))
          .filter((item): item is ToolExecution => Boolean(item)),
      );
      const last = ids[ids.length - 1];
      if (last) {
        this.collapsedToolGroupIds = new Set(this.collapsedToolGroupIds);
        this.collapsedToolGroupIds.delete(last);
      }
    }
    this.emitChange();
  };

  readonly sendMessage = (
    content: string,
    attachments?: FileAttachment[],
    modeOverride?: ClientMode,
    options?: { logAsUser?: boolean },
  ): string | null => {
    if (this.connectionStatus !== "connected") return null;
    const mode = modeOverride ?? this.mode;
    if (this.streamingState !== StreamingState.Idle) {
      this.sendEventOnly("chat.interrupt", { intent: "cancel" });
    }
    const requestId = this.sendEventOnly("chat.send", {
      content,
      query: content,
      mode,
      ...(attachments?.length ? { attachments } : {}),
    }, true);
    this.lastError = null;
    if (options?.logAsUser !== false) {
      this.entries = [
        ...this.entries,
        {
          kind: "user",
          id: `user-${requestId}`,
          sessionId: this.sessionId,
          content,
          at: new Date().toISOString(),
        },
      ];
    }
    this.streamingState = StreamingState.Responding;
    this.emitChange();
    return requestId;
  };

  supplement(content: string, attachments?: FileAttachment[]): string | null {
    if (this.connectionStatus !== "connected") return null;
    const trimmed = content.trim();
    if (!trimmed) return null;
    const requestId = this.sendEventOnly("chat.interrupt", {
      intent: "supplement",
      new_input: trimmed,
      ...(attachments?.length ? { attachments } : {}),
    });
    this.lastError = null;
    this.entries = [
      ...this.entries,
      {
        kind: "user",
        id: `user-${requestId}`,
        sessionId: this.sessionId,
        content: trimmed,
        at: new Date().toISOString(),
      },
    ];
    this.streamingState = StreamingState.Responding;
    this.emitChange();
    return requestId;
  }

  /** 向服务端请求中断当前 session 的任务；成功发送前不宣称"已中断"。 */
  cancel(options?: { showNotice?: boolean }): boolean {
    if (this.connectionStatus !== "connected") {
      if (options?.showNotice !== false) {
        this.addItem(addError(this.sessionId, "Unable to interrupt task while disconnected"));
      }
      return false;
    }
    if (options?.showNotice === false) {
      this.suppressInterruptResult = true;
    }
    // Set local interrupt flag immediately for long-running command detection
    this.interruptRequested = true;
    const hadLocalWork = this.getSnapshot().cancellableWork;
    this.sendEventOnly("chat.interrupt", { intent: "cancel" });
    if (options?.showNotice !== false && hadLocalWork) {
      this.addItem(addInfo(this.sessionId, "Request Interrupted", "i"));
    }
    return true;
  }

  resume(): void {
    this.sendEventOnly("chat.resume", {});
  }

  submitQuestionAnswers(answers: UserAnswer[]): void {
    if (!this.pendingQuestion) return;
    if (
      this.localPendingQuestion &&
      this.pendingQuestion.requestId === this.localPendingQuestion.requestId
    ) {
      const resolver = this.localPendingQuestion;
      this.localPendingQuestion = null;
      this.pendingQuestion = null;
      this.streamingState = StreamingState.Idle;
      resolver.resolve(answers);
      this.emitChange();
      return;
    }
    const source = this.pendingQuestion.source;
    if (source === "permission_interrupt" || source === "ask_user_interrupt") {
      this.sendEventOnly("chat.send", {
        query: "",
        request_id: this.pendingQuestion.requestId,
        answers,
        source,
        mode: this.mode,
      }, true);
    } else {
      this.sendEventOnly("chat.user_answer", {
        request_id: this.pendingQuestion.requestId,
        answers,
        mode: this.mode,
      });
    }
    this.pendingQuestion = null;
    this.streamingState = StreamingState.Idle;
    this.emitChange();
  }

  answerQuestion(answer: string): void {
    this.submitQuestionAnswers([{ selected_options: [answer], custom_input: answer }]);
  }

  readonly askQuestions = (
    questions: PendingQuestionItem[],
    source = "local_command",
  ): Promise<UserAnswer[]> => {
    if (questions.length === 0) {
      return Promise.resolve([]);
    }
    if (this.pendingQuestion || this.localPendingQuestion) {
      return Promise.reject(new Error("another question is already active"));
    }

    const requestId = `local_${Date.now().toString(16)}_${Math.random().toString(36).slice(2, 6)}`;
    this.pendingQuestion = {
      requestId,
      source,
      questions,
    };
    this.streamingState = StreamingState.Idle;
    this.emitChange();

    return new Promise<UserAnswer[]>((resolve, reject) => {
      this.localPendingQuestion = { requestId, resolve, reject };
    });
  };

  readonly restoreHistory = async (targetSessionId: string): Promise<void> => {
    this.historyRequestToken += 1;
    const requestToken = this.historyRequestToken;
    this.historyEntries = [];
    this.historyTotalPages = null;
    this.historyPageDoneResolvers.clear();
    this.clearToolExecutionState();
    if (this.historyFlushTimer) {
      clearTimeout(this.historyFlushTimer);
      this.historyFlushTimer = null;
    }

    // 本地 channel 对 `history.get` 的 ack 里只有 `accepted: true` / `session_id` / `page_idx`，
    // 真正的消息和分页元数据通过 `history.message` 事件流异步到达；这里先处理本地 ack 返回里
    // 恰好带 messages 的分支（老链路兼容），再按 `total_pages` 循环逐页拉取。
    const fetchPage = async (pageIdx: number): Promise<void> => {
      const donePromise = new Promise<void>((resolve) => {
        this.historyPageDoneResolvers.set(pageIdx, resolve);
      });
      let ackPayload: {
        messages?: unknown[];
        total_pages?: number;
        page_idx?: number;
      } = {};
      try {
        ackPayload = await this.request<{
          messages?: unknown[];
          total_pages?: number;
          page_idx?: number;
        }>("history.get", { session_id: targetSessionId, page_idx: pageIdx });
      } catch (error) {
        this.historyPageDoneResolvers.delete(pageIdx);
        throw error;
      }

      if (Array.isArray(ackPayload.messages) && ackPayload.messages.length > 0) {
        const merged = mergeHistoryMessagesForRestore(ackPayload.messages);
        for (const message of merged) {
          if (requestToken !== this.historyRequestToken) {
            this.historyPageDoneResolvers.delete(pageIdx);
            return;
          }
          const entry = parseHistoryFrame({
            type: "event",
            event: "history.message",
            payload: {
              session_id: targetSessionId,
              message,
              total_pages: ackPayload.total_pages,
              page_idx: ackPayload.page_idx,
            },
          });
          if (entry) {
            this.historyEntries.push(entry);
          }
        }
        if (typeof ackPayload.total_pages === "number" && ackPayload.total_pages > 0) {
          this.historyTotalPages = ackPayload.total_pages;
        }
        // 本地 ack 已经自带全部数据，不会再发 done 事件，这里手动解析。
        this.historyPageDoneResolvers.delete(pageIdx);
        return;
      }

      // 等待 history.message 流的 `status: done` 帧；加超时保护，避免 done 丢失时无限挂起。
      const PAGE_TIMEOUT_MS = 15_000;
      await Promise.race([
        donePromise,
        new Promise<void>((resolve) => {
          setTimeout(() => {
            if (this.historyPageDoneResolvers.has(pageIdx)) {
              this.historyPageDoneResolvers.delete(pageIdx);
            }
            resolve();
          }, PAGE_TIMEOUT_MS);
        }),
      ]);
    };

    try {
      // 先拉第 1 页（取最新 50 条）。
      await fetchPage(1);
      if (requestToken !== this.historyRequestToken) return;

      // 后端按 `list(reversed(raw))` 分页：第 2, 3, ... 页是更早的消息。
      // 没有显式上限能确定会话总大小，统一按 total_pages 循环；每页不超过 50 条事件，成本可控。
      const totalPages = this.historyTotalPages ?? 1;
      for (let page = 2; page <= totalPages; page++) {
        if (requestToken !== this.historyRequestToken) return;
        try {
          await fetchPage(page);
        } catch (error) {
          // 容忍老会话或竞态下的 `invalid page_idx` 错误：停止翻页，保留已拉到的消息。
          if (isIgnorableHistoryRestoreError(error)) {
            break;
          }
          throw error;
        }
      }
    } catch (error) {
      if (requestToken === this.historyRequestToken) {
        throw error;
      }
      return;
    }

    setTimeout(() => {
      if (requestToken !== this.historyRequestToken) return;
      this.applyHistoryEntriesToTranscript();
    }, 80);
  };

  /** cancel 成功后自动回退判断。

   条件：
   1. 输入框为空（用户取消后没有输入新内容）
   2. 最后一个 selectable user message 之后的消息只有合成/系统类，
      无实质 assistant 输出
   满足时自动调用 rewind_and_restore RPC 截断对话+恢复文件，
   并将被回退的内容填充输入框。
   */
  readonly tryAutoRestoreAfterCancel = async (): Promise<void> => {
    // 条件 1：输入框为空
    if (this._getInputValueRef && this._getInputValueRef().trim() !== "") return;

    const entries = this.getSnapshot().entries;
    if (!entries || entries.length === 0) return;

    // 找最后一个实质 user message
    const nonSyntheticTags = [
      "<local-command-stdout>",
      "<bash-stdout>",
      "<task-notification>",
    ];
    let lastUserIdx = -1;
    for (let i = entries.length - 1; i >= 0; i--) {
      const e = entries[i];
      if (e.kind !== "user") continue;
      const content = typeof e.content === "string" ? e.content : "";
      if (nonSyntheticTags.some((tag) => content.includes(tag))) continue;
      lastUserIdx = i;
      break;
    }
    if (lastUserIdx < 0) return;

    // 判断 lastUserIdx 之后是否只有合成/系统类消息
    const hasSubstantialAfter = entries.slice(lastUserIdx + 1).some((e) => {
      if (e.kind === "user") {
        const content = typeof e.content === "string" ? e.content : "";
        return !nonSyntheticTags.some((tag) => content.includes(tag));
      }
      // assistant 含实质内容视为有实质输出
      if (e.kind === "assistant") return true;
      // 其他（info/error/system）视为合成消息
      return false;
    });

    if (hasSubstantialAfter) return; // 有实质内容，不自动回退

    // 获取 turn 列表以确定 lastUserIdx 对应的 turn_index
    try {
      const turnsPayload = await this.request<{
        turns?: { turn_index: number; content_preview: string; content?: string }[];
        total?: number;
      }>("history.list_turns", { session_id: this.sessionId });
      const turns = turnsPayload.turns ?? [];
      if (turns.length === 0) return;

      const lastTurn = turns[turns.length - 1];
      if (!lastTurn) return;

      const rewindPayload = await this.request<{
        content?: string;
        content_preview?: string;
        remaining_records?: number;
        removed_records?: number;
        restored_files?: string[];
        deleted_files?: string[];
        restore_errors?: { file: string; error: string }[];
      }>("session.rewind_and_restore", {
        session_id: this.sessionId,
        turn_index: lastTurn.turn_index,
      });

      this.entries = [];
      this.emitChange();
      await this.restoreHistory(this.sessionId);

      const restoreText = rewindPayload.content ?? lastTurn.content_preview ?? "";
      this._setInputRef?.(restoreText);

      this.addItem(
        addInfo(
          this.sessionId,
          "Auto-restored to before the last turn (no substantial output after cancel). " +
            "The removed turn text has been placed in the input for you to edit.\n" +
            "Note: Rewinding does not affect files edited manually or via bash commands.",
          "i",
        ),
      );
    } catch {
      // 自动回退失败不影响 cancel 本身
    }
  };

  private applyHistoryEntriesToTranscript(): void {
    // AgentServer 为了让分页优先返回最新页，在 `_handle_history_get_stream` 中把整条历史做了
    // `list(reversed(raw))` 后再流式下发；CLI 按到达顺序 push 到 `historyEntries` 会得到倒序。
    // 这里按消息时间戳重排回时间升序，再做同 turn 合并，保证 UI 从最早到最新正常显示。
    const ordered = [...this.historyEntries]
      .map((entry, originalIndex) => ({ entry, originalIndex, ts: Date.parse(entry.at) }))
      .sort((a, b) => {
        const ta = Number.isNaN(a.ts) ? 0 : a.ts;
        const tb = Number.isNaN(b.ts) ? 0 : b.ts;
        if (ta !== tb) return ta - tb;
        return a.originalIndex - b.originalIndex;
      })
      .map((item) => item.entry);

    const restored = coalesceAssistantHistoryEntries(ordered);

    // Merge: keep existing frontend entries that aren't in the restored set,
    // so that live entries accumulated during streaming aren't lost on reconnection.
    const restoredById = new Map<string, HistoryItem>();
    for (const entry of restored) {
      restoredById.set(entry.id, entry);
    }

    // Build merged set: restored entries + frontend-only entries (not in restored).
    // For entries present in both, prefer the restored version (server-authoritative).
    const merged: HistoryItem[] = [];
    for (const entry of restored) {
      merged.push(entry);
    }
    for (const entry of this.entries) {
      if (!restoredById.has(entry.id)) {
        merged.push(entry);
      }
    }

    // Sort merged entries by timestamp for consistent display order.
    const sorted = merged
      .map((entry, originalIndex) => ({ entry, originalIndex, ts: Date.parse(entry.at) }))
      .sort((a, b) => {
        const ta = Number.isNaN(a.ts) ? 0 : a.ts;
        const tb = Number.isNaN(b.ts) ? 0 : b.ts;
        if (ta !== tb) return ta - tb;
        return a.originalIndex - b.originalIndex;
      })
      .map((item) => item.entry);

    this.entries = [...sorted];
    this.rebuildToolExecutionState();
    this.emitChange();
  }

  private readonly clearToolExecutionState = (): void => {
    if (this.toolTimeoutTimer) {
      clearTimeout(this.toolTimeoutTimer);
      this.toolTimeoutTimer = null;
    }
    this.toolExecutions = new Map();
    this.toolExecutionOrder = [];
    this.orphanToolResults = new Map();
  };

  private scheduleToolTimeoutCheck(): void {
    if (this.toolTimeoutTimer) {
      clearTimeout(this.toolTimeoutTimer);
      this.toolTimeoutTimer = null;
    }
    let nextTimeoutMs = Number.POSITIVE_INFINITY;
    const now = Date.now();
    for (const execution of this.toolExecutions.values()) {
      if (execution.tool.status !== "running") {
        continue;
      }
      const timeoutMs = Date.parse(execution.timeoutAt);
      if (Number.isNaN(timeoutMs)) {
        continue;
      }
      nextTimeoutMs = Math.min(nextTimeoutMs, timeoutMs);
    }
    if (!Number.isFinite(nextTimeoutMs)) {
      return;
    }
    const delay = Math.max(0, nextTimeoutMs - now);
    this.toolTimeoutTimer = setTimeout(() => {
      this.toolTimeoutTimer = null;
      if (this.markTimedOutExecutions()) {
        this.emitChange();
      } else {
        this.scheduleToolTimeoutCheck();
      }
    }, delay + 10);
  }

  private markRunningToolsInterrupted(): void {
    const nowIso = new Date().toISOString();
    let changed = false;
    for (const [toolCallId, execution] of this.toolExecutions) {
      if (execution.tool.status !== "running") {
        continue;
      }
      const nextTool: ToolCallDisplay = {
        ...execution.tool,
        status: "completed",
        summary: execution.tool.summary?.trim() ? execution.tool.summary : "Interrupted",
      };
      this.toolExecutions.set(toolCallId, {
        ...execution,
        tool: nextTool,
        updatedAt: nowIso,
      });
      this.entries = upsertToolGroupDisplay(
        this.entries,
        execution.sessionId,
        execution.requestId,
        nextTool,
      );
      changed = true;
    }
    if (changed) {
      this.scheduleToolTimeoutCheck();
      this.emitChange();
    }
  }

  private markTimedOutExecutions(): boolean {
    const nowIso = new Date().toISOString();
    const nowMs = Date.parse(nowIso);
    let changed = false;
    for (const [toolCallId, execution] of this.toolExecutions) {
      if (execution.tool.status !== "running") {
        continue;
      }
      const timeoutMs = Date.parse(execution.timeoutAt);
      if (Number.isNaN(timeoutMs) || timeoutMs > nowMs) {
        continue;
      }
      const nextTool: ToolCallDisplay = {
        ...execution.tool,
        status: "timeout",
        isError: false,
      };
      this.toolExecutions.set(toolCallId, {
        ...execution,
        tool: nextTool,
        updatedAt: nowIso,
        timedOutAt: nowIso,
      });
      this.entries = upsertToolGroupDisplay(
        this.entries,
        execution.sessionId,
        execution.requestId,
        nextTool,
      );
      changed = true;
    }
    this.scheduleToolTimeoutCheck();
    return changed;
  }

  private addToolCallPayload(
    payload: Record<string, unknown>,
    sessionId: string,
    requestId?: string,
    startedAt?: string,
  ): void {
    const tool = createToolCallDisplay(payload);
    if (!tool.callId) {
      return;
    }

    if (this.toolExecutions.has(tool.callId)) {
      return;
    }

    const started = startedAt ?? new Date().toISOString();
    const orphan = this.orphanToolResults.get(tool.callId);
    const nextTool = orphan
      ? {
          ...tool,
          status: orphan.tool.status,
          result: orphan.tool.result,
          summary: orphan.tool.summary,
          isError: orphan.tool.isError,
        }
      : tool;

    this.toolExecutions.set(tool.callId, {
      toolCallId: tool.callId,
      sessionId,
      requestId: requestId ?? orphan?.requestId,
      tool: nextTool,
      startedAt: started,
      updatedAt: orphan?.updatedAt ?? started,
      timeoutAt: computeTimeoutAt(started),
      resultArrivedAfterTimeout: false,
    });
    this.toolExecutionOrder.push(tool.callId);
    if (orphan) {
      this.orphanToolResults.delete(tool.callId);
    }
    this.entries = upsertToolGroupDisplay(
      this.entries,
      sessionId,
      requestId ?? orphan?.requestId,
      nextTool,
    );
    this.scheduleToolTimeoutCheck();
  }

  private addToolResultPayload(
    payload: Record<string, unknown>,
    sessionId: string,
    requestId?: string,
    updatedAt?: string,
  ): void {
    const baseTool = createToolCallDisplay(payload);
    const resultTool = applyToolResult(baseTool, payload);
    if (!resultTool.callId) {
      return;
    }

    const nowIso = updatedAt ?? new Date().toISOString();
    const existing = this.toolExecutions.get(resultTool.callId);
    if (!existing) {
      this.orphanToolResults.set(resultTool.callId, {
        tool: resultTool,
        requestId,
        updatedAt: nowIso,
      });
      this.entries = upsertToolGroupDisplay(this.entries, sessionId, requestId, resultTool);
      return;
    }

    const wasTimedOut = existing.tool.status === "timeout";
    const nextTool: ToolCallDisplay = {
      ...existing.tool,
      ...resultTool,
      arguments: existing.tool.arguments,
      description: existing.tool.description ?? resultTool.description,
      formattedArgs: existing.tool.formattedArgs ?? resultTool.formattedArgs,
      status: wasTimedOut && !resultTool.isError ? "timeout" : resultTool.status,
      summary: wasTimedOut
        ? resultTool.summary
          ? `${resultTool.summary} (after timeout)`
          : resultTool.isError
            ? "failed after timeout"
            : "completed after timeout"
        : resultTool.summary,
    };
    this.toolExecutions.set(resultTool.callId, {
      ...existing,
      requestId: existing.requestId ?? requestId,
      tool: nextTool,
      updatedAt: nowIso,
      resultArrivedAfterTimeout: wasTimedOut || existing.resultArrivedAfterTimeout,
    });
    this.entries = upsertToolGroupDisplay(
      this.entries,
      sessionId,
      existing.requestId ?? requestId,
      nextTool,
    );
    this.scheduleToolTimeoutCheck();
  }

  private addSyntheticToolExecution(
    tool: ToolCallDisplay,
    sessionId: string,
    requestId?: string,
    at?: string,
  ): void {
    const timestamp = at ?? new Date().toISOString();
    this.toolExecutions.set(tool.callId, {
      toolCallId: tool.callId,
      sessionId,
      requestId,
      tool,
      startedAt: timestamp,
      updatedAt: timestamp,
      timeoutAt: computeTimeoutAt(timestamp),
    });
    if (!this.toolExecutionOrder.includes(tool.callId)) {
      this.toolExecutionOrder.push(tool.callId);
    }
    this.entries = upsertToolGroupDisplay(this.entries, sessionId, requestId, tool);
    this.scheduleToolTimeoutCheck();
  }

  private rebuildToolExecutionState(): void {
    const rebuilt = rebuildToolExecutionStateFromEntries(this.entries);
    this.toolExecutions = rebuilt.toolExecutions;
    this.toolExecutionOrder = rebuilt.toolExecutionOrder;
    this.orphanToolResults = new Map();
    this.scheduleToolTimeoutCheck();
  }

  private startStatusLinePoll(): void {
    this.executeStatusLineCommand();
    this.statusLineTimer = setInterval(() => {
      this.executeStatusLineCommand();
    }, 2_000);
  }

  private stopStatusLinePoll(): void {
    if (this.statusLineTimer) {
      clearInterval(this.statusLineTimer);
      this.statusLineTimer = null;
    }
  }

  restartStatusLinePoll(): void {
    this.stopStatusLinePoll();
    this.statusLineText = null;
    this.emitChange();
    this.startStatusLinePoll();
  }

  
  private buildStatusLineJsonInput(): Record<string, unknown> {
    const snapshot = this.getSnapshot();
    const usage = this.getUsageSummary();
    const trustedDirs = getTrustedDirs();
    const cwd = trustedDirs[0] || process.cwd();
    return {
      session_id: snapshot.sessionId,
      session_name: snapshot.sessionTitle,
      cwd,
      mode: snapshot.mode,
      model: snapshot.modelInfo.model,
      provider: snapshot.modelInfo.provider,
      version: snapshot.modelInfo.version,
      connection: snapshot.connectionStatus,
      theme: snapshot.themeName,
      accent_color: snapshot.accentColor,
      transcript_mode: snapshot.transcriptMode,
      transcript_fold_mode: snapshot.transcriptFoldMode,
      is_processing: snapshot.isProcessing,
      is_paused: snapshot.isPaused,
      is_interrupted: snapshot.isInterrupted,
      cancellable_work: snapshot.cancellableWork,
      streaming_state: snapshot.streamingState,
      last_error: snapshot.lastError,
      evolution_status: snapshot.evolutionStatus,
      active_subtask_count: snapshot.activeSubtasks.length,
      todo_count: snapshot.todos.length,
      usage: {
        total_input_tokens: usage.total_input_tokens,
        total_output_tokens: usage.total_output_tokens,
        total_tokens: usage.total_tokens,
      },
    };
  }

  private executeStatusLineCommand(): void {
    const config = loadTuiConfig();
    const sl = config.statusLine;
    if (!sl || sl.type !== "command" || !sl.command) {
      if (this.statusLineText !== null) {
        this.statusLineText = null;
        this.emitChange();
      }
      return;
    }
    const jsonInput = JSON.stringify(this.buildStatusLineJsonInput());
    const cmd = sl.command;
    const isWindows = process.platform === "win32";

    try {
      if (isWindows) {
        // On Windows, sh.exe from MSYS2/Git Bash can't read stdin in $(cat)
        // inside sh -c. Solution: write JSON to a temp file and:
        // 1. Replace $(cat) (no args) with $(cat "filepath") for inline commands
        // 2. Export JIUWENCLAW_SL_FILE so script files can use it too:
        //    input=$(cat "$JIUWENCLAW_SL_FILE")
        const tmpFile = join(tmpdir(), "jiuwenswarm-sl.json");
        writeFileSync(tmpFile, jsonInput, "utf8");
        const msysPath = tmpFile.split(sep).join("/").replace(/^([A-Za-z]):/, (_, d) => "/" + d.toLowerCase());
        const patchedCmd = cmd.replace(/\$\(cat\)/g, `$(cat "${msysPath}")`);
        const fullCmd = `export JIUWENCLAW_SL_FILE="${msysPath}"; ${patchedCmd}`;

        const child = execFile("sh", ["-c", fullCmd], { timeout: 3_000, maxBuffer: 10_240, cwd: process.cwd() }, (err, stdout) => {
          if (err) return;
          const text = stdout.trim();
          if (text !== this.statusLineText) {
            this.statusLineText = text || null;
            this.emitChange();
          }
        });
      } else {
        // On POSIX, stdin piping works correctly in sh -c.
        const child = execFile("sh", ["-c", cmd], { timeout: 3_000, maxBuffer: 10_240 }, (err, stdout) => {
          if (err) return;
          const text = stdout.trim();
          if (text !== this.statusLineText) {
            this.statusLineText = text || null;
            this.emitChange();
          }
        });
        child.stdin?.end(jsonInput);
      }
    } catch {
      // Silently ignore — sh may not be in PATH on Windows
    }
  }

  private emitChange(): void {
    for (const listener of this.listeners) {
      listener();
    }
  }

  private handleFrame(frame: unknown): void {
    if (!isEventFrame(frame as EventFrame | any)) {
      return;
    }
    const typedFrame = frame as EventFrame;
    if (handleIncomingFrame(this.eventDelegate, typedFrame)) {
      this.emitChange();
    }
  }

  private scheduleHistoryFlush(): void {
    if (this.historyFlushTimer) {
      clearTimeout(this.historyFlushTimer);
    }
    this.historyFlushTimer = setTimeout(() => {
      this.historyFlushTimer = null;
      this.applyHistoryEntriesToTranscript();
    }, 50);
  }

  private safeRestoreHistory(sessionId: string): void {
    void (async () => {
      try {
        await this.restoreHistory(sessionId);
      } catch (error) {
        if (isIgnorableHistoryRestoreError(error)) {
          return;
        }
        this.lastError = error instanceof Error ? error.message : String(error);
        this.emitChange();
      }
    })();
  }
}
